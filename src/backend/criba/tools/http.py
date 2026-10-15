"""Internal agent-tools router: ``POST /internal/tools/{name}`` (spec 002 task 11, ADR 0026).

Called only by the Hermes ``criba-tools`` plugin on the Compose network, with
``Authorization: Bearer <shared secret>`` and the body ``{tool, args, session_id}``
(``contracts/tools.json``, ``$defs/tool_request``). The integrator mounts :data:`router`
with ``prefix="/internal/tools"``.

Status codes (``$defs/tool_response``): 200 for every tool-level result, ``ok: false``
included; 401 for a missing or wrong secret; 404 for an unknown tool; 422 with an
``invalid_args`` body when the envelope or ``args`` fail the schema.

Dependencies to override (``app.dependency_overrides``): :func:`get_connection_factory`
(default: ``criba.db.connect($CRIBA_DB_PATH)``), :func:`get_clock` (default: system
time; the simulator swaps in its clock) and :func:`get_after_call` (default: none). An
after-call hook runs as a background task once the response is sent, after the tool's
transaction committed, with ``(tool, session_id, response_body)``; the integrator uses it
to write the summary when Screening ends.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from criba.db import connect
from criba.screening import Clock, SystemClock
from criba.tools import trace
from criba.tools.common import ToolError
from criba.tools.dispatch import TOOLS, call_tool

logger = logging.getLogger("criba.tools")

SECRET_FILE_ENV = "CRIBA_TOOLS_SECRET_FILE"
DB_PATH_ENV = "CRIBA_DB_PATH"
MAX_BODY_BYTES = 64 * 1024
_ENVELOPE_KEYS = frozenset({"tool", "args", "session_id"})

ConnectionFactory = Callable[[], sqlite3.Connection]
AfterCall = Callable[[str, str, dict[str, Any]], None]

router = APIRouter(tags=["internal-tools"])
_system_clock = SystemClock()


def get_connection_factory() -> ConnectionFactory:
    path = os.environ.get(DB_PATH_ENV, "/data/criba.db")
    return lambda: connect(path)


def get_clock() -> Clock:
    return _system_clock


def get_after_call() -> AfterCall | None:
    return None


def read_secret() -> str:
    """The shared secret from ``$CRIBA_TOOLS_SECRET_FILE`` ('' if unset or unreadable)."""
    path = os.environ.get(SECRET_FILE_ENV)
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def authorized(header: str | None) -> bool:
    secret = read_secret()
    if not secret:
        logger.error("%s is unset or empty: refusing every tool call", SECRET_FILE_ENV)
        return False
    scheme, _, token = (header or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return hmac.compare_digest(token.strip().encode(), secret.encode())


def _invalid(hint: str) -> JSONResponse:
    return JSONResponse(ToolError("invalid_args", hint).body(), status_code=422)


def _envelope_problem(name: str, body: Any) -> str | None:
    if not isinstance(body, dict):
        return "the request body must be a JSON object {tool, args, session_id}"
    keys = set(body)
    if keys != _ENVELOPE_KEYS:
        missing, extra = sorted(_ENVELOPE_KEYS - keys), sorted(keys - _ENVELOPE_KEYS)
        return f"envelope keys are wrong (missing: {missing}, unexpected: {extra})"
    if body["tool"] != name:
        return "body.tool must equal the tool name in the path"
    if not isinstance(body["args"], dict):
        return "args must be a JSON object"
    if not isinstance(body["session_id"], str) or not body["session_id"]:
        return "session_id must be a non-empty string"
    return None


@router.post("/{name}", include_in_schema=False)
async def call(
    name: str,
    request: Request,
    connection_factory: Annotated[ConnectionFactory, Depends(get_connection_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
    after_call: Annotated[AfterCall | None, Depends(get_after_call)],
    background: BackgroundTasks,
) -> JSONResponse:
    if not authorized(request.headers.get("authorization")):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    if name not in TOOLS:
        return JSONResponse({"detail": f"unknown tool {name!r}"}, status_code=404)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return _invalid("The request body is too large.")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _invalid("The request body is not valid JSON.")
    if problem := _envelope_problem(name, body):
        return _invalid(f"Malformed tool request: {problem}.")

    def run() -> tuple[int, dict[str, Any]]:
        conn = connection_factory()
        started_at = clock.now()
        started = time.monotonic()
        try:
            status, payload = call_tool(
                conn, name, body["args"], body["session_id"], now=started_at
            )
        finally:
            conn.close()
        if payload.get("error") != "unknown_session":
            trace.record(
                connection_factory,
                tool=name,
                args=body["args"],
                session_id=body["session_id"],
                payload=payload,
                started_at=started_at,
                duration_ms=round((time.monotonic() - started) * 1000),
                now=clock.now(),
            )
        return status, payload

    status, payload = await run_in_threadpool(run)
    if after_call is not None and payload.get("ok"):
        background.add_task(after_call, name, body["session_id"], payload)
    return JSONResponse(payload, status_code=status, background=background)
