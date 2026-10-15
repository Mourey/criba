"""Load mode: the plan, the runner against the in-process fake web chat, the report and the CLI.

No stack and no model: the runner plays Candidates against `sim.fake_webchat`, and the read-back
returns the fake's ApplicationDetail with a `cost` block the test chooses.
"""

from __future__ import annotations

import asyncio
import json
import random
import subprocess
from typing import Any

import httpx
import pytest

from sim.driver import ChatError
from sim.fake_webchat import FakeWebChat
from sim.load import __main__ as cli
from sim.load.plan import (
    LoadPlan,
    load_personas_for,
    moves_clock,
    poisson_arrivals,
    schedule,
)
from sim.load.report import (
    CPU_SATURATED_PCT,
    compute,
    find_bottleneck,
    markdown,
    max_concurrency,
    pct,
)
from sim.load.runner import (
    CandidateResult,
    DockerStatsSampler,
    FrozenClock,
    LoadRun,
    LoadRunner,
    ResourceSample,
    TurnSample,
    classify_error,
    parse_mem_mib,
    parse_stats,
    summarise_record,
)
from sim.personas.loader import load_personas
from sim.spend import SpendGuard, SpendRefused

PERSONAS = load_personas()
STUB_COST = {
    "runs": 3,
    "models": ["stub-model", "deterministic-summary"],
    "est_cost_usd": 0.0,
}


# ---- plan --------------------------------------------------------------------------------------


