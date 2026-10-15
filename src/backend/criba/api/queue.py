"""Review queue: Approvals, Reviews, Escalations and Rejections (spec 003 task 4).

Every action runs in one IMMEDIATE transaction that first re-reads the item, so two
Recruiters resolving the same item get one success and one ``already_resolved``. Side
effects that leave this lane go through :mod:`criba.api.services`: the Rejection template
through the sender; after an Approval commits, ``submit_action(conn, id, "approved")`` hands
the router action to the scheduler (lane W2-C); other events go to ``notify`` after commit.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from criba.api import present
from criba.api.applications import decode_cursor, encode_cursor
from criba.api.auth import COOKIE_NAME, Ctx, User, hash_token
from criba.api.context import ApiContext
from criba.api.errors import conflict, not_found
from criba.api.routing import new_router
from criba.api.streams import frame, sse_response, stream
from criba.api.takeover import begin_takeover
from criba.db import repo
from criba.db.connection import iso, transaction

log = logging.getLogger("criba.api")
router = new_router(tags=["queue"])

RejectionReason = Literal["no_show", "failed_call", "role_filled", "other"]
QueueKind = Literal["approval", "review", "escalation"]


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


def _check_note(reason: str, note: str | None) -> None:
    if reason == "other" and not (note or "").strip():
        raise ValueError("A note is required when the reason is other.")


class RejectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: RejectionReason
    note: Annotated[str | None, Field(max_length=2000)] = None

    @model_validator(mode="after")
    def note_for_other(self) -> RejectionRequest:
        _check_note(self.reason, self.note)
        return self


class ApprovedDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved"]
    note: Annotated[str | None, Field(max_length=2000)] = None


class RejectedDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["rejected"]
    reason: RejectionReason
    note: Annotated[str | None, Field(max_length=2000)] = None

    @model_validator(mode="after")
    def note_for_other(self) -> RejectedDecision:
        _check_note(self.reason, self.note)
        return self


ApprovalDecision = Annotated[ApprovedDecision | RejectedDecision, Field(discriminator="decision")]


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["upheld", "overturned"]
    note: Annotated[str | None, Field(max_length=2000)] = None


class EscalationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["dismissed", "takeover"]
    note: Annotated[str | None, Field(max_length=2000)] = None


# ---------------------------------------------------------------------------
# Presenting items
# ---------------------------------------------------------------------------


def item_json(conn: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> dict[str, Any]:
    app = repo.require_application(conn, row["application_id"])
    out = present.queue_item_ref(row)
    out.update(
        {
            "application": present.application_summary(conn, app, now),
            "resolution": row["resolution"],
            "resolution_note": row["resolution_note"],
            "resolved_by": row["resolved_by"],
            "resolved_at": row["resolved_at"],
        }
    )
    return out


def _load_item(conn: sqlite3.Connection, user: Any, item_id: str, kind: str) -> sqlite3.Row:
    scope, params = present.scope_sql(user)
    row = repo.find_scoped_queue_item(conn, item_id, kind, scope, params)
    if row is None:
        raise not_found("Queue item not found.")
    if row["status"] != "open":
        raise conflict("already_resolved", "This item was already resolved.")
    return row


def _resolve(
    conn: sqlite3.Connection,
    item_id: str,
    resolution: str,
    user_id: str,
    note: str | None,
    now: datetime,
) -> None:
    if not repo.resolve_queue_item(conn, item_id, resolution, user_id, note, now):
        raise conflict("already_resolved", "This item was already resolved.")


def _reload(conn: sqlite3.Connection, item_id: str, now: datetime) -> dict[str, Any]:
    row = repo.get_queue_item_row(conn, item_id)
    return item_json(conn, row, now)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@router.get("/queue")
def list_queue(
    user: User,
    ctx: Ctx,
    kind: Annotated[list[QueueKind] | None, Query()] = None,
    status: Literal["open", "resolved"] = "open",
    location_id: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Open items oldest first; resolved items newest first (the useful end of a history)."""
    offset = decode_cursor(cursor)
    now = ctx.clock.now()
    scope, params = present.scope_sql(user)
    with ctx.db() as conn:
        rows = repo.list_queue_items(
            conn,
            scope,
            params,
            status=status,
            kind=kind,
            location_id=location_id,
            limit=limit,
            offset=offset,
        )
        items = [item_json(conn, r, now) for r in rows[:limit]]
    return {
        "items": items,
        "next_cursor": encode_cursor(offset + limit) if len(rows) > limit else None,
    }


# ---------------------------------------------------------------------------
# Resolving
# ---------------------------------------------------------------------------


