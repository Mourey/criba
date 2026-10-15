"""tap_answer (ADR 0043): the backend records a tap on a pending answer option itself and
hands the agent the record_answer result, so no model has to read the option id."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from criba.db import repo
from criba.tools import pending_choice, tap_answer, verify_booking_token
from criba.tools.booking import BookingTokenExpired
from criba.tools.choices import BOOKING_LINK_STATUS, TAP_RESULT_PREFIX, booking_link_of

TOLUCA = "zone_mx_toluca_toluca"


def tap(tools, app_id: str, option_id: str) -> dict | None:
    """The agent's input for a tap, parsed; None when the tap is left to the agent."""
    text = tap_answer(tools.conn, app_id, option_id, now=tools.clock.now())
    if text is None:
        return None
    assert text.startswith(TAP_RESULT_PREFIX)
    return json.loads(text[len(TAP_RESULT_PREFIX) :])


def at_zone(tools, country: str = "MX") -> str:
    app_id = tools.new_app(country=country)
    tools.say(app_id, "Luis Hernández")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    return app_id


def test_a_tapped_zone_from_buttons_is_recorded_and_the_next_field_returned(tools) -> None:
    app_id = at_zone(tools)
    needs = tools.ok(app_id, "search_zones", query="Puebla")["zones"]
    ids = [z["zone_id"] for z in needs]
    tools.ok(app_id, "record_answer", field="zone", value=ids)  # 3 Zones: needs_choice
    centro = "zone_mx_puebla_centro"
    tools.say(app_id, "Centro")
    body = tap(tools, app_id, centro)
    assert body == {
        "ok": True,
        "value": {
            "status": "accepted",
            "field": "zone",
            "next_field": "driver_license",
            "stage": "driver_license",
            "flags": [],
        },
    }
    assert tools.app(app_id)["zone_id"] == centro
    assert pending_choice(tools.conn, app_id)["options"][0]["id"] == "yes"


def test_the_recorded_answer_event_references_the_tap_message(tools) -> None:
    app_id = at_zone(tools)
    ids = [z["zone_id"] for z in tools.ok(app_id, "search_zones", query="Puebla")["zones"]]
    tools.ok(app_id, "record_answer", field="zone", value=ids)
    message_id = tools.say(app_id, "Centro")
    tap(tools, app_id, "zone_mx_puebla_centro")
    row = tools.conn.execute(
        "SELECT data FROM events WHERE application_id = ? AND kind = 'answer_recorded'"
        " AND json_extract(data, '$.field') = 'zone'",
        (app_id,),
    ).fetchone()
    assert json.loads(row["data"])["message_id"] == message_id


def test_a_city_list_tap_is_recorded(tools) -> None:
    app_id = at_zone(tools)
    found = tools.ok(app_id, "search_zones", query="Ciudad de México, Cuajimalpa")["zones"]
    tools.err(app_id, "record_answer", field="zone", value=[z["zone_id"] for z in found])
    body = tap(tools, app_id, "zone_mx_cdmx_polanco")
    assert body is not None and body["value"]["next_field"] == "driver_license"


