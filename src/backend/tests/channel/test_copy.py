"""Synthetic nudge copy for the Stalled ladder (Phase 1 sections 4.1 and 6)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from criba.channel import copy
from criba.scheduler import NUDGE_LADDER


def test_the_copy_is_marked_synthetic() -> None:
    assert copy.SYNTHETIC is True
    assert "SYNTHETIC" in (copy.__doc__ or "")


@pytest.mark.parametrize("step", NUDGE_LADDER, ids=lambda s: s.kind)
@pytest.mark.parametrize("language", copy.LANGUAGES)
def test_every_ladder_step_has_short_copy_in_every_language(step, language: str) -> None:
    text = copy.NUDGE_COPY[step.kind][language]
    assert text.strip() == text and text
    assert len(text) < 300  # section 6: short, under 300 characters
    sentences = [s for s in re.split(r"(?<=[.?!])\s+", text) if s]
    assert len(sentences) <= 3  # a short opener plus one or two sentences
    assert "STOP" in text  # an easy way to stop (Opted out)


def test_the_ladder_and_the_copy_cover_the_same_steps() -> None:
    assert {s.kind for s in NUDGE_LADDER} == set(copy.NUDGE_COPY)


@pytest.mark.parametrize(
    ("language", "country", "expected"),
    [
        ("en", "MX", "en"),
        ("es-MX", "ES", "es-MX"),
        (None, "MX", "es-MX"),
        (None, "ES", "es-ES"),
        (None, None, "es-ES"),
        ("fr", None, "es-ES"),
    ],
)
def test_copy_language(language, country, expected) -> None:
    assert copy.copy_language(language, country) == expected
    assert copy.nudge_text("nudge", language, country) == copy.NUDGE_COPY["nudge"][expected]


# --- the AI disclosure guard (ADR 0044) ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Soy el asistente virtual (IA) de Grupo Sazón",
        "Soy una inteligencia artificial",
        "Hi! I'm Grupo Sazón's virtual (AI) assistant",
        "Soy un bot de Grupo Sazón",
        "Soy un ChatBot",
    ],
)
def test_texts_that_disclose_are_left_alone(text: str) -> None:
    assert copy.discloses_ai(text)
    assert copy.with_ai_disclosure(text, []) == text


@pytest.mark.parametrize(
    "text",
    ["Soy el asistente virtual de Grupo Sazón", "Hola, ¿cómo te llamas?", "Paris is in Asia"],
)
def test_virtual_assistant_alone_is_not_a_disclosure(text: str) -> None:
    assert not copy.discloses_ai(text)


def test_an_earlier_disclosure_covers_later_messages() -> None:
    assert copy.with_ai_disclosure("¿En qué zona vives?", ["Soy una IA de Grupo Sazón"]) == (
        "¿En qué zona vives?"
    )


def test_asistente_virtual_gets_its_ia_in_place() -> None:
    out = copy.with_ai_disclosure("¡Hola! Soy el asistente virtual de Grupo Sazón 🛵", [])
    assert out == "¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵"


def test_virtual_assistant_gets_its_ai_in_place() -> None:
    out = copy.with_ai_disclosure("Hi! I'm Grupo Sazón's Virtual Assistant.", [], language="en")
    assert out == "Hi! I'm Grupo Sazón's virtual (AI) assistant."


@pytest.mark.parametrize("language", copy.LANGUAGES)
def test_without_the_phrase_the_sentence_leads_in_the_candidates_language(language: str) -> None:
    out = copy.with_ai_disclosure("What is your name?", [], language=language)
    assert out == f"{copy.AI_DISCLOSURE[language]} What is your name?"
    assert copy.discloses_ai(copy.AI_DISCLOSURE[language])


def test_the_country_picks_the_spanish_variant_when_no_language_is_known() -> None:
    assert copy.with_ai_disclosure("Hola", [], country="MX").startswith("Soy un asistente")


@pytest.mark.parametrize("language", ["es-ES", "es-MX", "en"])
def test_the_spoken_opt_out_confirmation_is_its_own_line(language: str) -> None:
    """Owner decision 64: a spoken 'baja' is answered by a line that points nowhere to write."""
    spoken = copy.opt_out_confirmation(language, "ES", spoken=True)
    typed = copy.opt_out_confirmation(language, "ES")
    assert spoken == copy.SPOKEN_OPT_OUT_CONFIRMATION[language] != typed
    assert typed == copy.OPT_OUT_CONFIRMATION[language]  # typed text unchanged
    assert "no te contactaremos más" in spoken and "vuelve a contactarnos" in spoken
    assert "escr" not in spoken.lower()


@pytest.mark.parametrize("language", ["es-ES", "es-MX", "en"])
def test_every_code_built_text_follows_the_stored_language(language: str) -> None:
    """Nudges, the opt-out reply and the Qualified close are all looked up by the stored
    language, whatever the country says (Phase 1 section 4.3)."""
    other_country = "ES" if language == "es-MX" else "MX"
    texts = [copy.nudge_text(kind, language, other_country) for kind in copy.NUDGE_COPY]
    texts += [
        copy.opt_out_confirmation(language, other_country),
        copy.qualified_close(language, other_country, "Ana"),
    ]
    assert texts == [
        *(copy.NUDGE_COPY[kind][language] for kind in copy.NUDGE_COPY),
        copy.OPT_OUT_CONFIRMATION[language],
        copy.QUALIFIED_CLOSE[language].format(name=", Ana"),
    ]


def test_the_qualified_close_without_a_language_follows_the_country() -> None:
    assert "revisará tu solicitud" in copy.qualified_close(None, "MX")
    assert copy.qualified_close(None, "MX", None).startswith("¡Listo!")
    assert copy.qualified_close("en", "MX").startswith("All set!")


# --- the spoken copy (spec voice-agent session-start SE10, SE15) ---------------------------

HANDOVER = "Una persona del equipo de Grupo Sazón se pondrá en contacto contigo."
REASK = "Perdona, no te he oído bien. ¿Es sí o no?"


@pytest.mark.parametrize("language", ["es-ES", "es-MX", "en", None, "es"])
@pytest.mark.parametrize("country", ["ES", "MX", None])
def test_spoken_copy_resolves_for_every_language(language: str | None, country: str | None) -> None:
    assert set(copy.SPOKEN_DISCLOSURE) == set(copy.LANGUAGES)
    assert set(copy.SPOKEN_HANDOVER) == set(copy.LANGUAGES)
    assert set(copy.SPOKEN_REASK) == set(copy.LANGUAGES)
    for key in copy.LANGUAGES:
        assert copy.discloses_ai(copy.SPOKEN_DISCLOSURE[key])
        assert copy.SPOKEN_HANDOVER[key] == HANDOVER
        assert copy.SPOKEN_REASK[key] == REASK
    # Spoken agents speak `es` (parent R18): the `en` line is the Spanish one that is spoken.
    assert copy.SPOKEN_DISCLOSURE["en"] == copy.SPOKEN_DISCLOSURE["es-ES"]

    variant = copy.copy_language(language, country)
    assert copy.spoken_disclosure(language, country) == copy.SPOKEN_DISCLOSURE[variant]
    assert copy.discloses_ai(copy.spoken_disclosure(language, country))
    assert copy.spoken_handover(language, country) == HANDOVER
    assert copy.spoken_reask(language, country) == REASK
    assert copy.spoken_disclosure(language) == copy.SPOKEN_DISCLOSURE[copy.copy_language(language)]


@pytest.mark.parametrize("country", ["ES", "MX"])
def test_the_spoken_disclosure_follows_the_country(country: str) -> None:
    spoken = copy.spoken_disclosure("es", country)
    disclosure = copy.AI_DISCLOSURE[copy.copy_language(None, country)]
    assert spoken.startswith(disclosure)
    greeting = spoken[len(disclosure) :].strip()
    assert greeting  # one short spoken greeting follows
    assert len(greeting) < 120
    assert copy.with_ai_disclosure("¿Cómo te llamas?", [spoken]) == "¿Cómo te llamas?"


# --- the privacy notice guard (ADR 0068) --------------------------------------------------


def test_a_first_message_without_the_link_gets_the_notice_on_its_own_line() -> None:
    text = "¡Hola! Soy el asistente virtual (IA) de Grupo Sazón. ¿Cómo te llamas?"
    out = copy.with_privacy_notice(text, [], language="es-ES", country="ES")
    notice = "Tus datos se tratan según nuestra política de privacidad"
    assert out == f"{text}\n{notice}: {copy.PRIVACY_URL}"


@pytest.mark.parametrize(
    ("language", "country", "words"),
    [
        ("es-MX", "MX", "aviso de privacidad"),
        ("es-ES", "ES", "política de privacidad"),
        ("en", "MX", "privacy notice"),
        (None, "MX", "aviso de privacidad"),
    ],
)
def test_the_notice_follows_the_language(language, country, words) -> None:
    out = copy.with_privacy_notice("Hola", [], language=language, country=country)
    assert words in out and out.endswith(copy.PRIVACY_URL)


def test_a_message_that_already_links_the_notice_is_unchanged() -> None:
    text = f"Hola. Tus datos: {copy.PRIVACY_URL}. ¿Cómo te llamas?"
    assert copy.with_privacy_notice(text, [], language="es-ES") == text


def test_said_once_an_earlier_message_with_the_link_ends_the_check() -> None:
    earlier = ["Hola", f"Aviso: {copy.PRIVACY_URL}"]
    assert copy.with_privacy_notice("¿En qué zona vives?", earlier, language="es-ES") == (
        "¿En qué zona vives?"
    )


def test_an_earlier_message_without_the_link_does_not_end_it() -> None:
    out = copy.with_privacy_notice("Hola", ["Soy un bot"], language="es-ES")
    assert copy.PRIVACY_URL in out


@pytest.mark.parametrize("language", copy.LANGUAGES)
def test_the_privacy_notice_has_a_link_slot_in_every_language(language: str) -> None:
    assert copy.PRIVACY_NOTICE[language].count("{url}") == 1


def test_the_skills_privacy_link_is_the_notice_link() -> None:
    skill = Path(__file__).parents[3] / "skills" / "criba" / "screener" / "SKILL.md"
    assert copy.PRIVACY_URL in skill.read_text(encoding="utf-8")


# --- the Zone funnel copy (ADR 0068) -------------------------------------------------------


@pytest.mark.parametrize("language", copy.LANGUAGES)
def test_the_funnel_question_names_the_cities_and_asks_once(language: str) -> None:
    text = copy.service_cities_question(["Puebla", "Monterrey"], language)
    assert "Puebla, Monterrey" in text and text.count("?") == 1
    assert len(copy.no_service_city_label(language)) <= 24  # a list row


@pytest.mark.parametrize("criterion", ["driver_license", "zone", None])
@pytest.mark.parametrize("language", copy.LANGUAGES)
def test_the_knockout_closed_note_says_closed_and_how_to_ask_for_a_review(
    criterion: str | None, language: str
) -> None:
    text = copy.knockout_closed(criterion, language)
    review = "review" if language == "en" else "revisión"
    assert f'"{review}"' in text
    assert ("closed" if language == "en" else "cerrada") in text
    assert not re.search(r"\b(?:knockout|driver_license|zone_)\b", text, re.IGNORECASE)


def test_the_knockout_closed_note_uses_each_countrys_words() -> None:
    assert "carnet de conducir" in copy.knockout_closed("driver_license", "es-ES")
    assert "licencia de manejo" in copy.knockout_closed("driver_license", "es-MX")
    assert "barrio" in copy.knockout_closed("zone", "es-ES")
    assert "colonia" in copy.knockout_closed("zone", "es-MX")
    # an unknown criterion, or a language we do not have, falls back
    assert copy.knockout_closed("other", "en") == copy.KNOCKOUT_CLOSED[None]["en"]
    assert copy.knockout_closed("zone", None, "MX") == copy.KNOCKOUT_CLOSED["zone"]["es-MX"]


@pytest.mark.parametrize("language", copy.LANGUAGES)
def test_the_welcome_back_says_a_new_application_starts(language: str) -> None:
    text = copy.welcome_back(language)
    assert ("new one" if language == "en" else "nueva") in text
    assert len(text) < 120
