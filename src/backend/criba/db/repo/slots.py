"""Interview slot management: the reads and writes behind ``criba.api.slots``.

None of these opens a transaction; ``create_slots``, ``update_slot`` and ``cancel_slot`` run
them inside their own ``transaction`` and read the written slot back with :func:`slot_detail`
in the same one. The caller's Location scope arrives as a SQL fragment on ``interview_slots s``
with its parameters (``present.slot_in_scope_sql``), so ``db/`` does not import from ``api/``.
Time values come in as ISO strings computed by the caller.
"""

from __future__ import annotations

import sqlite3
from typing import Any

_SLOT_SELECT = (
    "SELECT s.*, l.name AS location_name, l.timezone AS timezone,"
    " b.application_id AS b_application_id, b.booked_via AS b_via, b.created_at AS b_at,"
    " c.full_name AS candidate_name"
    " FROM interview_slots s JOIN locations l ON l.id = s.location_id"
    " LEFT JOIN bookings b ON b.slot_id = s.id AND b.status = 'booked'"
    " LEFT JOIN applications a ON a.id = b.application_id"
    " LEFT JOIN candidates c ON c.id = a.candidate_id"
)


def slot_detail(conn: sqlite3.Connection, slot_id: str) -> sqlite3.Row | None:
    """The slot with its Location and standing booking (``b_*``, ``candidate_name``), or None."""
    row: sqlite3.Row | None = conn.execute(f"{_SLOT_SELECT} WHERE s.id = ?", (slot_id,)).fetchone()
    return row


def slot_start_taken(
    conn: sqlite3.Connection, location_id: str, starts_at: str, ignore: str = ""
) -> bool:
    """True when another live slot (id other than ``ignore``) starts there at ``starts_at``."""
    return (
        conn.execute(
            "SELECT 1 FROM interview_slots WHERE location_id = ? AND starts_at = ?"
            " AND cancelled_at IS NULL AND id != ?",
            (location_id, starts_at, ignore),
        ).fetchone()
        is not None
    )


def list_scoped_slots(
    conn: sqlite3.Connection,
    scope: str,
    scope_params: list[Any],
    *,
    location_id: str | None,
    starts_from: str | None,
    starts_to: str | None,
) -> list[sqlite3.Row]:
    """Slots in the caller's scope, optionally one Location and a start window, by start."""
    params = [*scope_params]
    where = [scope]
    if location_id:
        where.append("s.location_id = ?")
        params.append(location_id)
    if starts_from:
        where.append("s.starts_at >= ?")
        params.append(starts_from)
    if starts_to:
        where.append("s.starts_at <= ?")
        params.append(starts_to)
    rows: list[sqlite3.Row] = conn.execute(
        f"{_SLOT_SELECT} WHERE {' AND '.join(where)} ORDER BY s.starts_at, s.id", params
    ).fetchall()
    return rows


def active_location_exists(conn: sqlite3.Connection, location_id: str) -> bool:
    """True when the Location exists and is active."""
    location = conn.execute(
        "SELECT id FROM locations WHERE id = ? AND active = 1", (location_id,)
    ).fetchone()
    return location is not None


def insert_interview_slot(
    conn: sqlite3.Connection,
    slot_id: str,
    location_id: str,
    starts_at: str,
    ends_at: str,
    created_by: str,
    created_at: str,
) -> None:
    """Insert one interview slot."""
    conn.execute(
        "INSERT INTO interview_slots (id, location_id, starts_at, ends_at, created_by,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (slot_id, location_id, starts_at, ends_at, created_by, created_at),
    )


def move_interview_slot(
    conn: sqlite3.Connection, slot_id: str, starts_at: str, ends_at: str
) -> None:
    """Set the slot's start and end."""
    conn.execute(
        "UPDATE interview_slots SET starts_at = ?, ends_at = ? WHERE id = ?",
        (starts_at, ends_at, slot_id),
    )


def cancel_interview_slot(conn: sqlite3.Connection, slot_id: str, cancelled_at: str) -> None:
    """Stamp the slot cancelled at ``cancelled_at``."""
    conn.execute(
        "UPDATE interview_slots SET cancelled_at = ? WHERE id = ?", (cancelled_at, slot_id)
    )


def public_open_slots(
    conn: sqlite3.Connection, location_ids: list[str], starts_after: str, limit: int
) -> list[sqlite3.Row]:
    """Unbooked live slots at ``location_ids`` starting after ``starts_after``, soonest first,
    at most ``limit``, for the public booking page."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT s.id, s.starts_at, s.ends_at, l.name AS location_name, l.address, l.timezone"
        " FROM interview_slots s JOIN locations l ON l.id = s.location_id"
        f" WHERE s.location_id IN ({','.join('?' * len(location_ids))})"
        " AND s.cancelled_at IS NULL AND s.starts_at > ?"
        " AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.slot_id = s.id AND b.status = 'booked')"
        " ORDER BY s.starts_at, s.id LIMIT ?",
        [*location_ids, starts_after, limit],
    ).fetchall()
    return rows
