"""Held actions (quiet-hours deferral, Q41): the reads and writes behind
``criba.scheduler.deferral``.

None of these opens a transaction. The claim and the finish are compare-and-set on the row's
``status`` (and ``attempts``) and return whether this caller won; the attempt limit and the
retry decision stay with the caller. Time values come in as ISO strings computed by the caller.
"""

from __future__ import annotations

import sqlite3


def hold_action(
    conn: sqlite3.Connection, application_id: str, action: str, not_before: str, now: str
) -> None:
    """Hold the action until ``not_before``; a row already ``held`` for it is kept as is."""
    conn.execute(
        "INSERT OR IGNORE INTO held_actions"
        " (application_id, action, not_before, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (application_id, action, not_before, now, now),
    )


def pending_held_actions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Rows still ``held``, soonest first (then by Application and id)."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT * FROM held_actions WHERE status = 'held' ORDER BY not_before, application_id, id"
    ).fetchall()
    return rows


def held_action_history(conn: sqlite3.Connection, application_id: str) -> list[sqlite3.Row]:
    """Every row for the Application, oldest first, whatever its status."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT * FROM held_actions WHERE application_id = ? ORDER BY id", (application_id,)
    ).fetchall()
    return rows


def waiting_held_actions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Rows still ``held``, in the order ``run_due`` walks them (``not_before``, ``id``)."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT * FROM held_actions WHERE status = 'held' ORDER BY not_before, id"
    ).fetchall()
    return rows


def reschedule_held_action(
    conn: sqlite3.Connection, held_id: int, not_before: str, now: str
) -> None:
    """Move a still-``held`` row to ``not_before``."""
    conn.execute(
        "UPDATE held_actions SET not_before = ?, updated_at = ? WHERE id = ? AND status = 'held'",
        (not_before, now, held_id),
    )


def claim_held_action(conn: sqlite3.Connection, held_id: int, attempts: int, now: str) -> bool:
    """Count one more attempt if the row is still ``held`` at ``attempts``; True if claimed."""
    cur = conn.execute(
        "UPDATE held_actions SET attempts = attempts + 1, updated_at = ?"
        " WHERE id = ? AND status = 'held' AND attempts = ?",
        (now, held_id, attempts),
    )
    return cur.rowcount == 1


def finish_held_action(conn: sqlite3.Connection, held_id: int, status: str, now: str) -> bool:
    """Move a still-``held`` row to ``status``; True if this call moved it."""
    cur = conn.execute(
        "UPDATE held_actions SET status = ?, updated_at = ? WHERE id = ? AND status = 'held'",
        (status, now, held_id),
    )
    return cur.rowcount == 1


def record_held_action_failure(
    conn: sqlite3.Connection, held_id: int, status: str, last_error: str, now: str
) -> None:
    """Store a failed dispatch on a still-``held`` row: ``status`` is ``held`` or ``failed``."""
    conn.execute(
        "UPDATE held_actions SET status = ?, last_error = ?, updated_at = ?"
        " WHERE id = ? AND status = 'held'",
        (status, last_error, now, held_id),
    )
