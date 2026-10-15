"""get_application and record_answer: stages, canonical answers, ambiguity, Knockouts,
misses and the 3-miss Escalation (spec 002 task 8, Phase 1 detailed spec sections 3-5)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from criba.db import repo

from .conftest import GOOD_ANSWERS


def test_get_application_at_greeting(tools) -> None:
    app_id = tools.new_app(country="MX")
    value = tools.ok(app_id, "get_application")
    assert value["stage"] == "greeting"
    assert value["status"] == "open"
    assert value["control"] == "agent"
    assert value["language"] == "es-MX"
    assert value["country"] == "MX"
    assert value["first_name"] is None
    assert value["next_field"] == "full_name"
    assert value["approved"] is False
    assert value["interview"] is None
    assert value["reopened"] is False
    # Nothing is answered yet: get_application sends no answers while a field is still to
    # ask (turn-size.md), and the stored ones are empty.
    assert "answers" not in value
    value = {**value, "answers": tools.answers(app_id)}
    assert set(value["answers"]) == set()
    assert all(v is None for v in value["answers"].values())


@pytest.mark.parametrize("country", ["ES", "MX"])
def test_full_screening_reaches_confirm_with_canonical_answers(tools, country: str) -> None:
    app_id = tools.new_app(country=country)
    last = tools.answer_all(app_id, country, say=True)
    assert last["next_field"] is None
    assert last["stage"] == "confirm"
    answers = last["answers"]
    if country == "ES":
        assert answers["full_name"] == "Ana García"
        assert answers["zone"] == {"name": "Chamberí", "city": "Madrid", "country": "ES"}
        assert answers["availability"] == ["full_time"]
        assert answers["preferred_schedule"] == ["morning"]
        assert answers["experience_years"] == 2
        assert answers["experience_platforms"] == ["glovo"]
        assert answers["start_date"] == "2026-10-12"  # next Monday, Madrid time
    else:
        assert answers["zone"] == {"name": "Polanco", "city": "Ciudad de México", "country": "MX"}
        assert answers["experience_years"] == 0.5
        assert answers["start_date"] == "2026-10-10"  # tomorrow in Mexico City (04:00 today)
    assert answers["driver_license"] is True
    app = tools.app(app_id)
    assert app["stage"] == "confirm"
    assert app["zone_input"] and app["driver_license_input"]
    assert repo.application_location_ids(tools.conn, app_id)  # covering Locations recorded
    value = tools.ok(app_id, "get_application")
    assert value["first_name"] == ("Ana" if country == "ES" else "Luis")
    assert value["next_field"] is None


def test_each_accepted_answer_moves_the_stage_to_the_next_field(tools) -> None:
    app_id = tools.new_app()
    value = tools.ok(app_id, "record_answer", field="full_name", value="Ana García")
    assert (value["next_field"], value["stage"]) == ("zone", "zone")
    assert tools.events(app_id, "stage_changed")[-1] == {"from": "greeting", "to": "zone"}


def test_recording_a_name_leaves_no_name_in_any_event(tools) -> None:
    app_id = tools.new_app()
    tools.ok(app_id, "record_answer", field="full_name", value="Ana Zyxwname")
    tools.ok(app_id, "record_answer", field="zone", value=["zone_es_madrid_chamberi"])
    app = tools.app(app_id)
    assert app["full_name"] == "Ana Zyxwname"
    assert repo.get_candidate(tools.conn, app["candidate_id"])["full_name"] == "Ana Zyxwname"
    rows = tools.conn.execute(
        "SELECT data FROM events WHERE candidate_id = ?", (app["candidate_id"],)
    ).fetchall()
    assert rows and not [r["data"] for r in rows if "Zyxw" in r["data"]]
    named, zone = tools.events(app_id, "answer_recorded")
    assert named == {"field": "full_name"}
    # Canonical values (here a Zone id) still travel in the event.
    assert zone["field"] == "zone" and zone["value"] == app["zone_id"]


def test_several_answers_at_once_skip_ahead_in_any_order(tools) -> None:
    app_id = tools.new_app()
    tools.ok(app_id, "record_answer", field="zone", value=["zone_es_madrid_chamberi"])
    value = tools.ok(app_id, "record_answer", field="full_name", value="Ana García")
    assert value["next_field"] == "driver_license"


def test_zero_years_skips_platforms(tools) -> None:
    app_id = tools.new_app()
    value = tools.ok(app_id, "record_answer", field="experience_years", value=0)
    assert value["flags"] == ["experience_platforms_skipped"]
    for field, answer in [
        ("full_name", "Ana García"),
        ("zone", ["zone_es_madrid_chamberi"]),
        ("driver_license", "yes"),
        ("availability", ["weekends"]),
        ("preferred_schedule", ["flexible"]),
    ]:
        value = tools.ok(app_id, "record_answer", field=field, value=answer)
    assert value["next_field"] == "start_date"


def test_far_start_date_is_accepted_and_flagged(tools) -> None:
    app_id = tools.new_app()
    value = tools.ok(app_id, "record_answer", field="start_date", value="2027-03-15")
    assert value["flags"] == ["start_date_far"]
    assert tools.app(app_id)["start_date_far"] is True


def test_correction_at_confirm_updates_the_field(tools) -> None:
    app_id = tools.new_app()
    tools.answer_all(app_id)
    value = tools.ok(app_id, "record_answer", field="zone", value=["zone_es_madrid_salamanca"])
    assert value["answers"]["zone"]["name"] == "Salamanca"
    assert value["stage"] == "confirm"


def test_tapped_option_ids_are_accepted(tools) -> None:
    app_id = tools.new_app(language="en")
    tools.ok(app_id, "record_answer", field="driver_license", value="yes")
    value = {"answers": tools.answers(app_id)}  # sent by get_application only when complete
    assert value["answers"]["driver_license"] is True
    tools.ok(app_id, "record_answer", field="availability", value=["part_time", "weekends"])
    value = {"answers": tools.answers(app_id)}
    assert value["answers"]["availability"] == ["part_time", "weekends"]


# --- ambiguity --------------------------------------------------------------


def add_santa_fe_zones(conn) -> None:
    """Two covered Zones sharing a name. The seed's shared name ("Centro") matches four
    cities per country, which asks for the city instead of offering buttons."""
    for city, slug, area in [
        ("Ciudad de México", "cdmx", "sa_mx_cdmx_polanco"),
        ("Guadalajara", "guadalajara", "sa_mx_guadalajara_zapopan"),
    ]:
        zone_id = f"zone_mx_{slug}_santa_fe"
        conn.execute(
            "INSERT INTO zones (id, country, city, name, aliases, timezone)"
            " VALUES (?, 'MX', ?, 'Santa Fe', '[]', 'America/Mexico_City')",
            (zone_id, city),
        )
        conn.execute(
            "INSERT INTO service_area_zones (service_area_id, zone_id) VALUES (?, ?)",
            (area, zone_id),
        )


def test_ambiguous_zone_needs_choice_and_writes_the_event(tools) -> None:
    add_santa_fe_zones(tools.conn)
    app_id = tools.new_app(country="MX")
    santa_fes = ["zone_mx_guadalajara_santa_fe", "zone_mx_cdmx_santa_fe"]  # search_zones
    value = tools.ok(app_id, "record_answer", field="zone", value=santa_fes)
    assert value["status"] == "needs_choice"
    assert value["field"] == "zone"
    assert [o["id"] for o in value["options"]] == [
        "zone_mx_cdmx_santa_fe",
        "zone_mx_guadalajara_santa_fe",
    ]
    (event,) = tools.events(app_id, "answer_ambiguous")
    assert event["field"] == "zone"
    assert event["options"] == value["options"]
    # Ambiguity is not the Candidate's miss: no attempt counted, the zone is still unanswered.
    app = tools.app(app_id)
    assert app["invalid_attempts"] == 0
    assert app["zone_id"] is None
    # The tapped id resolves (the agent passes it in a list, the Zone's shape).
    tapped = value["options"][0]["id"]
    value = tools.ok(app_id, "record_answer", field="zone", value=[tapped])
    assert value["status"] == "accepted"
    assert tools.app(app_id)["zone_id"] == tapped


# --- Knockouts --------------------------------------------------------------


@pytest.mark.parametrize(
    ("country", "language", "answer", "template", "needle"),
    [
        ("ES", "es-ES", "no", "knockout_driver_license.es-ES", "carnet"),
        ("MX", "es-MX", "no", "knockout_driver_license.es-MX", "licencia de manejo"),
        ("ES", "en", "no", "knockout_driver_license.en", "driving licence"),
        ("ES", "es-ES", "expired", "knockout_driver_license_expired.es-ES", "en vigor"),
        ("MX", "es-MX", "pending", "knockout_driver_license_pending.es-MX", "ya tengas"),
    ],
)
def test_driver_license_knockout_returns_the_template_and_closes(
    tools, country, language, answer, template, needle
) -> None:
    app_id = tools.new_app(country=country, language=language)
    tools.ok(app_id, "record_answer", field="full_name", value="Ana García")
    value = tools.ok(app_id, "record_answer", field="driver_license", value=answer)
    assert value["status"] == "knockout"
    assert value["criterion"] == value["field"] == "driver_license"
    assert value["outcome"] == "knockout"
    assert value["template"] == template
    assert needle in value["message"]
    assert value["message"].startswith(("Gracias, Ana.", "Thank you, Ana."))
    app = tools.app(app_id)
    assert (app["status"], app["outcome"], app["knockout_criterion"]) == (
        "closed",
        "knockout",
        "driver_license",
    )
    assert app["driver_license"] is False
    (event,) = tools.events(app_id, "knockout")
    assert event["template"] == template
    # The text (with the first name) is never copied into the event (Q38); the channel
    # adds the sent message's id once the agent's reply goes out.
    assert set(event) == {"reason", "template", "criterion", "stage"}
    assert event["criterion"] == "driver_license"


def test_zone_knockout_names_the_zone(tools) -> None:
    app_id = tools.new_app(country="MX")
    milpa_alta = ["zone_mx_cdmx_milpa_alta"]
    assert tools.ok(app_id, "record_answer", field="zone", value=milpa_alta)["confirm"]
    tools.say(app_id, "Sí")
    value = tools.ok(app_id, "record_answer", field="zone", value=milpa_alta)
    assert value["status"] == "knockout"
    assert value["criterion"] == "zone"
    assert value["template"] == "knockout_zone.es-MX"
    assert "Milpa Alta" in value["message"]
    app = tools.app(app_id)
    assert app["outcome"] == "knockout"
    assert app["zone_id"] == "zone_mx_cdmx_milpa_alta"
    assert repo.application_location_ids(tools.conn, app_id) == []


def test_correction_to_no_licence_at_confirm_is_a_knockout(tools) -> None:
    app_id = tools.new_app()
    tools.answer_all(app_id)
    tools.say(app_id, "perdón, en realidad no tengo carnet")
    value = tools.ok(app_id, "record_answer", field="driver_license", value="no")
    assert value["status"] == "knockout"
    assert tools.app(app_id)["driver_license_input"] == "perdón, en realidad no tengo carnet"


def test_closed_application_refuses_answers(tools) -> None:
    app_id = tools.new_app()
    tools.ok(app_id, "record_answer", field="driver_license", value="no")
    err = tools.err(app_id, "record_answer", field="full_name", value="Ana García")
    assert err["error"] == "application_closed"
    assert "revisión" in err["hint"]
    value = tools.ok(app_id, "get_application")
    assert (value["status"], value["outcome"], value["knockout_criterion"]) == (
        "closed",
        "knockout",
        "driver_license",
    )
    assert value["next_field"] is None


# --- misses -----------------------------------------------------------------


def test_misses_count_offer_options_and_escalate_on_the_third(tools) -> None:
    app_id = tools.new_app(country="MX")
    first = tools.err(app_id, "record_answer", field="availability", value=None)
    assert first["error"] == "invalid_value"
    assert first["attempt"] == 1
    assert "options" not in first
    second = tools.err(app_id, "record_answer", field="availability", value=None)
    assert second["attempt"] == 2
    assert [o["id"] for o in second["options"]] == ["full_time", "part_time", "weekends"]
    assert second["options"][0]["label"] == "Tiempo completo"
    assert repo.get_application(tools.conn, app_id)["invalid_attempts"] == 2
    assert not tools.events(app_id, "escalation_raised")
    third = tools.err(app_id, "record_answer", field="availability", value=None)
    assert third["attempt"] == 3
    assert "Escalation" in third["hint"]
    (escalation,) = tools.events(app_id, "escalation_raised")
    assert escalation["reason"] == "repeated_invalid_answers"
    fourth = tools.err(app_id, "record_answer", field="availability", value=None)
    assert fourth["attempt"] == 4
    assert len(tools.events(app_id, "escalation_raised")) == 1  # still one open
    # An accepted answer resets the counter.
    tools.ok(app_id, "record_answer", field="availability", value=["part_time"])
    assert tools.app(app_id)["invalid_attempts"] == 0


def city_zones(tools, query: str) -> list[str]:
    """What the agent passes after searching a place: every Zone search_zones found."""
    app_id = tools.new_app(country="MX")
    found = tools.ok(app_id, "search_zones", query=query)["zones"]
    return [z["zone_id"] for z in found]


CITY_ANSWERS = ["Ciudad de México", "Ciudad de Mexico, Cuajimalpa", "ciudad de mexico"]


def test_a_known_city_gets_its_zones_as_a_list_and_counts_no_miss(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    found = city_zones(tools, CITY_ANSWERS[0])
    err = tools.err(app_id, "record_answer", field="zone", value=found)
    assert err["error"] == "invalid_value"
    assert "attempt" not in err  # a narrowing step is not a miss (Q45)
    assert len(err["options"]) == 9
    assert all(o["id"].startswith("zone_mx_cdmx_") for o in err["options"])
    assert all(len(o["label"]) <= 20 for o in err["options"])
    assert "City recognised (Ciudad de México)" in err["hint"]
    app = tools.app(app_id)
    assert app["invalid_attempts"] == 0 and app["zone_id"] is None
    assert not tools.events(app_id, "answer_invalid")
    (event,) = tools.events(app_id, "answer_ambiguous")
    assert event["field"] == "zone" and event["options"] == err["options"]
    assert set(event) <= {"field", "options", "message_id"}  # never the Candidate's words
    # The tapped id is accepted.
    tapped = next(o["id"] for o in err["options"] if o["id"].endswith("polanco"))
    value = tools.ok(app_id, "record_answer", field="zone", value=[tapped])
    assert value["status"] == "accepted" and value["next_field"] == "driver_license"
    assert tools.app(app_id)["zone_id"] == tapped


def test_city_only_answers_never_escalate_but_real_misses_still_count(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    for answer in [*CITY_ANSWERS, *CITY_ANSWERS]:
        found = city_zones(tools, answer)
        err = tools.err(app_id, "record_answer", field="zone", value=found)
        assert len(err["options"]) == 9 and "attempt" not in err
    assert tools.app(app_id)["invalid_attempts"] == 0
    assert not tools.events(app_id, "escalation_raised")
    # An unknown place in an unknown city: search_zones finds nothing and the first null is the
    # Zone funnel (ADR 0068), asked once and counting no miss.
    assert city_zones(tools, "Colonia Nueva, Atlantis") == []
    asked = tools.err(app_id, "record_answer", field="zone", value=None)
    assert asked["reason"] == "zone_funnel" and "attempt" not in asked
    assert tools.app(app_id)["invalid_attempts"] == 0
    # A city's list replaces the funnel; a null after it is an ordinary miss, as before.
    tools.err(app_id, "record_answer", field="zone", value=city_zones(tools, CITY_ANSWERS[0]))
    miss = tools.err(app_id, "record_answer", field="zone", value=None)
    assert miss["attempt"] == 1 and "options" not in miss
    # A narrowing step in between does not add to it.
    tools.err(app_id, "record_answer", field="zone", value=city_zones(tools, CITY_ANSWERS[0]))
    assert tools.app(app_id)["invalid_attempts"] == 1


def test_invalid_name_hint_has_no_options(tools) -> None:
    app_id = tools.new_app()
    err = tools.err(app_id, "record_answer", field="full_name", value="Ana")
    assert err["error"] == "invalid_value"
    assert "surname" in err["hint"]
    assert tools.events(app_id, "answer_invalid")[0]["field"] == "full_name"


# --- stage and control guards ----------------------------------------------


def test_answers_after_qualified_are_wrong_stage(tools) -> None:
    app_id = tools.new_app()
    tools.answer_all(app_id)
    tools.ok(app_id, "finish_screening")
    err = tools.err(app_id, "record_answer", field="full_name", value="Ana López")
    assert err["error"] == "wrong_stage"


def test_takeover_blocks_every_tool_but_get_application(tools) -> None:
    app_id = tools.new_app()
    tools.conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'user_rec' WHERE id = ?",
        (app_id,),
    )
    assert tools.ok(app_id, "get_application")["control"] == "human"
    for tool, args in [
        ("record_answer", {"field": "full_name", "value": "Ana García"}),
        ("finish_screening", {}),
        ("raise_escalation", {"reason": "wants_human"}),
        ("record_backout", {"kind": "withdrawn"}),
        ("offer_interview_slots", {}),
        ("book_interview", {"slot_ref": "so_x"}),
        ("request_review", {}),
    ]:
        assert tools.err(app_id, tool, **args)["error"] == "human_in_control", tool


# --- structured values (ADR 0042, spec 006a tasks 7-8) ------------------------------

TOLUCA = "zone_mx_toluca_toluca"  # outside every Service Area
POLANCO = "zone_mx_cdmx_polanco"
CDMX_ZONES = [
    "zone_mx_cdmx_centro",
    "zone_mx_cdmx_condesa",
    "zone_mx_cdmx_coyoacan",
    "zone_mx_cdmx_del_valle",
    "zone_mx_cdmx_iztapalapa",
    "zone_mx_cdmx_milpa_alta",
    "zone_mx_cdmx_narvarte",
    "zone_mx_cdmx_polanco",
    "zone_mx_cdmx_roma",
]
CENTROS = [
    "zone_mx_cdmx_centro",
    "zone_mx_guadalajara_centro",
    "zone_mx_monterrey_centro",
    "zone_mx_puebla_centro",
]
STRUCTURED_ES: list[tuple[str, object]] = [
    ("full_name", "Ana García"),
    ("zone", ["zone_es_madrid_chamberi"]),
    ("driver_license", "yes"),
    ("availability", ["weekends", "full_time"]),
    ("preferred_schedule", ["morning"]),
    ("experience_years", 1.5),
    ("experience_platforms", ["other", "glovo"]),
    ("start_date", "2026-10-12"),
]


def named(tools, country: str = "MX") -> str:
    app_id = tools.new_app(country=country)
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    return app_id


def misses(tools, app_id: str) -> int:
    return tools.app(app_id)["invalid_attempts"]


def test_structured_values_reach_confirm_with_canonical_answers(tools) -> None:
    app_id = tools.new_app(country="ES")
    for field, value in STRUCTURED_ES:
        last = tools.ok(app_id, "record_answer", field=field, value=value)
        assert last["status"] == "accepted", (field, last)
    assert last["stage"] == "confirm" and last["next_field"] is None
    assert last["answers"] == {
        "full_name": "Ana García",
        "zone": {"name": "Chamberí", "city": "Madrid", "country": "ES"},
        "driver_license": True,
        "availability": ["full_time", "weekends"],
        "preferred_schedule": ["morning"],
        "experience_years": 1.5,
        "experience_platforms": ["glovo", "other"],
        "start_date": "2026-10-12",
    }
    recorded = tools.events(app_id, "answer_recorded")
    assert [e.get("value") for e in recorded][1:] == [
        "zone_es_madrid_chamberi",
        True,
        ["full_time", "weekends"],
        ["morning"],
        1.5,
        ["glovo", "other"],
        "2026-10-12",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("zone", 5),
        ("zone", {"ids": [POLANCO]}),
        ("driver_license", True),
        ("driver_license", {"ok": True}),
        ("availability", [1]),
        ("availability", []),
        ("experience_years", -1),
        ("experience_years", [2]),
        ("start_date", 20261012),
        ("start_date", ["2026-10-11"]),  # two readings or one date, never a list of one
        ("start_date", ["2026-10-11", "2026-10-18", "2026-10-25"]),
        ("start_date", ["2026-10-11", "2026-10-11"]),
        ("start_date", ["2026-10-11", "el domingo"]),
        ("full_name", ["Ana", 5]),
    ],
)
def test_a_wrong_shape_for_the_field_is_invalid_args_and_counts_nothing(tools, field, value):
    app_id = named(tools)
    status, body = tools.raw(app_id, "record_answer", field=field, value=value)
    assert status == 422 and body["error"] == "invalid_args"
    assert misses(tools, app_id) == 0
    assert tools.events(app_id, "answer_invalid") == []


def test_the_shape_hint_names_the_fields_shape(tools) -> None:
    status, body = tools.raw(named(tools), "record_answer", field="zone", value=5)
    assert status == 422
    assert "value for zone must be a list of 1 to 10 Zone ids from search_zones" in body["hint"]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("full_name", "Ana", "invalid_name"),
        ("experience_years", 61, "out_of_range"),
        ("start_date", "2026-10-08", "past_start_date"),
        ("zone", CENTROS, "ambiguous_zone"),
    ],
)
def test_a_value_that_fails_a_rule_is_a_miss_with_its_reason(tools, field, value, reason):
    app_id = tools.new_app(country="MX")
    err = tools.err(app_id, "record_answer", field=field, value=value)
    assert (err["error"], err["reason"], err["attempt"]) == ("invalid_value", reason, 1)
    assert misses(tools, app_id) == 1
    [event] = tools.events(app_id, "answer_invalid")
    assert event["reason"] == reason and event["field"] == field


def test_null_is_an_unresolved_miss_and_options_come_from_the_second(tools) -> None:
    app_id = named(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[POLANCO])
    first = tools.err(app_id, "record_answer", field="driver_license", value=None)
    assert (first["reason"], first["attempt"]) == ("unresolved", 1)
    assert "options" not in first and "driving licence" in first["hint"]
    second = tools.err(app_id, "record_answer", field="driver_license", value=None)
    assert second["attempt"] == 2
    assert [o["id"] for o in second["options"]] == ["yes", "no"]
    third = tools.err(app_id, "record_answer", field="driver_license", value=None)
    assert third["attempt"] == 3 and "Escalation" in third["hint"]
    [item] = tools.conn.execute(
        "SELECT reason FROM queue_items WHERE application_id = ? AND kind = 'escalation'",
        (app_id,),
    ).fetchall()
    assert item["reason"] == "repeated_invalid_answers"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("zone", ["zone_mx_atlantis"], "unknown_zone_id"),
        ("start_date", "2026-02-30", "invalid_date"),
    ],
)
def test_the_agents_errors_count_no_miss(tools, field, value, reason) -> None:
    app_id = named(tools)
    err = tools.err(app_id, "record_answer", field=field, value=value)
    assert (err["error"], err["reason"]) == ("invalid_value", reason)
    assert "attempt" not in err
    assert misses(tools, app_id) == 0
    assert tools.events(app_id, "answer_invalid") == []


def test_unknown_zone_id_hint_points_at_search_zones(tools) -> None:
    err = tools.err(named(tools), "record_answer", field="zone", value=["zone_mx_atlantis"])
    assert "search_zones" in err["hint"]


def test_one_citys_zones_are_a_list_without_a_miss(tools) -> None:
    app_id = named(tools)
    err = tools.err(app_id, "record_answer", field="zone", value=CDMX_ZONES)
    assert (err["error"], err["reason"]) == ("invalid_value", "ambiguous_zone")
    assert "attempt" not in err and len(err["options"]) == 9
    assert misses(tools, app_id) == 0
    [event] = tools.events(app_id, "answer_ambiguous")
    assert sorted(o["id"] for o in event["options"]) == CDMX_ZONES
    # Tapping a covered Zone from the list records it.
    value = tools.ok(app_id, "record_answer", field="zone", value=[POLANCO])
    assert value["status"] == "accepted"


def test_ids_across_cities_are_an_ambiguous_zone_miss(tools) -> None:
    app_id = named(tools)
    err = tools.err(app_id, "record_answer", field="zone", value=CENTROS)
    assert (err["reason"], err["attempt"]) == ("ambiguous_zone", 1)
    assert tools.events(app_id, "answer_ambiguous") == []


def test_two_or_three_ids_are_a_choice(tools) -> None:
    app_id = named(tools)
    value = tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA, POLANCO])
    assert value["status"] == "needs_choice" and "confirm" not in value
    assert [o["id"] for o in value["options"]] == [POLANCO, TOLUCA]  # covered first
    assert misses(tools, app_id) == 0


@pytest.mark.parametrize("language", ["es-ES", "es-MX", "en"])
@pytest.mark.parametrize("code", ["no", "expired", "suspended", "pending"])
def test_each_licence_code_is_a_knockout_with_its_template(tools, code, language) -> None:
    country = "ES" if language == "es-ES" else "MX"
    app_id = tools.new_app(country=country, language=language)
    tools.ok(app_id, "record_answer", field="full_name", value="Ana García")
    zone = "zone_es_madrid_chamberi" if country == "ES" else POLANCO
    tools.ok(app_id, "record_answer", field="zone", value=[zone])
    value = tools.ok(app_id, "record_answer", field="driver_license", value=code)
    variant = "knockout_driver_license" + ("" if code == "no" else f"_{code}")
    assert value["status"] == "knockout"
    assert value["template"] == f"{variant}.{language}"
    app = tools.app(app_id)
    assert (app["status"], app["outcome"], app["driver_license"]) == ("closed", "knockout", 0)
    [knockout] = tools.events(app_id, "knockout")
    assert knockout["reason"] == code


# --- Zone confirmation before a Knockout (spec 006, decision 3) ---------------------


def test_one_uncovered_zone_found_by_the_agent_is_confirmed_not_knocked_out(tools) -> None:
    app_id = named(tools)
    value = tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    assert value == {
        "status": "needs_choice",
        "field": "zone",
        "options": [{"id": TOLUCA, "label": "Sí"}, {"id": "no", "label": "No"}],
        "confirm": TOLUCA,
    }
    app = tools.app(app_id)
    assert app["status"] == "open" and app["zone_id"] is None
    assert misses(tools, app_id) == 0
    [event] = tools.events(app_id, "answer_ambiguous")
    assert event["confirm"] == TOLUCA


def test_the_confirmation_is_in_the_candidates_language(tools) -> None:
    app_id = tools.new_app(country="MX", language="en")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    value = tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    assert [o["label"] for o in value["options"]] == ["Yes", "No"]


def test_tapping_the_confirmed_zone_is_the_knockout(tools) -> None:
    app_id = named(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    tools.say(app_id, "Sí")
    value = tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    assert value["status"] == "knockout" and value["criterion"] == "zone"
    assert value["template"] == "knockout_zone.es-MX" and "Toluca" in value["message"]
    app = tools.app(app_id)
    assert (app["status"], app["zone_id"], app["zone_input"]) == ("closed", TOLUCA, "Sí")


def test_no_to_the_confirmation_in_spain_asks_for_the_barrio(tools) -> None:
    """The Zone question is asked with the Application's own word: Spain says barrio."""
    tools.conn.execute(
        "INSERT INTO zones (id, country, city, name, timezone, aliases)"
        " VALUES ('zone_es_alcala_centro', 'ES', 'Alcalá', 'Centro', 'Europe/Madrid', '[]')"
    )
    app_id = named(tools, "ES")
    tools.ok(app_id, "record_answer", field="zone", value=["zone_es_alcala_centro"])
    err = tools.err(app_id, "record_answer", field="zone", value=None)
    assert err["reason"] == "zone_not_confirmed"
    assert "barrio" in err["hint"] and "colonia" not in err["hint"]


