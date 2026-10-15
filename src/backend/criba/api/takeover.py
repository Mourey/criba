"""Takeover and hand-back, plus the live relay stream (spec 003 task 5, ADR 0020).

While a person holds the chat (``control = human``) the router starts no agent runs and
inbound messages go only to the panel. The lock is a conditional ``UPDATE`` on
``control``: two people racing for the same chat cannot both win. Starting a Takeover here also
resolves an open ``unreadable`` Escalation (ADR 0063), which held the agent until a Recruiter
acted, so the hand-back gives the chat to the agent.

The relay stream emits ``message`` (inbound and outbound), ``control`` (Takeover started
or handed back) and ``event`` (timeline entries). Ids are ``m-<message id>`` and
``e-<event id>``; on reconnect the other cursor is recovered from the timestamp of the
one given, replaying a little rather than losing anything (the client de-duplicates by id).
"""

from __future__ import annotations

import re
import sqlite3
from typing import Annotated, Any

from fastapi import Header, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from criba.api import present
from criba.api.auth import COOKIE_NAME, Ctx, User, hash_token
from criba.api.context import ApiContext
from criba.api.errors import conflict, forbidden
from criba.api.routing import new_router
from criba.api.streams import frame, sse_response, stream
from criba.db import repo
from criba.db.connection import iso, transaction

router = new_router(tags=["takeover"])

_SUMMARY_LIMIT = 2000
_LINE_LIMIT = 240


class TakeoverMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Annotated[str, Field(min_length=1, max_length=4000)]

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class HandBackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: Annotated[str | None, Field(max_length=2000)] = None


