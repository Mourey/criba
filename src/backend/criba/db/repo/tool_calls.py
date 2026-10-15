"""Tool calls: the agent's tool-call trace (ADR 0069).

One ``tool_calls`` row per call through ``/internal/tools``, written by ``criba.tools.trace``
and read by the Operator's trace endpoint. A leaf module: it imports no sibling."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from criba.db.connection import iso


def limit_lock_wait(conn: sqlite3.Connection, milliseconds: int) -> None:
    """Make ``conn`` wait at most ``milliseconds`` for the writer lock (``connect`` sets 10 s)."""
    conn.execute(f"PRAGMA busy_timeout = {int(milliseconds)}")


def insert_tool_call(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    tool: str,
    started_at: datetime,
    duration_ms: int,
    outcome: str,
    error: str | None,
    args: str,
    now: datetime,
) -> int:
    """Store one call, only if the Application exists. Returns the rows written (0 or 1).

    ``args`` is the JSON text of the canonical arguments (``criba.tools.trace.stored_args``)."""
    cur = conn.execute(
        "INSERT INTO tool_calls (application_id, tool, started_at, duration_ms,"
        " outcome, error, args, created_at)"
        " SELECT ?, ?, ?, ?, ?, ?, ?, ?"
        " WHERE EXISTS (SELECT 1 FROM applications WHERE id = ?)",
        (
            application_id,
            tool,
            iso(started_at),
            max(duration_ms, 0),
            outcome,
            error,
            args,
            iso(now),
            application_id,
        ),
    )
    return cur.rowcount


def close_tool_calls(
    conn: sqlite3.Connection,
    application_id: str,
    run_id: str | None,
    started_at: datetime,
    now: datetime,
) -> int:
    """Give a run's id to the Application's calls that started between ``started_at`` and
    ``now`` (inclusive) and have no run and no closed mark; with no ``run_id``, mark them closed
    instead. Returns how many rows changed."""
    if run_id is None:
        sql = "UPDATE tool_calls SET run_closed = 1"
        params: tuple[str, ...] = ()
    else:
        sql = "UPDATE tool_calls SET run_id = ?"
        params = (run_id,)
    cur = conn.execute(
        f"{sql} WHERE application_id = ? AND run_id IS NULL AND run_closed = 0"
        " AND started_at BETWEEN ? AND ?",
        (*params, application_id, iso(started_at), iso(now)),
    )
    return cur.rowcount


def delete_tool_calls_before(conn: sqlite3.Connection, cutoff: datetime) -> int:
    """Delete the calls recorded before ``cutoff``. Returns how many rows went."""
    cur = conn.execute("DELETE FROM tool_calls WHERE created_at < ?", (iso(cutoff),))
    conn.commit()
    return cur.rowcount


def list_tool_calls(conn: sqlite3.Connection, application_id: str) -> list[sqlite3.Row]:
    """The Application's calls, oldest first."""
    return conn.execute(
        "SELECT id, run_id, tool, started_at, duration_ms, outcome, error, args"
        " FROM tool_calls WHERE application_id = ? ORDER BY id",
        (application_id,),
    ).fetchall()


def run_usage_rows(conn: sqlite3.Connection, application_id: str) -> list[sqlite3.Row]:
    """The usage rows of the Application's runs (``kind = 'run'`` with a run id), oldest first."""
    return conn.execute(
        "SELECT id, ts, run_id, status, input_tokens, output_tokens, est_cost_usd,"
        " latency_ms, profile, model FROM usage WHERE application_id = ? AND kind = 'run'"
        " AND run_id IS NOT NULL ORDER BY id",
        (application_id,),
    ).fetchall()


def jev_usage_rows(conn: sqlite3.Connection, application_id: str) -> list[sqlite3.Row]:
    """The usage rows of the Application's Jev calls (``kind = 'jev'``), oldest first: time,
    model, latency and status only (ADR 0075). Never a reading or a score."""
    return conn.execute(
        "SELECT ts, model, latency_ms, status FROM usage WHERE application_id = ?"
        " AND kind = 'jev' ORDER BY id",
        (application_id,),
    ).fetchall()


def run_reply_ids(conn: sqlite3.Connection, application_id: str) -> list[sqlite3.Row]:
    """``(id, run_id)`` of the Application's messages that carry a run id, oldest first."""
    return conn.execute(
        "SELECT id, run_id FROM messages WHERE application_id = ? AND run_id IS NOT NULL"
        " ORDER BY id",
        (application_id,),
    ).fetchall()
