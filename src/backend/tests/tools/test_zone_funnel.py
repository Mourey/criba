"""The Zone funnel (ADR 0068): a place that matches no Zone is asked once, as a list of the cities
with a Service Area, and "none of these" (or a second answer that still matches nothing) is the
Zone Knockout.

The demo chat (Application app_01M4PGC8A4JPQ3NAEK0KK03EWA, real Haiku): "I live in Texas!" and
"I live in the united states" were two unresolved Zone answers, no Knockout, and a loop.
"""

from __future__ import annotations

import json

from criba.channel.copy import PRIVACY_URL
from criba.db import repo
from criba.tools import funnel, pending_choice, tap_answer
from criba.tools.application import ZONE_FUNNEL_NOTE, ZONE_NOTE, zone_note
from criba.tools.knockout import REQUIREMENTS_URL

from .test_tap_answer import at_zone, tap

MX_CITIES = ["Ciudad de México", "Guadalajara", "Monterrey", "Puebla"]
ES_CITIES = ["Barcelona", "Madrid", "Málaga", "Sevilla", "Valencia"]
CDMX = "city_mx_ciudad_de_mexico"
NONE = funnel.NO_CITY_OPTION_ID


def funnel_events(tools, app_id: str) -> list[dict]:
    return [e for e in tools.events(app_id, "answer_ambiguous") if e.get("funnel")]


def tapping(tools, app_id: str, option_id: str, label: str) -> dict | None:
    """The Candidate taps ``option_id``: the stored message is a button reply, as the web chat
    stores it, and the backend records it (ADR 0043)."""
    app = tools.app(app_id)
    repo.add_message(
        tools.conn,
        candidate_id=app["candidate_id"],
        application_id=app_id,
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body=label,
        content_type="button_reply",
        reply_option_id=option_id,
        now=tools.clock.now(),
    )
    return tap(tools, app_id, option_id)


def texas(tools, app_id: str) -> dict:
    """The Candidate writes a place that is no Zone, and the agent searches it."""
    tools.say(app_id, "I live in Texas!")
    return tools.ok(app_id, "search_zones", query="Texas")


def test_a_search_that_finds_nothing_offers_the_cities_once(tools) -> None:
    app_id = at_zone(tools)
    message_id = tools.say(app_id, "I live in Texas!")
    value = tools.ok(app_id, "search_zones", query="Texas")
    assert value["zones"] == [] and value["more"] is False
    assert value["options_attached"] is True
    for city in MX_CITIES:
        assert city in value["message"]
    assert "?" in value["message"] and "Toluca" not in value["message"]
    (event,) = funnel_events(tools, app_id)
    assert event["field"] == "zone" and event["message_id"] == message_id
    assert set(event) == {"field", "options", "funnel", "message_id"}  # ids and codes only
    pending = pending_choice(tools.conn, app_id)
    assert pending is not None and pending["style"] == "list"
    assert [o["label"] for o in pending["options"]][:-1] == MX_CITIES
    assert [o["id"] for o in pending["options"]][-1] == NONE
    app = tools.app(app_id)
    assert app["zone_id"] is None and app["invalid_attempts"] == 0
    assert tools.events(app_id, "answer_invalid") == []


def test_the_question_and_the_buttons_follow_the_applications_language(tools) -> None:
    english = tools.new_app(country="MX", language="en")
    tools.say(english, "Luis Hernández")
    tools.ok(english, "record_answer", field="full_name", value="Luis Hernández")
    value = texas(tools, english)
    assert value["message"].startswith("We hire delivery drivers in these cities: ")
    assert pending_choice(tools.conn, english)["options"][-1]["label"] == "None of these"

    mexico = at_zone(tools)
    texas(tools, mexico)
    assert pending_choice(tools.conn, mexico)["options"][-1]["label"] == "Ninguna de estas"

    spain = at_zone(tools, "ES")
    tools.say(spain, "Vivo en Lima")
    value = tools.ok(spain, "search_zones", query="Lima")
    for city in ES_CITIES:
        assert city in value["message"]
    assert [o["label"] for o in pending_choice(tools.conn, spain)["options"]][:-1] == ES_CITIES


def test_the_same_message_asks_again_for_nothing_new(tools) -> None:
    app_id = at_zone(tools)
    tools.say(app_id, "I live in Texas!")
    first = tools.ok(app_id, "search_zones", query="Texas")
    second = tools.ok(app_id, "search_zones", query="Texas, USA")
    assert second["message"] == first["message"] and second["options_attached"] is True
    assert len(funnel_events(tools, app_id)) == 1


