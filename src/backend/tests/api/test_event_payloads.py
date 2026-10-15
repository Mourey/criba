"""Event.data per kind: every payload the backend writes matches its contract schema.

The contract maps each ``Event.kind`` to ``EventData_<kind>`` through ``x-event-payloads``
on ``Event.data``. Each test drives real code paths (tools, the REST API, the scheduler,
the channel), then reads the Application back through ``GET /api/applications/{id}`` and
validates every served event against the schema for its kind. Free text (Q38) is in
neither storage nor the API: the payloads reference it by id, and the tests pin both sides.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from criba.api import present
from criba.channel.config import ChannelSettings
from criba.channel.copy import qualified_close
from criba.channel.router import APPROVED_TURN
from criba.channel.service import Channel
from criba.db import repo
from criba.db.connection import connect, iso
from criba.scheduler.jobs import run_flags, run_stalled
from criba.summary import SummaryWriter
from criba.tools import call_tool

from ..channel.conftest import FakeHermes, Fakes, make_run_client
from .support import (
    NOW,
    OPERATOR,
    PUEBLA,
    PUEBLA_2,
    PUEBLA_CENTRO,
    REPO_ROOT,
    UNCOVERED_ZONE,
    Env,
)

NEVER_WRITTEN = {"outreach_sent", "import_unresponsive"}

# Keys that once carried free text (the Candidate's words, or text quoting them). Since Q38
# no payload has them, stored or served: they are referenced by id instead.
FREE_TEXT = {
    "answer_recorded": {"raw_input"},
    "answer_invalid": {"raw_input"},
    "answer_ambiguous": {"raw_input"},
    "review_requested": {"note"},
    "escalation_raised": {"note"},
    "approval_requested": {"note"},
    "withdrawn": {"note"},
    "erasure_requested": {"note"},
    "knockout": {"message"},
    "takeover_handed_back": {"summary"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def schema_sql_kinds() -> set[str]:
    text = (REPO_ROOT / "contracts" / "schema.sql").read_text()
    match = re.search(r"CREATE TABLE events \(.*?kind IN \((.*?)\)\),", text, re.S)
    assert match, "events.kind CHECK not found in contracts/schema.sql"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def payload_map(contract) -> dict[str, str]:
    data = contract.spec["components"]["schemas"]["Event"]["properties"]["data"]
    return data["x-event-payloads"]


def payload_errors(contract, kind: str, data: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(
        {"$ref": payload_map(contract)[kind], "components": contract.spec["components"]},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    return [f"{kind} {list(e.path)}: {e.message}" for e in validator.iter_errors(data)]


def served_events(env: Env, contract, app_id: str) -> dict[str, list[dict[str, Any]]]:
    """The Application's events as the API serves them, each checked against its kind."""
    response = env.client(OPERATOR).get(f"/api/applications/{app_id}")
    body = contract.conforms(response, "getApplication")
    by_kind: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for event in body["events"]:
        errors += payload_errors(contract, event["kind"], event["data"])
        assert not FREE_TEXT.get(event["kind"], set()) & set(event["data"]), event
        by_kind.setdefault(event["kind"], []).append(event["data"])
    assert not errors, "\n".join(errors)
    for row in env.conn.execute(
        "SELECT kind, data FROM events WHERE application_id = ?", (app_id,)
    ).fetchall():
        stored_keys = set(json.loads(row["data"]))
        assert not FREE_TEXT.get(row["kind"], set()) & stored_keys, (row["kind"], stored_keys)
    return by_kind


def stored(env: Env, app_id: str, kind: str) -> list[dict[str, Any]]:
    rows = env.conn.execute(
        "SELECT data FROM events WHERE application_id = ? AND kind = ? ORDER BY id",
        (app_id, kind),
    ).fetchall()
    return [json.loads(r["data"]) for r in rows]


def tool(env: Env, app_id: str, name: str, **args: Any) -> dict[str, Any]:
    status, body = call_tool(env.conn, name, args, app_id, now=env.clock.now())
    assert status == 200, body
    return body


def tool_ok(env: Env, app_id: str, name: str, **args: Any) -> dict[str, Any]:
    body = tool(env, app_id, name, **args)
    assert body["ok"], body
    return body["value"]


