"""The custom-LLM endpoint ElevenLabs calls for each spoken turn (spec voice-agent spoken-turn
R4, R5.1, R12, R16.4), implementing ``voiceChatCompletion`` in ``contracts/voice.openapi.yaml``.

``voice_router`` is mounted by ``app.py`` at ``/api/voice``, before the ``/api`` router.

One request, in order, each check before the next starts (R4.1):

1. voice not available (``voice_available``): 404;
2. the bearer, compared as bytes in constant time with ``criba_voice_llm_secret``; a missing
   header, an empty value or an empty secret: 401;
3. the body, read in chunks on the event loop and refused once it passes ``MAX_BODY_BYTES``,
   whatever ``Content-Length`` says: 413; then parsed leniently (unknown fields and roles are
   ignored, ``content`` may be text parts): no ``user`` entry, or a blank last one: 422;
4. the voice token (``elevenlabs_extra_body.criba_voice_token``): missing or invalid: 401; a
   session a Takeover ended: the handover line and ``end_call``, with no write (R16.4);
5. the rate limit of the web chat, counted only for a new message id: 429.

Checks 1, 2, 4 and 5 run in the thread pool; none writes. Then
:meth:`~criba.channel.service.Channel.run_spoken_turn` runs the turn on its own thread and the
response only reads its queue: every text event through :class:`SpeechScrubber`, ``FIRST_CHUNK``
when nothing was written within ``FIRST_CHUNK_S`` (decision 33), and ``fallback_text`` once the
response deadline passes. Ids come from the verified token only (R5.1). Nothing here logs the
bearer, the token or any text.
"""

from __future__ import annotations

import hmac
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from criba.channel.copy import spoken_handover
from criba.channel.errors import guarded_route, validation_message
from criba.channel.sender import fallback_text
from criba.channel.service import (
    SPOKEN_CHANNEL,
    Channel,
    DeltaQueue,
    SpokenEvent,
    SpokenTurnRequest,
)
from criba.channel.voice_config import VoiceSettings, voice_available
from criba.channel.voice_session import (
    VoiceSessionEnded,
    VoiceSessionRef,
    VoiceTokenError,
    verify_voice_token,
)
from criba.channel.webchat import _limiter, error, get_channel, get_voice
from criba.db import repo

FIRST_CHUNK = "... "
FIRST_CHUNK_S = 2.0
MAX_BODY_BYTES = 512 * 1024
QUEUE_POLL_S = 0.25
MODEL = "criba-voice"
LINK_PHRASE = "el enlace que tienes en el chat"
# The Takeover end (decision 34): ElevenLabs acts on `end_call` only with a `reason`.
END_CALL_ARGUMENTS = {"reason": "takeover", "criba_end_reason": "takeover"}
DEADLINE_GRACE_S = 5.0


class _BadRequest(Exception):
    """The body is not a usable chat completion request."""


# ---------------------------------------------------------------------------
# The scrubber (R12.1)
# ---------------------------------------------------------------------------

_SCHEME = re.compile(r"https?://", re.IGNORECASE)
_HOST_PATH = re.compile(r"[a-z0-9-]+\.[a-z]{2,}/", re.IGNORECASE)
_COMMON_TLD = re.compile(r"[a-z0-9-]+\.(?:com|es|mx|work|net|org|io|app|dev)(?![a-z0-9])")
_BOOKING_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")
_TRAILING = ".,;:!?"
_MARKDOWN_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]*)\)")
_HOLD_BRACKET_CHARS = 300  # a "[" held this long without closing is released as text


def _unfinished_bracket(tail: str) -> bool:
    """``tail`` starts with "[" and may still become a Markdown link ``[text](url)``."""
    close = tail.find("]")
    if close < 0:
        return "\n" not in tail
    after = tail[close + 1 :]
    if not after:
        return True
    if after.startswith("("):
        return ")" not in after
    return False


def _is_link(token: str) -> bool:
    lowered = token.lower()
    return bool(
        _SCHEME.search(token)
        or lowered.startswith("www.")
        or _HOST_PATH.search(token)
        or _COMMON_TLD.search(token)
        or "localhost" in lowered
        or lowered.endswith(".ics")
        or "/book/" in lowered
        or _BOOKING_TOKEN.search(token)
    )


