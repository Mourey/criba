"""The Operator's agent tool-call trace (``GET /api/applications/{id}/tool-calls``, ADR 0069):
grouped by run, with each run's tokens and cost read from its usage row."""

from __future__ import annotations

from typing import Any

from criba.db import repo
from criba.db.connection import iso

from .support import PUEBLA, at

URL = "/api/applications/{}/tool-calls"
CALL_KEYS = {"tool", "started_at", "duration_ms", "outcome", "error", "args"}
RUN_KEYS = {
    "run_id",
    "status",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "latency_ms",
    "at",
    "hermes_profile",
    "model",
    "reply_message_ids",
    "calls",
}


def call(
    env: Any,
    app_id: str,
    *,
    minutes: float,
    run_id: str | None = None,
    run_closed: int = 0,
    tool: str = "get_application",
    outcome: str = "ok",
    error: str | None = None,
    args: str = "{}",
    duration_ms: int = 5,
) -> None:
    env.conn.execute(
        "INSERT INTO tool_calls (application_id, run_id, run_closed, tool, started_at,"
        " duration_ms, outcome, error, args, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            app_id,
            run_id,
            run_closed,
            tool,
            iso(at(minutes)),
            duration_ms,
            outcome,
            error,
            args,
            iso(at(minutes)),
        ),
    )


def reply(env: Any, app: dict[str, Any], *, run_id: str | None, body: str = "Hola") -> int:
    message, _ = repo.add_message(
        env.conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        channel="webchat",
        direction="outbound",
        sender="agent",
        body=body,
        run_id=run_id,
        now=at(0),
    )
    return message["id"]


def trace(client: Any, contract: Any, app_id: str) -> dict[str, Any]:
    response = client.get(URL.format(app_id))
    assert response.status_code == 200, response.text
    return contract.conforms(response, "getToolCalls")


def test_runs_come_oldest_first_each_with_its_calls_in_id_order(
    env, build, operator, contract
) -> None:
    app = build.application(stage="greeting")
    # Written out of time order on purpose: grouping follows the run, ordering the clock.
    call(env, app["id"], minutes=21, run_id="run_b", tool="record_answer")
    call(env, app["id"], minutes=11, run_id="run_a", tool="get_application")
    call(env, app["id"], minutes=12, run_id="run_a", tool="search_zones")
    call(env, app["id"], minutes=13, run_id="run_a", tool="record_answer")
    build.usage(application_id=app["id"], run_id="run_a", ts=iso(at(10)))
    build.usage(application_id=app["id"], run_id="run_b", ts=iso(at(20)))
    body = trace(operator, contract, app["id"])
    assert body["application_id"] == app["id"]
    assert [r["run_id"] for r in body["runs"]] == ["run_a", "run_b"]
    assert [c["tool"] for c in body["runs"][0]["calls"]] == [
        "get_application",
        "search_zones",
        "record_answer",
    ]
    assert [c["tool"] for c in body["runs"][1]["calls"]] == ["record_answer"]
    assert all(set(r) == RUN_KEYS for r in body["runs"])
    assert all(set(c) == CALL_KEYS for r in body["runs"] for c in r["calls"])


def test_a_call_carries_its_outcome_error_and_canonical_args(
    env, build, operator, contract
) -> None:
    app = build.application(stage="greeting")
    call(
        env,
        app["id"],
        minutes=1,
        run_id="run_a",
        tool="record_answer",
        outcome="invalid",
        error="invalid_value",
        args='{"field": "start_date", "value": null}',
        duration_ms=17,
    )
    build.usage(application_id=app["id"], run_id="run_a", ts=iso(at(0)))
    [run] = trace(operator, contract, app["id"])["runs"]
    assert run["calls"] == [
        {
            "tool": "record_answer",
            "started_at": iso(at(1)),
            "duration_ms": 17,
            "outcome": "invalid",
            "error": "invalid_value",
            "args": {"field": "start_date", "value": None},
        }
    ]