def new_app(env: Env, *, country: str = "MX", channel: str = "webchat") -> dict[str, Any]:
    candidate, _ = repo.get_or_create_candidate(
        env.conn,
        client_id="grupo_sazon",
        channel=channel,
        external_id=uuid.uuid4().hex,
        country=country,
        language="es-MX" if country == "MX" else "es-ES",
        now=env.clock.now(),
    )
    return repo.start_application(env.conn, candidate["id"], channel, now=env.clock.now())


# (field, the Candidate's words, the structured value the agent passes; ADR 0042)
MX_ANSWERS: list[tuple[str, str, Any]] = [
    ("full_name", "Luis Hernández", "Luis Hernández"),
    ("zone", "Centro de Puebla", [PUEBLA_CENTRO]),
    ("driver_license", "sí tengo licencia", "yes"),
    ("availability", "tiempo completo", ["full_time"]),
    ("preferred_schedule", "mañana y tarde", ["morning", "afternoon"]),
    ("experience_years", "6 meses", 0.5),
    ("experience_platforms", "rappi", ["rappi"]),
    ("start_date", "mañana", "2026-10-10"),
]


def say(env: Env, app_id: str, text: str) -> int:
    """The Candidate writes ``text`` (an inbound message); returns its id."""
    app = repo.require_application(env.conn, app_id)
    message, _ = repo.add_message(
        env.conn,
        candidate_id=app["candidate_id"],
        application_id=app_id,
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body=text,
        now=env.clock.now(),
    )
    return message["id"]


def screen(env: Env, app_id: str) -> None:
    for field, words, value in MX_ANSWERS:
        say(env, app_id, words)
        assert tool_ok(env, app_id, "record_answer", field=field, value=value)["status"] == (
            "accepted"
        )
    assert tool_ok(env, app_id, "finish_screening") == {
        "stage": "qualified",
        "message": qualified_close("es-MX", "MX", "Luis"),
    }


def open_item(env: Env, app_id: str, kind: str) -> str:
    return env.conn.execute(
        "SELECT id FROM queue_items WHERE application_id = ? AND kind = ? AND status = 'open'",
        (app_id, kind),
    ).fetchone()[0]


def add_santa_fe_zones(conn) -> None:
    """Two covered Zones sharing a name, so "Santa Fe" asks for a choice."""
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


# ---------------------------------------------------------------------------
# The mapping itself
# ---------------------------------------------------------------------------


def test_every_schema_sql_kind_has_a_payload_schema(contract):
    kinds = schema_sql_kinds()
    mapping = payload_map(contract)
    assert set(mapping) == kinds
    enum = contract.spec["components"]["schemas"]["Event"]["properties"]["kind"]["enum"]
    assert set(enum) == kinds
    schemas = contract.spec["components"]["schemas"]
    for kind, ref in mapping.items():
        assert ref == f"#/components/schemas/EventData_{kind}", (kind, ref)
        assert f"EventData_{kind}" in schemas


def test_payload_schemas_stay_open(contract):
    """Lanes may add fields: every payload schema (and each allOf part) is open."""
    schemas = contract.spec["components"]["schemas"]
    for kind in payload_map(contract):
        schema = schemas[f"EventData_{kind}"]
        parts = [p for p in schema.get("allOf", [schema]) if "$ref" not in p]
        for part in parts:
            assert part.get("type") == "object", kind
            assert part.get("additionalProperties") is True, kind


def test_never_written_kinds_are_marked_and_not_written(contract):
    schemas = contract.spec["components"]["schemas"]
    for kind in NEVER_WRITTEN:
        assert "Not written by the backend yet" in schemas[f"EventData_{kind}"]["description"]
    source = "\n".join(
        p.read_text() for p in (REPO_ROOT / "src" / "backend" / "criba").rglob("*.py")
    )
    for kind in NEVER_WRITTEN:
        assert not re.search(rf'kind\s*=\s*"{kind}"', source), kind


# ---------------------------------------------------------------------------
# Screening: answers, misses, ambiguity, Qualified, Approval
# ---------------------------------------------------------------------------


