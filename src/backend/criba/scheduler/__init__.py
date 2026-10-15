"""Scheduler (spec 002 task 20): Stalled follow-ups, Abandoned, the Qualified-waiting
and Claimed-idle flags, and the quiet-hours deferral of router actions (Q41).

Entry points: `Scheduler.run_due_jobs()` and `running(scheduler)` (a lifespan helper).
"""

from criba.scheduler.active import ACTIVE_CANDIDATE_WINDOW, candidate_recently_active
from criba.scheduler.deferral import (
    ACTION_APPROVED,
    DeferralReport,
    DeferredActions,
    Dispatch,
    HeldAction,
)
from criba.scheduler.jobs import (
    ABANDON_AFTER,
    NUDGE_LADDER,
    QUALIFIED_WAITING_AFTER,
    STALLED_TEMPLATE,
    FlagReport,
    NudgeRequest,
    SendNudge,
    StalledReport,
    run_flags,
    run_stalled,
)
from criba.scheduler.quiet import candidate_tz, is_quiet_hours, quiet_hours_end
from criba.scheduler.service import RunReport, Scheduler, running

__all__ = [
    "ABANDON_AFTER",
    "ACTION_APPROVED",
    "ACTIVE_CANDIDATE_WINDOW",
    "NUDGE_LADDER",
    "QUALIFIED_WAITING_AFTER",
    "STALLED_TEMPLATE",
    "DeferralReport",
    "DeferredActions",
    "Dispatch",
    "FlagReport",
    "HeldAction",
    "NudgeRequest",
    "RunReport",
    "Scheduler",
    "SendNudge",
    "StalledReport",
    "candidate_recently_active",
    "candidate_tz",
    "is_quiet_hours",
    "quiet_hours_end",
    "run_flags",
    "run_stalled",
    "running",
]
