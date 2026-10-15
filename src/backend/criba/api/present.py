"""Row-to-JSON presenters and the Location-scope helpers shared by the routers.

Everything here returns plain dicts shaped like ``contracts/api.openapi.yaml``. Cost
data is built only in :func:`cost_of`, which callers invoke for Operators.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from criba.api.auth import CurrentUser
from criba.api.errors import not_found
from criba.db import repo
from criba.db.connection import iso, parse_iso
from criba.jev import store as jev_store
from criba.summary import FLAG_ORDER

_OUTCOME_EVENTS = frozenset({"knockout", "rejection", "abandoned", "withdrawn"})
SCREENING_STAGES = repo.SCREENING_STAGES


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def scope_sql(user: CurrentUser, alias: str = "a") -> tuple[str, list[Any]]:
    """SQL fragment limiting ``applications`` rows to the user's scope (ADR 0021).

    Operators see everything. A Recruiter sees an Application when any covering Location
    is in their scope.
    """
    if user.is_operator:
        return "1 = 1", []
    return (
        f"({alias}.client_id = ? AND EXISTS (SELECT 1 FROM application_locations al"
        " JOIN user_locations ul ON ul.location_id = al.location_id"
        f" WHERE al.application_id = {alias}.id AND ul.user_id = ?))",
        [user.client_id, user.id],
    )


def visible_application(
    conn: sqlite3.Connection, user: CurrentUser, application_id: str
) -> dict[str, Any]:
    """The Application, or a 404 when it does not exist or is out of the user's scope."""
    app = repo.get_application(conn, application_id)
    if app is None or not repo.user_can_access(conn, user.id, application_id):
        raise not_found("Application not found.")
    return app


def slot_in_scope_sql(user: CurrentUser, alias: str = "s") -> tuple[str, list[Any]]:
    if user.is_operator:
        return "1 = 1", []
    if not user.location_ids:
        return "0 = 1", []
    return (
        f"{alias}.location_id IN ({','.join('?' * len(user.location_ids))})",
        list(user.location_ids),
    )


# ---------------------------------------------------------------------------
# Small objects
# ---------------------------------------------------------------------------


def user_name(conn: sqlite3.Connection, user_id: str | None) -> str | None:
    if user_id is None:
        return None
    return repo.user_name(conn, user_id)


def candidate_ref(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": candidate["id"],
        "full_name": candidate["full_name"],
        "phone": candidate["phone"],
        "country": candidate["country"],
        "language": candidate["language"],
        "opted_out": bool(candidate["opted_out"]),
    }


def zone_ref(conn: sqlite3.Connection, zone_id: str | None) -> dict[str, Any] | None:
    if zone_id is None:
        return None
    row = repo.zone_row(conn, zone_id)
    return dict(row) if row else None


def location_refs_of(conn: sqlite3.Connection, application_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in repo.application_location_rows(conn, application_id)]


def claim_idle(app: dict[str, Any], now: datetime) -> bool:
    if app["claim_user_id"] is None:
        return False
    last = app["claim_last_action_at"] or app["claimed_at"]
    return bool(app["claimed_idle"]) or now - parse_iso(last) >= repo.CLAIM_IDLE_AFTER


def claim_json(
    conn: sqlite3.Connection, app: dict[str, Any], now: datetime
) -> dict[str, Any] | None:
    if app["claim_user_id"] is None:
        return None
    return {
        "user_id": app["claim_user_id"],
        "user_name": user_name(conn, app["claim_user_id"]) or "",
        "claimed_at": app["claimed_at"],
        "last_action_at": app["claim_last_action_at"] or app["claimed_at"],
        "idle": claim_idle(app, now),
    }


def takeover_state(conn: sqlite3.Connection, app: dict[str, Any]) -> dict[str, Any]:
    human = app["control"] == "human"
    return {
        "control": app["control"],
        "user_id": app["control_user_id"] if human else None,
        "user_name": user_name(conn, app["control_user_id"]) if human else None,
        "since": app["control_since"] or app["updated_at"],
    }


def open_queue_kinds(conn: sqlite3.Connection, application_id: str) -> set[str]:
    return repo.open_queue_kinds(conn, application_id)


