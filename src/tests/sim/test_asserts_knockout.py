"""Assertion helpers against the record the real backend writes for a Knockout (and a Qualified).

The records here are shaped like the backend's, not like the fake web chat's:

- `repo.close_application` writes the `knockout` event with `data` = `{reason, template,
  criterion, stage}` (`criba.tools.application._knock_out`) and then a
  `screening_finished` event with `{"result": "knockout"}`; the channel adds `message_id`
  once the agent's reply is sent (`repo.link_knockout_message`);
- `repo.mark_qualified` writes `screening_finished` with `{"result": "qualified"}`;
- the Knockout text goes out as an ordinary agent message (content type `text`, no template
  name), the one `data.message_id` names, built by `criba.tools.knockout.knockout_message`
  (imported here, so names and texts are the backend's own);
- events and messages have the API's shape (`api/present.py`: `event_json`, `message_json`).
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from criba.tools.knockout import REQUIREMENTS_URL, TEMPLATES, knockout_message
from sim import asserts
from sim.personas import load_personas

PERSONAS = load_personas()
T0 = datetime(2026, 10, 9, 20, 0, 0, tzinfo=UTC)

GREETING = {
    "es-MX": "¡Hola! Soy el asistente virtual de Grupo Sazón. ¿Cómo te llamas?",
    "es-ES": "¡Hola! Soy el asistente virtual de Grupo Sazón. ¿Cómo te llamas?",
    "en": "Hi! I am the virtual assistant of Grupo Sazón. What is your name?",
}


# The seeded Puebla Zones as `_ambiguous` offers them: sorted by name, labelled "Name (City)".
PUEBLA_OPTIONS = [
    {"id": "zone_mx_puebla_angelopolis", "label": "Angelópolis (Puebla)"},
    {"id": "zone_mx_puebla_centro", "label": "Centro (Puebla)"},
    {"id": "zone_mx_puebla_la_paz", "label": "La Paz (Puebla)"},
]
PUEBLA_CENTRO = PUEBLA_OPTIONS[1]


class Record:
    """Builds an ApplicationDetail the way the API returns it, one second per step."""

    def __init__(self, *, tick: float = 1.0) -> None:
        self.events: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.tick = tick
        self._t = 0.0

    def _stamp(self) -> str:
        self._t += self.tick
        return (T0 + timedelta(seconds=self._t)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def event(self, kind: str, data: dict[str, Any], actor: str = "agent") -> None:
        self.events.append(
            {
                "id": len(self.events) + 1,
                "kind": kind,
                "actor": actor,
                "user_name": None,
                "data": data,
                "created_at": self._stamp(),
            }
        )

    def message(
        self,
        direction: str,
        body: str,
        *,
        sender: str | None = None,
        content_type: str = "text",
        template_name: str | None = None,
        interactive: dict[str, Any] | None = None,
        reply_option_id: str | None = None,
    ) -> None:
        self.messages.append(
            {
                "id": len(self.messages) + 1,
                "direction": direction,
                "sender": sender or ("candidate" if direction == "inbound" else "agent"),
                "sender_name": None,
                "content_type": content_type,
                "body": body,
                "interactive": interactive,
                "reply_option_id": reply_option_id,
                "template_name": template_name,
                "media_type": None,
                "created_at": self._stamp(),
            }
        )

    def detail(self, **fields: Any) -> dict[str, Any]:
        base = {
            "id": "app_1",
            "candidate": {
                "id": "cand_1",
                "phone": "+525500000002",
                "language": None,
                "opted_out": False,
            },
            "answers": {},
            "summary": None,
            "events": self.events,
            "messages": self.messages,
        }
        return copy.deepcopy({**base, **fields})


def knockout_record(
    *,
    language: str = "es-MX",
    reason: str = "no",
    body: str | None = None,
    tick: float = 1.0,
    template_name: str | None = None,
    extra_agent_lines: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The no_license_mx run as the backend records it."""
    template, text = knockout_message("driver_license", reason, language, first_name="Luis")
    text = body if body is not None else text
    r = Record(tick=tick)
    r.event("application_started", {"channel": "webchat"}, actor="candidate")
    r.message("inbound", "Hola, quiero trabajar de repartidor")
    r.message("outbound", GREETING[language])
    r.message("inbound", "Luis Hernández")
    r.event("answer_recorded", {"field": "full_name", "value": "Luis Hernández"})
    r.message("outbound", "¿En qué zona o ciudad vives?")
    r.message("inbound", "Puebla")
    # "Puebla" names three seeded Zones: `record_answer` returns needs_choice and writes
    # `answer_ambiguous` {field, options, raw_input}; the options go out as buttons.
    r.event(
        "answer_ambiguous",
        {"field": "zone", "options": PUEBLA_OPTIONS, "raw_input": "Puebla"},
    )
    r.message(
        "outbound",
        "Hay varias zonas en Puebla. ¿Cuál es la tuya?",
        content_type="interactive",
        interactive={"style": "buttons", "options": PUEBLA_OPTIONS},
    )
    r.message(
        "inbound",
        "Centro (Puebla)",
        content_type="button_reply",
        reply_option_id=PUEBLA_CENTRO["id"],
    )
    r.event(
        "answer_recorded",
        {
            "field": "zone",
            "value": PUEBLA_CENTRO["id"],
            "raw_input": PUEBLA_CENTRO["id"],
            "locations": ["loc_mx_puebla_centro"],
        },
    )
    r.message("outbound", "¿Tienes licencia de manejo vigente?")
    r.message("inbound", "No, todavía no tengo licencia de manejo")
    r.event("answer_recorded", {"field": "driver_license", "value": False})
    # The Knockout reply is sent after the extra lines; the event names it by id.
    knockout_message_id = len(r.messages) + len(extra_agent_lines) + 1
    r.event(
        "knockout",
        {
            "reason": reason,
            "template": template,
            "message_id": knockout_message_id,
            "criterion": "driver_license",
            "stage": "driver_license",
        },
    )
    r.event("screening_finished", {"result": "knockout"})
    for line in extra_agent_lines:
        r.message("outbound", line)
    r.message("outbound", text, template_name=template_name)
    r.message("inbound", "revisión")
    r.event("review_requested", {"queue_item_id": "q_1", "reason": None}, actor="candidate")
    r.message("outbound", "Hecho: una persona del equipo revisará tu caso.")
    return r.detail(
        stage="driver_license",
        status="closed",
        outcome="knockout",
        knockout_criterion="driver_license",
        answers={
            "full_name": "Luis Hernández",
            "zone": {"id": PUEBLA_CENTRO["id"], "name": "Centro"},
            "driver_license": False,
        },
    )


