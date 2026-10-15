"""Results (scoped) and economics (Operators only) aggregates."""

from __future__ import annotations

import pytest

from criba.db import repo

from .support import NOW, OPERATOR, PUEBLA, PUEBLA_ANGELOPOLIS, PUEBLA_CENTRO, at


def partial(build, env, stage_name):
    """An open Application that has a Zone and sits at ``stage_name``."""
    app = build.application(stage="greeting")
    repo.record_answer(env.conn, app["id"], "zone", PUEBLA_CENTRO, now=NOW)
    repo.set_stage(env.conn, app["id"], stage_name, now=NOW)
    return app


@pytest.fixture
def funnel(env, build):
    """Six Applications created on 9 October: one in every state the dashboard counts."""
    rows = {}
    rows["qualified"] = build.application()
    rows["approved"] = build.application(stage="approved")
    rows["knockout_license"] = build.application(stage="knockout_license")
    rows["knockout_zone"] = build.application(stage="knockout_zone")
    rows["abandoned"] = partial(build, env, "zone")
    repo.close_application(env.conn, rows["abandoned"]["id"], "abandoned", now=NOW)
    rows["stalled"] = partial(build, env, "name")
    env.conn.execute(
        "UPDATE applications SET stalled_since = ? WHERE id = ?",
        ("2026-10-09T09:00:00Z", rows["stalled"]["id"]),
    )
    env.conn.execute(
        "UPDATE applications SET qualified_at = '2026-10-09T10:04:00Z' WHERE id = ?",
        (rows["qualified"]["id"],),
    )
    env.conn.execute(
        "UPDATE applications SET qualified_at = '2026-10-09T10:08:00Z' WHERE id = ?",
        (rows["approved"]["id"],),
    )
    env.conn.execute(
        "UPDATE applications SET qualified_waiting = 1 WHERE id = ?", (rows["qualified"]["id"],)
    )
    # Review: one requested and Upheld, one requested and Overturned, one still open.
    for key in ("knockout_license", "knockout_zone"):
        build.queue_item(rows[key]["id"], "review")
    for kind in ("review_upheld", "review_overturned", "review_overturned"):
        repo.add_event(
            env.conn,
            candidate_id=rows["knockout_license"]["candidate_id"],
            application_id=rows["knockout_license"]["id"],
            kind=kind,
            actor="system",
            now=NOW,
        )
    build.queue_item(rows["qualified"]["id"], "escalation", reason="wants_human")
    env.client(PUEBLA).post(f"/api/applications/{rows['qualified']['id']}/takeover")
    return rows


def test_results_for_an_operator(env, funnel, contract):
    response = env.client(OPERATOR).get("/api/metrics/results")
    body = contract.conforms(response, "getResults")
    assert (body["from"], body["to"]) == ("2026-09-10", "2026-10-09")
    assert body["applications"] == 6
    assert body["funnel"]["open_by_stage"] == {
        "greeting": 0,
        "name": 1,
        "zone": 0,
        "driver_license": 0,
        "availability": 0,
        "preferred_schedule": 0,
        "experience": 0,
        "start_date": 0,
        "confirm": 0,
        "qualified": 1,
        "approved": 1,
        "interview_booked": 0,
    }
    assert body["funnel"]["outcomes"] == {
        "knockout": 2,
        "rejection": 0,
        "abandoned": 1,
        "withdrawn": 0,
    }
    assert body["knockouts_by_criterion"] == {"zone": 1, "driver_license": 1}
    assert body["time_to_qualify_seconds"] == {"p50": 360.0, "p95": 468.0, "p99": 477.6}
    assert body["reviews"] == {
        "requested": 2,
        "upheld": 1,
        "overturned": 2,
        "overturn_rate": 0.6667,
    }
    assert body["dropoff_by_stage"]["zone"] == 1  # the Abandoned one
    assert body["dropoff_by_stage"]["name"] == 1  # the Stalled one
    assert sum(body["dropoff_by_stage"].values()) == 2
    assert body["qualified_waiting"] == 1
    assert body["takeovers"] == 1
    assert body["escalations"] == 1


