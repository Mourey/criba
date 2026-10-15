"""Injectable time source (spec 002 task 6).

Everything in the backend that needs "now" takes a `Clock`, so tests and the
simulator (spec 004) can drive time instead of sleeping. `now()` always returns
an aware datetime in UTC; convert to the Candidate's zone with `local_today`.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """The current instant, timezone-aware, in UTC."""
        ...


class SystemClock:
    """Real wall-clock time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """A clock that only moves when told to. Safe to share across threads."""

    def __init__(self, start: datetime) -> None:
        self._now = _as_utc(start)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, delta: timedelta | None = None, /, **kwargs: float) -> datetime:
        """Move forward by `delta` or by `timedelta(**kwargs)`; returns the new now.

        Moving backwards is refused: use `set` when a test really needs that.
        """
        step = (delta or timedelta()) + timedelta(**kwargs)
        if step < timedelta(0):
            raise ValueError("FakeClock.advance cannot move backwards; use set()")
        with self._lock:
            self._now += step
            return self._now

    def set(self, instant: datetime) -> datetime:
        """Jump to an absolute instant (either direction)."""
        with self._lock:
            self._now = _as_utc(instant)
            return self._now


def local_today(clock: Clock, tz: tzinfo) -> date:
    """Today's date in the given time zone, according to `clock`."""
    return clock.now().astimezone(tz).date()


def _as_utc(instant: datetime) -> datetime:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("FakeClock needs a timezone-aware datetime")
    return instant.astimezone(UTC)