def test_no_to_the_confirmation_counts_no_miss_clears_the_buttons_and_reasks(tools) -> None:
    app_id = named(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    err = tools.err(app_id, "record_answer", field="zone", value=None)
    assert (err["error"], err["reason"]) == ("invalid_value", "zone_not_confirmed")
    assert "attempt" not in err and "options" not in err
    assert "search_zones" in err["hint"]
    assert "colonia" in err["hint"] and "barrio" not in err["hint"]  # an es-MX Application
    assert misses(tools, app_id) == 0
    assert tools.events(app_id, "answer_ambiguous")[-1]["options"] == []
    # The re-asked Zone records normally. A place that maps to nothing is the funnel's first
    # question (ADR 0068), not a miss; once a city's list has replaced the funnel, a second
    # null is an ordinary miss.
    asked = tools.err(app_id, "record_answer", field="zone", value=None)
    assert asked["reason"] == "zone_funnel" and misses(tools, app_id) == 0
    tools.err(app_id, "record_answer", field="zone", value=city_zones(tools, CITY_ANSWERS[0]))
    second = tools.err(app_id, "record_answer", field="zone", value=None)
    assert (second["reason"], second["attempt"]) == ("unresolved", 1)
    value = tools.ok(app_id, "record_answer", field="zone", value=[POLANCO])
    assert value["status"] == "accepted" and misses(tools, app_id) == 0


def test_after_no_the_same_uncovered_zone_is_confirmed_again(tools) -> None:
    app_id = named(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    tools.err(app_id, "record_answer", field="zone", value=None)
    value = tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    assert value["confirm"] == TOLUCA


def test_an_uncovered_zone_tapped_from_a_choice_is_a_knockout_directly(tools) -> None:
    app_id = named(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA, POLANCO])
    value = tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    assert value["status"] == "knockout"


def test_an_uncovered_zone_tapped_from_a_city_list_is_a_knockout_directly(tools) -> None:
    app_id = named(tools)
    tools.err(app_id, "record_answer", field="zone", value=CDMX_ZONES)
    value = tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_cdmx_milpa_alta"])
    assert value["status"] == "knockout"


def test_a_confirmation_at_the_confirm_step_does_not_loop(tools) -> None:
    app_id = named(tools)
    for field, value in [
        ("zone", [POLANCO]),
        ("driver_license", "yes"),
        ("availability", ["full_time"]),
        ("preferred_schedule", ["morning"]),
        ("experience_years", 0),
        ("start_date", "2026-10-12"),
    ]:
        tools.ok(app_id, "record_answer", field=field, value=value)
    assert tools.app(app_id)["stage"] == "confirm"
    assert tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])["confirm"] == TOLUCA
    assert tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])["status"] == "knockout"