def begin_takeover(
    conn: sqlite3.Connection,
    ctx: ApiContext,
    application_id: str,
    user_id: str,
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a Takeover for ``user_id`` inside the caller's transaction.

    Idempotent for the current holder; ``control_held`` for anyone else.
    """
    now = ctx.clock.now()
    ts = iso(now)
    with transaction(conn):
        app = repo.require_application(conn, application_id)
        if app["status"] != "open":
            raise conflict("application_closed", "The Application is closed.")
        if app["control"] == "human":
            if app["control_user_id"] == user_id:
                return present.takeover_state(conn, app)
            holder = present.user_name(conn, app["control_user_id"]) or "Another team member"
            raise conflict("control_held", f"{holder} already holds the chat.")
        if not repo.take_control(conn, application_id, user_id, ts):
            raise conflict("control_held", "Another team member already holds the chat.")
        repo.add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            kind="takeover_started",
            actor="user",
            user_id=user_id,
            data=data or {},
            now=now,
        )
        # The person answers now: no owed turn is retried into their conversation (ADR 0073).
        repo.drop_owed_turns(conn, application_id)
        ctx.services.sender.send_notice(
            conn, application_id=application_id, notice="takeover_joined", now=now
        )
        repo.touch_claim(conn, application_id, user_id, now=now)
        return present.takeover_state(conn, repo.require_application(conn, application_id))


def generated_handback_summary(
    conn: sqlite3.Connection, application_id: str, since: str | None
) -> str:
    """A plain recap of the Takeover from the transcript (no model call)."""
    rows = repo.transcript_since(conn, application_id, since or "")
    if not rows:
        return "No messages were exchanged during the Takeover."
    lines = []
    for r in rows:
        who = "Candidate" if r["sender"] == "candidate" else "Team member"
        body = " ".join(r["body"].split())
        if len(body) > _LINE_LIMIT:
            body = body[: _LINE_LIMIT - 1] + "…"
        lines.append(f"{who}: {body}")
    text = "Exchanged during the Takeover. " + " | ".join(lines)
    return text if len(text) <= _SUMMARY_LIMIT else text[: _SUMMARY_LIMIT - 1] + "…"


@router.post("/applications/{application_id}/takeover")
def start_takeover(application_id: str, user: User, ctx: Ctx) -> dict[str, Any]:
    with ctx.db() as conn, transaction(conn):
        present.visible_application(conn, user, application_id)
        state = begin_takeover(conn, ctx, application_id, user.id)
        # A Takeover answers an open `unreadable` Escalation (ADR 0063): resolved as taken
        # over, so the hand-back gives the conversation to the agent.
        repo.resolve_hold(conn, application_id, user_id=user.id, now=ctx.clock.now())
    ctx.services.notify(application_id, "takeover_started")
    return state


@router.post("/applications/{application_id}/takeover/messages", status_code=201)
def send_takeover_message(
    application_id: str, body: TakeoverMessageRequest, user: User, ctx: Ctx
) -> dict[str, Any]:
    now = ctx.clock.now()
    with ctx.db() as conn, transaction(conn):
        app = present.visible_application(conn, user, application_id)
        if (
            app["status"] != "open"
            or app["control"] != "human"
            or app["control_user_id"] != user.id
        ):
            raise forbidden("Start a Takeover before writing to the Candidate.", "not_in_control")
        message = ctx.services.sender.send_human_text(
            conn, application_id=application_id, user_id=user.id, text=body.text.strip(), now=now
        )
        repo.touch_claim(conn, application_id, user.id, now=now)
        return present.message_with_name(conn, message)


@router.post("/applications/{application_id}/takeover/handback")
def hand_back(
    application_id: str, user: User, ctx: Ctx, body: HandBackRequest | None = None
) -> dict[str, Any]:
    now = ctx.clock.now()
    ts = iso(now)
    with ctx.db() as conn, transaction(conn):
        app = present.visible_application(conn, user, application_id)
        if app["control"] != "human":
            return present.takeover_state(conn, app)
        holder = app["control_user_id"]
        if holder != user.id and not user.is_operator:
            raise forbidden(
                "Only the team member holding the chat can hand it back.", "not_in_control"
            )
        given = (body.summary or "").strip() if body else ""
        summary = given or generated_handback_summary(conn, application_id, app["control_since"])
        if repo.release_control(conn, application_id, ts):
            # The summary quotes the transcript (or is the team member's text), so it is
            # stored as a hand-back row, erasable with the Candidate's data; the event
            # only references it (Q38).
            handback_id = repo.add_handback(
                conn, application_id, user_id=user.id, summary=summary, generated=not given, now=now
            )
            repo.add_event(
                conn,
                candidate_id=app["candidate_id"],
                application_id=application_id,
                kind="takeover_handed_back",
                actor="user",
                user_id=user.id,
                data={"handback_id": handback_id, "generated": not given, "held_by": holder},
                now=now,
            )
            repo.touch_claim(conn, application_id, user.id, now=now)
        state = {"control": "agent", "user_id": None, "user_name": None, "since": ts}
    ctx.services.notify(application_id, "takeover_handed_back")
    return state


# ---------------------------------------------------------------------------
# Relay stream
# ---------------------------------------------------------------------------

# ASCII digits only, and few enough for SQLite's 64-bit INTEGER; any other id is ignored.
_ID = re.compile(r"(m|e)-([0-9]{1,18})")


def _initial_cursors(conn: sqlite3.Connection, application_id: str, last_event_id: str | None):
    """``(message_cursor, event_cursor)`` to resume from."""
    match = _ID.fullmatch(last_event_id or "")
    if match:
        kind, number = match.group(1), int(match.group(2))
        if kind == "m":
            created = repo.message_created_at(conn, application_id, number)
            if created is not None:
                return number, repo.max_event_id_before(conn, application_id, created)
        else:
            created = repo.event_created_at(conn, application_id, number)
            if created is not None:
                return repo.max_message_id_before(conn, application_id, created), number
    return repo.max_message_id(conn, application_id), repo.max_application_event_id(
        conn, application_id
    )


def _poll_application(
    ctx: ApiContext, application_id: str, token_hash: str, cursors: list[int]
) -> tuple[list[str], bool]:
    with ctx.db() as conn:
        if not repo.session_is_alive(conn, token_hash, ctx.session_clock.now()):
            return [], False
        messages = repo.list_messages(conn, application_id, after_id=cursors[0])
        events = repo.list_events(conn, application_id, after_id=cursors[1])
        timeline: list[tuple[str, int, str]] = []
        for m in messages:
            timeline.append(
                (
                    m["created_at"],
                    0,
                    frame(
                        "message",
                        m["created_at"],
                        present.message_with_name(conn, m),
                        f"m-{m['id']}",
                    ),
                )
            )
        for e in events:
            timeline.append(
                (
                    e["created_at"],
                    1,
                    frame(
                        "event", e["created_at"], present.event_with_name(conn, e), f"e-{e['id']}"
                    ),
                )
            )
            control = None
            if e["kind"] == "takeover_started":
                control = {
                    "control": "human",
                    "user_id": e["user_id"],
                    "user_name": present.user_name(conn, e["user_id"]),
                    "since": e["created_at"],
                }
            elif e["kind"] == "takeover_handed_back":
                control = {
                    "control": "agent",
                    "user_id": None,
                    "user_name": None,
                    "since": e["created_at"],
                }
            if control:
                timeline.append((e["created_at"], 2, frame("control", e["created_at"], control)))
        if messages:
            cursors[0] = messages[-1]["id"]
        if events:
            cursors[1] = events[-1]["id"]
    timeline.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in timeline], True


@router.get("/applications/{application_id}/stream")
def stream_application(
    application_id: str,
    request: Request,
    user: User,
    ctx: Ctx,
    last_event_id: Annotated[str | None, Header()] = None,
):
    with ctx.db() as conn:
        present.visible_application(conn, user, application_id)
        message_cursor, event_cursor = _initial_cursors(conn, application_id, last_event_id)
    cursors = [message_cursor, event_cursor]
    token_hash = hash_token(request.cookies.get(COOKIE_NAME, ""))
    settings = ctx.settings
    return sse_response(
        stream(
            request,
            poll=lambda: _poll_application(ctx, application_id, token_hash, cursors),
            poll_seconds=settings.sse_poll_seconds,
            max_seconds=settings.sse_max_seconds,
            heartbeat_seconds=settings.sse_heartbeat_seconds,
        )
    )
