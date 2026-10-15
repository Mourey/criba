"""Web chat channel (spec 002 task 16), implementing ``contracts/webchat.openapi.yaml``.

``chat_router`` is mounted by ``app.py`` at ``/api/chat``, before the ``/api`` router.

A conversation is one web chat identity of one Candidate (``candidate_identities`` with
``channel = 'webchat'`` and ``external_id = conversation_id``). Inbound messages are
normalised, stored verbatim before anything else, and handed to the router; the agent's
run happens in a background task, so ``POST .../messages`` answers as soon as the
message is stored. Clients read replies with ``GET .../messages``.

A voice conversation (ADR 0059) is a ``voice`` identity, started with ``channel: voice``. It is
the only kind that starts a call (``POST .../voice-session``); the same ``GET`` endpoints read its
transcript (the ``voice`` channel's messages), and it takes no typed message or tap (404).
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from criba.channel.errors import INT64_MAX, guarded_route, validation_message
from criba.channel.router import MessageIdReused
from criba.channel.sender import InvalidTap, OptionExpired, check_tap
from criba.channel.service import Channel
from criba.channel.voice_config import voice_available
from criba.channel.voice_session import (
    UNAVAILABLE,
    VoiceRuntime,
    VoiceSessionRefused,
    VoiceSessionStart,
    start_voice_session,
    voice_runtime_from_env,
)
from criba.db import repo
from criba.db.connection import iso
from criba.tools.booking import chat_invite_attachments

CHANNEL = "webchat"
VOICE_CHANNEL = "voice"
CONVERSATION_CHANNELS = (CHANNEL, VOICE_CHANNEL)
RATE_LIMIT = 30  # inbound messages per conversation
RATE_WINDOW_S = 60.0
LONG_POLL_STEP_S = 0.25
_URL = re.compile(r"https?://\S+")

# ---------------------------------------------------------------------------
# Request models (the contract's schemas)
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartConversationRequest(_Strict):
    country: Literal["ES", "MX"]
    phone: str | None = Field(default=None, pattern=r"^\+(34|52)[0-9]{8,11}$")
    locale: Literal["es-ES", "es-MX", "en"] | None = None
    client: Literal["test_chat", "simulator", "demo"] = "test_chat"
    channel: Literal["webchat", "voice"] = "webchat"
    persona_id: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")

    @model_validator(mode="after")
    def _persona_id_only_for_demo(self) -> StartConversationRequest:
        if self.persona_id is not None and self.client != "demo":
            raise ValueError("persona_id is accepted only with client demo.")
        return self


class Media(_Strict):
    type: Literal["audio", "image", "video", "document", "sticker"]
    filename: str | None = Field(default=None, max_length=255)


class SendMessageRequest(_Strict):
    message_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=20000)
    media: Media | None = None


class ButtonReplyRequest(_Strict):
    message_id: str = Field(min_length=1, max_length=128)
    in_reply_to: int = Field(ge=1, le=INT64_MAX)
    option_id: str = Field(min_length=1)


class StartVoiceSessionRequest(_Strict):
    """Empty: never an Application, Candidate or agent id."""


_PHONE_COUNTRY = {"+34": "ES", "+52": "MX"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def error(status: int, code: str, message: str, **headers: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status, headers=headers)


async def _parse[M: BaseModel](request: Request, model: type[M]) -> M | JSONResponse:
    try:
        payload = await request.json()
    except ValueError:
        return error(422, "validation_error", "The body must be JSON.")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "body"
        return error(422, "validation_error", f"{where}: {first['msg']}")


_channel_lock = threading.Lock()


def get_channel(request: Request) -> Channel:
    """The app's :class:`Channel`: ``app.state.criba_channel``, built from env on first use."""
    channel = getattr(request.app.state, "criba_channel", None)
    if channel is None:
        with _channel_lock:
            channel = getattr(request.app.state, "criba_channel", None)
            if channel is None:
                channel = Channel.from_env()
                request.app.state.criba_channel = channel
    return channel


_voice_lock = threading.Lock()


def get_voice(request: Request) -> VoiceRuntime:
    """The app's :class:`VoiceRuntime`: ``app.state.criba_voice``, else the integrator's
    ``app.state.criba_runtime.voice``, else one built from env on first use and kept."""
    voice: VoiceRuntime | None = getattr(request.app.state, "criba_voice", None)
    if voice is not None:
        return voice
    runtime = getattr(request.app.state, "criba_runtime", None)
    voice = getattr(runtime, "voice", None)
    if voice is not None:
        return voice
    with _voice_lock:
        voice = getattr(request.app.state, "criba_voice", None)
        if voice is None:
            voice = voice_runtime_from_env()
            request.app.state.criba_voice = voice
    return voice