def qualified_record() -> dict[str, Any]:
    """The qualified_es run: `finish_screening` writes `screening_finished` (qualified)."""
    r = Record()
    r.event("application_started", {"channel": "webchat"}, actor="candidate")
    r.message("inbound", "Hola, busco trabajo de repartidora")
    r.message("outbound", GREETING["es-ES"])
    for field, value in (
        ("full_name", "Ana García"),
        ("zone", "zone_chamberi"),
        ("driver_license", True),
        ("availability", ["full_time"]),
        ("preferred_schedule", ["afternoon"]),
        ("experience_years", 2),
        ("experience_platforms", ["glovo", "uber_eats"]),
        ("start_date", "2026-10-14"),
    ):
        r.event("answer_recorded", {"field": field, "value": value})
    r.event("stage_changed", {"from": "confirm", "to": "qualified"})
    r.event("screening_finished", {"result": "qualified"})
    r.event("approval_requested", {"queue_item_id": "q_2", "reason": None}, actor="agent")
    return r.detail(
        stage="qualified",
        status="open",
        outcome=None,
        knockout_criterion=None,
        answers={
            "full_name": "Ana García",
            "zone": {"id": "zone_chamberi", "name": "Madrid, Chamberí"},
            "driver_license": True,
            "availability": ["full_time"],
            "preferred_schedule": ["afternoon"],
            "experience_years": 2,
            "experience_platforms": ["glovo", "uber_eats"],
            "start_date": "2026-10-14",
        },
    )


# ---- the Knockout persona passes on the backend's record ---------------------------------


def test_knockout_record_passes_assert_persona_for_a_knockout_persona():
    persona = PERSONAS["no_license_mx"]
    app = knockout_record()
    assert not asserts.check_persona(persona, app)
    asserts.assert_persona(persona, app)