def reject_in_transaction(
    conn: sqlite3.Connection,
    ctx: ApiContext,
    application_id: str,
    user_id: str,
    reason: str,
    note: str | None,
    now: datetime,
) -> None:
    """End the Application with a Rejection and send the client-approved template."""
    app = repo.require_application(conn, application_id)
    repo.close_application(
        conn,
        application_id,
        "rejection",
        rejection_reason=reason,
        rejection_note=(note or "").strip() or None,
        actor="user",
        user_id=user_id,
        now=now,
    )
    if app["control"] == "human":
        # An ended Application has no chat to hold.
        repo.clear_control(conn, application_id)
        repo.add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            kind="takeover_handed_back",
            actor="system",
            data={"reason": "application_closed", "held_by": app["control_user_id"]},
            now=now,
        )
    ctx.services.sender.send_notice(
        conn, application_id=application_id, notice="rejection", now=now
    )


@router.post("/queue/approvals/{item_id}")
def resolve_approval(item_id: str, body: ApprovalDecision, user: User, ctx: Ctx) -> dict[str, Any]:
    now = ctx.clock.now()
    with ctx.db() as conn:
        with transaction(conn):
            item = _load_item(conn, user, item_id, "approval")
            app_id = item["application_id"]
            approved = isinstance(body, ApprovedDecision)
            if isinstance(body, ApprovedDecision):
                repo.mark_approved(conn, app_id, user.id, note=body.note, now=now)
            else:
                reject_in_transaction(conn, ctx, app_id, user.id, body.reason, body.note, now)
            _resolve_if_open(conn, item_id, body, user.id, now)
            repo.touch_claim(conn, app_id, user.id, now=now)
            payload = _reload(conn, item_id, now)
        if approved:
            # After the commit: the scheduler opens its own connection (lane W2-C). The
            # Approval stands even if dispatch fails; the scheduler can pick it up again.
            try:
                ctx.services.submit_action(conn, app_id, "approved")
            except Exception:
                log.exception("submit_action failed for %s after Approval", app_id)
    if not approved:
        ctx.services.notify(app_id, "rejection")
    return payload


def _resolve_if_open(
    conn: sqlite3.Connection, item_id: str, body: Any, user_id: str, now: datetime
) -> None:
    """``mark_approved`` and ``close_application`` resolve the item themselves; make sure."""
    if repo.queue_item_status(conn, item_id) == "open":
        resolution = "approved" if isinstance(body, ApprovedDecision) else "rejected"
        _resolve(conn, item_id, resolution, user_id, body.note, now)


@router.post("/queue/reviews/{item_id}")
def resolve_review(item_id: str, body: ReviewDecision, user: User, ctx: Ctx) -> dict[str, Any]:
    now = ctx.clock.now()
    with ctx.db() as conn, transaction(conn):
        item = _load_item(conn, user, item_id, "review")
        app_id = item["application_id"]
        app = repo.require_application(conn, app_id)
        if app["status"] != "closed" or app["outcome"] != "knockout":
            raise conflict("wrong_stage", "A Review applies only to a Knockout.")
        if body.decision == "overturned":
            repo.reopen_application(
                conn, app_id, actor="user", user_id=user.id, via="review_overturned", now=now
            )
        _resolve(conn, item_id, body.decision, user.id, body.note, now)
        repo.add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=app_id,
            kind="review_overturned" if body.decision == "overturned" else "review_upheld",
            actor="user",
            user_id=user.id,
            data={"queue_item_id": item_id},
            now=now,
        )
        repo.touch_claim(conn, app_id, user.id, now=now)
        payload = _reload(conn, item_id, now)
    ctx.services.notify(app_id, f"review_{body.decision}")
    return payload


@router.post("/queue/escalations/{item_id}")
def resolve_escalation(
    item_id: str, body: EscalationDecision, user: User, ctx: Ctx
) -> dict[str, Any]:
    now = ctx.clock.now()
    with ctx.db() as conn, transaction(conn):
        item = _load_item(conn, user, item_id, "escalation")
        app_id = item["application_id"]
        if body.decision == "takeover":
            begin_takeover(conn, ctx, app_id, user.id, data={"queue_item_id": item_id})
        else:
            app = repo.require_application(conn, app_id)
            repo.add_event(
                conn,
                candidate_id=app["candidate_id"],
                application_id=app_id,
                kind="escalation_dismissed",
                actor="user",
                user_id=user.id,
                data={"queue_item_id": item_id},
                now=now,
            )
        _resolve(conn, item_id, body.decision, user.id, body.note, now)
        repo.touch_claim(conn, app_id, user.id, now=now)
        payload = _reload(conn, item_id, now)
    ctx.services.notify(
        app_id, "takeover_started" if body.decision == "takeover" else "escalation_dismissed"
    )
    return payload


