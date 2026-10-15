"""The agent tool-call trace: one ``tool_calls`` row per call through POST /internal/tools/{name}
(ADR 0069), best effort, with arguments as field names and canonical values only (ADR 0035)."""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from criba.db import connect, iso, repo
from criba.tools import dispatch, trace
from criba.tools import http as tools_http
from criba.tools.schema import contract, input_schema

from .conftest import NOW

AUTH = {"Authorization": "Bearer test-tools-secret"}


def post(client, tool: str, args=None, session_id: str = "app_x", headers=AUTH, **extra):
    body = {"tool": tool, "args": {} if args is None else args, "session_id": session_id}
    body.update(extra)
    return client.post(f"/internal/tools/{tool}", json=body, headers=headers)


@pytest.fixture
def app_id(db_path) -> str:
    conn = connect(db_path)
    candidate, _ = repo.get_or_create_candidate(
        conn, client_id="grupo_sazon", channel="webchat", external_id="trace-1", country="ES"
    )
    app_id = repo.start_application(conn, candidate["id"], "webchat")["id"]
    conn.close()
    return app_id


def rows(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tool_calls ORDER BY id").fetchall()


def args_of(row: sqlite3.Row) -> dict[str, Any]:
    return json.loads(row["args"])


# --- one row per call, for each outcome -------------------------------------


def test_an_accepted_answer_is_one_ok_row(client, conn, app_id, clock) -> None:
    response = post(
        client, "record_answer", {"field": "full_name", "value": "Ana García"}, session_id=app_id
    )
    assert response.status_code == 200
    (row,) = rows(conn)
    assert (row["application_id"], row["tool"], row["outcome"], row["error"]) == (
        app_id,
        "record_answer",
        "ok",
        None,
    )
    assert row["started_at"] == iso(NOW)
    assert row["duration_ms"] >= 0
    assert (row["run_id"], row["run_closed"]) == (None, 0)
    assert row["created_at"] == iso(NOW)


def test_started_at_follows_the_clock(client, conn, app_id, clock) -> None:
    clock.advance(minutes=7, seconds=3)
    post(client, "get_application", session_id=app_id)
    assert rows(conn)[0]["started_at"] == iso(NOW + timedelta(minutes=7, seconds=3))


def test_an_invalid_value_is_an_invalid_row(client, conn, app_id) -> None:
    response = post(
        client, "record_answer", {"field": "start_date", "value": "2026-10-01"}, session_id=app_id
    )
    assert response.json()["error"] == "invalid_value"
    (row,) = rows(conn)
    assert (row["outcome"], row["error"]) == ("invalid", "invalid_value")


def test_a_knockout_is_a_knockout_row_not_an_error(client, conn, app_id) -> None:
    post(client, "record_answer", {"field": "full_name", "value": "Ana García"}, session_id=app_id)
    response = post(
        client, "record_answer", {"field": "driver_license", "value": "no"}, session_id=app_id
    )
    assert response.json()["ok"] is True
    assert response.json()["value"]["status"] == "knockout"
    knockout = rows(conn)[-1]
    assert (knockout["outcome"], knockout["error"]) == ("knockout", None)
    assert args_of(knockout) == {"field": "driver_license", "value": "no"}


def test_a_wrong_stage_is_an_error_row(client, conn, app_id) -> None:
    response = post(client, "request_review", session_id=app_id)
    assert response.json()["error"] == "wrong_stage"
    (row,) = rows(conn)
    assert (row["tool"], row["outcome"], row["error"]) == ("request_review", "error", "wrong_stage")


def test_an_internal_error_is_an_error_row(client, conn, app_id, monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setitem(dispatch.TOOLS, "get_application", boom)
    assert post(client, "get_application", session_id=app_id).json()["error"] == "internal"
    (row,) = rows(conn)
    assert (row["outcome"], row["error"]) == ("error", "internal")
    assert "disk" not in json.dumps(dict(row))


def test_a_dispatch_level_invalid_args_on_a_known_session_is_one_invalid_row(
    client, conn, app_id
) -> None:
    response = post(client, "get_application", {"candidate_id": "cand_1"}, session_id=app_id)
    assert response.status_code == 422
    (row,) = rows(conn)
    assert (row["outcome"], row["error"]) == ("invalid", "invalid_args")
    assert args_of(row) == {}


def test_a_dispatch_level_invalid_args_on_an_unknown_session_writes_nothing(client, conn) -> None:
    response = post(client, "get_application", {"candidate_id": "cand_1"}, session_id="app_nope")
    assert response.status_code == 422
    assert rows(conn) == []


# --- requests that leave no row ---------------------------------------------


def test_refused_requests_and_unknown_sessions_leave_no_row(client, conn, app_id) -> None:
    assert post(client, "get_application", session_id=app_id, headers={}).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert post(client, "get_application", session_id=app_id, headers=bad).status_code == 401
    unknown = client.post(
        "/internal/tools/delete_everything",
        json={"tool": "delete_everything", "args": {}, "session_id": app_id},
        headers=AUTH,
    )
    assert unknown.status_code == 404
    oversize = post(client, "search_zones", {"query": "x" * (70 * 1024)}, session_id=app_id)
    assert oversize.status_code == 422
    envelope = post(client, "get_application", session_id=app_id, application_id="a")
    assert envelope.status_code == 422
    not_json = client.post(
        "/internal/tools/get_application",
        content=b"{not json",
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert not_json.status_code == 422
    gone = post(client, "get_application", session_id="app_nope")
    assert gone.json()["error"] == "unknown_session"
    assert rows(conn) == []


# --- a failing trace write never changes the call ---------------------------


def _fail_on_second_connection(db_path: Path):
    """A connection factory whose second connection, the trace's, raises."""
    opened: list[int] = []

    def factory() -> sqlite3.Connection:
        opened.append(1)
        if len(opened) == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return connect(db_path)

    return factory, opened


def test_a_trace_write_that_raises_leaves_the_response_and_after_call_as_they_were(
    client, conn, app_id, db_path, monkeypatch
) -> None:
    after: list[tuple[str, str]] = []
    client.app.dependency_overrides[tools_http.get_after_call] = lambda: (
        lambda tool, session, _body: after.append((tool, session))
    )
    with monkeypatch.context() as patched:
        patched.setattr(trace, "record", lambda *_a, **_k: None)
        untraced = post(client, "get_application", session_id=app_id)
    assert after == [("get_application", app_id)]
    assert rows(conn) == []

    factory, opened = _fail_on_second_connection(db_path)
    client.app.dependency_overrides[tools_http.get_connection_factory] = lambda: factory
    traced = post(client, "get_application", session_id=app_id)
    assert len(opened) == 2
    assert (traced.status_code, traced.content) == (untraced.status_code, untraced.content)
    assert after == [("get_application", app_id)] * 2
    assert rows(conn) == []


def test_a_held_write_lock_does_not_hold_the_response(
    client, conn, app_id, db_path, caplog
) -> None:
    blockers: list[sqlite3.Connection] = []
    opened: list[int] = []

    def factory() -> sqlite3.Connection:
        opened.append(1)
        if len(opened) == 2:
            blocker = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
            blocker.execute("BEGIN IMMEDIATE")
            blockers.append(blocker)
        return connect(db_path)

    client.app.dependency_overrides[tools_http.get_connection_factory] = lambda: factory
    start = time.monotonic()
    try:
        response = post(client, "get_application", session_id=app_id)
        elapsed = time.monotonic() - start
    finally:
        for blocker in blockers:
            blocker.execute("ROLLBACK")
            blocker.close()
    assert response.status_code == 200 and response.json()["ok"] is True
    assert elapsed < 1.0
    assert rows(conn) == []
    assert "tool trace" in caplog.text


# --- arguments: names and canonical values only -----------------------------

ALLOWED_KEYS = {
    ("get_application", "language"),
    ("search_zones", "country"),
    ("record_answer", "field"),
    ("raise_escalation", "reason"),
    ("record_backout", "kind"),
    ("offer_interview_slots", "reschedule"),
}


def _resolved(schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        node: Any = contract()
        for part in schema["$ref"][2:].split("/"):
            node = node[part]
        schema = {**node, **{k: v for k, v in schema.items() if k != "$ref"}}
    return schema


def test_the_allow_list_is_what_the_spec_names() -> None:
    assert {(t, k) for t, keys in trace.ALLOWED_ARGS.items() for k in keys} == ALLOWED_KEYS


def test_every_allowed_key_is_an_enum_or_a_boolean_in_tools_json() -> None:
    for tool, key in sorted(ALLOWED_KEYS):
        schema = _resolved(input_schema(tool)["properties"][key])
        assert "enum" in schema or schema.get("type") == "boolean", (tool, key, schema)


def test_every_key_of_every_tool_is_listed_the_answer_value_or_stored_null() -> None:
    seen = 0
    for tool in contract()["x-tools"]:
        for key in input_schema(tool).get("properties", {}):
            seen += 1
            if (tool, key) in ALLOWED_KEYS:
                continue
            stored = trace.stored_args(tool, {key: "free words, Ana García"}, "error")
            assert stored == {key: None}, (tool, key)
    assert seen >= 12


def test_a_listed_value_that_fails_its_own_schema_is_stored_null() -> None:
    assert trace.stored_args("raise_escalation", {"reason": "bored"}, "invalid") == {"reason": None}
    assert trace.stored_args("raise_escalation", {"reason": "abuse"}, "ok") == {"reason": "abuse"}
    assert trace.stored_args("search_zones", {"country": "FR"}, "invalid") == {"country": None}
    assert trace.stored_args("offer_interview_slots", {"reschedule": "yes"}, "invalid") == {
        "reschedule": None
    }
    assert trace.stored_args("offer_interview_slots", {"reschedule": True}, "ok") == {
        "reschedule": True
    }
    assert trace.stored_args("get_application", {"language": "en"}, "ok") == {"language": "en"}
    assert trace.stored_args("record_backout", {"kind": ["opted_out"]}, "invalid") == {"kind": None}


def test_a_key_outside_the_tools_schema_is_dropped() -> None:
    args = {"reason": "other", "application_id": "app_1", "candidate": "cand_1"}
    assert trace.stored_args("raise_escalation", args, "invalid") == {"reason": "other"}
    assert trace.stored_args("finish_screening", {"anything": 1}, "invalid") == {}
    assert trace.stored_args("get_application", "not a dict", "invalid") == {}


def test_free_text_keys_keep_their_names_and_never_their_values(client, conn, app_id) -> None:
    secret = "Ana García llamó desde Chamberí"
    post(client, "search_zones", {"query": secret, "country": "ES"}, session_id=app_id)
    post(client, "request_review", {"note": secret}, session_id=app_id)
    post(client, "raise_escalation", {"reason": "other", "note": secret}, session_id=app_id)
    post(client, "record_backout", {"kind": "opted_out", "note": secret}, session_id=app_id)
    post(client, "book_interview", {"slot_ref": "slot-token-abc"}, session_id=app_id)
    stored = {r["tool"]: args_of(r) for r in rows(conn)}
    assert stored == {
        "search_zones": {"query": None, "country": "ES"},
        "request_review": {"note": None},
        "raise_escalation": {"reason": "other", "note": None},
        "record_backout": {"kind": "opted_out", "note": None},
        "book_interview": {"slot_ref": None},
    }
    everything = " ".join(r["args"] for r in rows(conn))
    assert "Chamberí" not in everything and "slot-token" not in everything


def test_record_answer_value_is_stored_for_an_accepted_canonical_answer(
    client, conn, app_id
) -> None:
    post(client, "record_answer", {"field": "full_name", "value": "Ana García"}, session_id=app_id)
    zone_value = ["zone_es_madrid_chamberi"]
    post(client, "record_answer", {"field": "zone", "value": zone_value}, session_id=app_id)
    post(client, "record_answer", {"field": "driver_license", "value": "yes"}, session_id=app_id)
    name, zone, licence = rows(conn)
    assert args_of(name) == {"field": "full_name", "value": None}
    assert args_of(zone) == {"field": "zone", "value": ["zone_es_madrid_chamberi"]}
    assert args_of(licence) == {"field": "driver_license", "value": "yes"}


def test_record_answer_value_is_null_for_a_rejected_shape_and_an_invalid_value(
    client, conn, app_id
) -> None:
    shape = post(
        client,
        "record_answer",
        {"field": "driver_license", "value": "sí tengo carnet"},
        session_id=app_id,
    )
    assert shape.status_code == 422
    value = post(
        client, "record_answer", {"field": "start_date", "value": "2026-10-01"}, session_id=app_id
    )
    assert value.json()["error"] == "invalid_value"
    bad_shape, bad_value = rows(conn)
    assert (bad_shape["outcome"], args_of(bad_shape)) == (
        "invalid",
        {"field": "driver_license", "value": None},
    )
    assert (bad_value["outcome"], args_of(bad_value)) == (
        "invalid",
        {"field": "start_date", "value": None},
    )


def test_record_answer_value_needs_a_valid_field_and_a_shape_that_fits_it() -> None:
    stored = trace.stored_args
    assert stored("record_answer", {"field": "driver_license", "value": "yes"}, "ok") == {
        "field": "driver_license",
        "value": "yes",
    }
    assert stored("record_answer", {"field": "experience_years", "value": 2}, "knockout") == {
        "field": "experience_years",
        "value": 2,
    }
    assert stored("record_answer", {"field": "full_name", "value": "Ana"}, "ok")["value"] is None
    assert stored("record_answer", {"field": "driver_license", "value": "yes"}, "error") == {
        "field": "driver_license",
        "value": None,
    }
    assert stored("record_answer", {"field": "salary", "value": "1000"}, "ok") == {
        "field": None,
        "value": None,
    }
    assert stored("record_answer", {"field": "availability", "value": "words"}, "ok") == {
        "field": "availability",
        "value": None,
    }
    assert stored("record_answer", {"value": "yes"}, "ok") == {"value": None}


def test_outcome_mapping() -> None:
    ok = {"ok": True, "value": {"status": "accepted"}}
    knockout = {"ok": True, "value": {"status": "knockout", "outcome": "knockout"}}
    assert trace.outcome_of(ok) == ("ok", None)
    assert trace.outcome_of(knockout) == ("knockout", None)
    assert trace.outcome_of({"ok": True, "value": {}}) == ("ok", None)
    for code in ("invalid_args", "invalid_value"):
        assert trace.outcome_of({"ok": False, "error": code}) == ("invalid", code)
    for code in ("wrong_stage", "human_in_control", "application_closed", "internal", "no_slots"):
        assert trace.outcome_of({"ok": False, "error": code}) == ("error", code)


# --- cost on the tool path --------------------------------------------------


def test_the_trace_adds_little_to_a_call(client, app_id, monkeypatch) -> None:
    def median_ms(calls: int) -> float:
        times = []
        for _ in range(calls):
            start = time.perf_counter()
            assert post(client, "get_application", session_id=app_id).status_code == 200
            times.append((time.perf_counter() - start) * 1000)
        return statistics.median(times)

    post(client, "get_application", session_id=app_id)  # warm the app and the route
    traced = median_ms(100)
    monkeypatch.setattr(trace, "record", lambda *_a, **_k: None)
    untraced = median_ms(100)
    added = traced - untraced
    print(
        f"\ntool trace: median {traced:.2f} ms traced, {untraced:.2f} ms untraced, +{added:.2f} ms"
    )
    assert added < 50