def test_knockout_record_passes_when_the_clock_is_frozen_and_every_timestamp_ties():
    persona = PERSONAS["no_license_mx"]
    asserts.assert_persona(persona, knockout_record(tick=0.0))


def test_knockout_does_not_infer_finish_screening_but_still_infers_record_answer():
    app = knockout_record()
    assert "screening_finished" in asserts.event_kinds(app)
    assert not asserts.finished_qualified(app)
    tools = asserts.inferred_tools(app)
    assert "finish_screening" not in tools
    assert "record_answer" in tools
    assert any(
        "forbidden finish_screening" in f
        for f in asserts.check_tools(app, PERSONAS["no_license_mx"].expect, ["finish_screening"])
    )


@pytest.mark.parametrize("result", ["knockout", "abandoned", "withdrawn"])
def test_a_closing_screening_finished_is_never_a_finish_screening_call(result):
    app = Record()
    app.event("application_started", {"channel": "webchat"}, actor="candidate")
    app.event("screening_finished", {"result": result})
    detail = app.detail(outcome=result)
    assert "finish_screening" not in asserts.inferred_tools(detail)


def test_without_a_result_the_outcome_and_the_knockout_event_decide():
    bare = knockout_record()
    for event in bare["events"]:
        if event["kind"] == "screening_finished":
            event["data"] = {}
    assert "finish_screening" not in asserts.inferred_tools(bare)

    qualified = qualified_record()
    for event in qualified["events"]:
        if event["kind"] == "screening_finished":
            event["data"] = {}
    assert "finish_screening" in asserts.inferred_tools(qualified)


def test_fallback_an_older_screening_finished_names_the_stage_not_a_result():
    """The fake web chat used to write `{"stage": "qualified"}`; it is still read that way."""
    qualified = qualified_record()
    for event in qualified["events"]:
        if event["kind"] == "screening_finished":
            event["data"] = {"stage": "qualified"}
    assert asserts.finished_qualified(qualified)
    assert "finish_screening" in asserts.inferred_tools(qualified)


def test_forbidding_screening_finished_means_not_qualified():
    expect = PERSONAS["no_license_mx"].expect
    assert "screening_finished" in expect.forbidden_events
    assert not asserts.check_events(knockout_record(), expect)
    assert any(
        "forbidden screening_finished" in f
        for f in asserts.check_events(qualified_record(), expect)
    )


# ---- a Qualified record still infers finish_screening ------------------------------------


def test_qualified_record_still_infers_finish_screening_and_passes_its_persona():
    persona = PERSONAS["qualified_es"]
    app = qualified_record()
    assert asserts.finished_qualified(app)
    assert {"record_answer", "finish_screening"} <= asserts.inferred_tools(app)
    assert not asserts.check_persona(persona, app)
    assert any(
        "forbidden finish_screening" in f
        for f in asserts.check_tools(
            app,
            PERSONAS["no_license_mx"].expect,
            None,
        )
    )


def test_qualified_record_that_never_finished_fails_the_expected_tool():
    app = qualified_record()
    app["events"] = [e for e in app["events"] if e["kind"] != "screening_finished"]
    failures = asserts.check_tools(app, PERSONAS["qualified_es"].expect)
    assert any("expected a finish_screening call" in f for f in failures)


# ---- the Knockout message, found where the backend stores it -------------------------------


def test_knockout_message_is_the_text_agent_reply_after_the_knockout_event():
    app = knockout_record()
    reply, message = asserts._knockout_reply(app)
    assert len(reply) == 1
    assert message is not None
    assert message["content_type"] == "text"
    assert message["template_name"] is None
    assert REQUIREMENTS_URL in message["body"]
    knockout = next(e for e in app["events"] if e["kind"] == "knockout")
    assert message["id"] == knockout["data"]["message_id"]
    assert "message" not in knockout["data"]


def test_the_anchor_is_the_message_id_not_the_body():
    """The Knockout reply is found by id even when its body was edited or paraphrased."""
    app = knockout_record()
    knockout = next(e for e in app["events"] if e["kind"] == "knockout")
    for m in app["messages"]:
        if m["id"] == knockout["data"]["message_id"]:
            m["body"] = "Gracias, Luis. Necesitamos licencia: " + REQUIREMENTS_URL
    reply, message = asserts._knockout_reply(app)
    assert message is not None and message["id"] == knockout["data"]["message_id"]
    assert len(reply) == 1


