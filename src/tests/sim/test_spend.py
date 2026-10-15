"""The spend guard (`sim.spend`) and the stack helpers (`sim.stack`): no stack, no model."""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any

import httpx
import pytest

from criba.channel.run_client import FREE_MODEL_PREFIXES as BACKEND_FREE_PREFIXES
from criba.channel.run_client import PRICES_PER_MTOK as BACKEND_PRICES
from criba.channel.run_client import estimate_cost
from sim.spend import (
    CAP_ENV,
    FREE_MODEL_PREFIXES,
    GUARD_PRICES_PER_MTOK,
    LEDGER_ENV,
    PRICES_CHECKED,
    PRICES_ENV,
    SpendGuard,
    SpendRefused,
    by_model_cost,
    call_cost,
    is_refused_run,
    is_stub_model,
    prices_from_env,
    read_ledger,
    reconcile_ledger,
    reported_cost,
    token_cost,
)
from sim.stack import is_sim_stack, login, new_phone, seed_password, usage_of

HAIKU = "anthropic/claude-haiku-5.5"
SONNET = "anthropic/claude-sonnet-5.5"


def test_free_models_are_the_stub_and_the_deterministic_summary() -> None:
    assert is_stub_model("stub-model") and is_stub_model("deterministic-summary")
    assert not is_stub_model(HAIKU) and not is_stub_model(None) and not is_stub_model("")


def test_token_cost_prices_the_dearest_real_model_and_refuses_unknown_ones() -> None:
    prices = prices_from_env({})
    tokens = {
        "input_tokens": 1_000_000,
        "output_tokens": 100_000,
        "cache_read_tokens": 1_000_000,
    }
    # Input includes the cache reads, so all of it is cached: reads + output only.
    assert token_cost(["stub-model"], tokens, prices) == 0.0
    assert token_cost([HAIKU], tokens, prices) == pytest.approx(0.01 + 0.05)
    assert token_cost([HAIKU, SONNET, "stub-model"], tokens, prices) == pytest.approx(0.10 + 1.0)
    with pytest.raises(SpendRefused, match="no guard price for openai/gpt-x"):
        token_cost(["openai/gpt-x"], tokens, prices)


def test_prices_can_be_added_from_the_environment() -> None:
    row = {"input": 9, "output": 9, "cache_read": 1, "cache_write": 1}
    prices = prices_from_env({PRICES_ENV: json.dumps({"openai/gpt-x": row})})
    assert prices["openai/gpt-x"]["input"] == 9.0 and HAIKU in prices
    with pytest.raises(ValueError, match="JSON object"):
        prices_from_env({PRICES_ENV: "[1]"})


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ("", "needs a spend cap"),
        ("0", "needs a spend cap"),
        ("x", "not a number"),
        ("-1", "negative"),
    ],
)
def test_a_real_guard_needs_a_positive_cap(raw: str, match: str) -> None:
    with pytest.raises(SpendRefused, match=match):
        SpendGuard.from_env(real=True, env={CAP_ENV: raw})


def test_the_stub_guard_ignores_the_cap_and_refuses_any_spend() -> None:
    guard = SpendGuard.from_env(real=False, env={CAP_ENV: "5"})
    assert guard.cap_usd == 0 and guard.allow(100.0)
    assert guard.record(["stub-model"], {"input_tokens": 10**9}) == 0.0
    with pytest.raises(SpendRefused, match=f"served {HAIKU}"):
        guard.record([HAIKU], {})
    with pytest.raises(SpendRefused, match="a priced model"):
        guard.record(["stub-model"], {}, backend_usd=0.01)


REFUSED = {
    "model": "unknown",
    "calls": 3,
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "est_cost_usd": 0.0,
    "reconciled_cost_usd": None,
}
STUB_ENTRY = {**REFUSED, "model": "stub-model", "input_tokens": 50, "calls": 4}


def test_a_refused_run_is_an_unknown_model_with_no_tokens_and_no_cost() -> None:
    assert is_refused_run("unknown", REFUSED)
    assert is_refused_run("unknown", {})
    assert not is_refused_run(HAIKU, REFUSED)
    assert not is_refused_run("stub-model", REFUSED)
    assert not is_refused_run(None, REFUSED)
    for key, value in (
        ("input_tokens", 1),
        ("output_tokens", 1),
        ("cache_read_tokens", 1),
        ("cache_write_tokens", 1),
        ("est_cost_usd", 0.0001),
        ("reconciled_cost_usd", 0.0001),
    ):
        assert not is_refused_run("unknown", {**REFUSED, key: value}), key


