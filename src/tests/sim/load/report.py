"""Load report: metrics, the first saturated component, markdown (spec 004 task 8, ADR 0028).

From a :class:`~sim.load.runner.LoadRun`:

- **throughput**: Candidate messages a second, agent runs a second, Candidates finished a
  minute; the offered rate as Candidates a week (the plan's weekly rate times the speed-up),
  and the message rate as a multiple of ADR 0028's peak (0.7 messages a second at 10,000 a
  week, scaled to the plan's weekly rate);
- **latency**: p50, p95 and p99 of the reply latency the driver saw (send to first agent
  message), all turns and first turns apart, against the Phase 1 limits (first reply within 5 s,
  later ones within 10 s); the median Hermes run latency from the usage ledger next to it;
- **errors**: Candidates that hit an HTTP error, a transport error or a reply timeout, or whose
  agent run Hermes refused at its concurrent-run cap (`hermes_refused`, from the usage ledger's
  `unknown`-model rows with no tokens), over Candidates started;
- **correctness**: Applications that ended as their persona expects (`sim.asserts`), when the
  run was read back;
- **cost**: the backend's estimate summed over the run, per Candidate, and per week at the
  plan's rate (0 on the stub model);
- **resources**: CPU (p90 and max, in % of one core) and memory per Compose service.

**The first saturated component** is named by rules, in this order: a service whose p90 CPU is
at or over 85 % of one core (the backend and Hermes are single Python processes, so one core is
their ceiling); otherwise, when the run breaks a limit (p95 over 10 s, or more than 1 % of
Candidates failing), the component the errors point at; otherwise nothing is saturated and the
busiest service is the one that saturates first, with a linear estimate of the rate at which it
would. Each component maps to its fix.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from sim.load.runner import LoadRun

FIRST_REPLY_LIMIT_S = 5.0
TURN_LIMIT_S = 10.0
ERROR_BUDGET = 0.01
CPU_SATURATED_PCT = 85.0
ADR_PEAK_MSGS_PER_S_AT_10K = 0.7  # ADR 0028: about 0.7 messages a second at peak, 10,000 a week

FIXES: dict[str, str] = {
    "backend": (
        "One uvicorn worker runs the web chat, the tools callback and the router, and SQLite has "
        "one writer. First run more uvicorn workers (reads scale, writes stay serialised), then "
        "move agent runs onto a queue with a bounded worker pool; past that, Postgres (ADR 0028)."
    ),
    "hermes": (
        "Hermes's API server runs every session in one process. Add Hermes replicas and route "
        "each Application's session to one of them (the session id is stable), or raise its run "
        "concurrency."
    ),
    "stub-llm": (
        "The stub model is test-only and never runs in production. With a real model this slot "
        "is OpenRouter: its rate limit and latency. Ask for a higher limit, and queue turns."
    ),
    "web": (
        "nginx holds one connection per long poll: raise worker_connections, or move the web "
        "chat to server-sent events."
    ),
    "model": (
        "The model provider (OpenRouter) throttles or slows down: raise the account's rate "
        "limit, keep the Sonnet fallback (ADR 0025), and queue turns instead of failing them."
    ),
    "backend-rate-limit": (
        "The web chat's per-conversation rate limit answers 429: a load profile problem, not "
        "capacity. Check the limit against a real Candidate's pace."
    ),
}


def pct(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile (q in 0..100) of `values`; None when empty."""
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * q / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def max_concurrency(spans: Sequence[tuple[float, float]]) -> int:
    """Most Candidates in conversation at once, from (start, end) spans."""
    edges = sorted([(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans])
    best = current = 0
    for _, step in edges:
        current += step
        best = max(best, current)
    return best


def compute(run: LoadRun) -> dict[str, Any]:
    plan = run.plan
    cands = run.candidates
    started = [c for c in cands if c.started_s is not None]
    errors = [c for c in started if c.error is not None]
    timeouts = [c for c in started if c.ended == "timeout"]
    finished = [c for c in started if c.ok]
    turns = [t for c in started for t in c.turns]
    firsts = [
        c.turns[0].latency_first_s
        for c in started
        if c.turns and c.turns[0].latency_first_s is not None
    ]
    latencies = [t.latency_first_s for t in turns if t.latency_first_s is not None]
    bursts = [t.latency_last_s for t in turns if t.latency_last_s is not None]
    wall = max(run.wall_s, 1e-9)
    records = [c.record for c in started if c.record and "correct" in c.record]
    runs_total = sum(r["runs"] for r in records)
    cost_total = sum(r["est_cost_usd"] for r in records)
    hermes_p50 = [
        r["p50_run_latency_ms"] for r in records if r.get("p50_run_latency_ms") is not None
    ]
    spans = [(c.started_s or 0.0, c.finished_s or c.started_s or 0.0) for c in started]
    error_kinds = Counter(c.error_kind or "other" for c in errors)
    if timeouts:
        error_kinds["reply_timeout"] += len(timeouts)
    # A Candidate whose agent run Hermes refused (429 at its concurrent-run cap) is degraded even
    # when the web chat answered it: count it once, unless it already failed another way.
    gone = {id(c) for c in errors} | {id(c) for c in timeouts}
    refused = [
        c for c in started if id(c) not in gone and c.record and c.record.get("refused_runs", 0) > 0
    ]
    if refused:
        error_kinds["hermes_refused"] += len(refused)
    refused_runs_total = sum(
        r.get("refused_runs", 0) for r in (c.record for c in started if c.record) if r
    )
    failed = len(errors) + len(timeouts) + len(refused)
    adr_peak = ADR_PEAK_MSGS_PER_S_AT_10K * plan.weekly_rate / 10_000
    per_candidate_cost = cost_total / len(records) if records else None
    return {
        "plan": {
            "weekly_rate": plan.weekly_rate,
            "peak_factor": plan.peak_factor,
            "speedup": plan.speedup,
            "sim_minutes": round(plan.sim_duration_s / 60, 2),
            "wall_minutes": round(plan.wall_duration_s / 60, 2),
            "offered_per_wall_minute": round(plan.wall_rate_per_s * 60, 3),
            "description": plan.describe(),
        },
        "wall_s": round(run.wall_s, 1),
        "candidates": {
            "scheduled": len(cands),
            "started": len(started),
            "finished": len(finished),
            "failed": failed,
            "spend_cap": sum(1 for c in cands if c.ended == "spend_cap"),
            "ended": dict(Counter(c.ended for c in cands)),
            "max_concurrent": max_concurrency(spans),
        },
        "throughput": {
            "messages_per_s": round(len(turns) / wall, 3),
            "agent_runs_per_s": round(runs_total / wall, 3) if records else None,
            "finished_per_minute": round(len(finished) / wall * 60, 2),
            "offered_weekly_equivalent": round(plan.weekly_rate * plan.speedup),
            "adr_peak_multiple": round(len(turns) / wall / adr_peak, 1),
        },
        "latency_s": {
            "turns": len(turns),
            "p50": _round(pct(latencies, 50)),
            "p95": _round(pct(latencies, 95)),
            "p99": _round(pct(latencies, 99)),
            "max": _round(max(latencies) if latencies else None),
            "first_reply_p95": _round(pct(firsts, 95)),
            "first_over_5s": sum(1 for v in firsts if v > FIRST_REPLY_LIMIT_S),
            "over_10s": sum(1 for v in latencies if v > TURN_LIMIT_S),
            "burst_p95": _round(pct(bursts, 95)),
            "hermes_run_p50_ms_median": _round(pct(hermes_p50, 50), 0),
        },
        "errors": {
            "rate": round(failed / len(started), 4) if started else None,
            "by_kind": dict(error_kinds),
            "refused_runs": refused_runs_total,
            "samples": [c.error for c in errors[:5]],
            "timed_out_turns": sum(1 for t in turns if t.timed_out),
        },
        "correctness": {
            "read_back": len(records),
            "correct": sum(1 for r in records if r["correct"]),
            "rate": round(sum(1 for r in records if r["correct"]) / len(records), 4)
            if records
            else None,
            "failures": [
                f"{c.persona_id}: {c.record['failures'][0]}"
                for c in started
                if c.record and c.record.get("failures")
            ][:5],
        },
        "cost": {
            "total_usd": round(cost_total, 6),
            "per_candidate_usd": _round(per_candidate_cost, 6),
            "per_week_at_plan_usd": _round(
                per_candidate_cost * plan.weekly_rate if per_candidate_cost is not None else None,
                2,
            ),
            "models": sorted({m for r in records for m in r["models"]}),
            "guard_spent_usd": run.spent_usd,
        },
        "resources": resource_summary(run),
    }


def resource_summary(run: LoadRun) -> dict[str, dict[str, float]]:
    by_service: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for s in run.resources:
        by_service[s.service].append((s.cpu_pct, s.mem_mib))
    out = {}
    for service, rows in sorted(by_service.items()):
        cpus = [c for c, _ in rows]
        out[service] = {
            "cpu_p90": round(pct(cpus, 90) or 0.0, 1),
            "cpu_max": round(max(cpus), 1),
            "mem_max_mib": round(max(m for _, m in rows), 1),
            "samples": len(rows),
        }
    return out


def find_bottleneck(metrics: dict[str, Any], *, real: bool = False) -> dict[str, Any]:
    """The first saturated component (or the one that saturates first), its evidence and fix."""
    resources: dict[str, dict[str, float]] = metrics["resources"]
    lat = metrics["latency_s"]
    errors = metrics["errors"]
    rate = errors["rate"] or 0.0
    breach = (lat["p95"] or 0.0) > TURN_LIMIT_S or rate > ERROR_BUDGET or lat["first_over_5s"] > 0
    busiest = max(resources.items(), key=lambda kv: kv[1]["cpu_p90"], default=None)
    model_slot = "model" if real else "stub-llm"
    if busiest and busiest[1]["cpu_p90"] >= CPU_SATURATED_PCT:
        name, row = busiest
        return {
            "component": name,
            "saturated": True,
            "evidence": (
                f"{name} p90 CPU {row['cpu_p90']:.0f}% of one core (max {row['cpu_max']:.0f}%)"
            ),
            "fix": FIXES.get(name, ""),
        }
    if breach:
        kinds = errors["by_kind"]
        if kinds.get("http_429"):
            name, why = "backend-rate-limit", f"{kinds['http_429']} Candidates hit 429"
        elif kinds.get("hermes_refused"):
            name = "hermes"
            why = (
                f"Hermes refused runs for {kinds['hermes_refused']} Candidates "
                f"({errors['refused_runs']} runs) at its concurrent-run cap (429)"
            )
        elif any(k.startswith("http_5") for k in kinds):
            name, why = "backend", f"5xx answers: {kinds}"
        else:
            hermes_ms = lat["hermes_run_p50_ms_median"]
            if hermes_ms is not None and hermes_ms / 1000 > (lat["p50"] or 0) * 0.5:
                name = model_slot if real else "hermes"
                why = f"agent runs take {hermes_ms:.0f} ms (median), most of the reply time"
            else:
                name = "backend"
                why = "replies wait outside the agent run (queueing in the backend)"
        return {
            "component": name,
            "saturated": True,
            "evidence": f"limit broken (p95 {lat['p95']} s, error rate {rate:.2%}): {why}",
            "fix": FIXES.get(name, ""),
        }
    if busiest is None:
        return {
            "component": None,
            "saturated": False,
            "evidence": "no limit broken; no resource samples (run with --compose-project)",
            "fix": "",
        }
    name, row = busiest
    msgs = metrics["throughput"]["messages_per_s"]
    headroom = CPU_SATURATED_PCT / row["cpu_p90"] if row["cpu_p90"] > 0 else None
    estimate = (
        f"; at linear scaling it reaches {CPU_SATURATED_PCT:.0f}% near {msgs * headroom:.1f} "
        f"messages/s ({headroom:.0f}x this run)"
        if headroom
        else ""
    )
    return {
        "component": name,
        "saturated": False,
        "evidence": (
            f"no limit broken; busiest is {name} at p90 CPU {row['cpu_p90']:.0f}% of one core"
            f"{estimate}"
        ),
        "fix": FIXES.get(name, ""),
    }


# ---- markdown ----------------------------------------------------------------------------------


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".") + suffix
    if isinstance(value, int):
        return f"{value:,}{suffix}"
    return f"{value}{suffix}"


