"""Runtime context shared by every router: settings, clock, services, database access.

The integrator either sets ``app.state.api_context = ApiContext(...)`` (to inject the
other lanes' services and the shared clock) or does nothing, in which case the first
request builds one from the environment.

Environment:

- ``CRIBA_DB_PATH``            SQLite file (default ``/data/criba.db``).
- ``CRIBA_ENV``                ``sim`` turns on the clock endpoints and the ``SimClock``.
- ``CRIBA_BOOKING_SECRET_FILE``  HMAC key for booking links, shared with the agent tools
  (``criba.tools.booking_secret``: else derived from ``CRIBA_TOOLS_SECRET_FILE``). Neither
  set: a random per-process key, with a warning, so links die on restart and the tool's
  links fail. Compose sets it.
- ``CRIBA_PUBLIC_BASE_URL``    Origin used in booking links (default ``http://localhost:8080``).
- ``CRIBA_COOKIE_SECURE``      ``1`` to mark the session cookie Secure (TLS in front).
- ``CRIBA_SESSION_TTL_HOURS``  Session lifetime (default 8).
- ``CRIBA_BOOKING_LINK_DAYS``  Booking link lifetime (default 7), for the agent tools' link and
  the Recruiter link alike; read in ``criba.tools.booking.booking_link_ttl``.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import FastAPI, Request

from criba.api.clock import SimClock
from criba.api.services import Services
from criba.db.connection import connect
from criba.screening.clock import Clock, SystemClock
from criba.tools.booking import booking_link_ttl, booking_secret

log = logging.getLogger("criba.api")

MIN_SECRET_LENGTH = 16


@dataclass(frozen=True)
class ApiSettings:
    db_path: str = "/data/criba.db"
    env: str = ""
    booking_secret: bytes = b""
    session_ttl: timedelta = timedelta(hours=8)
    cookie_secure: bool = False
    public_base_url: str = "http://localhost:8080"
    booking_link_ttl: timedelta = timedelta(days=7)
    # Server-sent events: poll the database, close after a bounded lifetime (the browser's
    # EventSource reconnects with Last-Event-ID), and send a comment line as a heartbeat.
    sse_poll_seconds: float = 0.5
    sse_max_seconds: float = 300.0
    sse_heartbeat_seconds: float = 15.0

    @property
    def is_sim(self) -> bool:
        return self.env == "sim"

    @property
    def usage_environment(self) -> str:
        """The ledger's ``environment`` value for this deployment."""
        return self.env if self.env in ("sim", "test") else "prod"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ApiSettings:
        env = os.environ if environ is None else environ
        secret = _load_secret(env)
        return cls(
            db_path=env.get("CRIBA_DB_PATH", "/data/criba.db"),
            env=env.get("CRIBA_ENV", "").strip().lower(),
            booking_secret=secret,
            session_ttl=timedelta(hours=float(env.get("CRIBA_SESSION_TTL_HOURS", "8"))),
            cookie_secure=env.get("CRIBA_COOKIE_SECURE", "").strip().lower() in ("1", "true"),
            public_base_url=env.get("CRIBA_PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/"),
            booking_link_ttl=booking_link_ttl(env),
        )


def _load_secret(env: Mapping[str, str]) -> bytes:
    """The booking-link key: the same one the agent tools sign with."""
    try:
        secret = booking_secret(env)
    except (RuntimeError, OSError):
        secret = b""
    if secret:
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"the booking secret must be at least {MIN_SECRET_LENGTH} bytes")
        return secret
    log.warning(
        "no booking secret (CRIBA_BOOKING_SECRET_FILE or CRIBA_TOOLS_SECRET_FILE): using a "
        "random per-process key; booking links will not survive a restart"
    )
    return secrets.token_bytes(32)


class LoginThrottle:
    """In-memory brake on password guessing: N failures per key within a window."""

    def __init__(self, limit: int = 5, window_seconds: float = 900.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        self._failures[key] = recent
        return recent

    def blocked(self, key: str, now: float) -> bool:
        with self._lock:
            return len(self._recent(key, now)) >= self.limit

    def fail(self, key: str, now: float) -> None:
        with self._lock:
            self._recent(key, now).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


@dataclass
class ApiContext:
    settings: ApiSettings
    clock: Clock
    services: Services
    # Session expiry follows the wall clock, never the sim clock: advancing the simulated
    # clock by a week must not log everyone out.
    session_clock: Clock = field(default_factory=SystemClock)
    # Lane W2-C's "run every scheduler job now due"; returns how many jobs ran.
    run_due_jobs: Callable[[], int] | None = None
    login_throttle: LoginThrottle = field(default_factory=LoginThrottle)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ApiContext:
        settings = ApiSettings.from_env(environ)
        clock: Clock = SimClock() if settings.is_sim else SystemClock()
        return cls(settings=settings, clock=clock, services=Services.defaults())

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def connect(self) -> sqlite3.Connection:
        return connect(self.settings.db_path)


_build_lock = threading.Lock()


def install_context(app: FastAPI, context: ApiContext) -> None:
    """Attach the context to the FastAPI app (``app.state.api_context``)."""
    app.state.api_context = context


def get_context(request: Request) -> ApiContext:
    """FastAPI dependency: the app's context, built from the environment on first use."""
    state = request.app.state
    context = getattr(state, "api_context", None)
    if context is None:
        with _build_lock:
            context = getattr(state, "api_context", None)
            if context is None:
                context = ApiContext.from_env()
                state.api_context = context
    return context
