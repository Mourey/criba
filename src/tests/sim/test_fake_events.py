"""The fake web chat writes the record shapes the backend writes (events and Knockout message).

`sim.fake_webchat` stands in for the backend in driver tests, so its `events` must have the
shapes `criba.db.repo` and `criba.tools` write, as `contracts/api.openapi.yaml` types them
(`EventData_<kind>`). Every persona is played through the fake, plus a text Zone re-ask, and each
event's `data` is validated against its schema. The backend stopped storing Candidate free text in
events (Q38), so no event may carry a message body or `raw_input`; they cite `message_id` instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from sim.clock import SimClock
from sim.driver import PersonaDriver
from sim.fake_webchat import FakeWebChat
from sim.personas import load_personas

REPO = Path(__file__).resolve().parents[3]
API = yaml.safe_load((REPO / "contracts" / "api.openapi.yaml").read_text("utf-8"))
SCHEMAS: dict[str, Any] = API["components"]["schemas"]
PERSONAS = load_personas()

# Candidate words never enter an event (Q38).
FREE_TEXT_KEYS = {"raw_input", "message", "body", "text", "note"}


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": API["components"]}
    )


def play(persona_id: str, **fake_kwargs: Any) -> dict[str, Any]:
    fake = FakeWebChat(**fake_kwargs)
    persona = PERSONAS[persona_id]

    async def go() -> None:
        http = fake.client()
        driver = PersonaDriver(http, clock=SimClock(http), poll_wait_s=1, settle_s=0)
        async with http:
            await driver.run(persona)

    asyncio.run(go())
    return fake.application_detail(persona.identity.phone)


RUNS: dict[str, dict[str, Any]] = {}


def detail_of(key: str) -> dict[str, Any]:
    """Play each scenario once for the whole module: `persona_id` or `persona_id:text-zone`."""
    if key not in RUNS:
        persona_id, _, mode = key.partition(":")
        RUNS[key] = play(persona_id, **({"zone_clarification": "text"} if mode else {}))
    return RUNS[key]


SCENARIOS = [*sorted(PERSONAS), "no_license_mx:text-zone"]


def events_of(detail: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [e for e in detail["events"] if e["kind"] == kind]


# ---- every event the fake writes fits its contract schema -----------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_event_payload_fits_its_contract_schema(scenario):
    detail = detail_of(scenario)
    assert detail["events"]
    for event in detail["events"]:
        schema = f"EventData_{event['kind']}"
        assert schema in SCHEMAS, f"no {schema} in the contract"
        errors = sorted(validator(schema).iter_errors(event["data"]), key=lambda e: e.path)
        assert not errors, f"{event['kind']} {event['data']}: {errors[0].message}"


def test_the_scenarios_cover_the_kinds_the_fake_writes():
    kinds = {e["kind"] for s in SCENARIOS for e in detail_of(s)["events"]}
    assert kinds >= {
        "application_started",
        "answer_recorded",
        "answer_invalid",
        "answer_ambiguous",
        "screening_finished",
        "knockout",
        "reopened",
        "review_requested",
        "withdrawn",
        "opted_out",
        "stalled",
        "nudge_sent",
        "template_sent",
        "abandoned",
    }


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_no_event_carries_candidate_free_text(scenario):
    for event in detail_of(scenario)["events"]:
        assert not FREE_TEXT_KEYS & set(event["data"]), event

    # The Candidate's name is not in any event either.
    detail = detail_of(scenario)
    name = detail["candidate"]["full_name"]
    if name:
        assert name not in str(detail["events"])


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_message_ids_in_events_name_real_messages(scenario):
    detail = detail_of(scenario)
    messages = {m["id"]: m for m in detail["messages"]}
    for event in detail["events"]:
        message_id = event["data"].get("message_id")
        if message_id is None:
            continue
        assert message_id in messages, event
        wanted = "outbound" if event["kind"] in ("knockout", "template_sent") else "inbound"
        assert messages[message_id]["direction"] == wanted, event


# ---- the shapes the task names --------------------------------------------------------------


def test_knockout_is_a_text_agent_message_that_the_event_points_at():
    detail = detail_of("no_license_mx")
    (event,) = events_of(detail, "knockout")
    data = event["data"]
    assert set(data) == {"reason", "template", "criterion", "stage", "message_id"}
    assert data["criterion"] == "driver_license" == detail["knockout_criterion"]
    assert data["reason"] == "no"
    assert data["template"] == "knockout_driver_license.es-MX"
    assert data["stage"] == "driver_license"
    message = next(m for m in detail["messages"] if m["id"] == data["message_id"])
    assert message["direction"] == "outbound" and message["sender"] == "agent"
    assert message["content_type"] == "text" and message["template_name"] is None
    assert "http" in message["body"]
    assert not [m for m in detail["messages"] if m["content_type"] == "template"]
    assert not events_of(detail, "template_sent")


def test_zone_knockout_names_the_zone_criterion_and_reason():
    detail = detail_of("zone_outside_es")
    first = events_of(detail, "knockout")[0]["data"]
    assert first["criterion"] == "zone"
    assert first["reason"] == "outside_service_areas"
    assert first["template"] == "knockout_zone.es-ES"
    assert first["stage"] == "zone"


def test_a_knockout_is_followed_by_screening_finished_with_its_result():
    detail = detail_of("no_license_mx")
    kinds = [e["kind"] for e in detail["events"]]
    assert kinds.index("screening_finished") == kinds.index("knockout") + 1
    (finished,) = events_of(detail, "screening_finished")
    assert finished["data"] == {"result": "knockout"}


def test_a_qualified_screening_finishes_with_a_qualified_result():
    detail = detail_of("qualified_es")
    (finished,) = events_of(detail, "screening_finished")
    assert finished["data"] == {"result": "qualified"}


def test_withdrawn_and_abandoned_close_with_their_screening_result():
    withdrawn = detail_of("withdrawn_mx")
    assert events_of(withdrawn, "withdrawn")[0]["data"]["stage"]
    assert [e["data"] for e in events_of(withdrawn, "screening_finished")] == [
        {"result": "withdrawn"}
    ]
    abandoned = detail_of("stalled_abandoned_es")
    assert events_of(abandoned, "abandoned")[0]["data"]["nudges_sent"] == 3
    assert [e["data"] for e in events_of(abandoned, "screening_finished")] == [
        {"result": "abandoned"}
    ]
    assert [e["data"]["step"] for e in events_of(abandoned, "nudge_sent")] == [1, 2, 3]
    assert [e["data"]["kind"] for e in events_of(abandoned, "nudge_sent")] == [
        "nudge",
        "last_nudge",
        "template",
    ]


def test_answer_recorded_cites_the_message_and_keeps_the_name_and_raw_words_out():
    detail = detail_of("qualified_es")
    recorded = events_of(detail, "answer_recorded")
    assert [e["data"]["field"] for e in recorded][:3] == [
        "full_name",
        "zone",
        "driver_license",
    ]
    for event in recorded:
        data = event["data"]
        assert "raw_input" not in data and "status" not in data
        assert isinstance(data.get("message_id"), int)
        if data["field"] == "full_name":
            assert "value" not in data
        else:
            assert "value" in data
    zone = next(e["data"] for e in recorded if e["data"]["field"] == "zone")
    assert zone["value"] == detail["answers"]["zone"]["id"]


def test_answer_ambiguous_lists_the_options_and_cites_the_message():
    detail = detail_of("no_license_mx")
    (event,) = events_of(detail, "answer_ambiguous")
    data = event["data"]
    assert set(data) == {"field", "options", "message_id"}
    assert data["field"] == "zone"
    assert all(set(o) == {"id", "label"} for o in data["options"])
    assert len(data["options"]) >= 2


def test_answer_invalid_counts_attempts_and_cites_the_message():
    detail = detail_of("invalid_then_corrected_es")
    invalid = events_of(detail, "answer_invalid")
    assert len(invalid) == 3
    for event in invalid:
        assert set(event["data"]) == {"field", "attempts", "message_id"}
        assert event["data"]["attempts"] >= 1


def test_a_nudge_template_is_announced_by_template_sent():
    detail = detail_of("stalled_abandoned_es")
    (sent,) = events_of(detail, "template_sent")
    assert sent["data"]["template"] == "stalled_resume"
    message = next(m for m in detail["messages"] if m["id"] == sent["data"]["message_id"])
    assert message["content_type"] == "template"
    assert message["template_name"] == "stalled_resume"