def test_results_by_location(env, funnel, contract):
    body = contract.conforms(env.client(OPERATOR).get("/api/metrics/results"), "getResults")
    by_location = {row["location_id"]: row for row in body["by_location"]}
    assert set(by_location) == {
        "loc_mx_puebla_angelopolis",
        "loc_mx_puebla_centro",
        "loc_mx_puebla_la_paz",
    }
    # The zone-Knockout has no covering Location; the others all sit in Centro's area.
    assert by_location["loc_mx_puebla_centro"] == {
        "location_id": "loc_mx_puebla_centro",
        "applications": 5,
        "qualified": 2,
        "knockouts": 1,
    }
    only = env.client(OPERATOR).get(
        "/api/metrics/results", params={"location_id": "loc_mx_puebla_la_paz"}
    )
    assert [r["location_id"] for r in only.json()["by_location"]] == ["loc_mx_puebla_la_paz"]


def test_results_for_a_recruiter_exclude_applications_without_a_covering_location(
    env, funnel, contract
):
    body = contract.conforms(env.client(PUEBLA).get("/api/metrics/results"), "getResults")
    assert body["applications"] == 5  # the zone-Knockout is invisible to Recruiters
    assert body["knockouts_by_criterion"] == {"zone": 0, "driver_license": 1}
    assert body["funnel"]["outcomes"]["knockout"] == 1


def test_results_are_scoped_to_the_recruiters_locations(env, build, contract):
    narrow = build.application(zone=PUEBLA_ANGELOPOLIS)
    la_paz = env.client("recruiter.lapaz@sazon.example")
    body = contract.conforms(la_paz.get("/api/metrics/results"), "getResults")
    assert body["applications"] == 0
    assert body["qualified_waiting"] == 0
    assert body["by_location"] == []
    build.application()
    assert la_paz.get("/api/metrics/results").json()["applications"] == 1
    assert env.client(OPERATOR).get("/api/metrics/results").json()["applications"] == 2
    assert narrow["id"]


def test_results_period_and_filters(env, funnel):
    op = env.client(OPERATOR)
    assert (
        op.get("/api/metrics/results", params={"from": "2026-10-10", "to": "2026-10-12"}).json()[
            "applications"
        ]
        == 0
    )
    day = op.get("/api/metrics/results", params={"from": "2026-10-09", "to": "2026-10-09"}).json()
    assert (day["from"], day["to"], day["applications"]) == ("2026-10-09", "2026-10-09", 6)
    assert op.get("/api/metrics/results", params={"country": "ES"}).json()["applications"] == 0
    assert op.get("/api/metrics/results", params={"country": "MX"}).json()["applications"] == 6
    # Five of the six cover La Paz; the zone-Knockout covers no Location.
    assert (
        op.get("/api/metrics/results", params={"location_id": "loc_mx_puebla_la_paz"}).json()[
            "applications"
        ]
        == 5
    )
    assert (
        op.get(
            "/api/metrics/results", params={"from": "2026-10-10", "to": "2026-10-01"}
        ).status_code
        == 422
    )
    env.clock.set(at(60 * 24 * 40))
    assert op.get("/api/metrics/results").json()["applications"] == 0  # default window: 30 days


def test_empty_results(env, contract):
    body = contract.conforms(env.client(PUEBLA).get("/api/metrics/results"), "getResults")
    assert body["applications"] == 0
    assert body["time_to_qualify_seconds"] == {"p50": None, "p95": None, "p99": None}
    assert body["reviews"]["overturn_rate"] is None


# --- economics ---------------------------------------------------------------------------