class SpeechScrubber:
    """Holds streamed text back to a word boundary and replaces each whitespace-delimited token
    that is a link (a URL with or without a scheme, an ``.ics`` path, a booking path or token)
    with :data:`LINK_PHRASE`; consecutive links become one phrase. Trailing punctuation stays.
    Applied by the response only: the stored message keeps the link."""

    def __init__(self) -> None:
        self._held = ""
        self._after_link = False  # the last token released was a replaced link

    def feed(self, text: str) -> str:
        """The scrubbed text up to the last whitespace; the rest is held."""
        self._held += text
        cut = max(self._held.rfind(c) for c in " \t\n\r")
        if cut < 0:
            return ""
        opened = self._held.rfind("[", 0, cut + 1)
        if (
            opened >= 0
            and len(self._held) - opened < _HOLD_BRACKET_CHARS
            and _unfinished_bracket(self._held[opened:])
        ):
            cut = opened - 1  # hold a Markdown link until it closes
            if cut < 0:
                return ""
        ready, self._held = self._held[: cut + 1], self._held[cut + 1 :]
        return self._scrub(ready)

    def flush(self) -> str:
        """Everything still held, scrubbed."""
        ready, self._held = self._held, ""
        return self._scrub(ready)

    def _scrub(self, text: str) -> str:
        text = _MARKDOWN_LINK.sub(
            lambda m: m.group(1) if _is_link(m.group(1)) else m.group(0), text
        )
        out: list[str] = []
        skip_space = False
        for part in re.split(r"(\s+)", text):
            if not part:
                continue
            if part.isspace():
                if not skip_space:
                    out.append(part)
                skip_space = False
                continue
            core = part.rstrip(_TRAILING)
            tail = part[len(core) :]
            if core and _is_link(core):
                if self._after_link and not tail:
                    skip_space = True  # merged into the phrase already spoken
                    continue
                if not self._after_link:
                    out.append(LINK_PHRASE)
                out.append(tail)
                self._after_link = not tail
                continue
            self._after_link = False
            out.append(part)
        return "".join(out)


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------


