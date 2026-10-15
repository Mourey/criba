"""The Application's country follows the recorded Zone's (ADR 0057).

The demo chat of 2026-10-10 (real Haiku): an English web chat opened as MX recorded
Chamberí (Madrid), and the Application kept country MX, so its Spanish variant, templates
and time zone fallback were Mexico's while its Locations were in Madrid.
"""

from __future__ import annotations

from datetime import UTC, datetime

from criba.channel.copy import qualified_close

from .conftest import GOOD_ANSWERS

CHAMBERI = "zone_es_madrid_chamberi"
POLANCO = "zone_mx_cdmx_polanco"
GETAFE = "zone_es_getafe_getafe"  # outside every Service Area


def _named(tools, *, country: str, language: str) -> str:
    app_id = tools.new_app(country=country, language=language)
    tools.ok(app_id, "record_answer", field="full_name", value="Ramiro Salcedo")
    return app_id


def _candidate_language(tools, app_id: str) -> str | None:
    row = tools.conn.execute(
        "SELECT c.language FROM candidates c JOIN applications a ON a.candidate_id = c.id"
        " WHERE a.id = ?",
        (app_id,),
    ).fetchone()
    return row["language"]


def test_a_spanish_zone_moves_a_mexican_application_to_spain_and_es_es(tools) -> None:
    app_id = _named(tools, country="MX", language="es-MX")
    value = tools.ok(app_id, "record_answer", field="zone", value=[CHAMBERI])
    assert value["status"] == "accepted" and value["country"] == "ES"
    app = tools.app(app_id)
    assert (app["country"], app["language"], app["zone_id"]) == ("ES", "es-ES", CHAMBERI)
    assert _candidate_language(tools, app_id) == "es-ES"
    [event] = [e for e in tools.events(app_id, "answer_recorded") if e["field"] == "zone"]
    assert event["country_changed"] == {"from": "MX", "to": "ES"}
    assert event["language_changed"] == {"from": "es-MX", "to": "es-ES"}
    assert event["locations"] and all(loc.startswith("loc_es_") for loc in event["locations"])
    view = tools.ok(app_id, "get_application")
    assert (view["country"], view["language"]) == ("ES", "es-ES")


def test_english_stays_english_when_the_country_follows_the_zone(tools) -> None:
    """The demo chat: English on an MX web chat, living in Chamberí."""
    app_id = _named(tools, country="MX", language="en")
    value = tools.ok(app_id, "record_answer", field="zone", value=[CHAMBERI])
    assert value["country"] == "ES"
    app = tools.app(app_id)
    assert (app["country"], app["language"]) == ("ES", "en")
    assert _candidate_language(tools, app_id) == "en"
    [event] = [e for e in tools.events(app_id, "answer_recorded") if e["field"] == "zone"]
    assert event["country_changed"] == {"from": "MX", "to": "ES"}
    assert "language_changed" not in event


def test_a_zone_in_the_same_country_changes_nothing_and_says_nothing(tools) -> None:
    app_id = _named(tools, country="MX", language="es-MX")
    value = tools.ok(app_id, "record_answer", field="zone", value=[POLANCO])
    assert value["status"] == "accepted" and "country" not in value
    app = tools.app(app_id)
    assert (app["country"], app["language"]) == ("MX", "es-MX")
    [event] = [e for e in tools.events(app_id, "answer_recorded") if e["field"] == "zone"]
    assert "country_changed" not in event and "language_changed" not in event


def test_the_date_and_a_reported_spanish_follow_the_new_country(tools) -> None:
    """At 23:30 UTC on Friday 9 it is already Saturday in Madrid, still Friday in Mexico City.
    A Spanish language the agent reports afterwards is Spain's (ADR 0047: Spanish follows
    `country`), so the Jev router's and the agent's variants agree."""
    tools.clock.set(datetime(2026, 10, 9, 23, 30, tzinfo=UTC))
    app_id = _named(tools, country="MX", language="en")
    assert tools.ok(app_id, "get_application")["today"] == "2026-10-09"
    tools.ok(app_id, "record_answer", field="zone", value=[CHAMBERI])
    assert tools.ok(app_id, "get_application")["today"] == "2026-10-10"
    view = tools.ok(app_id, "get_application", language="es-MX")
    assert view["language"] == "es-ES"
    value = tools.ok(app_id, "record_answer", field="start_date", value="2026-10-11")
    assert value["status"] == "accepted"  # tomorrow in Madrid


def test_a_correction_to_a_mexican_zone_moves_it_back(tools) -> None:
    app_id = _named(tools, country="MX", language="es-MX")
    tools.ok(app_id, "record_answer", field="zone", value=[CHAMBERI])
    value = tools.ok(app_id, "record_answer", field="zone", value=[POLANCO])
    assert value["country"] == "MX"
    app = tools.app(app_id)
    assert (app["country"], app["language"]) == ("MX", "es-MX")
    last = [e for e in tools.events(app_id, "answer_recorded") if e["field"] == "zone"][-1]
    assert last["country_changed"] == {"from": "ES", "to": "MX"}


def test_a_zone_knockout_in_the_other_country_uses_that_countrys_template(tools) -> None:
    app_id = _named(tools, country="MX", language="es-MX")
    tools.ok(app_id, "record_answer", field="zone", value=[GETAFE])  # asks "¿Vives en …?"
    tools.say(app_id, "Sí")
    value = tools.ok(app_id, "record_answer", field="zone", value=[GETAFE])
    assert value["status"] == "knockout"
    assert value["template"] == "knockout_zone.es-ES" and "Getafe" in value["message"]
    app = tools.app(app_id)
    assert (app["status"], app["country"], app["language"]) == ("closed", "ES", "es-ES")


def test_the_qualified_close_follows_the_new_country(tools) -> None:
    app_id = tools.new_app(country="MX", language="es-MX")
    for field, answer in GOOD_ANSWERS["ES"]:
        tools.ok(app_id, "record_answer", field=field, value=answer)
    value = tools.ok(app_id, "finish_screening")
    assert value["message"] == qualified_close("es-ES", "ES", "Ana")
