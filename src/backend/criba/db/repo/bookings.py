"""Interview slots, slot offers and bookings: the reads and writes behind ``criba.tools.booking``.

None of these opens a transaction or writes an event; ``book_slot`` and
``offer_interview_slots`` group them with their ``add_event`` calls in their own
``transaction``. :func:`insert_booking` lets ``sqlite3.IntegrityError`` from the unique index
``bookings_one_per_slot`` (the slot lock) propagate untouched: the caller turns it into
``slot_unavailable``. Time values come in as ISO strings computed by the caller.
"""

from __future__ import annotations

import sqlite3


def open_slot_rows(
    conn: sqlite3.Connection, application_id: str, starts_from: str
) -> list[sqlite3.Row]:
    """Bookable slots at the Application's covering Locations starting at or after
    ``starts_from``, soonest first."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT s.id AS slot_id, s.location_id, s.starts_at, s.ends_at,"
        " l.name AS location_name, l.address, l.timezone"
        " FROM interview_slots s"
        " JOIN application_locations al ON al.location_id = s.location_id"
        " JOIN locations l ON l.id = s.location_id"
        " WHERE al.application_id = ? AND s.cancelled_at IS NULL AND l.active = 1"
        "   AND s.starts_at >= ?"
        "   AND NOT EXISTS (SELECT 1 FROM bookings b"
        "                   WHERE b.slot_id = s.id AND b.status = 'booked')"
        " ORDER BY s.starts_at, l.name, s.id",
        (application_id, starts_from),
    ).fetchall()
    return rows


def insert_slot_offer(
    conn: sqlite3.Connection,
    ref: str,
    application_id: str,
    slot_id: str,
    offered_at: str,
    expires_at: str,
) -> None:
    """Store one offered ``slot_ref`` for the Application."""
    conn.execute(
        "INSERT INTO slot_offers (ref, application_id, slot_id, offered_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (ref, application_id, slot_id, offered_at, expires_at),
    )


def expire_slot_offers(conn: sqlite3.Connection, application_id: str, now: str) -> None:
    """End every offer of the Application still outstanding at ``now``."""
    conn.execute(
        "UPDATE slot_offers SET expires_at = ? WHERE application_id = ? AND expires_at > ?",
        (now, application_id, now),
    )


def offered_slot(
    conn: sqlite3.Connection, ref: str, application_id: str, now: str
) -> sqlite3.Row | None:
    """The offer ``ref`` made to this Application, unexpired at ``now`` (``slot_id``)."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT slot_id FROM slot_offers WHERE ref = ? AND application_id = ? AND expires_at > ?",
        (ref, application_id, now),
    ).fetchone()
    return row


def slot_row(conn: sqlite3.Connection, slot_id: str) -> sqlite3.Row | None:
    """The slot (``id``, ``location_id``, ``starts_at``, ``cancelled_at``), or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT s.id, s.location_id, s.starts_at, s.cancelled_at FROM interview_slots s"
        " WHERE s.id = ?",
        (slot_id,),
    ).fetchone()
    return row


def current_booking(conn: sqlite3.Connection, application_id: str) -> sqlite3.Row | None:
    """The Application's standing booking (``id``, ``slot_id``, ``ics_uid``), or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, slot_id, ics_uid FROM bookings WHERE application_id = ? AND status = 'booked'",
        (application_id,),
    ).fetchone()
    return row


def slot_is_booked(conn: sqlite3.Connection, slot_id: str) -> bool:
    """True when anyone holds a standing booking on the slot."""
    row = conn.execute(
        "SELECT 1 FROM bookings WHERE slot_id = ? AND status = 'booked'", (slot_id,)
    ).fetchone()
    return row is not None


def release_booking(conn: sqlite3.Connection, booking_id: str, released_at: str) -> None:
    """Mark the booking released at ``released_at``."""
    conn.execute(
        "UPDATE bookings SET status = 'released', released_at = ? WHERE id = ?",
        (released_at, booking_id),
    )


def insert_booking(
    conn: sqlite3.Connection,
    booking_id: str,
    application_id: str,
    slot_id: str,
    via: str,
    ics_uid: str,
    created_at: str,
) -> None:
    """Insert a standing booking; raises ``sqlite3.IntegrityError`` if the slot is taken."""
    conn.execute(
        "INSERT INTO bookings (id, application_id, slot_id, status, booked_via, ics_uid,"
        " created_at) VALUES (?, ?, ?, 'booked', ?, ?, ?)",
        (booking_id, application_id, slot_id, via, ics_uid, created_at),
    )