def test_tokens_cost_status_and_latency_come_from_the_runs_usage_row(
    env, build, operator, contract
) -> None:
    app = build.application(stage="greeting")
    call(env, app["id"], minutes=1, run_id="run_a")
    call(env, app["id"], minutes=11, run_id="run_b")
    build.usage(
        application_id=app["id"],
        run_id="run_a",
        ts=iso(at(0)),
        input_tokens=7200,
        output_tokens=410,
        est_cost_usd=0.0031,
        latency_ms=1700,
        status="ok",
    )
    # One run id can also have rows of another kind: they are not the run's own.
    build.usage(
        application_id=app["id"],
        run_id="run_a",
        ts=iso(at(0)),
        kind="auxiliary",
        input_tokens=999,
        output_tokens=999,
        est_cost_usd=9.0,
        latency_ms=1,
    )
    build.usage(
        application_id=app["id"],
        run_id="run_b",
        ts=iso(at(10)),
        input_tokens=120,
        output_tokens=0,
        est_cost_usd=0.0005,
        latency_ms=30000,
        status="timeout",
    )
    first, second = trace(operator, contract, app["id"])["runs"]
    assert (first["status"], first["input_tokens"], first["output_tokens"]) == ("ok", 7200, 410)
    assert (first["cost_usd"], first["latency_ms"]) == (0.0031, 1700)
    assert (second["status"], second["input_tokens"], second["output_tokens"]) == (
        "timeout",
        120,
        0,
    )
    assert (second["cost_usd"], second["latency_ms"]) == (0.0005, 30000)


def test_reply_message_ids_are_the_replies_that_carry_the_run_id(
    env, build, operator, contract
) -> None:
    app = build.application(stage="greeting")
    other = build.application(stage="greeting")
    call(env, app["id"], minutes=1, run_id="run_a")
    build.usage(application_id=app["id"], run_id="run_a", ts=iso(at(0)))
    first = reply(env, app, run_id="run_a", body="Primera")
    second = reply(env, app, run_id="run_a", body="Segunda")
    reply(env, app, run_id="run_zzz", body="De otra corrida")
    reply(env, app, run_id=None, body="Sin corrida")
    reply(env, other, run_id="run_a", body="De otra solicitud")
    [run] = trace(operator, contract, app["id"])["runs"]
    assert run["reply_message_ids"] == [first, second]


def test_the_last_group_has_no_run_and_holds_unlinked_and_closed_calls(
    env, build, operator, contract
) -> None:
    app = build.application(stage="greeting")
    call(env, app["id"], minutes=1, run_id="run_a")
    build.usage(application_id=app["id"], run_id="run_a", ts=iso(at(0)))
    call(env, app["id"], minutes=2, tool="search_zones")  # a late call: unlinked
    call(env, app["id"], minutes=3, tool="record_answer", run_closed=1)  # closed, no run id
    body = trace(operator, contract, app["id"])
    assert [r["run_id"] for r in body["runs"]] == ["run_a", None]
    last = body["runs"][-1]
    assert [c["tool"] for c in last["calls"]] == ["search_zones", "record_answer"]
    assert last["status"] is None
    assert (last["input_tokens"], last["output_tokens"]) == (None, None)
    assert (last["cost_usd"], last["latency_ms"]) == (None, None)
    assert last["reply_message_ids"] == []


def test_the_unlinked_group_comes_last_even_when_its_calls_are_older(
    env, build, operator, contract
) -> None:
    app = build.application(stage="greeting")
    call(env, app["id"], minutes=1)
    call(env, app["id"], minutes=50, run_id="run_a")
    build.usage(application_id=app["id"], run_id="run_a", ts=iso(at(49)))
    runs = trace(operator, contract, app["id"])["runs"]
    assert [r["run_id"] for r in runs] == ["run_a", None]


def test_a_run_with_calls_and_no_usage_row_is_listed_with_null_numbers(
    env, build, operator, contract
) -> None:
    app = build.application(stage="greeting")
    call(env, app["id"], minutes=30, run_id="run_lost")
    build.usage(application_id=app["id"], run_id="run_a", ts=iso(at(40)))
    call(env, app["id"], minutes=41, run_id="run_a")
    first, second = trace(operator, contract, app["id"])["runs"]
    assert first["run_id"] == "run_lost"
    assert (first["status"], first["input_tokens"], first["cost_usd"], first["latency_ms"]) == (
        None,
        None,
        None,
        None,
    )
    assert len(first["calls"]) == 1
    assert second["run_id"] == "run_a" and second["status"] == "ok"


def test_a_run_with_a_usage_row_and_no_calls_is_listed_with_no_calls(
    env, build, operator, contract
) -> None:
    app = build.application(stage="greeting")
    build.usage(application_id=app["id"], run_id="run_a", ts=iso(at(0)), input_tokens=11)
    [run] = trace(operator, contract, app["id"])["runs"]
    assert (run["run_id"], run["input_tokens"], run["calls"]) == ("run_a", 11, [])


