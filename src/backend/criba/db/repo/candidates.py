"""Candidates: identity lookup and creation, language, and opt-outs."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from criba.db.connection import iso, new_id, transaction
from criba.db.repo.applications import get_open_application
from criba.db.repo.base import _now
from criba.db.repo.events import add_event


def country_from_phone(phone: str | None) -> str | None:
    """Country from the E.164 prefix: +34 Spain, +52 Mexico (Phase 1 §3)."""
    if not phone:
        return None
    digits = phone.replace(" ", "")
    if digits.startswith("+34"):
        return "ES"
    if digits.startswith("+52"):
        return "MX"
    return None


def get_candidate(conn: sqlite3.Connection, candidate_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    return _candidate(row) if row else None


def _candidate(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["opted_out"] = bool(out["opted_out"])
    return out


def find_candidate_by_identity(
    conn: sqlite3.Connection, channel: str, external_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT c.* FROM candidates c JOIN candidate_identities i ON i.candidate_id = c.id"
        " WHERE i.channel = ? AND i.external_id = ?",
        (channel, external_id),
    ).fetchone()
    return _candidate(row) if row else None


def identity_candidate_row(
    conn: sqlite3.Connection, channel: str, external_id: str
) -> sqlite3.Row | None:
    """The ``candidate_id`` of the identity ``(channel, external_id)``, or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT candidate_id FROM candidate_identities WHERE channel = ? AND external_id = ?",
        (channel, external_id),
    ).fetchone()
    return row


def identity_exists(conn: sqlite3.Connection, channel: str, external_id: str) -> bool:
    """Whether the identity ``(channel, external_id)`` exists."""
    found = conn.execute(
        "SELECT 1 FROM candidate_identities WHERE channel = ? AND external_id = ?",
        (channel, external_id),
    ).fetchone()
    return found is not None


def conversation_identity(
    conn: sqlite3.Connection, channels: tuple[str, str], external_id: str, preferred: str
) -> sqlite3.Row | None:
    """The identity of ``external_id`` on either of ``channels`` (``candidate_id``,
    ``created_at``, ``channel``), the one on ``preferred`` first, or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT candidate_id, created_at, channel FROM candidate_identities"
        " WHERE channel IN (?, ?) AND external_id = ? ORDER BY channel = ? DESC LIMIT 1",
        (*channels, external_id, preferred),
    ).fetchone()
    return row


def add_candidate_identity(
    conn: sqlite3.Connection,
    candidate_id: str,
    channel: str,
    external_id: str,
    created_at: str,
    client: str | None,
    persona_id: str | None,
) -> None:
    """Add a channel identity to an existing Candidate (``client`` and ``persona_id`` codes)."""
    conn.execute(
        "INSERT INTO candidate_identities"
        " (candidate_id, channel, external_id, created_at, client, persona_id)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (candidate_id, channel, external_id, created_at, client, persona_id),
    )


def first_candidate_by_phone(
    conn: sqlite3.Connection, phone: str, client_id: str
) -> sqlite3.Row | None:
    """The ``id`` of the Client's oldest Candidate with this phone, or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT id FROM candidates WHERE phone = ? AND client_id = ?"
        " ORDER BY created_at, id LIMIT 1",
        (phone, client_id),
    ).fetchone()
    return row


