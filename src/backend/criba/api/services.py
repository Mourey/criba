"""Injection points for the other wave 2 lanes.

The API never imports another lane's code. Each dependency is a small protocol with a
working default in this package, so the lane's gate passes on its own and the integrator
swaps in the real thing at merge (see the lane's merge notes):

- ``sender``  (lane W2-B): how a message reaches the Candidate (Takeover text, the
  "team member joined" notice, the Rejection template). Default: store the outbound
  message; the web chat reads it from there.
- ``booking`` (lane W2-A): the slot lock and the booking record. Default and only
  implementation: :class:`criba.api.booking.ToolsBookingService`, over ``criba.tools.book_slot``.
- ``submit_action`` (lane W2-C): ``scheduler.submit_action(conn, application_id, "approved")``,
  called once, after an Approval has committed (dispatch opens its own connection). Default:
  no-op.
- ``notify``  (optional hook): called after other Recruiter actions committed, with
  ``(application_id, event_kind)`` such as ``rejection`` or ``review_overturned``. Never
  called for ``approved``. Default: no-op.
- ``comparison`` (spec 004 task 10): the model comparison replay. Default: derived from
  the usage ledger's replay rows.
- ``run_due_jobs`` (lane W2-C): "run every scheduler job now due" for the sim clock.
  Lives on :class:`criba.api.context.ApiContext`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

Notice = Literal["takeover_joined", "rejection"]


class SlotUnavailable(Exception):
    """The slot is booked, cancelled, in the past, or the lock was lost."""


class SlotNotCovering(Exception):
    """The slot is at a Location that does not cover the Application."""


@dataclass(frozen=True)
class BookingResult:
    booking_id: str
    ics_uid: str
    application_id: str
    slot_id: str
    starts_at: str
    ends_at: str
    location_name: str
    address: str | None
    timezone: str
    booked_via: str


class CandidateSender(Protocol):
    def send_human_text(
        self,
        conn: sqlite3.Connection,
        *,
        application_id: str,
        user_id: str,
        text: str,
        now: datetime,
    ) -> dict[str, Any]:
        """Deliver a team member's text to the Candidate; return the stored message row."""
        ...

    def send_notice(
        self, conn: sqlite3.Connection, *, application_id: str, notice: Notice, now: datetime
    ) -> dict[str, Any] | None:
        """Deliver a fixed notice (``takeover_joined``, ``rejection``); return the stored row."""
        ...


class BookingService(Protocol):
    def book(
        self,
        conn: sqlite3.Connection,
        *,
        application_id: str,
        slot_id: str,
        via: Literal["chat", "booking_page"],
        now: datetime,
    ) -> BookingResult:
        """Lock the slot and record the booking, releasing the previous one (reschedule).

        Raises :class:`SlotUnavailable` or :class:`SlotNotCovering`.
        """
        ...


class ComparisonProvider(Protocol):
    def comparison(self, conn: sqlite3.Connection, dataset: str | None) -> dict[str, Any] | None:
        """The ``ModelComparison`` payload for the dataset (latest when None), or None."""
        ...


def _noop_notify(application_id: str, event_kind: str) -> None:
    return None


def _noop_submit(conn: sqlite3.Connection, application_id: str, action: str) -> None:
    return None


@dataclass
class Services:
    sender: CandidateSender
    booking: BookingService
    comparison: ComparisonProvider
    notify: Any = _noop_notify  # Callable[[str, str], None]
    submit_action: Any = _noop_submit  # Callable[[sqlite3.Connection, str, str], Any]

    @classmethod
    def defaults(cls) -> Services:
        from criba.api.booking import ToolsBookingService
        from criba.api.messages import StoredMessageSender
        from criba.api.metrics import UsageComparison

        return cls(
            sender=StoredMessageSender(),
            booking=ToolsBookingService(),
            comparison=UsageComparison(),
        )