# --- the Candidate's words are never parsed (spec 006a tasks 11-12) -----------------


@pytest.mark.parametrize(
    ("field", "words"),
    [
        ("zone", "vivo en Toluca"),
        ("zone", "zone_mx_toluca_toluca"),  # a tapped id not wrapped in its list
        ("driver_license", "sí, tengo carnet"),
        ("driver_license", "no tengo"),
        ("availability", "tiempo completo"),
        ("availability", "full_time"),  # a tapped code not wrapped in its list
        ("preferred_schedule", ["por la mañana"]),
        ("experience_years", "2 años"),
        ("experience_platforms", "glovo"),
        ("start_date", "el lunes"),
        ("start_date", "15/03/2027"),
    ],
)
def test_the_candidates_words_are_invalid_args_and_nothing_happens(tools, field, words) -> None:
    """The old parser is gone: words, or a bare tapped id for a list field, are the wrong
    shape. Nothing is stored, no miss counts, no event is written, and nothing knocks out."""
    app_id = named(tools)
    before = tools.conn.execute(
        "SELECT count(*) FROM events WHERE application_id = ?", (app_id,)
    ).fetchone()[0]
    status, body = tools.raw(app_id, "record_answer", field=field, value=words)
    assert status == 422 and body["error"] == "invalid_args"
    assert f"value for {field} must be" in body["hint"] and "no miss" in body["hint"]
    app = tools.app(app_id)
    assert (app["status"], app["stage"], app["invalid_attempts"]) == ("open", "zone", 0)
    assert app["zone_id"] is None and app["driver_license"] is None
    after = tools.conn.execute(
        "SELECT count(*) FROM events WHERE application_id = ?", (app_id,)
    ).fetchone()[0]
    assert after == before


