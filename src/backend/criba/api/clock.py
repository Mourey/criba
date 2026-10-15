"""The simulated clock behind ``/api/sim/clock*`` (spec 004 task 3).

``SimClock`` is real time plus an offset that only ever grows, so the Stalled path
(1h, 20h, 48h, 7d) can be crossed in seconds while everything else keeps flowing.
It satisfies :class:`criba.screening.clock.Clock`. The scheduler, the channel and
the API must share one instance (``ApiContext.clock``) or the offset means nothing.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta


class SimClock:
    def __init__(self) -> None:
        self._offset = timedelta(0)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return datetime.now(UTC) + self._offset

    @property
    def offset_seconds(self) -> int:
        with self._lock:
            return int(self._offset.total_seconds())

    def advance(self, seconds: int) -> datetime:
        if seconds < 1:
            raise ValueError("the clock only moves forward")
        with self._lock:
            self._offset += timedelta(seconds=seconds)
            return datetime.now(UTC) + self._offset

    def advance_to(self, instant: datetime) -> datetime:
        """Move forward to an absolute instant. Refuses to move backwards."""
        if instant.tzinfo is None:
            raise ValueError("the target instant needs a time zone")
        with self._lock:
            current = datetime.now(UTC) + self._offset
            delta = instant.astimezone(UTC) - current
            if delta <= timedelta(0):
                raise ValueError("the clock never moves backwards")
            self._offset += delta
            return datetime.now(UTC) + self._offset

    def reset(self) -> datetime:
        with self._lock:
            self._offset = timedelta(0)
            return datetime.now(UTC)