def step_markdown(label: str, m: dict[str, Any], bottleneck: dict[str, Any]) -> str:
    c, t, lat, e, cost = (
        m["candidates"],
        m["throughput"],
        m["latency_s"],
        m["errors"],
        m["cost"],
    )
    corr = m["correctness"]
    lines = [
        f"### {label}",
        "",
        f"Plan: {m['plan']['description']}. Ran {m['wall_s']} s.",
        "",
        "| Metric | Value |",
        "|---|---|",
        "| Candidates scheduled / started / finished | "
        f"{c['scheduled']} / {c['started']} / {c['finished']} |",
        f"| Ended | {', '.join(f'{k} {v}' for k, v in sorted(c['ended'].items()))} |",
        f"| Most Candidates in conversation at once | {c['max_concurrent']} |",
        f"| Candidate messages per second | {_fmt(t['messages_per_s'])} |",
        f"| Agent runs per second | {_fmt(t['agent_runs_per_s'])} |",
        "| Offered rate, as Candidates a week (same peak shape) | "
        f"{_fmt(t['offered_weekly_equivalent'])} |",
        "| Message rate, as a multiple of ADR 0028's peak at this weekly rate | "
        f"{_fmt(t['adr_peak_multiple'])}x |",
        "| Reply latency p50 / p95 / p99 (s) | "
        f"{_fmt(lat['p50'])} / {_fmt(lat['p95'])} / {_fmt(lat['p99'])} |",
        "| First reply p95 (s); first replies over 5 s | "
        f"{_fmt(lat['first_reply_p95'])}; {lat['first_over_5s']} |",
        f"| Turns over 10 s, of all turns | {lat['over_10s']} of {lat['turns']} |",
        "| Agent run latency, median of per-Application medians (ms) | "
        f"{_fmt(lat['hermes_run_p50_ms_median'])} |",
        f"| Error rate (Candidates) | {_fmt(e['rate'])} {dict(e['by_kind']) or ''} |",
        f"| Applications as the persona expects | {corr['correct']} of {corr['read_back']} |",
        "| Cost: total / per Candidate / per week at this rate (USD) | "
        f"{_fmt(cost['total_usd'])} / {_fmt(cost['per_candidate_usd'])} / "
        f"{_fmt(cost['per_week_at_plan_usd'])} |",
        f"| Models served | {', '.join(cost['models']) or 'n/a'} |",
        "",
    ]
    if m["resources"]:
        lines += [
            "| Service | CPU p90 (% of one core) | CPU max | Memory max (MiB) |",
            "|---|---|---|---|",
        ]
        for service, r in m["resources"].items():
            lines.append(f"| {service} | {r['cpu_p90']} | {r['cpu_max']} | {r['mem_max_mib']} |")
        lines.append("")
    if e["samples"]:
        lines += ["Errors (first five):", ""] + [f"- `{s}`" for s in e["samples"]] + [""]
    if corr["failures"]:
        lines += ["Not as expected (first five):", ""] + [f"- {f}" for f in corr["failures"]] + [""]
    verdict = "saturated" if bottleneck["saturated"] else "not saturated"
    lines += [
        f"**Bottleneck ({verdict}):** {bottleneck['component'] or 'none'}. "
        f"{bottleneck['evidence']}.",
        "",
    ]
    return "\n".join(lines)