def test_a_list_fields_shape_hint_says_list(tools) -> None:
    """A bare tapped code tells the agent to wrap it: the hint names a list of codes."""
    status, body = tools.raw(named(tools), "record_answer", field="availability", value="weekends")
    assert status == 422
    assert "a list of codes from full_time, part_time, weekends" in body["hint"]


@pytest.mark.parametrize(
    ("country", "today", "tomorrow"),
    [("ES", "2026-10-10", "2026-10-11"), ("MX", "2026-10-09", "2026-10-10")],
)
def test_the_start_date_is_checked_in_the_candidates_local_calendar(
    tools, country, today, tomorrow
) -> None:
    """23:30 UTC on Friday 9 October is already Saturday 10 in Madrid, still Friday 9 in
    Mexico City: today is accepted, yesterday is a past date, both by local time."""
    tools.clock.set(datetime(2026, 10, 9, 23, 30, tzinfo=UTC))
    app_id = tools.new_app(country=country)
    assert tools.ok(app_id, "get_application")["today"] == today
    tools.ok(app_id, "record_answer", field="start_date", value=today)
    value = {"answers": tools.answers(app_id)}
    assert value["answers"]["start_date"] == today
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    err = tools.err(app_id, "record_answer", field="start_date", value=yesterday)
    assert err["reason"] == "past_start_date"
    assert tools.ok(app_id, "record_answer", field="start_date", value=tomorrow)["flags"] == []


