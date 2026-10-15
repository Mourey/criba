"""`python -m live.smoke`: the refusals, the spend cap, the report and the exit codes.

The stack is the in-process fake and the flows are small stand-ins, so nothing here calls a
model; the real flows are covered in `test_flows.py` and run for real by `test_live_smoke.py`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from live import smoke
from live.client import LiveStack
from live.fake_api import FakeApi, cost_block, make_application
from live.flows import FlowResult, FlowSpec
from sim.spend import PRICES_ENV, SpendGuard

PHONE = "+34600000042"


async def hello_flow(stack: LiveStack, res: FlowResult) -> None:
    res.phones.append(PHONE)
    res.conversations += 1
    conversation, cursor = await stack.start("ES", "es-ES", PHONE)
    turn = await stack.say(conversation, "hola", cursor)
    res.timed("reply to hola", turn.first_s)
    res.check("the agent answered", bool(turn.messages))


async def failing_flow(stack: LiveStack, res: FlowResult) -> None:
    res.check("a check that fails", False, "on purpose")


HELLO = FlowSpec("hello", "Hello", hello_flow)
FAILING = FlowSpec("failing", "Failing", failing_flow)


def api_with_app(*, sim: bool = False) -> FakeApi:
    api = FakeApi(sim=sim, password="s3cret")
    api.applications["app_1"] = make_application("app_1", PHONE)
    api.on_message = lambda a, cid, text: a.say(cid, "Hola, soy el asistente virtual (IA)")
    return api


@pytest.fixture
def password_file(tmp_path: Path) -> Path:
    path = tmp_path / "seed_password.txt"
    path.write_text("s3cret\n", encoding="utf-8")
    return path


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A cap of $3 and an empty ledger; returns the ledger path."""
    ledger = tmp_path / "ledger.json"
    monkeypatch.setenv("CRIBA_REAL_SPEND_CAP_USD", "3")
    monkeypatch.setenv("CRIBA_REAL_SPEND_LEDGER", str(ledger))
    monkeypatch.delenv(PRICES_ENV, raising=False)
    monkeypatch.delenv("CRIBA_E2E_SEED_PASSWORD_FILE", raising=False)
    monkeypatch.delenv("CRIBA_E2E_SEED_PASSWORD", raising=False)
    return ledger


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Callable[[FakeApi, list[FlowSpec]], None]:
    """Point `live.smoke` at an in-process API and a list of flows."""

    def install(api: FakeApi, flows: list[FlowSpec]) -> None:
        def client(**kwargs: Any) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api.app), base_url=kwargs["base_url"]
            )

        monkeypatch.setattr(
            smoke, "httpx", SimpleNamespace(AsyncClient=client, HTTPError=httpx.HTTPError)
        )
        monkeypatch.setattr(smoke, "select", lambda ids: flows)

    return install


def run_cli(*extra: str, password_file: Path | None = None) -> int:
    args = ["--base-url", "http://fake", *extra]
    if password_file:
        args += ["--seed-password-file", str(password_file)]
    return smoke.main(args)


