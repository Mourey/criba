"""The scheduler's time-driven jobs: Stalled follow-ups, Abandoned, and the two flags.

Phase 1 detailed spec section 4.1 and section 5. Every job takes `now` and reads
the record, so running them twice, or after a long jump of the simulator clock,
is safe. Messaging the Candidate is an injected callable (`SendNudge`);
`criba.channel.scheduling.NudgeSender` implements it with the channel's sender.

Stalled ladder, measured from the Candidate's last message (or the Application's
start or last Reopen if they have not written since):

    1 hour     nudge        repeats the pending question
    20 hours   last_nudge   last free-form message inside WhatsApp's 24-hour window
    48 hours   template     approved template ("Want to pick up where you left off?")
    7 days     Abandoned    no message

At most 3 nudges; none in quiet hours, to an Opted-out Candidate, while a
human holds the chat (Takeover), or while we owe the Candidate a reply (an owed turn, ADR
0073: the silence is ours, so nothing counts towards Stalled or Abandoned).
`nudges_sent` is the ladder position: when the clock jumps past a step (a long outage, or the
simulator), the latest due step is sent and the earlier ones are not made up.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from criba.db import repo
from criba.db.connection import iso, parse_iso, transaction
from criba.scheduler.quiet import candidate_tz, is_quiet_hours

log = logging.getLogger(__name__)

NudgeKind = Literal["nudge", "last_nudge", "template"]

ABANDON_AFTER = timedelta(days=7)
QUALIFIED_WAITING_AFTER = timedelta(days=14)
CLAIMED_IDLE_AFTER = repo.CLAIM_IDLE_AFTER
STALLED_AFTER = timedelta(hours=1)
STALLED_TEMPLATE = "stalled_resume"


@dataclass(frozen=True, slots=True)
class NudgeStep:
    number: int
    after: timedelta
    kind: NudgeKind


NUDGE_LADDER: tuple[NudgeStep, ...] = (
    NudgeStep(1, timedelta(hours=1), "nudge"),
    NudgeStep(2, timedelta(hours=20), "last_nudge"),
    NudgeStep(3, timedelta(hours=48), "template"),
)


@dataclass(frozen=True, slots=True)
class NudgeRequest:
    """Ask the sender to message a stalled Candidate.

    `kind="nudge"` and `"last_nudge"` are free-form messages that repeat the
    pending question (the second adds "we'll keep your place"). `"template"` is
    the approved pick-up template named `template` (language chosen by the
    sender from `language`). The sender applies its own 24-hour-window and
    template guard and may still refuse.
    """

    application_id: str
    candidate_id: str
    channel: str
    language: str | None
    stage: str
    step: int
    kind: NudgeKind
    template: str | None


# (conn, request) -> True when the message went out (or was logged as sent),
# False when the sender declined: the step is then retried on the next run.
SendNudge = Callable[[sqlite3.Connection, NudgeRequest], bool]
# (conn, application_id) -> anything. Called once an Application is Abandoned.
OnAbandoned = Callable[[sqlite3.Connection, str], object]


@dataclass(slots=True)
class StalledReport:
    stalled_marked: int = 0
    nudges_sent: int = 0
    nudges_held_quiet: int = 0
    nudges_declined: int = 0
    abandoned: int = 0
    reset: int = 0
    errors: int = 0


@dataclass(slots=True)
class FlagReport:
    qualified_waiting_set: int = 0
    claimed_idle_set: int = 0
    cleared: int = 0


class _Declined(Exception):
    """Raised inside a transaction to roll it back when the sender declines."""


def silent_since(app: dict) -> datetime:
    """The instant the Candidate's silence is measured from."""
    candidates = [parse_iso(app["created_at"])]
    for column in ("last_inbound_at", "last_reopened_at"):
        if app[column]:
            candidates.append(parse_iso(app[column]))
    return max(candidates)


def run_stalled(
    conn: sqlite3.Connection,
    now: datetime,
    send_nudge: SendNudge,
    on_abandoned: OnAbandoned | None = None,
) -> StalledReport:
    report = StalledReport()
    ids = repo.screening_application_ids(conn)
    for application_id in ids:
        try:
            _stalled_one(conn, application_id, now, send_nudge, on_abandoned, report)
        except Exception:
            log.exception("stalled job failed for %s", application_id)
            report.errors += 1
    return report


