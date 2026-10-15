"""pending_choice (Q40): slot offers, then the latest ambiguity, then next_field's options."""

from __future__ import annotations

from datetime import timedelta

from criba.tools import pending_choice
from criba.tools.schema import validate_answer_value

LIST_FIELDS = {"zone", "availability", "preferred_schedule", "experience_platforms"}


def choice(tools, app_id: str):
    return pending_choice(tools.conn, app_id, now=tools.clock.now())


def tapped(field: str, option_id: str):
    """What the agent passes record_answer for a tap (the skill's rule, ADR 0042): the id
    as is for the licence, in a list for the list fields and the Zone."""
    return [option_id] if field in LIST_FIELDS else option_id


def test_unknown_application_is_none(tools) -> None:
    assert pending_choice(tools.conn, "app_missing") is None


def test_now_is_optional(tools) -> None:
    app_id = tools.new_app()
    tools.ok(app_id, "record_answer", field="full_name", value="Ana García")
    tools.ok(app_id, "record_answer", field="zone", value=["zone_es_madrid_chamberi"])
    assert pending_choice(tools.conn, app_id)["style"] == "buttons"


def test_no_choice_for_free_text_fields(tools) -> None:
    app_id = tools.new_app()
    assert choice(tools, app_id) is None  # full_name
    tools.ok(app_id, "record_answer", field="full_name", value="Ana García")
    assert choice(tools, app_id) is None  # zone, no ambiguity yet


def test_driver_license_buttons_in_the_candidates_language(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_cdmx_polanco"])
    assert choice(tools, app_id) == {
        "style": "buttons",
        "options": [{"id": "yes", "label": "Sí, tengo licencia"}, {"id": "no", "label": "No"}],
    }


def test_availability_buttons_and_schedule_list(tools) -> None:
    app_id = tools.new_app(language="en")
    for field, answer in [
        ("full_name", "Ann Smith"),
        ("zone", ["zone_es_madrid_chamberi"]),
        ("driver_license", "yes"),
    ]:
        tools.ok(app_id, "record_answer", field=field, value=answer)
    assert choice(tools, app_id) == {
        "style": "buttons",
        "options": [
            {"id": "full_time", "label": "Full-time"},
            {"id": "part_time", "label": "Part-time"},
            {"id": "weekends", "label": "Weekends"},
        ],
    }
    tools.ok(app_id, "record_answer", field="availability", value=["full_time"])
    schedule = choice(tools, app_id)
    assert schedule["style"] == "list"
    assert [o["id"] for o in schedule["options"]] == ["morning", "afternoon", "evening", "flexible"]
    tools.ok(app_id, "record_answer", field="preferred_schedule", value=["morning"])
    assert choice(tools, app_id) is None  # experience years: free text


def test_the_platforms_question_is_a_list_that_ends_with_no_app(tools) -> None:
    """ADR 0053: the platforms list ends with "no app", so a Candidate who delivered on their
    own can answer it with a tap from the first question; 0 years skips it, so no list."""
    for language, none_label in (("es-MX", "Ninguna app"), ("en", "No app")):
        app_id = tools.new_app(language=language, country="MX")
        for field, answer in [
            ("full_name", "Luis Hernández"),
            ("zone", ["zone_mx_cdmx_polanco"]),
            ("driver_license", "yes"),
            ("availability", ["part_time"]),
            ("preferred_schedule", ["afternoon"]),
            ("experience_years", 10),
        ]:
            tools.ok(app_id, "record_answer", field=field, value=answer)
        platforms = choice(tools, app_id)
        assert platforms["style"] == "list"
        assert [o["id"] for o in platforms["options"]] == [
            "glovo",
            "uber_eats",
            "just_eat",
            "rappi",
            "didi_food",
            "other",
            "none",
        ]
        assert platforms["options"][-1]["label"] == none_label
        tools.ok(app_id, "record_answer", field="experience_platforms", value=[])
        assert choice(tools, app_id) is None  # start date: free text


def test_tapped_option_ids_are_valid_answers(tools) -> None:
    """Every option id pending_choice offers, put in its field's shape, is accepted."""
    for language in ("es-ES", "es-MX", "en"):
        app_id = tools.new_app(language=language)
        tools.ok(app_id, "record_answer", field="full_name", value="Ana García")
        tools.ok(app_id, "record_answer", field="zone", value=["zone_es_madrid_chamberi"])
        for field in ("driver_license", "availability", "preferred_schedule"):
            options = choice(tools, app_id)["options"]
            for option in options:  # every tap has its field's shape (No would knock out)
                assert validate_answer_value(field, tapped(field, option["id"])) is None
            value = tools.ok(
                app_id, "record_answer", field=field, value=tapped(field, options[0]["id"])
            )
            assert value["status"] == "accepted", (language, field)


def test_a_tapped_list_code_not_wrapped_is_invalid_args_and_keeps_the_buttons(tools) -> None:
    """The backend parses nothing: a bare code for a list field is the agent's error. The
    hint names the list shape, no miss counts, and the same buttons stay attached."""
    app_id = tools.new_app()
    for field, value in [
        ("full_name", "Ana García"),
        ("zone", ["zone_es_madrid_chamberi"]),
        ("driver_license", "yes"),
    ]:
        tools.ok(app_id, "record_answer", field=field, value=value)
    before = choice(tools, app_id)
    status, body = tools.raw(app_id, "record_answer", field="availability", value="full_time")
    assert status == 422 and "a list of codes" in body["hint"]
    assert tools.app(app_id)["invalid_attempts"] == 0
    assert choice(tools, app_id) == before
    value = tools.ok(app_id, "record_answer", field="availability", value=["full_time"])
    assert value["status"] == "accepted"


def test_a_zone_confirmations_taps_sí_is_the_id_and_no_is_null(tools) -> None:
    """The Sí button's id is the Zone (a Knockout when tapped); No reaches the tool as null."""
    toluca = "zone_mx_toluca_toluca"
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    tools.ok(app_id, "record_answer", field="zone", value=[toluca])
    buttons = choice(tools, app_id)
    assert [o["id"] for o in buttons["options"]] == [toluca, "no"]
    tools.err(app_id, "record_answer", field="zone", value=None)  # the No tap
    assert choice(tools, app_id) is None  # buttons cleared; the Zone is asked again
    tools.ok(app_id, "record_answer", field="zone", value=[toluca])
    sí = choice(tools, app_id)["options"][0]["id"]
    value = tools.ok(app_id, "record_answer", field="zone", value=tapped("zone", sí))
    assert value["status"] == "knockout"


def test_ambiguity_wins_over_the_field_options(tools) -> None:
    # Reuse the two-way "Santa Fe" Zones (the seed has no 2-3 way ambiguity).
    for city, slug, area in [
        ("Ciudad de México", "cdmx", "sa_mx_cdmx_polanco"),
        ("Guadalajara", "guadalajara", "sa_mx_guadalajara_zapopan"),
    ]:
        tools.conn.execute(
            "INSERT INTO zones (id, country, city, name, aliases, timezone)"
            " VALUES (?, 'MX', ?, 'Santa Fe', '[]', 'America/Mexico_City')",
            (f"zone_mx_{slug}_santa_fe", city),
        )
        tools.conn.execute(
            "INSERT INTO service_area_zones (service_area_id, zone_id) VALUES (?, ?)",
            (area, f"zone_mx_{slug}_santa_fe"),
        )
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="driver_license", value="yes")
    santa_fes = ["zone_mx_cdmx_santa_fe", "zone_mx_guadalajara_santa_fe"]
    needs = tools.ok(app_id, "record_answer", field="zone", value=santa_fes)
    assert choice(tools, app_id) == {"style": "buttons", "options": needs["options"]}
    # A miss on the same field keeps the buttons.
    tools.err(app_id, "record_answer", field="zone", value=None)
    assert choice(tools, app_id)["options"] == needs["options"]
    # Once answered, the ambiguity is gone.
    tools.ok(app_id, "record_answer", field="zone", value=tapped("zone", needs["options"][1]["id"]))
    assert choice(tools, app_id) is None  # full_name is next: free text


