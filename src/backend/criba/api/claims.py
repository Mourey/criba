"""Claims: claim, release, and take over a Claimed, idle Claim (spec 003 task 3).

The race is closed in the repository: ``claim_application`` writes with a conditional
``UPDATE`` inside an IMMEDIATE transaction. The routes here also run their own checks
inside one such transaction, so the answer a loser sees (``already_claimed``) is decided
against the state the winner committed.
"""

from __future__ import annotations

from typing import Any

from fastapi import Response

from criba.api import present
from criba.api.auth import Ctx, User
from criba.api.errors import conflict
from criba.api.routing import new_router
from criba.db import repo
from criba.db.connection import transaction

router = new_router(tags=["claims"])


@router.post("/applications/{application_id}/claim")
def claim_application(application_id: str, user: User, ctx: Ctx) -> dict[str, Any]:
    now = ctx.clock.now()
    with ctx.db() as conn, transaction(conn):
        app = present.visible_application(conn, user, application_id)
        holder = app["claim_user_id"]
        if holder is not None and holder != user.id:
            name = present.user_name(conn, holder) or "Another Recruiter"
            raise conflict(
                "already_claimed",
                f"{name} already holds the Claim."
                + (" It is idle: use the take-over route." if present.claim_idle(app, now) else ""),
            )
        app = repo.claim_application(conn, application_id, user.id, now=now)
        return present.claim_json(conn, app, now) or {}


@router.delete("/applications/{application_id}/claim", status_code=204)
def release_claim(application_id: str, user: User, ctx: Ctx) -> Response:
    now = ctx.clock.now()
    with ctx.db() as conn, transaction(conn):
        present.visible_application(conn, user, application_id)
        repo.release_claim(conn, application_id, user.id, now=now)
    return Response(status_code=204)


@router.post("/applications/{application_id}/claim/takeover")
def take_over_idle_claim(application_id: str, user: User, ctx: Ctx) -> dict[str, Any]:
    now = ctx.clock.now()
    with ctx.db() as conn, transaction(conn):
        app = present.visible_application(conn, user, application_id)
        holder = app["claim_user_id"]
        if holder is None:
            raise conflict("wrong_stage", "The Application has no Claim to take over.")
        if holder != user.id and not present.claim_idle(app, now):
            name = present.user_name(conn, holder) or "Another Recruiter"
            raise conflict(
                "already_claimed", f"{name}'s Claim is still active (idle after 7 days)."
            )
        app = repo.claim_application(conn, application_id, user.id, now=now)
        return present.claim_json(conn, app, now) or {}
