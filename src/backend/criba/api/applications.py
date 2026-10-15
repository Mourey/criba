"""Applications: list and filter, detail, JSON export (spec 003 task 2)."""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import Query, Response

from criba.api import present
from criba.api.auth import Ctx, User
from criba.api.errors import validation_error
from criba.api.routing import new_router
from criba.db import repo
from criba.db.connection import iso
from criba.jev.notes import communication_notes

router = new_router(tags=["applications"])

Stage = Literal[
    "greeting", "name", "zone", "driver_license", "availability", "preferred_schedule",
    "experience", "start_date", "confirm", "qualified", "approved", "interview_booked",
]  # fmt: skip
Outcome = Literal["knockout", "rejection", "abandoned", "withdrawn"]
Flag = Literal[
    "review_requested", "escalation", "stalled", "takeover", "qualified_waiting",
    "claimed_idle", "start_date_far", "opted_out",
]  # fmt: skip
Sort = Literal["-updated_at", "updated_at", "-created_at", "created_at", "-time_in_stage"]

_STAGE_ENTERED = (
    "COALESCE((SELECT MAX(e.created_at) FROM events e WHERE e.application_id = a.id"
    " AND e.kind IN ('stage_changed', 'reopened')), a.created_at)"
)
_ORDER = {
    "-updated_at": "a.updated_at DESC, a.id DESC",
    "updated_at": "a.updated_at ASC, a.id ASC",
    "-created_at": "a.created_at DESC, a.id DESC",
    "created_at": "a.created_at ASC, a.id ASC",
    # Longest in the current stage first; closed Applications (no stage clock) last.
    "-time_in_stage": f"(a.status = 'open') DESC, {_STAGE_ENTERED} ASC, a.id ASC",
}


# SQLite binds OFFSET as a signed 64-bit integer; a larger one cannot be sent.
MAX_OFFSET = 2**63 - 1


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        offset = int(json.loads(base64.urlsafe_b64decode(padded))["o"])
    except Exception as exc:
        raise validation_error("Invalid cursor.", "cursor") from exc
    if not 0 <= offset <= MAX_OFFSET:
        raise validation_error("Invalid cursor.", "cursor")
    return offset


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _flag_sql(flag: str, now: datetime) -> tuple[str, list[Any]]:
    idle_before = iso(now - repo.CLAIM_IDLE_AFTER)
    open_item = (
        "EXISTS (SELECT 1 FROM queue_items qi WHERE qi.application_id = a.id"
        " AND qi.status = 'open' AND qi.kind = ?)"
    )
    table: dict[str, tuple[str, list[Any]]] = {
        "review_requested": (open_item, ["review"]),
        "escalation": (open_item, ["escalation"]),
        "stalled": ("(a.status = 'open' AND a.stalled_since IS NOT NULL)", []),
        "takeover": ("a.control = 'human'", []),
        "qualified_waiting": ("a.qualified_waiting = 1", []),
        "claimed_idle": (
            "(a.claim_user_id IS NOT NULL AND (a.claimed_idle = 1"
            " OR COALESCE(a.claim_last_action_at, a.claimed_at) <= ?))",
            [idle_before],
        ),
        "start_date_far": ("a.start_date_far = 1", []),
        "opted_out": ("c.opted_out = 1", []),
    }
    return table[flag]


def build_filters(
    user: Any,
    now: datetime,
    *,
    location_id: str | None = None,
    country: str | None = None,
    status: str | None = None,
    stages: Sequence[str] | None = None,
    outcomes: Sequence[str] | None = None,
    flags: Sequence[str] | None = None,
    claimed_by: str | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
    q: str | None = None,
) -> tuple[str, list[Any]]:
    scope, params = present.scope_sql(user)
    where = [scope]
    if location_id:
        where.append(
            "EXISTS (SELECT 1 FROM application_locations f WHERE f.application_id = a.id"
            " AND f.location_id = ?)"
        )
        params.append(location_id)
    if country:
        where.append("a.country = ?")
        params.append(country)
    if status:
        where.append("a.status = ?")
        params.append(status)
    if stages:
        where.append(f"a.stage IN ({','.join('?' * len(stages))})")
        params += stages
    if outcomes:
        where.append(f"a.outcome IN ({','.join('?' * len(outcomes))})")
        params += outcomes
    if flags:
        parts = [_flag_sql(f, now) for f in flags]
        where.append("(" + " OR ".join(p[0] for p in parts) + ")")
        for _, p in parts:
            params += p
    if claimed_by == "none":
        where.append("a.claim_user_id IS NULL")
    elif claimed_by:
        where.append("a.claim_user_id = ?")
        params.append(user.id if claimed_by == "me" else claimed_by)
    if created_from:
        where.append("a.created_at >= ?")
        params.append(f"{created_from.isoformat()}T00:00:00Z")
    if created_to == date.max:  # the bound is the day after it
        raise validation_error("created_to is too late.", "created_to")
    if created_to:
        where.append("a.created_at < ?")
        params.append(f"{(created_to + timedelta(days=1)).isoformat()}T00:00:00Z")
    if q and q.strip():
        pattern = _like(q.strip())
        where.append(
            "(c.full_name LIKE ? ESCAPE '\\' OR a.full_name LIKE ? ESCAPE '\\'"
            " OR c.phone LIKE ? ESCAPE '\\')"
        )
        params += [pattern, pattern, pattern]
    return " AND ".join(where), params


