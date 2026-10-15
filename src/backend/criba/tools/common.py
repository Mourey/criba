"""Shared pieces of the agent tools: the tool error, guards, and the answer and stage rules."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from criba.db import repo

# Collection order after the greeting (Phase 1 detailed spec section 1 and 3).
FIELD_ORDER: tuple[str, ...] = (
    "full_name",
    "zone",
    "driver_license",
    "availability",
    "preferred_schedule",
    "experience_years",
    "experience_platforms",
    "start_date",
)
FIELD_STAGE: dict[str, str] = {
    "full_name": "name",
    "zone": "zone",
    "driver_license": "driver_license",
    "availability": "availability",
    "preferred_schedule": "preferred_schedule",
    "experience_years": "experience",
    "experience_platforms": "experience",
    "start_date": "start_date",
}
# Applications.column per answer field (``zone`` is stored as ``zone_id``).
FIELD_COLUMN: dict[str, str] = {f: ("zone_id" if f == "zone" else f) for f in FIELD_ORDER}
APPROVED_STAGES = frozenset({"approved", "interview_booked"})


class ToolError(Exception):
    """A tool-level failure: becomes ``{ok: false, error, hint, ...}`` (``$defs/tool_error``)."""

    def __init__(self, error: str, hint: str, **extra: Any) -> None:
        super().__init__(f"{error}: {hint}")
        self.error = error
        self.hint = hint
        self.extra = extra

    def body(self) -> dict[str, Any]:
        return {"ok": False, "error": self.error, "hint": self.hint, **self.extra}


def language_of(app: dict[str, Any]) -> str:
    """The Candidate's language; before any is known, the country's Spanish."""
    if app.get("language"):
        return app["language"]
    return "es-MX" if app.get("country") == "MX" else "es-ES"


def field_missing(app: dict[str, Any], field: str) -> bool:
    """Whether ``field`` still needs an answer. Platforms are skipped when years is 0."""
    if field == "experience_platforms":
        years = app.get("experience_years")
        if years is not None and years == 0:
            return False
    return app.get(FIELD_COLUMN[field]) is None


def next_field(app: dict[str, Any]) -> str | None:
    """The next unanswered field in collection order, or None when all are answered."""
    return next((f for f in FIELD_ORDER if field_missing(app, f)), None)


def screening_stage(app: dict[str, Any]) -> str:
    """The Screening substage the answers put the Application at."""
    pending = next_field(app)
    return FIELD_STAGE[pending] if pending else "confirm"


def is_approved(app: dict[str, Any]) -> bool:
    """Approved in the database (Q41): open, past Approval, with an Approval time recorded."""
    return (
        app["status"] == "open"
        and app["stage"] in APPROVED_STAGES
        and app.get("approved_at") is not None
    )


def load_application(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    """The Application for this session (``session_id`` is the Application id)."""
    app = repo.get_application(conn, session_id)
    if app is None:
        raise ToolError(
            "unknown_session",
            "This session is not linked to any Application. Tell the Candidate you need a "
            "moment; do not ask them for an id.",
        )
    return app


def require_agent_control(app: dict[str, Any]) -> None:
    if app["control"] == "human":
        raise ToolError(
            "human_in_control",
            "A team member holds this chat (Takeover). Send one short line that the team is "
            "with them and call nothing else.",
        )


def require_open(app: dict[str, Any]) -> None:
    if app["status"] != "open":
        hint = "The Application has ended"
        if app["outcome"] == "knockout":
            hint += (
                " in a Knockout. Say so kindly; if they think it is wrong, they can reply "
                "'revisión': on a dispute or a request for a person, call request_review. "
                "Only a new answer to the failed question (record_answer with that field) "
                "can reopen it, within 30 days."
            )
        else:
            hint += ". Say so kindly and do nothing else."
        raise ToolError("application_closed", hint)


def answers_view(conn: sqlite3.Connection, app: dict[str, Any]) -> dict[str, Any]:
    """The ``$defs/answers`` shape: canonical values, the Zone as ``{name, city, country}``.

    A field not answered yet is left out: a ``null`` per field would repeat in every result the
    model keeps in its history. ``0`` years and ``false`` are answers and stay.
    """
    zone = None
    if app["zone_id"]:
        row = repo.zone_row(conn, app["zone_id"])
        if row:
            zone = {"name": row["name"], "city": row["city"], "country": row["country"]}
    years = app["experience_years"]
    view = {
        "full_name": app["full_name"],
        "zone": zone,
        "driver_license": app["driver_license"],
        "availability": app["availability"],
        "preferred_schedule": app["preferred_schedule"],
        "experience_years": years,
        "experience_platforms": app["experience_platforms"],
        "start_date": app["start_date"],
    }
    return {name: value for name, value in view.items() if value is not None}


def first_name_of(app: dict[str, Any]) -> str | None:
    name = (app.get("full_name") or "").split()
    return name[0] if name else None


def interview_view(conn: sqlite3.Connection, app: dict[str, Any]) -> dict[str, Any] | None:
    """The active booking as ``$defs/interview``, or None."""
    row = repo.interview_booking_row(conn, app["id"])
    if row is None:
        return None
    return slot_interview(row, language_of(app))


def slot_interview(row: Any, language: str) -> dict[str, Any]:
    return {
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "location_name": row["location_name"],
        "address": row["address"],
        "local_label": repo.local_label(row["starts_at"], row["timezone"], language),
    }


class FixedClock:
    """A ``Clock`` pinned to one instant: a tool call sees one consistent "now"."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now