def test_screening_payloads(env, contract):
    add_santa_fe_zones(env.conn)
    app_id = new_app(env)["id"]
    assert tool_ok(env, app_id, "record_answer", field="full_name", value="Luis Hernández")
    santa_fes = ["zone_mx_cdmx_santa_fe", "zone_mx_guadalajara_santa_fe"]
    ambiguous = tool_ok(env, app_id, "record_answer", field="zone", value=santa_fes)
    assert ambiguous["status"] == "needs_choice"
    missed = tool(env, app_id, "record_answer", field="availability", value=None)
    assert not missed["ok"] and missed["error"] == "invalid_value", missed
    screen(env, app_id)
    item = open_item(env, app_id, "approval")
    response = env.client(OPERATOR).post(
        f"/api/queue/approvals/{item}", json={"decision": "approved", "note": "Buen perfil"}
    )
    assert response.status_code == 200, response.text

    events = served_events(env, contract, app_id)
    assert {
        "application_started",
        "answer_recorded",
        "answer_invalid",
        "answer_ambiguous",
        "stage_changed",
        "screening_finished",
        "approval_requested",
        "approved",
    } <= set(events)
    # The simulator reads field order from answer_recorded.data.field.
    fields = [d["field"] for d in events["answer_recorded"]]
    assert fields == ["full_name"] + [f for f, _, _ in MX_ANSWERS]
    zone = next(d for d in events["answer_recorded"] if d["field"] == "zone")
    assert zone["value"] == PUEBLA_CENTRO and zone["locations"]
    assert events["screening_finished"] == [{"result": "qualified"}]
    assert events["answer_ambiguous"][0]["field"] == "zone"
    assert events["answer_invalid"][0] == {
        "field": "availability",
        "attempts": 1,
        "reason": "unresolved",
    }
    assert events["approved"] == [{"note": "Buen perfil"}]
    # Storage matches what is served: the Candidate's words are not in the event log. Zone
    # and driver_license keep them on the Application (AnswerStatus.raw_input).
    assert stored(env, app_id, "answer_invalid") == events["answer_invalid"]
    assert stored(env, app_id, "answer_ambiguous") == events["answer_ambiguous"]
    app = repo.require_application(env.conn, app_id)
    assert (app["zone_input"], app["driver_license_input"]) == (
        "Centro de Puebla",
        "sí tengo licencia",
    )


def test_structured_answer_payloads(env, contract):
    """ADR 0042: canonical values and message ids; the Candidate's words come from their
    message (never the agent's value); a miss carries its reason; the Zone confirmation
    carries confirm, and its No clears the options."""
    app_id = new_app(env)["id"]
    say(env, app_id, "me llamo luis hernández")
    tool_ok(env, app_id, "record_answer", field="full_name", value="luis hernández")
    asked = say(env, app_id, "vivo en Milpa Alta")
    confirm = tool_ok(env, app_id, "record_answer", field="zone", value=[UNCOVERED_ZONE])
    assert confirm["confirm"] == UNCOVERED_ZONE
    said_no = say(env, app_id, "No")
    refused = tool(env, app_id, "record_answer", field="zone", value=None)
    assert refused["reason"] == "zone_not_confirmed" and "attempt" not in refused
    zone_words = say(env, app_id, "por Puebla centro, cerca del zócalo")
    tool_ok(env, app_id, "record_answer", field="zone", value=[PUEBLA_CENTRO])
    unsure = say(env, app_id, "pues no sé, depende")
    missed = tool(env, app_id, "record_answer", field="driver_license", value=None)
    assert missed["reason"] == "unresolved" and missed["attempt"] == 1
    licence = say(env, app_id, "sí, la tengo vigente desde 2019")
    tool_ok(env, app_id, "record_answer", field="driver_license", value="yes")

    events = served_events(env, contract, app_id)
    recorded = {d["field"]: d for d in events["answer_recorded"]}
    assert "value" not in recorded["full_name"]
    assert recorded["zone"]["value"] == PUEBLA_CENTRO
    assert recorded["zone"]["message_id"] == zone_words
    assert recorded["driver_license"] == {
        "field": "driver_license",
        "value": True,
        "message_id": licence,
    }
    assert events["answer_ambiguous"] == [
        {
            "field": "zone",
            "options": [{"id": UNCOVERED_ZONE, "label": "Sí"}, {"id": "no", "label": "No"}],
            "confirm": UNCOVERED_ZONE,
            "message_id": asked,
        },
        {"field": "zone", "options": [], "message_id": said_no},
    ]
    assert events["answer_invalid"] == [
        {"field": "driver_license", "attempts": 1, "reason": "unresolved", "message_id": unsure}
    ]
    app = repo.require_application(env.conn, app_id)
    assert app["full_name"] == "Luis Hernández"
    assert (app["zone_input"], app["driver_license_input"]) == (
        "por Puebla centro, cerca del zócalo",
        "sí, la tengo vigente desde 2019",
    )


