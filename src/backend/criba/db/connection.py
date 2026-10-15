"""SQLite connections, transactions, timestamps and ids.

Every connection runs ``PRAGMA foreign_keys = ON`` and, for file databases, WAL
mode (spec 002). Connections are in autocommit mode; group writes with
:func:`transaction`, which nests through savepoints.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with the runtime settings the schema relies on."""
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block atomically. Nested calls become savepoints."""
    if conn.in_transaction:
        name = f"sp_{secrets.token_hex(4)}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield conn
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        conn.execute(f"RELEASE {name}")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    """Format as the schema's timestamp convention: ISO 8601 UTC, ``Z`` suffix."""
    if dt.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def new_id(prefix: str, now: datetime | None = None) -> str:
    """A sortable ULID-style id with a type prefix, e.g. ``app_01JA2B...``."""
    millis = int(now.timestamp() * 1000) if now else time.time_ns() // 1_000_000
    value = (millis << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return f"{prefix}_{''.join(reversed(chars))}"