def test_the_stub_guard_ignores_runs_hermes_refused() -> None:
    guard = SpendGuard()
    # Hermes answered 429 for some runs; the backend recorded them with model `unknown`.
    assert (
        guard.record(["stub-model", "unknown"], {"input_tokens": 50}, 0.0, [STUB_ENTRY, REFUSED])
        == 0.0
    )
    assert guard.record(["unknown"], {}, 0.0, [REFUSED]) == 0.0
    # Without a by_model split (an older backend) the totals decide.
    assert guard.record(["stub-model", "unknown"], {}) == 0.0


@pytest.mark.parametrize(
    "unknown_entry",
    [
        {**REFUSED, "input_tokens": 120},
        {**REFUSED, "output_tokens": 5},
        {**REFUSED, "est_cost_usd": 0.002},
        {**REFUSED, "reconciled_cost_usd": 0.002},
    ],
)
def test_the_stub_guard_still_refuses_an_unknown_model_that_carries_tokens_or_cost(
    unknown_entry: dict[str, Any],
) -> None:
    with pytest.raises(SpendRefused, match="served unknown"):
        SpendGuard().record(["stub-model", "unknown"], {}, 0.0, [STUB_ENTRY, unknown_entry])


def test_the_stub_guard_still_refuses_a_real_model_next_to_refused_runs() -> None:
    haiku = {**REFUSED, "model": HAIKU}  # a real model name with no tokens is still real
    with pytest.raises(SpendRefused, match=f"served {HAIKU}"):
        SpendGuard().record([HAIKU, "unknown"], {}, 0.0, [haiku, REFUSED])
    with pytest.raises(SpendRefused, match=f"served {HAIKU}"):
        SpendGuard().record(
            [HAIKU, "unknown"], {"input_tokens": 9}, 0.0, [{**haiku, "input_tokens": 9}, REFUSED]
        )
    # Without a split, tokens or a cost on the totals keep `unknown` a real model.
    with pytest.raises(SpendRefused, match="served unknown"):
        SpendGuard().record(["unknown"], {"output_tokens": 3})
    with pytest.raises(SpendRefused, match="a priced model"):
        SpendGuard().record(
            ["stub-model", "unknown"], {}, backend_usd=0.01, by_model=[STUB_ENTRY, REFUSED]
        )


def test_a_real_guard_prices_refused_runs_at_nothing_instead_of_refusing_them() -> None:
    prices = {HAIKU: {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25}}
    haiku = {**REFUSED, "model": HAIKU, "input_tokens": 1_000_000}
    assert by_model_cost([haiku, REFUSED], prices) == pytest.approx(1.0)
    assert token_cost([HAIKU, "unknown"], {"input_tokens": 0}, prices) == 0.0
    with pytest.raises(SpendRefused, match="no guard price for unknown"):
        by_model_cost([{**REFUSED, "input_tokens": 10}], prices)
    with pytest.raises(SpendRefused, match="no guard price for unknown"):
        token_cost(["unknown"], {"input_tokens": 10}, prices)


def test_a_real_guard_counts_spend_and_allows_only_what_fits() -> None:
    # Haiku at $1 per million input tokens (a price override), so the sums stay round.
    dollar = {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25}
    env = {CAP_ENV: "1.0", PRICES_ENV: json.dumps({HAIKU: dollar})}
    guard = SpendGuard.from_env(real=True, env=env)
    assert guard.allow(1.0) and not guard.allow(1.01)
    assert guard.record([HAIKU], {"input_tokens": 400_000}) == pytest.approx(0.4)
    # The backend's estimate counts when it is the higher figure.
    assert guard.record([HAIKU], {"input_tokens": 1}, backend_usd=0.3) == pytest.approx(0.3)
    assert guard.total_usd == pytest.approx(0.7) and guard.remaining_usd == pytest.approx(0.3)
    assert guard.allow(0.3) and not guard.allow(0.31)


