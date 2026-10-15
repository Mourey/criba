"""The agent tool-call trace: one ``tool_calls`` row per ``/internal/tools`` call (ADR 0069).

:func:`record` is called by the HTTP router after ``call_tool`` returns. It is best effort: it
opens its own connection, waits at most :data:`BUSY_TIMEOUT_MS` for the writer lock, and never
raises, so a failed write leaves the tool's response, status code and timing as they were.

Arguments are stored as field names and canonical values only (ADR 0035). A value is kept only
for the keys in :data:`ALLOWED_ARGS`, whose ``tools.json`` schema is an enum or a boolean and
which the value must satisfy, and for ``record_answer.value`` under the rules in
:func:`stored_args`. Every other key keeps its name with a null value. A test reads
``tools.json`` and fails if an allowed key stops being an enum or a boolean.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from criba.db import repo
from criba.db.connection import transaction
from criba.tools.schema import ANSWER_SHAPES, answer_form, check_value, input_schema

log = logging.getLogger("criba.tools")

BUSY_TIMEOUT_MS = 250

ALLOWED_ARGS: dict[str, frozenset[str]] = {
    "get_application": frozenset({"language"}),
    "search_zones": frozenset({"country"}),
    "record_answer": frozenset({"field"}),
    "raise_escalation": frozenset({"reason"}),
    "record_backout": frozenset({"kind"}),
    "offer_interview_slots": frozenset({"reschedule"}),
}

_INVALID_CODES = frozenset({"invalid_args", "invalid_value"})
_VALUE_OUTCOMES = frozenset({"ok", "knockout"})


def outcome_of(payload: dict[str, Any]) -> tuple[str, str | None]:
    """The trace outcome and error code of a tool response.

    ``ok`` for ``ok: true``, except a Knockout, which ``record_answer`` returns as an accepted
    call whose value says ``knockout``; ``invalid`` for the two validation codes; ``error`` for
    every other code. The code is kept for ``invalid`` and ``error``."""
    if payload.get("ok"):
        value = payload.get("value")
        if isinstance(value, dict) and value.get("status") == "knockout":
            return "knockout", None
        return "ok", None
    code = payload.get("error")
    error = code if isinstance(code, str) else None
    return ("invalid" if error in _INVALID_CODES else "error"), error


def stored_args(tool: str, args: Any, outcome: str) -> dict[str, Any]:
    """``args`` as the trace keeps them: the tool's own keys, canonical values or null."""
    if not isinstance(args, dict):
        return {}
    props = input_schema(tool).get("properties", {})
    allowed = ALLOWED_ARGS.get(tool, frozenset())
    stored: dict[str, Any] = {}
    for key, value in args.items():
        if key not in props:
            continue
        keep = key in allowed and check_value(value, props[key], key) is None
        if tool == "record_answer" and key == "value":
            keep = _answer_value_is_canonical(args, outcome)
        stored[key] = value if keep else None
    return stored


def _answer_value_is_canonical(args: dict[str, Any], outcome: str) -> bool:
    """A ``record_answer`` value is kept when the call was accepted, the field is not the name
    and the value has the field's structured shape (ADR 0042)."""
    field = args.get("field")
    if outcome not in _VALUE_OUTCOMES or not isinstance(field, str):
        return False
    if field == "full_name" or field not in ANSWER_SHAPES:
        return False
    return answer_form(field, args.get("value")) == "structured"


def record(
    connection_factory: Callable[[], sqlite3.Connection],
    *,
    tool: str,
    args: Any,
    session_id: str,
    payload: dict[str, Any],
    started_at: datetime,
    duration_ms: int,
    now: datetime,
) -> None:
    """Write one ``tool_calls`` row for the Application ``session_id``. Never raises.

    The row is written only if that Application exists, so a call on an unknown session leaves
    nothing. A failure is logged without the arguments."""
    try:
        outcome, error = outcome_of(payload)
        stored = json.dumps(stored_args(tool, args, outcome), ensure_ascii=False)
        conn = connection_factory()
        try:
            repo.limit_lock_wait(conn, BUSY_TIMEOUT_MS)
            with transaction(conn):
                repo.insert_tool_call(
                    conn,
                    application_id=session_id,
                    tool=tool,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    outcome=outcome,
                    error=error,
                    args=stored,
                    now=now,
                )
        finally:
            conn.close()
    except Exception:
        log.warning("tool trace write failed for %s", tool, exc_info=True)


def close_run(
    conn: sqlite3.Connection,
    application_id: str,
    run_id: str | None,
    started_at: datetime,
    now: datetime,
) -> int:
    """Close one run's window on the Application's rows; returns how many rows it touched.

    The rows that have no run id, are not closed and started between ``started_at`` and ``now``
    (both inclusive) get ``run_id``, or, when the run has no id (a failure before Hermes gave
    one), are marked closed so a later run never claims them. A call that arrives after its
    run's window closed is not touched, unless it lands inside a later run's window (ADR 0069).
    """
    with transaction(conn):
        return repo.close_tool_calls(conn, application_id, run_id, started_at, now)


def purge_tool_calls(conn: sqlite3.Connection, now: datetime, days: int) -> int:
    """Delete trace rows older than ``days``. Returns how many rows went. The caller passes the
    period the other short-lived traces use, so the two are kept for as long as each other."""
    return repo.delete_tool_calls_before(conn, now - timedelta(days=days))
