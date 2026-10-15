"""Clients: the lookups the notices need."""

from __future__ import annotations

import sqlite3


def client_name(conn: sqlite3.Connection, client_id: str) -> str | None:
    """The Client's name, or None for no such Client."""
    row = conn.execute("SELECT name FROM clients WHERE id = ?", (client_id,)).fetchone()
    name: str | None = row["name"] if row else None
    return name