def test_the_ledger_carries_spend_across_runs(tmp_path: Path) -> None:
    ledger = tmp_path / "spend" / "ledger.json"
    env = {CAP_ENV: "1.0", LEDGER_ENV: str(ledger)}
    first = SpendGuard.from_env(real=True, env=env, label="one")
    first.save()  # nothing spent: nothing written
    assert not ledger.exists() and read_ledger(None) == 0.0 and read_ledger(ledger) == 0.0
    first.record([HAIKU], {"input_tokens": 6_000_000})
    first.save()
    second = SpendGuard.from_env(real=True, env=env, label="two")
    assert second.prior_usd == pytest.approx(0.6) and not second.allow(0.5)
    second.record([HAIKU], {"input_tokens": 4_000_000})
    second.save()
    assert [r["label"] for r in json.loads(ledger.read_text("utf-8"))["runs"]] == [
        "one",
        "two",
    ]
    with pytest.raises(SpendRefused, match="at or over"):
        SpendGuard.from_env(real=True, env=env)


# ---- pricing: cache tokens, the serving model, OpenRouter's own cost ---------------------------


def _entry(
    model: str,
    *,
    inp: int = 0,
    out: int = 0,
    read: int = 0,
    write: int = 0,
    est: float = 0.0,
    billed: float | None = None,
) -> dict[str, Any]:
    """One `by_model` entry of the Operator cost block."""
    return {
        "model": model,
        "calls": 1,
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": read,
        "cache_write_tokens": write,
        "est_cost_usd": est,
        "reconciled_cost_usd": billed,
    }


def test_the_guard_prices_are_the_verified_openrouter_list_prices() -> None:
    assert PRICES_CHECKED == "2026-10-10"
    haiku, sonnet = GUARD_PRICES_PER_MTOK[HAIKU], GUARD_PRICES_PER_MTOK[SONNET]
    # List prices from OpenRouter's model listing; cache_write is the dearer 1-hour rate.
    assert (haiku["input"], haiku["output"], haiku["cache_read"]) == (0.10, 0.50, 0.01)
    assert (sonnet["input"], sonnet["output"], sonnet["cache_read"]) == (
        2.0,
        10.0,
        0.10,
    )
    assert haiku["cache_write"] >= 0.125 and sonnet["cache_write"] >= 2.50


def test_cached_input_tokens_are_not_charged_twice() -> None:
    # A real Hermes run: 39,040 input tokens, of which 28,665 were read from the cache and
    # 10,365 written (model-comparison.md). The input count already includes the cached part.
    tokens = {
        "input_tokens": 39_040,
        "output_tokens": 1_000,
        "cache_read_tokens": 28_665,
        "cache_write_tokens": 10_365,
    }
    prices = prices_from_env({})
    row = prices[HAIKU]
    expected = (
        (39_040 - 28_665) * row["input"]
        + 28_665 * row["cache_read"]
        + 10_365 * row["cache_write"]
        + 1_000 * row["output"]
    ) / 1_000_000
    assert call_cost(HAIKU, tokens, prices) == pytest.approx(expected)
    double_counted = (
        39_040 * row["input"]
        + 28_665 * row["cache_read"]
        + 10_365 * row["cache_write"]
        + 1_000 * row["output"]
    ) / 1_000_000
    assert call_cost(HAIKU, tokens, prices) < double_counted
    # All input cached: the input rate is not charged at all.
    cached = {"input_tokens": 1_000_000, "cache_read_tokens": 1_000_000}
    assert call_cost(HAIKU, cached, prices) == pytest.approx(0.01)


def test_input_below_the_cache_reads_is_taken_as_it_stands() -> None:
    # A source that reports input without the cached part: never subtract into an under-count.
    tokens = {"input_tokens": 1_000_000, "cache_read_tokens": 3_000_000}
    cost = call_cost(HAIKU, tokens, prices_from_env({}))
    assert cost == pytest.approx(1.0 * 0.10 + 3.0 * 0.01)


def test_each_call_is_priced_by_the_model_that_served_it() -> None:
    guard = SpendGuard(cap_usd=5.0, real=True)
    haiku_turns = _entry(HAIKU, inp=1_000_000, read=980_000, write=10_000, out=10_000)
    sonnet_summary = _entry(SONNET, inp=1_350, out=499)
    only_haiku = guard.cost([HAIKU], {}, by_model=[haiku_turns])
    only_sonnet = guard.cost([SONNET], {}, by_model=[sonnet_summary])
    both = guard.cost(
        [HAIKU, SONNET],
        {"input_tokens": 1_001_350, "output_tokens": 10_499},
        by_model=[haiku_turns, sonnet_summary],
    )
    assert both == pytest.approx(only_haiku + only_sonnet)
    # Haiku rates for the Haiku tokens.
    assert only_haiku == pytest.approx(
        (20_000 * 0.10 + 980_000 * 0.01 + 10_000 * 0.20 + 10_000 * 0.50) / 1_000_000
    )
    assert both < 0.05
    # Without the split, the run is priced at the dearest model (an upper bound).
    unsplit = guard.cost([HAIKU, SONNET], {"input_tokens": 1_001_350, "output_tokens": 10_499})
    assert unsplit > both * 10


