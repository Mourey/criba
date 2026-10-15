"""Application JSON export (Phase 2 field list; contracts/api.openapi.yaml
ApplicationExport) and the slot label it shares with the booking tools."""

from __future__ import annotations

import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from criba.db.connection import parse_iso
from criba.db.repo.applications import application_location_ids, require_application

_WEEKDAYS = {
    "es": ("lun", "mar", "mié", "jue", "vie", "sáb", "dom"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
}


def local_label(starts_at: str, timezone: str, language: str | None) -> str:
    """A short local label for a slot, e.g. ``mar 14 · 10:00`` (es) or ``Tue 14 · 10:00`` (en)."""
    local = parse_iso(starts_at).astimezone(ZoneInfo(timezone))
    names = _WEEKDAYS["en" if language == "en" else "es"]
    return f"{names[local.weekday()]} {local.day} · {local:%H:%M}"


def _zone_ref(conn: sqlite3.Connection, zone_id: str | None) -> dict[str, Any] | None:
    if zone_id is None:
        return None
    row = conn.execute(
        "SELECT id, name, city, country FROM zones WHERE id = ?", (zone_id,)
    ).fetchone()
    return dict(row) if row else None


def _interview(conn: sqlite3.Connection, app: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT s.starts_at, s.ends_at, l.name AS location_name, l.address, l.timezone"
        " FROM bookings b JOIN interview_slots s ON s.id = b.slot_id"
        " JOIN locations l ON l.id = s.location_id"
        " WHERE b.application_id = ? AND b.status = 'booked'",
        (app["id"],),
    ).fetchone()
    if row is None:
        return None
    return {
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "local_label": local_label(row["starts_at"], row["timezone"], app["language"]),
        "location_name": row["location_name"],
        "address": row["address"],
    }


def export_application(conn: sqlite3.Connection, application_id: str) -> dict[str, Any]:
    """The Application's structured JSON: every screening field. Contains no cost data."""
    app = require_application(conn, application_id)
    summary = None
    if app["summary_at"] is not None:
        summary = {
            "structured": app["summary_structured"] or {},
            "prose": app["summary_prose"],
            "written_at": app["summary_at"],
        }
    return {
        "application_id": app["id"],
        "candidate_id": app["candidate_id"],
        "channel": app["channel"],
        "country": app["country"],
        "language": app["language"],
        "status": app["status"],
        "stage": app["stage"],
        "outcome": app["outcome"],
        "knockout_criterion": app["knockout_criterion"],
        "rejection_reason": app["rejection_reason"],
        "answers": {
            "full_name": app["full_name"],
            "zone": _zone_ref(conn, app["zone_id"]),
            "driver_license": app["driver_license"],
            "availability": app["availability"],
            "preferred_schedule": app["preferred_schedule"],
            "experience_years": app["experience_years"],
            "experience_platforms": app["experience_platforms"],
            "start_date": app["start_date"],
        },
        "start_date_far": app["start_date_far"],
        "locations": application_location_ids(conn, application_id),
        "created_at": app["created_at"],
        "updated_at": app["updated_at"],
        "qualified_at": app["qualified_at"],
        "approved_at": app["approved_at"],
        "closed_at": app["closed_at"],
        "interview": _interview(conn, app),
        "summary": summary,
    }