@router.get("/applications")
def list_applications(
    user: User,
    ctx: Ctx,
    location_id: str | None = None,
    country: Annotated[Literal["ES", "MX"] | None, Query()] = None,
    status: Annotated[Literal["open", "closed"] | None, Query()] = None,
    stage: Annotated[list[Stage] | None, Query()] = None,
    outcome: Annotated[list[Outcome] | None, Query()] = None,
    flag: Annotated[list[Flag] | None, Query()] = None,
    claimed_by: str | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
    q: str | None = None,
    sort: Sort = "-updated_at",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    offset = decode_cursor(cursor)
    now = ctx.clock.now()
    where, params = build_filters(
        user,
        now,
        location_id=location_id,
        country=country,
        status=status,
        stages=stage,
        outcomes=outcome,
        flags=flag,
        claimed_by=claimed_by,
        created_from=created_from,
        created_to=created_to,
        q=q,
    )
    base = f"FROM applications a JOIN candidates c ON c.id = a.candidate_id WHERE {where}"
    with ctx.db() as conn:
        total = repo.count_applications(conn, base, params)
        rows = repo.application_ids_page(conn, base, params, _ORDER[sort], limit + 1, offset)
        has_more = len(rows) > limit
        items = [
            present.application_summary(conn, repo.require_application(conn, r["id"]), now)
            for r in rows[:limit]
        ]
    return {
        "items": items,
        "next_cursor": encode_cursor(offset + limit) if has_more else None,
        "total": total,
    }


@router.get("/applications/{application_id}")
def get_application(application_id: str, user: User, ctx: Ctx) -> dict[str, Any]:
    now = ctx.clock.now()
    with ctx.db() as conn:
        app = present.visible_application(conn, user, application_id)
        events = repo.list_events(conn, application_id)
        messages = repo.list_messages(conn, application_id)
        names = present.names_for(
            conn,
            {m["sender_user_id"] for m in messages if m["sender_user_id"]}
            | {e["user_id"] for e in events if e["user_id"]},
        )
        queue_rows = repo.open_queue_item_rows(conn, application_id)
        detail = present.application_summary(conn, app, now, events=events)
        detail.update(
            {
                "answers": present.answers_of(conn, app),
                "answer_status": present.answer_status(app, events),
                "messages": [present.message_json(m, names) for m in messages],
                "events": [present.event_json(e, names) for e in events],
                "summary": present.summary_json(app),
                "files": [],
                "other_applications": present.other_applications(conn, app),
                "open_queue_items": [present.queue_item_ref(r) for r in queue_rows],
                "interview": present.interview_of(conn, app),
                # Conversation context, not an assessment (ADR 0051): which messages a signal
                # fired on, as flags, and the notes derived from them. No probabilities.
                "message_signals": present.message_signals_json(conn, application_id),
                "communication_notes": communication_notes(conn, app),
                # Jev's labels per typed message, the same for every role (ADR 0075): codes,
                # never a score.
                "message_labels": present.message_labels_json(conn, application_id, messages),
            }
        )
        if user.is_operator:
            detail["cost"] = present.cost_of(conn, application_id)
    return detail


@router.get("/applications/{application_id}/export")
def export_application(
    application_id: str, user: User, ctx: Ctx, response: Response
) -> dict[str, Any]:
    with ctx.db() as conn:
        present.visible_application(conn, user, application_id)
        payload = repo.export_application(conn, application_id)
    response.headers["Content-Disposition"] = f'attachment; filename="{application_id}.json"'
    return payload