class _RateLimiter:
    def __init__(self, limit: int, window_s: float) -> None:
        self.limit, self.window_s = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        """0 if allowed (and counted), else seconds to wait."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, int(self.window_s - (now - hits[0])) + 1)
            hits.append(now)
            return 0


def _limiter(request: Request) -> _RateLimiter:
    limiter = getattr(request.app.state, "criba_webchat_limiter", None)
    if limiter is None:
        limiter = _RateLimiter(RATE_LIMIT, RATE_WINDOW_S)
        request.app.state.criba_webchat_limiter = limiter
    return limiter


def _identity(conn: Any, conversation_id: str) -> dict[str, Any] | None:
    """The conversation's identity: ``candidate_id``, ``created_at`` and ``channel`` (web chat
    or voice), or None."""
    row = repo.conversation_identity(conn, CONVERSATION_CHANNELS, conversation_id, CHANNEL)
    return dict(row) if row else None


def _cursor(conn: Any, candidate_id: str, channel: str = CHANNEL) -> int:
    return repo.max_candidate_message_id(conn, candidate_id, channel)


def _language(conn: Any, candidate_id: str) -> str | None:
    """The Candidate's language (chosen at start or detected from the last exchange), or None."""
    return repo.candidate_language(conn, candidate_id)


def _conversation(
    conn: Any, conversation_id: str, identity: dict[str, Any], channel: Channel
) -> dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "created_at": identity["created_at"],
        "cursor": _cursor(conn, identity["candidate_id"], identity["channel"]),
        "persona_name": channel.settings.persona_name,
        "language": _language(conn, identity["candidate_id"]),
        "channel": identity["channel"],
    }


def _chat_message(conn: Any, row: dict[str, Any], channel: Channel) -> dict[str, Any]:
    """One stored message as the contract's ``ChatMessage``. The booking confirmation (the
    agent's first message after a chat booking) carries the ``.ics`` invite attachment."""
    sender = row["sender"]
    label = {
        "agent": channel.settings.persona_name,
        "human": channel.settings.team_label,
    }.get(sender)
    reply_to = None
    if row["content_type"] == "button_reply" and row["reply_option_id"]:
        reply_to = _reply_target(conn, row)
    links = []
    if row["content_type"] == "template":
        links = [{"url": url, "label": url} for url in _URL.findall(row["body"])]
    return {
        "id": row["id"],
        "message_id": row["client_message_id"],
        "direction": row["direction"],
        "sender": sender,
        "sender_label": label,
        "text": row["body"],
        "interactive": row["interactive"] if row["direction"] == "outbound" else None,
        "reply_to": reply_to,
        "links": links,
        "attachments": chat_invite_attachments(conn, row, now=channel.clock.now()),
        "created_at": row["created_at"],
        "modality": row["modality"],
    }


def _reply_target(conn: Any, row: dict[str, Any]) -> dict[str, Any] | None:
    """The interactive message a tap answered: the latest one before it with that option."""
    candidates = repo.recent_interactive_messages(conn, row["candidate_id"], row["id"])
    for candidate in candidates:
        options = json.loads(candidate["interactive"]).get("options", [])
        if any(o.get("id") == row["reply_option_id"] for o in options):
            return {"message_id": candidate["id"], "option_id": row["reply_option_id"]}
    return None