def test_slot_offers_come_first_with_the_other_option(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    assert choice(tools, app_id) is None  # approved, nothing offered yet
    offer = tools.ok(app_id, "offer_interview_slots")
    got = choice(tools, app_id)
    assert got["style"] == "list"  # 3 slots + "Otro horario" exceed 3 reply buttons
    assert got["options"] == [
        *({"id": s["slot_ref"], "label": s["label"]} for s in offer["slots"]),
        {"id": "other", "label": "Otro horario"},
    ]


def test_slot_options_drop_booked_slots_and_fit_buttons(tools) -> None:
    a = tools.new_app()
    b = tools.new_app()
    tools.approve(a)
    tools.approve(b)
    slots_b = tools.ok(b, "offer_interview_slots")["slots"]
    ref_a = tools.ok(a, "offer_interview_slots")["slots"][0]["slot_ref"]
    tools.ok(a, "book_interview", slot_ref=ref_a)
    got = choice(tools, b)
    assert got["style"] == "buttons"
    assert [o["id"] for o in got["options"]] == [s["slot_ref"] for s in slots_b[1:]] + ["other"]
    # After a's booking its own offers are consumed.
    assert choice(tools, a) is None


def test_expired_offers_are_not_offered(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    tools.ok(app_id, "offer_interview_slots")
    later = tools.clock.now() + timedelta(hours=49)
    assert pending_choice(tools.conn, app_id, now=later) is None


def test_closed_or_taken_over_has_no_choice(tools) -> None:
    app_id = tools.new_app()
    tools.ok(app_id, "record_answer", field="full_name", value="Ana García")
    tools.ok(app_id, "record_answer", field="zone", value=["zone_es_madrid_chamberi"])
    tools.conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'user_rec' WHERE id = ?",
        (app_id,),
    )
    assert choice(tools, app_id) is None
    tools.conn.execute(
        "UPDATE applications SET control = 'agent', control_user_id = NULL WHERE id = ?",
        (app_id,),
    )
    tools.ok(app_id, "record_answer", field="driver_license", value="no")
    assert choice(tools, app_id) is None


def test_no_slot_buttons_once_booked_whatever_the_clock(tools) -> None:
    """The booking confirmation carries no slots: the booked Application's older offers are
    gone even when the caller's clock is behind the tool's (sim clock vs wall clock)."""
    app_id = tools.new_app()
    tools.approve(app_id)
    ref = tools.ok(app_id, "offer_interview_slots")["slots"][0]["slot_ref"]
    tools.ok(app_id, "book_interview", slot_ref=ref)
    assert choice(tools, app_id) is None
    behind = tools.clock.now() - timedelta(hours=1)
    assert pending_choice(tools.conn, app_id, now=behind) is None


def test_a_reschedule_offer_after_the_booking_brings_the_slots_back(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    first = tools.ok(app_id, "offer_interview_slots")["slots"][0]["slot_ref"]
    tools.ok(app_id, "book_interview", slot_ref=first)
    offer = tools.ok(app_id, "offer_interview_slots", reschedule=True)["slots"]
    got = choice(tools, app_id)
    assert got is not None
    assert [o["id"] for o in got["options"]] == [s["slot_ref"] for s in offer] + ["other"]
    # Booking one of them ends the reschedule offer too.
    tools.ok(app_id, "book_interview", slot_ref=offer[0]["slot_ref"])
    assert choice(tools, app_id) is None
    assert pending_choice(tools.conn, app_id, now=tools.clock.now() - timedelta(hours=1)) is None


def test_a_known_city_attaches_its_zones_as_a_list_until_one_is_picked(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    assert choice(tools, app_id) is None
    found = tools.ok(app_id, "search_zones", query="Ciudad de Mexico, Cuajimalpa")["zones"]
    err = tools.err(app_id, "record_answer", field="zone", value=[z["zone_id"] for z in found])
    pending = choice(tools, app_id)
    assert pending == {"style": "list", "options": err["options"]}
    assert len(pending["options"]) == 9
    tools.ok(
        app_id, "record_answer", field="zone", value=tapped("zone", pending["options"][0]["id"])
    )
    after = choice(tools, app_id)
    assert after is not None and [o["id"] for o in after["options"]] == ["yes", "no"]


# --- the Zone confirmation (ADR 0042, spec 006 decision 3) --------------------------

TOLUCA = "zone_mx_toluca_toluca"


def test_a_zone_confirmation_attaches_si_and_no_buttons(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    assert choice(tools, app_id) == {
        "style": "buttons",
        "options": [{"id": TOLUCA, "label": "Sí"}, {"id": "no", "label": "No"}],
    }


def test_no_clears_the_confirmation_buttons(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    tools.err(app_id, "record_answer", field="zone", value=None)
    assert choice(tools, app_id) is None


def test_si_ends_in_a_knockout_with_no_buttons(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    tools.ok(app_id, "record_answer", field="zone", value=[TOLUCA])
    assert choice(tools, app_id) is None  # closed


# --- a correction at the confirm step (ADR 0048) ---------------------------------------

PUEBLA = ["zone_mx_puebla_angelopolis", "zone_mx_puebla_centro", "zone_mx_puebla_la_paz"]


def test_a_correction_s_options_are_attached_while_the_field_is_already_answered(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.answer_all(app_id, "MX")
    assert choice(tools, app_id) is None  # the summary question: nothing to tap
    needs = tools.ok(app_id, "record_answer", field="zone", value=PUEBLA)
    assert choice(tools, app_id) == {"style": "buttons", "options": needs["options"]}
    # A typed answer the agent records ends it, like a tap would.
    tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_puebla_la_paz"])
    assert choice(tools, app_id) is None


def test_a_correction_left_open_is_dropped_when_screening_ends(tools) -> None:
    """The Candidate keeps the old Zone after all: no Zone buttons on the Qualified close."""
    app_id = tools.new_app(country="MX")
    tools.answer_all(app_id, "MX")
    tools.ok(app_id, "record_answer", field="zone", value=PUEBLA)
    assert choice(tools, app_id) is not None
    tools.ok(app_id, "finish_screening")
    assert choice(tools, app_id) is None


def test_another_field_recorded_does_not_end_a_correction_s_options(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.answer_all(app_id, "MX")
    tools.ok(app_id, "record_answer", field="zone", value=PUEBLA)
    tools.ok(app_id, "record_answer", field="experience_years", value=3)
    assert [o["id"] for o in choice(tools, app_id)["options"]] == PUEBLA