# ---------------------------------------------------------------------------
# Knockout, Review, Reopen
# ---------------------------------------------------------------------------


def test_knockout_and_review_payloads(env, contract):
    app_id = new_app(env)["id"]
    tool_ok(env, app_id, "record_answer", field="full_name", value="Luis Hernández")
    confirm = tool_ok(env, app_id, "record_answer", field="zone", value=[UNCOVERED_ZONE])
    assert confirm["confirm"] == UNCOVERED_ZONE
    result = tool_ok(env, app_id, "record_answer", field="zone", value=[UNCOVERED_ZONE])
    assert result["status"] == "knockout"
    tool_ok(env, app_id, "request_review", note="Vivo muy cerca de Puebla")
    item = open_item(env, app_id, "review")
    response = env.client(OPERATOR).post(
        f"/api/queue/reviews/{item}", json={"decision": "overturned"}
    )
    assert response.status_code == 200, response.text

    events = served_events(env, contract, app_id)
    [knockout] = events["knockout"]
    # The sim reads the failed criterion from knockout.data.criterion; the field it
    # failed on is the answer_recorded just before it.
    assert knockout["criterion"] == "zone" and "field" not in knockout
    assert knockout["reason"] == "outside_service_areas"
    assert knockout["template"].startswith("knockout_zone")
    # No reply was sent here (tools called directly), so no message_id yet; never the text.
    assert knockout["stage"] == "zone" and "message" not in knockout
    assert events["answer_recorded"][-1]["field"] == "zone"
    assert events["screening_finished"] == [{"result": "knockout"}]
    assert events["review_requested"][0] == {"queue_item_id": item, "reason": None}
    note = env.conn.execute("SELECT note FROM queue_items WHERE id = ?", (item,)).fetchone()[0]
    assert note == "Vivo muy cerca de Puebla"  # the words stay on the queue item
    assert events["review_overturned"] == [{"queue_item_id": item}]
    assert events["reopened"] == [
        {
            "previous_outcome": "knockout",
            "via": "review_overturned",
            "criterion": "zone",
            "previous_value": UNCOVERED_ZONE,
        }
    ]

    upheld = new_app(env)["id"]
    tool_ok(env, upheld, "record_answer", field="driver_license", value="no")
    tool_ok(env, upheld, "request_review")
    item = open_item(env, upheld, "review")
    response = env.client(OPERATOR).post(f"/api/queue/reviews/{item}", json={"decision": "upheld"})
    assert response.status_code == 200, response.text
    events = served_events(env, contract, upheld)
    assert events["knockout"][0]["criterion"] == "driver_license"
    assert events["review_upheld"] == [{"queue_item_id": item}]


# ---------------------------------------------------------------------------
# Escalation, Takeover, Claim, Rejection
# ---------------------------------------------------------------------------


def test_escalation_takeover_and_rejection_payloads(env, build, contract):
    app_id = build.application()["id"]
    tool_ok(env, app_id, "raise_escalation", reason="wants_human", note="Quiere hablar")
    item = open_item(env, app_id, "escalation")
    operator = env.client(OPERATOR)
    response = operator.post(f"/api/queue/escalations/{item}", json={"decision": "takeover"})
    assert response.status_code == 200, response.text
    response = operator.post(
        f"/api/applications/{app_id}/takeover/handback", json={"summary": "Resuelto."}
    )
    assert response.status_code == 200, response.text
    tool_ok(env, app_id, "raise_escalation", reason="other")
    dismissed = open_item(env, app_id, "escalation")
    response = operator.post(f"/api/queue/escalations/{dismissed}", json={"decision": "dismissed"})
    assert response.status_code == 200, response.text
    assert operator.post(f"/api/applications/{app_id}/claim").status_code == 200
    assert operator.post(f"/api/applications/{app_id}/takeover").status_code == 200
    response = operator.post(
        f"/api/applications/{app_id}/rejection", json={"reason": "other", "note": "Duplicado"}
    )
    assert response.status_code == 200, response.text

    events = served_events(env, contract, app_id)
    assert events["escalation_raised"][0] == {
        "queue_item_id": item,
        "reason": "wants_human",
    }
    note = env.conn.execute("SELECT note FROM queue_items WHERE id = ?", (item,)).fetchone()[0]
    assert note == "Quiere hablar"  # on the queue item only
    assert events["takeover_started"][0] == {"queue_item_id": item}
    assert events["takeover_started"][1] == {}
    first, closing = events["takeover_handed_back"]
    assert first == {
        "handback_id": first["handback_id"],
        "generated": False,
        "held_by": "usr_operator",
    }
    assert repo.get_handback(env.conn, first["handback_id"])["summary"] == "Resuelto."
    assert closing == {"reason": "application_closed", "held_by": "usr_operator"}
    assert events["escalation_dismissed"] == [{"queue_item_id": dismissed}]
    assert events["claim_taken"] == [{}]
    assert events["claim_released"] == [{"user_id": "usr_operator", "reason": "application_closed"}]
    assert events["rejection"] == [{"reason": "other", "note": "Duplicado", "stage": "qualified"}]