def test_openrouters_own_cost_is_used_when_present() -> None:
    guard = SpendGuard(cap_usd=5.0, real=True)
    # Tokens that would price at about $20 as list; the billed figure is what counts.
    billed = _entry(SONNET, inp=10_000_000, out=0, est=0.5, billed=0.0042)
    assert guard.cost([SONNET], {}, by_model=[billed]) == pytest.approx(0.0042)
    # A model the guard has no rate for is fine when its rows carry OpenRouter's charge.
    other = _entry("openai/gpt-x", inp=1_000_000, billed=0.25)
    assert by_model_cost([other], prices_from_env({})) == pytest.approx(0.25)
    with pytest.raises(SpendRefused, match="no guard price for openai/gpt-x"):
        by_model_cost([_entry("openai/gpt-x", inp=1)], prices_from_env({}))
    # Billed and unbilled rows of one model are separate entries and are added.
    unbilled = _entry(SONNET, inp=1_000_000)
    assert by_model_cost([billed, unbilled], prices_from_env({})) == pytest.approx(0.0042 + 2.0)
    # Stub entries are free; the backend's estimate counts when it is higher than the tokens.
    assert by_model_cost([_entry("stub-model", inp=10**9, billed=9.0)], {}) == 0.0
    estimate = _entry(HAIKU, inp=1_000, est=0.30)
    assert by_model_cost([estimate], prices_from_env({})) == pytest.approx(0.30)


def test_a_cap_of_zero_refuses_and_the_guard_stops_at_the_cap() -> None:
    with pytest.raises(SpendRefused, match="needs a spend cap"):
        SpendGuard.from_env(real=True, env={CAP_ENV: "0"})
    with pytest.raises(SpendRefused, match="needs a spend cap"):
        SpendGuard.from_env(real=True, env={})
    guard = SpendGuard.from_env(real=True, env={CAP_ENV: "0.10"})
    turns = [_entry(HAIKU, inp=1_000_000)]  # $0.10 each
    assert guard.allow(0.10)
    assert guard.record([HAIKU], {}, by_model=turns) == pytest.approx(0.10)
    assert not guard.allow(0.01)


# The real run of 2026-10-10 (docs/evidence/model-comparison.md): the OpenRouter bill was
# $0.1473 in total. Its shape, from the generation records: 116 Haiku turn calls (p50 prompt
# 10,905 tokens) and 24 Sonnet turn calls (p50 12,688), about 98% of each prompt read from the
# cache; and a Sonnet summary per Application (8, 1,350 prompt and 499 output tokens each, no
# cache). Output tokens per turn call are not in the document: 100 for Haiku and 190 for Sonnet
# are assumptions. The guard of the time counted $4.9156.
REAL_BILL_USD = 0.1473
REAL_GUARD_USD_THEN = 4.9156


def _real_run() -> tuple[list[dict[str, Any]], dict[str, int]]:
    haiku_in, sonnet_in = 116 * 10_905, 24 * 12_688
    haiku = _entry(
        HAIKU,
        inp=haiku_in,
        read=round(haiku_in * 0.98),
        write=round(haiku_in * 0.02),
        out=116 * 100,
    )
    sonnet = _entry(
        SONNET,
        inp=sonnet_in,
        read=round(sonnet_in * 0.98),
        write=round(sonnet_in * 0.02),
        out=24 * 190,
    )
    summaries = _entry(SONNET, inp=8 * 1_350, out=8 * 499)
    entries = [haiku, sonnet, summaries]
    totals = {
        k: sum(e[k] for e in entries)
        for k in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        )
    }
    return entries, totals


def _old_guard_cost(totals: dict[str, int]) -> float:
    """The guard before the fix: every token at the dearest model (Sonnet, then at $3/$15), all
    input at the full rate and the cache reads and writes on top."""
    return (
        totals["input_tokens"] * 3.0
        + totals["output_tokens"] * 15.0
        + totals["cache_read_tokens"] * 0.30
        + totals["cache_write_tokens"] * 3.75
    ) / 1_000_000