def test_the_zone_confirmation_si_tap_is_the_knockout_and_the_message_comes_back(tools) -> None:
    app_id = at_zone(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    body = tap(tools, app_id, TOLUCA)
    assert body is not None and body["value"]["status"] == "knockout"
    assert body["value"]["message"]  # the approved text the agent sends as is
    app = tools.app(app_id)
    assert app["status"] == "closed" and app["outcome"] == "knockout"


def test_the_zone_confirmation_no_tap_clears_the_buttons_and_counts_no_miss(tools) -> None:
    app_id = at_zone(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    body = tap(tools, app_id, "no")
    assert body is not None and body["ok"] is False
    assert body["reason"] == "zone_not_confirmed"
    assert tools.app(app_id)["invalid_attempts"] == 0
    assert pending_choice(tools.conn, app_id) is None


def test_fixed_option_taps_are_recorded_in_their_fields_shape(tools) -> None:
    app_id = tools.new_app(country="MX")
    for field, value in [("full_name", "Luis Hernández"), ("zone", ["zone_mx_cdmx_polanco"])]:
        tools.ok(app_id, "record_answer", field=field, value=value)
    tools.say(app_id, "Sí, tengo licencia")
    assert tap(tools, app_id, "yes")["value"]["next_field"] == "availability"
    tools.say(app_id, "Tiempo completo")
    assert tap(tools, app_id, "full_time")["value"]["next_field"] == "preferred_schedule"
    tools.say(app_id, "Mañana")
    assert tap(tools, app_id, "morning")["value"]["next_field"] == "experience_years"
    app = tools.app(app_id)
    assert app["driver_license"] == 1
    assert app["availability"] == ["full_time"]
    assert app["preferred_schedule"] == ["morning"]


def at_platforms(tools) -> str:
    app_id = tools.new_app(country="MX")
    for field, value in [
        ("full_name", "Luis Hernández"),
        ("zone", ["zone_mx_cdmx_polanco"]),
        ("driver_license", "yes"),
        ("availability", ["part_time"]),
        ("preferred_schedule", ["afternoon"]),
        ("experience_years", 10),
    ]:
        tools.ok(app_id, "record_answer", field=field, value=value)
    return app_id


def test_a_no_app_tap_is_recorded_as_the_empty_list(tools) -> None:
    """ADR 0053: "Ninguna app" is the Candidate's whole answer; the backend records [] (the
    contract's value for no app) and the agent moves on to the start date."""
    app_id = at_platforms(tools)
    tools.say(app_id, "Ninguna app")
    body = tap(tools, app_id, "none")
    assert body is not None and body["value"]["status"] == "accepted"
    assert body["value"]["next_field"] == "start_date"
    app = tools.app(app_id)
    assert app["experience_platforms"] == [] and app["invalid_attempts"] == 0


def test_a_platform_tap_is_recorded_in_a_list(tools) -> None:
    app_id = at_platforms(tools)
    tools.say(app_id, "Rappi")
    assert tap(tools, app_id, "rappi")["value"]["next_field"] == "start_date"
    assert tools.app(app_id)["experience_platforms"] == ["rappi"]


def at_start_date_on_saturday(tools) -> str:
    """Saturday 10 October 2026, noon in Mexico City: every answer but the start date."""
    tools.clock.set(datetime(2026, 10, 10, 18, 0, tzinfo=UTC))
    app_id = at_platforms(tools)
    tools.ok(app_id, "record_answer", field="experience_platforms", value=[])
    assert tools.ok(app_id, "get_application")["weekday"] == "saturday"
    return app_id


def test_next_sunday_on_a_saturday_asks_with_both_dates_and_the_tap_is_recorded(tools) -> None:
    """ADR 0054, the demo chat of 2026-10-10: "El próximo domingo" said on Saturday was
    recorded as tomorrow. The agent passes both readings; the backend offers them as buttons
    and records the tapped one, with no miss."""
    app_id = at_start_date_on_saturday(tools)
    tools.say(app_id, "El próximo domingo me queda bien")
    both = ["2026-10-11", "2026-10-18"]
    value = tools.ok(app_id, "record_answer", field="start_date", value=both)
    assert value["status"] == "needs_choice" and value["field"] == "start_date"
    buttons = pending_choice(tools.conn, app_id, now=tools.clock.now())
    assert buttons == {
        "style": "buttons",
        "options": [
            {"id": "2026-10-11", "label": "Domingo 11 oct"},
            {"id": "2026-10-18", "label": "Domingo 18 oct"},
        ],
    }
    assert tools.app(app_id)["start_date"] is None
    tools.say(app_id, "Domingo 18 oct")
    body = tap(tools, app_id, "2026-10-18")
    assert body is not None and body["value"]["status"] == "accepted"
    assert body["value"]["next_field"] is None and body["value"]["stage"] == "confirm"
    app = tools.app(app_id)
    assert app["start_date"] == "2026-10-18" and app["invalid_attempts"] == 0
    assert pending_choice(tools.conn, app_id, now=tools.clock.now()) is None


def test_date_buttons_displaced_by_a_zone_correction_come_back_after_the_zone_tap(tools) -> None:
    """Real Haiku, 2026-10-10: in the turn that offered the two Sundays the agent also re-asked
    the Zone with Puebla's three Zones; those buttons replaced the dates (one pending choice at
    a time), and once the Zone was tapped the dates never came back. They do now."""
    app_id = at_start_date_on_saturday(tools)
    tools.ok(app_id, "record_answer", field="start_date", value=["2026-10-11", "2026-10-18"])
    puebla = ["zone_mx_puebla_angelopolis", "zone_mx_puebla_centro", "zone_mx_puebla_la_paz"]
    assert tools.ok(app_id, "record_answer", field="zone", value=puebla)["status"] == (
        "needs_choice"
    )
    zones = pending_choice(tools.conn, app_id, now=tools.clock.now())
    assert [o["id"] for o in zones["options"]] == puebla
    tools.say(app_id, "Centro")
    assert tap(tools, app_id, "zone_mx_puebla_centro")["value"]["next_field"] == "start_date"
    dates = pending_choice(tools.conn, app_id, now=tools.clock.now())
    assert [o["id"] for o in dates["options"]] == ["2026-10-11", "2026-10-18"]
    tools.say(app_id, "Domingo 18 oct")
    assert tap(tools, app_id, "2026-10-18")["value"]["next_field"] is None
    assert tools.app(app_id)["start_date"] == "2026-10-18"


def test_a_closed_ambiguity_does_not_bring_back_an_older_one_for_the_same_field(tools) -> None:
    """Only a field's latest ambiguity counts: the Zone's No (empty options) closes it."""
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])  # Sí/No confirmation
    tools.err(app_id, "record_answer", field="zone", value=None)  # the No
    assert pending_choice(tools.conn, app_id, now=tools.clock.now()) is None


