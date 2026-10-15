"""The scheduler's reads and writes: the Stalled ladder and the two flags.

Used by ``criba.scheduler.jobs``. None of these opens a transaction or writes an event; the
caller groups them with its ``add_event`` calls in its own ``transaction``.
"""

from __future__ import annotations

import sqlite3


def screening_application_ids(conn: sqlite3.Connection) -> list[str]:
    """Open Applications still in Screening (greeting to confirm), oldest first."""
    return [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM applications WHERE status = 'open' AND stage IN"
            " ('greeting','name','zone','driver_license','availability',"
            "'preferred_schedule','experience','start_date','confirm') ORDER BY created_at, id"
        ).fetchall()
    ]


def reset_stalled(conn: sqlite3.Connection, application_id: str) -> None:
    """The Candidate wrote again: clear ``stalled_since`` and start the ladder over."""
    conn.execute(
        "UPDATE applications SET stalled_since = NULL, nudges_sent = 0 WHERE id = ?",
        (application_id,),
    )


def set_stalled_since(conn: sqlite3.Connection, application_id: str, stalled_since: str) -> None:
    """Mark the Application Stalled from ``stalled_since`` (ISO)."""
    conn.execute(
        "UPDATE applications SET stalled_since = ? WHERE id = ?",
        (stalled_since, application_id),
    )


def set_nudges_sent(conn: sqlite3.Connection, application_id: str, step: int) -> None:
    """Move the Application to ladder position ``step``."""
    conn.execute(
        "UPDATE applications SET nudges_sent = ? WHERE id = ?",
        (step, application_id),
    )


def qualified_waiting_due(conn: sqlite3.Connection, cutoff: str) -> list[sqlite3.Row]:
    """Unclaimed Qualified Applications, not yet flagged, Qualified at or before ``cutoff``
    (``id``, ``candidate_id``, ``qualified_at``)."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT id, candidate_id, qualified_at FROM applications"
        " WHERE status = 'open' AND stage = 'qualified' AND claim_user_id IS NULL"
        " AND qualified_waiting = 0 AND qualified_at IS NOT NULL AND qualified_at <= ?",
        (cutoff,),
    ).fetchall()
    return rows


def set_qualified_waiting(conn: sqlite3.Connection, application_id: str) -> None:
    conn.execute("UPDATE applications SET qualified_waiting = 1 WHERE id = ?", (application_id,))


def claimed_idle_due(conn: sqlite3.Connection, cutoff: str) -> list[sqlite3.Row]:
    """Claimed open Applications, not yet flagged, whose last Claim action is at or before
    ``cutoff`` (``id``, ``candidate_id``, ``claim_user_id``, ``last_action``)."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT id, candidate_id, claim_user_id,"
        " COALESCE(claim_last_action_at, claimed_at) AS last_action FROM applications"
        " WHERE status = 'open' AND claim_user_id IS NOT NULL AND claimed_idle = 0"
        " AND COALESCE(claim_last_action_at, claimed_at) <= ?",
        (cutoff,),
    ).fetchall()
    return rows


def set_claimed_idle(conn: sqlite3.Connection, application_id: str) -> None:
    conn.execute("UPDATE applications SET claimed_idle = 1 WHERE id = ?", (application_id,))


def clear_stale_flags(conn: sqlite3.Connection) -> int:
    """Clear flags that no longer apply; returns how many were cleared.

    Waiting ends when claimed, Approved or closed; idle ends when the Claim is released or the
    Application closes."""
    cleared: int = conn.execute(
        "UPDATE applications SET qualified_waiting = 0 WHERE qualified_waiting = 1"
        " AND (status != 'open' OR stage != 'qualified' OR claim_user_id IS NOT NULL)"
    ).rowcount
    cleared += conn.execute(
        "UPDATE applications SET claimed_idle = 0 WHERE claimed_idle = 1"
        " AND (status != 'open' OR claim_user_id IS NULL)"
    ).rowcount
    return cleared
