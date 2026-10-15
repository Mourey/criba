"""Jev's labels on the Application detail (ADR 0075): Recruiters see labels, never scores.

Every role gets ``message_labels`` on ``getApplication``: one entry per typed Candidate message
Jev read, as codes. No probability, confidence, threshold or Manipulation score reaches the wire,
the ``abusive`` signal and the ``steering`` intent are withheld, spoken turns and failed reads have
no entry, and the Location scope of the detail applies. Operators keep the full readings at
``getJevReadings`` (ADR 0066).
"""

from __future__ import annotations

import json
from typing import Any

import yaml

from criba.db import repo
from criba.jev import store
from criba.jev.battery import Reading

from ..api.support import LA_PAZ, MADRID, OPERATOR, PUEBLA, REPO_ROOT, at

LABEL_KEYS = {
    "message_id",
    "stage",
    "at",
    "language",
    "fired",
    "intent",
    "tone_hint",
    "language_set",
    "language_reason",
}
# Distinct score values, so a leak of any of them into the wire text is found by search.
SCORES = (0.913, 0.877, 0.861, 0.952, 0.934, 0.947, 0.968, 0.123, 0.271)


def say(conn, app: dict[str, Any], body: str, minutes: float, modality: str = "typed") -> int:
    message, _ = repo.add_message(
        conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body=body,
        modality=modality,
        now=at(minutes),
    )
    return message["id"]


def keep(conn, app, message_id, minutes, *, stage, status="ok", reading=None, **kw) -> None:
    store.record_signals(
        conn,
        message_id=message_id,
        application={**app, "stage": stage},
        now=at(minutes),
        status=status,
        model="typesafe/jev-1.13-20260917" if status == "ok" else None,
        policy_version="jev-1.13-v3" if status == "ok" else None,
        reading=reading if status == "ok" else None,
        **kw,
    )


def conversation(env, build) -> tuple[dict[str, Any], dict[str, int]]:
    conn = env.conn
    app = build.application()
    ids: dict[str, int] = {}
    ids["first"] = say(conn, app, "Hola", 1)
    keep(
        conn, app, ids["first"], 1, stage="greeting", first_message=True,
        language_set="es-MX", language_reason="first_message",
        reading=Reading(
            "es", 0.913, 0.123,
            {"frustrated": 0.271, "confused": 0.03, "wants_human": 0.02, "abusive": 0.01},
            intent="answering", intent_confidence=0.877, malice=0.02,
        ),
    )  # fmt: skip
    ids["hot"] = say(conn, app, "quiero una persona ya", 3)
    keep(
        conn, app, ids["hot"], 3, stage="start_date",
        fired=("abusive", "wants_human", "frustrated"), hint="calm", escalation="abuse",
        reading=Reading(
            "es", 0.861, 0.03,
            {"frustrated": 0.952, "confused": 0.04, "wants_human": 0.934, "abusive": 0.947},
            intent="steering", intent_confidence=0.968, malice=0.968,
        ),
    )  # fmt: skip
    ids["english"] = say(conn, app, "Can we talk in English please", 4)
    keep(
        conn, app, ids["english"], 4, stage="start_date", language_set="en",
        language_reason="request", fired=("confused",), hint="simpler",
        reading=Reading(
            "en", 0.99, 0.95,
            {"frustrated": 0.02, "confused": 0.8, "wants_human": 0.02, "abusive": 0.01},
            intent="asking", intent_confidence=0.9, malice=0.01,
        ),
    )  # fmt: skip
    ids["error"] = say(conn, app, "otra cosa", 5)
    keep(conn, app, ids["error"], 5, stage="start_date", status="timeout")
    ids["spoken"] = say(conn, app, "dicho en voz alta", 6, modality="spoken")
    keep(
        conn, app, ids["spoken"], 6, stage="confirm",
        reading=Reading("es", 0.99, 0.01, {"frustrated": 0.9}), fired=("frustrated",),
    )  # fmt: skip
    ids["unread"] = say(conn, app, "sin lectura", 7)
    return app, ids


def labels(env, app, email: str, contract=None) -> list[dict[str, Any]]:
    response = env.client(email).get(f"/api/applications/{app['id']}")
    assert response.status_code == 200
    body = contract.conforms(response, "getApplication") if contract else response.json()
    return body["message_labels"]


