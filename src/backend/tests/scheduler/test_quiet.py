"""Quiet hours: 22:00 to 09:00 in the Candidate's local time (Q41)."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from criba.db import repo
from criba.scheduler import candidate_tz, is_quiet_hours, quiet_hours_end

MADRID = ZoneInfo("Europe/Madrid")
MEXICO = ZoneInfo("America/Mexico_City")


def at(tz: ZoneInfo, hour: int, minute: int = 0, day: int = 12) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=tz).astimezone(UTC)


@pytest.mark.parametrize(
    ("hour", "minute", "quiet"),
    [(21, 59, False), (22, 0, True), (23, 59, True), (0, 0, True), (8, 59, True), (9, 0, False)],
)
def test_quiet_hours_boundaries(hour: int, minute: int, quiet: bool) -> None:
    assert is_quiet_hours(at(MADRID, hour, minute), MADRID) is quiet


def test_the_same_instant_is_quiet_in_one_country_and_not_in_another() -> None:
    instant = at(MADRID, 23)  # 15:00 in Mexico City
    assert is_quiet_hours(instant, MADRID)
    assert not is_quiet_hours(instant, MEXICO)


def test_quiet_hours_end_at_nine_local_the_same_morning_or_the_next() -> None:
    assert quiet_hours_end(at(MEXICO, 6, 30), MEXICO) == at(MEXICO, 9)
    assert quiet_hours_end(at(MEXICO, 23, 0), MEXICO) == at(MEXICO, 9, day=13)
    assert quiet_hours_end(at(MEXICO, 22, 0), MEXICO) == at(MEXICO, 9, day=13)


def test_outside_quiet_hours_the_end_is_now() -> None:
    instant = at(MADRID, 12)
    assert quiet_hours_end(instant, MADRID) == instant


def test_quiet_hours_end_follows_a_daylight_saving_change() -> None:
    # Madrid leaves summer time on 2026-10-25: 22:00 on the 24th is UTC+2, 09:00 on the 25th UTC+1.
    start = datetime(2026, 10, 24, 22, 30, tzinfo=MADRID)
    assert quiet_hours_end(start, MADRID) == datetime(2026, 10, 25, 8, 0, tzinfo=UTC)


def test_the_candidates_zone_decides_the_time_zone(conn, start_app) -> None:
    app = start_app(conn, phone="+34600111222")
    assert candidate_tz(conn, app) == ZoneInfo("Europe/Madrid")
    repo.record_answer(conn, app["id"], "zone", "zone_mx_puebla_centro")
    app = repo.require_application(conn, app["id"])
    assert candidate_tz(conn, app) == ZoneInfo("America/Mexico_City")


def test_without_a_zone_or_country_quiet_hours_use_utc(conn, start_app) -> None:
    app = start_app(conn, phone=None)
    assert candidate_tz(conn, app) == ZoneInfo("UTC")


@pytest.mark.parametrize(
    ("source", "tz"),
    [
        ("zone", MEXICO),  # the Zone beats the +34 phone's country
        ("country", MADRID),
        ("none", ZoneInfo("UTC")),
    ],
)
@pytest.mark.parametrize(
    ("hour", "minute", "quiet"),
    [(21, 59, False), (22, 0, True), (3, 0, True), (8, 59, True), (9, 0, False)],
)
def test_the_one_quiet_hours_rule(
    conn, start_app, source: str, tz: ZoneInfo, hour: int, minute: int, quiet: bool
) -> None:
    """The single rule the scheduler and the channel sender both use: no message
    22:00-09:00 in the Candidate's local time, the time zone taken from the Zone, else
    the country, else UTC."""
    phone = None if source == "none" else "+34600111222"
    app = start_app(conn, phone=phone)
    if source == "zone":
        repo.record_answer(conn, app["id"], "zone", "zone_mx_puebla_centro")
        app = repo.require_application(conn, app["id"])
    assert candidate_tz(conn, app) == tz
    instant = at(tz, hour, minute)
    assert is_quiet_hours(instant, tz) is quiet
    end = quiet_hours_end(instant, tz)
    if quiet:
        assert end.astimezone(tz).time() == time(9, 0) and end > instant
    else:
        assert end == instant


def test_the_channel_sender_uses_the_scheduler_rule() -> None:
    from criba.channel import sender

    assert sender.is_quiet_hours is is_quiet_hours
    assert sender.candidate_tz is candidate_tz