def test_claim_and_flag_payloads(env, build, contract):
    waiting = build.application()["id"]
    claimed = build.application()["id"]
    assert env.client(PUEBLA).post(f"/api/applications/{claimed}/claim").status_code == 200
    run_flags(env.conn, NOW + timedelta(days=15))
    env.clock.set(NOW + timedelta(days=15))
    response = env.client(PUEBLA_2).post(f"/api/applications/{claimed}/claim/takeover")
    assert response.status_code == 200, response.text
    assert env.client(PUEBLA_2).delete(f"/api/applications/{claimed}/claim").status_code == 204

    events = served_events(env, contract, waiting)
    assert events["flag_qualified_waiting"] == [{"qualified_at": iso(NOW)}]
    events = served_events(env, contract, claimed)
    assert events["flag_claimed_idle"] == [
        {"user_id": "usr_rec_puebla", "last_action_at": iso(NOW)}
    ]
    assert events["claim_taken_over"] == [{"previous_user_id": "usr_rec_puebla"}]
    assert events["claim_released"] == [{"user_id": "usr_rec_puebla_2"}]


# ---------------------------------------------------------------------------
# Stalled, nudges, Abandoned, Candidate Reopen
# ---------------------------------------------------------------------------


def test_stalled_abandoned_and_reopen_payloads(env, contract):
    app = new_app(env, country="ES")
    app_id = app["id"]
    sent: list[Any] = []
    run_stalled(env.conn, NOW + timedelta(hours=2), lambda conn, req: sent.append(req) or True)
    run_stalled(env.conn, NOW + timedelta(days=8), lambda conn, req: True)
    repo.resolve_application(env.conn, app["candidate_id"], "webchat", now=NOW + timedelta(days=9))

    events = served_events(env, contract, app_id)
    assert events["stalled"] == [{"stage": "greeting"}]
    assert events["nudge_sent"][0] == {"step": 1, "kind": "nudge", "stage": "greeting"}
    assert events["abandoned"] == [{"nudges_sent": 1, "stage": "greeting"}]
    assert events["screening_finished"] == [{"result": "abandoned"}]
    assert events["reopened"] == [{"previous_outcome": "abandoned", "via": "candidate"}]


# ---------------------------------------------------------------------------
# Interview booking
# ---------------------------------------------------------------------------


def test_booking_payloads(env, build, contract, tmp_path, monkeypatch):
    secret = tmp_path / "booking_secret.txt"
    secret.write_text("test-booking-secret\n", encoding="utf-8")
    monkeypatch.setenv("CRIBA_BOOKING_SECRET_FILE", str(secret))
    app_id = build.application(stage="approved")["id"]
    response = env.client(OPERATOR).post(f"/api/applications/{app_id}/booking-link")
    assert response.status_code == 201, response.text
    offer = tool_ok(env, app_id, "offer_interview_slots")
    tool_ok(env, app_id, "book_interview", slot_ref=offer["slots"][0]["slot_ref"])
    offer = tool_ok(env, app_id, "offer_interview_slots", reschedule=True)
    tool_ok(env, app_id, "book_interview", slot_ref=offer["slots"][-1]["slot_ref"])
    response = env.client(OPERATOR).post(
        f"/api/applications/{app_id}/rejection", json={"reason": "no_show"}
    )
    assert response.status_code == 200, response.text

    events = served_events(env, contract, app_id)
    assert set(events["booking_link_sent"][0]) == {"expires_at"}
    assert [d["reschedule"] for d in events["slots_offered"]] == [False, True]
    [booked] = events["interview_booked"]
    assert booked["via"] == "chat" and booked["ics_uid"].endswith("@criba.local")
    [moved] = events["interview_rescheduled"]
    assert moved["previous_slot_id"] == booked["slot_id"]
    assert [d["reason"] for d in events["interview_slot_released"]] == [
        "rescheduled",
        "application_closed",
    ]