def test_an_older_record_with_the_text_anchors_on_the_body():
    """Records written before Q38 carry `data.message` and no `message_id`."""
    app = knockout_record()
    _, text = knockout_message("driver_license", "no", "es-MX", first_name="Luis")
    for event in app["events"]:
        if event["kind"] == "knockout":
            del event["data"]["message_id"]
            event["data"]["message"] = text
    reply, message = asserts._knockout_reply(app)
    assert len(reply) == 1 and message is not None and message["body"] == text
    assert not asserts.check_knockout_template(app, PERSONAS["no_license_mx"].expect)


def test_language_comes_from_the_template_name_in_the_knockout_event():
    app = knockout_record(language="es-MX")
    _, message = asserts._knockout_reply(app)
    assert asserts._template_language(app, message) == "es-MX"
    for event in app["events"]:
        if event["kind"] == "knockout":
            event["data"]["template"] = "knockout_driver_license.es-ES"
    assert asserts._template_language(app, message) == "es-ES"


def test_language_falls_back_to_event_language_then_to_the_detector():
    app = knockout_record(language="es-MX")
    _, message = asserts._knockout_reply(app)
    for event in app["events"]:
        if event["kind"] == "knockout":
            del event["data"]["template"]
            event["data"]["language"] = "es-ES"
    assert asserts._template_language(app, message) == "es-ES"
    for event in app["events"]:
        if event["kind"] == "knockout":
            del event["data"]["language"]
    assert asserts._template_language(app, message) == "es-MX"  # "licencia de manejo"


def test_a_paraphrased_reply_is_still_found_after_the_event():
    app = knockout_record()
    for event in app["events"]:
        if event["kind"] == "knockout":
            del event["data"]["message_id"]
            event["data"]["message"] = "texto distinto"
    # No message_id, and the agent's text does not match data.message; the first agent
    # message after the event (by time) is the reply.
    reply, message = asserts._knockout_reply(app)
    assert len(reply) == 1 and message is reply[0]
    assert not asserts.check_knockout_template(app, PERSONAS["no_license_mx"].expect)


def test_a_template_message_named_knockout_is_found_as_well():
    app = knockout_record(template_name="knockout_driver_license.es-MX")
    for m in app["messages"]:
        if m["template_name"]:
            m["content_type"] = "template"
    for event in app["events"]:
        if event["kind"] == "knockout":
            del event["data"]["message_id"]
    assert not asserts.check_knockout_template(app, PERSONAS["no_license_mx"].expect)


# ---- failures -----------------------------------------------------------------------------


def test_wrong_language_knockout_fails():
    persona = PERSONAS["no_license_mx"]
    app = knockout_record(language="en")
    failures = asserts.check_knockout_template(app, persona.expect)
    assert any("expected es-MX, got en" in f for f in failures)
    assert asserts.check_persona(persona, app)  # and so does the whole persona
    with pytest.raises(AssertionError, match="expected es-MX, got en"):
        asserts.assert_persona(persona, app)


def test_wrong_regional_spanish_fails():
    app = knockout_record(language="es-ES")
    failures = asserts.check_knockout_template(app, PERSONAS["no_license_mx"].expect)
    assert any("expected es-MX, got es-ES" in f for f in failures)


def test_spanish_name_over_an_english_body_fails_on_the_prose():
    _, english = knockout_message("driver_license", "no", "en")
    app = knockout_record(language="es-MX", body=english)
    failures = asserts.check_knockout_template(app, PERSONAS["no_license_mx"].expect)
    assert any("reads as English" in f for f in failures)


def test_knockout_with_an_emoji_fails():
    _, text = knockout_message("driver_license", "no", "es-MX")
    app = knockout_record(body=text + " 🛵")
    failures = asserts.check_knockout_template(app, PERSONAS["no_license_mx"].expect)
    assert any("emoji" in f for f in failures)
    assert asserts.check_persona(PERSONAS["no_license_mx"], app)


def test_knockout_without_the_requirements_link_fails():
    _, text = knockout_message("driver_license", "no", "es-MX")
    app = knockout_record(body=text.replace(REQUIREMENTS_URL, "pronto"))
    failures = asserts.check_knockout_template(app, PERSONAS["no_license_mx"].expect)
    assert any("no link" in f for f in failures)


