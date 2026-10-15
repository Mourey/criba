"""The synthetic seed lists uncovered Zones so `outside_service_areas` is reachable.

Spec section 3: an unknown place is re-asked, and a Zone Knockout needs a known Zone
that no Service Area covers. These tests lock the seeded set and prove it leaves the
resolution of the existing Zones unchanged.

Under ADR 0042 a place goes through two steps: ``search_zones`` ranks the seeded Zones
(``rank_zones``), and the agent passes the ids it picked to ``record_answer``, which
verifies them (``verify_zone``). ``check_zone`` plays both, picking what a competent agent
would: the readings in the Candidate's country if there are any, at most ten.
"""

import json
import sqlite3
from datetime import UTC, date, datetime

import pytest

from criba.db.repo import covering_location_ids
from criba.db.seed import CITIES, UNCOVERED_TOWNS, location_id, seed, zone_id
from criba.screening import Knockout, NeedsChoice, rank_zones
from criba.screening.results import Accepted, Invalid, ValidationResult
from criba.screening.verify import verify_zone
from criba.screening.zones import ZoneRef

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
TOWNS = [(t.country, t.name, t.timezone) for t in UNCOVERED_TOWNS]
TOWN_IDS = {zone_id(t, t.name) for t in UNCOVERED_TOWNS}


def load_zones(conn: sqlite3.Connection) -> list[ZoneRef]:
    rows = conn.execute(
        "SELECT z.id, z.country, z.city, z.name, z.timezone, z.aliases,"
        " EXISTS (SELECT 1 FROM service_area_zones saz WHERE saz.zone_id = z.id) AS covered"
        " FROM zones z ORDER BY z.id"
    ).fetchall()
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


def check_zone(
    zones: list[ZoneRef], text: str, country: str | None = None
) -> ValidationResult | None:
    """Search, then verify the ids found; None when the search finds nothing (a re-ask)."""
    _, matched = rank_zones(text, zones)
    home = [z for z in matched if z.country == country] or matched
    ids = [z.id for z in sorted(home, key=lambda z: (z.city, z.name, z.id))][:10]
    return verify_zone(ids, zones) if ids else None


def tap(zones: list[ZoneRef], zone: str) -> ValidationResult:
    """The Candidate taps a Zone id we offered (a button, a list row or the Sí)."""
    return verify_zone([zone], zones, offered={zone})


def shape(result: object) -> object:
    """A comparable summary of a validation result."""
    if result is None:
        return ("not_found",)
    if isinstance(result, NeedsChoice):
        return ("choice", tuple(o.id for o in result.options), result.confirm)
    if isinstance(result, Knockout):
        return ("knockout", result.reason, result.value.id)
    if isinstance(result, Accepted):
        return ("accepted", result.value.id)
    assert isinstance(result, Invalid)
    return ("invalid", result.reason)


def without_new_towns(zones: list[ZoneRef]) -> list[ZoneRef]:
    return [z for z in zones if z.id not in TOWN_IDS]


def test_each_country_gets_a_sensible_set() -> None:
    by_country = {c: [t for t in UNCOVERED_TOWNS if t.country == c] for c in ("ES", "MX")}
    assert all(6 <= len(towns) <= 10 for towns in by_country.values())
    assert len(TOWN_IDS) == len(UNCOVERED_TOWNS)
    for example in ("Getafe", "Toledo", "Zaragoza", "Bilbao", "Granada"):
        assert example in {t.name for t in by_country["ES"]}
    for example in ("Toluca", "Querétaro", "Tijuana", "Cancún", "Mérida", "León"):
        assert example in {t.name for t in by_country["MX"]}


def test_new_zones_are_seeded_with_iana_timezones(seeded: sqlite3.Connection) -> None:
    rows = {
        r["name"]: r
        for r in seeded.execute(
            f"SELECT * FROM zones WHERE id IN ({','.join('?' * len(TOWN_IDS))})", [*TOWN_IDS]
        )
    }
    assert len(rows) == len(UNCOVERED_TOWNS)
    assert {n: r["timezone"] for n, r in rows.items()} == {name: tz for _, name, tz in TOWNS}
    assert rows["Tijuana"]["timezone"] == "America/Tijuana"
    assert rows["Cancún"]["timezone"] == "America/Cancun"
    assert "Cancun" in json.loads(rows["Cancún"]["aliases"])