@pytest.mark.parametrize(("country", "today"), [("ES", "2026-10-11"), ("MX", "2026-10-11")])
def test_a_date_choice_holding_today_counts_no_miss_and_asks_nothing(tools, country, today) -> None:
    """The live run of Sunday 2026-10-11 (ADR 0058): "el próximo domingo" came as
    [2026-10-11, 2026-10-18]. It is sent back to the agent with no miss, no event and no
    buttons; the two Sundays after today are the choice."""
    tools.clock.set(datetime(2026, 10, 11, 16, 0, tzinfo=UTC))  # Sunday in Madrid and CDMX
    app_id = tools.new_app(country=country)
    assert tools.ok(app_id, "get_application")["today"] == today
    err = tools.err(app_id, "record_answer", field="start_date", value=[today, "2026-10-18"])
    assert (err["error"], err["reason"]) == ("invalid_value", "today_in_date_choice")
    assert "attempt" not in err and "options" not in err
    assert misses(tools, app_id) == 0
    assert tools.events(app_id, "answer_invalid") == []
    assert tools.events(app_id, "answer_ambiguous") == []
    choice = tools.ok(
        app_id, "record_answer", field="start_date", value=["2026-10-18", "2026-10-25"]
    )
    assert choice["status"] == "needs_choice"
    assert [o["id"] for o in choice["options"]] == ["2026-10-18", "2026-10-25"]


