"""The Operator's agent tool-call trace (``GET /api/applications/{id}/tool-calls``, ADR 0069).

Runs are the Application's ``usage`` rows of kind ``run``, plus any run id its ``tool_calls`` hold
that has no such row (a run whose usage write failed). Each run carries its tokens, cost, latency
and status from that row and the ids of the agent replies that carry its run id. The calls no run
claimed form a last group with a null ``run_id``. Only ids, codes, times, numbers and the stored
canonical arguments are returned: never a tool's result, a hint or message text. Each run also
carries its usage time, profile and model, and the response lists the Application's Jev calls from
the usage ledger (time, model, latency, status), for the Demo stage's Agent activity (ADR 0075).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from criba.api import present
from criba.api.auth import Ctx, Operator
from criba.api.routing import new_router
from criba.db import repo

router = new_router(tags=["applications"])


def _call(row: sqlite3.Row) -> dict[str, Any]:
    try:
        args = json.loads(row["args"])
    except ValueError:
        args = {}
    return {
        "tool": row["tool"],
        "started_at": row["started_at"],
        "duration_ms": row["duration_ms"],
        "outcome": row["outcome"],
        "error": row["error"],
        "args": args if isinstance(args, dict) else {},
    }


def _run(
    run_id: str | None,
    usage: sqlite3.Row | None,
    calls: list[sqlite3.Row],
    replies: list[int],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": usage["status"] if usage else None,
        "input_tokens": usage["input_tokens"] if usage else None,
        "output_tokens": usage["output_tokens"] if usage else None,
        "cost_usd": usage["est_cost_usd"] if usage else None,
        "latency_ms": usage["latency_ms"] if usage else None,
        "at": usage["ts"] if usage else None,
        "hermes_profile": usage["profile"] if usage else None,
        "model": usage["model"] if usage else None,
        "reply_message_ids": replies,
        "calls": [_call(c) for c in calls],
    }


@router.get("/applications/{application_id}/tool-calls")
def get_tool_calls(application_id: str, user: Operator, ctx: Ctx) -> dict[str, Any]:
    with ctx.db() as conn:
        present.visible_application(conn, user, application_id)
        usage = {r["run_id"]: r for r in repo.run_usage_rows(conn, application_id)}
        calls = repo.list_tool_calls(conn, application_id)
        jev_calls = [
            {
                "at": r["ts"],
                "model": r["model"],
                "latency_ms": r["latency_ms"],
                "status": r["status"],
            }
            for r in repo.jev_usage_rows(conn, application_id)
        ]
        replies: dict[str, list[int]] = {}
        for r in repo.run_reply_ids(conn, application_id):
            replies.setdefault(r["run_id"], []).append(r["id"])

    by_run: dict[str, list[sqlite3.Row]] = {}
    unlinked: list[sqlite3.Row] = []
    for row in calls:
        (by_run.setdefault(row["run_id"], []) if row["run_id"] else unlinked).append(row)

    # Oldest first by the earlier of the run's usage time and its first call, then by id (a run
    # with a usage row before one without, when they tie).
    keyed: list[tuple[tuple[str, int, int], dict[str, Any]]] = []
    for run_id in usage.keys() | by_run.keys():
        ran, run_calls = usage.get(run_id), by_run.get(run_id, [])
        times = [run_calls[0]["started_at"]] if run_calls else []
        times += [ran["ts"]] if ran else []
        key = (min(times), 0, ran["id"]) if ran else (min(times), 1, run_calls[0]["id"])
        keyed.append((key, _run(run_id, ran, run_calls, replies.get(run_id, []))))
    runs = [run for _, run in sorted(keyed, key=lambda pair: pair[0])]
    if unlinked:
        runs.append(_run(None, None, unlinked, []))
    return {"application_id": application_id, "runs": runs, "jev_calls": jev_calls}
