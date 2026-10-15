"""Results and economics aggregates (spec 003 task 7, ADR 0019).

**Results** (Recruiters and Operators): a cohort of Applications created in the period,
limited to the caller's Location scope. Counts are computed from the Application rows and
the event timeline. **Economics** and the **model comparison** (Operators only, 403 for
everyone else) are computed from the usage ledger (``usage``); the reconciled cost counts
when there is one, the estimate otherwise.

Definitions worth knowing:

- ``overturn_rate`` = Overturned / (Upheld + Overturned) Reviews; null before any resolves.
- ``cache_hit_rate`` = cache-read tokens / input tokens (input includes cached tokens).
- ``per_qualified`` and ``per_interview`` divide total spend by the Applications, among
  those with ledger rows, that reached Qualified or have an Interview booking.
- ``retries`` is 0: the ledger has no retry column yet.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import Query

from criba.api import present
from criba.api.auth import Ctx, Operator, User
from criba.api.context import ApiContext
from criba.api.errors import not_found, validation_error
from criba.api.routing import new_router
from criba.db import repo
from criba.db.connection import parse_iso
from criba.jev import store as jev_store

router = new_router(tags=["metrics"])

DEFAULT_DAYS = 30
SCREENING_STAGES = present.SCREENING_STAGES
ALL_STAGES = (*SCREENING_STAGES, "qualified", "approved", "interview_booked")


def _period(from_: date | None, to: date | None, now: datetime) -> tuple[date, date, str, str]:
    end_day = to or now.date()
    try:
        start_day = from_ or end_day - timedelta(days=DEFAULT_DAYS - 1)
    except OverflowError:  # a default period would start before year 1
        raise validation_error("to is too early.", "to") from None
    if start_day > end_day:
        raise validation_error("from must not be after to.", "from")
    if end_day == date.max:  # the period's end bound is the day after it
        raise validation_error("to is too late.", "to")
    return (
        start_day,
        end_day,
        f"{start_day.isoformat()}T00:00:00Z",
        f"{(end_day + timedelta(days=1)).isoformat()}T00:00:00Z",
    )


def _pcts(values: list[float]) -> dict[str, float | None]:
    ordered = sorted(values)
    if not ordered:
        return {"p50": None, "p95": None, "p99": None}
    return {f"p{p}": round(present.percentile(ordered, p), 3) for p in (50, 95, 99)}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@router.get("/metrics/results")
def get_results(
    user: User,
    ctx: Ctx,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    location_id: str | None = None,
    country: Annotated[Literal["ES", "MX"] | None, Query()] = None,
) -> dict[str, Any]:
    now = ctx.clock.now()
    start_day, end_day, start, end = _period(from_, to, now)
    scope, scope_params = present.scope_sql(user)
    base_where, params = repo.results_where(
        scope, scope_params, location_id=location_id, country=country
    )
    apps, event_counts, waiting, by_location = _read_cohort(
        ctx, user, base_where, params, start, end, location_id
    )
    open_by_stage, outcomes, knockouts, dropoff, to_qualify = _tally_cohort(apps)
    return {
        "from": start_day.isoformat(),
        "to": end_day.isoformat(),
        "applications": len(apps),
        "funnel": {"open_by_stage": open_by_stage, "outcomes": outcomes},
        "time_to_qualify_seconds": _pcts(to_qualify),
        "knockouts_by_criterion": knockouts,
        "reviews": _review_counts(event_counts),
        "dropoff_by_stage": dropoff,
        "qualified_waiting": waiting,
        "takeovers": event_counts.get("takeover_started", 0),
        "escalations": event_counts.get("escalation_raised", 0),
        "by_location": [
            {
                "location_id": r["location_id"],
                "applications": r["applications"],
                "qualified": r["qualified"] or 0,
                "knockouts": r["knockouts"] or 0,
            }
            for r in by_location
        ],
    }


def _read_cohort(
    ctx: ApiContext,
    user: Any,
    base_where: str,
    params: list[Any],
    start: str,
    end: str,
    location_id: str | None,
) -> tuple[list[Any], dict[str, int], int, list[Any]]:
    with ctx.db() as conn:
        apps = repo.cohort_applications(conn, base_where, params, start, end)
        event_counts = {
            r["kind"]: r["n"]
            for r in repo.cohort_event_counts(conn, base_where, params, start, end)
        }
        waiting = repo.qualified_waiting_count(conn, base_where, params)
        by_location = repo.cohort_by_location(conn, base_where, params, start, end, location_id)
        if not user.is_operator:
            by_location = [r for r in by_location if r["location_id"] in user.location_ids]
    return apps, event_counts, waiting, by_location


def _tally_cohort(
    apps: list[Any],
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int], list[float]]:
    open_by_stage = dict.fromkeys(ALL_STAGES, 0)
    outcomes = dict.fromkeys(("knockout", "rejection", "abandoned", "withdrawn"), 0)
    knockouts = {"zone": 0, "driver_license": 0}
    dropoff = dict.fromkeys(SCREENING_STAGES, 0)
    to_qualify: list[float] = []
    for a in apps:
        if a["status"] == "open":
            open_by_stage[a["stage"]] += 1
            if a["stalled_since"] is not None:
                dropoff[a["stage"]] = dropoff.get(a["stage"], 0) + 1
        else:
            outcomes[a["outcome"]] += 1
            if a["outcome"] == "knockout" and a["knockout_criterion"] in knockouts:
                knockouts[a["knockout_criterion"]] += 1
            if a["outcome"] == "abandoned":
                dropoff[a["stage"]] = dropoff.get(a["stage"], 0) + 1
        if a["qualified_at"]:
            to_qualify.append(
                (parse_iso(a["qualified_at"]) - parse_iso(a["created_at"])).total_seconds()
            )
    return open_by_stage, outcomes, knockouts, dropoff, to_qualify


def _review_counts(event_counts: dict[str, int]) -> dict[str, Any]:
    upheld = event_counts.get("review_upheld", 0)
    overturned = event_counts.get("review_overturned", 0)
    return {
        "requested": event_counts.get("review_requested", 0),
        "upheld": upheld,
        "overturned": overturned,
        "overturn_rate": round(overturned / (upheld + overturned), 4)
        if upheld + overturned
        else None,
    }


# ---------------------------------------------------------------------------
# Economics (Operators only)
# ---------------------------------------------------------------------------


@router.get("/metrics/conversation-signals")
def get_conversation_signals(
    user: User,
    ctx: Ctx,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
) -> dict[str, Any]:
    """Per screening stage, how many typed messages were read and how many a Jev signal fired
    on (ADR 0051): counts and shares for improving the bot, in the caller's Location scope. No
    Candidate and no Application appears in it."""
    now = ctx.clock.now()
    start_day, end_day, start, end = _period(from_, to, now)
    scope, params = present.scope_sql(user)
    with ctx.db() as conn:
        stages = jev_store.aggregate_by_stage(conn, scope, params, start, end)
    for row in stages:
        read = row["messages"]
        row["shares"] = {
            name: round(row[name] / read, 4) if read else None
            for name in ("frustrated", "confused", "wants_human", "abusive", "language_switches")
        }
    totals = {
        key: sum(r[key] for r in stages)
        for key in (
            "messages", "unread", "frustrated", "confused", "wants_human", "abusive",
            "language_switches",
        )
    }  # fmt: skip
    return {
        "period": {"from": start_day.isoformat(), "to": end_day.isoformat()},
        "by_stage": stages,
        "totals": totals,
    }


def _ratio(numerator: float, denominator: float, digits: int = 6) -> float | None:
    return round(numerator / denominator, digits) if denominator else None


@router.get("/metrics/economics")
def get_economics(
    user: Operator,
    ctx: Ctx,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    environment: Literal["prod", "sim", "test"] | None = None,
    source: Literal["live", "replay"] = "live",
    dataset: str | None = None,
) -> dict[str, Any]:
    now = ctx.clock.now()
    start_day, end_day, start, end = _period(from_, to, now)
    env = environment or (ctx.settings.usage_environment if source == "live" else None)
    where, params = repo.usage_where(start, end, source=source, dataset=dataset, environment=env)
    with ctx.db() as conn:
        rows = repo.usage_cost_rows(conn, where, params)
        reached = repo.usage_reach(conn, where, params)
        where_all, params_all = repo.usage_where(
            start, end, source=source, dataset=dataset, environment=None
        )
        by_env = {
            r["environment"]: r["cost"]
            for r in repo.usage_cost_by_environment(conn, where_all, params_all)
        }
    total = sum(r["cost"] for r in rows)
    conversations = reached["conversations"] or len(
        {r["session_id"] or r["run_id"] for r in rows if r["kind"] == "run"}
    )
    inputs = sum(r["input_tokens"] for r in rows)
    cache_read = sum(r["cache_read_tokens"] for r in rows)
    runs = [r for r in rows if r["kind"] == "run"]
    errors = [r for r in rows if r["status"] != "ok"]
    mix: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        entry = mix.setdefault(
            (r["model"], r["kind"]),
            {"model": r["model"], "kind": r["kind"], "calls": 0, "cost_usd": 0.0},
        )
        entry["calls"] += 1
        entry["cost_usd"] += r["cost"]
    calls = len(rows)
    return {
        "from": start_day.isoformat(),
        "to": end_day.isoformat(),
        "conversations": conversations,
        "cost_usd": {
            "total": round(total, 6),
            "per_conversation": _ratio(total, conversations),
            "per_qualified": _ratio(total, reached["qualified"]),
            "per_interview": _ratio(total, reached["interviews"]),
        },
        "tokens": {
            "input": inputs,
            "output": sum(r["output_tokens"] for r in rows),
            "cache_read": cache_read,
            "cache_write": sum(r["cache_write_tokens"] for r in rows),
            "per_turn_input": _ratio(sum(r["input_tokens"] for r in runs), len(runs), 1),
            "cache_hit_rate": min(1.0, _ratio(cache_read, inputs, 4) or 0.0) if inputs else None,
        },
        "latency_ms": _pcts([r["latency_ms"] for r in runs if r["latency_ms"] is not None]),
        "model_mix": [
            {
                **entry,
                "share": round(entry["calls"] / calls, 4),
                "cost_usd": round(entry["cost_usd"], 6),
            }
            for entry in sorted(mix.values(), key=lambda e: -e["calls"])
        ],
        "errors": {
            "count": len(errors),
            "rate": _ratio(len(errors), calls, 4),
            "retries": 0,
        },
        "spend_by_environment": {
            name: round(by_env.get(name) or 0.0, 6) for name in ("prod", "sim", "test")
        },
    }


# ---------------------------------------------------------------------------
# Model comparison (Operators only)
# ---------------------------------------------------------------------------


class UsageComparison:
    """Comparison replay derived from the ledger's ``source = 'replay'`` rows.

    Runs, latency and cost per screening are exact. The schema has no columns for the
    other three figures, so until the dataset import (spec 004 task 10) stores them:
    ``validation_failures`` counts non-ok ledger rows, ``escalations`` is 0, and
    ``outcome_accuracy`` is the share of screenings with no non-ok row. They are proxies.
    """

    def comparison(self, conn: sqlite3.Connection, dataset: str | None) -> dict[str, Any] | None:
        if dataset is None:
            row = repo.latest_replay_dataset(conn)
            if row is None:
                return None
            dataset = row["dataset"]
        rows = repo.replay_cost_rows(conn, dataset)
        if not rows:
            return None
        by_model: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            by_model.setdefault(r["model"], []).append(r)
        models = []
        personas: set[str] = set()
        for model, group in sorted(by_model.items()):
            screenings: dict[str, bool] = {}
            for r in group:
                key = r["session_id"] or r["application_id"] or r["run_id"] or str(r["id"])
                screenings[key] = screenings.get(key, True) and r["status"] == "ok"
            personas |= set(screenings)
            runs = [r for r in group if r["kind"] == "run"]
            models.append(
                {
                    "model": model,
                    "runs": len(runs),
                    "latency_ms": _pcts(
                        [r["latency_ms"] for r in runs if r["latency_ms"] is not None]
                    ),
                    "cost_per_screening_usd": round(
                        sum(r["cost"] for r in group) / len(screenings), 6
                    ),
                    "validation_failures": sum(1 for r in group if r["status"] != "ok"),
                    "escalations": 0,
                    "outcome_accuracy": round(sum(screenings.values()) / len(screenings), 4),
                }
            )
        return {"dataset": dataset, "personas": len(personas), "models": models}


@router.get("/metrics/comparison")
def get_model_comparison(user: Operator, ctx: Ctx, dataset: str | None = None) -> dict[str, Any]:
    with ctx.db() as conn:
        payload = ctx.services.comparison.comparison(conn, dataset)
    if payload is None:
        raise not_found("No comparison dataset found.")
    return payload
