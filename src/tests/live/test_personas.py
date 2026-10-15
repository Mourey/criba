"""The live Candidates are valid personas and say what the flows rely on."""

from __future__ import annotations

import pytest

from live import personas
from sim.personas.schema import FIELD_ORDER, Persona

ALL = [
    personas.qualified_english,
    personas.knockout_then_review,
    personas.cdmx_zone_list,
    personas.qualified_for_booking,
]


@pytest.mark.parametrize("build", ALL, ids=lambda f: f.__name__)
def test_each_persona_is_valid_and_ignores_latency(build) -> None:
    persona = build()
    assert isinstance(persona, Persona)
    assert persona.expect.max_reply_latency_s == personas.NO_LATENCY_LIMIT
    assert persona.expect.forbidden_agent_text[0] == "Criba"
    if persona.identity.locale == "es-ES":  # Spain asks for the barrio, never the colonia
        assert persona.expect.forbidden_agent_text == ["Criba", "colonia"]
    else:
        assert persona.expect.forbidden_agent_text == ["Criba"]


def test_phones_and_ids_are_unique() -> None:
    built = [build() for build in ALL]
    assert len({p.id for p in built}) == len(built)
    assert len({p.identity.phone for p in built}) == len(built)


def test_field_order_is_the_products() -> None:
    assert tuple(personas.FIELD_ORDER) == FIELD_ORDER


def test_the_qualified_personas_answer_every_field_in_free_text() -> None:
    for persona in (personas.qualified_english(), personas.qualified_for_booking()):
        assert set(persona.answers) == set(FIELD_ORDER)
        assert persona.expect.stage == "qualified"
        assert persona.opening.covers == []  # one thing at a time: the real model asks each


def test_the_knockout_persona_ends_on_a_review_request() -> None:
    persona = personas.knockout_then_review()
    assert persona.expect.outcome == "knockout" and persona.expect.knockout_template == "es-MX"
    (interrupt,) = persona.interrupts
    assert interrupt.at == "after_knockout" and interrupt.then == "stop"
    assert "revisi" in (interrupt.say.text if interrupt.say else "")


def test_the_cdmx_persona_asks_for_cuajimalpa_and_stops_at_the_licence() -> None:
    persona = personas.cdmx_zone_list()
    assert persona.answers["zone"][0].text == "Ciudad de México, Cuajimalpa"
    assert persona.zone_choice == "Polanco"
    assert [i.at for i in persona.interrupts] == ["driver_license"]


@pytest.mark.parametrize("language", list(personas.NO_APP_ANSWERS))
def test_the_no_app_personas_say_it_in_words_and_stop_at_the_start_date(language) -> None:
    """ADR 0053: the demo's words, in each language; the record must hold []."""
    persona = personas.no_app(language)
    assert persona.identity.locale == language
    assert persona.answers["experience_platforms"][0].text == personas.NO_APP_ANSWERS[language][1]
    assert persona.expect.fields["experience_platforms"].value == []
    assert [i.at for i in persona.interrupts] == ["start_date"]


@pytest.mark.parametrize("reading", personas.DATE_READINGS, ids=lambda r: r.key)
def test_each_start_date_persona_taps_only_when_its_phrase_is_ambiguous(reading) -> None:
    """ADR 0054: an ambiguous phrase has a second reply that taps a date; a plain one has one."""
    persona = personas.start_date_reading(reading)
    replies = persona.answers["start_date"]
    assert replies[0].text == reading.text
    assert len(replies) == (2 if reading.ambiguous else 1)
    if reading.ambiguous:
        assert replies[1].pick_option == reading.pick
    assert [i.at for i in persona.interrupts] == ["confirm"]


def test_the_qualified_personas_can_tap_a_monday_if_asked() -> None:
    """ "next Monday" and "el lunes que viene" may fit two Mondays (ADR 0054)."""
    for persona in (personas.qualified_english(), personas.qualified_for_booking()):
        assert persona.answers["start_date"][1].pick_option == 0
