"""An ambiguous Zone answer is a question about the Zone, not a Knockout (Phase 1 section 3).

"Puebla" names three seeded Zones, so the backend asks which one: Zone buttons for two or three
matches, a text re-ask for the city or neighbourhood for more. The driver must answer either
way and still tell it apart from the Knockout message, which asks nothing. Runs against the
in-process fake (`sim.fake_webchat`), no stack or model.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from sim.clock import SimClock
from sim.detect import detect_prompt, zone_options
from sim.driver import PersonaDriver, RunResult
from sim.fake_webchat import PUEBLA_OPTIONS, FakeWebChat
from sim.personas import Persona, load_personas

PERSONAS = load_personas()
PUEBLA_ANGELOPOLIS, PUEBLA_CENTRO, PUEBLA_LA_PAZ = (o["id"] for o in PUEBLA_OPTIONS)


ZONE_KNOCKOUT = {
    **PERSONAS["no_license_mx"].expect.model_dump(),
    "stage": "zone",
    "knockout_criterion": "zone",
    "fields_unanswered": [],
    "field_order": ["full_name"],
    "events": ["application_started", "knockout"],
    "event_counts": [],
}


def persona(**changes: Any) -> Persona:
    """no_license_mx with parts replaced: `zone` (reply texts) and any Persona field."""
    base = PERSONAS["no_license_mx"]
    update = dict(changes)
    if "zone" in update:
        replies = [{"text": t} for t in update.pop("zone")]
        update["answers"] = {
            **base.model_dump(exclude_unset=True)["answers"],
            "zone": replies,
        }
    return Persona.model_validate({**base.model_dump(exclude_unset=True), **update})


def play(p: Persona, fake: FakeWebChat | None = None):
    fake = fake or FakeWebChat()

    async def go() -> RunResult:
        http = fake.client()
        driver = PersonaDriver(http, clock=SimClock(http), poll_wait_s=1, settle_s=0)
        async with http:
            return await driver.run(p)

    result = asyncio.run(go())
    return fake, result, fake.application_detail(p.identity.phone)


def taps(fake: FakeWebChat) -> list[str]:
    return [x["request"]["option_id"] for x in fake.log if x["path"].endswith("/replies")]


def kinds(detail: dict[str, Any]) -> list[str]:
    return [e["kind"] for e in detail["events"]]


# ---- detector ---------------------------------------------------------------------------


def test_zone_ids_as_options_are_a_zone_question_whatever_the_text():
    interactive = {"style": "buttons", "options": PUEBLA_OPTIONS}
    assert zone_options(interactive) == PUEBLA_OPTIONS
    assert detect_prompt("Hay varias opciones. ¿Cuál es la tuya?", interactive) == "zone"
    assert detect_prompt("Elige una", interactive) == "zone"
    assert zone_options({"options": [{"id": "morning", "label": "Mañana"}]}) == []
    assert zone_options(None) == []


def test_a_city_zone_list_is_a_zone_question_like_the_buttons():
    """Q45: a known city is answered with its Zones as a list of up to ten."""
    options = [{"id": f"zone_mx_cdmx_{i}", "label": f"Colonia {i} (Ciudad…"} for i in range(9)]
    interactive = {"style": "list", "options": options}
    assert zone_options(interactive) == options
    assert detect_prompt("¿Cuál es tu zona? Elígela en la lista.", interactive) == "zone"
    assert detect_prompt("Elígela en la lista.", interactive) == "zone"


@pytest.mark.parametrize(
    "text",
    [
        "Ese lugar tiene varias zonas. ¿En qué barrio y ciudad vives? Por ejemplo: Centro, Puebla.",
        "Puebla tiene varias colonias. ¿Cuál es tu colonia?",
        "¿En qué ciudad estás? Hay un Centro en varias.",
        "That place has several areas. Which neighbourhood and city do you live in?",
        "Which city is that in?",
    ],
)
def test_a_text_re_ask_for_the_city_or_neighbourhood_is_a_zone_question(text):
    assert detect_prompt(text) == "zone"


def test_a_knockout_message_is_still_not_a_prompt():
    zone_knockout = (
        "Gracias. Por ahora no tenemos ninguna sucursal que reparta en Milpa Alta. "
        "Aquí están los requisitos: https://example.test/requisitos. "
        'Si crees que hay un error, responde "revisión".'
    )
    assert detect_prompt(zone_knockout) is None


# ---- ambiguous Zone, answered with buttons ----------------------------------------------


def test_ambiguous_zone_with_buttons_is_tapped_and_screening_goes_on():
    fake, result, detail = play(persona())
    assert taps(fake) == [PUEBLA_CENTRO]  # zone_choice: Centro
    assert result.ended == "knockout"  # the license Knockout, not the Zone question
    assert detail["knockout_criterion"] == "driver_license"
    assert detail["answers"]["zone"]["id"] == PUEBLA_CENTRO
    assert "answer_ambiguous" in kinds(detail)
    assert kinds(detail).count("knockout") == 1
    tapped = next(e for e in result.entries if e.reply_to)
    assert tapped.text == "Centro (Puebla)" and tapped.purpose == "answer:zone"


def test_buttons_default_to_the_option_that_shares_the_most_words_with_the_answers():
    fake, _, _ = play(persona(zone_choice=None, zone=["Puebla", "Centro, Puebla"]))
    assert taps(fake) == [PUEBLA_CENTRO]


def test_buttons_default_to_the_first_option_when_nothing_tells_them_apart():
    fake, _, detail = play(persona(zone_choice=None, zone=["Puebla"]))
    assert taps(fake) == [PUEBLA_ANGELOPOLIS]
    assert detail["knockout_criterion"] == "driver_license"


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        (2, PUEBLA_LA_PAZ),
        ("la paz", PUEBLA_LA_PAZ),
        ("ANGELOPOLIS", PUEBLA_ANGELOPOLIS),
        (9, PUEBLA_ANGELOPOLIS),  # out of range: falls back to the default
        ("Narnia", PUEBLA_ANGELOPOLIS),  # no label matches: falls back to the default
    ],
)
def test_zone_choice_picks_by_index_or_label(choice, expected):
    fake, _, _ = play(persona(zone_choice=choice, zone=["Puebla"]))
    assert taps(fake) == [expected]


def test_a_scripted_pick_option_wins_over_zone_choice():
    p = persona(zone_choice="Centro")
    p.answers["zone"] = [
        p.answers["zone"][0],
        p.answers["zone"][1].model_copy(update={"pick_option": 2}),
    ]
    fake, _, _ = play(p)
    assert taps(fake) == [PUEBLA_LA_PAZ]


# ---- ambiguous Zone, answered with a text re-ask ----------------------------------------


def test_ambiguous_zone_with_a_text_re_ask_is_answered_in_text():
    fake, result, detail = play(persona(), FakeWebChat(zone_clarification="text"))
    assert taps(fake) == []
    sent = [e.text for e in result.entries if e.direction == "inbound"]
    assert sent[2:4] == ["Puebla", "Centro, Puebla"]
    assert result.ended == "knockout"
    assert detail["knockout_criterion"] == "driver_license"
    assert detail["answers"]["zone"]["id"] == PUEBLA_CENTRO
    assert kinds(detail).count("knockout") == 1


def test_a_re_ask_the_detector_cannot_place_is_answered_not_taken_for_a_knockout():
    # The fake's invalid-answer wording ("¿Me lo dices de otra forma?") names no field.
    fake, result, detail = play(
        persona(zone=["Narnia", "Centro, Puebla"]), FakeWebChat(zone_funnel=False)
    )
    assert taps(fake) == []
    assert "answer_invalid" in kinds(detail)
    assert result.ended == "knockout"
    assert detail["knockout_criterion"] == "driver_license"
    assert detail["answers"]["zone"]["id"] == PUEBLA_CENTRO


# ---- a place that matches no Zone: the cities funnel (ADR 0068) -------------------------


def test_a_place_with_no_zone_is_asked_as_a_list_of_cities_and_the_city_leads_to_its_zones():
    fake, result, detail = play(persona(zone=["Narnia", "Centro, Puebla"]))
    assert taps(fake) == ["city_mx_puebla", PUEBLA_CENTRO]
    assert "answer_invalid" not in kinds(detail)
    funnel = next(e["data"] for e in detail["events"] if e["kind"] == "answer_ambiguous")
    assert funnel["funnel"] is True and funnel["options"][-1]["id"] == "city_none"
    assert result.ended == "knockout"
    assert detail["knockout_criterion"] == "driver_license"
    assert detail["answers"]["zone"]["id"] == PUEBLA_CENTRO


def test_none_of_these_is_the_zone_knockout_with_no_zone_stored():
    p = persona(zone=["Narnia"], zone_choice="Ninguna", interrupts=[], expect=ZONE_KNOCKOUT)
    fake, result, detail = play(p)
    assert taps(fake) == ["city_none"]
    assert result.ended == "knockout"
    assert (detail["knockout_criterion"], detail["outcome"]) == ("zone", "knockout")
    assert detail["answers"]["zone"] is None
    assert kinds(detail).count("knockout") == 1 and "answer_invalid" not in kinds(detail)


def test_a_second_place_that_matches_no_zone_is_the_zone_knockout():
    p = persona(zone=["Narnia", "Mordor"], interrupts=[], expect=ZONE_KNOCKOUT)
    first, second = p.answers["zone"]
    typed = second.model_copy(update={"pick_option": "no-such-option"})  # types it, taps nothing
    p = p.model_copy(update={"answers": {**p.answers, "zone": [first, typed]}})
    fake, result, detail = play(p)
    assert taps(fake) == []
    assert result.ended == "knockout"
    assert detail["knockout_criterion"] == "zone" and detail["answers"]["zone"] is None
    assert kinds(detail).count("answer_ambiguous") == 1


# ---- a real Zone Knockout is still a Knockout --------------------------------------------


def test_a_zone_outside_every_service_area_is_still_detected_as_a_knockout():
    expect = {
        **PERSONAS["no_license_mx"].expect.model_dump(),
        "stage": "zone",
        "knockout_criterion": "zone",
        "fields_unanswered": [],
        "field_order": ["full_name", "zone"],
        "events": ["application_started", "knockout"],
        "event_counts": [],
    }
    p = persona(zone=["Vivo en Toledo"], interrupts=[], expect=expect)
    fake, result, detail = play(p)
    assert result.ended == "knockout"
    assert taps(fake) == []
    assert detail["knockout_criterion"] == "zone"
    assert detail["outcome"] == "knockout"
    assert "answer_ambiguous" not in kinds(detail)
    assert detail["answers"]["driver_license"] is None


def test_a_license_knockout_after_a_resolved_ambiguity_fires_the_dispute():
    _, result, detail = play(persona())
    assert result.ended == "knockout"
    assert "review_requested" in kinds(detail)  # the after_knockout interrupt ran


# ---- one uncovered Zone: the Sí/No confirmation (ADR 0042) --------------------------------


def _toluca_persona(second_zone_reply: dict[str, Any], **changes: Any) -> Persona:
    data = PERSONAS["no_license_mx"].model_dump(exclude_unset=True)
    zone = [
        {"text": "por la salida a Toluca", "query": "Toluca"},
        second_zone_reply,
        {"text": "La Paz, Puebla"},
    ]
    return Persona.model_validate({**data, "answers": {**data["answers"], "zone": zone}, **changes})


def test_the_si_tap_on_the_toluca_confirmation_is_the_zone_knockout():
    expect = {
        **PERSONAS["no_license_mx"].expect.model_dump(),
        "stage": "zone",
        "knockout_criterion": "zone",
        "fields_unanswered": [],
        "field_order": ["full_name", "zone"],
        "events": ["application_started", "answer_ambiguous", "knockout"],
        "event_counts": [],
    }
    p = _toluca_persona(
        {"text": "Sí", "pick_option": 0, "query": None}, interrupts=[], expect=expect
    )
    fake, result, detail = play(p)
    assert result.ended == "knockout"
    assert taps(fake) == ["zone_mx_toluca"]
    assert detail["knockout_criterion"] == "zone"
    assert kinds(detail).count("answer_ambiguous") == 1
    assert "answer_invalid" not in kinds(detail)


def test_the_no_tap_on_the_toluca_confirmation_counts_no_miss_and_asks_again():
    p = _toluca_persona({"text": "No", "pick_option": 1, "query": None})
    fake, _, detail = play(p)
    assert taps(fake)[0] == "no"
    # The Zone is no Knockout: this persona ends at the license, once Puebla's La Paz is recorded.
    assert detail["knockout_criterion"] == "driver_license"
    assert detail["answers"]["zone"]["id"] == "zone_mx_puebla_la_paz"
    assert "answer_invalid" not in kinds(detail)
    # The confirmation, then the empty `answer_ambiguous` that clears its buttons.
    ambiguous = [e for e in detail["events"] if e["kind"] == "answer_ambiguous"]
    assert ambiguous[1]["data"]["options"] == []
