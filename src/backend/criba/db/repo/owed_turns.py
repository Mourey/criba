"""Owed turns (ADR 0073): the reads and writes behind the turn recovery in
``criba.channel.router`` and ``criba.channel.service``.

One row per inbound Candidate message we still owe a reply to. None of these opens a
transaction; times come in as ISO strings computed by the caller. A leaf: imports no sibling.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def add_owed_turn(
    conn: sqlite3.Connection,
    *,
    message_pk: int,
    application_id: str,
    channel: str,
    turn_input: str,
    idempotency_key: str,
    after_event_id: int | None,
    booking: tuple[str, str] | None,
    typed: bool,
    next_attempt_at: str,
    now: str,
) -> None:
    """Record that ``message_pk`` is owed a reply. A message already owed is kept as is. The new
    row inherits the Application's holding flag: one holding message per outage."""
    conn.execute(
        "INSERT OR IGNORE INTO owed_turns (message_id, application_id, channel, turn_input,"
        " idempotency_key, after_event_id, booking_url, booking_message, typed, holding_sent,"
        " next_attempt_at, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,"
        " EXISTS (SELECT 1 FROM owed_turns WHERE application_id = ? AND holding_sent = 1),"
        " ?, ?, ?)",
        (
            message_pk,
            application_id,
            channel,
            turn_input,
            idempotency_key,
            after_event_id,
            booking[0] if booking else None,
            booking[1] if booking else None,
            int(typed),
            application_id,
            next_attempt_at,
            now,
            now,
        ),
    )


def owed_turn(conn: sqlite3.Connection, message_pk: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM owed_turns WHERE message_id = ?", (message_pk,)).fetchone()
    return dict(row) if row else None


def owed_turns_of(
    conn: sqlite3.Connection, application_id: str, *, before: int | None = None
) -> list[dict[str, Any]]:
    """The Application's owed turns, oldest message first; only those before ``before`` when
    given."""
    rows = conn.execute(
        "SELECT * FROM owed_turns WHERE application_id = ? AND (? IS NULL OR message_id < ?)"
        " ORDER BY message_id",
        (application_id, before, before),
    ).fetchall()
    return [dict(r) for r in rows]


def set_owed_run_input(conn: sqlite3.Connection, message_pk: int, run_input: str, now: str) -> None:
    """Keep the input exactly as first POSTed; a later attempt sends the same bytes."""
    conn.execute(
        "UPDATE owed_turns SET run_input = ?, updated_at = ? WHERE message_id = ?",
        (run_input, now, message_pk),
    )


def resolve_owed_turn(conn: sqlite3.Connection, message_pk: int) -> None:
    """The turn is answered (or needs no answer any more): forget it."""
    conn.execute("DELETE FROM owed_turns WHERE message_id = ?", (message_pk,))


def drop_owed_turns(conn: sqlite3.Connection, application_id: str) -> int:
    """Forget every owed turn of the Application (a person has the conversation)."""
    return conn.execute(
        "DELETE FROM owed_turns WHERE application_id = ?", (application_id,)
    ).rowcount


def record_owed_failure(
    conn: sqlite3.Connection,
    message_pk: int,
    *,
    attempts: int,
    status: str,
    idempotency_key: str,
    next_attempt_at: str,
    last_error: str,
    now: str,
) -> None:
    conn.execute(
        "UPDATE owed_turns SET attempts = ?, status = ?, idempotency_key = ?,"
        " next_attempt_at = ?, last_error = ?, updated_at = ? WHERE message_id = ?",
        (attempts, status, idempotency_key, next_attempt_at, last_error, now, message_pk),
    )


def exhaust_owed_turns(conn: sqlite3.Connection, application_id: str, now: str) -> None:
    """No more automatic attempts for the Application: its owed turns wait for the Candidate's
    next message or a person."""
    conn.execute(
        "UPDATE owed_turns SET status = 'exhausted', updated_at = ? WHERE application_id = ?",
        (now, application_id),
    )


def holding_sent(conn: sqlite3.Connection, application_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM owed_turns WHERE application_id = ? AND holding_sent = 1 LIMIT 1",
        (application_id,),
    ).fetchone()
    return row is not None


def mark_holding_sent(conn: sqlite3.Connection, application_id: str, now: str) -> None:
    conn.execute(
        "UPDATE owed_turns SET holding_sent = 1, updated_at = ? WHERE application_id = ?",
        (now, application_id),
    )


def reply_owed(conn: sqlite3.Connection, application_id: str) -> bool:
    """True while we owe the Candidate a reply: an owed turn still being retried, or one whose
    retries ran out while an Escalation of the Application is open (the ``technical_error``
    one the last failure opened, unless another was open already). The Stalled clock does not
    run meanwhile (ADR 0073)."""
    row = conn.execute(
        "SELECT 1 FROM owed_turns o WHERE o.application_id = ? AND (o.status = 'owed'"
        " OR EXISTS (SELECT 1 FROM queue_items q WHERE q.application_id = o.application_id"
        " AND q.kind = 'escalation' AND q.status = 'open')) LIMIT 1",
        (application_id,),
    ).fetchone()
    return row is not None


def due_owed_applications(
    conn: sqlite3.Connection, now: str, *, ignore_lease: bool = False
) -> list[str]:
    """Applications whose oldest owed turn is ``owed`` and due (``next_attempt_at`` reached,
    or any time with ``ignore_lease``: at startup nothing of this process is running it)."""
    rows = conn.execute(
        "SELECT o.application_id FROM owed_turns o JOIN (SELECT application_id,"
        " MIN(message_id) AS head FROM owed_turns GROUP BY application_id) h"
        " ON h.application_id = o.application_id AND h.head = o.message_id"
        " WHERE o.status = 'owed' AND (? OR o.next_attempt_at <= ?)"
        " ORDER BY o.next_attempt_at, o.message_id",
        (int(ignore_lease), now),
    ).fetchall()
    return [r[0] for r in rows]


def owed_message_body(conn: sqlite3.Connection, message_pk: int) -> str | None:
    """The stored text of an owed message (its Jev read and Zone note need it on a resume)."""
    row = conn.execute("SELECT body FROM messages WHERE id = ?", (message_pk,)).fetchone()
    return row["body"] if row else None