def test_it_refuses_without_a_spend_cap(
    monkeypatch: pytest.MonkeyPatch,
    serve: Callable[..., None],
    password_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("CRIBA_REAL_SPEND_CAP_USD", raising=False)
    serve(api_with_app(), [HELLO])
    assert run_cli(password_file=password_file) == 2
    err = capsys.readouterr().err
    assert "needs a spend cap" in err
    assert "real-model spend: $0.0000 (nothing was called)" in err


def test_it_refuses_a_ledger_already_over_the_cap(
    env: Path, serve: Callable[..., None], password_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env.write_text(json.dumps({"runs": [{"spent_usd": 3.5}]}), encoding="utf-8")
    serve(api_with_app(), [HELLO])
    assert run_cli(password_file=password_file) == 2
    assert "at or over the $3.00 cap" in capsys.readouterr().err


def test_it_refuses_an_unknown_flow(
    env: Path, password_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli("--flows", "nope", password_file=password_file) == 2
    assert "unknown flow nope" in capsys.readouterr().err


def test_it_refuses_a_sim_stack(
    env: Path, serve: Callable[..., None], password_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(api_with_app(sim=True), [HELLO])
    assert run_cli(password_file=password_file) == 2
    assert "this is a sim stack" in capsys.readouterr().err


def test_it_refuses_a_login_that_fails(
    env: Path, serve: Callable[..., None], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wrong = tmp_path / "wrong.txt"
    wrong.write_text("nope", encoding="utf-8")
    serve(api_with_app(), [HELLO])
    assert run_cli(password_file=wrong) == 2
    assert "cannot log in as the Operator" in capsys.readouterr().err


def test_a_passing_run_writes_the_report_the_json_and_the_ledger(
    env: Path,
    serve: Callable[..., None],
    password_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(api_with_app(), [HELLO])
    out, dump = tmp_path / "report" / "live-smoke.md", tmp_path / "live.json"
    code = run_cli(
        "--out", str(out), "--json", str(dump), "--note", "from main", password_file=password_file
    )
    assert code == 0
    report = out.read_text(encoding="utf-8")
    assert report.startswith("# Live smoke run") and "| Note | from main |" in report
    assert "**1 of 1 flows passed.**" in report
    data = json.loads(dump.read_text(encoding="utf-8"))
    assert data["results"][0]["id"] == "hello" and data["meta"]["note"] == "from main"
    captured = capsys.readouterr()
    assert captured.out.startswith("PASS    hello")
    assert "1 of 1 flows passed" in captured.out
    assert captured.err.strip().splitlines()[-1].startswith("real-model spend: this run $")
    spent = json.loads(env.read_text(encoding="utf-8"))["runs"]
    assert [r["label"] for r in spent] == ["live-smoke"] and spent[0]["spent_usd"] > 0


def test_without_out_the_report_is_printed(
    env: Path, serve: Callable[..., None], password_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(api_with_app(), [HELLO])
    assert run_cli(password_file=password_file) == 0
    assert capsys.readouterr().out.startswith("# Live smoke run")


def test_a_failing_flow_exits_one_and_is_reported_as_it_failed(
    env: Path,
    serve: Callable[..., None],
    password_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    serve(api_with_app(), [FAILING])
    out = tmp_path / "r.md"
    assert run_cli("--out", str(out), password_file=password_file) == 1
    assert "FAIL a check that fails: on purpose" in capsys.readouterr().err
    assert "- FAIL: a check that fails (on purpose)" in out.read_text(encoding="utf-8")


def test_the_run_stops_before_a_flow_the_cap_cannot_take(
    env: Path,
    serve: Callable[..., None],
    password_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env.write_text(json.dumps({"runs": [{"spent_usd": 2.99}]}), encoding="utf-8")
    serve(api_with_app(), [HELLO, FAILING])
    out = tmp_path / "r.md"
    assert run_cli("--out", str(out), password_file=password_file) == 4
    assert "stopped early: spend cap" in capsys.readouterr().err
    report = out.read_text(encoding="utf-8")
    assert "The run stopped early: spend cap" in report
    assert "| Hello | NOT RUN |" in report and "**0 of 2 flows passed.**" in report


def test_a_spend_refusal_mid_run_exits_three(
    env: Path,
    serve: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
    password_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = api_with_app()
    api.applications["app_1"]["cost"]["by_model"][0]["model"] = "vendor/unpriced-model"
    serve(api, [HELLO])
    assert run_cli(password_file=password_file) == 3
    assert "no guard price for vendor/unpriced-model" in capsys.readouterr().err


def test_run_all_stops_once_the_dearest_flow_so_far_no_longer_fits() -> None:
    api = api_with_app()
    api.applications["app_1"]["cost"] = cost_block(haiku_in=3_000_000)  # about $0.30
    guard = SpendGuard(cap_usd=3, real=True)
    guard.cap_usd = smoke.first_projection(guard, "anthropic/claude-haiku-5.5") + 0.001
    seen: list[str] = []

    async def go() -> tuple[list[FlowResult], str | None]:
        transport = httpx.ASGITransport(app=api.app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://fake") as chat,
            httpx.AsyncClient(transport=transport, base_url="http://fake") as operator,
        ):
            stack = LiveStack(chat, operator, guard, poll_wait_s=0.0, reply_timeout_s=2.0)
            return await smoke.run_all(stack, [HELLO, HELLO, HELLO], seen.append)

    results, stopped = asyncio.run(go())
    assert [r.status for r in results] == ["pass", "not run", "not run"]
    assert stopped and stopped.startswith("spend cap:")
    assert results[1].not_run == stopped
    assert len(seen) == 1 and seen[0].startswith("hello: pass")


def test_first_projection_is_a_long_conversation_at_the_models_list_price() -> None:
    guard = SpendGuard(cap_usd=3, real=True)
    haiku = smoke.first_projection(guard, "anthropic/claude-haiku-5.5")
    sonnet = smoke.first_projection(guard, "anthropic/claude-sonnet-5.5")
    assert 0.05 < haiku < 0.2 < sonnet


def test_git_state_reads_the_repo_and_survives_a_missing_one(tmp_path: Path) -> None:
    commit, dirty = smoke.git_state()
    assert len(commit) >= 7 and isinstance(dirty, bool)
    assert smoke.git_state(tmp_path / "missing") == ("unknown", False)


def test_password_comes_from_the_option_then_the_environment_then_the_repo_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    option = tmp_path / "a.txt"
    option.write_text("from-option\n", encoding="utf-8")
    other = tmp_path / "b.txt"
    other.write_text("from-env-file\n", encoding="utf-8")
    parser = smoke.build_parser()
    monkeypatch.delenv("CRIBA_E2E_SEED_PASSWORD", raising=False)
    monkeypatch.setenv("CRIBA_E2E_SEED_PASSWORD_FILE", str(other))
    with_option = parser.parse_args(["--base-url", "x", "--seed-password-file", str(option)])
    assert smoke.password(with_option) == "from-option"
    assert smoke.password(parser.parse_args(["--base-url", "x"])) == "from-env-file"
    monkeypatch.delenv("CRIBA_E2E_SEED_PASSWORD_FILE")
    monkeypatch.setattr(smoke, "DEFAULT_PASSWORD_FILE", tmp_path / "missing.txt")
    assert smoke.password(parser.parse_args(["--base-url", "x"])) == ""
    default = tmp_path / "default.txt"
    default.write_text("from-repo\n", encoding="utf-8")
    monkeypatch.setattr(smoke, "DEFAULT_PASSWORD_FILE", default)
    assert smoke.password(parser.parse_args(["--base-url", "x"])) == "from-repo"