def test_a_typed_pick_after_the_date_buttons_is_recorded_and_clears_them(tools) -> None:
    app_id = at_start_date_on_saturday(tools)
    tools.ok(app_id, "record_answer", field="start_date", value=["2026-10-18", "2026-10-11"])
    value = tools.ok(app_id, "record_answer", field="start_date", value="2026-10-11")
    assert value["status"] == "accepted" and tools.app(app_id)["start_date"] == "2026-10-11"
    assert pending_choice(tools.conn, app_id, now=tools.clock.now()) is None


def test_a_licence_no_tap_is_a_knockout(tools) -> None:
    app_id = tools.new_app(country="MX")
    for field, value in [("full_name", "Luis Hernández"), ("zone", ["zone_mx_cdmx_polanco"])]:
        tools.ok(app_id, "record_answer", field=field, value=value)
    tools.say(app_id, "No")
    body = tap(tools, app_id, "no")
    assert body is not None and body["value"]["status"] == "knockout"
    assert body["value"]["criterion"] == "driver_license"


def test_what_is_not_a_pending_answer_option_is_left_to_the_agent(tools) -> None:
    app_id = at_zone(tools)  # asking for the Zone: no buttons pending
    assert tap(tools, app_id, "yes") is None
    assert tap(tools, app_id, "zone_mx_cdmx_polanco") is None
    assert tools.app(app_id)["zone_id"] is None
    assert tap_answer(tools.conn, "app_missing", "yes", now=tools.clock.now()) is None


def test_an_option_that_is_not_in_the_pending_choice_is_left_to_the_agent(tools) -> None:
    app_id = tools.new_app(country="MX")
    for field, value in [("full_name", "Luis Hernández"), ("zone", ["zone_mx_cdmx_polanco"])]:
        tools.ok(app_id, "record_answer", field=field, value=value)
    assert tap(tools, app_id, "full_time") is None  # the licence is pending, not availability
    assert tools.app(app_id)["availability"] is None


def test_a_tapped_slot_is_booked_and_the_interview_comes_back(tools) -> None:
    """ADR 0050: the backend books the tapped slot; the agent only confirms from the result."""
    app_id = tools.new_app()
    tools.approve(app_id)
    offered = tools.ok(app_id, "offer_interview_slots")["slots"]
    message_id = tools.say(app_id, offered[1]["label"])
    body = tap(tools, app_id, offered[1]["slot_ref"])
    assert body is not None and body["ok"] is True
    assert body["value"]["stage"] == "interview_booked"
    assert body["value"]["interview"] == offered[1]["interview"]
    assert tools.app(app_id)["stage"] == "interview_booked"
    (booked,) = tools.events(app_id, "interview_booked")
    assert booked["via"] == "chat" and booked["invite_after_message"] == message_id
    assert pending_choice(tools.conn, app_id, now=tools.clock.now()) is None  # no slot buttons


def test_slot_taps_are_left_to_the_agent(tools) -> None:
    """Only the slot taps the backend cannot book: an id never offered, and a button of an
    offer a newer one ended. A slot in the pending choice is booked (above), and "other time"
    gets the booking link (ADR 0064, below)."""
    app_id = tools.new_app()
    tools.approve(app_id)
    slot = tools.ok(app_id, "offer_interview_slots")["slots"][0]["slot_ref"]
    assert tap(tools, app_id, "other") is not None  # was None before ADR 0064
    assert tap(tools, app_id, "so_never_offered") is None
    tools.ok(app_id, "offer_interview_slots")  # a newer offer ends the first one
    assert tap(tools, app_id, slot) is None
    assert tools.app(app_id)["stage"] == "approved"