def test_the_10k_plan_matches_adr_0028_rates() -> None:
    plan = LoadPlan(weekly_rate=10_000, sim_duration_s=3600, peak_factor=3, speedup=10)
    assert plan.sim_rate_per_s == pytest.approx(10_000 / 604_800 * 3)
    assert plan.wall_rate_per_s == pytest.approx(plan.sim_rate_per_s * 10)
    assert plan.wall_duration_s == 360
    assert plan.expected_arrivals == pytest.approx(178.6, abs=0.1)
    assert "10,000/week" in plan.describe()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"weekly_rate": 0, "sim_duration_s": 60},
        {"weekly_rate": 10, "sim_duration_s": 0},
        {"weekly_rate": 10, "sim_duration_s": 60, "speedup": 0},
        {"weekly_rate": 10, "sim_duration_s": 60, "peak_factor": -1},
    ],
)
def test_a_plan_refuses_non_positive_numbers(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        LoadPlan(**kwargs)


def test_poisson_arrivals_average_to_the_rate_and_are_reproducible() -> None:
    times = poisson_arrivals(2.0, 1000, random.Random(1))
    assert 1800 < len(times) < 2200
    assert times == sorted(times) and times[-1] < 1000
    assert times == poisson_arrivals(2.0, 1000, random.Random(1))
    assert poisson_arrivals(0, 1000, random.Random(1)) == []


def test_load_personas_leave_out_those_that_move_the_clock() -> None:
    usable = load_personas_for(PERSONAS)
    assert usable and set(usable) < set(PERSONAS)
    for pid, persona in PERSONAS.items():
        assert (pid in usable) == (not moves_clock(persona))
    assert "stalled_abandoned_es" not in usable
    assert "qualified_es" in usable


def test_schedule_gives_fresh_unique_numbers_from_the_mix() -> None:
    plan = LoadPlan(weekly_rate=10_000, sim_duration_s=3600, peak_factor=3, speedup=50, seed=3)
    arrivals = schedule(plan, PERSONAS, mix={"qualified_es": 1, "no_license_mx": 1})
    assert len(arrivals) > 100
    assert {a.persona.id for a in arrivals} == {"qualified_es", "no_license_mx"}
    assert len({a.phone for a in arrivals}) == len(arrivals)
    for a in arrivals:
        prefix = "+346" if a.persona.identity.country == "ES" else "+5255"
        assert a.phone.startswith(prefix) and a.phone != a.persona.identity.phone
    assert [a.index for a in arrivals] == list(range(len(arrivals)))
    again = schedule(plan, PERSONAS, mix={"qualified_es": 1, "no_license_mx": 1})
    assert [(a.at_s, a.phone) for a in again] == [(a.at_s, a.phone) for a in arrivals]


def test_schedule_refuses_unknown_clock_moving_or_empty_mixes() -> None:
    plan = LoadPlan(weekly_rate=10_000, sim_duration_s=600)
    with pytest.raises(ValueError, match="unknown"):
        schedule(plan, PERSONAS, mix={"nobody": 1})
    with pytest.raises(ValueError, match="move the sim clock"):
        schedule(plan, PERSONAS, mix={"stalled_abandoned_es": 1})
    with pytest.raises(ValueError, match="positive weight"):
        schedule(plan, PERSONAS, mix={"qualified_es": 0})


# ---- runner ------------------------------------------------------------------------------------


def _plan(n_per_s: float = 20.0, seconds: float = 0.5, seed: int = 1) -> LoadPlan:
    # A wall rate of n_per_s for `seconds`: weekly rate chosen to land there at speed-up 1.
    return LoadPlan(weekly_rate=n_per_s * 604_800, sim_duration_s=seconds, seed=seed)


def _enricher(fake: FakeWebChat, cost: dict[str, Any]) -> Any:
    async def enrich(phone: str) -> dict[str, Any]:
        app = fake.application_detail(phone)
        app["cost"] = dict(cost)
        return app

    return enrich


def _run(runner: LoadRunner, plan: LoadPlan, arrivals: list[Any]) -> LoadRun:
    return asyncio.run(runner.run(plan, arrivals))


def test_a_stub_load_run_plays_every_candidate_and_reads_them_back() -> None:
    fake = FakeWebChat()
    plan = _plan()
    arrivals = schedule(plan, PERSONAS, mix={"qualified_es": 1, "no_license_mx": 1})
    assert len(arrivals) >= 3

    async def go() -> LoadRun:
        async with fake.client() as http:
            runner = LoadRunner(http, enrich=_enricher(fake, STUB_COST), poll_wait_s=1)
            return await runner.run(plan, arrivals)

    run = asyncio.run(go())
    assert len(run.candidates) == len(arrivals)
    assert all(c.ok for c in run.candidates), [c.error for c in run.candidates]
    assert all(c.turns and c.conversation_id for c in run.candidates)
    assert {c.ended for c in run.candidates} <= {"completed", "knockout"}
    records = [c.record for c in run.candidates]
    assert all(r and r["correct"] for r in records), records
    assert all(r["models"] == STUB_COST["models"] for r in records if r)
    assert run.spent_usd == 0 and run.real is False and run.note is None
    assert run.to_dict()["plan"]["weekly_rate"] == plan.weekly_rate


def test_a_persona_with_a_say_only_interrupt_runs_on_the_frozen_clock() -> None:
    sayers = [p for p in load_personas_for(PERSONAS).values() if p.interrupts]
    assert sayers, "a load persona with an interrupt that only speaks"
    fake = FakeWebChat()
    plan = _plan(n_per_s=4, seconds=0.5)
    arrivals = schedule(plan, PERSONAS, mix={sayers[0].id: 1})

    async def go() -> LoadRun:
        async with fake.client() as http:
            return await LoadRunner(http, poll_wait_s=1).run(plan, arrivals)

    run = asyncio.run(go())
    assert all(c.error is None for c in run.candidates), [c.error for c in run.candidates]


def test_the_frozen_clock_refuses_to_move() -> None:
    with pytest.raises(RuntimeError, match="never moves"):
        asyncio.run(FrozenClock().advance("1h"))


def test_a_failed_candidate_is_a_data_point_not_a_crash() -> None:
    fake = FakeWebChat()
    plan = _plan(n_per_s=6, seconds=0.5)
    arrivals = schedule(plan, PERSONAS, mix={"qualified_es": 1})

    async def go() -> LoadRun:
        async with fake.client() as http:
            # A path the fake does not serve: every conversation start answers 404.
            broken = httpx.AsyncClient(transport=http._transport, base_url="http://fake/nowhere")
            async with broken:
                return await LoadRunner(broken, poll_wait_s=1).run(plan, arrivals)

    run = asyncio.run(go())
    assert run.candidates and all(c.ended == "error" for c in run.candidates)
    assert {c.error_kind for c in run.candidates} == {"http_404"}
    metrics = compute(run)
    assert metrics["errors"]["rate"] == 1.0
    assert metrics["errors"]["by_kind"] == {"http_404": len(run.candidates)}


def test_stub_mode_stops_after_the_probe_when_a_real_model_answers() -> None:
    fake = FakeWebChat()
    plan = _plan()
    arrivals = schedule(plan, PERSONAS, mix={"qualified_es": 1})
    real_cost = {
        "runs": 3,
        "models": ["anthropic/claude-haiku-5.5"],
        "est_cost_usd": 0.001,
    }

    async def go() -> None:
        async with fake.client() as http:
            await LoadRunner(http, enrich=_enricher(fake, real_cost), poll_wait_s=1).run(
                plan, arrivals
            )

    with pytest.raises(SpendRefused, match="not the stub"):
        asyncio.run(go())
    assert len(fake.convs) == 1  # only the probe ever started


def test_a_stub_run_reports_runs_hermes_refused_instead_of_aborting() -> None:
    fake = FakeWebChat()
    plan = _plan()
    arrivals = schedule(plan, PERSONAS, mix={"qualified_es": 1})
    refused = {
        "model": "unknown",
        "calls": 2,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "est_cost_usd": 0.0,
        "reconciled_cost_usd": None,
    }
    stub = {**refused, "model": "stub-model", "calls": 3, "input_tokens": 40}
    cost = {
        "runs": 5,
        "models": ["stub-model", "unknown"],
        "est_cost_usd": 0.0,
        "by_model": [stub, refused],
    }

    async def go() -> LoadRun:
        async with fake.client() as http:
            return await LoadRunner(http, enrich=_enricher(fake, cost), poll_wait_s=1).run(
                plan, arrivals
            )

    run = asyncio.run(go())  # no SpendRefused: nothing real served the stack
    assert all(c.record and c.record["refused_runs"] == 2 for c in run.candidates)
    assert run.spent_usd == 0
    metrics = compute(run)
    n = len(run.candidates)
    assert metrics["errors"]["by_kind"] == {"hermes_refused": n}
    assert metrics["errors"]["refused_runs"] == 2 * n
    assert metrics["errors"]["rate"] == 1.0 and metrics["candidates"]["failed"] == n


def test_stub_mode_still_stops_when_an_unknown_model_carries_tokens() -> None:
    fake = FakeWebChat()
    plan = _plan()
    arrivals = schedule(plan, PERSONAS, mix={"qualified_es": 1})
    leaky = {
        "model": "unknown",
        "calls": 1,
        "input_tokens": 500,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "est_cost_usd": 0.0,
        "reconciled_cost_usd": None,
    }
    cost = {"runs": 1, "models": ["unknown"], "est_cost_usd": 0.0, "by_model": [leaky]}

    async def go() -> None:
        async with fake.client() as http:
            await LoadRunner(http, enrich=_enricher(fake, cost), poll_wait_s=1).run(plan, arrivals)

    with pytest.raises(SpendRefused, match="served unknown"):
        asyncio.run(go())
    assert len(fake.convs) == 1  # only the probe ever started


def test_real_mode_stops_arrivals_before_the_cap() -> None:
    fake = FakeWebChat()
    plan = _plan(n_per_s=20, seconds=0.5)
    arrivals = schedule(plan, PERSONAS, mix={"qualified_es": 1})
    assert len(arrivals) >= 4
    # Each Candidate costs 10M input tokens of Haiku at the guard's price: $1.00.
    cost = {
        "runs": 3,
        "models": ["anthropic/claude-haiku-5.5"],
        "est_cost_usd": 0.0,
        "input_tokens": 10_000_000,
    }
    guard = SpendGuard(cap_usd=2.4, real=True)

    async def go() -> LoadRun:
        async with fake.client() as http:
            runner = LoadRunner(http, enrich=_enricher(fake, cost), guard=guard, poll_wait_s=1)
            return await runner.run(plan, arrivals)

    run = asyncio.run(go())
    assert guard.spent_usd <= 2.4
    assert run.spent_usd == pytest.approx(guard.spent_usd)
    capped = [c for c in run.candidates if c.ended == "spend_cap"]
    assert capped and run.note and "spend cap" in run.note
    assert compute(run)["candidates"]["spend_cap"] == len(capped)


def test_real_mode_prices_a_candidate_by_the_model_that_served_each_call() -> None:
    fake = FakeWebChat()
    plan = _plan(n_per_s=20, seconds=0.5)
    arrivals = schedule(plan, PERSONAS, mix={"qualified_es": 1})
    haiku, sonnet = "anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5"
    split = {
        "model": haiku,
        "input_tokens": 10_000_000,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "est_cost_usd": 0.0,
        "reconciled_cost_usd": None,
    }
    summary = {**split, "model": sonnet, "input_tokens": 0, "reconciled_cost_usd": 0.5}
    cost = {
        "runs": 3,
        "models": [haiku, sonnet],
        "est_cost_usd": 0.0,
        "input_tokens": 10_000_000,
        "by_model": [split, summary],
    }
    guard = SpendGuard(cap_usd=3.4, real=True)

    async def go() -> LoadRun:
        async with fake.client() as http:
            runner = LoadRunner(http, enrich=_enricher(fake, cost), guard=guard, poll_wait_s=1)
            return await runner.run(plan, arrivals)

    run = asyncio.run(go())
    # Haiku turns $1.00 at Haiku rates plus the summary's billed $0.50: $1.50 a Candidate.
    assert guard.spent_usd == pytest.approx(1.5 * round(guard.spent_usd / 1.5))
    assert 0 < guard.spent_usd <= 3.4
    assert any(c.ended == "spend_cap" for c in run.candidates)


def test_a_read_back_failure_is_recorded_on_the_candidate() -> None:
    fake = FakeWebChat()
    plan = _plan(n_per_s=4, seconds=0.5)
    arrivals = schedule(plan, PERSONAS, mix={"qualified_es": 1})

    async def missing(phone: str) -> dict[str, Any]:
        raise LookupError(f"no Application for {phone}")

    async def go() -> LoadRun:
        async with fake.client() as http:
            return await LoadRunner(http, enrich=missing, poll_wait_s=1).run(plan, arrivals)

    run = asyncio.run(go())
    assert all(c.record and "LookupError" in c.record["error"] for c in run.candidates)
    assert compute(run)["correctness"]["read_back"] == 0


def test_summarise_record_reads_correctness_runs_and_events() -> None:
    fake = FakeWebChat()
    plan = _plan(n_per_s=2, seconds=1)
    arrivals = schedule(plan, PERSONAS, mix={"qualified_es": 1})[:1]

    async def go() -> LoadRun:
        async with fake.client() as http:
            return await LoadRunner(http, poll_wait_s=1).run(plan, arrivals)

    run = asyncio.run(go())
    app = fake.application_detail(run.candidates[0].phone)
    app["cost"] = {**STUB_COST, "input_tokens": 10, "p50_latency_ms": 250}
    record = summarise_record(PERSONAS["qualified_es"], app)
    assert record["correct"] and record["stage"] == "qualified"
    assert record["runs"] == 3 and record["tokens"]["input_tokens"] == 10
    assert record["by_model"] == []
    assert record["p50_run_latency_ms"] == 250
    assert record["validation_failures"] == 0 and record["escalations"] == 0
    wrong = summarise_record(PERSONAS["no_license_mx"], app)
    assert not wrong["correct"] and wrong["failures"]


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (ChatError("x", status=503), "http_503"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("refused"), "transport"),
        (ValueError("odd"), "other"),
    ],
)
def test_errors_are_classified(exc: BaseException, kind: str) -> None:
    assert classify_error(exc) == kind


