"""Interview slots, signed booking links and the public booking page (spec 003 task 6, ADR 0030).

``router`` needs a session; ``public_router`` is reached by a signed, expiring token and
exposes nothing but the open slots at the Application's covering Locations, the first
name and the language. Locking and the booking record go through ``services.booking``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import Query, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from criba.api import present
from criba.api.auth import Ctx, User
from criba.api.booking import confirmation_message, expired_message, slot_status
from criba.api.errors import conflict, gone, not_found, validation_error
from criba.api.routing import new_router
from criba.api.services import SlotNotCovering, SlotUnavailable
from criba.db import repo
from criba.db.connection import iso, new_id, transaction
from criba.tools import (
    BookingTokenError,
    BookingTokenExpired,
    decode_booking_token,
    ics_for_booking,
    make_booking_token,
)
from criba.tools.booking import INVITE_FILENAME

router = new_router(tags=["slots"])
public_router = new_router(tags=["public-booking"])

MAX_SLOT = timedelta(hours=8)
BOOKABLE_STAGES = ("approved", "interview_booked")
PUBLIC_SLOT_LIMIT = 200


# ---------------------------------------------------------------------------
# Slot CRUD
# ---------------------------------------------------------------------------


class SlotTimes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def ordered(self) -> SlotTimes:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.ends_at - self.starts_at > MAX_SLOT:
            raise ValueError("a slot lasts at most 8 hours")
        return self


class SlotCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str
    slots: Annotated[list[SlotTimes], Field(min_length=1, max_length=100)]


def slot_json(row: sqlite3.Row, now: datetime) -> dict[str, Any]:
    booked = row["b_application_id"] is not None
    return {
        "id": row["id"],
        "location_id": row["location_id"],
        "location_name": row["location_name"],
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "local_label": repo.local_label(row["starts_at"], row["timezone"], "es"),
        "status": slot_status(row, booked, now),
        "booking": {
            "application_id": row["b_application_id"],
            "candidate_name": row["candidate_name"],
            "booked_via": row["b_via"],
            "booked_at": row["b_at"],
        }
        if booked
        else None,
    }


def _written_slot(conn: sqlite3.Connection, slot_id: str) -> sqlite3.Row:
    """A slot this transaction has just inserted or updated, so the row exists."""
    row = repo.slot_detail(conn, slot_id)
    assert row is not None
    return row


def _scoped_slot(conn: sqlite3.Connection, user: Any, slot_id: str) -> sqlite3.Row:
    row = repo.slot_detail(conn, slot_id)
    if row is None or (not user.is_operator and row["location_id"] not in user.location_ids):
        raise not_found("Slot not found.")
    return row


def _require_future(times: SlotTimes, now: datetime, field_prefix: str = "") -> None:
    if times.starts_at <= now:
        raise validation_error("A slot must start in the future.", f"{field_prefix}starts_at")


@router.get("/slots")
def list_slots(
    user: User,
    ctx: Ctx,
    location_id: str | None = None,
    from_: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    to: AwareDatetime | None = None,
    status: Annotated[str | None, Query(pattern="^(open|booked|cancelled|past)$")] = None,
) -> dict[str, Any]:
    now = ctx.clock.now()
    scope, params = present.slot_in_scope_sql(user)
    with ctx.db() as conn:
        rows = repo.list_scoped_slots(
            conn,
            scope,
            params,
            location_id=location_id,
            starts_from=iso(from_) if from_ else None,
            starts_to=iso(to) if to else None,
        )
    items = [slot_json(r, now) for r in rows]
    if status:
        items = [i for i in items if i["status"] == status]
    return {"items": items}


@router.post("/slots", status_code=201)
def create_slots(body: SlotCreateRequest, user: User, ctx: Ctx) -> dict[str, Any]:
    now = ctx.clock.now()
    if not user.is_operator and body.location_id not in user.location_ids:
        raise not_found("Location not found.")
    with ctx.db() as conn, transaction(conn):
        if not repo.active_location_exists(conn, body.location_id):
            raise validation_error("Unknown Location.", "location_id")
        seen: set[str] = set()
        ids = []
        for index, times in enumerate(body.slots):
            _require_future(times, now, f"slots.{index}.")
            starts = iso(times.starts_at)
            if starts in seen or repo.slot_start_taken(conn, body.location_id, starts):
                raise validation_error(
                    "A slot already starts at that time.", f"slots.{index}.starts_at"
                )
            seen.add(starts)
            slot_id = new_id("slot", now)
            repo.insert_interview_slot(
                conn, slot_id, body.location_id, starts, iso(times.ends_at), user.id, iso(now)
            )
            ids.append(slot_id)
        items = [slot_json(_written_slot(conn, i), now) for i in ids]
    return {"items": items}


@router.patch("/slots/{slot_id}")
def update_slot(slot_id: str, body: SlotTimes, user: User, ctx: Ctx) -> dict[str, Any]:
    now = ctx.clock.now()
    with ctx.db() as conn, transaction(conn):
        row = _scoped_slot(conn, user, slot_id)
        if row["cancelled_at"] is not None:
            raise conflict("already_resolved", "The slot was cancelled.")
        if row["b_application_id"] is not None:
            raise conflict("slot_booked", "The slot is booked; move the booking first.")
        _require_future(body, now)
        starts = iso(body.starts_at)
        if repo.slot_start_taken(conn, row["location_id"], starts, ignore=slot_id):
            raise validation_error("A slot already starts at that time.", "starts_at")
        repo.move_interview_slot(conn, slot_id, starts, iso(body.ends_at))
        return slot_json(_written_slot(conn, slot_id), now)


@router.delete("/slots/{slot_id}", status_code=204)
def cancel_slot(slot_id: str, user: User, ctx: Ctx) -> Response:
    now = ctx.clock.now()
    with ctx.db() as conn, transaction(conn):
        row = _scoped_slot(conn, user, slot_id)
        if row["b_application_id"] is not None:
            raise conflict("slot_booked", "The slot is booked; reject or reschedule it first.")
        if row["cancelled_at"] is None:
            repo.cancel_interview_slot(conn, slot_id, iso(now))
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Booking links
# ---------------------------------------------------------------------------


@router.post("/applications/{application_id}/booking-link", status_code=201)
def create_booking_link(application_id: str, user: User, ctx: Ctx) -> dict[str, Any]:
    now = ctx.clock.now()
    with ctx.db() as conn, transaction(conn):
        app = present.visible_application(conn, user, application_id)
        if app["status"] != "open":
            raise conflict("application_closed", "The Application is closed.")
        if app["stage"] not in BOOKABLE_STAGES:
            raise conflict("wrong_stage", "Only an Approved Application can book an Interview.")
        expires_at = now + ctx.settings.booking_link_ttl
        token = make_booking_token(application_id, expires_at, secret=ctx.settings.booking_secret)
        repo.add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            kind="booking_link_sent",
            actor="user",
            user_id=user.id,
            data={"expires_at": iso(expires_at)},
            now=now,
        )
        repo.touch_claim(conn, application_id, user.id, now=now)
    return {
        "url": f"{ctx.settings.public_base_url}/book/{token}",
        "token": token,
        "expires_at": iso(expires_at),
    }


# ---------------------------------------------------------------------------
# Public booking page
# ---------------------------------------------------------------------------


class PublicBookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot_id: str


def _language(app: dict[str, Any] | None) -> str | None:
    return app["language"] if app else None


def _bookable_application(
    conn: sqlite3.Connection, ctx: Any, token: str, now: datetime
) -> tuple[dict[str, Any], datetime]:
    """Verify the token and return its Application, or raise 404 / 410."""
    try:
        application_id, expires_at = decode_booking_token(
            token, now=now, secret=ctx.settings.booking_secret
        )
    except BookingTokenExpired as exc:
        app = repo.get_application(conn, exc.application_id)
        raise gone(expired_message(_language(app))) from None
    except BookingTokenError:
        raise not_found("This link is not valid.") from None
    app = repo.get_application(conn, application_id)
    if app is None or app["status"] != "open" or app["stage"] not in BOOKABLE_STAGES:
        raise not_found("This link is not valid.")
    return app, expires_at


def _open_slots(
    conn: sqlite3.Connection, app: dict[str, Any], now: datetime
) -> list[dict[str, Any]]:
    location_ids = repo.application_location_ids(conn, app["id"])
    if not location_ids:
        return []
    rows = repo.public_open_slots(conn, location_ids, iso(now), PUBLIC_SLOT_LIMIT)
    return [
        {
            "slot_id": r["id"],
            "starts_at": r["starts_at"],
            "ends_at": r["ends_at"],
            "local_label": repo.local_label(r["starts_at"], r["timezone"], app["language"]),
            "location_name": r["location_name"],
            "address": r["address"],
        }
        for r in rows
    ]


def _first_name(conn: sqlite3.Connection, app: dict[str, Any]) -> str | None:
    candidate = repo.get_candidate(conn, app["candidate_id"])
    name = (candidate or {}).get("full_name") or app["full_name"]
    return name.split()[0] if name and name.strip() else None


def _page_language(app: dict[str, Any]) -> str:
    return app["language"] or ("es-ES" if app["country"] == "ES" else "es-MX")


@public_router.get("/public/booking/{token}")
def get_booking_page(token: str, ctx: Ctx) -> dict[str, Any]:
    now = ctx.clock.now()
    with ctx.db() as conn:
        app, expires_at = _bookable_application(conn, ctx, token, now)
        return {
            "first_name": _first_name(conn, app),
            "language": _page_language(app),
            "expires_at": iso(expires_at),
            "current_booking": present.interview_of(conn, app),
            "slots": _open_slots(conn, app, now),
        }


@public_router.post("/public/booking/{token}", status_code=201)
def book_from_page(token: str, body: PublicBookingRequest, ctx: Ctx) -> dict[str, Any]:
    now = ctx.clock.now()
    with ctx.db() as conn:
        app, _ = _bookable_application(conn, ctx, token, now)
        try:
            result = ctx.services.booking.book(
                conn,
                application_id=app["id"],
                slot_id=body.slot_id,
                via="booking_page",
                now=now,
            )
        except SlotUnavailable:
            message = (
                "That time is no longer available. Pick another."
                if app["language"] == "en"
                else "Ese horario ya no está disponible. Elige otro."
            )
            raise conflict("slot_taken", message) from None
        except SlotNotCovering:
            raise not_found("That slot is not offered to you.") from None
        app = repo.require_application(conn, app["id"])
        interview = present.interview_of(conn, app)
        first = _first_name(conn, app)
    ctx.services.notify(app["id"], "interview_booked")
    assert interview is not None
    return {
        "interview": interview,
        "ics_url": f"/api/public/booking/{token}/invite.ics",
        "message": confirmation_message(
            _page_language(app), first, result.starts_at, result.timezone, result.location_name
        ),
    }


def _invite_response(conn: sqlite3.Connection, application_id: str) -> Response:
    """The Application's active booking as ``text/calendar``: one builder, one UID, for the
    Candidate (public page) and the Recruiter (app) alike."""
    booking_id = repo.current_booking_id(conn, application_id)
    if booking_id is None:
        raise not_found("There is no booked Interview.")
    return Response(
        content=ics_for_booking(conn, booking_id),
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{INVITE_FILENAME}"'},
    )


@public_router.get("/public/booking/{token}/invite.ics")
def download_invite(token: str, ctx: Ctx) -> Response:
    now = ctx.clock.now()
    with ctx.db() as conn:
        app, _ = _bookable_application(conn, ctx, token, now)
        return _invite_response(conn, app["id"])


@router.get("/applications/{application_id}/booking/invite.ics")
def download_application_invite(application_id: str, user: User, ctx: Ctx) -> Response:
    """The Recruiter's copy of the invite (ADR 0030): the Candidate's ``.ics``, same UID."""
    with ctx.db() as conn:
        present.visible_application(conn, user, application_id)
        return _invite_response(conn, application_id)