def test_the_real_run_prices_within_about_two_times_the_bill() -> None:
    entries, totals = _real_run()
    # The fixture reproduces the failure: the old rule priced this run at over 20 times the bill.
    assert _old_guard_cost(totals) > 20 * REAL_BILL_USD
    guard = SpendGuard(cap_usd=5.0, real=True)
    priced = guard.cost([HAIKU, SONNET], totals, by_model=entries)
    # Never under the bill (an upper bound), and not the 33 times of the old guard.
    assert REAL_BILL_USD <= priced <= 2 * REAL_BILL_USD
    assert priced < REAL_GUARD_USD_THEN / 20
    # Per persona, as the runner counts: the 8 runs together stay inside the same bound.
    assert guard.record([HAIKU, SONNET], totals, by_model=entries) == pytest.approx(priced)
    assert guard.total_usd <= 2 * REAL_BILL_USD


# ---- the ledger: reconciliation ----------------------------------------------------------------


def test_reconciling_replaces_an_over_count_with_the_billed_figure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps({"runs": [{"label": "summary", "spent_usd": 0.665}, {"spent_usd": 4.2506}]}),
        "utf-8",
    )
    assert read_ledger(ledger) == pytest.approx(4.9156)
    with caplog.at_level("WARNING", logger="sim.spend"):
        entry = reconcile_ledger(ledger, 0.1473, "guard double-counted cached tokens")
    assert entry["spent_usd"] == pytest.approx(0.1473 - 4.9156)
    assert entry["kind"] == "reconcile" and entry["billed_usd"] == 0.1473
    assert entry["total_before_usd"] == pytest.approx(4.9156)
    assert entry["reason"] == "guard double-counted cached tokens"
    assert read_ledger(ledger) == pytest.approx(0.1473)
    assert "was $4.9156, now $0.1473" in caplog.text
    assert "double-counted" in caplog.text
    # The earlier entries stay; the correction is one more line.
    runs = json.loads(ledger.read_text("utf-8"))["runs"]
    assert [r.get("kind") for r in runs] == [None, None, "reconcile"]
    # A later run adds to the corrected total.
    env = {CAP_ENV: "5", LEDGER_ENV: str(ledger)}
    guard = SpendGuard.from_env(real=True, env=env)
    assert guard.prior_usd == pytest.approx(0.1473)


def test_a_ledger_is_never_negative_overall(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    for bad in (-0.01, float("nan"), float("inf")):
        with pytest.raises(SpendRefused, match="0 or more"):
            reconcile_ledger(ledger, bad, "reason")
    with pytest.raises(SpendRefused, match="needs a reason"):
        reconcile_ledger(ledger, 1.0, "  ")
    assert not ledger.exists()  # nothing was written
    # Reconciling to 0 is allowed and leaves a total of exactly 0.
    ledger.write_text(json.dumps({"runs": [{"spent_usd": 2.0}]}), "utf-8")
    reconcile_ledger(ledger, 0.0, "nothing was billed")
    assert read_ledger(ledger) == 0.0
    # A hand-edited ledger cannot go below 0 either: only a reconcile entry may be negative.
    ledger.write_text(json.dumps({"runs": [{"spent_usd": -3.0}, {"spent_usd": 1.0}]}), "utf-8")
    assert read_ledger(ledger) == 1.0
    ledger.write_text(
        json.dumps({"runs": [{"spent_usd": 1.0}, {"kind": "reconcile", "spent_usd": -3.0}]}),
        "utf-8",
    )
    assert read_ledger(ledger) == 0.0


def test_reconciling_a_missing_ledger_creates_it(tmp_path: Path) -> None:
    ledger = tmp_path / "new" / "ledger.json"
    entry = reconcile_ledger(ledger, 0.25, "bill read from /api/v1/key")
    assert entry["spent_usd"] == pytest.approx(0.25) and read_ledger(ledger) == 0.25


# ---- sim.stack ---------------------------------------------------------------------------------


def test_seed_password_reads_the_file_first(tmp_path: Path) -> None:
    secret = tmp_path / "pw.txt"
    secret.write_text("s3cret\n", encoding="utf-8")
    assert (
        seed_password(
            {
                "CRIBA_E2E_SEED_PASSWORD_FILE": str(secret),
                "CRIBA_E2E_SEED_PASSWORD": "x",
            }
        )
        == "s3cret"
    )
    assert seed_password({"CRIBA_E2E_SEED_PASSWORD": "x"}) == "x"
    assert seed_password({}) == ""


def test_login_and_sim_detection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sim/clock":
            return httpx.Response(200, json={})
        ok = json.loads(request.content)["password"] == "right"
        return httpx.Response(200 if ok else 401, json={})

    async def go() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://x"
        ) as http:
            assert await is_sim_stack(http)
            await login(http, "right")
            with pytest.raises(RuntimeError, match="401"):
                await login(http, "wrong")
            with pytest.raises(RuntimeError, match="no seed password"):
                await login(http, "")
        async with httpx.AsyncClient(base_url="http://127.0.0.1:9") as closed:
            assert not await is_sim_stack(closed)

    asyncio.run(go())


