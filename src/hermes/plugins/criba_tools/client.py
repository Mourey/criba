"""Forwards one tool call to the backend: ``POST /internal/tools/{name}``.

Request body is exactly ``{"tool": <name>, "args": <what the model passed>, "session_id": <id>}``
(``contracts/tools.json``, ``$defs/tool_request``), with ``Authorization: Bearer <secret>``.
The backend answers HTTP 200 with ``{ok: true, value}`` or ``{ok: false, error, hint}`` (and
HTTP 422 with an ``invalid_args`` error body). Anything else is turned into an ``internal``
tool error here, so a handler never raises into the agent loop.

Standard library only (``urllib``): the handler runs on the agent's own thread, so a blocking
call is correct, and the plugin needs no dependency in the Hermes image.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("criba_tools")

BACKEND_URL_ENV = "CRIBA_BACKEND_URL"
DEFAULT_BACKEND_URL = "http://backend:8000"
SECRET_ENV = "CRIBA_TOOLS_SECRET"
SECRET_FILE_ENV = "CRIBA_TOOLS_SECRET_FILE"
DEFAULT_SECRET_FILE = "/run/secrets/criba_tools_secret"
TIMEOUT_ENV = "CRIBA_TOOLS_TIMEOUT"
DEFAULT_TIMEOUT_SECONDS = 15.0

# Bodies larger than this are not a tool result; refuse rather than feed them to the model.
MAX_RESPONSE_BYTES = 256 * 1024


def tool_error(error: str, hint: str, **extra: Any) -> dict[str, Any]:
    """A contract-shaped ``{ok: false, error, hint}`` (``$defs/tool_error``)."""
    return {"ok": False, "error": error, "hint": hint, **extra}


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def backend_url() -> str:
    return (os.environ.get(BACKEND_URL_ENV) or DEFAULT_BACKEND_URL).rstrip("/")


def timeout_seconds() -> float:
    raw = os.environ.get(TIMEOUT_ENV)
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def read_secret() -> str:
    """The shared secret. ``$CRIBA_TOOLS_SECRET`` wins; otherwise the Compose secret file."""
    direct = os.environ.get(SECRET_ENV, "").strip()
    if direct:
        return direct
    path = Path(os.environ.get(SECRET_FILE_ENV) or DEFAULT_SECRET_FILE)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _parse_tool_body(raw: bytes) -> dict[str, Any] | None:
    """The decoded body if it is a contract-shaped tool result, else None."""
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if isinstance(body, dict) and isinstance(body.get("ok"), bool):
        return body
    return None


def forward(
    tool: str,
    args: dict[str, Any] | None,
    session_id: str | None,
    *,
    timeout: float | None = None,
) -> str:
    """Call the backend for one tool invocation and return the JSON string for the model.
    ``timeout`` (seconds) overrides ``$CRIBA_TOOLS_TIMEOUT`` for this call."""
    if not session_id:
        # The dispatcher always supplies a session id for a Runs API turn; without one the
        # backend could not tell whose Application this is, so do not guess.
        return dumps(
            tool_error(
                "unknown_session",
                "No session is attached to this call. Tell the Candidate you need a moment "
                "and raise an Escalation if it persists.",
            )
        )

    secret = read_secret()
    if not secret:
        logger.error(
            "%s and %s are both empty; cannot authenticate to the backend",
            SECRET_ENV,
            SECRET_FILE_ENV,
        )
        return dumps(
            tool_error(
                "internal",
                "The tool service is not configured. Tell the Candidate you need a moment.",
            )
        )

    payload = json.dumps(
        {
            "tool": tool,
            "args": args if isinstance(args, dict) else {},
            "session_id": session_id,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{backend_url()}/internal/tools/{tool}",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {secret}",
        },
    )

    started = time.monotonic()
    status: int | str = "error"
    try:
        try:
            with urllib.request.urlopen(request, timeout=timeout or timeout_seconds()) as response:
                status = response.status
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("tool %s session %s: backend unreachable: %s", tool, session_id, exc)
            return dumps(
                tool_error(
                    "internal",
                    "The tool service did not answer. Tell the Candidate you need a moment; "
                    "do not guess the result.",
                )
            )
    finally:
        logger.info(
            "tool %s session %s -> %s in %d ms",
            tool,
            session_id,
            status,
            int((time.monotonic() - started) * 1000),
        )

    if len(raw) > MAX_RESPONSE_BYTES:
        return dumps(tool_error("internal", "The tool service returned an oversized answer."))

    # HTTP 200 carries any tool-level result; HTTP 422 carries an invalid_args tool_error.
    if status in (200, 422):
        body = _parse_tool_body(raw)
        if body is not None:
            return dumps(body)
        return dumps(tool_error("internal", "The tool service returned an unreadable answer."))

    if status == 401:
        logger.error("backend rejected the tools secret for tool %s", tool)
    return dumps(
        tool_error(
            "internal",
            f"The tool service refused the call (HTTP {status}). Tell the Candidate you need "
            "a moment; do not guess the result.",
        )
    )