def test_the_funnel_is_offered_once_per_application(tools) -> None:
    app_id = at_zone(tools)
    texas(tools, app_id)
    tools.say(app_id, "the united states")
    again = tools.ok(app_id, "search_zones", query="the united states")
    assert "options_attached" not in again and "message" not in again
    assert again["service_cities"] == MX_CITIES  # the only areas the agent may name
    assert len(funnel_events(tools, app_id)) == 1


def test_the_first_null_is_the_funnel_not_a_miss(tools) -> None:
    app_id = at_zone(tools)
    tools.say(app_id, "I live in Texas!")
    err = tools.err(app_id, "record_answer", field="zone", value=None)
    assert (err["error"], err["reason"]) == ("invalid_value", "zone_funnel")
    assert "attempt" not in err and "options" not in err and "Puebla" in err["message"]
    assert "exactly" in err["hint"]
    app = tools.app(app_id)
    assert app["invalid_attempts"] == 0 and app["status"] == "open"
    assert tools.events(app_id, "answer_invalid") == []
    assert len(funnel_events(tools, app_id)) == 1


def test_searching_and_then_recording_null_in_one_turn_asks_but_does_not_knock_out(tools) -> None:
    """Haiku searches, finds nothing and records null in the same turn: the Candidate has not
    answered the funnel yet."""
    app_id = at_zone(tools)
    value = texas(tools, app_id)
    err = tools.err(app_id, "record_answer", field="zone", value=None)
    assert err["reason"] == "zone_funnel" and err["message"] == value["message"]
    assert len(funnel_events(tools, app_id)) == 1
    assert tools.app(app_id)["status"] == "open"


def test_a_city_tap_offers_the_citys_zones(tools) -> None:
    app_id = at_zone(tools)
    texas(tools, app_id)
    body = tapping(tools, app_id, CDMX, "Ciudad de México")
    assert body is not None and body["ok"] is False
    assert body["reason"] == "ambiguous_zone" and "attempt" not in body
    assert len(body["options"]) == 9
    assert all(o["id"].startswith("zone_mx_cdmx_") for o in body["options"])
    pending = pending_choice(tools.conn, app_id)
    assert pending == {"style": "list", "options": body["options"]}
    assert tools.app(app_id)["invalid_attempts"] == 0
    # The tapped Zone is recorded like any list tap (ADR 0043).
    done = tapping(tools, app_id, "zone_mx_cdmx_polanco", "Polanco")
    assert done is not None and done["value"]["next_field"] == "driver_license"
    assert tools.app(app_id)["status"] == "open"


def test_a_small_city_gets_buttons(tools) -> None:
    app_id = at_zone(tools)
    texas(tools, app_id)
    body = tapping(tools, app_id, "city_mx_puebla", "Puebla")
    assert body is not None and body["ok"] is True
    assert body["value"]["status"] == "needs_choice" and len(body["value"]["options"]) == 3
    pending = pending_choice(tools.conn, app_id)
    assert pending is not None and pending["style"] == "buttons"


def test_every_listed_city_leads_to_its_zones(tools) -> None:
    for country, cities in (("MX", MX_CITIES), ("ES", ES_CITIES)):
        for city in cities:
            app_id = at_zone(tools, country)
            tools.say(app_id, "Lima")
            tools.ok(app_id, "search_zones", query="Lima")
            option = next(
                o["id"] for o in pending_choice(tools.conn, app_id)["options"] if o["label"] == city
            )
            body = tap(tools, app_id, option)
            assert body is not None
            offered = (
                body["value"]["options"] if body["ok"] else body["options"]
            )  # buttons or a list
            assert offered and all(o["id"].startswith("zone_") for o in offered), city