def test_a_slot_tap_after_the_booking_is_left_to_the_agent(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    slots = tools.ok(app_id, "offer_interview_slots")["slots"]
    assert tap(tools, app_id, slots[0]["slot_ref"])["value"]["stage"] == "interview_booked"
    assert tap(tools, app_id, slots[1]["slot_ref"]) is None  # an old button, tapped again
    assert len(tools.events(app_id, "interview_booked")) == 1


def test_a_closed_or_taken_over_application_is_left_to_the_agent(tools) -> None:
    app_id = at_zone(tools)
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    tools.conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'user_rec' WHERE id = ?",
        (app_id,),
    )
    assert tap(tools, app_id, TOLUCA) is None
    tools.conn.execute(
        "UPDATE applications SET control = 'agent', control_user_id = NULL WHERE id = ?",
        (app_id,),
    )
    assert tap(tools, app_id, TOLUCA)["value"]["status"] == "knockout"  # now closed
    assert repo.require_application(tools.conn, app_id)["status"] == "closed"
    assert tap(tools, app_id, TOLUCA) is None


# --- a correction at the confirm step (ADR 0048) ---------------------------------------

PUEBLA = ["zone_mx_puebla_angelopolis", "zone_mx_puebla_centro", "zone_mx_puebla_la_paz"]


def at_confirm(tools) -> str:
    app_id = tools.new_app(country="MX")
    assert tools.answer_all(app_id, "MX")["next_field"] is None
    assert tools.app(app_id)["stage"] == "confirm"
    return app_id


def test_a_zone_correction_at_confirm_keeps_its_buttons_and_the_tap_is_recorded(tools) -> None:
    """The real-model stall: "no, mi zona es La Paz" at the summary, the agent passes the
    city's three ids, and the buttons it asks the Candidate to tap must be there."""
    app_id = at_confirm(tools)
    tools.say(app_id, "no, mi zona es La Paz")
    needs = tools.ok(app_id, "record_answer", field="zone", value=PUEBLA)
    assert needs["status"] == "needs_choice"
    assert [o["id"] for o in pending_choice(tools.conn, app_id)["options"]] == PUEBLA
    message_id = tools.say(app_id, "La Paz")
    body = tap(tools, app_id, "zone_mx_puebla_la_paz")
    assert body is not None and body["ok"] is True
    value = body["value"]
    assert value["status"] == "accepted" and value["next_field"] is None
    assert value["stage"] == "confirm"
    assert value["answers"]["zone"]["name"] == "La Paz"  # the summary to confirm again
    assert tools.app(app_id)["zone_id"] == "zone_mx_puebla_la_paz"
    assert pending_choice(tools.conn, app_id) is None  # answered: the buttons are gone
    assert tap(tools, app_id, "zone_mx_puebla_centro") is None
    recorded = tools.events(app_id, "answer_recorded")[-1]
    assert recorded["field"] == "zone" and recorded["message_id"] == message_id
    assert tools.ok(app_id, "finish_screening")["stage"] == "qualified"


def test_an_uncovered_zone_correction_at_confirm_asks_si_or_no_first(tools) -> None:
    app_id = at_confirm(tools)
    tools.say(app_id, "perdón, vivo en Toluca")
    assert tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])["confirm"] == TOLUCA
    assert [o["id"] for o in pending_choice(tools.conn, app_id)["options"]] == [TOLUCA, "no"]
    body = tap(tools, app_id, "no")
    assert body is not None and body["reason"] == "zone_not_confirmed"
    assert pending_choice(tools.conn, app_id) is None
    app = tools.app(app_id)
    assert app["zone_id"] == "zone_mx_cdmx_polanco"  # the earlier answer stands
    assert app["invalid_attempts"] == 0


# --- "other time" gets the booking link (ADR 0064) -------------------------------------


@pytest.mark.parametrize(
    ("country", "language", "words"),
    [
        ("ES", "es-ES", "mejor te venga"),
        ("MX", "es-MX", "mejor te acomode"),
        ("ES", "en", "pick the one that suits you"),
    ],
)
def test_other_time_is_answered_with_the_booking_link_in_the_candidates_language(
    tools, country, language, words
) -> None:
    app_id = tools.new_app(country=country, language=language)
    tools.approve(app_id, country)
    tools.ok(app_id, "offer_interview_slots")
    body = tap(tools, app_id, "other")
    assert body is not None and body["ok"] is True
    value = body["value"]
    assert value["status"] == BOOKING_LINK_STATUS
    assert value["booking_url"].startswith("https://empleo.gruposazon.example/book/")
    assert value["message"].endswith(value["booking_url"]) and words in value["message"]
    token = value["booking_url"].rsplit("/book/", 1)[1]
    assert verify_booking_token(token, now=tools.clock.now()) == app_id
    with pytest.raises(BookingTokenExpired):
        verify_booking_token(token, now=tools.clock.now() + timedelta(days=8))