@pytest.mark.parametrize(("country", "name", "tz"), TOWNS)
def test_every_new_zone_is_uncovered_and_has_no_location(
    seeded: sqlite3.Connection, country: str, name: str, tz: str
) -> None:
    town = next(t for t in UNCOVERED_TOWNS if t.name == name)
    zid = zone_id(town, name)
    assert covering_location_ids(seeded, zid) == []
    in_areas = seeded.execute(
        "SELECT count(*) FROM service_area_zones WHERE zone_id = ?", (zid,)
    ).fetchone()[0]
    locations = seeded.execute(
        "SELECT count(*) FROM locations WHERE id = ?", (location_id(town, name),)
    ).fetchone()[0]
    assert (in_areas, locations) == (0, 0)


def test_the_45_locations_and_their_service_areas_are_unchanged(
    seeded: sqlite3.Connection,
) -> None:
    assert seeded.execute("SELECT count(*) FROM locations").fetchone()[0] == 45
    assert seeded.execute("SELECT count(*) FROM service_areas").fetchone()[0] == 45
    covered = seeded.execute("SELECT count(DISTINCT zone_id) FROM service_area_zones").fetchone()[0]
    assert covered == sum(len(c.served) for c in CITIES)


@pytest.mark.parametrize(("country", "name", "tz"), TOWNS)
def test_naming_a_new_town_is_confirmed_then_an_outside_service_areas_knockout(
    seeded: sqlite3.Connection, country: str, name: str, tz: str
) -> None:
    zones = load_zones(seeded)
    town = next(t for t in UNCOVERED_TOWNS if t.name == name)
    zid = zone_id(town, name)
    for text in (name, name.upper(), f"vivo en {name}"):
        for phone_country in (country, None):
            result = check_zone(zones, text, phone_country)
            assert isinstance(result, NeedsChoice), (text, result)
            assert result.confirm == zid
            assert [o.id for o in result.options] == [zid, "no"]
    knockout = tap(zones, zid)
    assert isinstance(knockout, Knockout)
    assert knockout.criterion == "zone"
    assert knockout.reason == "outside_service_areas"
    assert knockout.value.id == zid


def test_accents_and_the_city_form_resolve_to_the_new_zones(seeded: sqlite3.Connection) -> None:
    zones = load_zones(seeded)
    for text, name in [
        ("cancun", "Cancún"),
        ("Queretaro", "Querétaro"),
        ("Merida, Yucatan", "Mérida"),
        ("leon guanajuato", "León"),
        ("Getafe, Madrid", "Getafe"),
        ("Leganes", "Leganés"),
    ]:
        result = check_zone(zones, text)
        assert isinstance(result, NeedsChoice) and result.confirm, (text, result)
        assert next(z for z in zones if z.id == result.confirm).name == name


def test_puebla_still_offers_exactly_the_same_three_options(seeded: sqlite3.Connection) -> None:
    zones = load_zones(seeded)
    for country in ("MX", None):
        result = check_zone(zones, "Puebla", country)
        assert isinstance(result, NeedsChoice)
        assert [o.id for o in result.options] == [
            "zone_mx_puebla_angelopolis",
            "zone_mx_puebla_centro",
            "zone_mx_puebla_la_paz",
        ]
        assert [o.label for o in result.options] == [
            "Angelópolis",
            "Centro",
            "La Paz",
        ]
    # The 2-3 way ambiguous Zone that the sim persona no_license_mx relies on.
    lowercase = check_zone(zones, "puebla", "MX")
    assert isinstance(lowercase, NeedsChoice) and len(lowercase.options) == 3


def test_centro_behaves_as_before(seeded: sqlite3.Connection) -> None:
    zones = load_zones(seeded)
    assert len([z for z in zones if z.name == "Centro"]) >= 4
    result = check_zone(zones, "Centro", "MX")
    assert isinstance(result, Invalid) and result.reason == "ambiguous_zone"
    assert shape(result) == shape(check_zone(without_new_towns(zones), "Centro", "MX"))
    assert shape(check_zone(zones, "Centro, Puebla", "MX")) == (
        "accepted",
        "zone_mx_puebla_centro",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Ciudad de México",
        "Ciudad de Mexico",
        "ciudad de mexico",
        "Ciudad de Mexico, Cuajimalpa",  # a barrio we do not know, in a city we do (Q45)
        "vivo en Ciudad de México, por Santa Fe",
    ],
)
def test_a_known_city_without_a_single_zone_gets_its_zones_as_a_list(
    seeded: sqlite3.Connection, text: str
) -> None:
    zones = load_zones(seeded)
    expected = sorted(
        (z for z in zones if z.city == "Ciudad de México"), key=lambda z: z.name.casefold()
    )
    assert len(expected) == 9
    for country in ("MX", None):
        result = check_zone(zones, text, country)
        assert isinstance(result, Invalid) and result.narrowing, (text, country, result)
        assert [o.id for o in result.options] == [
            z.id for z in sorted(expected, key=lambda z: (z.name.casefold(), z.id))
        ]
        assert all(len(o.label) <= 20 for o in result.options)
        assert result.options[0].label == "Centro"
        assert [o.label for o in result.options] == [z.name for z in expected]
        assert "City recognised (Ciudad de México)" in result.hint
        assert "offer the list" in result.hint


