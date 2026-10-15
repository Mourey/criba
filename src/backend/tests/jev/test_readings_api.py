"""``GET /api/applications/{id}/jev-readings``: the Operator's stored Jev readings (ADR 0066).

One entry per typed Candidate message, calm and unreadable ones included; ids, codes and numbers
only, never a message body; Recruiters and anonymous callers are refused. The readings are
display only (ADR 0051): ``test_not_a_decision_input.py`` guards the reader.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import yaml

import criba.db.repo.signals as signals_module
from criba.db import repo
from criba.jev import store
from criba.jev.battery import Reading
from criba.jev.policy import THRESHOLDS

from ..api.support import LA_PAZ, OPERATOR, PUEBLA, REPO_ROOT, at

CONTRACT = REPO_ROOT / "contracts" / "api.openapi.yaml"
DAY = 24 * 60

# Words that exist only in message bodies, so a leak into the wire text is found by search.
BODY_WORDS = ("zanahoria", "betabel", "alcachofa", "rábano", "calabaza", "pepino", "apio")


def url(app: dict[str, Any]) -> str:
    return f"/api/applications/{app['id']}/jev-readings"


def message(
    conn,
    app: dict[str, Any],
    body: str,
    minutes: float,
    *,
    direction: str = "inbound",
    sender: str = "candidate",
    content_type: str = "text",
    modality: str = "typed",
    reply_option_id: str | None = None,
) -> int:
    stored, _ = repo.add_message(
        conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        channel="webchat",
        direction=direction,
        sender=sender,
        body=body,
        content_type=content_type,
        modality=modality,
        reply_option_id=reply_option_id,
        now=at(minutes),
    )
    return stored["id"]


def read(
    conn,
    app: dict[str, Any],
    message_id: int,
    minutes: float,
    *,
    stage: str,
    status: str = "ok",
    reading: Reading | None = None,
    **kwargs: Any,
) -> None:
    """Store a row as the router does: a failed read has no values, model or policy version."""
    ok = status == "ok"
    store.record_signals(
        conn,
        message_id=message_id,
        application={**app, "stage": stage},
        now=at(minutes),
        status=status,
        model="typesafe/jev-1.13-20260917" if ok else None,
        policy_version=THRESHOLDS.version if ok else None,
        reading=reading if ok else None,
        **kwargs,
    )


CALM = Reading(
    "es", 0.99, 0.02, {"frustrated": 0.03, "confused": 0.04, "wants_human": 0.02, "abusive": 0.01}
)
HOT = Reading(
    "en", 0.81, 0.91, {"frustrated": 0.88, "confused": 0.12, "wants_human": 0.97, "abusive": 0.05}
)


def seed(env, build) -> dict[str, Any]:
    """Every kind of message the endpoint must keep or drop, oldest first by id."""
    conn = env.conn
    app = build.application()
    ids: dict[str, Any] = {"app": app}
    # Older than the 180-day retention period, no row left: `expired`. Inserted first so the
    # id order and the time order agree.
    ids["expired"] = message(conn, app, "zanahoria vieja", -181 * DAY)
    ids["calm"] = message(conn, app, "Hola betabel", 1)
    read(
        conn,
        app,
        ids["calm"],
        1,
        stage="greeting",
        reading=CALM,
        first_message=True,
        language_set="es-MX",
    )
    ids["tap"] = message(
        conn, app, "alcachofa", 2, content_type="button_reply", reply_option_id="opt_yes"
    )
    ids["fired"] = message(conn, app, "quiero hablar con una persona, rábano", 3)
    read(
        conn, app, ids["fired"], 3, stage="start_date", reading=HOT,
        fired=("frustrated", "wants_human"), hint="escalated", escalation="wants_human",
    )  # fmt: skip
    ids["agent"] = message(
        conn, app, "Respuesta del agente", 3.5, direction="outbound", sender="agent"
    )
    ids["error"] = message(conn, app, "calabaza uno", 4)
    read(conn, app, ids["error"], 4, stage="start_date", status="error")
    ids["timeout"] = message(conn, app, "calabaza dos", 5)
    read(conn, app, ids["timeout"], 5, stage="confirm", status="timeout", first_message=True)
    ids["system"] = message(
        conn, app, "[system] approved", 6, direction="outbound", sender="system"
    )
    ids["spoken"] = message(conn, app, "pepino dicho en voz alta", 7, modality="spoken")
    read(conn, app, ids["spoken"], 7, stage="confirm", reading=CALM)
    ids["newlines"] = message(conn, app, "\n\n", 8)
    ids["blank"] = message(conn, app, " \t\r\n ", 9)
    ids["unread"] = message(conn, app, "apio sin lectura", 10)
    return ids


def entries(env, ids: dict[str, Any], contract=None) -> list[dict[str, Any]]:
    response = env.client(OPERATOR).get(url(ids["app"]))
    if contract is not None:
        return contract.conforms(response, "getJevReadings")["readings"]
    assert response.status_code == 200, response.text
    return response.json()["readings"]


# --------------------------------------------------------------------------- entries


def test_one_entry_per_typed_candidate_message_oldest_first(env, build, contract) -> None:
    ids = seed(env, build)
    got = entries(env, ids, contract)
    assert [e["message_id"] for e in got] == [
        ids["expired"], ids["calm"], ids["fired"], ids["error"], ids["timeout"], ids["unread"],
    ]  # fmt: skip
    assert [e["message_id"] for e in got] == sorted(e["message_id"] for e in got)
    assert [e["status"] for e in got] == [
        "expired", "ok", "ok", "error", "timeout", "not_read",
    ]  # fmt: skip


def test_taps_spoken_system_agent_and_blank_messages_have_no_entry(env, build) -> None:
    ids = seed(env, build)
    seen = {e["message_id"] for e in entries(env, ids)}
    for dropped in ("tap", "agent", "system", "spoken", "newlines", "blank"):
        assert ids[dropped] not in seen, dropped


def test_an_ok_entry_carries_exactly_the_stored_row(env, build) -> None:
    ids = seed(env, build)
    got = {e["message_id"]: e for e in entries(env, ids)}
    assert got[ids["calm"]] == {
        "message_id": ids["calm"],
        "at": at(1).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": "greeting",
        "status": "ok",
        "reading": {
            "lang": "es",
            "lang_confidence": 0.99,
            "switch": 0.02,
            "frustrated": 0.03,
            "confused": 0.04,
            "wants_human": 0.02,
            "abusive": 0.01,
            "fired": [],
            "hint": None,
            "escalation": None,
            "model": "typesafe/jev-1.13-20260917",
            "policy_version": THRESHOLDS.version,
            "first_message": True,
            "language_set": "es-MX",
            "intent": None,
            "malice": None,
        },
    }
    hot = got[ids["fired"]]
    assert hot["stage"] == "start_date"
    assert hot["reading"] == {
        "lang": "en",
        "lang_confidence": 0.81,
        "switch": 0.91,
        "frustrated": 0.88,
        "confused": 0.12,
        "wants_human": 0.97,
        "abusive": 0.05,
        "fired": ["frustrated", "wants_human"],
        "hint": "escalated",
        "escalation": "wants_human",
        "model": "typesafe/jev-1.13-20260917",
        "policy_version": THRESHOLDS.version,
        "first_message": False,
        "language_set": None,
        "intent": None,
        "malice": None,
    }


def test_the_entries_equal_the_rows_the_store_lists(env, build) -> None:
    ids = seed(env, build)
    stored = {row["message_id"]: row for row in store.list_signals(env.conn, ids["app"]["id"])}
    got = {e["message_id"]: e for e in entries(env, ids) if e["status"] == "ok"}
    assert set(got) == {ids["calm"], ids["fired"]}
    for message_id, entry in got.items():
        row = stored[message_id]
        assert entry["stage"] == row["stage"]
        for name, value in entry["reading"].items():
            assert value == row[name], name


def test_error_and_timeout_entries_carry_no_reading(env, build) -> None:
    ids = seed(env, build)
    got = {e["message_id"]: e for e in entries(env, ids)}
    assert got[ids["error"]] == {
        "message_id": ids["error"],
        "at": at(4).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": "start_date",
        "status": "error",
        "reading": None,
    }
    assert got[ids["timeout"]]["status"] == "timeout"
    assert got[ids["timeout"]]["stage"] == "confirm"
    assert got[ids["timeout"]]["reading"] is None


def test_no_row_means_no_stage_and_no_reading(env, build) -> None:
    ids = seed(env, build)
    got = {e["message_id"]: e for e in entries(env, ids)}
    for name, status in (("unread", "not_read"), ("expired", "expired")):
        entry = got[ids[name]]
        assert entry["status"] == status, name
        assert entry["stage"] is None and entry["reading"] is None, name
    for entry in got.values():
        if entry["status"] != "ok":
            assert entry["reading"] is None


def test_a_message_just_inside_the_retention_period_is_not_read_not_expired(env, build) -> None:
    app = build.application()
    inside = message(env.conn, app, "justo dentro", -179 * DAY)
    outside = message(env.conn, app, "justo fuera", -181 * DAY + 1)
    got = [(e["message_id"], e["status"]) for e in entries(env, {"app": app})]
    assert got == [(inside, "not_read"), (outside, "expired")]


def test_expired_follows_the_retention_setting(env, build, monkeypatch) -> None:
    app = build.application()
    two_months = message(env.conn, app, "hace dos meses", -60 * DAY)
    assert entries(env, {"app": app})[0]["status"] == "not_read"
    monkeypatch.setenv("CRIBA_SIGNALS_RETENTION_DAYS", "30")
    got = entries(env, {"app": app})
    assert [(e["message_id"], e["status"]) for e in got] == [(two_months, "expired")]


def test_a_row_still_in_the_table_shows_its_reading_even_past_the_cutoff(env, build) -> None:
    app = build.application()
    old = message(env.conn, app, "vieja con lectura", -200 * DAY)
    read(env.conn, app, old, -200 * DAY, stage="name", reading=CALM)
    [entry] = entries(env, {"app": app})
    assert entry["status"] == "ok" and entry["reading"]["lang"] == "es"


def test_a_no_break_space_body_still_gets_an_entry(env, build) -> None:
    """SQL ``trim`` removes only the characters listed, the router's ``strip()`` removes all
    Unicode whitespace (plan Risk 10): such a body shows as `not_read` and carries no text."""
    app = build.application()
    nbsp = message(env.conn, app, "\xa0", 1)
    vtab = message(env.conn, app, "\x0b\x0c", 2)
    got = entries(env, {"app": app})
    assert [(e["message_id"], e["status"]) for e in got] == [
        (nbsp, "not_read"),
        (vtab, "not_read"),
    ]


def test_an_application_with_no_typed_messages_has_no_entries(env, build, contract) -> None:
    app = build.application()
    body = contract.conforms(env.client(OPERATOR).get(url(app)), "getJevReadings")
    assert body["readings"] == [] and body["application_id"] == app["id"]


def test_only_the_applications_own_messages_are_listed(env, build) -> None:
    ids = seed(env, build)
    other = build.application()
    theirs = message(env.conn, other, "de otro candidato", 1)
    assert theirs not in {e["message_id"] for e in entries(env, ids)}
    assert [e["message_id"] for e in entries(env, {"app": other})] == [theirs]


# --------------------------------------------------------------------------- thresholds


def test_thresholds_are_the_current_policy_bundle(env, build) -> None:
    ids = seed(env, build)
    body = env.client(OPERATOR).get(url(ids["app"])).json()
    expected = asdict(THRESHOLDS)
    assert set(body["thresholds"]) == set(expected)
    assert body["thresholds"] == expected
    for name, value in expected.items():
        assert body["thresholds"][name] == value, name


# --------------------------------------------------------------------------- wire


def test_the_wire_carries_no_message_body_and_no_intent_or_malice(env, build, contract) -> None:
    ids = seed(env, build)
    response = env.client(OPERATOR).get(url(ids["app"]))
    body = contract.conforms(response, "getJevReadings")
    for word in (*BODY_WORDS, "voz alta", "agente", "[system]", "justo"):
        assert word not in response.text, word
    for entry in body["readings"]:
        assert set(entry) == {"message_id", "at", "stage", "status", "reading"}
        if entry["status"] == "ok":
            assert "intent" in entry["reading"] and "malice" in entry["reading"]
    candidate_name = ids["app"].get("full_name") or "Luis"
    assert candidate_name not in response.text


# --------------------------------------------------------------------------- access


def test_a_recruiter_gets_403_inside_and_outside_the_locations(env, build) -> None:
    ids = seed(env, build)
    # Inside: Puebla and La Paz both cover the Application's zone. Outside: Madrid does not.
    for email in (PUEBLA, LA_PAZ, "recruiter.madrid@sazon.example"):
        response = env.client(email).get(url(ids["app"]))
        assert response.status_code == 403, email
        assert response.json()["error"] == "forbidden", email
        assert not any(word in response.text for word in BODY_WORDS)


def test_anonymous_gets_401(env, build) -> None:
    ids = seed(env, build)
    response = env.client().get(url(ids["app"]))
    assert response.status_code == 401
    assert "calabaza" not in response.text


def test_an_unknown_application_is_404_for_the_operator(env, contract) -> None:
    response = env.client(OPERATOR).get("/api/applications/app_nope/jev-readings")
    assert response.status_code == 404
    contract.conforms(response, "getJevReadings")


def test_the_endpoint_is_read_only(env, build) -> None:
    ids = seed(env, build)
    operator = env.client(OPERATOR)
    for method in ("post", "put", "patch", "delete"):
        assert getattr(operator, method)(url(ids["app"])).status_code == 405, method


# --------------------------------------------------------------------------- contract text


def spec() -> dict[str, Any]:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def test_the_reading_schema_is_closed_with_room_for_intent_and_malice() -> None:
    schema = spec()["components"]["schemas"]["JevReading"]
    assert schema["additionalProperties"] is False
    assert {"intent", "malice"} <= set(schema["properties"])
    assert not {"intent", "malice"} & set(schema["required"])
    for name in ("intent", "malice"):
        assert "jev-intent" in schema["properties"][name]["description"]


def test_no_jev_property_is_marked_for_a_role() -> None:
    """Only `cost` carries ``x-roles`` (tests/api/test_cost_fields.py); the whole endpoint is
    Operator-only instead."""
    schemas = spec()["components"]["schemas"]
    for name in ("JevReadings", "JevThresholds", "JevReadingEntry", "JevReading"):
        assert "x-roles" not in schemas[name], name
        for prop, value in schemas[name]["properties"].items():
            assert "x-roles" not in value, (name, prop)


def test_the_threshold_schema_is_closed_and_lists_every_policy_field() -> None:
    schema = spec()["components"]["schemas"]["JevThresholds"]
    fields = set(asdict(THRESHOLDS))
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == fields and set(schema["properties"]) == fields


def test_the_operation_is_operator_only_and_states_the_bar_mapping() -> None:
    operation = spec()["paths"]["/api/applications/{application_id}/jev-readings"]["get"]
    assert operation["operationId"] == "getJevReadings"
    assert operation["x-roles"] == ["operator"] and operation["x-operator-only"] is True
    assert set(operation["responses"]) == {"200", "401", "403", "404"}
    text = " ".join(operation["description"].split())
    for marker in (
        "switch_noul",
        "frustrated",
        "confused",
        "wants_human_hint",
        "wants_human_escalate",
        "abusive_escalate",
    ):
        assert f"`{marker}`" in text, marker
    for phrase in (
        "Switch gets `switch_noul`",
        'two markers, `wants_human_hint` (labelled "hint") and `wants_human_escalate` '
        '(labelled "Escalation")',
        "`first_confidence` or `switch_confidence`, not as a bar",
        "only when that version equals `thresholds.version`",
    ):
        assert phrase in text, phrase


# --------------------------------------------------------------------------- intent and malice
# ADR 0071: the two display-only readings Jev gives on each typed message, as stored.

INTENTS = ("answering", "asking", "off_topic", "wants_to_stop", "steering", "other")
STEERING = Reading(
    "es",
    0.99,
    0.02,
    {"frustrated": 0.03, "confused": 0.04, "wants_human": 0.02, "abusive": 0.01},
    intent="steering",
    intent_confidence=0.93,
    malice=0.97,
)


def one_reading(env, build, contract, reading: Reading) -> dict[str, Any]:
    """The only entry of a new Application with one typed message read as ``reading``."""
    app = build.application()
    stored = message(env.conn, app, "hola zanahoria", 1)
    read(env.conn, app, stored, 1, stage="greeting", reading=reading)
    [entry] = entries(env, {"app": app}, contract)
    assert entry["message_id"] == stored
    return entry


def test_an_ok_row_with_the_three_values_returns_the_intent_and_the_malice(
    env, build, contract
) -> None:
    app = build.application()
    stored = message(env.conn, app, "hola zanahoria", 1)
    read(env.conn, app, stored, 1, stage="greeting", reading=STEERING)
    response = env.client(OPERATOR).get(url(app))
    [entry] = contract.conforms(response, "getJevReadings")["readings"]
    assert entry["status"] == "ok"
    assert entry["reading"]["intent"] == {"choice": "steering", "confidence": 0.93}
    assert entry["reading"]["malice"] == 0.97
    [row] = store.list_signals(env.conn, app["id"])
    assert (row["intent"], row["intent_confidence"], row["malice"]) == ("steering", 0.93, 0.97)
    assert "zanahoria" not in response.text  # still no message body


def test_every_intent_option_goes_out_as_stored(env, build, contract) -> None:
    for option in INTENTS:
        reading = Reading(
            "es", 0.99, 0.02, CALM.signals, intent=option, intent_confidence=1.0, malice=0.0
        )
        entry = one_reading(env, build, contract, reading)
        assert entry["reading"]["intent"] == {"choice": option, "confidence": 1.0}, option
        assert entry["reading"]["malice"] == 0.0, option  # a stored 0.0 is a reading, not null


def test_an_ok_row_with_nulls_returns_null_intent_and_null_malice(env, build, contract) -> None:
    app = build.application()
    stored = message(env.conn, app, "hola zanahoria", 1)
    read(env.conn, app, stored, 1, stage="greeting", reading=CALM)
    got = contract.conforms(env.client(OPERATOR).get(url(app)), "getJevReadings")["readings"]
    [entry] = got
    assert entry["status"] == "ok"
    assert entry["reading"]["intent"] is None and entry["reading"]["malice"] is None
    assert {"intent", "malice"} <= set(entry["reading"])  # present, as null


def test_an_intent_without_its_confidence_is_sent_as_null_never_half_filled(
    env, build, contract
) -> None:
    app = build.application()
    stored = message(env.conn, app, "hola zanahoria", 1)
    read(env.conn, app, stored, 1, stage="greeting", reading=CALM)
    env.conn.execute("UPDATE message_signals SET intent = 'asking' WHERE message_id = ?", (stored,))
    [entry] = contract.conforms(env.client(OPERATOR).get(url(app)), "getJevReadings")["readings"]
    assert entry["reading"]["intent"] is None  # `confidence` is required by the contract


def test_a_failed_read_has_no_reading_so_no_intent_or_malice(env, build, contract) -> None:
    app = build.application()
    stored = message(env.conn, app, "calabaza", 1)
    read(env.conn, app, stored, 1, stage="greeting", status="error")
    [entry] = contract.conforms(env.client(OPERATOR).get(url(app)), "getJevReadings")["readings"]
    assert entry["status"] == "error" and entry["reading"] is None


def test_the_query_selects_the_two_readings_and_never_the_message_body() -> None:
    selected = signals_module.READINGS_QUERY.split("FROM", 1)[0]
    for column in ("s.intent", "s.intent_confidence", "s.malice"):
        assert column in selected, column
    assert "body" not in selected


def test_the_contract_types_intent_and_malice_and_keeps_them_optional() -> None:
    schema = spec()["components"]["schemas"]["JevReading"]
    intent, malice = schema["properties"]["intent"], schema["properties"]["malice"]
    assert intent["type"] == ["object", "null"] and intent["additionalProperties"] is False
    assert intent["required"] == ["choice", "confidence"]
    assert intent["properties"]["choice"]["enum"] == list(INTENTS)
    assert intent["properties"]["confidence"] == {"type": "number", "minimum": 0, "maximum": 1}
    assert malice["type"] == ["number", "null"]
    assert (malice["minimum"], malice["maximum"]) == (0, 1)
    assert not {"intent", "malice"} & set(schema["required"])
    for name, prop in (("intent", intent), ("malice", malice)):
        text = prop["description"]
        assert "jev-intent" in text and "never an input to a decision" in text, name
        assert "Display only" in text and "`null`" in text, name