def test_none_of_these_is_the_zone_knockout_with_the_template(tools) -> None:
    app_id = at_zone(tools)
    texas(tools, app_id)
    body = tapping(tools, app_id, NONE, "Ninguna de estas")
    assert body is not None and body["ok"] is True
    value = body["value"]
    assert (value["status"], value["criterion"], value["outcome"]) == (
        "knockout",
        "zone",
        "knockout",
    )
    assert value["template"] == "knockout_zone.es-MX"
    assert REQUIREMENTS_URL in value["message"] and "revisión" in value["message"]
    assert "Luis" in value["message"] and "tu zona" in value["message"]
    app = tools.app(app_id)
    assert (app["status"], app["outcome"], app["knockout_criterion"]) == (
        "closed",
        "knockout",
        "zone",
    )
    assert app["zone_id"] is None
    assert app["zone_input"] == "I live in Texas!"  # the Candidate's words, not the label
    (knockout,) = tools.events(app_id, "knockout")
    assert knockout["reason"] == "outside_service_areas" and knockout["criterion"] == "zone"
    assert set(knockout) == {"reason", "template", "criterion", "stage"}
    assert tools.events(app_id, "answer_recorded")[-1]["field"] == "full_name"
    assert pending_choice(tools.conn, app_id) is None


def test_the_knockout_template_is_in_the_applications_language(tools) -> None:
    app_id = tools.new_app(country="MX", language="en")
    tools.say(app_id, "Luis Hernández")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    texas(tools, app_id)
    value = tapping(tools, app_id, NONE, "None of these")["value"]
    assert value["template"] == "knockout_zone.en" and 'reply "review"' in value["message"]


def test_a_second_place_that_matches_nothing_is_the_knockout(tools) -> None:
    app_id = at_zone(tools)
    texas(tools, app_id)
    tools.say(app_id, "I live in the united states")
    again = tools.ok(app_id, "search_zones", query="united states")
    assert again["zones"] == []
    value = tools.ok(app_id, "record_answer", field="zone", value=None)
    assert value["status"] == "knockout" and value["template"] == "knockout_zone.es-MX"
    app = tools.app(app_id)
    assert app["status"] == "closed" and app["zone_id"] is None
    assert app["zone_input"] == "I live in the united states"
    assert len(tools.events(app_id, "knockout")) == 1


def test_a_known_city_after_the_funnel_never_knocks_out(tools) -> None:
    app_id = at_zone(tools)
    texas(tools, app_id)
    tools.say(app_id, "ok, Puebla")
    found = tools.ok(app_id, "search_zones", query="Puebla")
    assert found["options_attached"] is True and "message" not in found
    ids = [z["zone_id"] for z in found["zones"]]
    assert tools.ok(app_id, "record_answer", field="zone", value=ids)["status"] == "needs_choice"
    assert tools.app(app_id)["status"] == "open"


def test_a_typo_or_a_known_place_never_offers_the_funnel(tools) -> None:
    app_id = at_zone(tools)
    for words, query in (
        ("Guadalajra", "Guadalajara"),
        ("en Polanco", "Polanco, Ciudad de México"),
        ("Tolucca", "Toluca"),
    ):
        tools.say(app_id, words)
        value = tools.ok(app_id, "search_zones", query=query)
        assert value["zones"] and "message" not in value, words
    assert funnel_events(tools, app_id) == []


def test_a_null_after_the_funnel_was_answered_with_a_city_is_an_ordinary_miss(tools) -> None:
    app_id = at_zone(tools)
    texas(tools, app_id)
    tapping(tools, app_id, CDMX, "Ciudad de México")
    tools.say(app_id, "no sé")
    miss = tools.err(app_id, "record_answer", field="zone", value=None)
    assert (miss["reason"], miss["attempt"]) == ("unresolved", 1)
    assert tools.app(app_id)["status"] == "open"
    assert len(funnel_events(tools, app_id)) == 1


def test_a_closed_zone_knockout_reopens_on_a_valid_new_zone(tools) -> None:
    app_id = at_zone(tools)
    texas(tools, app_id)
    tapping(tools, app_id, NONE, "None of these")
    tools.say(app_id, "Me mudé a Polanco")
    value = tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_cdmx_polanco"])
    assert value["status"] == "accepted" and value["next_field"] == "driver_license"
    app = tools.app(app_id)
    assert app["status"] == "open" and app["zone_id"] == "zone_mx_cdmx_polanco"
    assert len(tools.events(app_id, "reopened")) == 1