def test_the_link_is_read_back_from_the_agent_input_only(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    tools.ok(app_id, "offer_interview_slots")
    text = tap_answer(tools.conn, app_id, "other", now=tools.clock.now())
    link = booking_link_of(text)
    assert link is not None and link[0] in link[1]
    assert booking_link_of("other") is None
    assert booking_link_of(TAP_RESULT_PREFIX + "not json") is None
    assert booking_link_of(TAP_RESULT_PREFIX + "[1]") is None
    assert booking_link_of(TAP_RESULT_PREFIX + '{"ok":true,"value":{"status":"accepted"}}') is None
    assert booking_link_of(TAP_RESULT_PREFIX + '{"value":{"status":"booking_link"}}') is None


def test_the_slot_offer_stays_valid_after_the_other_time_tap(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    offered = tools.ok(app_id, "offer_interview_slots")["slots"]
    before = pending_choice(tools.conn, app_id, now=tools.clock.now())
    assert tap(tools, app_id, "other")["value"]["status"] == BOOKING_LINK_STATUS
    assert pending_choice(tools.conn, app_id, now=tools.clock.now()) == before
    body = tap(tools, app_id, offered[0]["slot_ref"])
    assert body["value"]["stage"] == "interview_booked"


def test_other_time_after_the_offer_expired_still_gets_the_link(tools) -> None:
    """An old message's "Otro horario" button: no slot buttons are live, but the booking page
    shows every open slot to an Approved Candidate with no Interview."""
    app_id = tools.new_app()
    tools.approve(app_id)
    tools.ok(app_id, "offer_interview_slots")
    later = tools.clock.now() + timedelta(days=3)
    assert pending_choice(tools.conn, app_id, now=later) is None
    link = booking_link_of(tap_answer(tools.conn, app_id, "other", now=later))
    assert link is not None
    assert verify_booking_token(link[0].rsplit("/", 1)[1], now=later) == app_id


def test_other_time_is_left_alone_when_there_is_nothing_to_book(tools) -> None:
    # Not Approved: "other" is a platform value, never a booking link.
    app_id = tools.new_app()
    assert tap(tools, app_id, "other") is None
    # Booked: an old "Otro horario" button has no booking to offer.
    booked = tools.new_app()
    tools.approve(booked)
    slot = tools.ok(booked, "offer_interview_slots")["slots"][0]["slot_ref"]
    assert tap(tools, booked, slot)["value"]["stage"] == "interview_booked"
    assert tap(tools, booked, "other") is None
    # Taken over: the agent is not the one answering.
    other = tools.new_app()
    tools.approve(other)
    tools.ok(other, "offer_interview_slots")
    tools.conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'user_rec' WHERE id = ?",
        (other,),
    )
    assert tap(tools, other, "other") is None


def test_the_platform_other_option_is_still_recorded_as_an_answer(tools) -> None:
    app_id = tools.new_app()
    for field, value in [
        ("full_name", "Ana García"),
        ("zone", ["zone_es_madrid_chamberi"]),
        ("driver_license", "yes"),
        ("availability", ["full_time"]),
        ("preferred_schedule", ["morning"]),
        ("experience_years", 2),
    ]:
        tools.ok(app_id, "record_answer", field=field, value=value)
    body = tap(tools, app_id, "other")
    assert body["value"]["status"] == "accepted"
    assert body["value"]["field"] == "experience_platforms"


def test_other_time_on_a_voice_application_is_left_to_the_agent(tools) -> None:
    """ADR 0059: no channel carries a link to a Candidate on a call, and the Voice Screener
    says someone from the team will contact them, so the bare id reaches the agent."""
    app_id = tools.new_app()
    tools.approve(app_id)
    tools.ok(app_id, "offer_interview_slots")
    assert tap(tools, app_id, "other")["value"]["status"] == BOOKING_LINK_STATUS
    tools.conn.execute("UPDATE applications SET channel = 'voice' WHERE id = ?", (app_id,))
    assert tap(tools, app_id, "other") is None
    later = tools.clock.now() + timedelta(days=3)  # an expired offer changes nothing
    assert tap_answer(tools.conn, app_id, "other", now=later) is None
