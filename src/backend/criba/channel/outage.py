"""OpenRouter outage: classify a failed run, and keep ONE system alarm (ADR 0078).

Hermes v0.21.6 passes no structured status through: a failed run's status and event carry
``error`` text only, ``"HTTP <status>: <message>"`` for an upstream refusal
(``gateway/platforms/api_server_runs.py:975-977``, ``agent/api_error_summary.py:71-73,196``;
``failure_reason`` is set on the agent result but not forwarded, ``api_server_runs.py:181-198``).
So :func:`classify` reads that text.

:class:`OutageAlarms` holds the alarm in memory (a restart drops it; the next failures or the next
key check raise it again). Two sources feed it, one alarm per cause: classified run failures
(3 Applications in 5 minutes, or one for ``credits`` and ``key_rejected``), cleared by a run that
succeeds; and :class:`KeyCheck`, the hourly read of the key's ``limit_remaining``. Codes only
(ADR 0035); the key is never logged.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

from criba.channel.run_client import RunFailed
from criba.screening.clock import Clock

log = logging.getLogger(__name__)

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
KEY_FLOOR_ENV = "CRIBA_OPENROUTER_KEY_FLOOR_USD"
# About two days of the measured spend at the pilot's volume (the pilot deployment design).
DEFAULT_FLOOR_USD = 5.0
KEY_CHECK_EVERY = timedelta(hours=1)
KEY_CHECK_TIMEOUT_S = 5.0

# Causes that mean the shared dependency is down; "other" is any failure that is not.
OUTAGE_CAUSES = ("credits", "rate_limited", "model_unavailable", "key_rejected")
OTHER = "other"
WINDOW = timedelta(minutes=5)
THRESHOLD = 3
# One failure is enough when the cause cannot be transient.
THRESHOLD_BY_CAUSE = {"credits": 1, "key_rejected": 1}

# A failed run's error as the run client wraps it: "run failed: …" (poll) or "run.failed: …"
# (stream). "run not accepted: HTTP 429" is Hermes refusing the POST, not the upstream.
_RUN_ERROR = re.compile(r"^run[. ]\w+: (?:HTTP (?P<status>\d{3}): )?(?P<text>.*)", re.DOTALL)
_UNREACHABLE = "can't reach the model provider"


def classify(failure: RunFailed) -> str:
    """The cause of a failed run: one of :data:`OUTAGE_CAUSES`, or ``other``."""
    match = _RUN_ERROR.match(str(failure))
    if not failure.final or match is None:
        return OTHER
    status = match["status"]
    if status is None:
        return "model_unavailable" if _UNREACHABLE in match["text"] else OTHER
    code = int(status)
    if code == 402:
        return "credits"
    if code == 429:
        return "rate_limited"
    if code == 401:
        return "key_rejected"
    if code == 404 or code >= 500:
        return "model_unavailable"
    return OTHER


@dataclass(frozen=True, slots=True)
class Alarm:
    cause: str
    since: datetime
    sources: tuple[str, ...]  # "runs" and/or "key"


class OutageAlarms:
    """The open alarms: at most one per cause, whatever number of Applications failed."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failed: dict[str, dict[str, datetime]] = {}  # cause -> Application -> last failure
        self._runs: dict[str, datetime] = {}  # cause -> since
        self._key: tuple[str, datetime] | None = None

    def record_failure(self, cause: str, application_id: str) -> None:
        if cause not in OUTAGE_CAUSES:
            return
        with self._lock:
            now = self._clock.now()
            seen = {a: t for a, t in self._failed.get(cause, {}).items() if now - t <= WINDOW}
            seen[application_id] = now
            self._failed[cause] = seen
            if cause not in self._runs and len(seen) >= THRESHOLD_BY_CAUSE.get(cause, THRESHOLD):
                self._runs[cause] = now
                log.error(
                    "OpenRouter outage alarm opened: cause=%s, %d Applications failed",
                    cause,
                    len(seen),
                )

    def run_succeeded(self) -> None:
        """A run got an answer from the model: the outage the runs saw is over."""
        with self._lock:
            self._failed.clear()
            for cause in self._runs:
                log.info("OpenRouter outage alarm cleared by a run that succeeded: cause=%s", cause)
            self._runs.clear()

    def set_key_state(self, cause: str | None) -> None:
        """The key check's verdict: a cause, or None when the key is fine."""
        with self._lock:
            if cause is None:
                if self._key is not None:
                    log.info("OpenRouter key alarm cleared: cause=%s", self._key[0])
                self._key = None
            elif self._key is None or self._key[0] != cause:
                self._key = (cause, self._clock.now())
                log.error("OpenRouter key alarm opened: cause=%s", cause)

    def open(self) -> list[Alarm]:
        with self._lock:
            since = dict(self._runs)
            sources: dict[str, list[str]] = {c: ["runs"] for c in since}
            if self._key is not None:
                cause, key_since = self._key
                since[cause] = min(since.get(cause, key_since), key_since)
                sources.setdefault(cause, []).append("key")
            return [Alarm(c, since[c], tuple(sources[c])) for c in sorted(since)]


class KeyCheck:
    """Reads the key's ``limit_remaining`` once an hour and tells the alarms: below the floor is
    ``credits``, a 401 is ``key_rejected``. An answer that says nothing about the key (a
    transport error, a 5xx, a body without the field) keeps the state it had."""

    def __init__(
        self,
        *,
        key: str,
        alarms: OutageAlarms,
        clock: Clock,
        http: httpx.Client | None = None,
        floor_usd: float = DEFAULT_FLOOR_USD,
        every: timedelta = KEY_CHECK_EVERY,
    ) -> None:
        self._key = key
        self._alarms = alarms
        self._clock = clock
        self._http = http or httpx.Client(timeout=KEY_CHECK_TIMEOUT_S)
        self.floor_usd = floor_usd
        self._every = every
        self._next_at: datetime | None = None

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None, *, key: str, alarms: OutageAlarms, clock: Clock
    ) -> KeyCheck:
        source = os.environ if env is None else env
        try:
            floor = float(source.get(KEY_FLOOR_ENV, DEFAULT_FLOOR_USD))
        except ValueError:
            floor = DEFAULT_FLOOR_USD
        return cls(key=key, alarms=alarms, clock=clock, floor_usd=floor)

    def run_if_due(self) -> None:
        now = self._clock.now()
        if self._next_at is not None and now < self._next_at:
            return
        self._next_at = now + self._every
        self.check()

    def check(self) -> None:
        try:
            response = self._http.get(
                OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {self._key}"}
            )
        except httpx.HTTPError as exc:
            log.warning("OpenRouter key check failed: %s", type(exc).__name__)
            return
        if response.status_code == 401:
            self._alarms.set_key_state("key_rejected")
        elif response.status_code == 200:
            self._read_balance(response)
        else:
            log.warning("OpenRouter key check answered HTTP %d", response.status_code)

    def _read_balance(self, response: httpx.Response) -> None:
        try:
            data: Any = response.json()["data"]
            remaining = data["limit_remaining"]
        except (ValueError, KeyError, TypeError):
            log.warning("OpenRouter key check: no limit_remaining in the answer")
            return
        if remaining is None:
            self._alarms.set_key_state(None)  # a key with no limit has no balance to run out
        elif isinstance(remaining, int | float) and not isinstance(remaining, bool):
            low = remaining < self.floor_usd
            self._alarms.set_key_state("credits" if low else None)
        else:
            log.warning("OpenRouter key check: limit_remaining is not a number")