class _Frames:
    """``chat.completion.chunk`` frames of one response: one id, one ``created``."""

    def __init__(self) -> None:
        self.id = f"chatcmpl-{uuid.uuid4().hex}"
        self.created = int(time.time())

    def chunk(self, delta: dict[str, Any], finish_reason: str | None = None) -> str:
        body = {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": MODEL,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

    def text(self, text: str) -> str:
        return self.chunk({"content": text})

    def stop(self) -> str:
        return self.chunk({}, "stop")

    def end_call(self) -> list[str]:
        call = {
            "index": 0,
            "id": "call_end",
            "type": "function",
            "function": {"name": "end_call", "arguments": json.dumps(END_CALL_ARGUMENTS)},
        }
        return [self.chunk({"tool_calls": [call]}), self.chunk({}, "tool_calls")]


DONE = "data: [DONE]\n\n"


async def _stream(events: DeltaQueue, deadline_s: float) -> AsyncIterator[str]:
    """Read the worker's queue and write frames. A client that goes away ends this generator
    only; the worker carries on (R6.5)."""
    frames = _Frames()
    started = time.monotonic()
    deadline = started + deadline_s
    scrubber = SpeechScrubber()
    written = False
    yield frames.chunk({"role": "assistant", "content": ""})
    while True:
        now = time.monotonic()
        if now >= deadline:
            yield frames.text(fallback_text(None))
            yield frames.stop()
            yield DONE
            return
        wait = min(QUEUE_POLL_S, deadline - now)
        if not written:
            wait = max(0.0, min(wait, started + FIRST_CHUNK_S - now))
        event = await run_in_threadpool(events.get, wait)
        if event is None:
            if not written and time.monotonic() - started >= FIRST_CHUNK_S:
                yield frames.text(FIRST_CHUNK)
                written = True
            continue
        if event.kind == "text":
            spoken = scrubber.feed(event.text)
            if spoken:
                yield frames.text(spoken)
                written = True
            continue
        rest = scrubber.flush()
        if rest:
            yield frames.text(rest)
        if event.kind == "end_call":
            for frame in frames.end_call():
                yield frame
        else:
            yield frames.stop()
        yield DONE
        return


def _event_stream(events: DeltaQueue, deadline_s: float) -> StreamingResponse:
    return StreamingResponse(
        _stream(events, deadline_s),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _failed_turn() -> StreamingResponse:
    """The answer to a turn that failed on our side (SQLite busy, or any unexpected error):
    the fallback line, spoken like the reply to a run that missed its deadline. A 200 stream
    is the one answer ``contracts/voice.openapi.yaml`` gives that keeps the call going; its
    ``VoiceError`` has no ``internal`` code. Nothing was stored, and nothing of the error is
    in the frames."""

    async def frames() -> AsyncIterator[str]:
        out = _Frames()
        yield out.chunk({"role": "assistant", "content": ""})
        yield out.text(fallback_text(None))
        yield out.stop()
        yield DONE

    return StreamingResponse(
        frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


voice_router = APIRouter(
    route_class=guarded_route(
        invalid=lambda exc: error(422, "validation_error", validation_message(exc)),
        busy=_failed_turn,
        failed=_failed_turn,
    )
)


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


def _not_found() -> JSONResponse:
    return error(404, "not_found", "Not found.")


def _unauthorized() -> JSONResponse:
    return error(401, "unauthorized", "Invalid credentials.")


def _gate_and_bearer(settings: VoiceSettings, header: str | None) -> JSONResponse | None:
    """Checks 1 and 2: a refusal, or None to go on."""
    if not voice_available(settings):
        return _not_found()
    secret = settings.read_secret("criba_voice_llm_secret")
    scheme, _, value = (header or "").partition(" ")
    value = value.strip()
    if not secret or not value or scheme.lower() != "bearer":
        return _unauthorized()
    if not hmac.compare_digest(value.encode("utf-8"), secret.encode("utf-8")):
        return _unauthorized()
    return None


async def _read_body(request: Request) -> bytes | None:
    """The body, or None as soon as it passes ``MAX_BODY_BYTES``."""
    parts: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            return None
        parts.append(chunk)
    return b"".join(parts)


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if not (isinstance(part, dict) and part.get("type") == "text"):
                raise _BadRequest()
            if not isinstance(part.get("text"), str):
                raise _BadRequest()
            texts.append(part["text"])
        return "".join(texts)
    raise _BadRequest()


def _parse(raw: bytes) -> tuple[str, int, str | None]:
    """``(last user text, user count, voice token or None)``; :class:`_BadRequest` otherwise.
    Only ``messages[].role``, ``messages[].content`` and the token are read."""
    try:
        body = json.loads(raw)
    except ValueError:
        raise _BadRequest() from None
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        raise _BadRequest()
    users = []
    for message in body["messages"]:
        if not isinstance(message, dict):
            raise _BadRequest()
        if message.get("role") == "user":
            users.append(_text(message.get("content")))
    if not users or not users[-1].strip():
        raise _BadRequest()
    extra = body.get("elevenlabs_extra_body")
    token = extra.get("criba_voice_token") if isinstance(extra, dict) else None
    return users[-1].strip(), len(users), token if isinstance(token, str) and token else None


def _ended(ref: VoiceSessionRef) -> DeltaQueue:
    """R16.4: the line and ``end_call``, with nothing written."""
    events = DeltaQueue()
    events.put(SpokenEvent("text", spoken_handover(None, ref.country)))
    events.put(SpokenEvent("end_call"))
    return events


def _admit(
    channel: Channel, request: Request, settings: VoiceSettings, token: str, n: int
) -> VoiceSessionRef | JSONResponse:
    """Checks 4 and 5 on one connection, closed before the stream starts. Raises
    :class:`VoiceSessionEnded` for R16.4."""
    secret = settings.read_secret("criba_voice_secret")
    with channel.connection() as conn:
        try:
            ref = verify_voice_token(
                conn, token, now=channel.clock.now(), secret=secret.encode("utf-8")
            )
        except VoiceSessionEnded:
            raise
        except VoiceTokenError:
            return _unauthorized()
        message_id = f"vc_{ref.voice_session_id}_{n}"
        seen = repo.client_message_seen(conn, SPOKEN_CHANNEL, message_id)
    if not seen:
        wait_s = _limiter(request).retry_after(ref.conversation_id)
        if wait_s:
            return error(
                429,
                "rate_limited",
                "Vas muy rápido. Espera un momento y vuelve a escribir.",
                **{"Retry-After": str(wait_s)},
            )
    return ref


@voice_router.post("/v1/chat/completions", include_in_schema=False)
async def chat_completions(request: Request) -> Any:
    voice = get_voice(request)
    refused = await run_in_threadpool(
        _gate_and_bearer, voice.settings, request.headers.get("authorization")
    )
    if refused is not None:
        return refused
    raw = await _read_body(request)
    if raw is None:
        return error(413, "payload_too_large", "Request body too large.")
    try:
        text, n, token = _parse(raw)
    except _BadRequest:
        return error(422, "validation_error", "Invalid chat completion request.")
    if token is None:
        return _unauthorized()
    channel = get_channel(request)
    deadline_s = channel.settings.run_timeout_s + DEADLINE_GRACE_S
    try:
        admitted = await run_in_threadpool(_admit, channel, request, voice.settings, token, n)
    except VoiceSessionEnded as ended:
        return _event_stream(_ended(ended.ref), deadline_s)
    if isinstance(admitted, JSONResponse):
        return admitted
    events = channel.run_spoken_turn(
        SpokenTurnRequest(
            conversation_id=admitted.conversation_id,
            candidate_id=admitted.candidate_id,
            voice_session_id=admitted.voice_session_id,
            message_id=f"vc_{admitted.voice_session_id}_{n}",
            text=text,
        )
    )
    return _event_stream(events, deadline_s)


__all__ = [
    "DEADLINE_GRACE_S",
    "END_CALL_ARGUMENTS",
    "FIRST_CHUNK",
    "FIRST_CHUNK_S",
    "LINK_PHRASE",
    "MAX_BODY_BYTES",
    "MODEL",
    "QUEUE_POLL_S",
    "SpeechScrubber",
    "voice_router",
]
