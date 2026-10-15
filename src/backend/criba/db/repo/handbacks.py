"""Handbacks: the Takeover recap the agent reads on its next turn."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from criba.db.connection import iso
from criba.db.repo.base import _now


def add_handback(
    conn: sqlite3.Connection,
    application_id: str,
    *,
    user_id: str | None,
    summary: str,
    generated: bool,
    now: datetime | None = None,
) -> int:
    """Store a hand-back summary; the ``takeover_handed_back`` event references its id."""
    cur = conn.execute(
        "INSERT INTO handbacks (application_id, user_id, summary, generated, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (application_id, user_id, summary, 1 if generated else 0, iso(_now(now))),
    )
    return int(cur.lastrowid or 0)


def get_handback(conn: sqlite3.Connection, handback_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM handbacks WHERE id = ?", (handback_id,)).fetchone()
    return dict(row) if row else None