# ---- docker stats ------------------------------------------------------------------------------


STATS = (
    '{"Name":"p-backend-1","CPUPerc":"12.50%","MemUsage":"49.52MiB / 7.75GiB"}\n'
    '{"Name":"p-hermes-1","CPUPerc":"101.0%","MemUsage":"1.5GiB / 7.75GiB"}\n'
    "\n"
    '{"Name":"other","CPUPerc":"0%","MemUsage":"512kB / 1GB"}\n'
)


def test_docker_stats_lines_become_samples_per_service() -> None:
    samples = parse_stats(STATS, {"p-backend-1": "backend", "p-hermes-1": "hermes"}, 1.234)
    assert [(s.service, s.cpu_pct, s.mem_mib) for s in samples] == [
        ("backend", 12.5, 49.52),
        ("hermes", 101.0, 1536.0),
        ("other", 0.0, 0.49),
    ]
    assert samples[0].t_s == 1.23
    assert parse_mem_mib("garbage") == 0.0
    assert parse_mem_mib("2GB / 4GB") == pytest.approx(1907.35, abs=0.01)


def test_the_sampler_lists_the_projects_containers_then_samples_until_stopped() -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[1] == "ps":
            out = "p-backend-1\tbackend\np-hermes-1\thermes\n"
        else:
            out = STATS.split("\n", 2)[0] + "\n" + STATS.split("\n", 2)[1] + "\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    sampler = DockerStatsSampler("p", interval_s=0.01, run=fake_run)

    async def go() -> list[ResourceSample]:
        stop = asyncio.Event()
        task = asyncio.create_task(sampler(stop, 0.0))
        await asyncio.sleep(0.05)
        stop.set()
        return await task

    samples = asyncio.run(go())
    assert {s.service for s in samples} == {"backend", "hermes"}
    assert "label=com.docker.compose.project=p" in calls[0]
    assert calls[1][:3] == ["docker", "stats", "--no-stream"]
    assert sampler.sample({}, 0.0) == []


