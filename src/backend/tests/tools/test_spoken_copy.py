"""Fixed Candidate-facing copy on a spoken turn (owner decision 64).

A Knockout or Qualified close that a tool returns on a turn of a voice call carries the spoken
variant: no link, no chat, no typed instruction. A typed turn still gets the existing template.
"""

from __future__ import annotations

import re

import pytest

from criba.channel import copy
from criba.channel.copy import SPOKEN_QUALIFIED_CLOSE, qualified_close
from criba.channel.voice_rules import VOICE_TURN_RULES
from criba.db import repo
from criba.tools.knockout import (
    REQUIREMENTS_URL,
    SPOKEN_TEMPLATES,
    TEMPLATES,
    knockout_message,
)

from .conftest import GOOD_ANSWERS

# What the Candidate must never hear: the text flow (owner decision 64, the two demo calls).
FORBIDDEN = re.compile(
    r"chat|enlace|bot[oó]n|\bescrib\w*|por aquí|https?|www\.|revisión|responde|\bsistema\b"
    r"|[\U0001F300-\U0001FAFF☀-➿]",
    re.IGNORECASE,
)
CASES = [
    ("zone", "outside_service_areas"),
    ("driver_license", "no"),
    ("driver_license", "expired"),
    ("driver_license", "suspended"),
    ("driver_license", "pending"),
    ("driver_license", "something_new"),
]


@pytest.mark.parametrize("language", ["es-ES", "es-MX", "en"])
@pytest.mark.parametrize(("criterion", "reason"), CASES)
def test_spoken_knockout_has_no_link_and_no_text_instruction(criterion, reason, language) -> None:
    template, text = knockout_message(
        criterion, reason, language, first_name="Ana", zone="Milpa Alta", spoken=True
    )
    typed_template, typed = knockout_message(
        criterion, reason, language, first_name="Ana", zone="Milpa Alta"
    )
    assert template == typed_template and text != typed
    assert FORBIDDEN.search(text) is None, text
    assert REQUIREMENTS_URL not in text and "{" not in text
    assert "dímelo y una persona del equipo lo revisará" in text  # how to ask for a person
    assert ("legal" in text) or criterion == "zone"  # why the requirement exists
    assert "Ana" in text
    if criterion == "zone":
        assert "Milpa Alta" in text


def test_no_spoken_copy_points_to_the_text_flow() -> None:
    """The rules and every fixed line spoken on a call: no chat, link, button or written reply."""
    spoken = [
        VOICE_TURN_RULES,
        *copy.SPOKEN_OPT_OUT_CONFIRMATION.values(),
        *copy.SPOKEN_HANDOVER.values(),
        *copy.SPOKEN_QUALIFIED_CLOSE.values(),
        *copy.SPOKEN_DISCLOSURE.values(),
        *copy.SPOKEN_REASK.values(),
        *(t for table in SPOKEN_TEMPLATES.values() for t in table.values()),
    ]
    for text in spoken:
        assert FORBIDDEN.search(text) is None, (FORBIDDEN.search(text), text)
    assert set(copy.SPOKEN_OPT_OUT_CONFIRMATION) == set(copy.LANGUAGES)


def test_every_language_has_every_spoken_variant() -> None:
    assert set(SPOKEN_TEMPLATES) == set(TEMPLATES)
    for table in SPOKEN_TEMPLATES.values():
        assert set(table) == set(TEMPLATES["es-ES"])
    for text in SPOKEN_QUALIFIED_CLOSE.values():
        assert FORBIDDEN.search(text) is None, text


def _speak(tools, app_id: str, text: str) -> None:
    app = tools.app(app_id)
    repo.add_message(
        tools.conn,
        candidate_id=app["candidate_id"],
        application_id=app_id,
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body=text,
        modality="spoken",
        now=tools.clock.now(),
    )


def _knock_out_on_licence(tools, *, spoken: bool) -> dict:
    app_id = tools.new_app(country="MX", language="es-MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_cdmx_polanco"])
    if spoken:
        _speak(tools, app_id, "No tengo licencia")
    else:
        tools.say(app_id, "No tengo licencia")
    value = tools.ok(app_id, "record_answer", field="driver_license", value="no")
    assert value["status"] == "knockout"
    return value


def test_a_spoken_turn_knockout_sends_the_spoken_variant(tools) -> None:
    value = _knock_out_on_licence(tools, spoken=True)
    assert value["template"] == "knockout_driver_license.es-MX"
    _, expected = knockout_message("driver_license", "no", "es-MX", first_name="Luis", spoken=True)
    assert value["message"] == expected
    assert FORBIDDEN.search(value["message"]) is None, value["message"]


def test_a_typed_turn_knockout_still_sends_the_existing_template(tools) -> None:
    value = _knock_out_on_licence(tools, spoken=False)
    _, expected = knockout_message("driver_license", "no", "es-MX", first_name="Luis")
    assert value["message"] == expected
    assert REQUIREMENTS_URL in value["message"] and '"revisión"' in value["message"]


def test_the_qualified_close_is_spoken_on_a_spoken_turn(tools) -> None:
    app_id = tools.new_app(country="ES", language="es-ES")
    for field, answer in GOOD_ANSWERS["ES"]:
        tools.ok(app_id, "record_answer", field=field, value=answer)
    _speak(tools, app_id, "Sí, todo correcto")
    value = tools.ok(app_id, "finish_screening")
    assert value["message"] == qualified_close("es-ES", "ES", "Ana", spoken=True)
    assert FORBIDDEN.search(value["message"]) is None, value["message"]
    assert "se pondrá en contacto contigo" in value["message"]