def _stalled_one(
    conn: sqlite3.Connection,
    application_id: str,
    now: datetime,
    send_nudge: SendNudge,
    on_abandoned: OnAbandoned | None,
    report: StalledReport,
) -> None:
    app = repo.require_application(conn, application_id)
    if app["control"] == "human" or repo.agent_on_hold(conn, application_id):
        # A Recruiter holds the chat (Takeover), or the Recruiters have it after unreadable
        # messages (ADR 0063); follow-up is theirs.
        return
    if repo.reply_owed(conn, application_id):
        # The Candidate is waiting on us (ADR 0073): no nudge, and no Abandoned.
        return
    base = silent_since(app)
    silence = now - base

    if app["stalled_since"] and parse_iso(app["stalled_since"]) < base:
        # The Candidate wrote since we marked them Stalled: start the ladder again.
        repo.reset_stalled(conn, application_id)
        app["stalled_since"], app["nudges_sent"] = None, 0
        report.reset += 1

    if silence >= ABANDON_AFTER:
        repo.close_application(
            conn,
            application_id,
            "abandoned",
            actor="system",
            data={"nudges_sent": app["nudges_sent"]},
            now=now,
        )
        report.abandoned += 1
        if on_abandoned is not None:
            try:
                on_abandoned(conn, application_id)
            except Exception:
                log.exception("summary after Abandoned failed for %s", application_id)
        return

    if silence < STALLED_AFTER:
        return

    if app["stalled_since"] is None:
        stalled_at = base + STALLED_AFTER
        with transaction(conn):
            repo.set_stalled_since(conn, application_id, iso(stalled_at))
            repo.add_event(
                conn,
                candidate_id=app["candidate_id"],
                application_id=application_id,
                kind="stalled",
                actor="system",
                data={"stage": app["stage"]},
                now=now,
            )
        report.stalled_marked += 1

    due = [s for s in NUDGE_LADDER if silence >= s.after]
    step = due[-1] if due else None
    if step is None or step.number <= app["nudges_sent"]:
        return
    candidate = repo.get_candidate(conn, app["candidate_id"])
    if candidate is None or candidate["opted_out"]:
        return
    if is_quiet_hours(now, candidate_tz(conn, app)):
        report.nudges_held_quiet += 1
        return

    request = NudgeRequest(
        application_id=application_id,
        candidate_id=app["candidate_id"],
        channel=app["channel"],
        language=app["language"] or candidate["language"],
        stage=app["stage"],
        step=step.number,
        kind=step.kind,
        template=STALLED_TEMPLATE if step.kind == "template" else None,
    )
    try:
        # State and send commit together: a sender that declines or fails leaves
        # nothing behind, and the step is tried again on the next run.
        with transaction(conn):
            repo.set_nudges_sent(conn, application_id, step.number)
            repo.add_event(
                conn,
                candidate_id=app["candidate_id"],
                application_id=application_id,
                kind="nudge_sent",
                actor="system",
                data={"step": step.number, "kind": step.kind, "stage": app["stage"]},
                now=now,
            )
            if not send_nudge(conn, request):
                raise _Declined
    except _Declined:
        report.nudges_declined += 1
        return
    report.nudges_sent += 1


def run_flags(conn: sqlite3.Connection, now: datetime) -> FlagReport:
    """Set Qualified, waiting (14 days unclaimed) and Claimed, idle (7 days); clear stale ones."""
    report = FlagReport()
    waiting_cutoff = iso(now - QUALIFIED_WAITING_AFTER)
    idle_cutoff = iso(now - CLAIMED_IDLE_AFTER)
    with transaction(conn):
        waiting = repo.qualified_waiting_due(conn, waiting_cutoff)
        for row in waiting:
            repo.set_qualified_waiting(conn, row["id"])
            repo.add_event(
                conn,
                candidate_id=row["candidate_id"],
                application_id=row["id"],
                kind="flag_qualified_waiting",
                actor="system",
                data={"qualified_at": row["qualified_at"]},
                now=now,
            )
        report.qualified_waiting_set = len(waiting)

        idle = repo.claimed_idle_due(conn, idle_cutoff)
        for row in idle:
            repo.set_claimed_idle(conn, row["id"])
            repo.add_event(
                conn,
                candidate_id=row["candidate_id"],
                application_id=row["id"],
                kind="flag_claimed_idle",
                actor="system",
                data={"user_id": row["claim_user_id"], "last_action_at": row["last_action"]},
                now=now,
            )
        report.claimed_idle_set = len(idle)

        # Flags that no longer apply: waiting ends when claimed, Approved or closed;
        # idle ends when the Claim is released or the Application closes.
        report.cleared = repo.clear_stale_flags(conn)
    return report