def test_knockout_split_over_two_messages_fails():
    app = knockout_record(extra_agent_lines=("Lo siento mucho.",))
    failures = asserts.check_knockout_template(app, PERSONAS["no_license_mx"].expect)
    assert any("expected one message, got 2" in f for f in failures)


def test_knockout_sent_twice_fails():
    app = knockout_record()
    knock = next(m for m in app["messages"] if m["body"].startswith("Gracias"))
    twin = {**knock, "id": knock["id"] + 0.5}  # sent right after it, in the same reply
    app["messages"].insert(app["messages"].index(knock) + 1, twin)
    failures = asserts.check_knockout_template(app, PERSONAS["no_license_mx"].expect)
    assert any("expected one message, got 2" in f for f in failures)


def test_no_agent_message_after_the_knockout_fails():
    app = knockout_record()
    app["messages"] = [m for m in app["messages"] if not m["body"].startswith("Gracias")]
    failures = asserts.check_knockout_template(app, PERSONAS["no_license_mx"].expect)
    assert any("no agent message after the knockout" in f for f in failures)


def test_no_knockout_at_all_fails_when_a_template_is_expected():
    failures = asserts.check_knockout_template(qualified_record(), PERSONAS["no_license_mx"].expect)
    assert failures == ["knockout template: no knockout happened"]


def test_template_for_the_wrong_criterion_fails():
    app = knockout_record()
    for event in app["events"]:
        if event["kind"] == "knockout":
            event["data"]["template"] = "knockout_zone.es-MX"
    failures = asserts.check_knockout_template(app, PERSONAS["no_license_mx"].expect)
    assert any("is for zone, expected driver_license" in f for f in failures)


# ---- the naming rule ------------------------------------------------------------------------


def _all_variants() -> list[tuple[str, str, str]]:
    from criba.tools import knockout

    return [
        (criterion, reason, language)
        for (criterion, reason) in knockout._VARIANT
        for language in TEMPLATES
    ]


@pytest.mark.parametrize(("criterion", "reason", "language"), _all_variants())
def test_every_backend_template_name_fits_the_naming_rule(criterion, reason, language):
    name, text = knockout_message(criterion, reason, language)
    parsed = asserts.parse_knockout_template(name)
    assert parsed is not None, name
    assert parsed["criterion"] == criterion
    assert parsed["locale"] == language
    assert asserts.template_locale(name) == language
    assert REQUIREMENTS_URL in text


def test_naming_rule_examples_and_rejects():
    parse = asserts.parse_knockout_template
    assert parse("knockout_zone.es-MX") == {
        "criterion": "zone",
        "reason": None,
        "locale": "es-MX",
    }
    assert parse("knockout_driver_license_pending.es-ES") == {
        "criterion": "driver_license",
        "reason": "pending",
        "locale": "es-ES",
    }
    assert parse("knockout_driver_license.en")["locale"] == "en"
    for bad in (
        "knockout_es-MX",
        "knockout_zone",
        "knockout_zone.fr",
        "nudge.es-MX",
        None,
    ):
        assert parse(bad) is None
    # The fake web chat's older spelling is still read for its language.
    assert asserts.template_locale("knockout_es-MX") == "es-MX"
    assert asserts.template_locale("knockout") is None


def test_a_malformed_template_name_in_the_record_is_reported():
    app = knockout_record()
    for event in app["events"]:
        if event["kind"] == "knockout":
            event["data"]["template"] = "knockout_driver_license.fr"
    failures = asserts.check_knockout_template(app, PERSONAS["no_license_mx"].expect)
    assert any("does not fit" in f for f in failures)


def test_recorded_order_reads_the_failed_field_from_answer_recorded_not_the_knockout():
    app = knockout_record()
    knockout = next(e for e in app["events"] if e["kind"] == "knockout")
    assert "field" not in knockout["data"]  # the backend names a `criterion`
    order = asserts.recorded_order(app)
    assert order == ["full_name", "zone", "driver_license"]
    # A `field` on a knockout event (the fake's old shape) is not read.
    for event in app["events"]:
        if event["kind"] == "answer_recorded" and event["data"]["field"] == "driver_license":
            event["data"] = {}
    knockout["data"]["field"] = "driver_license"
    assert "driver_license" not in asserts.recorded_order(app)
