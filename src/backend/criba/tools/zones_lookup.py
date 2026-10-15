"""``search_zones`` (ADR 0042): a read-only Zone lookup for the Screening Agent.

The agent turns the Candidate's words into Zone ids with this tool, then passes the ids to
``record_answer(zone)``, where code verifies them and decides any Knockout. The lookup ranks
the seeded Zones (names, aliases, cities) with ``rank_zones`` and reports whether some active
Location's Service Area covers each one. It needs no open Application and never stores an
answer.

The lookup reads reference tables only. A SQLite authorizer enforces that while the query
runs: any read of another table (Candidates, Applications, messages, ...) or any write is
denied by SQLite itself, so a later change to the query cannot widen what the tool can see.

One write follows the lookup, outside the authorizer (ADR 0062): when the query matched a
city but no Zone in it, and the Zone is the question, ``offer_city_zones`` offers the city's
Zones as options (an ``answer_ambiguous`` event, as ``record_answer`` would), so the
Candidate sees them even if the agent asks without recording. A query that matched nothing at
all offers the Zone funnel instead (ADR 0068): the cities that have a Service Area, once.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from criba.db import repo
from criba.screening.zones import CITY_TIER, ZoneRef, rank_zones, zone_labels
from criba.tools import zone_searches

MAX_RESULTS = 10  # Q45's list size: WhatsApp list rows, and tools.json maxItems

REFERENCE_TABLES = frozenset({"zones", "service_area_zones", "service_areas", "locations"})


def _reference_only(action: int, arg1: str | None, *_: Any) -> int:
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and arg1 in REFERENCE_TABLES:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@contextmanager
def reference_tables_only(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block with every statement limited to reading ``REFERENCE_TABLES``.

    Setting an authorizer expires the connection's prepared statements, so cached
    statements are re-checked too; the authorizer is removed afterwards.
    """
    conn.set_authorizer(_reference_only)
    try:
        yield conn
    finally:
        conn.set_authorizer(None)


def load_zones(conn: sqlite3.Connection) -> list[ZoneRef]:
    """Every Zone, with ``covered`` set when an active Location's Service Area contains it."""
    rows = repo.zone_rows_with_coverage(conn)
    return [
        ZoneRef(
            id=r["id"],
            country=r["country"],
            city=r["city"],
            name=r["name"],
            timezone=r["timezone"],
            covered=bool(r["covered"]),
            aliases=tuple(json.loads(r["aliases"])),
        )
        for r in rows
    ]


def search_zones(
    conn: sqlite3.Connection, app: dict[str, Any], args: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Up to ten best-matching Zones for ``query`` (``search_zones_output``).

    With ``country``, only that country's Zones are ranked. Without it, every Zone is, and
    the Application's country comes first. Whether the query matched a Zone in any country is
    kept for the router's Zone backstop (:mod:`criba.tools.zone_searches`, ADR 0076).
    """
    with reference_tables_only(conn):
        zones = load_zones(conn)
    country = args.get("country")
    ranked = [z for z in zones if z.country == country] if country else zones
    tier, matched = rank_zones(args["query"], ranked)
    home = app.get("country")
    # Whether the place matches a Zone in any country, whatever the filter (ADR 0076): an empty
    # search of one country says nothing about the others.
    anywhere = bool(matched) or (bool(country) and bool(rank_zones(args["query"], zones)[1]))
    zone_searches.note(app["id"], repo.last_inbound_message_id(conn, app["id"]), matched=anywhere)
    ordered = sorted(matched, key=lambda z: (z.country != home, z.country, z.city, z.name, z.id))
    shown = ordered[:MAX_RESULTS]
    result: dict[str, Any] = {
        "zones": [_row(z, label, home) for z, label in zip(shown, zone_labels(shown), strict=True)],
        "more": len(ordered) > MAX_RESULTS,
    }
    # A city matched, but no Zone in it (ADR 0062): its Zones go to the Candidate as options.
    from criba.tools.application import offer_city_zones, offer_funnel  # imports this module

    if tier == CITY_TIER and offer_city_zones(conn, app, matched, now):
        result["options_attached"] = True
    elif not anywhere and country in (None, home):
        # Nothing matched anywhere (ADR 0068), searched without a filter or in the Application's
        # own country (ADR 0076): the backend asks once which of our cities they can work in, and
        # the reply must carry that question. If it was asked already, or cannot be, the agent
        # still gets the only cities it may name. A search of another country alone offers
        # nothing: the agent may be exploring, and the router's backstop decides after the run.
        question = offer_funnel(conn, app, now)
        if question is not None:
            result["options_attached"] = True
            result["message"] = question
        else:
            from criba.tools.funnel import service_cities

            result["service_cities"] = service_cities(conn, home)
    return result


def _row(zone: ZoneRef, label: str, home: str | None) -> dict[str, Any]:
    """One result row. The result stays in the model's history for the rest of the session and
    a city lists up to ten rows, so ``country`` is sent only when it is not the Application's
    own, and ``covered`` only when it is false (docs/evidence/turn-size.md)."""
    row: dict[str, Any] = {"zone_id": zone.id, "label": label, "city": zone.city}
    if zone.country != home:
        row["country"] = zone.country
    if not zone.covered:
        row["covered"] = False
    return row
