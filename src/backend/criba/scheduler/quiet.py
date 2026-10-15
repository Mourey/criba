"""Quiet hours (Q41, Phase 1 detailed spec section 4.1).

No message to a Candidate between 22:00 and 09:00 in the Candidate's local time,
on web chat and WhatsApp alike. Everything here takes an aware instant and a
time zone; callers get "now" from a `Clock`.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from criba.db import repo
from criba.screening.zones import COUNTRY_TIMEZONES

QUIET_START = time(22, 0)
QUIET_END = time(9, 0)


def is_quiet_hours(instant: datetime, tz: tzinfo) -> bool:
    """True from 22:00 (inclusive) to 09:00 (exclusive) local time."""
    local = instant.astimezone(tz).time()
    return local >= QUIET_START or local < QUIET_END


def quiet_hours_end(instant: datetime, tz: tzinfo) -> datetime:
    """When the quiet hours covering `instant` end (UTC); `instant` itself outside them."""
    if not is_quiet_hours(instant, tz):
        return instant.astimezone(UTC)
    local = instant.astimezone(tz)
    day = local.date() if local.time() < QUIET_END else local.date() + timedelta(days=1)
    return datetime.combine(day, QUIET_END, tzinfo=tz).astimezone(UTC)


def candidate_tz(conn: sqlite3.Connection, application: dict[str, Any]) -> tzinfo:
    """The Candidate's local zone: their Zone's, else their country's, else UTC."""
    zone_id = application.get("zone_id")
    if zone_id:
        timezone = repo.zone_timezone(conn, zone_id)
        if timezone:
            return ZoneInfo(timezone)
    country = application.get("country")
    if country is None:
        country = repo.candidate_country(conn, application["candidate_id"])
    if country in COUNTRY_TIMEZONES:
        return ZoneInfo(COUNTRY_TIMEZONES[country])
    return ZoneInfo("UTC")
