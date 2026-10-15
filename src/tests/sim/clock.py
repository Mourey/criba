"""Client for the sim clock endpoints (spec 004 task 3; contracts/webchat.openapi.yaml).

The endpoints (`GET /api/sim/clock`, `POST .../advance`, `POST .../reset`) exist only when the
backend runs with `CRIBA_ENV=sim` and are implemented by lane W2-D. This module is the client:
it advances simulated time and lets the scheduler run between steps, so the Stalled path (1h,
20h, 48h, 7d) completes in seconds.

The clock is global to the backend. Personas that move it must run one at a time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from sim.personas.schema import duration_seconds

CLOCK_PATH = "/api/sim/clock"
MAX_ADVANCE_SECONDS = 7_776_000  # contract maximum per call (90 days)

# Candidate time zones per country (Locations carry their own; the sim uses one per country).
COUNTRY_TIMEZONES = {"ES": "Europe/Madrid", "MX": "America/Mexico_City"}


class SimClockError(RuntimeError):
    """The clock endpoint rejected a request or answered something unexpected."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class SimDisabledError(SimClockError):
    """404 `sim_disabled`: the backend is not running with CRIBA_ENV=sim."""


@dataclass(frozen=True)
class ClockState:
    now: datetime
    offset_seconds: int
    due_jobs_run: int

    @classmethod
    def from_json(cls, body: dict) -> ClockState:
        return cls(
            now=parse_time(body["now"]),
            offset_seconds=int(body["offset_seconds"]),
            due_jobs_run=int(body["due_jobs_run"]),
        )


def parse_time(value: str) -> datetime:
    """Parse an RFC 3339 timestamp (with `Z`) into an aware datetime."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def format_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_local_time(now: datetime, hhmm: str, tz_name: str) -> datetime:
    """The next instant at or after `now` when the wall clock in `tz_name` reads `hhmm`."""
    tz = ZoneInfo(tz_name)
    hour, minute = (int(part) for part in hhmm.split(":"))
    local_now = now.astimezone(tz)
    candidate = datetime.combine(local_now.date(), time(hour, minute), tzinfo=tz)
    if candidate < local_now:
        candidate = datetime.combine(
            local_now.date() + timedelta(days=1), time(hour, minute), tzinfo=tz
        )
    return candidate.astimezone(UTC)


class SimClock:
    """Advance the backend's simulated time.

    `http` is an `httpx.AsyncClient` already pointed at the backend (a real base URL, a Prism
    mock, or an ASGI transport in tests).
    """

    def __init__(self, http: httpx.AsyncClient, *, path: str = CLOCK_PATH):
        self._http = http
        self._path = path.rstrip("/")
        self.jobs_run = 0  # scheduler jobs run by every advance made through this helper

    async def _call(self, method: str, suffix: str = "", json: dict | None = None) -> ClockState:
        url = f"{self._path}{suffix}"
        response = await self._http.request(method, url, json=json)
        body = _json_or_empty(response)
        if response.status_code == 404 and body.get("error") == "sim_disabled":
            raise SimDisabledError(
                body.get("message", "sim clock disabled"),
                status=404,
                code="sim_disabled",
            )
        if response.status_code != 200:
            raise SimClockError(
                f"{method} {url} -> {response.status_code}: {body.get('message', response.text)}",
                status=response.status_code,
                code=body.get("error"),
            )
        return ClockState.from_json(body)

    async def now(self) -> datetime:
        return (await self.state()).now

    async def state(self) -> ClockState:
        return await self._call("GET")

    async def reset(self) -> ClockState:
        """Back to real time. Use between scenarios that share a backend."""
        self.jobs_run = 0
        return await self._call("POST", "/reset")

    async def advance(self, seconds: int | str) -> ClockState:
        """Move the clock forward and run every job now due; returns the final state.

        `seconds` is a count or a persona duration such as '20h'. Spans over the contract's
        90-day limit go out as several calls; `due_jobs_run` in the result is the sum.
        """
        remaining = duration_seconds(seconds) if isinstance(seconds, str) else int(seconds)
        if remaining < 1:
            raise ValueError(f"advance needs at least 1 second, got {remaining}")
        jobs = 0
        state: ClockState | None = None
        while remaining > 0:
            chunk = min(remaining, MAX_ADVANCE_SECONDS)
            state = await self._call("POST", "/advance", {"seconds": chunk})
            jobs += state.due_jobs_run
            remaining -= chunk
        assert state is not None
        self.jobs_run += jobs
        return ClockState(now=state.now, offset_seconds=state.offset_seconds, due_jobs_run=jobs)

    async def advance_to(self, when: datetime) -> ClockState:
        """Move the clock to `when`. The clock never moves backwards: an earlier time raises."""
        current = await self.state()
        if when < current.now:
            raise SimClockError(f"cannot move the clock back from {current.now} to {when}")
        if when == current.now:
            return ClockState(current.now, current.offset_seconds, 0)
        state = await self._call("POST", "/advance", {"to": format_time(when)})
        self.jobs_run += state.due_jobs_run
        return state

    async def advance_steps(self, steps: list[str] | list[int]) -> list[ClockState]:
        """Advance by each step in turn; due jobs run between steps (nudges fire one by one).

        `['1h', '19h', '28h']` reaches +1h, +20h and +48h, running the scheduler at each.
        """
        return [await self.advance(step) for step in steps]

    async def advance_to_local_time(self, hhmm: str, country: str) -> ClockState:
        """Move to the next time the Candidate's wall clock reads `hhmm` (persona start time).

        Personas choose `timing.start_local_time` so nudges at +1h, +20h and +48h fall outside
        quiet hours (22:00 to 09:00).
        """
        current = await self.state()
        target = next_local_time(current.now, hhmm, COUNTRY_TIMEZONES[country])
        return await self.advance_to(target)


def _json_or_empty(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