# ---- report ------------------------------------------------------------------------------------


def _synthetic(
    *,
    latencies: list[float],
    errors: dict[int, str] | None = None,
    resources: dict[str, float] | None = None,
    hermes_ms: int | None = 300,
) -> LoadRun:
    plan = LoadPlan(weekly_rate=10_000, sim_duration_s=600, speedup=10, peak_factor=3)
    cands = []
    for i, lat in enumerate(latencies):
        c = CandidateResult(i, "qualified_es", f"+3460000{i:04d}", float(i))
        c.started_s, c.finished_s = float(i), float(i) + 5
        c.ended = "completed"
        c.turns = [TurnSample(lat, lat + 0.1, False), TurnSample(lat, lat, False)]
        c.record = {
            "correct": True,
            "failures": [],
            "runs": 2,
            "models": ["stub-model"],
            "p50_run_latency_ms": hermes_ms,
            "est_cost_usd": 0.001,
        }
        if errors and i in errors:
            c.ended, c.error, c.error_kind, c.turns, c.record = (
                "error",
                "boom",
                errors[i],
                [],
                None,
            )
        cands.append(c)
    samples = [
        ResourceSample(t, service, cpu, 100.0)
        for service, cpu in (resources or {}).items()
        for t in range(3)
    ]
    return LoadRun(plan, "2026-10-09T00:00:00Z", 60.0, cands, samples)


