"""Booking-page texts and the booking service behind ``services.booking`` (ADR 0030).

One implementation each, shared with the agent tools (lane W2-A, ``criba.tools``):

- **Token.** The signed, expiring booking link is ``criba.tools.make_booking_token`` /
  ``decode_booking_token``, keyed by ``criba.tools.booking_secret()``
  (``CRIBA_BOOKING_SECRET_FILE``). The link the agent sends from ``offer_interview_slots``
  and the one a Recruiter creates here are the same format, so the public page accepts both.
- **Booking.** :class:`ToolsBookingService` wraps ``criba.tools.book_slot``, the one slot
  lock and booking record, used by ``book_interview`` in the chat too.
- **Invite.** ``criba.tools.ics_for_booking`` builds the ``.ics``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from criba.api.services import BookingResult, SlotNotCovering, SlotUnavailable
from criba.db import repo
from criba.db.connection import parse_iso
from criba.tools import ToolError, book_slot

# ---------------------------------------------------------------------------
# Labels and the confirmation message
# ---------------------------------------------------------------------------

_DAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_DAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def confirmation_message(
    language: str | None, first_name: str | None, starts_at: str, timezone: str, location: str
) -> str:
    local = parse_iso(starts_at).astimezone(ZoneInfo(timezone))
    time = f"{local:%H:%M}"
    if language == "en":
        who = f"Done, {first_name}!" if first_name else "Done!"
        return (
            f"{who} Your interview is on {_DAYS_EN[local.weekday()]} the {local.day} at {time} "
            f"at {location}. Download the invitation for your calendar."
        )
    who = f"¡Listo, {first_name}!" if first_name else "¡Listo!"
    return (
        f"{who} Tu entrevista quedó para el {_DAYS_ES[local.weekday()]} {local.day} a las "
        f"{time} en {location}. Descarga la invitación para tu calendario."
    )


def expired_message(language: str | None) -> str:
    if language == "en":
        return "This link has expired. Write to us in the chat and we will send you a new one."
    return "Este enlace ya caducó. Escríbenos por el chat y te mandamos uno nuevo."


# ---------------------------------------------------------------------------
# Booking service
# ---------------------------------------------------------------------------


def slot_status(slot: sqlite3.Row | dict[str, Any], booked: bool, now: datetime) -> str:
    if slot["cancelled_at"] is not None:
        return "cancelled"
    if booked:
        return "booked"
    if parse_iso(slot["starts_at"]) <= now:
        return "past"
    return "open"


class ToolsBookingService:
    """``services.booking`` on top of ``criba.tools.book_slot`` (one lock, one record)."""

    def book(
        self,
        conn: sqlite3.Connection,
        *,
        application_id: str,
        slot_id: str,
        via: Literal["chat", "booking_page"],
        now: datetime,
    ) -> BookingResult:
        slot = repo.slot_with_location(conn, slot_id)
        if slot is None:
            raise SlotNotCovering("there is no such slot")
        if slot["location_id"] not in repo.application_location_ids(conn, application_id):
            raise SlotNotCovering("the slot is at a Location that does not cover the Application")
        if repo.application_holds_slot(conn, application_id, slot_id):
            raise SlotUnavailable("the Application already holds this slot")
        try:
            result = book_slot(conn, application_id, slot_id, via=via, now=now)
        except ToolError as exc:
            raise SlotUnavailable(exc.hint) from exc
        return BookingResult(
            booking_id=result["booking_id"],
            ics_uid=result["ics_uid"],
            application_id=application_id,
            slot_id=slot_id,
            starts_at=slot["starts_at"],
            ends_at=slot["ends_at"],
            location_name=slot["location_name"],
            address=slot["address"],
            timezone=slot["timezone"],
            booked_via=via,
        )