def test_the_start_date_follows_the_candidates_zone_time_zone(tools) -> None:
    """A Zone in Tijuana: 06:30 UTC on Saturday 10 is still Friday 9 there (23:30 PDT),
    but already Saturday in Mexico City."""
    tools.conn.execute(
        "INSERT INTO zones (id, country, city, name, aliases, timezone)"
        " VALUES ('zone_mx_test_tijuana_rio', 'MX', 'Tijuana', 'Zona Río', '[]',"
        " 'America/Tijuana')"
    )
    tools.conn.execute(
        "INSERT INTO service_area_zones (service_area_id, zone_id)"
        " VALUES ('sa_mx_cdmx_polanco', 'zone_mx_test_tijuana_rio')"
    )
    tools.clock.set(datetime(2026, 10, 10, 6, 30, tzinfo=UTC))
    app_id = named(tools)
    tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_test_tijuana_rio"])
    assert tools.ok(app_id, "get_application")["today"] == "2026-10-09"
    value = tools.ok(app_id, "record_answer", field="start_date", value="2026-10-09")
    assert value["status"] == "accepted"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("experience_years", 60),
        ("start_date", "2026-12-08"),  # 60 days out: not flagged
    ],
)
def test_the_upper_bounds_are_accepted_without_a_flag(tools, field, value) -> None:
    out = tools.ok(named(tools), "record_answer", field=field, value=value)
    assert out["status"] == "accepted" and out["flags"] == []