def test_the_funnel_waits_until_the_zone_is_the_question(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.say(app_id, "Hola, vivo en Texas")
    value = tools.ok(app_id, "search_zones", query="Texas")
    assert "options_attached" not in value and value["service_cities"] == MX_CITIES
    assert funnel_events(tools, app_id) == []
    # The name comes first; the Zone is then asked and a null is the funnel.
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    err = tools.err(app_id, "record_answer", field="zone", value=None)
    assert err["reason"] == "zone_funnel"


def test_a_correction_at_the_confirm_step_can_use_the_funnel(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.answer_all(app_id, "MX")
    assert tools.app(app_id)["stage"] == "confirm"
    tools.say(app_id, "En realidad vivo en Texas")
    value = tools.ok(app_id, "search_zones", query="Texas")
    assert value["options_attached"] is True
    done = tapping(tools, app_id, NONE, "None of these")
    assert done is not None and done["value"]["status"] == "knockout"
    assert tools.app(app_id)["status"] == "closed"


def test_a_voice_application_gets_no_funnel(tools) -> None:
    app_id = at_zone(tools)
    tools.conn.execute("UPDATE applications SET channel = 'voice' WHERE id = ?", (app_id,))
    tools.say(app_id, "I live in Texas!")
    value = tools.ok(app_id, "search_zones", query="Texas")
    assert "options_attached" not in value and value["service_cities"] == MX_CITIES
    miss = tools.err(app_id, "record_answer", field="zone", value=None)
    assert (miss["reason"], miss["attempt"]) == ("unresolved", 1)
    assert funnel_events(tools, app_id) == []


def test_a_spoken_turn_gets_no_funnel(tools) -> None:
    app_id = at_zone(tools)
    app = tools.app(app_id)
    repo.add_message(
        tools.conn,
        candidate_id=app["candidate_id"],
        application_id=app_id,
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body="I live in Texas",
        modality="spoken",
        now=tools.clock.now(),
    )
    value = tools.ok(app_id, "search_zones", query="Texas")
    assert "options_attached" not in value and funnel_events(tools, app_id) == []


def test_a_zone_confirmation_is_never_replaced_by_the_funnel(tools) -> None:
    app_id = at_zone(tools)
    tools.say(app_id, "por la salida a Toluca")
    tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_toluca_toluca"])
    value = tools.ok(app_id, "search_zones", query="Atlantis")
    assert "options_attached" not in value and funnel_events(tools, app_id) == []


def test_a_search_with_a_country_filter_does_not_offer_the_funnel(tools) -> None:
    """A search of another country than the Application's, on its own, offers nothing: the agent
    may be exploring (ADR 0076). The own country's filter is in test_zone_country_filter.py."""
    app_id = at_zone(tools)
    tools.say(app_id, "Madrid")
    value = tools.ok(app_id, "search_zones", query="Atlantis", country="ES")
    assert "options_attached" not in value and funnel_events(tools, app_id) == []


def test_a_tap_on_a_stale_funnel_option_is_left_to_the_agent(tools) -> None:
    app_id = at_zone(tools)
    texas(tools, app_id)
    tools.say(app_id, "Polanco")
    tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_cdmx_polanco"])
    assert tap_answer(tools.conn, app_id, CDMX, now=tools.clock.now()) is None
    assert tap_answer(tools.conn, app_id, NONE, now=tools.clock.now()) is None
    assert tools.app(app_id)["status"] == "open"


def test_get_application_does_not_list_the_cities(tools) -> None:
    """With the list in the record, real Haiku judged "Texas" by itself and never searched, so the
    cities reach the agent only in the search result (ADR 0068)."""
    for country in ("MX", "ES"):
        app_id = tools.new_app(country=country)
        tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
        value = tools.ok(app_id, "get_application")
        assert value["next_field"] == "zone" and "service_cities" not in value


def test_the_cities_come_from_the_service_areas(tools, conn) -> None:
    """A city is listed when one of its Zones is covered: Toluca (uncovered) never is, and a new
    city appears the moment a Service Area covers one of its Zones."""
    assert funnel.service_cities(conn, "MX") == MX_CITIES
    conn.execute(
        "INSERT INTO zones (id, country, city, name, timezone, aliases)"
        " VALUES ('zone_mx_oaxaca_centro', 'MX', 'Oaxaca', 'Centro', 'America/Mexico_City', '[]')"
    )
    assert funnel.service_cities(conn, "MX") == MX_CITIES  # no Service Area covers it
    area = conn.execute("SELECT id FROM service_areas LIMIT 1").fetchone()["id"]
    conn.execute(
        "INSERT INTO service_area_zones (service_area_id, zone_id)"
        " VALUES (?, 'zone_mx_oaxaca_centro')",
        (area,),
    )
    assert funnel.service_cities(conn, "MX") == [
        "Ciudad de México",
        "Guadalajara",
        "Monterrey",
        "Oaxaca",
        "Puebla",
    ]
    assert funnel.service_cities(conn, None) == []


def test_more_cities_than_a_list_holds_offer_no_funnel(tools, conn) -> None:
    area = conn.execute("SELECT id FROM service_areas LIMIT 1").fetchone()["id"]
    for i in range(funnel.MAX_CITIES - len(MX_CITIES) + 1):
        zone = f"zone_mx_extra{i}_centro"
        conn.execute(
            "INSERT INTO zones (id, country, city, name, timezone, aliases)"
            " VALUES (?, 'MX', ?, 'Centro', 'America/Mexico_City', '[]')",
            (zone, f"Extra{i}"),
        )
        conn.execute(
            "INSERT INTO service_area_zones (service_area_id, zone_id) VALUES (?, ?)", (area, zone)
        )
    app_id = at_zone(tools)
    tools.say(app_id, "Texas")
    value = tools.ok(app_id, "search_zones", query="Texas")
    assert "options_attached" not in value and len(value["service_cities"]) == 10
    assert funnel_events(tools, app_id) == []


def test_a_city_with_more_than_ten_zones_asks_for_the_neighbourhood(tools, conn) -> None:
    for i in range(8):  # Puebla has three Zones; eleven in all is more than a call carries
        conn.execute(
            "INSERT INTO zones (id, country, city, name, timezone, aliases)"
            " VALUES (?, 'MX', 'Puebla', ?, 'America/Mexico_City', '[]')",
            (f"zone_mx_puebla_extra{i}", f"Barrio {i}"),
        )
    app_id = at_zone(tools)
    texas(tools, app_id)
    body = tapping(tools, app_id, "city_mx_puebla", "Puebla")
    assert body is not None and body["ok"] is False and "options" not in body
    assert "City recognised (Puebla)" in body["hint"]
    assert "ask which colonia" in body["hint"]  # the es-MX Application's word
    assert tools.app(app_id)["invalid_attempts"] == 0


def test_the_events_hold_codes_and_ids_only(tools) -> None:
    app_id = at_zone(tools)
    texas(tools, app_id)
    tapping(tools, app_id, NONE, "None of these")
    rows = tools.conn.execute(
        "SELECT data FROM events WHERE application_id = ?", (app_id,)
    ).fetchall()
    blob = " ".join(json.dumps(json.loads(r["data"]), ensure_ascii=False) for r in rows)
    for words in ("Texas", PRIVACY_URL):  # the Candidate's words stay in their messages
        assert words not in blob


def test_the_zone_note_is_for_a_typed_message_read_while_the_zone_is_the_question(tools) -> None:
    """Real Haiku judged "Texas" and "the united states" itself and never searched (ADR 0068)."""
    app_id = tools.new_app(country="MX")
    assert zone_note(tools.conn, tools.app(app_id)) is None  # the name comes first
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    tools.say(app_id, "I live in Texas!")
    assert zone_note(tools.conn, tools.app(app_id)) == ZONE_NOTE
    tools.ok(app_id, "search_zones", query="Texas")  # the cities question is asked now
    assert zone_note(tools.conn, tools.app(app_id)) == ZONE_NOTE  # still the same message
    tools.say(app_id, "I live in the united states")
    assert zone_note(tools.conn, tools.app(app_id)) == ZONE_FUNNEL_NOTE
    tools.say(app_id, "Puebla")
    tools.ok(app_id, "search_zones", query="Puebla")  # a city's list replaces the funnel
    assert zone_note(tools.conn, tools.app(app_id)) == ZONE_NOTE
    tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_puebla_centro"])
    assert zone_note(tools.conn, tools.app(app_id)) is None  # the Zone is answered


def test_the_zone_note_waits_for_an_open_agent_application(tools) -> None:
    app_id = at_zone(tools)
    tools.conn.execute("UPDATE applications SET channel = 'voice' WHERE id = ?", (app_id,))
    assert zone_note(tools.conn, tools.app(app_id)) is None
    tools.conn.execute("UPDATE applications SET channel = 'webchat' WHERE id = ?", (app_id,))
    assert zone_note(tools.conn, tools.app(app_id)) == ZONE_NOTE
    tools.conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'user_rec' WHERE id = ?",
        (app_id,),
    )
    assert zone_note(tools.conn, tools.app(app_id)) is None