def test_the_city_list_still_leaves_the_other_outcomes_alone(seeded: sqlite3.Connection) -> None:
    zones = load_zones(seeded)
    # A known uncovered Zone of the same city is a Knockout, not a list.
    # A known uncovered Zone of the same city is confirmed, then a Knockout, not a list.
    confirm = check_zone(zones, "Milpa Alta, Ciudad de México", "MX")
    assert isinstance(confirm, NeedsChoice) and confirm.confirm is not None
    knockout = tap(zones, confirm.confirm)
    assert isinstance(knockout, Knockout) and knockout.reason == "outside_service_areas"
    # A tapped option from the list resolves to the Zone.
    assert isinstance(tap(zones, "zone_mx_cdmx_polanco"), Accepted)
    # An unknown place in an unknown city finds nothing: the agent re-asks.
    assert check_zone(zones, "Colonia Nueva, Ciudad de Nunca Jamás", "MX") is None


def test_existing_zones_resolve_exactly_as_before(seeded: sqlite3.Connection) -> None:
    """Every name, alias and city of an old Zone gives the same result with or without
    the new towns, for each phone country, and so do some typos and unknown places."""
    zones = load_zones(seeded)
    old = without_new_towns(zones)
    texts = {t for z in old for t in (z.name, z.city, *z.aliases, f"{z.name}, {z.city}")}
    texts |= {"Chamberi", "Madrid", "Barcelona", "Sevilla", "Monterrey", "Guadalajara"}
    texts |= {"Salamnca", "Zapoan", "Coyoacan", "Tlaquepake", "Ciudad de Mexico", "Mexico"}
    texts |= {"Alcalá de Henares", "Dos Hermanas", "Milpa Alta", "Apodaca", "Narnia", "xyz"}
    assert len(texts) > 100
    for text in sorted(texts):
        for country in ("ES", "MX", None):
            assert shape(check_zone(zones, text, country)) == shape(
                check_zone(old, text, country)
            ), (text, country)


def test_unknown_places_are_still_a_reask_not_a_knockout(seeded: sqlite3.Connection) -> None:
    zones = load_zones(seeded)
    assert check_zone(zones, "Narnia", "ES") is None
    # An id the search never returned is the agent's error, not a Knockout or a miss.
    result = verify_zone(["zone_es_narnia"], zones)
    assert isinstance(result, Invalid) and result.reason == "unknown_zone_id"
    assert not result.counted


def test_seeding_twice_changes_nothing_including_the_new_zones(
    seeded: sqlite3.Connection,
) -> None:
    def snapshot() -> dict[str, list[tuple]]:
        return {
            t: [tuple(r) for r in seeded.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
            for t in ("zones", "locations", "service_areas", "service_area_zones")
        }

    before = snapshot()
    seed(seeded, slots_from=date(2026, 10, 12), days=7, now=NOW)
    assert snapshot() == before


def test_an_existing_database_gains_the_new_zones_on_reseed(
    seeded: sqlite3.Connection,
) -> None:
    """A database seeded before this change lacks the towns; seeding again adds just them."""
    seeded.execute(f"DELETE FROM zones WHERE id IN ({','.join('?' * len(TOWN_IDS))})", [*TOWN_IDS])
    seeded.commit()
    total = seeded.execute("SELECT count(*) FROM zones").fetchone()[0]
    assert total == sum(len(c.served) + len(c.unserved) for c in CITIES)
    seed(seeded, slots_from=date(2026, 10, 12), days=7, now=NOW)
    assert seeded.execute("SELECT count(*) FROM zones").fetchone()[0] == total + len(TOWN_IDS)
    ids = {r[0] for r in seeded.execute("SELECT id FROM zones")}
    assert ids >= TOWN_IDS
    toluca = check_zone(load_zones(seeded), "Toluca")
    assert isinstance(toluca, NeedsChoice) and toluca.confirm is not None
    assert isinstance(tap(load_zones(seeded), toluca.confirm), Knockout)
