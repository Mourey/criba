"""search_zones (ADR 0042, spec 006a task 4): a read-only Zone lookup over the SYNTHETIC seed.

It returns the top match tier only, at most 10 rows plus ``more``, with the Application's
country first when ``country`` is omitted. It reads reference tables only, enforced by a
SQLite authorizer, and works without an open Application. Its one write, the city's Zones
offered when only a city matched (ADR 0062), is tested in ``test_city_zone_offer.py``.
"""

from __future__ import annotations

import sqlite3

import pytest

from criba.db import repo
from criba.screening.results import REPLY_BUTTON_MAX
from criba.tools.zones_lookup import REFERENCE_TABLES, reference_tables_only

ROW_KEYS = {"zone_id", "label", "city", "country", "covered"}


def ids(value: dict) -> list[str]:
    return [z["zone_id"] for z in value["zones"]]


def filled(value: dict, home: str) -> dict:
    """The result with the keys it leaves out put back: a row without `country` is in the
    Application's own country (``home``) and one without `covered` is covered. The tool sends
    them only when they say something (docs/evidence/turn-size.md)."""
    rows = [{"country": home, "covered": True, **row} for row in value["zones"]]
    return {**value, "zones": rows}


def test_a_neighbourhood_is_one_covered_zone(tools) -> None:
    app_id = tools.new_app(country="ES")
    value = tools.ok(app_id, "search_zones", query="vivo en Chamberí")
    assert value["zones"] == [
        {"zone_id": "zone_es_madrid_chamberi", "label": "Chamberí (Madrid)", "city": "Madrid"}
    ]  # no country (the Application's own) and no covered (true)
    value = filled(value, "ES")
    assert value == {
        "zones": [
            {
                "zone_id": "zone_es_madrid_chamberi",
                "label": "Chamberí (Madrid)",
                "city": "Madrid",
                "country": "ES",
                "covered": True,
            }
        ],
        "more": False,
    }


def test_an_uncovered_town_is_returned_with_covered_false(tools) -> None:
    app_id = tools.new_app(country="MX")
    value = tools.ok(app_id, "search_zones", query="Toluca")
    assert value["zones"][0]["covered"] is False and "country" not in value["zones"][0]
    value = filled(value, "MX")
    assert value["zones"] == [
        {
            "zone_id": "zone_mx_toluca_toluca",
            "label": "Toluca",
            "city": "Toluca",
            "country": "MX",
            "covered": False,
        }
    ]


def test_a_city_returns_its_zones_for_the_q45_list(tools) -> None:
    app_id = tools.new_app(country="MX")
    value = filled(tools.ok(app_id, "search_zones", query="Ciudad de México", country="MX"), "MX")
    assert len(value["zones"]) == 9 and value["more"] is False
    assert {z["city"] for z in value["zones"]} == {"Ciudad de México"}
    assert "zone_mx_cdmx_milpa_alta" in ids(value)  # uncovered Zones are listed too
    assert {z["covered"] for z in value["zones"]} == {True, False}


def test_a_one_city_list_has_bare_zone_names(tools) -> None:
    app_id = tools.new_app(country="MX")
    value = tools.ok(app_id, "search_zones", query="Ciudad de México", country="MX")
    labels = {z["zone_id"]: z["label"] for z in value["zones"]}
    assert labels["zone_mx_cdmx_milpa_alta"] == "Milpa Alta"
    assert not any("(" in label for label in labels.values())
    assert all(len(label) <= REPLY_BUTTON_MAX for label in labels.values())


def test_a_mixed_city_list_keeps_the_city_with_its_alias(tools) -> None:
    value = tools.ok(tools.new_app(country="MX"), "search_zones", query="Centro", country="MX")
    labels = {z["zone_id"]: z["label"] for z in value["zones"]}
    assert labels["zone_mx_cdmx_centro"] == "Centro (CDMX)"
    assert labels["zone_mx_puebla_centro"] == "Centro (Puebla)"
    assert all(len(label) <= REPLY_BUTTON_MAX for label in labels.values())


def test_without_country_the_applications_country_comes_first(tools) -> None:
    mx = tools.ok(tools.new_app(country="MX"), "search_zones", query="Centro")
    es = tools.ok(tools.new_app(country="ES"), "search_zones", query="Centro")
    # Only the rows from the other country carry `country`.
    assert [z.get("country") for z in mx["zones"]] == [None] * 4 + ["ES"] * 4
    assert [z.get("country") for z in es["zones"]] == [None] * 4 + ["MX"] * 4
    mx, es = filled(mx, "MX"), filled(es, "ES")
    assert sorted(ids(mx)) == sorted(ids(es)) and len(ids(mx)) == 8
    assert [z["country"] for z in mx["zones"]] == ["MX"] * 4 + ["ES"] * 4
    assert [z["country"] for z in es["zones"]] == ["ES"] * 4 + ["MX"] * 4


