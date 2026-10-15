"""Users, scope and Claims."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from criba.db.connection import iso, parse_iso, transaction
from criba.db.repo.applications import _require_open, _update, require_application
from criba.db.repo.base import CLAIM_IDLE_AFTER, Conflict, Forbidden, _now
from criba.db.repo.events import add_event


def get_user(conn: sqlite3.Connection, user_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def session_is_alive(conn: sqlite3.Connection, token_hash: str, now: datetime) -> bool:
    """Whether the auth session is unrevoked, unexpired at ``now`` and its user active."""
    alive = conn.execute(
        "SELECT 1 FROM auth_sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?"
        " AND s.revoked_at IS NULL AND s.expires_at > ? AND u.active = 1",
        (token_hash, iso(now)),
    ).fetchone()
    return alive is not None


def user_can_access(conn: sqlite3.Connection, user_id: str, application_id: str) -> bool:
    """Operators see everything; a Recruiter sees Applications at their Locations."""
    user = get_user(conn, user_id)
    if user is None or not user["active"]:
        return False
    if user["role"] == "operator":
        return True
    row = conn.execute(
        "SELECT 1 FROM application_locations al"
        " JOIN user_locations ul ON ul.location_id = al.location_id"
        " JOIN applications a ON a.id = al.application_id"
        " WHERE al.application_id = ? AND ul.user_id = ? AND a.client_id = ?",
        (application_id, user_id, user["client_id"]),
    ).fetchone()
    return row is not None


def claim_application(
    conn: sqlite3.Connection, application_id: str, user_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Take the Claim on an open Application.

    - Unclaimed: the caller takes it (``claim_taken``).
    - Already the caller's: no change.
    - Another Recruiter's, idle for 7 days: the caller takes it over (``claim_taken_over``).
    - Another Recruiter's, active: ``Conflict("claimed")``.

    Safe under concurrent callers: the write is a conditional UPDATE inside an
    IMMEDIATE transaction, so exactly one of two racing callers wins.
    """
    current = _now(now)
    ts = iso(current)
    with transaction(conn):
        app = _require_open(conn, application_id)
        if not user_can_access(conn, user_id, application_id):
            raise Forbidden("the user cannot see this Application", "out_of_scope")
        holder = app["claim_user_id"]
        if holder == user_id:
            return app
        kind = "claim_taken"
        data: dict[str, Any] = {}
        if holder is None:
            cur = conn.execute(
                "UPDATE applications SET claim_user_id = ?, claimed_at = ?,"
                " claim_last_action_at = ?, claimed_idle = 0, qualified_waiting = 0,"
                " updated_at = ? WHERE id = ? AND claim_user_id IS NULL",
                (user_id, ts, ts, ts, application_id),
            )
        else:
            last = app["claim_last_action_at"] or app["claimed_at"]
            if current - parse_iso(last) < CLAIM_IDLE_AFTER:
                raise Conflict("another Recruiter holds the Claim", "claimed")
            kind, data = "claim_taken_over", {"previous_user_id": holder}
            cur = conn.execute(
                "UPDATE applications SET claim_user_id = ?, claimed_at = ?,"
                " claim_last_action_at = ?, claimed_idle = 0, updated_at = ?"
                " WHERE id = ? AND claim_user_id = ?",
                (user_id, ts, ts, ts, application_id, holder),
            )
        if cur.rowcount != 1:
            raise Conflict("another Recruiter holds the Claim", "claimed")
        add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            kind=kind,
            actor="user",
            user_id=user_id,
            data=data,
            now=now,
        )
    return require_application(conn, application_id)


def release_claim(
    conn: sqlite3.Connection, application_id: str, user_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Release the Claim. Only its holder, or an Operator, may release it."""
    ts = iso(_now(now))
    with transaction(conn):
        app = require_application(conn, application_id)
        holder = app["claim_user_id"]
        if holder is None:
            return app
        user = get_user(conn, user_id)
        if holder != user_id and (user is None or user["role"] != "operator"):
            raise Forbidden("only the Claim's holder can release it", "not_claim_holder")
        _update(
            conn,
            application_id,
            {
                "claim_user_id": None,
                "claimed_at": None,
                "claim_last_action_at": None,
                "claimed_idle": 0,
                "updated_at": ts,
            },
        )
        add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            kind="claim_released",
            actor="user",
            user_id=user_id,
            data={"user_id": holder},
            now=now,
        )
    return require_application(conn, application_id)


def touch_claim(
    conn: sqlite3.Connection, application_id: str, user_id: str, *, now: datetime | None = None
) -> bool:
    """Record a Recruiter action on their Claim (resets Claimed, idle). True if it was theirs."""
    ts = iso(_now(now))
    cur = conn.execute(
        "UPDATE applications SET claim_last_action_at = ?, claimed_idle = 0, updated_at = ?"
        " WHERE id = ? AND claim_user_id = ?",
        (ts, ts, application_id, user_id),
    )
    return cur.rowcount == 1


def user_name(conn: sqlite3.Connection, user_id: str) -> str | None:
    """The user's display name, or None for no such user."""
    row = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["name"] if row else None