def test_61_days_out_is_flagged_far(tools) -> None:
    value = tools.ok(named(tools), "record_answer", field="start_date", value="2026-12-09")
    assert value["flags"] == ["start_date_far"]


@pytest.mark.parametrize(
    ("field", "language", "first"),
    [
        ("availability", "es-ES", "Jornada completa"),
        ("availability", "es-MX", "Tiempo completo"),
        ("availability", "en", "Full-time"),
        ("preferred_schedule", "en", "Morning"),
        ("experience_platforms", "es-MX", "Glovo"),
    ],
)
def test_the_second_misses_options_follow_the_language(tools, field, language, first) -> None:
    app_id = tools.new_app(country="MX" if language == "es-MX" else "ES", language=language)
    tools.err(app_id, "record_answer", field=field, value=None)
    second = tools.err(app_id, "record_answer", field=field, value=None)
    assert second["options"][0]["label"] == first


# --- result size (docs/evidence/turn-size.md) --------------------------------------------


def test_results_carry_only_what_says_something(tools) -> None:
    """Every result stays in the model's context for the rest of the session, so an accepted
    answer carries no `answers` until everything is answered (they are in the last
    get_application result), and get_application leaves out the keys that are null, false or 0."""
    app_id = tools.new_app()
    results = []
    for field, answer in GOOD_ANSWERS["ES"]:
        results.append(tools.ok(app_id, "record_answer", field=field, value=answer))
    *middle, last = results
    assert all("answers" not in r for r in middle)
    assert middle[-1]["next_field"] == "start_date"
    assert last["next_field"] is None and last["stage"] == "confirm"
    assert last["answers"]["full_name"] == "Ana García"
    assert last["answers"]["experience_years"] == 2
    assert len(last["answers"]) == 8

    # Everything is answered, so get_application sends the answers too.
    state = tools.ok(app_id, "get_application")
    assert state["answers"] == last["answers"]
    assert state["first_name"] == "Ana"
    assert not {"outcome", "interview", "approved", "reopened", "invalid_attempts"} & set(state)
    assert set(state) == {
        "stage",
        "status",
        "control",
        "language",
        "country",
        "first_name",
        "answers",
        "next_field",
        "today",
        "weekday",
    }


