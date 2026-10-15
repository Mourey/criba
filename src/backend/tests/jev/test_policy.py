"""Thresholds and the decisions made from a reading (ADR 0051)."""

from __future__ import annotations

import dataclasses

import pytest

import criba.jev.policy as policy
from criba.jev.policy import (
    THRESHOLDS,
    TONE_HINT_TEXT,
    decide_language,
    decide_tone,
    family,
    spanish_for,
)

from .conftest import reading


def test_spanish_follows_the_country() -> None:
    assert spanish_for("ES") == "es-ES" and spanish_for("MX") == "es-MX"
    assert spanish_for(None) == "es-ES"
    assert family("en") == "en" and family("es-MX") == "es" and family(None) == "es"


@pytest.mark.parametrize(
    ("stored", "country", "got", "first", "target", "reason"),
    [
        # the first typed message
        ("es-MX", "MX", reading("en", 1.0), True, "en", "first_message"),
        ("es-MX", "MX", reading("es", 1.0), True, None, "first_message"),
        (None, "MX", reading("es", 0.9), True, "es-MX", "first_message"),
        ("es-ES", "MX", reading("es", 0.9), True, "es-MX", "first_message"),
        ("es-MX", "MX", reading("en", 0.6), True, None, "low_confidence"),
        ("es-MX", "MX", reading("unclear", 1.0), True, None, "unclear"),
        ("es-MX", "MX", reading("other", 1.0), True, None, "other"),
        # later messages: both the Choice and the switch Noul must agree
        ("en", "MX", reading("es", 0.99, 0.96), False, "es-MX", "switch"),
        ("en", "ES", reading("es", 0.99, 0.96), False, "es-ES", "switch"),
        ("es-MX", "MX", reading("en", 0.99, 0.96), False, "en", "switch"),
        ("en", "MX", reading("es", 1.0, 0.84), False, None, "not_a_switch"),
        ("en", "MX", reading("es", 1.0, 0.04), False, None, "not_a_switch"),
        ("en", "MX", reading("es", 0.59, 0.99), False, None, "low_confidence"),
        ("en", "MX", reading("unclear", 0.99, 0.99), False, None, "unclear"),
        ("en", "MX", reading("other", 0.99, 0.99), False, None, "other"),
        ("es-MX", "MX", reading("es", 1.0, 0.0), False, None, "same_language"),
        ("en", "MX", reading("en", 1.0, 0.0), False, None, "same_language"),
    ],
)
def test_language_decisions(stored, country, got, first, target, reason) -> None:
    decision = decide_language(stored, country, got, first=first)
    assert (decision.target, decision.reason) == (target, reason)


@pytest.mark.parametrize(
    ("stored", "country", "got", "first", "target", "reason"),
    [
        # "me preguntas en inglés porfi": written in Spanish, asks for English
        (
            "es-ES",
            "ES",
            reading("es", 1.0, 0.15, asks="en", asks_confidence=0.97),
            False,
            "en",
            "request",
        ),
        (
            "es-MX",
            "MX",
            reading("es", 1.0, 0.04, asks="en", asks_confidence=0.9),
            False,
            "en",
            "request",
        ),
        # "can we do this in Spanish": written in English, asks for the country's Spanish
        (
            "en",
            "ES",
            reading("en", 1.0, 0.1, asks="es", asks_confidence=0.95),
            False,
            "es-ES",
            "request",
        ),
        (
            "en",
            "MX",
            reading("en", 1.0, 0.1, asks="es", asks_confidence=0.95),
            False,
            "es-MX",
            "request",
        ),
        # the request wins over the written language, even a full sentence in the other one
        (
            "es-MX",
            "MX",
            reading("es", 1.0, 0.01, asks="en", asks_confidence=0.9),
            False,
            "en",
            "request",
        ),
        (
            "en",
            "MX",
            reading("en", 1.0, 0.01, asks="es", asks_confidence=0.9),
            False,
            "es-MX",
            "request",
        ),
        # ... and on the first message
        (
            "es-MX",
            "MX",
            reading("es", 1.0, 0.0, asks="en", asks_confidence=0.9),
            True,
            "en",
            "request",
        ),
        (
            "en",
            "ES",
            reading("en", 1.0, 0.0, asks="es", asks_confidence=0.9),
            True,
            "es-ES",
            "request",
        ),
        # a request for the language already stored changes nothing but a stale variant
        (
            "en",
            "MX",
            reading("en", 1.0, 0.0, asks="en", asks_confidence=0.99),
            False,
            None,
            "request_same",
        ),
        (
            "es-MX",
            "MX",
            reading("es", 1.0, 0.0, asks="es", asks_confidence=0.99),
            False,
            None,
            "request_same",
        ),
        (
            "es-ES",
            "MX",
            reading("es", 1.0, 0.0, asks="es", asks_confidence=0.99),
            False,
            "es-MX",
            "request_same",
        ),
        (
            None,
            "MX",
            reading("es", 1.0, 0.0, asks="es", asks_confidence=0.99),
            False,
            None,
            "request_same",
        ),
        # a mention is not a request: the written-language rules apply as before
        (
            "es-ES",
            "ES",
            reading("es", 1.0, 0.04, asks="none", asks_confidence=0.99),
            False,
            None,
            "same_language",
        ),
        ("es-ES", "ES", reading("es", 1.0, 0.04), False, None, "same_language"),
        # a request below the threshold is no request (the written language decides)
        (
            "es-ES",
            "ES",
            reading("es", 1.0, 0.04, asks="en", asks_confidence=0.79),
            False,
            None,
            "same_language",
        ),
        (
            "es-ES",
            "ES",
            reading("es", 1.0, 0.04, asks="en", asks_confidence=0.5),
            False,
            None,
            "same_language",
        ),
        # one word never switches: a lone "English" is not a request, and an English word in a
        # Spanish chat with no request keeps the stored language
        (
            "es-ES",
            "ES",
            reading("en", 0.9, 0.2, asks="none", asks_confidence=0.9),
            False,
            None,
            "not_a_switch",
        ),
        (
            "es-ES",
            "ES",
            reading("en", 0.9, 0.2, asks="en", asks_confidence=0.4),
            False,
            None,
            "not_a_switch",
        ),
        # the written-language switch still works with no request
        ("en", "MX", reading("es", 0.99, 0.96, asks="none"), False, "es-MX", "switch"),
    ],
)
def test_an_explicit_language_request_wins_over_the_written_language(
    stored, country, got, first, target, reason
) -> None:
    decision = decide_language(stored, country, got, first=first)
    assert (decision.target, decision.reason) == (target, reason)