# ---------------------------------------------------------------------------
# Back-outs, opt-out, Erasure, summary
# ---------------------------------------------------------------------------


def test_backout_and_summary_payloads(env, contract):
    withdrawn = new_app(env)["id"]
    tool_ok(env, withdrawn, "record_backout", kind="withdrawn", note="Encontró otro trabajo")
    SummaryWriter(None, env.clock, environment="test").write(env.conn, withdrawn)
    events = served_events(env, contract, withdrawn)
    assert events["withdrawn"] == [{"stage": "greeting"}]  # the agent's note is not kept
    assert events["screening_finished"] == [{"result": "withdrawn"}]
    assert events["summary_written"] == [{}]

    app = new_app(env)
    tool_ok(env, app["id"], "record_backout", kind="erasure_request", note="Borrar mis datos")
    tool_ok(env, app["id"], "record_backout", kind="opted_out")
    assert repo.lift_opt_out(env.conn, app["candidate_id"], now=NOW + timedelta(hours=1))
    events = served_events(env, contract, app["id"])
    assert events["erasure_requested"] == [{}]
    assert events["opted_out"] == [{}]
    # opt_out_lifted is Candidate-level (no Application): check the stored row as served.
    [row] = [
        e
        for e in env.conn.execute(
            "SELECT * FROM events WHERE candidate_id = ? AND kind = 'opt_out_lifted'",
            (app["candidate_id"],),
        ).fetchall()
    ]
    served = present.event_json({**dict(row), "data": json.loads(row["data"])}, {})
    assert row["application_id"] is None
    assert not payload_errors(contract, "opt_out_lifted", served["data"])


# ---------------------------------------------------------------------------
# Channel: system turn, run error, template
# ---------------------------------------------------------------------------


@pytest.fixture
def hermes() -> FakeHermes:
    return FakeHermes()


@pytest.fixture
def channel(env: Env, hermes: FakeHermes) -> tuple[Channel, Fakes]:
    fakes = Fakes()
    built = Channel(
        connect=lambda: connect(env.db_path),
        run_client=make_run_client(hermes),
        clock=env.clock,
        settings=ChannelSettings(db_path=str(env.db_path), environment="test"),
        pending_choice=fakes.pending_choice,
        interview_offer=fakes.interview_offer,
    )
    return built, fakes


def test_channel_payloads(env, build, contract, channel, hermes):
    service, fakes = channel
    env.clock.set(NOW + timedelta(hours=8))  # 12:00 in Mexico City: outside quiet hours
    turn = build.application(stage="approved")["id"]
    service.handle_event(turn, "approved")
    events = served_events(env, contract, turn)
    assert events["system_turn"] == [{"turn": APPROVED_TURN}]

    failed = build.application(stage="approved")["id"]
    hermes.final_status = "failed"
    service.handle_event(failed, "approved")
    events = served_events(env, contract, failed)
    [error] = events["run_error"]
    assert error["kind"] == "error" and error["system_turn"] is True and error["run_id"]

    fakes.offer = {
        "slots": [{"slot_ref": "so_1", "label": "mar 13 · 10:00", "interview": {}}],
        "booking_url": "https://empleo.gruposazon.example/book/tok123",
        "other_label": "Otro horario",
    }
    whatsapp = new_app(env, channel="whatsapp")["id"]
    env.conn.execute(
        "UPDATE applications SET stage = 'approved', approved_at = ?,"
        " last_inbound_at = ? WHERE id = ?",
        (iso(NOW), iso(NOW - timedelta(hours=30)), whatsapp),
    )
    assert service.handle_event(whatsapp, "approved").kind == "template"
    events = served_events(env, contract, whatsapp)
    [template] = events["template_sent"]
    assert template["template"] == "interview_offer" and isinstance(template["message_id"], int)