def _read_page(
    channel: Channel, conversation_id: str, after: int, limit: int
) -> dict[str, Any] | None:
    """One page of messages, with the run state.

    The run state is read **before** the messages: if ``run_finished`` is true, every
    reply of the finished runs is already stored, so this page (or the next, past
    ``limit``) holds it. The state covers all of the Candidate's Applications, because
    the agent may run on a closed one (a Knockout, ADR 0033).
    """
    with channel.connection() as conn:
        identity = _identity(conn, conversation_id)
        if identity is None:
            return None
        candidate_id = identity["candidate_id"]
        app_ids = repo.candidate_application_ids(conn, candidate_id)
        language = _language(conn, candidate_id)
        typing = any(channel.is_running(a) for a in app_ids)
        finished = channel.run_finished(app_ids)
        rows = repo.candidate_messages_after(conn, candidate_id, identity["channel"], after, limit)
        messages = []
        for r in rows:
            d = dict(r)
            if d["interactive"] is not None:
                d["interactive"] = json.loads(d["interactive"])
            messages.append(_chat_message(conn, d, channel))
    return {
        "cursor": messages[-1]["id"] if messages else after,
        "agent_typing": typing,
        "run_finished": finished,
        "persona_name": channel.settings.persona_name,
        "language": language,
        "messages": messages,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

BUSY_RETRY_AFTER_S = 2
BUSY_MESSAGE = "Hay mucha actividad ahora mismo. Espera un momento y vuelve a enviarlo."


def _busy() -> JSONResponse:
    """SQLite stayed locked past ``busy_timeout``: nothing was stored, so the client may send
    the same message again. The contract's retry answer is ``rate_limited`` with
    ``Retry-After``; it names no 503 for these routes."""
    return error(429, "rate_limited", BUSY_MESSAGE, **{"Retry-After": str(BUSY_RETRY_AFTER_S)})


chat_router = APIRouter(
    tags=["chat"],
    route_class=guarded_route(
        invalid=lambda exc: error(422, "validation_error", validation_message(exc)),
        busy=_busy,
        failed=lambda: error(500, "internal", "Unexpected error."),
    ),
)


@chat_router.post("/conversations", status_code=201)
async def start_conversation(request: Request) -> Any:
    body = await _parse(request, StartConversationRequest)
    if isinstance(body, JSONResponse):
        return body
    if body.phone and _PHONE_COUNTRY[body.phone[:3]] != body.country:
        return error(422, "validation_error", "phone does not match country (+34 ES, +52 MX).")
    channel = get_channel(request)
    kind = body.channel
    prefix = "wv" if kind == VOICE_CHANNEL else "wc"
    conversation_id = f"{prefix}_{secrets.token_hex(16)}"

    def work() -> dict[str, Any]:
        now = channel.clock.now()
        with channel.connection() as conn:
            existing = None
            if body.phone:
                existing = repo.first_candidate_by_phone(
                    conn, body.phone, channel.settings.client_id
                )
            if existing is not None:
                repo.add_candidate_identity(
                    conn,
                    existing["id"],
                    kind,
                    conversation_id,
                    iso(now),
                    body.client,
                    body.persona_id,
                )
                if body.locale:
                    repo.set_candidate_language(conn, existing["id"], body.locale, now=now)
            else:
                repo.get_or_create_candidate(
                    conn,
                    client_id=channel.settings.client_id,
                    channel=kind,
                    external_id=conversation_id,
                    phone=body.phone,
                    country=body.country,
                    language=body.locale,
                    now=now,
                    client=body.client,
                    persona_id=body.persona_id,
                )
            identity = _identity(conn, conversation_id)
            assert identity is not None
            return _conversation(conn, conversation_id, identity, channel)

    return JSONResponse(await run_in_threadpool(work), status_code=201)


@chat_router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, request: Request) -> Any:
    channel = get_channel(request)
    with channel.connection() as conn:
        identity = _identity(conn, conversation_id)
        if identity is None:
            return error(404, "not_found", "Conversation not found.")
        return _conversation(conn, conversation_id, identity, channel)


@chat_router.get("/conversations/{conversation_id}/messages")
async def read_messages(
    conversation_id: str,
    request: Request,
    after: int = 0,
    wait: int = 0,
    limit: int = 100,
    agent_typing: bool | None = None,
    run_finished: bool | None = None,
) -> Any:
    if not 0 <= after <= INT64_MAX or not 0 <= wait <= 30 or not 1 <= limit <= 200:
        return error(
            422,
            "validation_error",
            f"0 <= after <= {INT64_MAX}, 0 <= wait <= 30, 1 <= limit <= 200.",
        )
    channel = get_channel(request)
    deadline = time.monotonic() + wait
    # The run state the client last saw. Without it the poll can only compare against its own
    # first read, and a run that ends between two polls changes nothing it can see: the poll
    # would sit until the deadline. With both fields, a state that already differs returns at once.
    first: tuple[bool, bool] | None = None
    if agent_typing is not None and run_finished is not None:
        first = (agent_typing, run_finished)
    while True:
        page = await run_in_threadpool(_read_page, channel, conversation_id, after, limit)
        if page is None:
            return error(404, "not_found", "Conversation not found.")
        # A run that ends (or starts) without a new message is news too: a client waiting on
        # `run_finished` would otherwise sit until the deadline.
        state = (page["agent_typing"], page["run_finished"])
        if first is None:
            first = state
        if page["messages"] or state != first or time.monotonic() >= deadline:
            return page
        await asyncio.sleep(LONG_POLL_STEP_S)


