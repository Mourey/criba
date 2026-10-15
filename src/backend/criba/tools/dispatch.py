"""Run one tool call: validate ``args``, resolve the Application, call the tool.

:func:`call_tool` returns ``(http_status, body)`` where ``body`` is ``$defs/tool_response``:
``{ok: true, value}`` or ``{ok: false, error, hint}``. ``args`` that fail the input schema
give HTTP 422 with ``invalid_args``; every other tool-level result is HTTP 200.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Any

from criba.tools import application, booking, zones_lookup
from criba.tools.common import ToolError, load_application
from criba.tools.schema import ANSWER_SHAPES, validate_answer_value, validate_args

logger = logging.getLogger("criba.tools")

Tool = Callable[[sqlite3.Connection, dict[str, Any], dict[str, Any], datetime], dict[str, Any]]

TOOLS: dict[str, Tool] = {
    "get_application": application.get_application,
    "search_zones": zones_lookup.search_zones,
    "record_answer": application.record_answer,
    "request_review": application.request_review,
    "raise_escalation": application.raise_escalation,
    "finish_screening": application.finish_screening,
    "record_backout": application.record_backout,
    "offer_interview_slots": booking.offer_interview_slots,
    "book_interview": booking.book_interview,
}


def call_tool(
    conn: sqlite3.Connection,
    tool: str,
    args: Any,
    session_id: str,
    *,
    now: datetime,
) -> tuple[int, dict[str, Any]]:
    """Run ``tool`` for the Application ``session_id`` at time ``now``."""
    if tool not in TOOLS:
        raise KeyError(tool)
    problem = validate_args(tool, args)
    if tool == "record_answer" and (value_problem := _answer_value_problem(args, problem)):
        hint = (
            f"Invalid arguments for record_answer: {value_problem}. Map the Candidate's answer "
            "to that shape (never pass their words) and call again; this counts no miss."
        )
        return 422, ToolError("invalid_args", hint).body()
    if problem is not None:
        hint = (
            f"Invalid arguments for {tool}: {problem}. Use only the parameters in the tool's "
            "schema; never pass an application or candidate id."
        )
        return 422, ToolError("invalid_args", hint).body()
    try:
        app = load_application(conn, session_id)
        value = TOOLS[tool](conn, app, args, now)
    except ToolError as exc:
        return 200, exc.body()
    except Exception:
        logger.exception("tool %s failed for session %s", tool, session_id)
        return 200, ToolError(
            "internal",
            "Something went wrong on our side. Tell the Candidate you need a moment; if it "
            "keeps failing, raise_escalation(reason: other).",
        ).body()
    return 200, {"ok": True, "value": value}


def _answer_value_problem(args: Any, problem: str | None) -> str | None:
    """The per-field shape error of a ``record_answer`` value, naming the expected shape.

    Checked when the input schema passed, or failed only on ``value`` (a value that fits no
    field's form, such as a long text): either way the hint names the field's own shape.
    Free text is never parsed (ADR 0042).
    """
    if problem is not None and not problem.startswith("args.value"):
        return None
    if not isinstance(args, dict) or args.get("field") not in ANSWER_SHAPES or "value" not in args:
        return None
    return validate_answer_value(args["field"], args["value"])
