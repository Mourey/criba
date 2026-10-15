"""The signed post-call webhook ElevenLabs calls after each voice call (spec voice-agent
session-webhook: SE11, SE12; parent R19). Served at ``POST /api/voice/webhooks/post-call``
(``contracts/voice.openapi.yaml``, ``voicePostCallWebhook``).

Verification comes first (SE11), in this order, and no payload field is read before the
signature and its window pass:

1. voice not available: 404;
2. the body, read in chunks and refused as soon as it passes ``MAX_BODY_BYTES``, whatever
   ``Content-Length`` says: 413;
3. ``ElevenLabs-Signature`` (``t=<unix seconds>,v0=<64 hex>``, HMAC-SHA256 of
   ``"{t}." + raw body`` with the webhook secret) missing, malformed or wrong, or the secret
   empty: 401;
4. ``t`` more than ``TOLERANCE_S`` from the channel clock: 401;
5. not a JSON object with a string ``type``: 400;
6. any type other than ``post_call_transcription``: 200, nothing written;
7. an agent that is not ours: 200, nothing written.

The body carries Candidate speech and the voice token: it is never logged or stored. Only the
agent id, conversation id, type and call duration are read from it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from criba.channel.errors import INT64_MAX, guarded_route, validation_message
from criba.channel.voice_config import VoiceSettings, voice_available
from criba.channel.voice_session import end_voice_session
from criba.channel.webchat import error, get_channel, get_voice
from criba.db import repo
from criba.db.connection import iso, transaction

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 262_144
TOLERANCE_S = 300
SPEECH_MODEL = "elevenlabs-convai"
SIGNATURE_HEADER = "ElevenLabs-Signature"
TRANSCRIPTION = "post_call_transcription"

_SIGNATURE = re.compile(r"t=([0-9]{1,12}),v0=([0-9a-f]{64})")

RETRY_AFTER_S = 60


def _retry_later() -> JSONResponse:
    """The answer when the write failed on our side (SQLite busy, or any unexpected error):
    nothing was written, and a resent webhook is safe (a replay is skipped). The contract's
    ``VoiceError`` has no ``internal`` code; ``rate_limited`` with ``Retry-After`` is its one
    "try again later"."""
    return error(429, "rate_limited", "Try again later.", **{"Retry-After": str(RETRY_AFTER_S)})


voice_webhook_router = APIRouter(
    route_class=guarded_route(
        invalid=lambda exc: error(400, "validation_error", validation_message(exc)),
        busy=_retry_later,
        failed=_retry_later,
    )
)


@dataclass(frozen=True)
class _Call:
    """The four values the webhook reads from a transcription; nothing else is kept."""

    agent_id: str
    conversation_id: str
    duration: int


def _not_found() -> JSONResponse:
    return error(404, "not_found", "Not found.")


def _unauthorized() -> JSONResponse:
    return error(401, "unauthorized", "Invalid credentials.")


def _invalid() -> JSONResponse:
    return error(400, "validation_error", "Invalid webhook payload.")


def _received() -> JSONResponse:
    return JSONResponse({"status": "received"})


async def _read_capped(request: Request) -> bytes | None:
    """The body, or None as soon as it passes ``MAX_BODY_BYTES``."""
    parts: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            return None
        parts.append(chunk)
    return b"".join(parts)


def _verified(settings: VoiceSettings, header: str | None, raw: bytes, now: datetime) -> bool:
    """True only for a well-formed header whose HMAC matches and whose ``t`` is in the
    window. An empty secret refuses every call, with one ERROR naming its variable."""
    secret = settings.read_secret("elevenlabs_webhook_secret")
    if not secret:
        logger.error(
            "CRIBA_ELEVENLABS_WEBHOOK_SECRET_FILE is unset or empty: refusing every post-call"
            " webhook"
        )
        return False
    match = _SIGNATURE.fullmatch(header or "")
    if match is None:
        return False
    t, given = match.group(1), match.group(2)
    expected = hmac.new(
        secret.encode("utf-8"), t.encode("ascii") + b"." + raw, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, given):
        return False
    return abs(now.timestamp() - int(t)) <= TOLERANCE_S


def _transcription(payload: dict[str, Any]) -> _Call | None:
    """The agent id, conversation id and duration of a transcription, or None when one is
    missing or of the wrong type."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    metadata = data.get("metadata")
    duration = metadata.get("call_duration_secs") if isinstance(metadata, dict) else None
    agent_id, conversation_id = data.get("agent_id"), data.get("conversation_id")
    if not (isinstance(agent_id, str) and agent_id):
        return None
    if not (isinstance(conversation_id, str) and conversation_id):
        return None
    if not isinstance(duration, int) or isinstance(duration, bool):
        return None
    if not 0 <= duration <= INT64_MAX:
        return None
    return _Call(agent_id=agent_id, conversation_id=conversation_id, duration=duration)


def _record_speech(
    conn: sqlite3.Connection, settings: VoiceSettings, call: _Call, now: datetime
) -> None:
    """SE12, in one ``BEGIN IMMEDIATE``: find the voice session by its ElevenLabs
    conversation id, skip a replay (a speech row already on that session), take the
    Application from this session's latest spoken turn, write one speech usage row, then end
    the session (a session already ended keeps its ``ended_at`` and ``seconds``)."""
    with transaction(conn):
        session = repo.voice_session_by_conversation(conn, call.conversation_id)
        if session is None:
            logger.warning("post-call webhook for a conversation with no voice session: ignored")
            return
        session_id, candidate_id = session["id"], session["candidate_id"]
        if repo.speech_usage_exists(conn, session_id):
            return
        application_id = repo.application_of_latest_message_with_prefix(
            conn, candidate_id, f"vc_{session_id}_"
        )
        repo.insert_speech_usage(
            conn,
            ts=iso(now),
            environment=settings.environment,
            model=SPEECH_MODEL,
            session_id=session_id,
            candidate_id=candidate_id,
            application_id=application_id,
            audio_seconds=float(call.duration),
            est_cost_usd=round(call.duration / 60 * settings.cost_per_min, 6),
        )
        end_voice_session(conn, session_id, now=now, seconds=float(call.duration))


@voice_webhook_router.post("/webhooks/post-call", include_in_schema=False)
async def post_call(request: Request) -> JSONResponse:
    settings = get_voice(request).settings
    if not voice_available(settings):
        return _not_found()
    raw = await _read_capped(request)
    if raw is None:
        return error(413, "payload_too_large", "Request body too large.")
    channel = get_channel(request)
    now = channel.clock.now()
    if not _verified(settings, request.headers.get(SIGNATURE_HEADER), raw, now):
        return _unauthorized()
    try:
        payload = json.loads(raw)
    except ValueError:
        return _invalid()
    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
        return _invalid()
    if payload["type"] != TRANSCRIPTION:
        return _received()
    call = _transcription(payload)
    del payload
    if call is None:
        return _invalid()
    if call.agent_id not in {settings.agent_es, settings.agent_mx}:
        return _received()

    def write() -> None:
        with channel.connection() as conn:
            _record_speech(conn, settings, call, now)

    await run_in_threadpool(write)
    return _received()


__all__ = [
    "MAX_BODY_BYTES",
    "SIGNATURE_HEADER",
    "SPEECH_MODEL",
    "TOLERANCE_S",
    "voice_webhook_router",
]