def test_get_application_at_greeting_sends_no_empty_keys(tools) -> None:
    value = tools.ok(tools.new_app(country="MX"), "get_application")
    assert set(value) == {
        "stage",
        "status",
        "control",
        "language",
        "country",
        "next_field",
        "today",
        "weekday",
    }


def test_a_zero_is_an_answer_and_stays_in_the_answers(tools) -> None:
    app_id = tools.new_app()
    tools.ok(app_id, "record_answer", field="experience_years", value=0)
    answers = tools.answers(app_id)
    assert answers["experience_years"] == 0
    assert set(answers) == {"experience_years"}


def test_get_application_sends_the_answers_only_when_nothing_is_left_to_ask(tools) -> None:
    app_id = tools.new_app()
    for field, answer in GOOD_ANSWERS["ES"][:-1]:
        tools.ok(app_id, "record_answer", field=field, value=answer)
        assert "answers" not in tools.ok(app_id, "get_application")
    tools.ok(app_id, "record_answer", field="start_date", value="2026-10-12")
    state = tools.ok(app_id, "get_application")
    assert state["next_field"] is None
    assert state["answers"] == tools.answers(app_id) and len(state["answers"]) == 8


def test_null_on_an_answered_field_mid_screening_is_refused_and_counts_no_miss(tools) -> None:
    app_id = named(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[POLANCO])
    before = tools.app(app_id)
    err = tools.err(app_id, "record_answer", field="zone", value=None)
    assert (err["error"], err["reason"]) == ("invalid_value", "already_answered")
    assert "attempt" not in err and "options" not in err and "message" not in err
    assert "Keep" in err["hint"] and "no miss" in err["hint"]
    after = tools.app(app_id)
    assert after["zone_id"] == POLANCO == before["zone_id"]
    assert misses(tools, app_id) == 0 and after["stage"] == before["stage"]
    assert tools.events(app_id, "answer_invalid") == []
    again = tools.err(app_id, "record_answer", field="full_name", value=None)
    assert again["reason"] == "already_answered"
    assert tools.app(app_id)["full_name"] == "Luis Hernández" and misses(tools, app_id) == 0


def test_null_on_a_never_answered_field_is_still_an_unresolved_miss(tools) -> None:
    app_id = named(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[POLANCO])
    err = tools.err(app_id, "record_answer", field="driver_license", value=None)
    assert (err["reason"], err["attempt"]) == ("unresolved", 1)
    assert misses(tools, app_id) == 1


def test_null_at_confirm_still_goes_through_the_correction_flow(tools) -> None:
    app_id = tools.new_app()
    tools.answer_all(app_id)
    err = tools.err(app_id, "record_answer", field="driver_license", value=None)
    assert (err["reason"], err["attempt"]) == ("unresolved", 1)
    assert misses(tools, app_id) == 1


def test_no_to_a_zone_confirmation_of_a_correction_is_not_already_answered(tools) -> None:
    app_id = named(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[POLANCO])
    assert tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])["status"] == (
        "needs_choice"
    )
    err = tools.err(app_id, "record_answer", field="zone", value=None)
    assert err["reason"] == "zone_not_confirmed" and misses(tools, app_id) == 0


def test_null_on_a_recorded_zone_while_a_zone_choice_is_open_is_not_refused(tools) -> None:
    app_id = named(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[POLANCO])
    tools.err(app_id, "record_answer", field="zone", value=city_zones(tools, CITY_ANSWERS[0]))
    err = tools.err(app_id, "record_answer", field="zone", value=None)
    assert (err["reason"], err["attempt"]) == ("unresolved", 1)