@pytest.fixture
def ledger(env, build):
    a = build.application()
    f = build.application(stage="approved")
    haiku, sonnet = "anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5"
    build.usage(
        application_id=a["id"],
        input_tokens=1000,
        cache_read_tokens=700,
        est_cost_usd=0.002,
        latency_ms=1000,
    )
    build.usage(
        application_id=a["id"],
        input_tokens=1000,
        cache_read_tokens=700,
        est_cost_usd=0.004,
        reconciled_cost_usd=0.003,
        latency_ms=2000,
    )
    build.usage(
        application_id=f["id"],
        input_tokens=2000,
        cache_read_tokens=1000,
        est_cost_usd=0.001,
        latency_ms=3000,
        status="error",
    )
    build.usage(
        application_id=a["id"],
        kind="auxiliary",
        model=sonnet,
        input_tokens=500,
        cache_read_tokens=0,
        est_cost_usd=0.01,
        latency_ms=None,
    )
    build.usage(environment="sim", session_id="s1", est_cost_usd=0.5)
    build.usage(environment="test", session_id="t1", est_cost_usd=0.25, model=haiku)
    return a, f


def test_economics(env, ledger, contract):
    response = env.client(OPERATOR).get("/api/metrics/economics")
    body = contract.conforms(response, "getEconomics")
    assert body["conversations"] == 2
    assert body["cost_usd"] == {
        "total": 0.016,
        "per_conversation": 0.008,
        "per_qualified": 0.008,
        "per_interview": None,
    }
    assert body["tokens"] == {
        "input": 4500,
        "output": 200,
        "cache_read": 2400,
        "cache_write": 0,
        "per_turn_input": 1333.3,
        "cache_hit_rate": 0.5333,
    }
    assert body["latency_ms"]["p50"] == 2000
    assert body["latency_ms"]["p95"] == 2900
    assert body["errors"] == {"count": 1, "rate": 0.25, "retries": 0}
    mix = {(m["model"], m["kind"]): m for m in body["model_mix"]}
    assert mix[("anthropic/claude-haiku-5.5", "run")]["calls"] == 3
    assert mix[("anthropic/claude-haiku-5.5", "run")]["share"] == 0.75
    assert mix[("anthropic/claude-sonnet-5.5", "auxiliary")]["cost_usd"] == 0.01
    assert body["spend_by_environment"] == {"prod": 0.016, "sim": 0.5, "test": 0.25}


def _speech(build, application_id):
    """One post-call webhook row (voice-agent session-webhook SE12)."""
    build.usage(
        kind="speech",
        provider="elevenlabs",
        model="elevenlabs-convai",
        application_id=application_id,
        session_id="vs_" + "1" * 32,
        audio_seconds=24.0,
        est_cost_usd=0.032,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        latency_ms=None,
    )


def test_economics_counts_a_speech_row(env, build, ledger, contract):
    a, _ = ledger
    _speech(build, a["id"])
    body = contract.conforms(env.client(OPERATOR).get("/api/metrics/economics"), "getEconomics")
    assert body["conversations"] == 2
    assert body["cost_usd"]["total"] == 0.048
    assert body["cost_usd"]["per_conversation"] == 0.024
    mix = {(m["model"], m["kind"]): m for m in body["model_mix"]}
    assert mix[("elevenlabs-convai", "speech")]["calls"] == 1
    assert mix[("elevenlabs-convai", "speech")]["cost_usd"] == 0.032


def test_a_speech_row_without_an_application_counts_in_total_only(env, build, ledger, contract):
    _speech(build, None)
    body = contract.conforms(env.client(OPERATOR).get("/api/metrics/economics"), "getEconomics")
    assert body["cost_usd"]["total"] == 0.048
    assert body["conversations"] == 2
    assert body["cost_usd"]["per_conversation"] == 0.024


def test_economics_environment_filter_and_default(env, ledger, make_env, contract):
    op = env.client(OPERATOR)
    sim = contract.conforms(
        op.get("/api/metrics/economics", params={"environment": "sim"}), "getEconomics"
    )
    assert sim["cost_usd"]["total"] == 0.5
    assert sim["conversations"] == 1
    # `spend_by_environment` always shows every environment, whatever the filter.
    assert sim["spend_by_environment"]["prod"] == 0.016


