"""Adapters between the scheduler (W2-C) and the channel (W2-B).

The scheduler owns time: it holds an action through quiet hours, dispatches it once and
retries it; it decides when a Stalled Candidate is nudged. The channel owns sending: it
answers whether a message may go out now, and sends. These adapters connect the two:

- :func:`dispatcher` is the scheduler's ``Dispatch``: a held action (``"approved"``)
  becomes :meth:`Channel.handle_event`. A ``not_now`` answer raises :class:`NotNow`, so
  the scheduler counts a failed attempt and retries on its next run (at most 3 attempts).
- :class:`NudgeSender` is the scheduler's ``SendNudge``: a :class:`NudgeRequest` becomes
  a proactive send with the synthetic copy from :mod:`criba.channel.copy`. It sends on the
  scheduler's own connection, inside the transaction that records the nudge, so the
  message and the ``nudge_sent`` event commit or roll back together.
- :func:`build_scheduler` wires both into a :class:`Scheduler`, with the channel's
  :meth:`Channel.resume_owed_turns` as its turn recovery (ADR 0073).

The scheduler depends on nothing here; the channel imports the scheduler, never the
other way round.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from criba.channel.copy import nudge_text
from criba.channel.router import Action
from criba.channel.service import Channel
from criba.db import repo
from criba.scheduler import Dispatch, NudgeRequest, Scheduler
from criba.scheduler.service import KeyCheckRun
from criba.summary import SummaryWriter

log = logging.getLogger(__name__)


class NotNow(RuntimeError):
    """The channel answered ``not_now`` for a dispatched action (quiet hours)."""


def dispatcher(channel: Channel) -> Dispatch:
    """The scheduler's ``Dispatch``: run a held router action through the channel."""

    def dispatch(conn: sqlite3.Connection, application_id: str, action: str) -> Action:
        result = channel.handle_event(application_id, action)
        if result.kind == "not_now":
            raise NotNow(f"{action} on {application_id}: {result.reason}")
        return result

    return dispatch


class NudgeSender:
    """The scheduler's ``SendNudge``: send a Stalled nudge through the channel's sender.

    Free-form nudges re-attach the pending choice (Q40), so a nudge that says "answer the
    last question" keeps its buttons live; the 48-hour step is the ``stalled_resume``
    template. Returns True only when the message went out; False (quiet hours, Opted out,
    outside WhatsApp's 24-hour window) makes the scheduler retry the step next run.
    """

    def __init__(self, channel: Channel) -> None:
        self.channel = channel

    def __call__(self, conn: sqlite3.Connection, request: NudgeRequest) -> bool:
        app = repo.require_application(conn, request.application_id)
        text = nudge_text(request.kind, request.language, app["country"])
        interactive = None
        if request.template is None:
            interactive = self.channel.router.pending_interactive(conn, app["id"])
        result = self.channel.sender.send(
            conn,
            app,
            text=text,
            channel=app["channel"],
            interactive=interactive,
            template_name=request.template,
            proactive=True,
        )
        if result.status != "sent":
            log.info("nudge %d for %s not sent: %s", request.step, app["id"], result.reason)
        return result.status == "sent"


def build_scheduler(
    channel: Channel,
    *,
    connect: Callable[[], sqlite3.Connection] | None = None,
    summary: SummaryWriter | None = None,
    key_check: KeyCheckRun | None = None,
) -> Scheduler:
    """A :class:`Scheduler` that dispatches held actions and nudges through ``channel``."""
    return Scheduler(
        connect=connect or channel.new_connection,
        clock=channel.clock,
        send_nudge=NudgeSender(channel),
        dispatch=dispatcher(channel),
        summary=summary,
        resume_turns=lambda startup: channel.resume_owed_turns(startup=startup),
        key_check=key_check,
    )