def flags_of(
    conn: sqlite3.Connection, app: dict[str, Any], candidate: dict[str, Any], now: datetime
) -> list[str]:
    kinds = open_queue_kinds(conn, app["id"])
    is_open = app["status"] == "open"
    active = {
        "review_requested": "review" in kinds,
        "escalation": "escalation" in kinds,
        "stalled": is_open and app["stalled_since"] is not None,
        "takeover": app["control"] == "human",
        "qualified_waiting": bool(app["qualified_waiting"]),
        "claimed_idle": claim_idle(app, now),
        "start_date_far": bool(app["start_date_far"]),
        "opted_out": bool(candidate["opted_out"]),
    }
    return [f for f in FLAG_ORDER if active[f]]


# ---------------------------------------------------------------------------
# Time per stage
# ---------------------------------------------------------------------------


def stage_timing(
    app: dict[str, Any], events: list[dict[str, Any]], now: datetime
) -> tuple[dict[str, int], int, int]:
    """``(stage_durations_seconds, time_in_stage_seconds, total_seconds)`` from the timeline.

    Walks ``stage_changed`` events; an Outcome event closes the running interval and a
    ``reopened`` event starts a new one (the time while closed is not counted).
    """
    created = parse_iso(app["created_at"])
    end = parse_iso(app["closed_at"]) if app["closed_at"] else now
    stage = "greeting"
    since: datetime | None = created
    durations: dict[str, float] = {}

    def close(at: datetime) -> None:
        nonlocal since
        if since is not None:
            durations[stage] = durations.get(stage, 0.0) + max(0.0, (at - since).total_seconds())
            since = None

    for ev in events:
        at = parse_iso(ev["created_at"])
        kind = ev["kind"]
        if kind == "stage_changed" and ev["data"].get("to") in repo.STAGES:
            close(at)
            stage, since = ev["data"]["to"], at
        elif kind in _OUTCOME_EVENTS:
            close(at)
        elif kind == "reopened":
            criterion = ev["data"].get("criterion")
            if criterion in ("zone", "driver_license"):
                stage = criterion
            since = at
    in_stage = 0
    if since is not None:
        in_stage = int(max(0.0, (end - since).total_seconds()))
        close(end)
    if app["status"] == "closed":
        in_stage = 0
    total = int(max(0.0, (end - created).total_seconds()))
    return {k: int(v) for k, v in durations.items()}, in_stage, total