def test_economics_defaults_to_the_deployments_own_environment(make_env):
    env = make_env(env="sim")
    from .support import Builder

    build = Builder(env)
    build.usage(environment="sim", est_cost_usd=0.7, session_id="s")
    build.usage(environment="prod", est_cost_usd=0.1, session_id="p")
    body = env.client(OPERATOR).get("/api/metrics/economics").json()
    assert body["cost_usd"]["total"] == 0.7


def test_economics_counts_interviews(env, ledger):
    _, f = ledger
    slot = env.conn.execute(
        "SELECT id FROM interview_slots WHERE location_id = 'loc_mx_puebla_centro' LIMIT 1"
    ).fetchone()[0]
    env.ctx.services.booking.book(
        env.conn, application_id=f["id"], slot_id=slot, via="chat", now=NOW
    )
    body = env.client(OPERATOR).get("/api/metrics/economics").json()
    assert body["cost_usd"]["per_interview"] == 0.016


def test_economics_period_and_validation(env, ledger):
    op = env.client(OPERATOR)
    body = op.get(
        "/api/metrics/economics", params={"from": "2026-10-10", "to": "2026-10-12"}
    ).json()
    assert body["conversations"] == 0
    assert body["cost_usd"] == {
        "total": 0,
        "per_conversation": None,
        "per_qualified": None,
        "per_interview": None,
    }
    assert body["tokens"]["cache_hit_rate"] is None
    assert body["latency_ms"]["p50"] is None
    assert op.get("/api/metrics/economics", params={"environment": "staging"}).status_code == 422
    assert op.get("/api/metrics/economics", params={"source": "x"}).status_code == 422


def test_replay_source_and_comparison(env, build, contract):
    haiku, sonnet = "anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5"
    common = {"source": "replay", "dataset": "compare-2026-10-12", "environment": "test"}
    build.usage(session_id="p1", model=haiku, est_cost_usd=0.002, latency_ms=1000, **common)
    build.usage(
        session_id="p2", model=haiku, est_cost_usd=0.004, latency_ms=3000, status="error", **common
    )
    build.usage(session_id="p1", model=sonnet, est_cost_usd=0.01, latency_ms=2000, **common)
    build.usage(session_id="p2", model=sonnet, est_cost_usd=0.012, latency_ms=4000, **common)
    build.usage(session_id="live", est_cost_usd=9)  # a live row never shows in the replay
    op = env.client(OPERATOR)

    replay = contract.conforms(
        op.get(
            "/api/metrics/economics", params={"source": "replay", "dataset": "compare-2026-10-12"}
        ),
        "getEconomics",
    )
    assert replay["cost_usd"]["total"] == 0.028

    comparison = contract.conforms(op.get("/api/metrics/comparison"), "getModelComparison")
    assert comparison["dataset"] == "compare-2026-10-12"
    assert comparison["personas"] == 2
    by_model = {m["model"]: m for m in comparison["models"]}
    assert by_model[haiku]["runs"] == 2
    assert by_model[haiku]["cost_per_screening_usd"] == 0.003
    assert by_model[haiku]["latency_ms"]["p50"] == 2000
    assert by_model[haiku]["validation_failures"] == 1
    assert by_model[haiku]["outcome_accuracy"] == 0.5
    assert by_model[sonnet]["validation_failures"] == 0
    assert by_model[sonnet]["outcome_accuracy"] == 1.0
    named = op.get("/api/metrics/comparison", params={"dataset": "compare-2026-10-12"})
    assert named.json() == comparison


def test_comparison_without_a_dataset_is_404(env, contract):
    op = env.client(OPERATOR)
    for params in ({}, {"dataset": "nope"}):
        response = op.get("/api/metrics/comparison", params=params)
        assert contract.conforms(response, "getModelComparison")["error"] == "not_found"
        assert response.status_code == 404
