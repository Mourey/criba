"""SYNTHETIC seed data: Grupo Sazón's 45 Locations, Zones, Service Areas and slots.

**Everything here is synthetic**, a stand-in until Grupo Sazón supplies its real
Locations, Service Areas and Interview availability (spec 002). Names are real
neighbourhoods so the demo reads naturally; addresses are marked "(synthetic)".

Shape:
- 25 Locations in Spain (Madrid, Barcelona, Valencia, Sevilla, Málaga) and 20 in
  Mexico (Ciudad de México, Guadalajara, Monterrey, Puebla).
- Each Location is named after its home Zone. Its Service Area covers its home
  Zone and the neighbouring Zones in the same city's list, so most Zones are
  covered by two or three Locations (ADR 0021).
- "Centro" exists in several cities, for Zone ambiguity.
- A few Zones are outside every Service Area, for Knockouts: one beside some cities
  (``City.unserved``) and a set of nearby towns and other cities Candidates often name
  (``UNCOVERED_TOWNS``). An unknown place is re-asked, not a Knockout (spec section 3),
  so a Knockout by Zone is only reachable for places listed here.
- Interview slots: Monday to Saturday, 10:00, 12:00 and 16:00 local time, 30
  minutes each, for ``days`` days from ``slots_from``.

Seeding is idempotent: ids are deterministic and rows are inserted only if missing.

    python -m criba.db.seed /data/criba.db
    CRIBA_SEED_PASSWORD=... python -m criba.db.seed /data/criba.db   # also seeds users
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from criba.db.connection import connect, iso, transaction, utc_now
from criba.db.migrator import migrate

SYNTHETIC = True
CLIENT_ID = "grupo_sazon"
CLIENT_NAME = "Grupo Sazón"
SLOT_TIMES = (time(10, 0), time(12, 0), time(16, 0))
SLOT_MINUTES = 30


@dataclass(frozen=True)
class City:
    country: str
    slug: str
    name: str
    timezone: str
    # Zones with a Location, in neighbour order.
    served: tuple[str, ...]
    # Zones outside every Service Area (Knockout by Zone).
    unserved: tuple[str, ...] = ()
    # Extra aliases for the unserved Zones (the accent-free and "Zone, City" forms are automatic).
    extra_aliases: tuple[str, ...] = ()


CITIES: tuple[City, ...] = (
    City(
        "ES",
        "madrid",
        "Madrid",
        "Europe/Madrid",
        (
            "Chamberí",
            "Salamanca",
            "Retiro",
            "Centro",
            "Arganzuela",
            "Carabanchel",
            "Tetuán",
            "Chamartín",
        ),
        unserved=("Alcalá de Henares",),
    ),
    City(
        "ES",
        "barcelona",
        "Barcelona",
        "Europe/Madrid",
        ("Les Corts", "Sants", "Eixample", "Gràcia", "Ciutat Vella", "Sant Martí"),
    ),
    City(
        "ES",
        "valencia",
        "Valencia",
        "Europe/Madrid",
        ("Campanar", "Centro", "Ruzafa", "Benimaclet"),
    ),
    City(
        "ES",
        "sevilla",
        "Sevilla",
        "Europe/Madrid",
        ("Triana", "Centro", "Macarena", "Nervión"),
        unserved=("Dos Hermanas",),
    ),
    City("ES", "malaga", "Málaga", "Europe/Madrid", ("Teatinos", "Centro", "El Palo")),
    City(
        "MX",
        "cdmx",
        "Ciudad de México",
        "America/Mexico_City",
        ("Polanco", "Condesa", "Roma", "Centro", "Narvarte", "Del Valle", "Coyoacán", "Iztapalapa"),
        unserved=("Milpa Alta",),
    ),
    City(
        "MX",
        "guadalajara",
        "Guadalajara",
        "America/Mexico_City",
        ("Zapopan", "Providencia", "Chapalita", "Centro", "Tlaquepaque"),
    ),
    City(
        "MX",
        "monterrey",
        "Monterrey",
        "America/Monterrey",
        ("San Pedro", "Cumbres", "Centro", "Guadalupe"),
        unserved=("Apodaca",),
    ),
    City(
        "MX",
        "puebla",
        "Puebla",
        "America/Mexico_City",
        ("Angelópolis", "Centro", "La Paz"),
    ),
)


def slugify(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "_".join("".join(c if c.isalnum() else " " for c in folded.lower()).split())


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _town(country: str, name: str, timezone: str, *aliases: str) -> City:
    """A town or city with one Zone of its own, outside every Service Area.

    The Zone's city is the town itself, so it never joins the city-only matches of
    the cities that have Locations ("Madrid", "Puebla") and the Candidate's words
    for those places resolve exactly as before.
    """
    return City(country, slugify(name), name, timezone, (), unserved=(name,), extra_aliases=aliases)


# SYNTHETIC. Places Candidates often name that no Service Area covers: nearby towns
# and other cities in both countries. They have no Location, so no recruiter or slots.
UNCOVERED_TOWNS: tuple[City, ...] = (
    _town("ES", "Getafe", "Europe/Madrid"),
    _town("ES", "Leganés", "Europe/Madrid"),
    _town("ES", "Toledo", "Europe/Madrid"),
    _town("ES", "Zaragoza", "Europe/Madrid"),
    _town("ES", "Bilbao", "Europe/Madrid"),
    _town("ES", "Granada", "Europe/Madrid"),
    _town("ES", "Badalona", "Europe/Madrid"),
    _town("ES", "Alicante", "Europe/Madrid", "Alacant"),
    _town("ES", "Córdoba", "Europe/Madrid"),
    _town("MX", "Toluca", "America/Mexico_City", "Toluca de Lerdo"),
    _town("MX", "Querétaro", "America/Mexico_City", "Santiago de Querétaro"),
    _town("MX", "Tijuana", "America/Tijuana", "Tijuana, Baja California"),
    _town("MX", "Cancún", "America/Cancun", "Cancún, Quintana Roo"),
    _town("MX", "Mérida", "America/Merida", "Mérida, Yucatán"),
    _town("MX", "León", "America/Mexico_City", "León, Guanajuato"),
    _town("MX", "Cuernavaca", "America/Mexico_City"),
    _town("MX", "Saltillo", "America/Monterrey"),
    _town("MX", "Cholula", "America/Mexico_City"),
)


def zone_id(city: City, zone: str) -> str:
    return f"zone_{city.country.lower()}_{city.slug}_{slugify(zone)}"


def location_id(city: City, zone: str) -> str:
    return f"loc_{city.country.lower()}_{city.slug}_{slugify(zone)}"


def _zone_aliases(city: City, zone: str) -> list[str]:
    aliases = {_ascii(zone), f"{zone}, {city.name}", f"{_ascii(zone)}, {_ascii(city.name)}"}
    if zone in city.unserved:
        aliases |= set(city.extra_aliases) | {_ascii(a) for a in city.extra_aliases}
    if zone == city.name:  # a town with a Zone of its own: "Toledo, Toledo" is noise
        aliases -= {f"{zone}, {city.name}", f"{_ascii(zone)}, {_ascii(city.name)}"}
    if zone == "Centro":
        aliases |= {f"Centro {city.name}", f"{city.name} Centro"}
    aliases.discard(zone)
    return sorted(aliases)


def seed(
    conn: sqlite3.Connection,
    *,
    slots_from: date | None = None,
    days: int = 21,
    now: datetime | None = None,
) -> dict[str, int]:
    """Insert the synthetic Client, Zones, Locations, Service Areas and slots.

    Returns row counts for the seeded tables.
    """
    current = now or utc_now()
    ts = iso(current)
    first_day = slots_from or current.date()
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO clients (id, name, created_at) VALUES (?, ?, ?)",
            (CLIENT_ID, CLIENT_NAME, ts),
        )
        for city in (*CITIES, *UNCOVERED_TOWNS):
            for zone in (*city.served, *city.unserved):
                conn.execute(
                    "INSERT OR IGNORE INTO zones (id, country, city, name, aliases, timezone)"
                    " VALUES (?, ?, ?, ?, json(?), ?)",
                    (
                        zone_id(city, zone),
                        city.country,
                        city.name,
                        zone,
                        _json_list(_zone_aliases(city, zone)),
                        city.timezone,
                    ),
                )
            for index, zone in enumerate(city.served):
                loc = location_id(city, zone)
                conn.execute(
                    "INSERT OR IGNORE INTO locations"
                    " (id, client_id, name, country, city, address, timezone, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        loc,
                        CLIENT_ID,
                        f"Sazón {city.name} {zone}",
                        city.country,
                        city.name,
                        f"(synthetic) Calle Ejemplo {index + 1}, {zone}, {city.name}",
                        city.timezone,
                        ts,
                    ),
                )
                area = f"sa_{loc.removeprefix('loc_')}"
                conn.execute(
                    "INSERT OR IGNORE INTO service_areas (id, location_id) VALUES (?, ?)",
                    (area, loc),
                )
                neighbours = city.served[max(0, index - 1) : index + 2]
                conn.executemany(
                    "INSERT OR IGNORE INTO service_area_zones (service_area_id, zone_id)"
                    " VALUES (?, ?)",
                    [(area, zone_id(city, z)) for z in neighbours],
                )
                _seed_slots(conn, loc, city.timezone, first_day, days, ts)
    return {
        table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "locations",
            "zones",
            "service_areas",
            "service_area_zones",
            "interview_slots",
        )
    }


def _json_list(items: list[str]) -> str:
    return json.dumps(items, ensure_ascii=False)


def _seed_slots(
    conn: sqlite3.Connection, loc: str, tz: str, first_day: date, days: int, ts: str
) -> None:
    zone = ZoneInfo(tz)
    rows = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        if day.weekday() == 6:  # no Interviews on Sunday
            continue
        for start in SLOT_TIMES:
            local = datetime.combine(day, start, tzinfo=zone)
            starts = local.astimezone(UTC)
            ends = starts + timedelta(minutes=SLOT_MINUTES)
            slot_id = f"slot_{loc.removeprefix('loc_')}_{day:%Y%m%d}_{start:%H%M}"
            rows.append((slot_id, loc, iso(starts), iso(ends), ts))
    conn.executemany(
        "INSERT OR IGNORE INTO interview_slots (id, location_id, starts_at, ends_at, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )


def seed_users(conn: sqlite3.Connection, password: str, *, now: datetime | None = None) -> None:
    """SYNTHETIC users: one Operator, and one Recruiter per city scoped to its Locations.

    The password is the caller's (never hardcoded); all seeded users share it.
    """
    from argon2 import PasswordHasher

    if not password:
        raise ValueError("a password is required")
    ts = iso(now or utc_now())
    password_hash = PasswordHasher().hash(password)
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO users (id, client_id, email, name, role, password_hash,"
            " created_at) VALUES (?, NULL, ?, ?, 'operator', ?, ?)",
            ("usr_operator", "operator@criba.example", "Operator (synthetic)", password_hash, ts),
        )
        for city in CITIES:
            user_id = f"usr_rec_{city.slug}"
            conn.execute(
                "INSERT OR IGNORE INTO users (id, client_id, email, name, role, password_hash,"
                " created_at) VALUES (?, ?, ?, ?, 'recruiter', ?, ?)",
                (
                    user_id,
                    CLIENT_ID,
                    f"recruiter.{city.slug}@sazon.example",
                    f"Recruiter {city.name} (synthetic)",
                    password_hash,
                    ts,
                ),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO user_locations (user_id, location_id) VALUES (?, ?)",
                [(user_id, location_id(city, zone)) for zone in city.served],
            )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m criba.db.seed <database path>", file=sys.stderr)
        return 2
    conn = connect(args[0])
    try:
        migrate(conn)
        counts = seed(conn)
        password = os.environ.get("CRIBA_SEED_PASSWORD")
        if password:
            seed_users(conn, password)
    finally:
        conn.close()
    print(f"seeded (synthetic): {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
