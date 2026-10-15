"""Recruiters never receive cost fields in stream frames either; Operators do (ADR 0019)."""

from __future__ import annotations

from typing import Any

from fastapi.responses import StreamingResponse

from criba.api import present, routing
from criba.api.auth import User
from criba.api.routing import (
    OPERATOR_ONLY_KEYS,
    new_router,
    strip_operator_fields,
    strip_operator_fields_from_frames,
)
from criba.api.streams import frame

from .support import OPERATOR, PUEBLA
from .test_cost_fields import keys_in
from .test_takeover import inbound, parse_sse, read_stream

LEAK = {
    "cost": {"est_cost_usd": 0.5, "reconciled_cost_usd": 0.6},
    "cost_usd": 0.7,
    "est_cost_usd": 0.8,
    "reconciled_cost_usd": 0.9,
}


def test_json_and_stream_filters_share_one_function_and_key_list(monkeypatch):
    """Both paths call the one function over the one key list, so they can't drift."""
    assert routing._filter.__globals__["strip_operator_fields"] is strip_operator_fields
    assert strip_operator_fields_from_frames.__globals__["strip_operator_fields"] is (
        strip_operator_fields
    )
    monkeypatch.setattr(routing, "OPERATOR_ONLY_KEYS", frozenset({"planted"}))
    assert strip_operator_fields({"planted": 1, "cost": 2}) == {"cost": 2}
    text = frame("event", "t", {"planted": 1, "cost": 2})
    assert parse_sse(strip_operator_fields_from_frames(text))[0]["data"]["data"] == {"cost": 2}


def test_frame_filter_keeps_the_frame_format():
    text = frame("message", "2026-10-09T10:00:00Z", {"id": 1, **LEAK, "n": [LEAK]}, "m-1")
    cleaned = strip_operator_fields_from_frames(text)
    assert cleaned == frame("message", "2026-10-09T10:00:00Z", {"id": 1, "n": [{}]}, "m-1")
    assert strip_operator_fields_from_frames(text.encode()) == cleaned.encode()
    assert strip_operator_fields_from_frames(memoryview(text.encode())) == cleaned.encode()
    for untouched in (": ping\n\n", ": connected\n\n", "data: not json\n\n"):
        assert strip_operator_fields_from_frames(untouched) == untouched


def test_planted_cost_in_a_relay_frame_reaches_operators_only(env, build, monkeypatch):
    original = present.message_with_name

    def leaky(conn, row):
        return {**original(conn, row), **LEAK}

    monkeypatch.setattr(present, "message_with_name", leaky)
    app = build.application()
    path = f"/api/applications/{app['id']}/stream"

    recruiter = read_stream(
        env.client(PUEBLA), path, act=lambda: inbound(env, app, "Hola, ¿sigue abierto?")
    )
    messages = [f for f in recruiter if f["event"] == "message"]
    assert messages, recruiter
    for f in messages:
        assert not keys_in(f["data"]) & OPERATOR_ONLY_KEYS, f
        assert f["data"]["data"]["body"] == "Hola, ¿sigue abierto?"

    operator = read_stream(env.client(OPERATOR), path, act=lambda: inbound(env, app, "Sigo aquí."))
    messages = [f for f in operator if f["event"] == "message"]
    assert messages, operator
    for f in messages:
        assert f["data"]["data"]["cost"] == LEAK["cost"]
        assert f["data"]["data"]["est_cost_usd"] == LEAK["est_cost_usd"]


def test_any_stream_under_the_api_is_filtered(env):
    """Defence in depth: the filter sits on the route class, so a new stream is covered."""
    leaky = new_router()

    @leaky.get("/leak/stream")
    def leak(user: User):
        async def frames():
            yield ": connected\n\n"
            yield frame("queue", "t", {"item_id": "q1", **LEAK}, "q-1").encode()

        return StreamingResponse(frames(), media_type="text/event-stream")

    env.app.include_router(leaky, prefix="/api")

    def read(email: str) -> list[dict[str, Any]]:
        with env.client(email).stream("GET", "/api/leak/stream") as response:
            return parse_sse("".join(response.iter_text()))

    (recruiter,) = read(PUEBLA)
    assert recruiter["data"]["data"] == {"item_id": "q1"}
    assert recruiter["id"] == "q-1"
    (operator,) = read(OPERATOR)
    assert operator["data"]["data"] == {"item_id": "q1", **LEAK}