def booking_for_ics(conn: sqlite3.Connection, booking_id: str) -> sqlite3.Row | None:
    """The booking joined with its Application, slot and Location, for the ``.ics`` invite."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT b.ics_uid, b.status, b.created_at, b.released_at, a.language, a.country,"
        " s.starts_at, s.ends_at, l.name AS location_name, l.address"
        " FROM bookings b JOIN applications a ON a.id = b.application_id"
        " JOIN interview_slots s ON s.id = b.slot_id JOIN locations l ON l.id = s.location_id"
        " WHERE b.id = ?",
        (booking_id,),
    ).fetchone()
    return row


def latest_invite_booking_event(
    conn: sqlite3.Connection, application_id: str, message_id: int
) -> sqlite3.Row | None:
    """The newest booking event whose ``invite_after_message`` is before ``message_id``
    (``data``, raw JSON)."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT data FROM events WHERE application_id = ?"
        " AND kind IN ('interview_booked', 'interview_rescheduled')"
        " AND json_extract(data, '$.invite_after_message') < ? ORDER BY id DESC LIMIT 1",
        (application_id, message_id),
    ).fetchone()
    return row


def booking_is_standing(conn: sqlite3.Connection, booking_id: str | None) -> bool:
    """True when the booking exists and is still ``booked``."""
    row = conn.execute(
        "SELECT 1 FROM bookings WHERE id = ? AND status = 'booked'", (booking_id,)
    ).fetchone()
    return row is not None


def current_booking_id(conn: sqlite3.Connection, application_id: str) -> str | None:
    """The id of the Application's standing booking, or None."""
    row = conn.execute(
        "SELECT id FROM bookings WHERE application_id = ? AND status = 'booked'",
        (application_id,),
    ).fetchone()
    booking_id: str | None = row["id"] if row else None
    return booking_id


def interview_booking_row(conn: sqlite3.Connection, application_id: str) -> sqlite3.Row | None:
    """The Application's booked Interview (``starts_at``, ``ends_at``, ``location_name``,
    ``address``, ``timezone``), or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT s.starts_at, s.ends_at, l.name AS location_name, l.address, l.timezone"
        " FROM bookings b JOIN interview_slots s ON s.id = b.slot_id"
        " JOIN locations l ON l.id = s.location_id"
        " WHERE b.application_id = ? AND b.status = 'booked'",
        (application_id,),
    ).fetchone()
    return row


def live_offer_rows(conn: sqlite3.Connection, application_id: str, now: str) -> list[sqlite3.Row]:
    """The Application's unexpired offers on slots that are neither cancelled nor booked
    (``ref``, ``starts_at``, ``timezone``), soonest first."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT o.ref, s.starts_at, l.timezone FROM slot_offers o"
        " JOIN interview_slots s ON s.id = o.slot_id JOIN locations l ON l.id = s.location_id"
        " WHERE o.application_id = ? AND o.expires_at > ? AND s.cancelled_at IS NULL"
        "   AND NOT EXISTS (SELECT 1 FROM bookings b"
        "                   WHERE b.slot_id = o.slot_id AND b.status = 'booked')"
        " ORDER BY s.starts_at, o.ref",
        (application_id, now),
    ).fetchall()
    return rows


def slot_with_location(conn: sqlite3.Connection, slot_id: str) -> sqlite3.Row | None:
    """The slot with its Location (``id``, ``location_id``, ``starts_at``, ``ends_at``,
    ``location_name``, ``address``, ``timezone``), or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT s.id, s.location_id, s.starts_at, s.ends_at, l.name AS location_name,"
        " l.address, l.timezone FROM interview_slots s"
        " JOIN locations l ON l.id = s.location_id WHERE s.id = ?",
        (slot_id,),
    ).fetchone()
    return row


def application_holds_slot(conn: sqlite3.Connection, application_id: str, slot_id: str) -> bool:
    """Whether the Application has a standing booking on the slot."""
    row = conn.execute(
        "SELECT 1 FROM bookings WHERE application_id = ? AND slot_id = ? AND status = 'booked'",
        (application_id, slot_id),
    ).fetchone()
    return row is not None
