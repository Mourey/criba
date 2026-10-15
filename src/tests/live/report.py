"""The live smoke report: what was tested, pass or fail, latency and cost, as Markdown.

Written to be shown to the Client: a result table first, then latency and cost, then each flow's
checks and a short transcript. Nothing in it is edited by hand; a failed flow is shown as it
failed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from live.flows import FIRST_REPLY_TARGET_S, LATER_REPLY_TARGET_S, FlowResult, Latency
from sim.asserts import percentile


@dataclass
class RunMeta:
    """Facts about the run that the flows do not know."""

    started_at: str  # UTC, ISO
    commit: str
    dirty: bool
    base_url: str
    model: str
    summary_model: str
    spend_line: str
    note: str = ""
    stopped: str | None = None


def model_latencies(results: Sequence[FlowResult]) -> list[float]:
    """Seconds of every reply that a model call produced (code-written replies are left out)."""
    return [lat.seconds for r in results for lat in r.latencies if lat.model]


def latency_stats(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "p50": None, "p95": None, "max": None, "within_5": 0, "within_10": 0}
    return {
        "n": len(values),
        "p50": percentile(values, 0.5),
        "p95": percentile(values, 0.95),
        "max": max(values),
        "within_5": sum(v <= FIRST_REPLY_TARGET_S for v in values),
        "within_10": sum(v <= LATER_REPLY_TARGET_S for v in values),
    }


def _replies(n: int) -> str:
    return f"{n} reply" if n == 1 else f"{n} replies"


def _s(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _usd(value: float) -> str:
    return f"{value:.4f}"


def _cell(text: object) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return [*lines, ""]


def _flow_latency_row(r: FlowResult) -> list[object]:
    stats = latency_stats([lat.seconds for lat in r.latencies if lat.model])
    return [r.title, stats["n"], _s(stats["p50"]), _s(stats["p95"]), _s(stats["max"])]


def _latency_lines(lats: Sequence[Latency]) -> list[str]:
    return [
        f"- {lat.label}: {lat.seconds:.1f} s"
        + ("" if lat.model else " (written by code, no model)")
        for lat in lats
    ]


def render(results: Sequence[FlowResult], meta: RunMeta) -> str:
    ran = [r for r in results if r.status != "not run"]
    passed = [r for r in results if r.passed]
    out: list[str] = ["# Live smoke run", ""]
    out.append(
        "Real conversations against the running product: the production Recruiter profile on "
        f"{meta.model} through OpenRouter, no stub model and no simulated clock. Candidates write "
        "through the web chat API; the record is read back through the Operator API. Checks are on "
        "the record (stage, events, answers, queue items, attachments), not on the agent's exact "
        "words."
    )
    out.append("")
    out += _table(
        ["Fact", "Value"],
        [
            ["Date (UTC)", meta.started_at],
            ["Commit", meta.commit + (" (uncommitted changes in the tree)" if meta.dirty else "")],
            ["Stack", meta.base_url],
            ["Turn model", meta.model],
            ["Summary model", meta.summary_model],
            *([["Note", meta.note]] if meta.note else []),
        ],
    )
    out += ["## Result", "", f"**{len(passed)} of {len(results)} flows passed.**", ""]
    if meta.stopped:
        out += [f"The run stopped early: {meta.stopped}", ""]
    out += _table(
        ["Flow", "Result", "Conversations", "Checks passed", "Cost (USD)", "Time (s)"],
        [
            [
                r.title,
                r.status.upper() if r.status != "pass" else "pass",
                r.conversations,
                f"{sum(c.ok for c in r.checks)} of {len(r.checks)}",
                _usd(r.charge.cost_usd),
                _s(r.elapsed_s),
            ]
            for r in results
        ],
    )

    stats = latency_stats(model_latencies(results))
    out += ["## Latency", ""]
    if stats["n"]:
        n = stats["n"]
        out.append(
            "Seconds from the Candidate's message (or tap, or Approval) to the agent's first "
            f"reply, over {_replies(n)} that a model call produced: "
            f"**p50 {_s(stats['p50'])} s, p95 {_s(stats['p95'])} s**, "
            f"slowest {_s(stats['max'])} s. "
            f"{stats['within_5']} of {n} within {FIRST_REPLY_TARGET_S:.0f} s (the first-reply "
            f"target), {stats['within_10']} of {n} within {LATER_REPLY_TARGET_S:.0f} s (the "
            "target for later replies). Latency does not decide pass or fail."
        )
        out.append("")
        out += _table(
            ["Flow", "Replies", "p50 (s)", "p95 (s)", "Slowest (s)"],
            [_flow_latency_row(r) for r in ran],
        )
    else:
        out += ["No model reply was timed.", ""]

    total = sum(r.charge.cost_usd for r in results)
    billed = sum(r.charge.billed_usd for r in results)
    out += ["## Cost", ""]
    out.append(
        "From the usage ledger, per conversation: OpenRouter's own charge where a call reports "
        "one (the summary), otherwise the call's tokens at OpenRouter's list prices (Hermes "
        "reports tokens, not dollars). The OpenRouter activity page is the bill."
    )
    out.append("")
    out += _table(
        ["Flow", "Agent runs", "Input tokens", "Cached", "Output tokens", "Cost (USD)"],
        [
            [
                r.title,
                r.charge.runs,
                r.charge.tokens.get("input_tokens", 0),
                r.charge.tokens.get("cache_read_tokens", 0),
                r.charge.tokens.get("output_tokens", 0),
                _usd(r.charge.cost_usd),
            ]
            for r in ran
        ]
        + [["**Total**", "", "", "", "", f"**{_usd(total)}**"]],
    )
    costed = [r for r in ran if r.charge.runs]
    if costed:
        out.append(
            f"Per conversation that called a model: {_usd(total / len(costed))} USD on average "
            f"over {len(costed)}. Of the total, {_usd(billed)} USD is OpenRouter's own charge."
        )
        out.append("")
    out += [f"`{meta.spend_line}`", ""]

    out += ["## Flows", ""]
    for number, r in enumerate(results, 1):
        out.append(f"### {number}. {r.title}: {r.status.upper() if r.status != 'pass' else 'pass'}")
        out.append("")
        if r.not_run:
            out += [f"Not run: {r.not_run}", ""]
            continue
        if r.error:
            out += [f"Error: `{_cell(r.error)}`", ""]
        for c in r.checks:
            out.append(
                f"- {'pass' if c.ok else 'FAIL'}: {c.name}" + ("" if c.ok else f" ({c.detail})")
            )
        out.append("")
        if r.notes:
            out += [f"- {n}" for n in r.notes]
            out.append("")
        if r.latencies:
            out += _latency_lines(r.latencies)
            out.append("")
        if r.cost_unknown:
            out += ["The cost of this flow could not be read; the spend line is a lower bound.", ""]
        if r.transcript:
            out += ["<details><summary>Transcript</summary>", ""]
            out += [f"    {line}" for line in r.transcript]
            out += ["", "</details>", ""]
    return "\n".join(out).rstrip() + "\n"


def short_summary(results: Sequence[FlowResult]) -> str:
    """The terminal view: one line per flow, then the totals."""
    lines = [
        f"{r.status.upper():7} {r.id:16} {sum(c.ok for c in r.checks)}/{len(r.checks)} checks, "
        f"${r.charge.cost_usd:.4f}"
        for r in results
    ]
    stats = latency_stats(model_latencies(results))
    if stats["n"]:
        lines.append(
            f"latency p50 {_s(stats['p50'])} s, p95 {_s(stats['p95'])} s "
            f"over {_replies(int(stats['n'] or 0))}"
        )
    total = sum(r.charge.cost_usd for r in results)
    lines.append(
        f"{sum(r.passed for r in results)} of {len(results)} flows passed, cost ${total:.4f}"
    )
    return "\n".join(lines)


__all__ = ["RunMeta", "latency_stats", "model_latencies", "render", "short_summary"]
