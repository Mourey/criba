"""The Zone funnel (ADR 0068): where Grupo Sazón hires, asked once when a place matches no Zone.

A Zone Knockout needs a recorded Zone that no Service Area covers (ADR 0042). A Candidate in
Texas names a place that is no Zone at all, so nothing was ever recorded and the chat looped. The
backend now asks once, in a fixed question (``criba.channel.copy``), which of the cities that
have a Service Area in the Application's country the Candidate can work in, as the pending
choice (ADR 0037): one option per city, plus "none of these".

- A city tap leads to that city's Zones, the same offer as a city-only search (ADR 0062).
- "None of these", or a second answer that still matches no Zone, is the Zone Knockout.

The cities come from the seeded reference tables (a Zone covered by an active Location's Service
Area), never from a typed list, and the funnel is a ``answer_ambiguous`` event with
``funnel: true`` (ids and codes only, ADR 0035). This module holds the pure lookups; the offer
and the Knockout are in ``criba.tools.application``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from criba.channel.copy import no_service_city_label, service_cities_question
from criba.db import repo
from criba.screening import ZoneRef
from criba.screening.results import truncate_label
from criba.screening.text import normalize
from criba.tools.common import language_of
from criba.tools.zones_lookup import load_zones, reference_tables_only

CITY_OPTION_PREFIX = "city_"
NO_CITY_OPTION_ID = "city_none"
MAX_CITIES = 9  # a list holds 10 rows, one of them "none of these"


def city_option_id(country: str, city: str) -> str:
    """The funnel option id of a city, e.g. ``city_mx_ciudad_de_mexico``."""
    return f"{CITY_OPTION_PREFIX}{country.lower()}_{'_'.join(normalize(city).split())}"


def served_zones(zones: Sequence[ZoneRef]) -> dict[tuple[str, str], list[ZoneRef]]:
    """``{(country, city): its Zones}`` for the cities with at least one covered Zone: a city
    that has a Service Area."""
    by_city: dict[tuple[str, str], list[ZoneRef]] = {}
    for zone in zones:
        by_city.setdefault((zone.country, zone.city), []).append(zone)
    return {key: found for key, found in by_city.items() if any(z.covered for z in found)}


def service_cities(conn: sqlite3.Connection, country: str | None) -> list[str]:
    """The names of the cities that have a Service Area in ``country``, in alphabetical order:
    the only list of areas the Screening Agent may name. Empty without a country."""
    if not country:
        return []
    with reference_tables_only(conn):
        zones = load_zones(conn)
    cities = {city for (c, city) in served_zones(zones) if c == country}
    return sorted(cities, key=normalize)


def options(conn: sqlite3.Connection, app: dict[str, Any]) -> list[dict[str, str]] | None:
    """The funnel's options for the Application's country (one per city, then "none of these"),
    or None when there are no cities or too many for one list."""
    country = app.get("country")
    cities = service_cities(conn, country)
    if not country or not cities or len(cities) > MAX_CITIES:
        return None
    found = [{"id": city_option_id(country, c), "label": truncate_label(c)} for c in cities]
    found.append(
        {
            "id": NO_CITY_OPTION_ID,
            "label": no_service_city_label(language_of(app), country),
        }
    )
    return found


def question(app: dict[str, Any], funnel_options: Sequence[dict[str, str]]) -> str:
    """The fixed question for a funnel's options, in the Application's language."""
    cities = [o["label"] for o in funnel_options if o["id"] != NO_CITY_OPTION_ID]
    return service_cities_question(cities, language_of(app), app.get("country"))


def city_zone_ids(conn: sqlite3.Connection, option_id: str) -> list[str] | None:
    """The Zone ids of the city a funnel option stands for, by name then id; None when the id is
    no served city's."""
    with reference_tables_only(conn):
        zones = load_zones(conn)
    for (country, city), found in served_zones(zones).items():
        if city_option_id(country, city) == option_id:
            ordered = sorted(found, key=lambda z: (normalize(z.name), z.id))
            return [z.id for z in ordered]
    return None


def is_funnel(data: dict[str, Any] | None) -> bool:
    """Whether an ``answer_ambiguous`` event's data is the funnel's."""
    return data is not None and data.get("funnel") is True


def last_offer(conn: sqlite3.Connection, application_id: str) -> dict[str, Any] | None:
    """The data of the Application's newest funnel event, or None if it was never offered."""
    return repo.last_funnel_offer(conn, application_id)


__all__ = [
    "CITY_OPTION_PREFIX",
    "MAX_CITIES",
    "NO_CITY_OPTION_ID",
    "city_option_id",
    "city_zone_ids",
    "is_funnel",
    "last_offer",
    "options",
    "question",
    "served_zones",
    "service_cities",
]
