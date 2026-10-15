"""Numbered ``.sql`` migration runner.

Migrations live in ``criba/db/migrations/NNNN_name.sql`` and run in order. Each
applied migration is recorded in ``schema_migrations``, so running again only
applies the new ones. ``0001_schema.sql`` is a verbatim copy of
``contracts/schema.sql`` as frozen at wave 0 and is never edited; later changes to
the contract arrive as new files (``0002_events_system_turn.sql``), and the schema
after all migrations equals the current contract.

    python -m criba.db.migrator /data/criba.db
"""

from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass
from importlib import resources

from criba.db.connection import connect, iso, utc_now

_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  TEXT NOT NULL
) STRICT
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def available_migrations() -> list[Migration]:
    found = []
    for entry in resources.files("criba.db").joinpath("migrations").iterdir():
        match = _NAME.match(entry.name)
        if match:
            found.append(Migration(int(match[1]), match[2], entry.read_text(encoding="utf-8")))
    found.sort(key=lambda m: m.version)
    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise RuntimeError(f"duplicate migration versions: {versions}")
    return found


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(_LEDGER)
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply pending migrations; return the versions applied this time."""
    done = applied_versions(conn)
    applied = []
    for migration in available_migrations():
        if migration.version in done:
            continue
        # executescript commits any open transaction, then runs the script as
        # written, so BEGIN/COMMIT make the migration and its ledger row atomic.
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{migration.sql}\n;\n"
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES "
            f"({migration.version}, '{migration.name}', '{iso(utc_now())}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(migration.version)
    # executescript leaves the connection's pragmas alone, but be explicit.
    conn.execute("PRAGMA foreign_keys = ON")
    return applied


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m criba.db.migrator <database path>", file=sys.stderr)
        return 2
    conn = connect(args[0])
    try:
        applied = migrate(conn)
    finally:
        conn.close()
    print(f"applied {applied}" if applied else "up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
