"""Zones: the lookups the screening tools need."""

from __future__ import annotations

import sqlite3


def zone_timezone(conn: sqlite3.Connection, zone_id: str) -> str | None:
    """The Zone's IANA time zone (``zones.timezone`` is NOT NULL), or None for no such Zone."""
    row = conn.execute("SELECT timezone FROM zones WHERE id = ?", (zone_id,)).fetchone()
    if row is None:
        return None
    timezone: str = row["timezone"]
    return timezone


def zone_row(conn: sqlite3.Connection, zone_id: str) -> sqlite3.Row | None:
    """The Zone's ``id``, ``name``, ``city`` and ``country``, or None for no such Zone."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, name, city, country FROM zones WHERE id = ?", (zone_id,)
    ).fetchone()
    return row


_ZONES_WITH_COVERAGE = (
    "SELECT z.id, z.country, z.city, z.name, z.timezone, z.aliases,"
    " EXISTS (SELECT 1 FROM service_area_zones saz"
    "   JOIN service_areas sa ON sa.id = saz.service_area_id"
    "   JOIN locations l ON l.id = sa.location_id"
    "   WHERE saz.zone_id = z.id AND l.active = 1) AS covered"
    " FROM zones z ORDER BY z.id"
)


def zone_rows_with_coverage(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every Zone (``id``, ``country``, ``city``, ``name``, ``timezone``, ``aliases``) in id
    order, with ``covered`` set when an active Location's Service Area contains it. Reads
    reference tables only (``tools.zones_lookup.reference_tables_only`` relies on that)."""
    rows: list[sqlite3.Row] = conn.execute(_ZONES_WITH_COVERAGE).fetchall()
    return rows