@router.post("/applications/{application_id}/rejection")
def reject_application(
    application_id: str, body: RejectionRequest, user: User, ctx: Ctx
) -> dict[str, Any]:
    now = ctx.clock.now()
    with ctx.db() as conn, transaction(conn):
        present.visible_application(conn, user, application_id)
        reject_in_transaction(conn, ctx, application_id, user.id, body.reason, body.note, now)
        payload = present.application_summary(
            conn, repo.require_application(conn, application_id), now
        )
    ctx.services.notify(application_id, "rejection")
    return payload


# ---------------------------------------------------------------------------
# Queue stream
# ---------------------------------------------------------------------------


def _poll_queue(
    ctx: ApiContext, user: Any, token_hash: str, state: dict[str, Any]
) -> tuple[list[str], bool]:
    changes = _read_queue_changes(ctx, user, token_hash, state)
    if changes is None:
        return [], False
    newest, newest_event, created, resolved, rejected = changes
    out = _queue_frames(created, resolved, rejected, state["seen_resolved"])
    # Each SELECT reads its own snapshot: a Rejection committed after ``newest_event`` was read
    # can already be in ``rejected``, so move past it or the next poll announces it again.
    state["event_id"] = max([state["event_id"], newest_event, *(r["id"] for r in rejected)])
    if resolved:
        latest = resolved[-1]["resolved_at"]
        state["seen_resolved"] = {r["id"] for r in resolved if r["resolved_at"] == latest} | (
            state["seen_resolved"] if latest == state["resolved_since"] else set()
        )
        state["resolved_since"] = latest
    state["rowid"] = max([state["rowid"], newest, *(r["rid"] for r in created)])
    return out, True


def _read_queue_changes(
    ctx: ApiContext, user: Any, token_hash: str, state: dict[str, Any]
) -> tuple[int, int, list[Any], list[Any], list[Any]] | None:
    scope, scope_params = present.scope_sql(user)
    with ctx.db() as conn:
        if not repo.session_is_alive(conn, token_hash, ctx.session_clock.now()):
            return None
        # Read the high-water mark first: rows inserted after this point are picked up (and
        # their rowids kept) by the query below or by the next poll, never skipped.
        newest = repo.max_queue_item_rowid(conn)
        newest_event = repo.max_event_id(conn)
        created = repo.queue_items_created_after(conn, state["rowid"], scope, scope_params)
        resolved = repo.queue_items_resolved_since(
            conn, state["resolved_since"], scope, scope_params
        )
        # A Rejection made outside the queue (the Application's own page) opens and resolves no
        # item. One made from an Approval resolves it, and the resolved frame above covers that.
        rejected = repo.direct_rejections_after(conn, state["event_id"], scope, scope_params)
    return newest, newest_event, created, resolved, rejected


def _queue_frames(
    created: list[Any], resolved: list[Any], rejected: list[Any], seen_resolved: set[str]
) -> list[str]:
    out: list[str] = []
    for r in created:
        out.append(
            frame(
                "queue",
                r["created_at"],
                {
                    "item_id": r["id"],
                    "kind": r["kind"],
                    "status": r["status"],
                    "application_id": r["application_id"],
                },
                f"q-{r['rid']}",
            )
        )
    for r in resolved:
        if r["id"] in seen_resolved:
            continue
        out.append(
            frame(
                "queue",
                r["resolved_at"],
                {
                    "item_id": r["id"],
                    "kind": r["kind"],
                    "status": "resolved",
                    "application_id": r["application_id"],
                    "resolution": r["resolution"],
                },
            )
        )
    for r in rejected:
        out.append(
            frame(
                "queue",
                r["created_at"],
                {"kind": "rejection", "status": "closed", "application_id": r["application_id"]},
            )
        )
    return out


_QUEUE_EVENT_ID = re.compile(r"q-[0-9]{1,19}")


@router.get("/queue/stream")
def stream_queue(
    request: Request,
    user: User,
    ctx: Ctx,
    last_event_id: Annotated[str | None, Header()] = None,
):
    with ctx.db() as conn:
        newest = repo.max_queue_item_rowid(conn)
        newest_event = repo.max_event_id(conn)
    rowid = newest
    # A Last-Event-ID this stream did not write (not ``q-`` and ASCII digits) is ignored, as an
    # SSE resume id the server does not know: the stream starts from now.
    if last_event_id and _QUEUE_EVENT_ID.fullmatch(last_event_id):
        rowid = min(int(last_event_id[2:]), newest)
    state: dict[str, Any] = {
        "rowid": rowid,
        "event_id": newest_event,
        "resolved_since": iso(ctx.clock.now()),
        "seen_resolved": set(),
    }
    token_hash = hash_token(request.cookies.get(COOKIE_NAME, ""))
    settings = ctx.settings
    return sse_response(
        stream(
            request,
            poll=lambda: _poll_queue(ctx, user, token_hash, state),
            poll_seconds=settings.sse_poll_seconds,
            max_seconds=settings.sse_max_seconds,
            heartbeat_seconds=settings.sse_heartbeat_seconds,
        )
    )
