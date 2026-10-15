"""Outbound sender (spec 002 task 19): every message we send goes through here.

- **Message log.** Each outbound message is stored verbatim before delivery.
- **Quiet hours** (22:00-09:00, Candidate's local time, the rule in
  :mod:`criba.scheduler.quiet`) refuse messages we start (``proactive=True``: nudges, the
  turn after Approval) on both channels. Replies to a Candidate who just wrote are never
  refused.
- **WhatsApp 24-hour window.** Outside it only a template may be sent. Web chat has no
  window.
- **Opt-out.** Nothing proactive reaches an Opted-out Candidate.
- **Pending choice (Q40).** One at a time: an interactive message's options stay live
  until the agent sends anything newer; :func:`check_tap` refuses superseded taps.

The sender never holds or retries anything. In quiet hours it answers ``not_now`` and
sends nothing; holding the action until 09:00 local and retrying it belongs to the
scheduler (:meth:`criba.scheduler.Scheduler.submit_action`), the one place that holds.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from criba.channel.hours import inside_whatsapp_window
from criba.db import repo
from criba.scheduler.quiet import candidate_tz, is_quiet_hours
from criba.screening.clock import Clock

log = logging.getLogger(__name__)

FALLBACK_TEXT = {
    "es": "Dame un momento, por favor. En seguida sigo contigo.",
    "en": "Give me a moment, please. I'll be right with you.",
}


def fallback_text(language: str | None) -> str:
    return FALLBACK_TEXT["en" if language == "en" else "es"]


class OptionExpired(Exception):
    """The tapped options were superseded by a newer agent message."""


class InvalidTap(Exception):
    """The tap names a message or option that was never offered to this Candidate."""


@dataclass
class SendResult:
    """``sent``; ``not_now`` (quiet hours: may go later, the scheduler decides when);
    or ``blocked`` (opted out, or outside WhatsApp's 24-hour window without a template)."""

    status: Literal["sent", "not_now", "blocked"]
    message: dict[str, Any] | None = None
    reason: str | None = None


class ChannelAdapter(Protocol):
    def deliver(self, message: dict[str, Any]) -> None: ...


class WebchatAdapter:
    """Web chat clients poll ``GET .../messages``; storing the message is delivery."""

    def deliver(self, message: dict[str, Any]) -> None:
        return None


class VoiceAdapter:
    """Voice calls (ADR 0059): the reply is spoken from the turn's stream, and the voice page
    reads the transcript with ``GET .../messages``; storing the message is all there is."""

    def deliver(self, message: dict[str, Any]) -> None:
        return None


@dataclass
class WhatsAppStubAdapter:
    """Stand-in for the WhatsApp Cloud API (ADR 0027): records what would be sent.

    It sends text, buttons and templates only. The ``.ics`` interview invite needs a
    document message, which arrives with the real adapter; until then ``book_interview``
    reports ``ics_sent: false`` on WhatsApp (see :mod:`criba.tools.booking`)."""

    outbox: list[dict[str, Any]] = field(default_factory=list)

    def deliver(self, message: dict[str, Any]) -> None:
        self.outbox.append(message)


class Sender:
    def __init__(self, clock: Clock, adapters: dict[str, ChannelAdapter] | None = None) -> None:
        self.clock = clock
        self.adapters: dict[str, ChannelAdapter] = adapters or {
            "webchat": WebchatAdapter(),
            "whatsapp": WhatsAppStubAdapter(),
            "voice": VoiceAdapter(),
        }

    def check(
        self,
        conn: sqlite3.Connection,
        application: dict[str, Any],
        *,
        proactive: bool,
        template: bool,
        quiet_hours_exempt: bool = False,
    ) -> SendResult | None:
        """The guard alone: a ``not_now`` or ``blocked`` result, or None if sending is allowed.

        ``quiet_hours_exempt`` lets the caller send in quiet hours: the router's answer for the
        Approval message to an active Candidate (ADR 0046). It never lifts the opt-out."""
        now = self.clock.now()
        if proactive:
            candidate = repo.get_candidate(conn, application["candidate_id"])
            if candidate and candidate["opted_out"]:
                return SendResult("blocked", reason="opted_out")
            tz = candidate_tz(conn, application)
            if is_quiet_hours(now, tz) and not quiet_hours_exempt:
                return SendResult("not_now", reason="quiet_hours")
        if (
            application["channel"] == "whatsapp"
            and not template
            and not inside_whatsapp_window(application, now)
        ):
            return SendResult("blocked", reason="outside_window")
        return None

    def send(
        self,
        conn: sqlite3.Connection,
        application: dict[str, Any],
        *,
        text: str,
        channel: str,
        sender: Literal["agent", "human", "system"] = "agent",
        interactive: dict[str, Any] | None = None,
        template_name: str | None = None,
        run_id: str | None = None,
        sender_user_id: str | None = None,
        proactive: bool = False,
        modality: Literal["typed", "spoken"] = "typed",
    ) -> SendResult:
        """Store and deliver one outbound message, unless a guard refuses it. ``modality`` is
        ``spoken`` for a reply to a voice call's turn (the guards are the same). ``channel`` is
        required: the channel of the inbound message a reply answers (``turn.channel``), or, for
        anything the Candidate did not just prompt, the Application's own (``application
        ["channel"]``). An Application keeps its channel; a message and its reply take the
        inbound's (ADR 0070)."""
        guard = self.check(
            conn, application, proactive=proactive, template=template_name is not None
        )
        if guard is not None:
            log.info("outbound %s for %s: %s", guard.status, application["id"], guard.reason)
            return guard
        now = self.clock.now()
        if template_name:
            content_type = "template"
        elif interactive:
            content_type = "interactive"
        else:
            content_type = "text"
        message, _ = repo.add_message(
            conn,
            candidate_id=application["candidate_id"],
            application_id=application["id"],
            channel=channel,
            direction="outbound",
            sender=sender,
            body=text,
            sender_user_id=sender_user_id,
            content_type=content_type,
            interactive=interactive,
            template_name=template_name,
            run_id=run_id,
            modality=modality,
            now=now,
        )
        if template_name:
            repo.add_event(
                conn,
                candidate_id=application["candidate_id"],
                application_id=application["id"],
                kind="template_sent",
                actor="system",
                data={"template": template_name, "message_id": message["id"]},
                now=now,
            )
        adapter = self.adapters.get(channel)
        if adapter is not None:
            adapter.deliver(message)
        return SendResult("sent", message=message)

    def reply_without_application(
        self,
        conn: sqlite3.Connection,
        candidate: dict[str, Any],
        *,
        channel: str,
        text: str,
        sender: Literal["agent", "system"] = "system",
        modality: Literal["typed", "spoken"] = "typed",
    ) -> SendResult:
        """Answer a message the Candidate just sent when they have no open Application
        (the opt-out confirmation, Q42).

        The message is stored with ``application_id`` NULL and delivered. No guard applies:
        the Candidate wrote a moment ago, so the WhatsApp window is open, and a reply to
        their own message is never held for quiet hours or blocked by an opt-out.
        """
        message, _ = repo.add_message(
            conn,
            candidate_id=candidate["id"],
            application_id=None,
            channel=channel,
            direction="outbound",
            sender=sender,
            body=text,
            modality=modality,
            now=self.clock.now(),
        )
        adapter = self.adapters.get(channel)
        if adapter is not None:
            adapter.deliver(message)
        return SendResult("sent", message=message)


def check_tap(
    conn: sqlite3.Connection, candidate_id: str, in_reply_to: int, option_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a button tap. Returns ``(interactive message, option)``.

    Raises :class:`InvalidTap` for a message or option never offered to this Candidate,
    and :class:`OptionExpired` when a newer agent message has superseded the options.
    """
    row = repo.interactive_outbound_message(conn, in_reply_to, candidate_id)
    if row is None:
        raise InvalidTap("in_reply_to is not an interactive message in this conversation")
    message = {**dict(row), "interactive": json.loads(row["interactive"])}
    option = next((o for o in message["interactive"]["options"] if o["id"] == option_id), None)
    if option is None:
        raise InvalidTap("option_id is not one of that message's options")
    if repo.agent_message_exists_after(conn, candidate_id, in_reply_to):
        raise OptionExpired("the options were superseded by a newer message")
    return message, option