def test_runs_are_ordered_by_the_earlier_of_usage_time_and_first_call_then_by_id(
    env, build, operator, contract
) -> None:
    app = build.application(stage="greeting")
    # run_x finished late (usage at 30) but its first call was at 1; run_y's usage is at 5.
    build.usage(application_id=app["id"], run_id="run_x", ts=iso(at(30)))
    call(env, app["id"], minutes=1, run_id="run_x")
    build.usage(application_id=app["id"], run_id="run_y", ts=iso(at(5)))
    call(env, app["id"], minutes=6, run_id="run_y")
    # run_t and run_u tie on time: the earlier usage row goes first.
    build.usage(application_id=app["id"], run_id="run_u", ts=iso(at(8)))
    build.usage(application_id=app["id"], run_id="run_t", ts=iso(at(8)))
    runs = trace(operator, contract, app["id"])["runs"]
    assert [r["run_id"] for r in runs] == ["run_x", "run_y", "run_u", "run_t"]


def test_another_applications_usage_and_calls_do_not_appear(env, build, operator, contract) -> None:
    app, other = build.application(stage="greeting"), build.application(stage="greeting")
    call(env, other["id"], minutes=1, run_id="run_o")
    build.usage(application_id=other["id"], run_id="run_o", ts=iso(at(0)))
    call(env, app["id"], minutes=2, run_id="run_a")
    build.usage(application_id=app["id"], run_id="run_a", ts=iso(at(0)))
    assert [r["run_id"] for r in trace(operator, contract, app["id"])["runs"]] == ["run_a"]


def test_an_application_without_calls_has_no_runs(env, build, operator, contract) -> None:
    app = build.application(stage="greeting")
    assert trace(operator, contract, app["id"]) == {
        "application_id": app["id"],
        "runs": [],
        "jev_calls": [],
    }


def test_no_hint_value_or_message_text_is_returned(env, build, operator, contract) -> None:
    app = build.application(stage="greeting")
    call(
        env,
        app["id"],
        minutes=1,
        run_id="run_a",
        tool="request_review",
        args='{"note": null}',
    )
    build.usage(application_id=app["id"], run_id="run_a", ts=iso(at(0)))
    reply(env, app, run_id="run_a", body="qzxmensaje del agente")
    env.conn.execute(
        "UPDATE usage SET error = 'qzxerror text' WHERE application_id = ?", (app["id"],)
    )
    response = operator.get(URL.format(app["id"]))
    assert response.status_code == 200
    assert "qzx" not in response.text
    assert '"hint"' not in response.text
    [call_row] = response.json()["runs"][0]["calls"]
    assert set(call_row) == CALL_KEYS and call_row["args"] == {"note": None}


def test_a_recruiter_is_refused_with_403_even_in_scope(env, build, contract) -> None:
    app = build.application(stage="greeting")
    call(env, app["id"], minutes=1, run_id="run_a")
    response = env.client(PUEBLA).get(URL.format(app["id"]))
    assert response.status_code == 403
    contract.conforms(response, "getToolCalls")
    assert "run_a" not in response.text


def test_an_anonymous_request_is_401(env, build, contract) -> None:
    app = build.application(stage="greeting")
    response = env.client().get(URL.format(app["id"]))
    assert response.status_code == 401
    contract.conforms(response, "getToolCalls")


def test_an_unknown_application_is_404_for_an_operator(operator, contract) -> None:
    response = operator.get(URL.format("app_nope"))
    assert response.status_code == 404
    contract.conforms(response, "getToolCalls")


def test_the_application_detail_is_unchanged_by_the_trace(env, build, operator) -> None:
    app = build.application(stage="qualified")
    before = operator.get(f"/api/applications/{app['id']}").json()
    call(env, app["id"], minutes=1, run_id="run_a")
    build.usage(application_id=app["id"], run_id="run_a", ts=iso(at(0)))
    after = operator.get(f"/api/applications/{app['id']}").json()
    assert "tool_calls" not in after and "runs" not in after
    before.pop("cost", None)
    after.pop("cost", None)
    assert after == before


