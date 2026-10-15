import sqlite3
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from argon2 import PasswordHasher

from criba.db.repo import covering_location_ids
from criba.db.seed import CLIENT_ID, seed, seed_users

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)


def scalar(conn: sqlite3.Connection, sql: str, *params: object) -> object:
    return conn.execute(sql, params).fetchone()[0]


def test_seed_creates_45_locations_in_spain_and_mexico(seeded: sqlite3.Connection) -> None:
    by_country = dict(
        seeded.execute("SELECT country, count(*) FROM locations GROUP BY country").fetchall()
    )
    assert sum(by_country.values()) == 45
    assert set(by_country) == {"ES", "MX"}
    assert scalar(seeded, "SELECT count(DISTINCT client_id) FROM locations") == 1
    assert scalar(seeded, "SELECT client_id FROM locations LIMIT 1") == CLIENT_ID


def test_seed_is_marked_synthetic(seeded: sqlite3.Connection) -> None:
    addresses = [r[0] for r in seeded.execute("SELECT address FROM locations")]
    assert all(a.startswith("(synthetic)") for a in addresses)


def test_every_location_has_a_service_area_and_slots(seeded: sqlite3.Connection) -> None:
    missing_area = scalar(
        seeded,
        "SELECT count(*) FROM locations l WHERE NOT EXISTS ("
        " SELECT 1 FROM service_areas sa JOIN service_area_zones z ON z.service_area_id = sa.id"
        " WHERE sa.location_id = l.id)",
    )
    missing_slots = scalar(
        seeded,
        "SELECT count(*) FROM locations l WHERE NOT EXISTS ("
        " SELECT 1 FROM interview_slots s WHERE s.location_id = l.id)",
    )
    assert missing_area == 0
    assert missing_slots == 0


def test_service_areas_overlap_and_leave_some_zones_uncovered(
    seeded: sqlite3.Connection,
) -> None:
    zones = [r[0] for r in seeded.execute("SELECT id FROM zones")]
    coverage = {z: covering_location_ids(seeded, z) for z in zones}
    assert any(len(locs) >= 2 for locs in coverage.values())
    uncovered = [z for z, locs in coverage.items() if not locs]
    assert uncovered, "some Zones must be outside every Service Area for Knockouts"
    assert "zone_mx_puebla_centro" in zones
    assert "loc_mx_puebla_centro" in coverage["zone_mx_puebla_centro"]
    # A Zone is only ever covered by Locations in its own country.
    for zone, locs in coverage.items():
        for loc in locs:
            assert zone.split("_")[1] == loc.split("_")[1]


def test_centro_is_ambiguous_across_cities(seeded: sqlite3.Connection) -> None:
    cities = [r[0] for r in seeded.execute("SELECT city FROM zones WHERE name = 'Centro'")]
    assert len(cities) >= 3


def test_slots_fall_at_local_business_hours(seeded: sqlite3.Connection) -> None:
    rows = seeded.execute(
        "SELECT s.starts_at, s.ends_at, l.timezone FROM interview_slots s"
        " JOIN locations l ON l.id = s.location_id"
    ).fetchall()
    assert rows
    for starts_at, ends_at, tz in rows:
        local = datetime.fromisoformat(starts_at).astimezone(ZoneInfo(tz))
        assert (local.hour, local.minute) in {(10, 0), (12, 0), (16, 0)}
        assert local.weekday() != 6
        assert ends_at > starts_at
        assert date(2026, 10, 12) <= local.date() < date(2026, 10, 19)


def test_seeding_twice_changes_nothing(seeded: sqlite3.Connection) -> None:
    tables = ["clients", "zones", "locations", "service_areas", "service_area_zones"]
    tables.append("interview_slots")
    before = {t: scalar(seeded, f"SELECT count(*) FROM {t}") for t in tables}
    seed(seeded, slots_from=date(2026, 10, 12), days=7, now=NOW)
    assert {t: scalar(seeded, f"SELECT count(*) FROM {t}") for t in tables} == before


def test_zone_aliases_are_json_lists(seeded: sqlite3.Connection) -> None:
    aliases = scalar(seeded, "SELECT aliases FROM zones WHERE id = 'zone_es_madrid_chamberi'")
    assert "Chamberi" in str(aliases)


def test_seed_users_scopes_recruiters_to_their_city(seeded: sqlite3.Connection) -> None:
    seed_users(seeded, "synthetic-test-password", now=datetime(2026, 10, 9, tzinfo=UTC))
    seed_users(seeded, "synthetic-test-password", now=datetime(2026, 10, 9, tzinfo=UTC))
    assert scalar(seeded, "SELECT count(*) FROM users WHERE role = 'operator'") == 1
    puebla = [
        r[0]
        for r in seeded.execute(
            "SELECT location_id FROM user_locations WHERE user_id = 'usr_rec_puebla'"
        )
    ]
    assert puebla and all(loc.startswith("loc_mx_puebla_") for loc in puebla)
    stored = scalar(seeded, "SELECT password_hash FROM users WHERE id = 'usr_operator'")
    assert PasswordHasher().verify(str(stored), "synthetic-test-password")