def candidate_country(conn: sqlite3.Connection, candidate_id: str) -> str | None:
    """The Candidate's country, or None (no country, or no such Candidate)."""
    row = conn.execute("SELECT country FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    country: str | None = row["country"] if row else None
    return country


def candidate_language(conn: sqlite3.Connection, candidate_id: str) -> str | None:
    """The Candidate's language, or None (none set, or no such Candidate)."""
    row = conn.execute("SELECT language FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    language: str | None = row["language"] if row else None
    return language


def get_or_create_candidate(
    conn: sqlite3.Connection,
    *,
    client_id: str,
    channel: str,
    external_id: str,
    phone: str | None = None,
    country: str | None = None,
    language: str | None = None,
    now: datetime | None = None,
    client: str | None = None,
    persona_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Look up the Candidate by channel identity, or create one. Returns ``(candidate, created)``.

    The country comes from ``country`` if given, else from the phone prefix. ``client`` and
    ``persona_id`` (codes only, ADR 0060) are written on a new identity row, never on an
    existing one.
    """
    ts = iso(_now(now))
    with transaction(conn):
        found = find_candidate_by_identity(conn, channel, external_id)
        if found:
            return found, False
        candidate_id = new_id("cand", now)
        conn.execute(
            "INSERT INTO candidates"
            " (id, client_id, phone, country, language, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                candidate_id,
                client_id,
                phone,
                country or country_from_phone(phone),
                language,
                ts,
                ts,
            ),
        )
        conn.execute(
            "INSERT INTO candidate_identities"
            " (candidate_id, channel, external_id, created_at, client, persona_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (candidate_id, channel, external_id, ts, client, persona_id),
        )
    candidate = get_candidate(conn, candidate_id)
    assert candidate is not None
    return candidate, True


def set_candidate_language(
    conn: sqlite3.Connection, candidate_id: str, language: str, *, now: datetime | None = None
) -> None:
    """Record the language of the Candidate's latest exchange (Phase 1 §4.3)."""
    ts = iso(_now(now))
    with transaction(conn):
        conn.execute(
            "UPDATE candidates SET language = ?, updated_at = ? WHERE id = ?",
            (language, ts, candidate_id),
        )
        conn.execute(
            "UPDATE applications SET language = ?, updated_at = ?"
            " WHERE candidate_id = ? AND status = 'open'",
            (language, ts, candidate_id),
        )


def mark_opted_out(
    conn: sqlite3.Connection, candidate_id: str, *, now: datetime | None = None
) -> None:
    """Opted out: blocks every follow-up and outreach, for every Application."""
    ts = iso(_now(now))
    with transaction(conn):
        cur = conn.execute(
            "UPDATE candidates SET opted_out = 1, opted_out_at = ?, updated_at = ?"
            " WHERE id = ? AND opted_out = 0",
            (ts, ts, candidate_id),
        )
        if cur.rowcount:
            open_app = get_open_application(conn, candidate_id)
            add_event(
                conn,
                candidate_id=candidate_id,
                application_id=open_app["id"] if open_app else None,
                kind="opted_out",
                actor="candidate",
                now=now,
            )


def lift_opt_out(
    conn: sqlite3.Connection, candidate_id: str, *, now: datetime | None = None
) -> bool:
    """Lift an opt-out because the Candidate wrote again. Returns True if one was lifted."""
    ts = iso(_now(now))
    with transaction(conn):
        cur = conn.execute(
            "UPDATE candidates SET opted_out = 0, opted_out_at = NULL, updated_at = ?"
            " WHERE id = ? AND opted_out = 1",
            (ts, candidate_id),
        )
        if not cur.rowcount:
            return False
        add_event(
            conn,
            candidate_id=candidate_id,
            application_id=None,
            kind="opt_out_lifted",
            actor="candidate",
            now=now,
        )
        return True


def stamp_erasure_request(conn: sqlite3.Connection, candidate_id: str, *, now: datetime) -> None:
    """Record when the Candidate first asked for erasure (a later ask keeps the first time).

    No transaction and no event here: the caller writes ``erasure_requested`` in its own."""
    ts = iso(now)
    conn.execute(
        "UPDATE candidates SET erasure_requested_at = coalesce(erasure_requested_at, ?),"
        " updated_at = ? WHERE id = ?",
        (ts, ts, candidate_id),
    )


def conversation_rows(
    conn: sqlite3.Connection, select_sql: str, limit: int, offset: int
) -> list[sqlite3.Row]:
    """Conversations, newest first. ``select_sql`` is the caller's SELECT over
    ``candidate_identities ci`` (its WHERE clause included); the ordering and paging are added
    here."""
    rows: list[sqlite3.Row] = conn.execute(
        f"{select_sql} ORDER BY ci.created_at DESC, ci.external_id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return rows


def conversation_row(
    conn: sqlite3.Connection, select_sql: str, conversation_id: str
) -> sqlite3.Row | None:
    """The conversation ``conversation_id`` from the caller's ``select_sql`` (see
    :func:`conversation_rows`), or None."""
    row: sqlite3.Row | None = conn.execute(
        f"{select_sql} AND ci.external_id = ?", (conversation_id,)
    ).fetchone()
    return row