def test_country_limits_the_search(tools) -> None:
    value = tools.ok(tools.new_app(country="ES"), "search_zones", query="Centro", country="MX")
    assert {z["country"] for z in value["zones"]} == {"MX"}
    assert len(value["zones"]) == 4


def test_more_than_ten_matches_are_cut_to_ten_with_more(tools, conn) -> None:
    # Eleven extra uncovered Zones in one invented city, so the city alone matches 11.
    for i in range(11):
        conn.execute(
            "INSERT INTO zones (id, country, city, name, timezone, aliases)"
            " VALUES (?, 'ES', 'Villaprueba', ?, 'Europe/Madrid', '[]')",
            (f"zone_es_villaprueba_{i:02d}", f"Barrio {i:02d}"),
        )
    value = tools.ok(tools.new_app(country="ES"), "search_zones", query="Villaprueba")
    assert len(value["zones"]) == 10 and value["more"] is True


def test_nothing_matched_is_an_empty_list(tools) -> None:
    value = tools.ok(tools.new_app(country="ES"), "search_zones", query="qzx nowhere")
    # The Zone is not the question yet (no name), so no funnel: the agent gets the only cities it
    # may name instead (ADR 0068).
    assert value == {
        "zones": [],
        "more": False,
        "service_cities": ["Barcelona", "Madrid", "Málaga", "Sevilla", "Valencia"],
    }


def test_rows_carry_only_the_five_fields(tools) -> None:
    value = tools.ok(tools.new_app(country="MX"), "search_zones", query="Puebla")
    assert value["zones"] and all(set(z) == {"zone_id", "label", "city"} for z in value["zones"])
    value = filled(value, "MX")
    assert value["zones"] and all(set(z) == ROW_KEYS for z in value["zones"])


def test_it_writes_no_event_and_works_on_a_closed_application(tools, conn) -> None:
    app_id = tools.new_app(country="MX")
    before = repo.last_event_id(conn, app_id)
    tools.ok(app_id, "search_zones", query="Polanco")
    assert repo.last_event_id(conn, app_id) == before
    repo.close_application(conn, app_id, "withdrawn", actor="candidate", now=tools.clock.now())
    assert ids(tools.ok(app_id, "search_zones", query="Polanco")) == ["zone_mx_cdmx_polanco"]


def test_the_query_is_bounded_by_the_schema(tools) -> None:
    app_id = tools.new_app()
    for args in ({"query": ""}, {"query": "x" * 101}, {"query": "Centro", "country": "FR"}, {}):
        status, body = tools.raw(app_id, "search_zones", **args)
        assert status == 422 and body["error"] == "invalid_args"


def test_an_unknown_session_is_refused(tools) -> None:
    status, body = tools.raw("app_nope", "search_zones", query="Centro")
    assert status == 200 and body["error"] == "unknown_session"


# --- the authorizer -----------------------------------------------------------------


def test_reference_tables_are_the_four_the_adr_names() -> None:
    assert {"zones", "service_area_zones", "service_areas", "locations"} == REFERENCE_TABLES


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM candidates",
        "SELECT * FROM applications",
        "SELECT body FROM messages",
        "SELECT * FROM events",
        "SELECT z.id FROM zones z JOIN applications a ON a.zone_id = z.id",
        "SELECT name FROM sqlite_master",
        "UPDATE zones SET name = 'x'",
        "INSERT INTO events (candidate_id, kind, actor, data, created_at)"
        " VALUES ('c', 'opted_out', 'system', '{}', '')",
        "DELETE FROM zones",
        "PRAGMA table_info(zones)",
    ],
)
def test_the_authorizer_denies_everything_but_reading_reference_tables(conn, sql) -> None:
    with (
        reference_tables_only(conn),
        pytest.raises(sqlite3.DatabaseError, match=r"prohibited|not authorized"),
    ):
        conn.execute(sql)


def test_the_authorizer_allows_the_reference_tables_and_is_removed_after(conn) -> None:
    with reference_tables_only(conn):
        for table in sorted(REFERENCE_TABLES):
            conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()  # allowed
    assert conn.execute("SELECT count(*) FROM candidates").fetchone()[0] >= 0


def test_a_statement_prepared_before_is_checked_again(conn) -> None:
    sql = "SELECT count(*) FROM candidates"
    conn.execute(sql).fetchone()  # now in the statement cache
    with reference_tables_only(conn), pytest.raises(sqlite3.DatabaseError):
        conn.execute(sql)