def test_a_recruiter_gets_labels_for_each_read_typed_message(env, build, contract) -> None:
    app, ids = conversation(env, build)
    got = labels(env, app, PUEBLA, contract)
    assert [e["message_id"] for e in got] == [ids["first"], ids["hot"], ids["english"]]
    assert got[0] == {
        "message_id": ids["first"],
        "stage": "greeting",
        "at": got[0]["at"],
        "language": "es",
        "fired": [],
        "intent": "answering",
        "tone_hint": None,
        "language_set": "es-MX",
        "language_reason": "first_message",
    }
    assert got[2]["intent"] == "asking" and got[2]["tone_hint"] == "simpler"
    assert got[2]["language_set"] == "en" and got[2]["language_reason"] == "request"


def test_a_label_has_exactly_the_label_keys_and_no_score(env, build) -> None:
    app, _ = conversation(env, build)
    got = labels(env, app, PUEBLA)
    for entry in got:
        assert set(entry) == LABEL_KEYS
        for key, value in entry.items():
            if key != "message_id":
                assert not isinstance(value, float | int) or isinstance(value, bool), key
    wire = json.dumps(got)
    for score in SCORES:
        assert str(score) not in wire
    for word in ("confidence", "malice", "manipulation", "threshold", "policy", "typesafe"):
        assert word not in wire.lower()


def test_judgemental_codes_are_withheld(env, build) -> None:
    app, ids = conversation(env, build)
    hot = next(e for e in labels(env, app, PUEBLA) if e["message_id"] == ids["hot"])
    assert hot["fired"] == ["wants_human", "frustrated"]
    assert hot["intent"] is None
    assert "abusive" not in json.dumps(hot) and "steering" not in json.dumps(hot)


def test_spoken_failed_and_unread_messages_have_no_label(env, build) -> None:
    app, ids = conversation(env, build)
    seen = {e["message_id"] for e in labels(env, app, PUEBLA)}
    assert not seen & {ids["error"], ids["spoken"], ids["unread"]}


def test_labels_carry_no_message_text(env, build) -> None:
    app, _ = conversation(env, build)
    wire = json.dumps(labels(env, app, PUEBLA)).lower()
    for word in ("hola", "persona", "english please", "otra cosa", "voz alta", "lectura"):
        assert word not in wire


def test_every_role_gets_the_same_labels(env, build) -> None:
    app, _ = conversation(env, build)
    assert labels(env, app, PUEBLA) == labels(env, app, LA_PAZ) == labels(env, app, OPERATOR)


def test_an_outside_location_recruiter_gets_404(env, build) -> None:
    app, _ = conversation(env, build)
    assert env.client(MADRID).get(f"/api/applications/{app['id']}").status_code == 404


def test_the_operator_still_gets_the_full_readings(env, build, contract) -> None:
    app, ids = conversation(env, build)
    response = env.client(OPERATOR).get(f"/api/applications/{app['id']}/jev-readings")
    body = contract.conforms(response, "getJevReadings")
    hot = next(e for e in body["readings"] if e["message_id"] == ids["hot"])["reading"]
    assert hot["abusive"] == 0.947 and hot["malice"] == 0.968
    assert hot["intent"] == {"choice": "steering", "confidence": 0.968}
    assert env.client(PUEBLA).get(f"/api/applications/{app['id']}/jev-readings").status_code == 403


def test_an_application_with_no_readings_has_no_labels(env, build, contract) -> None:
    app = build.application()
    assert labels(env, app, PUEBLA, contract) == []


def test_the_label_schema_is_closed_and_holds_no_number_but_the_id() -> None:
    spec = yaml.safe_load((REPO_ROOT / "contracts" / "api.openapi.yaml").read_text("utf-8"))
    schema = spec["components"]["schemas"]["MessageLabel"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == LABEL_KEYS == set(schema["required"])
    numbers = {k for k, v in schema["properties"].items() if v.get("type") in ("number", "integer")}
    assert numbers == {"message_id"}
    assert "abusive" not in schema["properties"]["fired"]["items"]["enum"]
    assert "steering" not in schema["properties"]["intent"]["enum"]
    detail = spec["components"]["schemas"]["ApplicationDetail"]
    props = next(p["properties"] for p in detail["allOf"] if "properties" in p)
    assert "x-roles" not in props["message_labels"]
