"""Model comparison: the runner against the in-process fake, the dataset, the import and the CLI.

The import is checked end to end against the backend's own schema and the dashboard's own
reader: the migrations build a SQLite file, `import_file` writes the replay rows, and
`criba.api.metrics.UsageComparison` reads them back as `GET /api/metrics/comparison` would.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from criba.api.metrics import UsageComparison
from criba.db.connection import connect
from criba.db.migrator import migrate
from sim.compare import __main__ as cli
from sim.compare.dataset import (
    FORMAT,
    RunRecord,
    markdown,
    read_jsonl,
    summarise,
    write_jsonl,
)
from sim.compare.import_dataset import (
    apply_rows,
    container_program,
    import_compose,
    import_file,
    usage_rows,
)
from sim.compare.runner import STUB_LABEL, ComparisonRunner
from sim.fake_webchat import FakeWebChat
from sim.personas.loader import load_personas
from sim.spend import SpendGuard, SpendRefused, read_ledger

PERSONAS = load_personas()
STUB = ["stub-model", "deterministic-summary"]
HAIKU = "anthropic/claude-haiku-5.5"
SONNET = "anthropic/claude-sonnet-5.5"
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def _enricher(fake: FakeWebChat, cost: dict[str, Any]) -> Any:
    async def enrich(phone: str) -> dict[str, Any]:
        app = fake.application_detail(phone)
        app["cost"] = dict(cost)
        return app

    return enrich


def _compare(
    persona_ids: list[str],
    *,
    cost: dict[str, Any] | None = None,
    reps: int = 1,
    **kwargs: Any,
) -> tuple[list[RunRecord], str | None, FakeWebChat]:
    fake = FakeWebChat()
    cost = cost or {"runs": 4, "models": STUB, "est_cost_usd": 0.0, "input_tokens": 100}

    async def go() -> tuple[list[RunRecord], str | None]:
        async with fake.client() as http:
            runner = ComparisonRunner(
                http, _enricher(fake, cost), dataset="d1", poll_wait_s=1, **kwargs
            )
            return await runner.run([PERSONAS[p] for p in persona_ids], reps=reps)

    records, stopped = asyncio.run(go())
    return records, stopped, fake


# ---- runner ------------------------------------------------------------------------------------


def test_a_stub_comparison_writes_one_line_per_persona_run() -> None:
    ids = ["qualified_es", "no_license_mx", "stalled_abandoned_es"]
    records, stopped, _ = _compare(ids, reps=2, label="stub-as-haiku")
    assert stopped is None
    assert [(r.persona_id, r.rep) for r in records] == [(p, rep) for rep in (1, 2) for p in ids]
    for r in records:
        assert r.dataset == "d1" and r.model == "stub-as-haiku"
        assert r.correct, (r.persona_id, r.failures)
        assert r.turns and len(r.latency_s) == r.turns
        assert r.served_models == STUB and r.agent_runs == 4
        assert r.tokens["input_tokens"] == 100 and r.cost_usd == 0 and r.guard_cost_usd == 0
        assert r.expected["stage"] == PERSONAS[r.persona_id].expect.stage
    knockout = next(r for r in records if r.persona_id == "no_license_mx")
    assert knockout.outcome == "knockout" and not knockout.qualified
    assert next(r for r in records if r.persona_id == "qualified_es").qualified


def test_the_label_defaults_to_the_stub() -> None:
    records, _, _ = _compare(["qualified_es"])
    assert records[0].model == STUB_LABEL


def test_counts_validation_failures_from_the_record() -> None:
    records, _, _ = _compare(["invalid_then_corrected_es"])
    assert records[0].validation_failures >= 1


def test_without_the_sim_clock_the_personas_that_move_it_are_left_out() -> None:
    records, _, _ = _compare(["qualified_es", "stalled_abandoned_es"], clock=False)
    assert [r.persona_id for r in records] == ["qualified_es"]


def test_stub_mode_refuses_a_stack_that_serves_a_real_model() -> None:
    cost = {"runs": 4, "models": [HAIKU], "est_cost_usd": 0.0}
    with pytest.raises(SpendRefused, match="not the stub"):
        _compare(["qualified_es", "no_license_mx"], cost=cost)


def test_real_mode_needs_the_expected_model() -> None:
    with pytest.raises(SpendRefused, match="--model"):
        ComparisonRunner(
            httpx.AsyncClient(),
            _enricher(FakeWebChat(), {}),
            dataset="d",
            guard=SpendGuard(cap_usd=1, real=True),
        )


def test_real_mode_stops_before_the_cap_and_counts_guard_cost() -> None:
    # 2M input tokens of Haiku at the guard's $0.10/M: $0.20 a persona. First projection is
    # FIRST_PERSONA_TOKENS ($0.045); after that 1.5 x $0.20.
    cost = {
        "runs": 4,
        "models": [HAIKU],
        "est_cost_usd": 0.01,
        "input_tokens": 2_000_000,
    }
    guard = SpendGuard(cap_usd=0.85, real=True)
    records, stopped, _ = _compare(
        ["qualified_es", "no_license_mx"],
        cost=cost,
        reps=3,
        guard=guard,
        expected_model=HAIKU,
    )
    assert stopped and "spend cap" in stopped
    # $0.40 spent + $0.30 projected fits; $0.60 + $0.30 does not.
    assert guard.spent_usd == pytest.approx(0.6)
    assert len(records) == 3
    assert all(r.guard_cost_usd == pytest.approx(0.2) and r.cost_usd == 0.01 for r in records)
    assert all(r.model == HAIKU for r in records)


def _split_entry(model: str, **kw: Any) -> dict[str, Any]:
    base = {
        "model": model,
        "calls": 1,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "est_cost_usd": 0.0,
        "reconciled_cost_usd": None,
    }
    return {**base, **kw}


def test_real_mode_prices_haiku_turns_at_haiku_rates_beside_a_sonnet_summary() -> None:
    # One persona: 1M Haiku prompt tokens (98% cache reads) and a Sonnet summary. Priced as
    # before the fix, the whole run would count at Sonnet rates with the cache counted twice.
    haiku = _split_entry(
        HAIKU,
        input_tokens=1_000_000,
        cache_read_tokens=980_000,
        cache_write_tokens=10_000,
        output_tokens=10_000,
    )
    summary = _split_entry(SONNET, input_tokens=1_350, output_tokens=499)
    cost = {
        "runs": 4,
        "models": [HAIKU, SONNET],
        "est_cost_usd": 0.0,
        "input_tokens": 1_001_350,
        "output_tokens": 10_499,
        "cache_read_tokens": 980_000,
        "cache_write_tokens": 10_000,
        "by_model": [haiku, summary],
    }
    guard = SpendGuard(cap_usd=5.0, real=True)
    records, stopped, _ = _compare(["qualified_es"], cost=cost, guard=guard, expected_model=HAIKU)
    assert stopped is None
    haiku_usd = (20_000 * 0.10 + 980_000 * 0.01 + 10_000 * 0.20 + 10_000 * 0.50) / 1e6
    sonnet_usd = (1_350 * 2.0 + 499 * 10.0) / 1e6
    assert records[0].guard_cost_usd == pytest.approx(haiku_usd + sonnet_usd)
    assert guard.spent_usd == pytest.approx(haiku_usd + sonnet_usd)
    assert guard.spent_usd < 0.05


def test_real_mode_counts_openrouters_own_cost_for_the_summary_call() -> None:
    summary = _split_entry(
        SONNET,
        input_tokens=5_000_000,  # would price at $10 from tokens
        reconciled_cost_usd=0.0077,
    )
    cost = {"runs": 4, "models": [HAIKU, SONNET], "by_model": [summary]}
    guard = SpendGuard(cap_usd=5.0, real=True)
    records, _, _ = _compare(["qualified_es"], cost=cost, guard=guard, expected_model=HAIKU)
    assert records[0].guard_cost_usd == pytest.approx(0.0077)


def test_report_cost_is_per_model_with_the_bill_where_there_is_one() -> None:
    # The real run's defects: an inflated backend estimate for Haiku and $0 for Sonnet. The
    # record carries each model at its own rates, and OpenRouter's charge for the summary call
    # where the ledger has it.
    haiku = _split_entry(
        HAIKU,
        input_tokens=1_000_000,
        cache_read_tokens=980_000,
        output_tokens=10_000,
        est_cost_usd=0.2504,
    )
    summary = _split_entry(SONNET, input_tokens=1_350, reconciled_cost_usd=0.0077)
    cost = {
        "runs": 4,
        "models": [HAIKU, SONNET],
        "est_cost_usd": 0.2504,
        "by_model": [haiku, summary],
    }
    records, _, _ = _compare(
        ["qualified_es"],
        cost=cost,
        guard=SpendGuard(cap_usd=5.0, real=True),
        expected_model=HAIKU,
    )
    haiku_usd = (20_000 * 0.10 + 980_000 * 0.01 + 10_000 * 0.50) / 1e6
    assert records[0].cost_usd == pytest.approx(haiku_usd + 0.0077)
    assert records[0].cost_usd < 0.05


def test_report_cost_shows_a_sonnet_run_the_backend_did_not_price() -> None:
    sonnet = _split_entry(SONNET, input_tokens=100_000, output_tokens=1_000)
    cost = {"runs": 4, "models": [SONNET], "est_cost_usd": 0.0, "by_model": [sonnet]}
    records, _, _ = _compare(
        ["qualified_es"],
        cost=cost,
        guard=SpendGuard(cap_usd=5.0, real=True),
        expected_model=SONNET,
    )
    expected = (100_000 * 2.0 + 1_000 * 10.0) / 1e6
    assert records[0].cost_usd == pytest.approx(expected)
    row = summarise(records)[0]
    assert row["cost_per_screening_usd"] == pytest.approx(expected, abs=1e-6)
    assert row["cost_per_screening_usd"] > 0


def test_real_mode_refuses_a_first_persona_over_the_cap() -> None:
    guard = SpendGuard(cap_usd=0.04, real=True)
    records, stopped, fake = _compare(["qualified_es"], guard=guard, expected_model=HAIKU)
    assert records == [] and stopped and not fake.convs


def test_real_mode_stops_when_the_stack_serves_another_model() -> None:
    cost = {"runs": 4, "models": ["anthropic/claude-sonnet-5.5"], "input_tokens": 10}
    with pytest.raises(SpendRefused, match=r"expected anthropic/claude-haiku-5\.5"):
        _compare(
            ["qualified_es"],
            cost=cost,
            guard=SpendGuard(cap_usd=5, real=True),
            expected_model=HAIKU,
        )


def test_a_failed_persona_is_an_error_line_in_stub_mode_and_a_stop_in_real_mode() -> None:
    async def missing(phone: str) -> dict[str, Any]:
        raise LookupError(f"no Application for {phone}")

    async def go(guard: SpendGuard, model: str | None) -> tuple[list[RunRecord], str | None]:
        fake = FakeWebChat()
        async with fake.client() as http:
            runner = ComparisonRunner(
                http,
                missing,
                dataset="d",
                guard=guard,
                expected_model=model,
                poll_wait_s=1,
            )
            return await runner.run([PERSONAS["qualified_es"]])

    records, _ = asyncio.run(go(SpendGuard(), None))
    assert records[0].ended == "error" and "LookupError" in (records[0].error or "")
    with pytest.raises(SpendRefused, match="spend is unknown"):
        asyncio.run(go(SpendGuard(cap_usd=5, real=True), HAIKU))


# ---- dataset -----------------------------------------------------------------------------------


def _record(model: str, persona: str, **kw: Any) -> RunRecord:
    base: dict[str, Any] = {
        "dataset": "d1",
        "model": model,
        "persona_id": persona,
        "rep": 1,
        "started_at": "2026-10-09T12:00:00Z",
        "ended": "completed",
        "served_models": [model],
        "stage": "qualified",
        "status": "open",
        "correct": True,
        "turns": 3,
        "agent_runs": 6,
        "latency_s": [1.0, 2.0, 3.0],
        "tokens": {"input_tokens": 300, "output_tokens": 30},
        "cost_usd": 0.03,
    }
    base.update(kw)
    return RunRecord(**base)


def test_jsonl_round_trips_and_refuses_another_format(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    records = [
        _record("a", "p1"),
        _record("b", "p1", correct=False, failures=["stage: x"]),
    ]
    assert write_jsonl(path, records[:1]) == 1
    assert write_jsonl(path, records[1:], append=True) == 1
    assert read_jsonl(path) == records
    assert json.loads(path.read_text("utf-8").splitlines()[0])["format"] == FORMAT
    path.write_text('{"format": "other/9"}\n\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"d\.jsonl:1: unknown dataset format"):
        read_jsonl(path)


def test_summary_compares_models_on_the_adr_0025_figures() -> None:
    records = [
        _record("haiku", "p1"),
        _record(
            "haiku",
            "p2",
            stage="driver_license",
            outcome="knockout",
            validation_failures=2,
        ),
        _record("sonnet", "p1", latency_s=[4.0, 5.0, 6.0], cost_usd=0.3, escalations=1),
        _record("sonnet", "p2", correct=False, failures=["stage"]),
        _record("sonnet", "p3", ended="error", error="boom", latency_s=[], turns=0),
    ]
    rows = {r["model"]: r for r in summarise(records)}
    haiku, sonnet = rows["haiku"], rows["sonnet"]
    assert list(rows) == ["haiku", "sonnet"]
    assert haiku["screenings"] == 2 and haiku["latency_p50_s"] == 2.0
    assert haiku["cost_per_screening_usd"] == 0.03 and haiku["cost_per_qualified_usd"] == 0.06
    assert haiku["validation_failures_per_screening"] == 1.0
    assert haiku["outcome_accuracy"] == 1.0 and haiku["escalation_rate"] == 0
    assert sonnet["errors"] == 1 and sonnet["screenings"] == 2 and sonnet["personas"] == 3
    assert sonnet["escalation_rate"] == 0.5 and sonnet["outcome_accuracy"] == 0.5
    assert sonnet["incorrect"] == ["p2"] and sonnet["latency_p95_s"] == pytest.approx(5.75)
    text = markdown(records, title="T")
    assert text.startswith("# T\n\nDataset: d1. 5 persona runs.")
    assert "| haiku | haiku | 2 | 6 | 2 |" in text
    assert "| sonnet | sonnet | 2 | 6 |" in text and "| p2 | 1 |" in text


def test_summary_of_a_model_with_only_errors() -> None:
    row = summarise([_record("m", "p", error="x", latency_s=[])])[0]
    assert row["screenings"] == 0 and row["outcome_accuracy"] is None
    assert row["cost_per_screening_usd"] is None and row["latency_p50_s"] is None


# ---- import ------------------------------------------------------------------------------------


def test_usage_rows_spread_tokens_and_cost_over_turns_and_flag_wrong_outcomes() -> None:
    records = [
        _record("haiku", "p1", tokens={"input_tokens": 301}),
        _record("haiku", "p2", correct=False, failures=["stage: expected qualified"]),
        _record("stub-as-x", "p3", served_models=["stub-model"]),
    ]
    rows = usage_rows(records, now=NOW)
    p1 = [r for r in rows if r["session_id"] == "d1/haiku/p1/1"]
    assert [r["input_tokens"] for r in p1] == [101, 100, 100]
    assert sum(r["est_cost_usd"] for r in p1) == pytest.approx(0.03)
    assert [r["latency_ms"] for r in p1] == [1000, 2000, 3000]
    assert {r["kind"] for r in p1} == {"run"} and {r["status"] for r in p1} == {"ok"}
    assert all(r["ts"] == "2026-10-09T12:00:00Z" and r["source"] == "replay" for r in rows)
    wrong = [r for r in rows if r["session_id"] == "d1/haiku/p2/1" and r["status"] == "error"]
    assert len(wrong) == 1 and wrong[0]["kind"] == "auxiliary"
    assert wrong[0]["error"] == "outcome_incorrect: stage: expected qualified"
    assert {r["provider"] for r in rows if r["model"] == "stub-as-x"} == {"stub"}
    assert {r["provider"] for r in rows if r["model"] == "haiku"} == {"openrouter"}


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "criba.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


def test_import_is_what_the_dashboard_comparison_reads(tmp_path: Path) -> None:
    db = _db(tmp_path)
    records = [
        _record("haiku", "p1"),
        _record("haiku", "p2", correct=False, failures=["x"]),
        _record("sonnet", "p1", cost_usd=0.3),
        _record("sonnet", "p2", cost_usd=0.3),
    ]
    assert import_file(db, records, now=NOW) == 13
    conn = connect(db)
    payload = UsageComparison().comparison(conn, "d1")
    conn.close()
    assert payload is not None and payload["dataset"] == "d1" and payload["personas"] == 4
    models = {m["model"]: m for m in payload["models"]}
    assert models["haiku"]["runs"] == 6 and models["sonnet"]["runs"] == 6
    assert models["haiku"]["latency_ms"] == {
        "p50": 2000.0,
        "p95": 3000.0,
        "p99": 3000.0,
    }
    assert models["haiku"]["cost_per_screening_usd"] == pytest.approx(0.03)
    assert models["sonnet"]["cost_per_screening_usd"] == pytest.approx(0.3)
    assert (
        models["haiku"]["outcome_accuracy"] == 0.5 and models["sonnet"]["outcome_accuracy"] == 1.0
    )
    # Today's schema has no column for validation failures: the error rows stand in for them.
    assert models["haiku"]["validation_failures"] == 1


def test_a_reimport_replaces_the_dataset_and_leaves_others_alone(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    import_file(db, [_record("haiku", "p1"), _record("haiku", "p1", dataset="d2")], now=NOW)
    import_file(db, [_record("haiku", "p9", latency_s=[1.0], turns=1)], now=NOW)
    conn = sqlite3.connect(db)
    counts = dict(conn.execute("SELECT dataset, count(*) FROM usage GROUP BY dataset").fetchall())
    conn.close()
    assert counts == {"d1": 1, "d2": 3}


def test_a_failed_write_rolls_back(tmp_path: Path) -> None:
    db = _db(tmp_path)
    import_file(db, [_record("haiku", "p1")], now=NOW)
    bad = usage_rows([_record("haiku", "p2")], now=NOW)
    bad[-1]["environment"] = "nowhere"  # violates the CHECK constraint
    conn = sqlite3.connect(db, isolation_level=None)
    with pytest.raises(sqlite3.IntegrityError):
        apply_rows(conn, "d1", bad)
    assert conn.execute("SELECT count(*) FROM usage WHERE dataset = 'd1'").fetchone()[0] == 3
    conn.close()


def test_import_refuses_an_empty_dataset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        import_file(_db(tmp_path), [])


def test_the_container_program_writes_through_docker_compose_exec(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        # Stand in for the container: run the same program here against the test database.
        seen.append(cmd)
        result = subprocess.run(
            [sys.executable, "-c", cmd[-1]],
            input=kwargs["input"],
            capture_output=True,
            text=True,
            env={"CRIBA_DB_PATH": str(db)},
            check=False,
        )
        return result

    assert "def apply_rows" in container_program()
    written = import_compose("proj", [_record("haiku", "p1")], run=fake_run, now=NOW)
    assert written == 3
    assert seen[0][:7] == ["docker", "compose", "-p", "proj", "exec", "-T", "backend"]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM usage WHERE source = 'replay'").fetchone()[0] == 3
    conn.close()

    def failing(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, "", "no such service")

    with pytest.raises(RuntimeError, match="no such service"):
        import_compose("proj", [_record("haiku", "p1")], run=failing)


# ---- command line ------------------------------------------------------------------------------


def test_cli_report_and_import_into_a_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "d.jsonl"
    write_jsonl(data, [_record("haiku", "p1"), _record("sonnet", "p1")])
    out = tmp_path / "comparison.md"
    assert cli.main(["report", str(data), "--out", str(out), "--title", "Comparison"]) == 0
    assert out.read_text("utf-8").startswith("# Comparison")
    assert cli.main(["report", str(data)]) == 0
    assert "| haiku |" in capsys.readouterr().out
    db = _db(tmp_path)
    assert cli.main(["import", str(data), "--db", str(db)]) == 0
    assert "wrote 6 replay rows" in capsys.readouterr().err


def test_cli_run_refuses_real_without_a_cap_and_a_stack_that_is_not_sim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.delenv("CRIBA_REAL_SPEND_CAP_USD", raising=False)
    out = str(tmp_path / "x.jsonl")
    base = ["run", "--base-url", "http://127.0.0.1:9", "--dataset", "d", "--out", out]
    assert cli.main([*base, "--real"]) == 2
    err = capsys.readouterr().err
    assert "needs a spend cap" in err
    assert "real-model spend: $0.0000 (nothing was called)" in err
    assert cli.main(base) == 2
    assert "not a sim stack" in capsys.readouterr().err


def _patch_clients(
    monkeypatch: pytest.MonkeyPatch, fake: FakeWebChat, cost: dict[str, Any]
) -> None:
    """Point the CLI's HTTP clients at the fake, and the read-back at the fake's record."""
    real_client = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.ASGITransport(app=fake.app)
        return real_client(*args, **kwargs)

    async def no_login(*_: Any, **__: Any) -> None:
        return None

    async def settled(_http: Any, phone: str, **__: Any) -> dict[str, Any]:
        return {**fake.application_detail(phone), "cost": cost}

    monkeypatch.setattr(cli.httpx, "AsyncClient", client)
    monkeypatch.setattr(cli, "login", no_login)
    monkeypatch.setattr(cli, "settled_application", settled)


def test_cli_run_against_the_fake_writes_and_appends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeWebChat()
    _patch_clients(monkeypatch, fake, {"runs": 2, "models": STUB})
    out = tmp_path / "d.jsonl"
    args = [
        "run",
        "--base-url",
        "http://fake",
        "--dataset",
        "d",
        "--personas",
        "qualified_es",
        "--out",
        str(out),
    ]
    assert cli.main([*args, "--label", "a"]) == 0
    assert cli.main([*args, "--label", "b", "--append"]) == 0
    assert [r.model for r in read_jsonl(out)] == ["a", "b"]
    err = capsys.readouterr().err
    assert "wrote 1 lines" in err
    assert err.rstrip().endswith("real-model spend: $0.0000 (stub model, no real model was called)")


def test_cli_spend_prints_the_ledger_total_against_the_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"runs": [{"spent_usd": 0.5}, {"spent_usd": 1.25}]}), "utf-8")
    monkeypatch.setenv("CRIBA_REAL_SPEND_LEDGER", str(ledger))
    monkeypatch.setenv("CRIBA_REAL_SPEND_CAP_USD", "5")
    assert cli.main(["spend"]) == 0
    assert "$1.7500 of the $5.00 cap" in capsys.readouterr().err
    monkeypatch.delenv("CRIBA_REAL_SPEND_LEDGER")
    monkeypatch.delenv("CRIBA_REAL_SPEND_CAP_USD")
    assert cli.main(["spend"]) == 0
    err = capsys.readouterr().err
    assert "no CRIBA_REAL_SPEND_LEDGER set" in err and "$0.0000 of the $0.00 cap" in err
    monkeypatch.setenv("CRIBA_REAL_SPEND_CAP_USD", "lots")
    assert cli.main(["spend"]) == 2


def test_cli_spend_reconcile_replaces_the_over_count_with_the_bill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"runs": [{"spent_usd": 0.665}, {"spent_usd": 4.2506}]}), "utf-8")
    monkeypatch.setenv("CRIBA_REAL_SPEND_LEDGER", str(ledger))
    monkeypatch.setenv("CRIBA_REAL_SPEND_CAP_USD", "5")
    reason = "guard counted cached tokens twice; bill from /api/v1/key"
    with caplog.at_level("WARNING", logger="sim.spend"):
        assert cli.main(["spend", "--reconcile", "0.1473", "--reason", reason]) == 0
    err = capsys.readouterr().err
    assert f"ledger reconciled: was $4.9156, now $0.1473 (adjustment -4.7683): {reason}" in err
    assert err.rstrip().endswith("$0.1473 of the $5.00 cap")
    assert "reconciled: was $4.9156, now $0.1473" in caplog.text
    assert read_ledger(ledger) == pytest.approx(0.1473)
    entry = json.loads(ledger.read_text("utf-8"))["runs"][-1]
    assert entry["kind"] == "reconcile" and entry["reason"] == reason


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--reconcile", "0.5"], "needs a reason"),
        (["--reconcile", "-0.5", "--reason", "x"], "0 or more"),
        (["--reconcile", "nan", "--reason", "x"], "0 or more"),
        (["--reason", "x"], "goes with --reconcile"),
    ],
)
def test_cli_spend_reconcile_refuses_a_bad_request_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    message: str,
) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"runs": [{"spent_usd": 1.0}]}), "utf-8")
    before = ledger.read_text("utf-8")
    monkeypatch.setenv("CRIBA_REAL_SPEND_LEDGER", str(ledger))
    monkeypatch.setenv("CRIBA_REAL_SPEND_CAP_USD", "5")
    assert cli.main(["spend", *args]) == 2
    assert message in capsys.readouterr().err
    assert ledger.read_text("utf-8") == before


def test_cli_spend_reconcile_needs_the_ledger(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CRIBA_REAL_SPEND_LEDGER", raising=False)
    assert cli.main(["spend", "--reconcile", "0.1", "--reason", "x"]) == 2
    assert "needs CRIBA_REAL_SPEND_LEDGER" in capsys.readouterr().err


def _summary_args() -> list[str]:
    return ["summary", "--base-url", "http://fake", "--persona", "qualified_es"]


def _patch_real_stack(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeWebChat,
    *,
    models: list[str],
    prose: str | None,
) -> None:
    """A real stack (no sim clock) whose Application carries `prose` and served `models`."""
    _patch_clients(
        monkeypatch,
        fake,
        {
            "runs": 2,
            "models": models,
            "input_tokens": 1000,
            "output_tokens": 500,
        },
    )

    async def not_sim(_http: Any) -> bool:
        return False

    async def settled(_http: Any, phone: str, **__: Any) -> dict[str, Any]:
        app = fake.application_detail(phone)
        app["cost"] = {
            "runs": 2,
            "models": models,
            "input_tokens": 1000,
            "output_tokens": 500,
        }
        app["summary"] = {"structured": {}, "prose": prose}
        return app

    monkeypatch.setattr(cli, "is_sim_stack", not_sim)
    monkeypatch.setattr(cli, "settled_application", settled)


def test_cli_summary_needs_the_cap_and_a_real_stack(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CRIBA_REAL_SPEND_CAP_USD", raising=False)
    assert cli.main(_summary_args()) == 2
    err = capsys.readouterr().err
    assert "needs a spend cap" in err
    assert "real-model spend: $0.0000 (nothing was called)" in err
    monkeypatch.setenv("CRIBA_REAL_SPEND_CAP_USD", "1")
    monkeypatch.delenv("CRIBA_REAL_SPEND_LEDGER", raising=False)
    _patch_clients(monkeypatch, FakeWebChat(), {"runs": 1, "models": STUB})
    assert cli.main(_summary_args()) == 2
    assert "a sim stack serves the stub" in capsys.readouterr().err


def test_cli_summary_prints_the_prose_and_the_spend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CRIBA_REAL_SPEND_CAP_USD", "5")
    monkeypatch.setenv("CRIBA_REAL_SPEND_LEDGER", str(tmp_path / "ledger.json"))
    _patch_real_stack(
        monkeypatch,
        FakeWebChat(),
        models=[HAIKU, SONNET],
        prose="Ana lives in Madrid and can start on Monday.",
    )
    assert cli.main(_summary_args()) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "Ana lives in Madrid and can start on Monday."
    assert f"summary prose by {SONNET}" in captured.err
    assert "total $" in captured.err and "of the $5.00 cap" in captured.err
    assert read_ledger(tmp_path / "ledger.json") > 0


@pytest.mark.parametrize(
    ("models", "prose", "message"),
    [
        ([HAIKU, SONNET], None, "prose null"),
        ([HAIKU, SONNET], "", "prose null"),
        ([HAIKU], "Some text.", "no summary prose"),
    ],
)
def test_cli_summary_fails_without_prose_or_without_sonnet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    models: list[str],
    prose: str | None,
    message: str,
) -> None:
    monkeypatch.setenv("CRIBA_REAL_SPEND_CAP_USD", "5")
    monkeypatch.setenv("CRIBA_REAL_SPEND_LEDGER", str(tmp_path / "ledger.json"))
    _patch_real_stack(monkeypatch, FakeWebChat(), models=models, prose=prose)
    assert cli.main(_summary_args()) == 6
    assert message in capsys.readouterr().err


def test_cli_summary_refuses_a_persona_that_moves_the_clock(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CRIBA_REAL_SPEND_CAP_USD", "5")
    monkeypatch.delenv("CRIBA_REAL_SPEND_LEDGER", raising=False)
    _patch_real_stack(monkeypatch, FakeWebChat(), models=[HAIKU, SONNET], prose="Some text.")
    args = [*_summary_args()[:-1], "stalled_abandoned_es"]
    assert cli.main(args) == 2
    assert "moves the sim clock" in capsys.readouterr().err


def test_cli_run_reports_a_refusal_and_an_early_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeWebChat()
    _patch_clients(monkeypatch, fake, {"runs": 2, "models": [HAIKU], "input_tokens": 1_000_000})
    out = tmp_path / "d.jsonl"
    args = [
        "run",
        "--base-url",
        "http://fake",
        "--dataset",
        "d",
        "--personas",
        "qualified_es,no_license_mx",
        "--out",
        str(out),
    ]
    assert cli.main(args) == 3
    assert "not the stub" in capsys.readouterr().err
    monkeypatch.setenv("CRIBA_REAL_SPEND_CAP_USD", "0.3")
    monkeypatch.setenv("CRIBA_REAL_SPEND_LEDGER", str(tmp_path / "ledger.json"))
    # $0.10 a persona: two fit under $0.30, the third projects to $0.35.
    assert cli.main([*args, "--real", "--model", HAIKU, "--reps", "2"]) == 4
    err = capsys.readouterr().err
    assert "stopped early: spend cap" in err
    assert err.rstrip().endswith("(guard prices; the OpenRouter bill is the figure that counts)")
    assert "this run $0.2000, earlier runs $0.0000, total $0.2000 of the $0.30 cap" in err
    ledger = json.loads((tmp_path / "ledger.json").read_text("utf-8"))
    assert ledger["runs"][0]["spent_usd"] == pytest.approx(0.2)
    assert len(read_jsonl(out)) == 2


def test_cli_import_checks_the_dashboard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "d.jsonl"
    write_jsonl(data, [_record("haiku", "p1")])
    db = _db(tmp_path)
    status = {"comparison": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={})
        if status["comparison"] != 200:
            return httpx.Response(status["comparison"], json={"error": "not_found"})
        conn = connect(db)
        body = UsageComparison().comparison(conn, request.url.params.get("dataset"))
        conn.close()
        return httpx.Response(200, json=body) if body else httpx.Response(404, json={})

    real_client = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(cli.httpx, "AsyncClient", client)
    monkeypatch.setenv("CRIBA_E2E_SEED_PASSWORD", "pw")
    monkeypatch.delenv("CRIBA_E2E_SEED_PASSWORD_FILE", raising=False)
    assert cli.main(["import", str(data), "--db", str(db), "--base-url", "http://x"]) == 0
    assert "dashboard replay d1: 1 screenings; haiku (3 runs)" in capsys.readouterr().err
    status["comparison"] = 404  # the dashboard does not see the dataset
    assert cli.main(["import", str(data), "--db", str(db), "--base-url", "http://x"]) == 5
    assert "comparison for d1: 404" in capsys.readouterr().err