def application_summary(
    conn: sqlite3.Connection,
    app: dict[str, Any],
    now: datetime,
    *,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    candidate = repo.get_candidate(conn, app["candidate_id"])
    assert candidate is not None
    if events is None:
        events = repo.list_events(conn, app["id"])
    durations, in_stage, total = stage_timing(app, events, now)
    human = app["control"] == "human"
    return {
        "id": app["id"],
        "candidate": candidate_ref(candidate),
        "channel": app["channel"],
        "status": app["status"],
        "stage": app["stage"],
        "outcome": app["outcome"],
        "knockout_criterion": app["knockout_criterion"],
        "rejection_reason": app["rejection_reason"],
        "rejection_note": app["rejection_note"],
        "zone": zone_ref(conn, app["zone_id"]),
        "locations": location_refs_of(conn, app["id"]),
        "claim": claim_json(conn, app, now),
        "control": app["control"],
        "control_user_id": app["control_user_id"] if human else None,
        "control_user_name": user_name(conn, app["control_user_id"]) if human else None,
        "flags": flags_of(conn, app, candidate, now),
        "created_at": app["created_at"],
        "updated_at": app["updated_at"],
        "closed_at": app["closed_at"],
        "time_in_stage_seconds": in_stage,
        "total_seconds": total,
        "stage_durations_seconds": durations,
    }


# ---------------------------------------------------------------------------
# Detail parts
# ---------------------------------------------------------------------------


def answers_of(conn: sqlite3.Connection, app: dict[str, Any]) -> dict[str, Any]:
    return {
        "full_name": app["full_name"],
        "zone": zone_ref(conn, app["zone_id"]),
        "driver_license": app["driver_license"],
        "availability": app["availability"],
        "preferred_schedule": app["preferred_schedule"],
        "experience_years": app["experience_years"],
        "experience_platforms": app["experience_platforms"],
        "start_date": app["start_date"],
    }


def message_json(row: dict[str, Any], names: dict[str, str | None]) -> dict[str, Any]:
    uid = row.get("sender_user_id")
    return {
        "id": row["id"],
        "direction": row["direction"],
        "sender": row["sender"],
        "sender_name": names.get(uid) if uid else None,
        "content_type": row["content_type"],
        "body": row["body"],
        "interactive": row["interactive"],
        "reply_option_id": row["reply_option_id"],
        "template_name": row["template_name"],
        "media_type": row["media_type"],
        "created_at": row["created_at"],
        "modality": row["modality"],
    }


def message_with_name(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    uid = row.get("sender_user_id")
    return message_json(row, {uid: user_name(conn, uid)} if uid else {})


# Events hold only ids, codes and canonical values, never Candidate free text (Q38): the
# words stay in ``messages``, queue items and ``handbacks``, so an Erasure request never has
# to scrub the event log. The writers no longer store these keys and migration 0005 removed
# them from old rows; stripping them here as well is defence in depth.
_FREE_TEXT_KEYS = {"raw_input"}
_FREE_TEXT_BY_KIND = {
    "escalation_raised": {"note"},
    "review_requested": {"note"},
    "approval_requested": {"note"},
    "withdrawn": {"note"},
    "erasure_requested": {"note"},
    "knockout": {"message"},
    "takeover_handed_back": {"summary"},
}


def event_json(row: dict[str, Any], names: dict[str, str | None]) -> dict[str, Any]:
    drop = _FREE_TEXT_KEYS | _FREE_TEXT_BY_KIND.get(row["kind"], set())
    data = {k: v for k, v in row["data"].items() if k not in drop}
    uid = row.get("user_id")
    return {
        "id": row["id"],
        "kind": row["kind"],
        "actor": row["actor"],
        "user_name": names.get(uid) if uid else None,
        "data": data,
        "created_at": row["created_at"],
    }


def event_with_name(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    uid = row.get("user_id")
    return event_json(row, {uid: user_name(conn, uid)} if uid else {})


def names_for(conn: sqlite3.Connection, user_ids: set[str]) -> dict[str, str | None]:
    return {uid: user_name(conn, uid) for uid in user_ids}


def answer_status(app: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per answer field: its status, the Candidate's words and attempts."""
    last_reopen = max((e["id"] for e in events if e["kind"] == "reopened"), default=0)
    recorded: dict[str, dict[str, Any]] = {}
    misses: dict[str, int] = {}
    for ev in events:
        field_name = ev["data"].get("field")
        if field_name not in repo.ANSWER_FIELDS:
            continue
        if ev["kind"] == "answer_recorded":
            recorded[field_name] = ev
        elif ev["kind"] in ("answer_invalid", "answer_ambiguous") and ev["id"] > last_reopen:
            misses[field_name] = misses.get(field_name, 0) + 1
    raw_columns = {"zone": app["zone_input"], "driver_license": app["driver_license_input"]}
    out = []
    for name in repo.ANSWER_FIELDS:
        value = app["zone_id"] if name == "zone" else app[name]
        has_value = value is not None
        entry = recorded.get(name)
        note = None
        if app["outcome"] == "knockout" and app["knockout_criterion"] == name:
            # A Zone Knockout for a place that matched no Zone has no Zone value (ADR 0068).
            status = "knockout"
        elif has_value:
            status = "valid"
            if name == "start_date" and app["start_date_far"]:
                status, note = "flagged", "more than 60 days out"
        else:
            status = "invalid" if misses.get(name) else "pending"
        # The Candidate's words are kept for zone and driver_license only, on the
        # Application (Q38: never in events).
        raw = raw_columns.get(name)
        out.append(
            {
                "field": name,
                "status": status,
                "raw_input": raw,
                "attempts": misses.get(name, 0) + (1 if has_value else 0),
                "recorded_at": entry["created_at"] if entry and has_value else None,
                "note": note,
            }
        )
    return out


def interview_of(conn: sqlite3.Connection, app: dict[str, Any]) -> dict[str, Any] | None:
    """The Application's active Interview booking, as the contract's ``Interview``."""
    row = repo.interview_booking_row(conn, app["id"])
    if row is None:
        return None
    return {
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "local_label": repo.local_label(row["starts_at"], row["timezone"], app["language"]),
        "location_name": row["location_name"],
        "address": row["address"],
    }


def summary_json(app: dict[str, Any]) -> dict[str, Any] | None:
    if app["summary_at"] is None:
        return None
    return {
        "structured": app["summary_structured"] or {},
        "prose": app["summary_prose"],
        "written_at": app["summary_at"],
    }


def message_signals_json(conn: sqlite3.Connection, application_id: str) -> list[dict[str, Any]]:
    """The messages a Jev signal fired on, as flags (ADR 0051): the message id, the stage it
    arrived at and which signals fired. Never a probability, and nothing for a message that
    read as calm or that Jev could not read."""
    return [
        {
            "message_id": row["message_id"],
            "stage": row["stage"],
            "at": row["message_at"],
            "fired": row["fired"],
            "language_changed": row["language_set"] is not None and not row["first_message"],
            "escalated": row["escalation"] is not None,
        }
        for row in jev_store.list_signals(conn, application_id)
        if row["status"] == "ok"
        and (row["fired"] or (row["language_set"] is not None and not row["first_message"]))
    ]


# What a Recruiter may see of Jev's reading of a message (ADR 0075): labels, never a score.
# Left out because they would read as a judgement of the person, not of the message:
# the `abusive` signal and the `steering` intent (an attempt to give the assistant orders).
LABEL_SIGNALS = ("frustrated", "confused", "wants_human")
LABEL_INTENTS = ("answering", "asking", "off_topic", "wants_to_stop", "other")


def message_labels_json(
    conn: sqlite3.Connection, application_id: str, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Jev's labels for each typed Candidate message it read (ADR 0075), for every role: the
    detected language, the signals that fired, the intent as a code and the tone hint applied.
    Built field by field from the stored row's code columns: no probability, confidence,
    threshold, Manipulation score, model or policy version, and no text. ``messages`` are the
    Application's messages; a spoken turn (ADR 0065) or a failed read gets no label."""
    typed = {m["id"] for m in messages if m["modality"] == "typed"}
    return [
        {
            "message_id": row["message_id"],
            "stage": row["stage"],
            "at": row["message_at"],
            "language": row["lang"],
            "fired": [s for s in row["fired"] if s in LABEL_SIGNALS],
            "intent": row["intent"] if row["intent"] in LABEL_INTENTS else None,
            "tone_hint": row["hint"],
            "language_set": row["language_set"],
            "language_reason": row["language_reason"] if row["language_set"] else None,
        }
        for row in jev_store.list_signals(conn, application_id)
        if row["status"] == "ok" and row["message_id"] in typed
    ]


def queue_item_ref(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "reason": row["reason"],
        "note": row["note"],
        "created_at": row["created_at"],
    }


def other_applications(conn: sqlite3.Connection, app: dict[str, Any]) -> list[dict[str, Any]]:
    rows = repo.other_application_rows(conn, app["candidate_id"], app["id"])
    return [dict(r) for r in rows]


def cost_of(conn: sqlite3.Connection, application_id: str) -> dict[str, Any]:
    """Operator-only usage totals for one Application (ADR 0019)."""
    rows = repo.live_usage_rows(conn, application_id)
    latencies = sorted(r["latency_ms"] for r in rows if r["latency_ms"] is not None)
    reconciled = [r["reconciled_cost_usd"] for r in rows if r["reconciled_cost_usd"] is not None]
    return {
        "est_cost_usd": round(sum(r["est_cost_usd"] for r in rows), 6),
        "reconciled_cost_usd": round(sum(reconciled), 6) if reconciled else None,
        "runs": sum(1 for r in rows if r["kind"] == "run"),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "cache_read_tokens": sum(r["cache_read_tokens"] for r in rows),
        "cache_write_tokens": sum(r["cache_write_tokens"] for r in rows),
        "p50_latency_ms": int(percentile(latencies, 50)) if latencies else None,
        "models": sorted({r["model"] for r in rows}),
        "by_model": _by_model(rows),
    }


def _by_model(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Usage split by the model that served each call, so a spend guard prices each model at its
    own rates. Rows that carry OpenRouter's billed cost (`reconciled_cost_usd`) are grouped apart
    from those that do not: a group with `reconciled_cost_usd` is a bill, one without is tokens
    to be priced."""
    groups: dict[tuple[str, bool], dict[str, Any]] = {}
    for r in rows:
        billed = r["reconciled_cost_usd"] is not None
        g = groups.setdefault(
            (r["model"], billed),
            {
                "model": r["model"],
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "est_cost_usd": 0.0,
                "reconciled_cost_usd": 0.0 if billed else None,
            },
        )
        g["calls"] += 1
        for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            g[key] += r[key]
        g["est_cost_usd"] += r["est_cost_usd"]
        if billed:
            g["reconciled_cost_usd"] += r["reconciled_cost_usd"]
    out = sorted(groups.values(), key=lambda g: (g["model"], g["reconciled_cost_usd"] is None))
    for g in out:
        g["est_cost_usd"] = round(g["est_cost_usd"], 6)
        if g["reconciled_cost_usd"] is not None:
            g["reconciled_cost_usd"] = round(g["reconciled_cost_usd"], 6)
    return out


def percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolated percentile of an ascending list (non-empty)."""
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * pct / 100
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (rank - low)


def now_iso(now: datetime) -> str:
    return iso(now)