def markdown(
    steps: Sequence[tuple[str, dict[str, Any], dict[str, Any]]],
    *,
    title: str = "Load results",
    preamble: str = "",
) -> str:
    """The report for one or more runs (a ramp is several steps of the same mix)."""
    out = [f"# {title}", ""]
    if preamble:
        out += [preamble.strip(), ""]
    if len(steps) > 1:
        out += [
            "## Ramp",
            "",
            "| Step | Offered, Candidates a week | Messages/s (x ADR peak) | p95 (s) | "
            "Error rate | Bottleneck |",
            "|---|---|---|---|---|---|",
        ]
        for label, m, b in steps:
            out.append(
                f"| {label} | {_fmt(m['throughput']['offered_weekly_equivalent'])} | "
                f"{m['throughput']['messages_per_s']} ({m['throughput']['adr_peak_multiple']}x) | "
                f"{_fmt(m['latency_s']['p95'])} | {_fmt(m['errors']['rate'])} | "
                f"{b['component'] or 'none'} ({'saturated' if b['saturated'] else 'headroom'}) |"
            )
        out.append("")
    out += ["## Runs", ""]
    for label, m, b in steps:
        out.append(step_markdown(label, m, b))
    first = next((s for s in steps if s[2]["saturated"]), steps[-1] if steps else None)
    if first is not None:
        label, _, b = first
        out += [
            "## Next bottleneck and its fix",
            "",
            f"First seen in **{label}**: **{b['component'] or 'none'}**. {b['evidence']}.",
            "",
            b["fix"] or "No fix needed at this rate.",
            "",
        ]
    return "\n".join(out)


__all__ = [
    "CPU_SATURATED_PCT",
    "ERROR_BUDGET",
    "FIXES",
    "compute",
    "find_bottleneck",
    "markdown",
    "max_concurrency",
    "pct",
    "resource_summary",
    "step_markdown",
]