def _accept(
    channel: Channel,
    request: Request,
    background: BackgroundTasks,
    conversation_id: str,
    *,
    message_id: str,
    text: str,
    content_type: str,
    media_type: str | None = None,
    tap: tuple[int, str] | None = None,
) -> Any:
    with channel.connection() as conn:
        identity = _identity(conn, conversation_id)
        if identity is None or identity["channel"] != CHANNEL:
            # A voice conversation takes no typed message or tap (ADR 0059).
            return error(404, "not_found", "Conversation not found.")
        candidate = repo.get_candidate(conn, identity["candidate_id"])
        assert candidate is not None
        seen = repo.client_message_seen(conn, CHANNEL, message_id)
        if not seen:
            wait_s = _limiter(request).retry_after(conversation_id)
            if wait_s:
                return error(
                    429,
                    "rate_limited",
                    "Vas muy rápido. Espera un momento y vuelve a escribir.",
                    **{"Retry-After": str(wait_s)},
                )
        option_id = None
        if tap is not None:
            in_reply_to, option_id = tap
            try:
                _, option = check_tap(conn, candidate["id"], in_reply_to, option_id)
            except OptionExpired:
                if not seen:
                    return error(
                        409,
                        "option_expired",
                        "Esas opciones ya no están disponibles. Escribe tu respuesta, por favor.",
                    )
                option = None
            except InvalidTap as exc:
                return error(422, "validation_error", str(exc))
            if option is not None:
                text = option["label"]
            else:  # a retried tap whose options have since expired: replay the original
                text = repo.stored_message_body(conn, CHANNEL, message_id)
        try:
            result = channel.router.accept_inbound(
                conn,
                candidate,
                channel=CHANNEL,
                message_id=message_id,
                text=text,
                content_type=content_type,
                media_type=media_type,
                reply_option_id=option_id,
            )
        except MessageIdReused:
            return error(
                409,
                "message_id_reused",
                "This message_id was already used for a different message.",
            )
    if result.turn is not None:
        channel.turn_accepted(result.turn)
        background.add_task(channel.run_turn, result.turn, accepted=True)
    return JSONResponse(
        {"message_id": message_id, "id": result.message["id"], "duplicate": result.duplicate},
        status_code=202,
    )


@chat_router.post("/conversations/{conversation_id}/messages", status_code=202)
async def send_message(conversation_id: str, request: Request, background: BackgroundTasks) -> Any:
    body = await _parse(request, SendMessageRequest)
    if isinstance(body, JSONResponse):
        return body
    channel = get_channel(request)
    return await run_in_threadpool(
        _accept,
        channel,
        request,
        background,
        conversation_id,
        message_id=body.message_id,
        text=body.text,
        content_type="media" if body.media else "text",
        media_type=body.media.type if body.media else None,
    )


@chat_router.post("/conversations/{conversation_id}/replies", status_code=202)
async def send_button_reply(
    conversation_id: str, request: Request, background: BackgroundTasks
) -> Any:
    body = await _parse(request, ButtonReplyRequest)
    if isinstance(body, JSONResponse):
        return body
    channel = get_channel(request)
    return await run_in_threadpool(
        _accept,
        channel,
        request,
        background,
        conversation_id,
        message_id=body.message_id,
        text="",
        content_type="button_reply",
        tap=(body.in_reply_to, body.option_id),
    )


@chat_router.post("/conversations/{conversation_id}/voice-session", status_code=201)
async def start_voice(conversation_id: str, request: Request) -> Any:
    """Start a voice call (spec voice-agent session-start SE4, SE5): the voice gate first,
    then the body (absent or ``{}``), then :func:`start_voice_session` on a worker thread."""
    voice = get_voice(request)
    if not voice_available(voice.settings):
        return error(503, "voice_unavailable", UNAVAILABLE)
    if (await request.body()).strip():
        body = await _parse(request, StartVoiceSessionRequest)
        if isinstance(body, JSONResponse):
            return body
    channel = get_channel(request)

    def work() -> VoiceSessionStart:
        with channel.connection() as conn:
            return start_voice_session(
                conn,
                conversation_id=conversation_id,
                settings=voice.settings,
                now=channel.clock.now(),
                client=voice.client,
                drift=voice.drift,
            )

    try:
        started = await run_in_threadpool(work)
    except VoiceSessionRefused as refused:
        headers = {"Retry-After": str(refused.retry_after)} if refused.retry_after else {}
        return error(refused.status, refused.code, refused.message, **headers)
    return JSONResponse(
        {
            "conversation_token": started.conversation_token,
            "voice_token": started.voice_token,
            "expires_at": iso(started.expires_at),
            "country": started.country,
        },
        status_code=201,
    )


__all__ = ["chat_router", "get_channel", "get_voice"]