def test_percentiles_and_concurrency() -> None:
    assert pct([], 50) is None
    assert pct([3.0], 95) == 3.0
    assert pct([1, 2, 3, 4], 50) == 2.5
    assert max_concurrency([(0, 10), (1, 2), (5, 6), (11, 12)]) == 2


def test_compute_reports_throughput_latency_errors_cost_and_resources() -> None:
    run = _synthetic(latencies=[0.5] * 9 + [6.0], resources={"backend": 20.0, "hermes": 60.0})
    m = compute(run)
    assert m["candidates"]["started"] == 10 and m["candidates"]["finished"] == 10
    assert m["candidates"]["max_concurrent"] == 5
    assert m["throughput"]["messages_per_s"] == pytest.approx(20 / 60, abs=1e-3)
    assert m["throughput"]["agent_runs_per_s"] == pytest.approx(20 / 60, abs=1e-3)
    assert m["throughput"]["offered_weekly_equivalent"] == 100_000
    assert m["latency_s"]["p50"] == 0.5 and m["latency_s"]["first_over_5s"] == 1
    assert m["latency_s"]["over_10s"] == 0
    assert m["cost"]["total_usd"] == pytest.approx(0.01)
    assert m["cost"]["per_week_at_plan_usd"] == pytest.approx(10.0)
    assert m["correctness"]["rate"] == 1.0
    assert m["resources"]["hermes"]["cpu_p90"] == 60.0


def test_bottleneck_a_saturated_cpu_wins() -> None:
    run = _synthetic(latencies=[0.5] * 5, resources={"backend": 20.0, "hermes": 110.0})
    b = find_bottleneck(compute(run))
    assert b["component"] == "hermes" and b["saturated"]
    assert "replicas" in b["fix"]


def test_bottleneck_headroom_names_the_busiest_service_and_an_estimate() -> None:
    run = _synthetic(latencies=[0.5] * 5, resources={"backend": 42.5, "hermes": 17.0})
    b = find_bottleneck(compute(run))
    assert b["component"] == "backend" and not b["saturated"]
    assert "2x this run" in b["evidence"]


def test_bottleneck_without_samples_or_breach_is_none() -> None:
    b = find_bottleneck(compute(_synthetic(latencies=[0.5] * 5)))
    assert b["component"] is None and not b["saturated"]


def test_refused_agent_runs_count_in_the_error_rate_and_name_hermes() -> None:
    run = _synthetic(latencies=[0.5] * 6, errors={0: "timeout"}, resources={"web": 1.0})
    for i in (1, 2):
        run.candidates[i].record["refused_runs"] = 2
    run.candidates[0].record = {"refused_runs": 1}  # failed otherwise: counted once, as an error
    m = compute(run)
    assert m["errors"]["by_kind"]["hermes_refused"] == 2
    assert m["errors"]["refused_runs"] == 5 and m["errors"]["by_kind"]["timeout"] == 1
    assert m["candidates"]["failed"] == 3 and m["errors"]["rate"] == 0.5
    b = find_bottleneck(m)
    assert b["component"] == "hermes" and b["saturated"]
    assert "concurrent-run cap" in b["evidence"] and "2 Candidates" in b["evidence"]
    assert "hermes_refused" in markdown([("step", m, b)])


