"""search_zones offers a known city's Zones when the query matched the city only (ADR 0062).

The demo chat (2026-10-11, Application app_01M4NHZ7MQY2XG4BJBYTXF8BRS): "Ciudad de Mexico en
Tepito". Tepito is not a seeded Zone, so search_zones returned the nine CDMX Zones; the agent
wrote "Del listado de zonas..." without calling record_answer, so no list was attached. Now
the search itself writes the city's options, so the list goes out with the agent's reply.
"""

from __future__ import annotations

from criba.db import repo
from criba.tools import pending_choice

from .test_tap_answer import TOLUCA, at_zone, tap

TEPITO = "Tepito, Ciudad de México"
CDMX_ZONES = 9


def zone_offers(tools, app_id: str) -> list[dict]:
    return [e for e in tools.events(app_id, "answer_ambiguous") if e["field"] == "zone"]


def test_a_city_plus_an_unknown_place_attaches_the_city_list(tools) -> None:
    app_id = at_zone(tools)
    message_id = tools.say(app_id, "Ciudad de Mexico en Tepito")
    value = tools.ok(app_id, "search_zones", query=TEPITO, country="MX")
    assert value["options_attached"] is True and len(value["zones"]) == CDMX_ZONES
    offers = zone_offers(tools, app_id)
    assert len(offers) == 1 and offers[0]["message_id"] == message_id
    pending = pending_choice(tools.conn, app_id)
    assert pending is not None and pending["style"] == "list"
    assert {o["id"] for o in pending["options"]} == {z["zone_id"] for z in value["zones"]}
    app = tools.app(app_id)
    assert app["zone_id"] is None and app["invalid_attempts"] == 0
    assert tools.events(app_id, "answer_invalid") == []


def test_the_tap_on_the_list_is_recorded_without_any_record_answer_call(tools) -> None:
    app_id = at_zone(tools)
    tools.say(app_id, "Ciudad de Mexico en Tepito")
    tools.ok(app_id, "search_zones", query=TEPITO, country="MX")
    tools.say(app_id, "Centro")
    body = tap(tools, app_id, "zone_mx_cdmx_centro")
    assert body is not None and body["value"]["next_field"] == "driver_license"
    assert tools.app(app_id)["zone_id"] == "zone_mx_cdmx_centro"


def test_recording_the_same_ids_in_the_same_turn_offers_the_list_once(tools) -> None:
    app_id = at_zone(tools)
    tools.say(app_id, "Ciudad de Mexico en Tepito")
    found = tools.ok(app_id, "search_zones", query=TEPITO, country="MX")["zones"]
    err = tools.err(app_id, "record_answer", field="zone", value=[z["zone_id"] for z in found])
    assert len(err["options"]) == CDMX_ZONES and "attempt" not in err
    assert len(zone_offers(tools, app_id)) == 1
    assert pending_choice(tools.conn, app_id) == {"style": "list", "options": err["options"]}


def test_the_same_list_after_a_new_candidate_message_is_offered_again(tools) -> None:
    app_id = at_zone(tools)
    tools.say(app_id, "Ciudad de Mexico en Tepito")
    tools.ok(app_id, "search_zones", query=TEPITO, country="MX")
    tools.say(app_id, "Ciudad de México")
    tools.ok(app_id, "search_zones", query="Ciudad de México", country="MX")
    assert len(zone_offers(tools, app_id)) == 2


def test_two_or_three_zones_of_a_city_are_buttons(tools) -> None:
    app_id = at_zone(tools)
    tools.say(app_id, "Puebla")
    value = tools.ok(app_id, "search_zones", query="Puebla")
    assert value["options_attached"] is True
    pending = pending_choice(tools.conn, app_id)
    assert pending is not None and pending["style"] == "buttons" and len(pending["options"]) == 3
    ids = [z["zone_id"] for z in value["zones"]]
    assert tools.ok(app_id, "record_answer", field="zone", value=ids)["status"] == "needs_choice"
    assert len(zone_offers(tools, app_id)) == 1


def test_a_named_zone_or_no_match_offers_nothing(tools) -> None:
    app_id = at_zone(tools)
    for query in ("Polanco, Ciudad de México", "Toluca"):
        assert "options_attached" not in tools.ok(app_id, "search_zones", query=query)
    assert zone_offers(tools, app_id) == []


def test_a_city_with_more_than_ten_zones_offers_nothing(tools, conn) -> None:
    for i in range(11):  # one invented city whose name alone matches 11 Zones
        conn.execute(
            "INSERT INTO zones (id, country, city, name, timezone, aliases)"
            " VALUES (?, 'ES', 'Villaprueba', ?, 'Europe/Madrid', '[]')",
            (f"zone_es_villaprueba_{i:02d}", f"Barrio {i:02d}"),
        )
    app_id = at_zone(tools, country="ES")
    value = tools.ok(app_id, "search_zones", query="Villaprueba")
    assert value["more"] is True and "options_attached" not in value
    assert zone_offers(tools, app_id) == []


def test_nothing_is_offered_once_the_zone_is_answered_before_the_confirm_step(tools) -> None:
    app_id = at_zone(tools)
    tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_cdmx_polanco"])
    value = tools.ok(app_id, "search_zones", query="Ciudad de México")
    assert "options_attached" not in value and zone_offers(tools, app_id) == []


def test_a_correction_at_the_confirm_step_gets_the_city_list(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.answer_all(app_id, "MX")
    assert tools.app(app_id)["stage"] == "confirm"
    tools.say(app_id, "no, vivo en Ciudad de México, en Tepito")
    value = tools.ok(app_id, "search_zones", query=TEPITO)
    assert value["options_attached"] is True
    pending = pending_choice(tools.conn, app_id)
    assert pending is not None and len(pending["options"]) == CDMX_ZONES


def test_a_waiting_zone_confirmation_is_not_replaced(tools) -> None:
    app_id = at_zone(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])  # ¿Vives en Toluca?
    value = tools.ok(app_id, "search_zones", query="Ciudad de México")
    assert "options_attached" not in value
    pending = pending_choice(tools.conn, app_id)
    assert pending is not None and [o["id"] for o in pending["options"]] == [TOLUCA, "no"]


def test_nothing_is_offered_under_takeover_or_on_a_closed_application(tools) -> None:
    app_id = at_zone(tools)
    tools.conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'user_rec' WHERE id = ?",
        (app_id,),
    )
    assert "options_attached" not in tools.ok(app_id, "search_zones", query=TEPITO)
    closed = at_zone(tools)
    repo.close_application(
        tools.conn, closed, "withdrawn", actor="candidate", now=tools.clock.now()
    )
    assert "options_attached" not in tools.ok(closed, "search_zones", query=TEPITO)
    assert zone_offers(tools, app_id) == [] and zone_offers(tools, closed) == []


def test_nothing_is_offered_after_screening(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.approve(app_id, "MX")
    assert "options_attached" not in tools.ok(app_id, "search_zones", query=TEPITO)
    assert zone_offers(tools, app_id) == []
