"""Voice sessions (spec voice-agent session-start SE4-SE10, SE16, R7.4).

- :func:`start_voice_session`: the refusals and spend caps, the reserved ``voice_sessions``
  row, the ElevenLabs mint after the reserving transaction commits, the write-once
  ElevenLabs conversation id and the stored spoken disclosure, then the signed token.
- The token is ``v1.voice.<conversation_id>.<voice_session_id>.<exp>.<sig>``: ``exp`` is
  integer Unix seconds (ASCII digits only) and ``sig`` the base64url (no padding)
  HMAC-SHA256, under ``criba_voice_secret`` alone, of everything before the last dot. It
  binds our own session id, never an Application or Candidate id. Verification checks the
  signature in constant time before it reads ``exp`` or the database, and every refusal
  carries one message, so a caller cannot tell a bad signature from an expiry.
- :func:`end_voice_session` is the one writer of ``ended_at``.
- :class:`AgentDriftCache` holds the last agent-configuration check, so the session
  transaction reads a flag and makes no network call.

Nothing here logs a secret, a token, an agent id or a drift line.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import math
import os
import secrets
import sqlite3
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from criba.channel.copy import spoken_disclosure
from criba.channel.elevenlabs import ElevenLabsClient, elevenlabs_client
from criba.channel.voice_config import VoiceSettings, voice_available
from criba.db import repo
from criba.db.connection import iso, parse_iso, transaction

log = logging.getLogger(__name__)

VOICE_TOKEN_TTL_S = 1800
RATE_LIMIT = 3  # sessions started per conversation
RATE_WINDOW_S = 600
_VERSION = ("v1", "voice")
_INVALID = "invalid voice token"
# A call starts on a voice conversation only: voice is its own channel (ADR 0059).
_CHANNEL = "voice"
_CHAT_CHANNEL = "webchat"
DISCLOSURE_PREFIX = "vd_"

# SYNTHETIC: Candidate-facing refusal copy (the contract's voice-session errors). A voice page has
# no chat to point to (ADR 0059).
NOT_FOUND = "Conversation not found."
REFUSED = "No podemos empezar la llamada ahora. Inténtalo más tarde."
RATE_LIMITED = "Vas muy rápido. Espera un momento y vuelve a escribir."
UNAVAILABLE = "La llamada de voz no está disponible ahora. Inténtalo más tarde."


class VoiceTokenError(Exception):
    """The voice token is not valid now. One message for every reason."""

    def __init__(self, message: str = _INVALID) -> None:
        super().__init__(message)


@dataclass(frozen=True)
class VoiceSessionRef:
    voice_session_id: str
    conversation_id: str
    candidate_id: str
    country: str


class VoiceSessionEnded(VoiceTokenError):
    """A valid token whose session ended while the Candidate's open Application is under a
    Takeover (decision 11): ``spoken-turn`` answers it with the handover line."""

    def __init__(self, ref: VoiceSessionRef) -> None:
        super().__init__()
        self.ref = ref


def _signature(payload: str, secret: bytes) -> str:
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def make_voice_token(
    conversation_id: str, voice_session_id: str, expires_at: datetime, *, secret: bytes
) -> str:
    """Sign a voice token. ``ValueError`` for an empty secret or an empty or dotted id."""
    if not secret:
        raise ValueError("the voice token secret is empty")
    for value in (conversation_id, voice_session_id):
        if not value or "." in value:
            raise ValueError("voice token ids must be non-empty and contain no '.'")
    if expires_at.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware")
    exp = int(expires_at.timestamp())
    payload = ".".join((*_VERSION, conversation_id, voice_session_id, str(exp)))
    return f"{payload}.{_signature(payload, secret)}"


def verify_voice_token(
    conn: sqlite3.Connection,
    token: str,
    *,
    now: datetime,
    secret: bytes,
    conversation_id: str | None = None,
) -> VoiceSessionRef:
    """The session a token names, if the token is valid now; else :class:`VoiceTokenError`.

    Valid: a non-empty secret; six parts; a matching signature (``hmac.compare_digest``,
    first); the ``v1.voice`` prefix; ``exp`` ASCII digits and later than ``now``; the token's
    conversation equal to ``conversation_id`` when given; and a session row of that
    conversation with ``ended_at IS NULL``. An ended session whose Candidate's open
    Application is under a Takeover raises :class:`VoiceSessionEnded` instead.
    """
    if not secret:
        raise VoiceTokenError()
    parts = token.split(".")
    if len(parts) != 6:
        raise VoiceTokenError()
    payload, signature = token.rsplit(".", 1)
    expected = _signature(payload, secret)
    if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("ascii")):
        raise VoiceTokenError()
    version, kind, token_conversation, session_id, exp_text, _ = parts
    if (version, kind) != _VERSION:
        raise VoiceTokenError()
    if not (exp_text.isascii() and exp_text.isdigit()):
        raise VoiceTokenError()
    if int(exp_text) <= now.timestamp():
        raise VoiceTokenError()
    if conversation_id is not None and conversation_id != token_conversation:
        raise VoiceTokenError()
    row = repo.voice_session_row(conn, session_id)
    if row is None or row["conversation_id"] != token_conversation:
        raise VoiceTokenError()
    ref = VoiceSessionRef(
        voice_session_id=row["id"],
        conversation_id=row["conversation_id"],
        candidate_id=row["candidate_id"],
        country=row["country"],
    )
    if row["ended_at"] is not None:
        if _under_takeover(conn, ref.candidate_id):
            raise VoiceSessionEnded(ref)
        raise VoiceTokenError()
    return ref


def _under_takeover(conn: sqlite3.Connection, candidate_id: str) -> bool:
    return repo.open_application_under_takeover(conn, candidate_id)


def end_voice_session(
    conn: sqlite3.Connection,
    voice_session_id: str,
    *,
    now: datetime,
    seconds: float | None = None,
) -> None:
    """End a session: ``ended_at`` (and ``seconds`` when given) only while ``ended_at`` is
    NULL. The one writer of ``ended_at``; a second call, or an unknown id, changes nothing."""
    repo.mark_voice_session_ended(conn, voice_session_id, iso(now), seconds)


def set_elevenlabs_conversation_id(
    conn: sqlite3.Connection, voice_session_id: str, elevenlabs_conversation_id: str
) -> None:
    """Write the ElevenLabs conversation id once, only while it is NULL; a different id
    later keeps the first. :class:`VoiceTokenError` for an unknown session, an empty id, or
    an id already on another session (the partial UNIQUE index); both rows stay as they are."""
    known = repo.voice_session_exists(conn, voice_session_id)
    if not known or not elevenlabs_conversation_id:
        raise VoiceTokenError()
    clashed = False
    try:
        repo.set_voice_session_conversation_id(conn, voice_session_id, elevenlabs_conversation_id)
    except sqlite3.IntegrityError:
        clashed = True
    if clashed:
        raise VoiceTokenError() from None


# ---------------------------------------------------------------------------
# The drift cache (SE16)
# ---------------------------------------------------------------------------


class AgentDriftCache:
    """The last result of the agent-configuration check. ``ok()`` is true only after a check
    returned no drift lines; false before the first check, after drift and after the check
    raised. ``start()`` checks once at once, then every ``interval_s``, on a daemon thread.
    ``ok()`` never blocks and makes no network call."""

    def __init__(self, check: Callable[[], Sequence[str]], *, interval_s: float = 600) -> None:
        self._check = check
        self.interval_s = interval_s
        self._ok = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def ok(self) -> bool:
        return self._ok

    def refresh(self) -> None:
        """Run the check once and keep its verdict. Logs counts only, never the lines."""
        try:
            lines = list(self._check())
        except Exception:
            self._ok = False
            log.warning("voice agent drift check failed: voice sessions are refused")
            return
        self._ok = not lines
        if lines:
            log.warning("voice agents drift (%d field(s)): voice sessions are refused", len(lines))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="voice-agent-drift", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        self.refresh()
        while not self._stop.wait(self.interval_s):
            self.refresh()


# ---------------------------------------------------------------------------
# Session start (SE4-SE6, SE10)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VoiceSessionStart:
    conversation_token: str
    voice_token: str
    expires_at: datetime
    country: str


class VoiceSessionRefused(Exception):
    """A refused start: the HTTP status, the contract's error code, the SYNTHETIC message
    (it never says why a 409 refused), and ``Retry-After`` seconds for a 429."""

    def __init__(
        self, status: int, code: str, message: str, *, retry_after: int | None = None
    ) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


def _unavailable() -> VoiceSessionRefused:
    return VoiceSessionRefused(503, "voice_unavailable", UNAVAILABLE)


@dataclass(frozen=True)
class VoiceRuntime:
    settings: VoiceSettings
    client: ElevenLabsClient
    drift: AgentDriftCache | None = None


def voice_runtime_from_env(env: Mapping[str, str] | None = None) -> VoiceRuntime:
    """Settings and client from the environment; no drift cache (``agents-config`` wires it)."""
    settings = VoiceSettings.from_env(os.environ if env is None else env)
    return VoiceRuntime(settings=settings, client=elevenlabs_client(settings))


def start_voice_session(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    settings: VoiceSettings,
    now: datetime,
    client: ElevenLabsClient,
    drift: AgentDriftCache | None,
) -> VoiceSessionStart:
    """Start a voice session for a voice conversation, or raise :class:`VoiceSessionRefused`.

    Refusals, in order, before any write or mint: voice unavailable (503); unknown
    conversation (404); a web chat conversation (409: voice is its own flow, ADR 0059); no
    country (503); the open Application under a Takeover, or not a voice Application (409);
    three sessions started in 10 minutes, failed mints aside (429); the concurrent or daily
    cap (503); the drift check (503). The checks and the insert share one ``BEGIN
    IMMEDIATE``; the mint runs after it commits. A failed mint, or a conversation id
    already on another session, ends the new row with ``seconds = 0`` and answers 503.
    Creates no Application and lifts no opt-out.
    """
    if conn.in_transaction:
        raise ValueError("start_voice_session needs a connection outside any transaction")
    secret = settings.read_secret("criba_voice_secret")
    if not voice_available(settings) or not secret:
        raise _unavailable()
    if not conversation_id or "." in conversation_id:
        raise VoiceSessionRefused(404, "not_found", NOT_FOUND)
    voice_session_id = f"vs_{secrets.token_hex(16)}"
    with transaction(conn):
        identity = repo.identity_candidate_row(conn, _CHANNEL, conversation_id)
        if identity is None:
            chat = repo.identity_exists(conn, _CHAT_CHANNEL, conversation_id)
            if chat:
                raise VoiceSessionRefused(409, "voice_refused", REFUSED)
            raise VoiceSessionRefused(404, "not_found", NOT_FOUND)
        candidate_id = identity["candidate_id"]
        country: str = repo.candidate_country(conn, candidate_id) or ""
        agent_id = {"ES": settings.agent_es, "MX": settings.agent_mx}.get(country)
        if not agent_id:
            raise _unavailable()
        open_app = repo.get_open_application(conn, candidate_id)
        # The Application a call would speak on: the open one, else one the first utterance
        # would reopen (or reach, after a Knockout) within 30 days.
        target = open_app or repo.reopenable_application(conn, candidate_id, now=now)
        if (open_app is not None and open_app["control"] == "human") or (
            target is not None and target["channel"] != _CHANNEL
        ):
            # A text Application is not continued by voice (ADR 0059).
            raise VoiceSessionRefused(409, "voice_refused", REFUSED)
        retry_after = _rate_limited(conn, conversation_id, now)
        if retry_after:
            raise VoiceSessionRefused(429, "rate_limited", RATE_LIMITED, retry_after=retry_after)
        if _live(conn, now) >= settings.max_concurrent:
            raise _unavailable()
        if _used_seconds(conn, now) >= settings.daily_minutes * 60:
            raise _unavailable()
        if drift is None:
            if settings.environment not in ("sim", "test"):
                raise _unavailable()
        elif not drift.ok():
            raise _unavailable()
        repo.insert_voice_session(
            conn, voice_session_id, conversation_id, candidate_id, country, iso(now)
        )

    minted = None
    try:
        minted = client.conversation_token(agent_id)
    except Exception:
        minted = None
    if minted is None:
        end_voice_session(conn, voice_session_id, now=now, seconds=0)
        raise _unavailable() from None

    clashed = False
    try:
        with transaction(conn):
            set_elevenlabs_conversation_id(conn, voice_session_id, minted.conversation_id)
            # A first-time caller has no Application yet: the disclosure is stored for the
            # Candidate now (the captions show it) and joins the Application on the first turn.
            open_app = repo.get_open_application(conn, candidate_id)
            store_spoken_disclosure(conn, voice_session_id, open_app, now=now)
    except VoiceTokenError:
        clashed = True
    if clashed:
        end_voice_session(conn, voice_session_id, now=now, seconds=0)
        raise _unavailable() from None

    expires_at = now + timedelta(seconds=VOICE_TOKEN_TTL_S)
    token = make_voice_token(
        conversation_id, voice_session_id, expires_at, secret=secret.encode("utf-8")
    )
    return VoiceSessionStart(
        conversation_token=minted.token, voice_token=token, expires_at=expires_at, country=country
    )


def _rate_limited(conn: sqlite3.Connection, conversation_id: str, now: datetime) -> int:
    """0 when another session may start, else the seconds until the oldest of the last
    three leaves the window. A failed mint (closed at once with ``seconds = 0``) is not a
    started session."""
    rows = repo.recent_voice_session_starts(
        conn, conversation_id, iso(now - timedelta(seconds=RATE_WINDOW_S))
    )
    if len(rows) < RATE_LIMIT:
        return 0
    oldest = parse_iso(rows[0]["started_at"])
    wait = (oldest + timedelta(seconds=RATE_WINDOW_S) - now).total_seconds()
    return max(1, math.ceil(wait))


def _live(conn: sqlite3.Connection, now: datetime) -> int:
    """Sessions not ended whose token has not expired."""
    return repo.live_voice_session_count(conn, iso(now - timedelta(seconds=VOICE_TOKEN_TTL_S)))


def _used_seconds(conn: sqlite3.Connection, now: datetime) -> float:
    """Voice seconds of sessions started on ``now``'s UTC day: ``seconds`` of an ended row,
    else ``ended_at - started_at``; an unended row adds ``min(now - started_at, 1800)``."""
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = repo.voice_sessions_started_since(conn, iso(day))
    used = 0.0
    for row in rows:
        started = parse_iso(row["started_at"])
        if row["ended_at"] is not None:
            if row["seconds"] is not None:
                used += float(row["seconds"])
            else:
                used += max(0.0, (parse_iso(row["ended_at"]) - started).total_seconds())
        else:
            used += min(max(0.0, (now - started).total_seconds()), VOICE_TOKEN_TTL_S)
    return used


def store_spoken_disclosure(
    conn: sqlite3.Connection,
    voice_session_id: str,
    application: dict[str, Any] | None,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """Store the spoken disclosure as the session's one outbound ``spoken`` agent message
    (id ``vd_<voice_session_id>``, so a second call returns the first row).

    With ``application`` None (a first-time caller: the call starts before an Application
    exists, and the first utterance creates it) the row belongs to the Candidate alone, so
    the captions, which read the conversation's messages, show it from the start of the call.
    With an Application it is stored on that Application, or, if the call's row is already
    stored without one, linked to it. None for an unknown session or an Application of
    another Candidate."""
    session = repo.voice_session_speaker(conn, voice_session_id)
    if session is None:
        return None
    if application is not None and session["candidate_id"] != application["candidate_id"]:
        return None
    message, _ = repo.add_message(
        conn,
        candidate_id=session["candidate_id"],
        application_id=None if application is None else application["id"],
        channel=_CHANNEL,
        direction="outbound",
        sender="agent",
        body=spoken_disclosure("es", session["country"]),
        client_message_id=f"{DISCLOSURE_PREFIX}{voice_session_id}",
        modality="spoken",
        now=now,
    )
    if application is not None and message["application_id"] is None:
        link_spoken_disclosure(conn, voice_session_id, application)
        message = {**message, "application_id": application["id"]}
    return message


def link_spoken_disclosure(
    conn: sqlite3.Connection, voice_session_id: str, application: dict[str, Any]
) -> bool:
    """Put the session's stored disclosure, if it has no Application yet, on ``application``.
    Stores nothing. False for an unknown session, an Application of another Candidate, a
    session with no stored disclosure, or one already on an Application."""
    session = repo.voice_session_speaker(conn, voice_session_id)
    if session is None or session["candidate_id"] != application["candidate_id"]:
        return False
    return repo.link_message_to_application(
        conn,
        _CHANNEL,
        f"{DISCLOSURE_PREFIX}{voice_session_id}",
        session["candidate_id"],
        application["id"],
    )
