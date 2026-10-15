"""Users, their sessions and the Locations they see: the statements behind ``criba.api.auth``.

None of these opens a transaction or commits. Times come in as ISO strings (or a datetime for
the one expiry comparison); the password check, roles and response shapes stay with the caller.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from criba.db.connection import iso


def user_location_ids(conn: sqlite3.Connection, user_id: str) -> list[str]:
    """The ids of the Locations the user is assigned to, in id order."""
    rows = conn.execute(
        "SELECT location_id FROM user_locations WHERE user_id = ? ORDER BY location_id", (user_id,)
    ).fetchall()
    return [r[0] for r in rows]


def session_user_row(
    conn: sqlite3.Connection, token_hash: str, now: datetime
) -> sqlite3.Row | None:
    """The active user row (``users.*``) of an unrevoked session not expired at ``now``."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT u.* FROM auth_sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.id = ? AND s.revoked_at IS NULL AND s.expires_at > ? AND u.active = 1",
        (token_hash, iso(now)),
    ).fetchone()
    return row


def active_user_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    """The active user whose lower-cased email is ``email`` (already lower-cased), or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM users WHERE lower(email) = ? AND active = 1", (email,)
    ).fetchone()
    return row


def insert_auth_session(
    conn: sqlite3.Connection, session_id: str, user_id: str, created_at: str, expires_at: str
) -> None:
    """Store a session under the SHA-256 of its token (``session_id``)."""
    conn.execute(
        "INSERT INTO auth_sessions (id, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (session_id, user_id, created_at, expires_at),
    )


def revoke_auth_session(conn: sqlite3.Connection, session_id: str, revoked_at: str) -> None:
    """Revoke the session unless it already is; an unknown id changes nothing."""
    conn.execute(
        "UPDATE auth_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (revoked_at, session_id),
    )


def location_ref_rows(conn: sqlite3.Connection, ids: list[str] | None) -> list[sqlite3.Row]:
    """Locations (``id``, ``name``, ``country``, ``city``) ordered by country, city and name:
    those in ``ids`` (a non-empty list), or every active Location when ``ids`` is None."""
    sql = "SELECT id, name, country, city FROM locations"
    params: list[Any] = []
    if ids is not None:
        sql += f" WHERE id IN ({','.join('?' * len(ids))})"
        params = list(ids)
    else:
        sql += " WHERE active = 1"
    sql += " ORDER BY country, city, name"
    rows: list[sqlite3.Row] = conn.execute(sql, params).fetchall()
    return rows


def visible_location_rows(
    conn: sqlite3.Connection, ids: list[str] | None, country: str | None
) -> list[sqlite3.Row]:
    """Locations with their time zone and address, ordered by country, city and name: those in
    ``ids`` (a non-empty list), or every active Location when ``ids`` is None, narrowed to
    ``country`` when given."""
    sql = "SELECT id, name, country, city, timezone, address FROM locations"
    where: list[str] = []
    params: list[Any] = []
    if ids is None:
        where.append("active = 1")
    else:
        where.append(f"id IN ({','.join('?' * len(ids))})")
        params += ids
    if country:
        where.append("country = ?")
        params.append(country)
    sql += " WHERE " + " AND ".join(where) + " ORDER BY country, city, name"
    rows: list[sqlite3.Row] = conn.execute(sql, params).fetchall()
    return rows