def test_phones_and_usage_defaults() -> None:
    rng = random.Random(1)
    assert new_phone("ES", rng).startswith("+346") and len(new_phone("MX", rng)) == 13
    assert usage_of({}) == {
        "runs": 0,
        "models": [],
        "est_cost_usd": 0.0,
        "reconciled_cost_usd": None,
        "p50_latency_ms": None,
        "by_model": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


def test_the_summary_line_names_this_run_the_earlier_ones_and_the_cap() -> None:
    stub = SpendGuard()
    assert stub.summary() == ("real-model spend: $0.0000 (stub model, no real model was called)")
    real = SpendGuard(cap_usd=5.0, real=True, prior_usd=1.5, spent_usd=0.25)
    assert real.summary() == (
        "real-model spend: this run $0.2500, earlier runs $1.5000, total $1.7500 "
        "of the $5.00 cap (guard prices; the OpenRouter bill is the figure that counts)"
    )


# ---- the backend's estimate and the guard's prices agree ---------------------------------------


def test_the_backend_price_table_equals_the_guard_table() -> None:
    # Two copies, one table: the backend must not import the test package.
    assert BACKEND_PRICES == GUARD_PRICES_PER_MTOK
    assert tuple(BACKEND_FREE_PREFIXES) == tuple(FREE_MODEL_PREFIXES)


@pytest.mark.parametrize("model", [HAIKU, SONNET, "stub-model", "deterministic-summary"])
def test_the_backend_estimate_equals_the_guard_cost_for_the_same_tokens(
    model: str,
) -> None:
    tokens = {
        "input_tokens": 8_182,
        "cache_read_tokens": 8_018,
        "cache_write_tokens": 136,
        "output_tokens": 60,
    }
    assert estimate_cost(model, tokens) == pytest.approx(
        call_cost(model, tokens, GUARD_PRICES_PER_MTOK)
    )


# ---- reported_cost: what the comparison report shows -------------------------------------------


def test_reported_cost_prefers_openrouters_charge_then_the_models_own_rates() -> None:
    haiku = _entry(HAIKU, inp=1_000_000, read=980_000, out=10_000, est=0.2504)
    sonnet = _entry(SONNET, inp=1_000_000, out=10_000, est=0.0)  # old backend: no row
    summary = _entry(SONNET, inp=5_000_000, billed=0.0077)  # tokens would price at $10
    got = reported_cost([haiku, sonnet, summary], GUARD_PRICES_PER_MTOK)
    haiku_usd = (20_000 * 0.10 + 980_000 * 0.01 + 10_000 * 0.50) / 1e6
    sonnet_usd = (1_000_000 * 2.0 + 10_000 * 10.0) / 1e6
    assert got == pytest.approx(haiku_usd + sonnet_usd + 0.0077)
    assert haiku_usd < 0.03  # not the inflated 0.2504 the backend once estimated


def test_reported_cost_falls_back_for_unpriced_models_stubs_and_old_backends() -> None:
    # A model with no rate here keeps the backend's estimate; the report never refuses.
    unpriced = _entry("some/new", inp=10, est=0.5)
    assert reported_cost([unpriced], GUARD_PRICES_PER_MTOK) == 0.5
    # Stub entries are free whatever their tokens.
    stub = _entry("stub-model", inp=9_000_000, est=3.0)
    assert reported_cost([stub], GUARD_PRICES_PER_MTOK) == 0.0
    # No by_model (an older backend): the run's total backend estimate.
    assert reported_cost([], GUARD_PRICES_PER_MTOK, 0.0123) == pytest.approx(0.0123)
    assert reported_cost([], GUARD_PRICES_PER_MTOK) == 0.0