def test_populated_args_conform_to_the_typed_contract(env, build, operator, contract) -> None:
    # Every value kind the trace stores: an enum code, a boolean, a number, a list of codes, null.
    app = build.application(stage="greeting")
    call(
        env,
        app["id"],
        minutes=1,
        run_id="run_a",
        tool="get_application",
        args='{"language": "es-MX"}',
    )
    call(
        env,
        app["id"],
        minutes=2,
        run_id="run_a",
        tool="offer_interview_slots",
        args='{"reschedule": true}',
    )
    call(
        env,
        app["id"],
        minutes=3,
        run_id="run_a",
        tool="record_answer",
        args='{"field": "experience_years", "value": 2.5}',
    )
    call(
        env,
        app["id"],
        minutes=4,
        run_id="run_a",
        tool="record_answer",
        args='{"field": "zone", "value": ["zone_mx_pue_centro", "zone_mx_pue_sur"]}',
    )
    call(env, app["id"], minutes=5, run_id="run_a", tool="search_zones", args='{"query": null}')
    build.usage(application_id=app["id"], run_id="run_a", ts=iso(at(0)))
    [run] = trace(operator, contract, app["id"])["runs"]
    assert [c["args"] for c in run["calls"]] == [
        {"language": "es-MX"},
        {"reschedule": True},
        {"field": "experience_years", "value": 2.5},
        {"field": "zone", "value": ["zone_mx_pue_centro", "zone_mx_pue_sur"]},
        {"query": None},
    ]


def test_the_contract_types_args_values_no_wider_than_the_trace_stores(contract) -> None:
    def call_with(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "tool": "record_answer",
            "started_at": "2026-10-11T09:31:02Z",
            "duration_ms": 9,
            "outcome": "ok",
            "error": None,
            "args": args,
        }

    for args in ({"a": "es"}, {"a": 3}, {"a": 2.5}, {"a": False}, {"a": None}, {"a": ["x"]}):
        contract.validate("ToolCall", call_with(args))
    for args in ({"a": {"nested": "x"}}, {"a": [1]}, {"a": [["x"]]}, {"a": [None]}):
        try:
            contract.validate("ToolCall", call_with(args))
        except AssertionError:
            continue
        raise AssertionError(f"ToolCall.args accepted {args!r}")


# ------------------------------------------------------------- Agent activity (ADR 0075)


def test_a_run_carries_its_usage_time_profile_and_model(env, build, operator, contract) -> None:
    app = build.application(stage="greeting")
    call(env, app["id"], minutes=1, run_id="run_a")
    call(env, app["id"], minutes=2, run_id="run_orphan")
    build.usage(
        application_id=app["id"],
        run_id="run_a",
        ts=iso(at(0)),
        profile="criba-recruiter",
        model="anthropic/claude-haiku-5.5",
    )
    runs = {r["run_id"]: r for r in trace(operator, contract, app["id"])["runs"]}
    assert runs["run_a"]["at"] == iso(at(0))
    assert runs["run_a"]["hermes_profile"] == "criba-recruiter"
    assert runs["run_a"]["model"] == "anthropic/claude-haiku-5.5"
    assert (runs["run_orphan"]["at"], runs["run_orphan"]["hermes_profile"]) == (None, None)
    assert runs["run_orphan"]["model"] is None


def test_the_jev_calls_are_the_applications_jev_usage_rows_with_no_reading(
    env, build, operator, contract
) -> None:
    app = build.application(stage="greeting")
    other = build.application(stage="greeting", name="Otra Persona")
    build.usage(
        application_id=app["id"], kind="jev", ts=iso(at(1)), model="typesafe/jev-1.13",
        latency_ms=312, status="ok", reconciled_cost_usd=0.00008, input_tokens=2100,
    )  # fmt: skip
    build.usage(
        application_id=app["id"], kind="jev", ts=iso(at(3)), model="typesafe/jev-1.13",
        latency_ms=1200, status="timeout",
    )  # fmt: skip
    build.usage(application_id=app["id"], kind="run", run_id="run_a", ts=iso(at(2)))
    build.usage(application_id=other["id"], kind="jev", ts=iso(at(1)), model="other")
    body = trace(operator, contract, app["id"])
    assert body["jev_calls"] == [
        {"at": iso(at(1)), "model": "typesafe/jev-1.13", "latency_ms": 312, "status": "ok"},
        {"at": iso(at(3)), "model": "typesafe/jev-1.13", "latency_ms": 1200, "status": "timeout"},
    ]