@pytest.mark.parametrize(
    ("errors", "hermes_ms", "real", "component"),
    [
        ({0: "http_429", 1: "http_429"}, 300, False, "backend-rate-limit"),
        ({0: "http_503", 1: "http_503"}, 300, False, "backend"),
        ({0: "timeout", 1: "timeout"}, 9000, False, "hermes"),
        ({0: "timeout", 1: "timeout"}, 9000, True, "model"),
        ({0: "timeout", 1: "timeout"}, 10, False, "backend"),
    ],
)
def test_bottleneck_on_a_broken_limit_follows_the_errors(
    errors: dict[int, str], hermes_ms: int, real: bool, component: str
) -> None:
    run = _synthetic(
        latencies=[0.5] * 6, errors=errors, hermes_ms=hermes_ms, resources={"web": 1.0}
    )
    b = find_bottleneck(compute(run), real=real)
    assert b["component"] == component and b["saturated"], b


def test_markdown_has_the_ramp_runs_and_the_next_bottleneck() -> None:
    calm = _synthetic(latencies=[0.5] * 5, resources={"hermes": 40.0})
    busy = _synthetic(latencies=[0.5] * 5, resources={"hermes": CPU_SATURATED_PCT + 10})
    steps = []
    for label, run in (("Step 1", calm), ("Step 2", busy)):
        m = compute(run)
        steps.append((label, m, find_bottleneck(m)))
    text = markdown(steps, title="Load results", preamble="Measured on the stub.")
    assert text.startswith("# Load results\n\nMeasured on the stub.")
    assert "## Ramp" in text and "| Step 2 |" in text
    assert "## Next bottleneck and its fix\n\nFirst seen in **Step 2**: **hermes**" in text
    assert "| hermes | 40.0 |" in text
    single = markdown(steps[:1])
    assert "## Ramp" not in single and "First seen in **Step 1**" in single


def test_markdown_lists_errors_and_unexpected_applications() -> None:
    run = _synthetic(latencies=[0.5] * 3, errors={0: "http_500"})
    run.candidates[1].record = {
        **(run.candidates[1].record or {}),
        "correct": False,
        "failures": ["stage: x"],
    }
    m = compute(run)
    text = markdown([("Only", m, find_bottleneck(m))])
    assert "Errors (first five):" in text and "- `boom`" in text
    assert "- qualified_es: stage: x" in text


# ---- command line ------------------------------------------------------------------------------


def test_parse_mix() -> None:
    assert cli.parse_mix(None) is None
    assert cli.parse_mix("qualified_es=3, no_license_mx") == {
        "qualified_es": 3.0,
        "no_license_mx": 1.0,
    }


def test_the_cli_refuses_a_real_run_without_a_cap(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CRIBA_REAL_SPEND_CAP_USD", raising=False)
    assert cli.main(["--base-url", "http://127.0.0.1:9", "--real"]) == 2
    err = capsys.readouterr().err
    assert "needs a spend cap" in err
    assert "real-model spend: $0.0000 (nothing was called)" in err


def test_the_cli_refuses_a_stack_that_is_not_sim(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--base-url", "http://127.0.0.1:9"]) == 2
    err = capsys.readouterr().err
    assert "not a sim stack" in err
    assert "real-model spend: $0.0000 (stub model, no real model was called)" in err


def test_the_cli_runs_a_ramp_against_the_fake_and_writes_both_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    fake = FakeWebChat()
    real_client = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("limits", None)
        kwargs["transport"] = httpx.ASGITransport(app=fake.app)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(cli.httpx, "AsyncClient", client)
    out, raw = tmp_path / "load.md", tmp_path / "load.json"
    code = cli.main(
        [
            "--base-url",
            "http://fake",
            "--weekly",
            str(4 * 604_800),
            "--peak-factor",
            "1",
            "--sim-minutes",
            "0.01",
            "--speedup",
            "1,2",
            "--mix",
            "qualified_es=1",
            "--no-read-back",
            "--out",
            str(out),
            "--json",
            str(raw),
        ]
    )
    assert code == 0
    text = out.read_text("utf-8")
    assert "## Ramp" in text and "Step 2: speed-up 2" in text
    data = json.loads(raw.read_text("utf-8"))
    assert [d["label"] for d in data] == ["Step 1: speed-up 1", "Step 2: speed-up 2"]
