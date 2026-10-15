"""The live smoke report: latency figures, the Markdown, and the terminal summary."""

from __future__ import annotations

from dataclasses import replace

import pytest

from live.client import Charge
from live.flows import FlowResult
from live.report import RunMeta, latency_stats, model_latencies, render, short_summary

META = RunMeta(
    started_at="2026-10-10 15:30:34",
    commit="abc1234",
    dirty=False,
    base_url="http://localhost:18160",
    model="anthropic/claude-haiku-5.5",
    summary_model="anthropic/claude-sonnet-5.5",
    spend_line="real-model spend: this run $0.0300, earlier runs $2.0000, total $2.0300",
)


def passing() -> FlowResult:
    res = FlowResult("qualified", "Qualified")
    res.check("stage qualified", True)
    res.check("summary prose written", True)
    res.conversations = 1
    for seconds in (2.0, 4.0, 6.0, 12.0):
        res.timed("reply", seconds)
    res.timed("reply to STOP", 0.0, model=False)
    res.note("Summary: Daniel is qualified.")
    res.transcript = ["Candidate: hi", "Agent: hello | there"]
    res.charge = Charge(
        cost_usd=0.0095,
        billed_usd=0.0085,
        guard_usd=0.0101,
        runs=4,
        tokens={"input_tokens": 13_000, "cache_read_tokens": 6_000, "output_tokens": 800},
    )
    res.elapsed_s = 42.2
    return res


def failing() -> FlowResult:
    res = FlowResult("knockout_review", "Knockout | Review")
    res.check("stage ok", True)
    res.check("one Review queue item open", False, "0 open Review items")
    res.conversations = 1
    res.timed("reply", 3.0)
    res.charge = Charge(cost_usd=0.006, runs=3)
    res.cost_unknown = True
    return res


def test_latency_stats_use_the_driver_percentile_and_the_targets() -> None:
    stats = latency_stats([2.0, 4.0, 6.0, 12.0])
    assert stats["n"] == 4 and stats["max"] == 12.0
    assert stats["p50"] == 6.0  # the driver's percentile rounds to the nearest rank
    assert stats["p95"] == 12.0
    assert stats["within_5"] == 2 and stats["within_10"] == 3
    assert latency_stats([])["n"] == 0 and latency_stats([])["p50"] is None


def test_code_written_replies_are_left_out_of_the_model_latency() -> None:
    assert model_latencies([passing()]) == [2.0, 4.0, 6.0, 12.0]


def test_render_shows_result_latency_cost_and_each_flow() -> None:
    errored = FlowResult("booking", "Booking")
    errored.error = "RuntimeError: boom | pipe"
    skipped = FlowResult("stop", "STOP")
    skipped.not_run = "spend cap"
    text = render([passing(), failing(), errored, skipped], META)
    assert text.startswith("# Live smoke run\n")
    assert "**1 of 4 flows passed.**" in text
    assert "| Commit | abc1234 |" in text
    assert "| Qualified | pass | 1 | 2 of 2 | 0.0095 | 42.2 |" in text
    assert "| Knockout / Review | FAIL | 1 | 1 of 2 |" in text  # pipes would break the table
    assert "| Booking | ERROR |" in text and "| STOP | NOT RUN |" in text
    assert "**p50 4.0 s, p95 12.0 s**" in text and "3 of 5 within 5 s" in text
    assert "- FAIL: one Review queue item open (0 open Review items)" in text
    assert "Error: `RuntimeError: boom / pipe`" in text
    assert "Not run: spend cap" in text
    assert "The cost of this flow could not be read" in text
    assert "(written by code, no model)" in text
    assert "    Candidate: hi" in text
    assert f"`{META.spend_line}`" in text
    assert "| **Total** |" in text and "**0.0155**" in text
    assert "Of the total, 0.0085 USD is OpenRouter's own charge." in text
    assert text.endswith("\n") and not text.endswith("\n\n")


def test_render_marks_uncommitted_trees_notes_and_early_stops() -> None:
    meta = replace(META, dirty=True, note="stack built from main", stopped="cap")
    text = render([passing()], meta)
    assert "abc1234 (uncommitted changes in the tree)" in text
    assert "| Note | stack built from main |" in text
    assert "The run stopped early: cap" in text


def test_render_without_any_model_reply_says_so() -> None:
    text = render([FlowResult("stop", "STOP")], META)
    assert "No model reply was timed." in text
    assert "**0 of 1 flows passed.**" in text


def test_short_summary_is_one_line_per_flow_and_totals() -> None:
    text = short_summary([passing(), failing()])
    lines = text.splitlines()
    assert lines[0].startswith("PASS    qualified") and "2/2 checks, $0.0095" in lines[0]
    assert lines[1].startswith("FAIL    knockout_review") and "1/2 checks" in lines[1]
    assert lines[2] == "latency p50 4.0 s, p95 12.0 s over 5 replies"
    assert lines[-1] == "1 of 2 flows passed, cost $0.0155"


@pytest.mark.parametrize("count,word", [(1, "1 reply"), (2, "2 replies")])
def test_the_summary_pluralises_replies(count: int, word: str) -> None:
    res = FlowResult("x", "X")
    for _ in range(count):
        res.timed("reply", 1.0)
    assert word in short_summary([res])
