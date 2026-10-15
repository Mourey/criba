"""Quiet-hours deferral of router actions (Q41).

A router action that would message the Candidate during quiet hours is held and
run when quiet hours end. This is the one place that holds: the channel only
answers "not now". Approval goes to `Scheduler.submit_action` (not to the channel
directly); the scheduler's `run_due_jobs` calls `run_due`, and the dispatch is
`criba.channel.scheduling.dispatcher`, which raises on a "not now" so the action
is retried. The first use is the action after Approval (`ACTION_APPROVED`).

One exception (ADR 0046): the `approved` action skips quiet hours when the Candidate wrote
within `ACTIVE_CANDIDATE_WINDOW` (`criba.scheduler.active`). `submit` dispatches it at once, and
`run_due` dispatches an already held one on its next pass once the Candidate has written.
No other action is exempt.

Held actions live in the `held_actions` table, so a restart keeps them. A held
row is only "run action X on Application Y not before T": the dispatched action
re-reads the record, so nothing stale is acted on, and a closed Application or an
Opted-out Candidate is dropped here before dispatch.

Row status carries the guarantees:

- `held`: waiting. At most one per (Application, action).
- `dispatched`: the dispatch returned. A dispatched row is never run again.
- `dropped`: the Application closed or the Candidate opted out before dispatch.
- `failed`: `MAX_ATTEMPTS` dispatches raised.

An attempt is claimed (`attempts` incremented, compare-and-set on the old value)
before the dispatch runs, so two runners cannot dispatch the same attempt, and a
crash mid-dispatch still counts against the limit.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from criba.db import repo
from criba.db.connection import iso, parse_iso
from criba.scheduler.active import candidate_recently_active
from criba.scheduler.quiet import candidate_tz, is_quiet_hours, quiet_hours_end
from criba.screening.clock import Clock

log = logging.getLogger(__name__)

ACTION_APPROVED = "approved"
MAX_ATTEMPTS = 3


def skips_quiet_hours(action: str, application: dict[str, Any], now: datetime) -> bool:
    """True for the one action that ignores quiet hours: `approved` for an active Candidate."""
    return action == ACTION_APPROVED and candidate_recently_active(application, now)


# (conn, application_id, action) -> anything; raise to retry. See criba.channel.scheduling.
Dispatch = Callable[[sqlite3.Connection, str, str], object]


@dataclass(frozen=True, slots=True)
class HeldAction:
    application_id: str
    action: str
    held_at: datetime
    run_at: datetime
    attempts: int = 0
    id: int = 0
    status: str = "held"
    last_error: str | None = None


@dataclass(slots=True)
class DeferralReport:
    dispatched: int = 0
    dropped: int = 0
    rescheduled: int = 0
    failed: int = 0


def _held_from_row(row: sqlite3.Row) -> HeldAction:
    return HeldAction(
        application_id=row["application_id"],
        action=row["action"],
        held_at=parse_iso(row["created_at"]),
        run_at=parse_iso(row["not_before"]),
        attempts=row["attempts"],
        id=row["id"],
        status=row["status"],
        last_error=row["last_error"],
    )


class DeferredActions:
    def __init__(self, dispatch: Dispatch, clock: Clock) -> None:
        self._dispatch = dispatch
        self._clock = clock

    def submit(
        self,
        conn: sqlite3.Connection,
        application_id: str,
        action: str,
        *,
        messages_candidate: bool = True,
    ) -> Literal["dispatched", "deferred"]:
        """Dispatch now, or hold until quiet hours end if the action messages the Candidate.

        Submitting an action that is already held changes nothing.
        """
        now = self._clock.now()
        if messages_candidate:
            app = repo.require_application(conn, application_id)
            tz = candidate_tz(conn, app)
            if is_quiet_hours(now, tz) and not skips_quiet_hours(action, app, now):
                repo.hold_action(
                    conn, application_id, action, iso(quiet_hours_end(now, tz)), iso(now)
                )
                return "deferred"
        self._dispatch(conn, application_id, action)
        return "dispatched"

    def pending(self, conn: sqlite3.Connection) -> list[HeldAction]:
        """The rows still waiting (`held`), soonest first."""
        rows = repo.pending_held_actions(conn)
        return [_held_from_row(row) for row in rows]

    def history(self, conn: sqlite3.Connection, application_id: str) -> list[HeldAction]:
        """Every row for an Application, oldest first, whatever its status."""
        rows = repo.held_action_history(conn, application_id)
        return [_held_from_row(row) for row in rows]

    def run_due(self, conn: sqlite3.Connection) -> DeferralReport:
        """Run every held action whose quiet hours have ended, and any `approved` action whose
        Candidate has written within `ACTIVE_CANDIDATE_WINDOW` (ADR 0046)."""
        now = self._clock.now()
        report = DeferralReport()
        waiting = repo.waiting_held_actions(conn)
        for row in waiting:
            held = _held_from_row(row)
            app = repo.get_application(conn, held.application_id)
            active = app is not None and skips_quiet_hours(held.action, app, now)
            if held.run_at > now and not active:
                continue  # still in quiet hours
            candidate = repo.get_candidate(conn, app["candidate_id"]) if app else None
            if (
                app is None
                or app["status"] != "open"
                or candidate is None
                or candidate["opted_out"]
            ):
                if self._finish(conn, held, "dropped", now):
                    report.dropped += 1
                continue
            tz = candidate_tz(conn, app)
            if is_quiet_hours(now, tz) and not active:
                # The Candidate's Zone changed since the action was held.
                repo.reschedule_held_action(conn, held.id, iso(quiet_hours_end(now, tz)), iso(now))
                report.rescheduled += 1
                continue
            if not self._claim(conn, held, now):
                continue  # another runner got it
            attempts = held.attempts + 1
            try:
                self._dispatch(conn, held.application_id, held.action)
            except Exception as exc:
                log.exception("held action %s for %s failed", held.action, held.application_id)
                self._record_failure(conn, held, attempts, exc, now)
                report.failed += 1
                continue
            if self._finish(conn, held, "dispatched", now):
                report.dispatched += 1
        return report

    def _claim(self, conn: sqlite3.Connection, held: HeldAction, now: datetime) -> bool:
        return repo.claim_held_action(conn, held.id, held.attempts, iso(now))

    def _finish(
        self,
        conn: sqlite3.Connection,
        held: HeldAction,
        status: Literal["dispatched", "dropped"],
        now: datetime,
    ) -> bool:
        return repo.finish_held_action(conn, held.id, status, iso(now))

    def _record_failure(
        self,
        conn: sqlite3.Connection,
        held: HeldAction,
        attempts: int,
        exc: Exception,
        now: datetime,
    ) -> None:
        status = "failed" if attempts >= MAX_ATTEMPTS else "held"
        repo.record_held_action_failure(
            conn, held.id, status, f"{type(exc).__name__}: {exc}"[:500], iso(now)
        )
