"""Server-sent events plumbing (spec 003: live updates use SSE, never WebSockets).

A stream polls the database on a short interval, which keeps every writer (the router,
the scheduler, this API) free of any fan-out code. Each stream lives for at most
``sse_max_seconds``; the browser's ``EventSource`` then reconnects and sends
``Last-Event-ID``, so a dropped or expired connection loses nothing. Named events are
``message``, ``control``, ``event`` and ``queue``; each ``data:`` line is a JSON
``StreamEvent`` (``{type, at, data}``).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Tell nginx not to buffer the stream.
    "X-Accel-Buffering": "no",
}


def frame(event: str, at: str, data: dict[str, Any], event_id: str | None = None) -> str:
    """One SSE frame carrying a ``StreamEvent``."""
    payload = json.dumps(
        {"type": event, "at": at, "data": data}, ensure_ascii=False, separators=(",", ":")
    )
    head = f"id: {event_id}\n" if event_id else ""
    return f"{head}event: {event}\ndata: {payload}\n\n"


async def stream(
    request: Request,
    *,
    poll: Callable[[], tuple[list[str], bool]],
    poll_seconds: float,
    max_seconds: float,
    heartbeat_seconds: float,
) -> AsyncIterator[str]:
    """Drive ``poll`` until the client leaves, the session ends or the lifetime is up.

    ``poll`` runs in a worker thread and returns ``(frames, keep_going)``.
    """
    started = last_sent = time.monotonic()
    yield ": connected\n\n"
    while True:
        if await request.is_disconnected():
            return
        frames, keep_going = await asyncio.to_thread(poll)
        for item in frames:
            yield item
        now = time.monotonic()
        if frames:
            last_sent = now
        elif now - last_sent >= heartbeat_seconds:
            yield ": ping\n\n"
            last_sent = now
        if not keep_going or now - started >= max_seconds:
            return
        await asyncio.sleep(poll_seconds)


def sse_response(generator: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)
