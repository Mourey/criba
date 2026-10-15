import threading
from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from criba.screening import Clock, FakeClock, SystemClock, local_today

T0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def test_system_clock_is_aware_utc_and_current() -> None:
    before = datetime.now(UTC)
    now = SystemClock().now()
    after = datetime.now(UTC)
    assert now.utcoffset() == timedelta(0)
    assert before <= now <= after


def test_both_clocks_satisfy_the_protocol() -> None:
    assert isinstance(SystemClock(), Clock)
    assert isinstance(FakeClock(T0), Clock)


def test_fake_clock_stands_still_until_advanced() -> None:
    clock = FakeClock(T0)
    assert clock.now() == T0
    assert clock.now() == T0


def test_fake_clock_advances_by_delta_or_kwargs() -> None:
    clock = FakeClock(T0)
    assert clock.advance(timedelta(hours=1)) == T0 + timedelta(hours=1)
    assert clock.advance(days=7, minutes=30) == T0 + timedelta(days=7, hours=1, minutes=30)
    assert clock.now() == T0 + timedelta(days=7, hours=1, minutes=30)


def test_fake_clock_refuses_to_advance_backwards_but_set_can() -> None:
    clock = FakeClock(T0)
    with pytest.raises(ValueError):
        clock.advance(hours=-1)
    earlier = T0 - timedelta(days=1)
    assert clock.set(earlier) == earlier
    assert clock.now() == earlier


def test_fake_clock_normalises_to_utc_and_rejects_naive() -> None:
    madrid_noon = datetime(2026, 10, 9, 12, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    clock = FakeClock(madrid_noon)
    assert clock.now() == madrid_noon
    assert clock.now().tzinfo == UTC
    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 10, 9, 12, 0))
    with pytest.raises(ValueError):
        clock.set(datetime(2026, 10, 9, 12, 0))


def test_fake_clock_is_thread_safe() -> None:
    clock = FakeClock(T0)
    threads = [
        threading.Thread(target=lambda: [clock.advance(seconds=1) for _ in range(500)])
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert clock.now() == T0 + timedelta(seconds=4000)


@pytest.mark.parametrize(
    ("utc_instant", "tz", "expected"),
    [
        # 23:30 UTC on Oct 9 is already Oct 10 in Madrid (UTC+2) ...
        (datetime(2026, 10, 9, 23, 30, tzinfo=UTC), "Europe/Madrid", date(2026, 10, 10)),
        # ... but still Oct 9 in Mexico City (UTC-6).
        (datetime(2026, 10, 9, 23, 30, tzinfo=UTC), "America/Mexico_City", date(2026, 10, 9)),
        # 03:00 UTC on Oct 10 is still Oct 9 in Tijuana (UTC-7, DST).
        (datetime(2026, 10, 10, 3, 0, tzinfo=UTC), "America/Tijuana", date(2026, 10, 9)),
        # Cancún is UTC-5 all year.
        (datetime(2026, 10, 10, 4, 30, tzinfo=UTC), "America/Cancun", date(2026, 10, 9)),
        (datetime(2026, 10, 10, 5, 30, tzinfo=UTC), "America/Cancun", date(2026, 10, 10)),
    ],
)
def test_local_today_uses_the_given_zone(utc_instant: datetime, tz: str, expected: date) -> None:
    assert local_today(FakeClock(utc_instant), ZoneInfo(tz)) == expected


def test_local_today_accepts_fixed_offsets() -> None:
    clock = FakeClock(datetime(2026, 10, 9, 23, 0, tzinfo=UTC))
    assert local_today(clock, timezone(timedelta(hours=2))) == date(2026, 10, 10)