def test_the_request_threshold_sits_at_the_calibrated_value() -> None:
    assert THRESHOLDS.ask_confidence == 0.80
    at = reading("es", 1.0, 0.0, asks="en", asks_confidence=THRESHOLDS.ask_confidence)
    assert decide_language("es-ES", "ES", at, first=False).target == "en"


def test_a_stale_spanish_variant_is_corrected_to_the_countrys() -> None:
    decision = decide_language("es-ES", "MX", reading("es", 1.0), first=False)
    assert decision.target == "es-MX"


@pytest.mark.parametrize(
    ("signals", "hint", "fired", "escalation"),
    [
        ({}, None, (), None),
        (
            {"frustrated": 0.79, "confused": 0.74, "wants_human": 0.69, "abusive": 0.84},
            None,
            (),
            None,
        ),
        ({"frustrated": 0.8}, "apologise", ("frustrated",), None),
        ({"confused": 0.75}, "simpler", ("confused",), None),
        ({"wants_human": 0.7}, "offer_person", ("wants_human",), None),
        ({"wants_human": 0.85}, "escalated", ("wants_human",), "wants_human"),
        ({"abusive": 0.85, "frustrated": 0.9}, "calm", ("abusive", "frustrated"), "abuse"),
        (
            {"wants_human": 0.99, "frustrated": 0.9, "confused": 0.8},
            "escalated",
            ("wants_human", "frustrated", "confused"),
            "wants_human",
        ),
        ({"frustrated": 0.9, "confused": 0.9}, "apologise", ("frustrated", "confused"), None),
    ],
)
def test_tone_decisions(signals, hint, fired, escalation) -> None:
    tone = decide_tone(reading(**signals))
    assert (tone.hint, tone.signals, tone.escalation) == (hint, fired, escalation)


def test_every_hint_the_policy_can_give_has_text_for_the_agent() -> None:
    codes = {
        decide_tone(reading(**{name: 0.99})).hint
        for name in ("frustrated", "confused", "wants_human", "abusive")
    } | {decide_tone(reading(wants_human=0.75)).hint}
    assert codes == set(TONE_HINT_TEXT)
    assert THRESHOLDS.wants_human_hint < THRESHOLDS.wants_human_escalate


@pytest.mark.parametrize(
    "base",
    [
        reading("en", 0.99, 0.96, asks="es", asks_confidence=0.95),
        reading("es", 0.99, 0.96, frustrated=0.9, confused=0.8),
        reading("es", 1.0, 0.02, wants_human=0.9, abusive=0.9, unreadable=0.9),
        reading("unclear", 0.9, 0.0, unreadable=0.1),
    ],
)
def test_the_decisions_ignore_intent_and_malice(base) -> None:
    """ADR 0071: two readings that differ only in the three new fields decide the same."""
    calm = dataclasses.replace(base, intent="answering", intent_confidence=0.9, malice=0.02)
    worst = dataclasses.replace(base, intent="steering", intent_confidence=1.0, malice=1.0)
    none = dataclasses.replace(base, intent=None, intent_confidence=None, malice=None)
    assert calm != worst  # they differ in the new fields, and only there
    assert (
        dataclasses.replace(worst, intent="answering", intent_confidence=0.9, malice=0.02) == calm
    )
    for stored, country, first in (("es-MX", "MX", True), ("en", "ES", False), (None, None, True)):
        decisions = {decide_language(stored, country, r, first=first) for r in (calm, worst, none)}
        assert len(decisions) == 1, (stored, country, first)
    assert len({decide_tone(r) for r in (calm, worst, none)}) == 1
    for text in ("asdkj qwe lkjh", "Hola, busco trabajo", "12345", "👍"):
        assert len({policy.is_unreadable(text, r) for r in (calm, worst, none)}) == 1, text
        states = {policy.unreadable_state(text, r.unreadable) for r in (calm, worst, none)}
        assert len(states) == 1, text


def test_the_thresholds_gain_no_field_for_intent_or_malice() -> None:
    names = [field.name for field in dataclasses.fields(THRESHOLDS)]
    assert len(names) == 12 and THRESHOLDS.version == "jev-1.13-v3"
    assert not [n for n in names if "intent" in n or "malice" in n or "manipulation" in n]
