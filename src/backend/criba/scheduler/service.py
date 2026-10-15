"""The scheduler service: one "run due jobs now" entry point, and a background loop.

    scheduler = Scheduler(
        connect=lambda: connect(db_path),
        clock=clock,
        send_nudge=NudgeSender(channel),    # criba.channel.scheduling
        dispatch=dispatcher(channel),       # criba.channel.scheduling
        summary=SummaryWriter(client, clock),
    )
    # or: criba.channel.scheduling.build_scheduler(channel, summary=...)
    scheduler.run_due_jobs()                # tests, and the sim clock endpoint (W2-D)
    async with running(scheduler, 60.0):    # app startup (integrator)
        ...

Time comes only from the `Clock`, so the simulator advances it and calls
`run_due_jobs` instead of waiting.

Owed turns (ADR 0073): `resume_turns` (the channel's `resume_owed_turns`) runs the owed turns
that are due again. `running` calls it every `TURN_RETRY_TICK_S` seconds on a loop of its own,
first with `startup=True` to resume what the last process left; `run_due_jobs` calls it too,
so the simulator clock reaches it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

from criba.jev.store import purge_signals, retention_days
from criba.scheduler.deferral import DeferralReport, DeferredActions, Dispatch
from criba.scheduler.jobs import FlagReport, SendNudge, StalledReport, run_flags, run_stalled
from criba.screening.clock import Clock
from criba.summary import SummaryWriter
from criba.tools.trace import purge_tool_calls

log = logging.getLogger(__name__)

# How often the turn recovery looks for owed turns that are due (ADR 0073). Its waits are
# seconds long, so it does not wait for the minute tick of the other jobs.
TURN_RETRY_TICK_S = 5.0

# (startup) -> the Applications whose owed turns were started again.
ResumeTurns = Callable[[bool], object]
# () -> anything. The OpenRouter key check: reads the balance when it is due (ADR 0078).
KeyCheckRun = Callable[[], object]


@dataclass(slots=True)
class RetentionReport:
    signals_purged: int = 0


@dataclass(slots=True)
class RunReport:
    deferred: DeferralReport = field(default_factory=DeferralReport)
    stalled: StalledReport = field(default_factory=StalledReport)
    flags: FlagReport = field(default_factory=FlagReport)
    retention: RetentionReport = field(default_factory=RetentionReport)

    def as_dict(self) -> dict[str, dict[str, int]]:
        names = ("deferred", "stalled", "flags", "retention")
        return {name: asdict(getattr(self, name)) for name in names}


class Scheduler:
    def __init__(
        self,
        *,
        connect: Callable[[], sqlite3.Connection],
        clock: Clock,
        send_nudge: SendNudge,
        dispatch: Dispatch,
        summary: SummaryWriter | None = None,
        resume_turns: ResumeTurns | None = None,
        key_check: KeyCheckRun | None = None,
    ) -> None:
        self._resume_turns = resume_turns
        self._key_check = key_check
        self._connect = connect
        self._clock = clock
        self._send_nudge = send_nudge
        self._summary = summary
        self.deferred = DeferredActions(dispatch, clock)
        self._run_lock = threading.Lock()

    def submit_action(
        self,
        conn: sqlite3.Connection,
        application_id: str,
        action: str,
        *,
        messages_candidate: bool = True,
    ) -> Literal["dispatched", "deferred"]:
        """For the router: run a router action now, or hold it past quiet hours (Q41)."""
        return self.deferred.submit(
            conn, application_id, action, messages_candidate=messages_candidate
        )

    def run_due_jobs(self, conn: sqlite3.Connection | None = None) -> RunReport:
        """Run everything that is due at the clock's `now`, once. Safe to call at any time.

        Held router actions first, then Stalled follow-ups and Abandoned, then the
        Qualified-waiting and Claimed-idle flags, the retention purges, the summary prose
        retries and the owed turns that are due (ADR 0073). Each phase is isolated: one that
        raises is logged, reports nothing, and the next one still runs. Opens its own
        connection unless one is given. Calls do not overlap: a second caller waits for the
        first.
        """
        with self._run_lock:
            own = conn is None
            db = self._connect() if conn is None else conn
            try:
                now = self._clock.now()
                report = RunReport()
                report.deferred = _phase(
                    "held actions", db, DeferralReport, lambda: self.deferred.run_due(db)
                )
                report.stalled = _phase(
                    "Stalled",
                    db,
                    StalledReport,
                    lambda: run_stalled(db, now, self._send_nudge, self._on_abandoned),
                )
                report.flags = _phase("flags", db, FlagReport, lambda: run_flags(db, now))
                report.retention = self._purge_signals(db, now)
                self._purge_tool_calls(db, now)
                self._retry_summaries(db)
                self.resume_turns()
                self._check_key()
                return report
            finally:
                if own:
                    db.close()

    @staticmethod
    def _purge_signals(conn: sqlite3.Connection, now: datetime) -> RetentionReport:
        """Delete message signals past the retention period (ADR 0051). A failure is logged and
        leaves the other jobs' results alone."""
        try:
            return RetentionReport(purge_signals(conn, now, retention_days()))
        except Exception:
            log.exception("purging message signals failed")
            return RetentionReport()

    @staticmethod
    def _purge_tool_calls(conn: sqlite3.Connection, now: datetime) -> None:
        """Delete tool-call trace rows past the same retention period (ADR 0069). The count is
        logged, not reported; a failure is logged and leaves the other jobs' results alone."""
        try:
            purged = purge_tool_calls(conn, now, retention_days())
            if purged:
                log.info("purged %d tool call rows", purged)
        except Exception:
            log.exception("purging tool calls failed")

    def _retry_summaries(self, conn: sqlite3.Connection) -> None:
        """Ask again for the summary prose that failed and is due for another try (ADR 0072).
        A failure is logged and leaves the other jobs' results alone."""
        if self._summary is None:
            return
        try:
            retried = self._summary.retry_due(conn)
            if retried:
                log.info("retried the summary prose of %d Applications", retried)
        except Exception:
            log.exception("retrying summary prose failed")
            _roll_back(conn)

    def _check_key(self) -> None:
        """Read the OpenRouter key's balance when it is due (ADR 0078). A failure is logged and
        leaves the other jobs' results alone."""
        if self._key_check is None:
            return
        try:
            self._key_check()
        except Exception:
            log.exception("key check failed")

    def resume_turns(self, *, startup: bool = False) -> None:
        """Start the owed turns that are due again (ADR 0073); at ``startup``, every one. A
        failure is logged and leaves the other jobs alone."""
        if self._resume_turns is None:
            return
        try:
            self._resume_turns(startup)
        except Exception:
            log.exception("resuming owed turns failed")

    async def turn_loop(self, interval_seconds: float = TURN_RETRY_TICK_S) -> None:
        """Resume every owed turn once (startup), then the due ones every ``interval_seconds``
        until cancelled."""
        startup = True
        while True:
            await asyncio.to_thread(self.resume_turns, startup=startup)
            startup = False
            await asyncio.sleep(interval_seconds)

    def _on_abandoned(self, conn: sqlite3.Connection, application_id: str) -> None:
        if self._summary is not None:
            self._summary.write(conn, application_id, trigger="finished")

    async def run_loop(self, interval_seconds: float = 60.0) -> None:
        """Call `run_due_jobs` every `interval_seconds` until cancelled."""
        while True:
            try:
                report = await asyncio.to_thread(self.run_due_jobs)
                log.debug("scheduler run: %s", report.as_dict())
            except Exception:
                log.exception("scheduler run failed")
            await asyncio.sleep(interval_seconds)


def _phase[R](
    name: str, conn: sqlite3.Connection, empty: Callable[[], R], run: Callable[[], R]
) -> R:
    """Run one phase of a scheduler tick. If it raises, log it, roll back what it left open, and
    report ``empty()`` so the phases after it still run."""
    try:
        return run()
    except Exception:
        log.exception("scheduler phase %s failed; the next phases still run", name)
        _roll_back(conn)
        return empty()


def _roll_back(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        try:
            conn.rollback()
        except sqlite3.Error:
            log.exception("rollback after a failed scheduler phase failed")


@asynccontextmanager
async def running(scheduler: Scheduler, interval_seconds: float = 60.0) -> AsyncIterator[Scheduler]:
    """Run the scheduler loop and the turn recovery loop for the duration of the block (FastAPI
    lifespan)."""
    tasks = [
        asyncio.create_task(scheduler.run_loop(interval_seconds), name="criba-scheduler"),
        asyncio.create_task(scheduler.turn_loop(), name="criba-turn-recovery"),
    ]
    try:
        yield scheduler
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
