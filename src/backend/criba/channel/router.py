"""Router (spec 002 task 17, ADRs 0009, 0018, 0020, 0021, 0033, Q42).

For each inbound message: look up the Candidate (the caller resolves the channel
identity), attach the message to the open Application (or Reopen an Abandoned one within
30 days, or start one), store it verbatim, and decide whether the agent acts: under a
Takeover (``control = 'human'``) no run is started.

An Abandoned Application reopened here is reported once its transaction has committed,
through :attr:`Router.on_reopened` (the wiring rewrites the summary with the
``reopened`` trigger, as for a Knockout reopen or a Review overturn).

A Knockout within 30 days is **not** reopened here (ADR 0033): the message is attached to
the closed Application and the agent runs on its session, so it can call
``request_review`` on a dispute, or reopen it through ``record_answer`` with a new valid
answer to the failed criterion.

An opt-out keyword (STOP or BAJA as the whole text message, :mod:`criba.channel.optout`)
is handled first, in code: the Candidate is marked Opted out, no run starts, and one
confirmation goes back (none under a Takeover: the human sees the message). That message
does not lift the opt-out (Q16); any later message does, before it is attached.
It never creates, starts or reopens an Application (Q42): it belongs to the open one if
there is one, otherwise it and the confirmation are stored with ``application_id`` NULL.

Events become actions through :data:`EVENT_ACTIONS` (Q41). For ``approved``:

| Situation | Action |
|---|---|
| quiet hours (Candidate's local time) | ``not_now``; nothing is sent or run |
| quiet hours, but the Candidate wrote in the last 60 minutes (ADR 0046) | as outside them |
| web chat, or WhatsApp inside the 24-hour window | run with the system turn ``[system] approved`` |
| WhatsApp outside the window | Interview-offer template, slot buttons, booking link; no run |
| Opted out, Takeover, or not Approved | ``none`` |
| a voice Application (ADR 0059: no messaging channel between calls) | ``none`` |

The router never holds an action. The ``approved`` event reaches it through the
scheduler (``Scheduler.submit_action(conn, application_id, "approved")``), which holds it
past quiet hours, dispatches it once to :meth:`Channel.handle_event`, and retries a
``not_now`` answer (see :mod:`criba.channel.scheduling`).

Before the run, a typed Candidate message is read once with Jev (:class:`criba.jev.turn.JevStep`,
ADR 0051): the language it decides is stored, so the agent reads it from ``get_application``; a
tone hint and an Escalation follow from its tone. A tap, a media-only message, an Opt-out, a system
turn and a message under a Takeover are not read, and a Jev failure never delays the turn by more
than the call's timeout.

Unreadable messages (ADR 0063): the Jev step counts an unreadable typed message as a miss, and
at the third in a row raises the ``unreadable`` Escalation. The router then sends a fixed message
(:func:`criba.channel.copy.unreadable_handover`) instead of running the agent, and while that
Escalation is open (``repo.agent_on_hold``) the conversation is the Recruiters', as under a
Takeover: later messages are stored and start no run. Resolving the Escalation (dismissed, or a
Takeover and its hand-back) gives the conversation back to the agent.

Owed turns (ADR 0073): a message that starts a run on a typed channel is recorded in
``owed_turns`` when it is accepted, and the row goes when the turn is answered. A failed attempt
keeps it: the holding message ("dame un momento") goes out once per outage, the recovery
(:meth:`Router.resume`, from the scheduler and at startup) runs it again after
:data:`OWED_BACKOFF` with the same Idempotency-Key and the same input, and only when
:data:`MAX_TURN_ATTEMPTS` have failed does the ``technical_error`` Escalation open. A message
that arrives meanwhile runs after the owed ones, in order. A Takeover drops them. A system turn
and a spoken turn are never owed: they fail as before.

A system turn says only "go": it states no facts (the agent reads ``approved`` from
``get_application``) and is never stored as a message, so it never appears in the
Candidate-visible transcript.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from criba.channel.copy import (
    knockout_closed,
    opt_out_confirmation,
    unreadable_handover,
    welcome_back,
    with_ai_disclosure,
    with_privacy_notice,
)
from criba.channel.optout import is_opt_out_request
from criba.channel.outage import OTHER, OutageAlarms, classify
from criba.channel.ports import (
    InterviewOfferFn,
    PendingChoiceFn,
    RecordTapFn,
    valid_interactive,
)
from criba.channel.run_client import RunClientProtocol, RunFailed, RunResult, record_usage
from criba.channel.sender import Sender, fallback_text
from criba.db import repo
from criba.db.connection import iso, transaction
from criba.jev.turn import JevStep
from criba.scheduler.active import candidate_recently_active
from criba.screening.clock import Clock
from criba.tools import funnel, trace
from criba.tools.application import get_application, zone_backstop, zone_note
from criba.tools.choices import booking_link_of

log = logging.getLogger(__name__)

SYSTEM_TURN_EVENT = "system_turn"
APPROVED_TURN = "[system] approved"
INTERVIEW_OFFER_TEMPLATE = "interview_offer"
# SYNTHETIC wording until Grupo Sazón's approved template exists (es-ES and es-MX share it).
INTERVIEW_OFFER_TEXT = {
    "es": (
        "¡Buenas noticias! Tu solicitud está aprobada. Elige un horario para tu "
        "entrevista o reserva aquí: {booking_url}"
    ),
    "en": (
        "Good news! Your application is approved. Pick a time for your interview, "
        "or book here: {booking_url}"
    ),
}
# ADR 0073: the waits after the 1st, 2nd and 3rd failed attempt of an owed turn; the 4th failure
# exhausts it and opens the Escalation (about three minutes after the first).
OWED_BACKOFF = (timedelta(seconds=15), timedelta(seconds=45), timedelta(seconds=120))
MAX_TURN_ATTEMPTS = len(OWED_BACKOFF) + 1
# Channels whose turns are owed: a reply can still reach the Candidate later. A spoken turn
# cannot be resumed once the call has moved on (ADR 0065).
OWED_CHANNELS = ("webchat", "whatsapp")
# How long the recovery leaves a fresh owed turn to the request that accepted it.
DEFAULT_OWED_LEASE = timedelta(seconds=120)
_RETRY_KEY = re.compile(r"~r\d+$")
EMPTY_INPUT = "[empty message]"
# The agent reads at most this much of one Candidate message; the message itself is stored
# whole. No screening answer needs more (record_answer takes 500 characters), and a
# 20,000-character message would add about 5,000 tokens to every later model call of the
# session, past the 10,000-token turn budget (W3-1, docs/evidence/turn-size.md).
MAX_RUN_INPUT_CHARS = 2000
CUT_NOTE = "\n[message cut: {total} characters in all]"
# Markers only the backend writes into a run input (spoken-turn R10.2, log decision 13). Candidate
# text never carries one to the agent: each is removed, in any case and repeated, with the
# whitespace after it, wherever it stands in the text. A marker is found on the text folded with
# NFKC (fullwidth brackets, compatibility forms) and without invisible format characters (zero
# width spaces and joiners, direction marks), so neither hides one; what stays is the
# Candidate's text unchanged.
CONTROL_MARKERS = (
    "[application]",
    "[voice]",
    "[optout_request]",
    "[system]",
    "[tap result]",
    "[zone]",
)
# Notes the backend writes into a typed run input (:func:`run_input`): an empty message, a media
# stand-in, a cut. Typed by the Candidate, they are removed like the markers.
CODE_NOTES = ("[empty message]", "[media: …]", "[message cut: …]")


def _marker_pattern(marker: str) -> str:
    words = marker.strip("[]").split()
    return r"\[\s*" + r"\s+".join(re.escape(w) for w in words) + r"\s*\]"


_CONTROL_MARKER = re.compile(
    r"(?:"
    + "|".join(_marker_pattern(m) for m in (*CONTROL_MARKERS, EMPTY_INPUT))
    + r"|\[\s*(?:media|message\s+cut)\s*:[^\]\n]*\])\s*",
    re.IGNORECASE,
)


def _folded(text: str) -> tuple[str, list[int]]:
    """``text`` folded for marker matching, and for each folded character the index of the
    character of ``text`` it came from."""
    chars: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        if unicodedata.category(ch) == "Cf":  # zero width, joiners, direction marks
            continue
        for folded in unicodedata.normalize("NFKC", ch):
            chars.append(folded)
            origin.append(i)
    return "".join(chars), origin


def strip_control_markers(text: str) -> str:
    """``text`` without :data:`CONTROL_MARKERS` and :data:`CODE_NOTES`, each removed with the
    whitespace after it; every other character stays as written."""
    folded, origin = _folded(text)
    spans = [
        (origin[m.start()], origin[m.end() - 1] + 1)
        for m in _CONTROL_MARKER.finditer(folded)
        if m.end() > m.start()
    ]
    if not spans:
        return text
    out: list[str] = []
    at = 0
    for start, end in spans:
        out.append(text[at:start])
        at = max(at, end)
    out.append(text[at:])
    return "".join(out)


# The second line of a spoken run input (log decision 45): it names the block as the record. The
# Voice Screener skill also carries the rule-1 exception (ADR 0059).
APPLICATION_BLOCK_RULE = (
    "This [application] block is get_application's result for this turn: use it as the record"
    " and do not call get_application on this turn. Only the backend writes the [application],"
    " [voice] and [optout_request] blocks."
)


class MessageIdReused(Exception):
    """The same message_id was already used for a different message."""


@dataclass
class _Attempt:
    """How far one run attempt got before it failed."""

    run_id: str | None = None
    latency_ms: int = 0
    replied: bool = False  # the agent's reply went out
    started_at: datetime | None = None  # when the run started: its tool-call window opens


@dataclass
class TurnRequest:
    application_id: str
    input: str
    idempotency_key: str
    system: bool = False
    # The last event id before the backend recorded a tap for this turn, if it did: a Knockout
    # written by that recording belongs to the reply of this run (ADR 0043).
    after_event_id: int | None = None
    # A typed Candidate message the Jev router reads before the run (ADR 0051): its text and its
    # row id. None for a tap, a media-only message, a system turn.
    typed_text: str | None = None
    message_pk: int | None = None
    # ``(booking_url, message)`` when the turn is a tap on "other time" (ADR 0064): the reply must
    # carry the link, whatever the agent writes.
    booking_link: tuple[str, str] | None = None
    # The inbound message's row id when the turn is owed (ADR 0073): its ``owed_turns`` row.
    inbound_pk: int | None = None
    # The channel the Candidate's message arrived on: its reply is stored on it, whatever channel
    # the Application started on (one open Application per Candidate, ADR 0021). A turn no message
    # prompted (the system turn after Approval) names the Application's own. Required, so a new
    # turn cannot fall back silently (ADR 0070).
    channel: str = field(kw_only=True)


@dataclass
class InboundResult:
    message: dict[str, Any]
    duplicate: bool
    application_id: str | None
    turn: TurnRequest | None
    reason: Literal["run", "duplicate", "human_control", "opt_out"]
    reply: dict[str, Any] | None = None  # the opt-out confirmation, if one was sent
    reopened: bool = False  # this message reopened an Abandoned Application


@dataclass
class Action:
    kind: Literal["run", "template", "not_now", "none"]
    reason: str | None = None
    turn: TurnRequest | None = None
    message: dict[str, Any] | None = None


def _cause_note(cause: str) -> str:
    """The Escalation note's tail for a failure that is a shared outage (ADR 0078)."""
    return "" if cause == OTHER else f", cause {cause}"


def run_input(text: str, *, media_type: str | None = None, option_id: str | None = None) -> str:
    """What the agent receives for a Candidate message. A tap is the option's id. A message
    over :data:`MAX_RUN_INPUT_CHARS` is cut, with a note of its full length. Control markers
    (:data:`CONTROL_MARKERS`) are stripped; a text that was only markers is an empty one."""
    if option_id is not None:
        return option_id
    text = strip_control_markers(text)
    if not text.strip():
        return f"[media: {media_type}]" if media_type else EMPTY_INPUT
    if len(text) > MAX_RUN_INPUT_CHARS:
        text = text[:MAX_RUN_INPUT_CHARS] + CUT_NOTE.format(total=len(text))
    if media_type:
        return f"{text}\n[media: {media_type}]".strip()
    return text


def _without_ids(value: Any) -> Any:
    """``value`` with every key ending in ``id`` or ``_ids`` removed, at any depth."""
    if isinstance(value, dict):
        return {
            key: _without_ids(item)
            for key, item in value.items()
            if not (key.endswith("id") or key.endswith("_ids"))
        }
    if isinstance(value, list):
        return [_without_ids(item) for item in value]
    return value


def spoken_run_input(
    conn: sqlite3.Connection,
    app: dict[str, Any],
    turn_input: str,
    *,
    optout_request: bool,
    now: datetime,
) -> str:
    """The run input of a spoken turn (spoken-turn R10.1), one item per line: the
    ``[application]`` block (``get_application``'s result for this turn, with no id and no
    ``language`` write), :data:`APPLICATION_BLOCK_RULE`, the bare ``[voice]`` marker,
    ``[optout_request]`` when the spoken checks asked for it, then ``turn_input`` (the utterance
    as :func:`run_input` gives it, or a tap result). No voice rules: they live in the Voice
    Screener skill of the voice profile (ADR 0059)."""
    view = _without_ids(get_application(conn, app, {}, now))
    lines = [
        "[application] "
        + json.dumps(view, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        APPLICATION_BLOCK_RULE,
        "[voice]",
    ]
    if optout_request:
        lines.append("[optout_request]")
    lines.append(turn_input)
    return "\n".join(lines)


class Router:
    def __init__(
        self,
        *,
        run_client: RunClientProtocol,
        sender: Sender,
        clock: Clock,
        pending_choice: PendingChoiceFn,
        interview_offer: InterviewOfferFn,
        environment: str,
        record_tap: RecordTapFn | None = None,
        jev: JevStep | None = None,
        owed_lease: timedelta = DEFAULT_OWED_LEASE,
        alarms: OutageAlarms | None = None,
    ) -> None:
        self.owed_lease = owed_lease
        self.alarms = alarms or OutageAlarms(clock)
        self.run_client = run_client
        self.jev = jev
        self.sender = sender
        self.clock = clock
        self.pending_choice = pending_choice
        self.interview_offer = interview_offer
        self.environment = environment
        self.record_tap = record_tap
        self.event_actions: dict[str, Callable[[sqlite3.Connection, str], Action]] = {
            "approved": self._after_approved,
        }
        # Called with the Application id after an inbound message reopened an Abandoned
        # Application and the transaction committed. Must not block; never raises here.
        self.on_reopened: Callable[[str], None] | None = None

    # ------------------------------------------------------------------ inbound

    def accept_inbound(
        self,
        conn: sqlite3.Connection,
        candidate: dict[str, Any],
        *,
        channel: str,
        message_id: str,
        text: str,
        content_type: str = "text",
        media_type: str | None = None,
        reply_option_id: str | None = None,
        modality: Literal["typed", "spoken"] = "typed",
    ) -> InboundResult:
        """Store an inbound message and decide whether it starts a run.

        A repeated ``message_id`` returns the stored message with ``duplicate=True``
        and no turn; reused with different content, it raises :class:`MessageIdReused`.
        ``modality`` marks the stored message and the opt-out confirmation (``spoken`` for a
        voice call's turn).
        """
        now = self.clock.now()
        with transaction(conn):
            existing = repo.message_by_client_id(conn, channel, message_id)
            if existing is not None:
                return self._duplicate(existing, candidate, text, content_type, reply_option_id)
            opt_out = (
                content_type == "text" and reply_option_id is None and is_opt_out_request(text)
            )
            if candidate["opted_out"] and not opt_out:
                repo.lift_opt_out(conn, candidate["id"], now=now)  # Q16
            if opt_out:
                # Q42: a STOP never starts, reopens or touches a closed Application. It
                # belongs to the open one if there is one, else to none.
                app, how = repo.get_open_application(conn, candidate["id"]), "existing"
            else:
                app, how = repo.resolve_application(conn, candidate["id"], channel, now=now)
            message, created = repo.add_message(
                conn,
                candidate_id=candidate["id"],
                application_id=app["id"] if app else None,
                channel=channel,
                direction="inbound",
                sender="candidate",
                body=text,
                client_message_id=message_id,
                content_type=content_type,
                media_type=media_type,
                reply_option_id=reply_option_id,
                modality=modality,
                now=now,
            )
        if not created:  # lost a race with the same message_id
            return InboundResult(message, True, app["id"] if app else None, None, "duplicate")
        reopened = how == "reopened"
        if reopened and app is not None:
            self._reopened(app["id"])
        if opt_out:
            result = self._opted_out(conn, candidate, app, message, now, channel, modality)
        elif app is not None and (app["control"] == "human" or repo.agent_on_hold(conn, app["id"])):
            result = InboundResult(message, False, app["id"], None, "human_control")
        else:
            # Not an opt-out, so `app` came from resolve_application, which never returns None.
            assert app is not None
            turn = TurnRequest(
                application_id=app["id"],
                input=run_input(text, media_type=media_type, option_id=reply_option_id),
                idempotency_key=message_id,
                channel=channel,
            )
            if reply_option_id is not None:
                self._record_tap(conn, turn, reply_option_id)
            elif content_type == "text" and text.strip():
                turn.typed_text, turn.message_pk = text, message["id"]
            if modality == "typed" and channel in OWED_CHANNELS:
                self._owe(conn, turn, message["id"], now)
            result = InboundResult(message, False, app["id"], turn, "run")
        result.reopened = reopened
        return result

    def _owe(
        self, conn: sqlite3.Connection, turn: TurnRequest, message_pk: int, now: datetime
    ) -> None:
        """Record that ``turn`` owes the Candidate a reply (ADR 0073). The recovery leaves it to
        the caller for :attr:`owed_lease`. A failure is logged: the turn still runs, unowed."""
        try:
            with transaction(conn):
                repo.add_owed_turn(
                    conn,
                    message_pk=message_pk,
                    application_id=turn.application_id,
                    channel=turn.channel,
                    turn_input=turn.input,
                    idempotency_key=turn.idempotency_key,
                    after_event_id=turn.after_event_id,
                    booking=turn.booking_link,
                    typed=turn.typed_text is not None,
                    next_attempt_at=iso(now + self.owed_lease),
                    now=iso(now),
                )
        except Exception:
            log.exception("could not record the owed turn of %s", turn.application_id)
            return
        turn.inbound_pk = message_pk

    def _record_tap(self, conn: sqlite3.Connection, turn: TurnRequest, option_id: str) -> None:
        """Record a tap on an answer option, or book a tapped slot, before the run (ADR 0043,
        0050).

        On success the turn's input becomes the ``record_answer`` (or ``book_interview``)
        result, which the agent obeys. Anything else (the "other time" option, an option that
        is not pending, a failure here) leaves the bare option id, as before. Never raises.
        """
        if self.record_tap is None:
            return
        try:
            before = repo.last_event_id(conn, turn.application_id)
            recorded = self.record_tap(conn, turn.application_id, option_id, now=self.clock.now())
        except Exception:
            log.exception(
                "record_tap failed for %s; the agent gets the bare id", turn.application_id
            )
            return
        if recorded is not None:
            turn.input, turn.after_event_id = recorded, before
            turn.booking_link = booking_link_of(recorded)

    def _reopened(self, application_id: str) -> None:
        if self.on_reopened is None:
            return
        try:
            self.on_reopened(application_id)
        except Exception:
            log.exception("on_reopened failed for %s", application_id)

    def _opted_out(
        self,
        conn: sqlite3.Connection,
        candidate: dict[str, Any],
        app: dict[str, Any] | None,
        message: dict[str, Any],
        now: datetime,
        channel: str,
        modality: Literal["typed", "spoken"] = "typed",
    ) -> InboundResult:
        """Mark the Candidate Opted out (an ``opted_out`` event; idempotent), start no run, and
        confirm once, unless a human has the conversation. The confirmation replies to the
        Candidate's own message, so ``proactive=False``: allowed although Opted out, and not
        held for quiet hours.

        ``app`` is the open Application, or None (Q42): then the confirmation is stored
        without an Application, in the Candidate's language, else their country's Spanish,
        else es-ES (the keyword itself carries no other language signal)."""
        repo.mark_opted_out(conn, candidate["id"], now=now)
        if app is None:
            sent = self.sender.reply_without_application(
                conn,
                candidate,
                channel=channel,
                text=opt_out_confirmation(
                    candidate["language"], candidate["country"], spoken=modality == "spoken"
                ),
                modality=modality,
            )
            return InboundResult(message, False, None, None, "opt_out", reply=sent.message)
        if app["control"] == "human":
            return InboundResult(message, False, app["id"], None, "human_control")
        app = repo.require_application(conn, app["id"])  # last_inbound_at has moved
        sent = self.sender.send(
            conn,
            app,
            text=opt_out_confirmation(
                app["language"] or candidate["language"],
                app["country"],
                spoken=modality == "spoken",
            ),
            sender="system",
            proactive=False,
            modality=modality,
            channel=channel,
        )
        return InboundResult(message, False, app["id"], None, "opt_out", reply=sent.message)

    @staticmethod
    def _duplicate(
        row: sqlite3.Row,
        candidate: dict[str, Any],
        text: str,
        content_type: str,
        reply_option_id: str | None,
    ) -> InboundResult:
        same = (
            row["candidate_id"] == candidate["id"]
            and row["body"] == text
            and row["content_type"] == content_type
            and row["reply_option_id"] == reply_option_id
        )
        if not same:
            raise MessageIdReused("message_id was already used for a different message")
        return InboundResult(dict(row), True, row["application_id"], None, "duplicate")

    # ------------------------------------------------------------------ runs

    def run_turn(self, conn: sqlite3.Connection, turn: TurnRequest) -> dict[str, Any] | None:
        """Run the agent for one turn and send its reply. Returns the sent message, if any.

        Never raises once the Application is found. Any failure of the attempt (Hermes
        failing or unreachable, a malformed response, an exception while recording the
        run or sending its reply) goes through :meth:`_run_failed`. An owed turn (ADR 0073)
        runs after the Application's older owed turns, and not at all if one of them fails
        again or if the recovery already answered it.
        """
        app = repo.get_application(conn, turn.application_id)
        if app is None:
            log.error("run_turn: no Application %s", turn.application_id)
            return None
        if not self._agent_may_run(conn, app):
            return None
        if turn.inbound_pk is None:
            return self._attempt(conn, app, turn, None)[0]
        row = repo.owed_turn(conn, turn.inbound_pk)
        if row is None:
            log.info("run_turn: the turn on %s was already answered", app["id"])
            return None
        for earlier in repo.owed_turns_of(conn, app["id"], before=turn.inbound_pk):
            if not self._run_owed(conn, earlier):
                return None  # this message waits behind the owed one; the recovery runs both
        current = repo.get_application(conn, app["id"])
        if current is None or not self._agent_may_run(conn, current):
            return None
        return self._attempt(conn, current, turn, row)[0]

    def resume(self, conn: sqlite3.Connection, application_id: str) -> bool:
        """Run the Application's owed turns, oldest first, stopping at the first that fails
        (ADR 0073). True when none is left. Never raises."""
        try:
            for row in repo.owed_turns_of(conn, application_id):
                if not self._run_owed(conn, row):
                    return False
            return True
        except Exception:
            log.exception("resuming the owed turns of %s failed", application_id)
            return False

    def _agent_may_run(self, conn: sqlite3.Connection, app: dict[str, Any]) -> bool:
        """False under a Takeover or while the Recruiters have the chat (ADR 0063); then the
        owed turns are dropped, since a person answers now (ADR 0020)."""
        if app["control"] == "human":
            log.info("run_turn: %s is under a Takeover, no run", app["id"])
        elif repo.agent_on_hold(conn, app["id"]):
            log.info("run_turn: %s is with the Recruiters (ADR 0063), no run", app["id"])
        else:
            return True
        self._drop_owed(conn, app["id"])
        return False

    @staticmethod
    def _drop_owed(conn: sqlite3.Connection, application_id: str) -> None:
        try:
            with transaction(conn):
                repo.drop_owed_turns(conn, application_id)
        except Exception:
            log.exception("could not drop the owed turns of %s", application_id)

    def _run_owed(self, conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
        """One attempt at an owed turn read back from its row. True when it needs no more."""
        app = repo.get_application(conn, row["application_id"])
        if app is None:
            return True
        if not self._agent_may_run(conn, app):
            return False
        return self._attempt(conn, app, self._owed_request(conn, row), row)[1]

    @staticmethod
    def _owed_request(conn: sqlite3.Connection, row: dict[str, Any]) -> TurnRequest:
        """The :class:`TurnRequest` an owed turn was accepted as."""
        booking = row["booking_url"]
        turn = TurnRequest(
            application_id=row["application_id"],
            input=row["turn_input"],
            idempotency_key=row["idempotency_key"],
            after_event_id=row["after_event_id"],
            booking_link=(booking, row["booking_message"] or "") if booking else None,
            inbound_pk=row["message_id"],
            channel=row["channel"],
        )
        if row["typed"]:
            turn.typed_text = repo.owed_message_body(conn, row["message_id"])
            turn.message_pk = row["message_id"] if turn.typed_text is not None else None
        return turn

    def _attempt(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        turn: TurnRequest,
        owed: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """One attempt: the sent message, and whether the turn needs nothing more (its owed
        row, if any, is gone). Never raises."""
        attempt = _Attempt()
        try:
            sent = self._run_and_reply(conn, app, turn, attempt, owed)
        except Exception as exc:
            if isinstance(exc, RunFailed):
                failure = exc
            else:
                log.exception("run_turn failed for %s", app["id"])
                # Only the exception's type goes into the ledger and the Escalation note: its
                # message may carry Candidate text (ADR 0035). The log has the traceback.
                failure = RunFailed(
                    f"internal error: {type(exc).__name__}",
                    run_id=attempt.run_id,
                    latency_ms=attempt.latency_ms,
                )
            sent = self._run_failed(conn, app, turn, failure, attempt, owed=owed)
            return sent, owed is None or self._answered(conn, owed)
        if owed is not None:
            self._resolve_owed(conn, owed["message_id"])
        return sent, True

    @staticmethod
    def _answered(conn: sqlite3.Connection, owed: dict[str, Any]) -> bool:
        """True when the owed row is gone (the failure path resolved or dropped it)."""
        try:
            return repo.owed_turn(conn, owed["message_id"]) is None
        except Exception:
            log.exception("could not read the owed turn %s", owed["message_id"])
            return False

    @staticmethod
    def _resolve_owed(conn: sqlite3.Connection, message_pk: int) -> None:
        try:
            with transaction(conn):
                repo.resolve_owed_turn(conn, message_pk)
        except Exception:
            log.exception("could not resolve the owed turn %s", message_pk)

    def _run_and_reply(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        turn: TurnRequest,
        attempt: _Attempt,
        owed: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if owed is not None and self._read_before(owed):
            hold = False  # a retry reuses the first attempt's Jev reading (ADR 0073)
        else:
            app, hold = self._read_with_jev(conn, app, turn)
        if hold:
            return self._hand_to_recruiters(conn, app, turn)
        events_before = (
            turn.after_event_id
            if turn.after_event_id is not None
            else repo.last_event_id(conn, app["id"])
        )
        if owed is not None and owed["run_input"] is not None:
            # The same key must carry the same bytes: Hermes fingerprints the body.
            run_input = owed["run_input"]
        else:
            run_input = self._with_zone_note(conn, app, turn)
            if owed is not None:
                with transaction(conn):
                    repo.set_owed_run_input(
                        conn, owed["message_id"], run_input, iso(self.clock.now())
                    )
        attempt.started_at = self.clock.now()
        result = self.run_client.run(
            session_id=app["id"],
            input=run_input,
            idempotency_key=owed["idempotency_key"] if owed is not None else turn.idempotency_key,
        )
        return self.store_reply(
            conn, app, turn, result, events_before=events_before, attempt=attempt
        )

    @staticmethod
    def _read_before(owed: dict[str, Any]) -> bool:
        """An earlier attempt already got past the Jev step: its input is stored, and that
        happens after the step and before the POST (ADR 0073)."""
        return owed["run_input"] is not None or not owed["typed"]

    def store_reply(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        turn: TurnRequest,
        result: RunResult,
        *,
        events_before: int,
        attempt: _Attempt,
        modality: Literal["typed", "spoken"] = "typed",
        profile: str | None = None,
    ) -> dict[str, Any] | None:
        """Everything after a run returned, shared by the typed and spoken paths (R6.3): the
        usage row, the Takeover re-read (a reply is dropped under ``human``), the empty-output
        check, the pending choice, the AI disclosure, the send (marked ``modality``) and the
        Knockout link. Returns the sent message, if any; raises like :meth:`_run_and_reply`.
        ``profile`` is the Hermes profile that ran (the voice profile for a spoken turn, ADR
        0059); None is this router's run client's."""
        attempt.run_id, attempt.latency_ms = result.run_id, result.latency_ms

        now = self.clock.now()
        record_usage(
            conn,
            application=app,
            profile=profile or self.run_client.profile,
            environment=self.environment,
            now=now,
            result=result,
        )
        self.alarms.run_succeeded()  # the model answered: an outage the runs saw is over
        self._close_tool_calls(conn, app["id"], result.run_id, attempt, now)
        app = repo.require_application(conn, app["id"])
        if app["control"] == "human":
            # A Takeover started while the run was in flight: the person speaks now.
            log.info("run_turn: dropping agent reply on %s after a Takeover", app["id"])
            return None
        knockout = None
        if not turn.system and modality == "typed":
            knockout = self._zone_backstop(conn, app, turn)
            app = repo.require_application(conn, app["id"])
        interactive = self.pending_interactive(conn, app["id"])
        question = self._funnel_question(conn, app, interactive, events_before)
        if knockout is not None:
            # ADR 0076: code decided the Zone Knockout after the run; its template is the reply.
            question = knockout["message"]
        closed = (
            None
            if turn.system or modality != "typed"
            else self._closed_note(conn, app, interactive, events_before)
        )
        if not result.output:
            log.warning("run %s completed with no output", result.run_id)
            fixed = question if question is not None else closed
            if fixed is not None:
                message = self._send_fixed(
                    conn, app, fixed, interactive, result.run_id, turn, modality
                )
                if knockout is not None and message is not None:
                    repo.link_knockout_message(
                        conn, app["id"], after_event_id=events_before, message_id=message["id"]
                    )
                return message
            return self._send_booking_link(conn, app, turn, result.run_id)
        text = result.output
        if question is not None and question not in text:
            # ADR 0068: the Zone funnel's question is code's, whatever the agent wrote.
            log.warning("the reply on %s lacked the Zone funnel question; sending it", app["id"])
            text = question
        if closed is not None:
            # ADR 0072: the first message after a Knockout is answered by code's closed note.
            text = closed
        if not turn.system:
            if modality == "typed":  # a spoken reply was already heard as it streamed
                text = self._welcoming_back(conn, app, text)
            text = self._disclosing(conn, app, text, notice=modality == "typed")
        link = turn.booking_link
        if link is not None and link[0] not in text:
            # ADR 0064: the "other time" tap's reply carries the booking link, whatever the
            # agent wrote; the fixed message stands in for a reply without it.
            log.warning("the reply to an 'other time' tap on %s had no link; sending it", app["id"])
            text = link[1]
        sent = self.sender.send(
            conn,
            app,
            text=text,
            interactive=interactive,
            run_id=result.run_id,
            modality=modality,
            channel=turn.channel,
        )
        if sent.message is not None:
            attempt.replied = True
            # A Knockout in this run: its text went out in this reply. The event gets the
            # message's id, never a copy of the text (Q38).
            repo.link_knockout_message(
                conn, app["id"], after_event_id=events_before, message_id=sent.message["id"]
            )
        return sent.message

    def _zone_backstop(
        self, conn: sqlite3.Connection, app: dict[str, Any], turn: TurnRequest
    ) -> dict[str, Any] | None:
        """After a typed run at the Zone question that did nothing with the Candidate's message,
        code offers the funnel, decides the Knockout or counts a miss (ADR 0076,
        :func:`criba.tools.application.zone_backstop`). The Knockout result, or None. Never
        raises."""
        if turn.typed_text is None or turn.message_pk is None:
            return None
        try:
            return zone_backstop(conn, app, turn.message_pk, self.clock.now())
        except Exception:
            log.exception("the Zone backstop failed for %s", app["id"])
            return None

    def _funnel_question(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        interactive: Any,
        after_event_id: int,
    ) -> str | None:
        """The Zone funnel's fixed question (ADR 0068) when this run offered the funnel and its
        options are still the pending choice, else None. Never raises."""
        try:
            data = repo.last_funnel_offer(conn, app["id"], after_event_id=after_event_id)
            if data is None or not interactive:
                return None
            if [o["id"] for o in interactive["options"]] != [o["id"] for o in data["options"]]:
                return None
            return funnel.question(app, data["options"])
        except Exception:
            log.exception("could not read the Zone funnel for %s", app["id"])
            return None

    def _closed_note(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        interactive: Any,
        events_before: int,
    ) -> str | None:
        """Code's reply to the first message a Candidate writes after a Knockout (ADR 0072):
        why the Application is closed and what they can do, in their language. None when the
        Application is not a closed Knockout after the run, when the Knockout happened in this
        run (its template is the reply), when this run requested a Review or left a choice
        pending (a new Zone being narrowed down), or when this is not the first message since
        the Knockout. Never raises."""
        try:
            if app["status"] != "closed" or app["outcome"] != "knockout" or interactive:
                return None
            knockout = repo.latest_event(conn, app["id"], "knockout")
            if knockout is None or knockout["id"] > events_before:
                return None
            if repo.has_event_after(conn, app["id"], "review_requested", events_before):
                return None
            if self._messages_since_knockout(conn, app["id"], knockout) != 1:
                return None
            return knockout_closed(app["knockout_criterion"], app["language"], app["country"])
        except Exception:
            log.exception("could not check the Knockout note for %s", app["id"])
            return None

    @staticmethod
    def _messages_since_knockout(
        conn: sqlite3.Connection, application_id: str, knockout: sqlite3.Row
    ) -> int:
        """How many Candidate messages arrived after the Knockout: after the message that
        carried its text when the event names one, else after the event's time."""
        message_id = json.loads(knockout["data"] or "{}").get("message_id")
        if isinstance(message_id, int):
            return repo.candidate_message_count_after(conn, application_id, after_id=message_id)
        return repo.candidate_message_count_after(
            conn, application_id, after_ts=knockout["created_at"]
        )

    def _welcoming_back(self, conn: sqlite3.Connection, app: dict[str, Any], text: str) -> str:
        """``text``, opened by the welcome-back line when it is the first reply of an
        Application whose Candidate's previous one ended Withdrawn (ADR 0072): the new
        Application's first question then does not come out of nowhere. Never raises."""
        try:
            if repo.has_outbound_message(conn, app["id"]):
                return text
            previous = repo.previous_application_outcome(conn, app["candidate_id"], app["id"])
            if previous != "withdrawn":
                return text
            return f"{welcome_back(app.get('language'), app.get('country'))}\n{text}"
        except Exception:
            log.exception("could not check for a welcome back on %s", app["id"])
            return text

    def _send_fixed(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        text: str,
        interactive: Any,
        run_id: str | None,
        turn: TurnRequest,
        modality: Literal["typed", "spoken"],
    ) -> dict[str, Any] | None:
        """A fixed message the Candidate is owed after a run that wrote nothing: a reply, so on
        the turn's channel and, like :meth:`store_reply`, with the privacy notice only when typed.
        Never raises."""
        try:
            body = self._disclosing(conn, app, text, notice=modality == "typed")
            return self.sender.send(
                conn,
                app,
                text=body,
                channel=turn.channel,
                interactive=interactive,
                run_id=run_id,
                modality=modality,
            ).message
        except Exception:
            log.exception("could not send the fixed message for %s", app["id"])
            return None

    def _send_booking_link(
        self, conn: sqlite3.Connection, app: dict[str, Any], turn: TurnRequest, run_id: str | None
    ) -> dict[str, Any] | None:
        """The booking link of an "other time" tap (ADR 0064) as a typed chat message, when the
        turn is one. None for any other turn. Never raises."""
        if turn.booking_link is None:
            return None
        try:
            return self.sender.send(
                conn,
                app,
                text=turn.booking_link[1],
                interactive=self.pending_interactive(conn, app["id"]),
                run_id=run_id,
                channel=turn.channel,
            ).message
        except Exception:
            log.exception("could not send the booking link for %s", app["id"])
            return None

    def _with_zone_note(
        self, conn: sqlite3.Connection, app: dict[str, Any], turn: TurnRequest
    ) -> str:
        """The run input, with the backend's Zone note ahead of a typed message that is read while
        the Zone is the question (ADR 0068). A tap, a system turn and everything else pass as they
        are. Never raises."""
        if turn.system or turn.typed_text is None:
            return turn.input
        try:
            note = zone_note(conn, repo.require_application(conn, app["id"]))
        except Exception:
            log.exception("zone note failed for %s; the input goes as it is", app["id"])
            return turn.input
        return turn.input if note is None else f"{note}\n{turn.input}"

    def _read_with_jev(
        self, conn: sqlite3.Connection, app: dict[str, Any], turn: TurnRequest
    ) -> tuple[dict[str, Any], bool]:
        """Read a typed Candidate message with Jev before the run (ADR 0051): the language it
        decides and the tone hint are in the record when the agent calls ``get_application``.
        Returns the Application as it stands afterwards, and whether the message handed the
        conversation to the Recruiters (ADR 0063). Never raises and never delays a turn by
        more than the call's timeout."""
        if self.jev is None or turn.system or turn.typed_text is None or turn.message_pk is None:
            return app, False
        step = self.jev.read(
            conn, app, message_id=turn.message_pk, text=turn.typed_text, now=self.clock.now()
        )
        return repo.get_application(conn, app["id"]) or app, step.hold

    def _hand_to_recruiters(
        self, conn: sqlite3.Connection, app: dict[str, Any], turn: TurnRequest
    ) -> dict[str, Any] | None:
        """The third unreadable message in a row (ADR 0063): send the fixed message, in the
        Application's language, and no agent run. Its reply to the Candidate's message, so
        ``proactive=False``; a person who took over meanwhile speaks instead."""
        if app["control"] == "human":
            return None
        sent = self.sender.send(
            conn,
            app,
            text=unreadable_handover(app.get("language"), app.get("country")),
            sender="system",
            proactive=False,
            channel=turn.channel,
        )
        return sent.message

    def _disclosing(
        self, conn: sqlite3.Connection, app: dict[str, Any], text: str, *, notice: bool = True
    ) -> str:
        """``text``, with the AI disclosure added if no message of this conversation has made
        it yet (EU AI Act, ADR 0012), and the privacy notice link too (ADR 0068). The agent is
        told to do both in its first message; this makes it true whatever the model writes (ADR
        0044). A system turn (after Approval) is never the start of a conversation. ``notice``
        is off for a spoken turn: nothing on a call points to a link. It follows the reply, never
        the Application's channel: a typed reply on a voice Application is the Candidate's first
        written message and carries the link (ADR 0068, 0070). Never raises."""
        try:
            earlier = repo.outbound_message_bodies(conn, app["id"])
            language, country = app.get("language"), app.get("country")
            text = with_ai_disclosure(text, earlier, language=language, country=country)
            if notice:
                text = with_privacy_notice(text, earlier, language=language, country=country)
            return text
        except Exception:
            log.exception("AI disclosure check failed for %s; sending as written", app["id"])
            return text

    def pending_interactive(self, conn: sqlite3.Connection, application_id: str) -> Any:
        """The pending choice (Q40) as a valid ``interactive``, or None. Never raises."""
        try:
            return valid_interactive(
                self.pending_choice(conn, application_id, now=self.clock.now())
            )
        except Exception:
            log.exception("pending_choice failed for %s; sending without buttons", application_id)
            return None

    @staticmethod
    def _close_tool_calls(
        conn: sqlite3.Connection,
        application_id: str,
        run_id: str | None,
        attempt: _Attempt,
        now: datetime,
    ) -> None:
        """Link the run's tool calls to it (ADR 0069). Never raises, and does nothing for a run
        that never started."""
        if attempt.started_at is None:
            return
        try:
            trace.close_run(conn, application_id, run_id, attempt.started_at, now)
        except Exception:
            log.exception("could not link the tool calls of the run for %s", application_id)

    def _run_failed(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        turn: TurnRequest,
        failure: RunFailed,
        attempt: _Attempt,
        modality: Literal["typed", "spoken"] = "typed",
        profile: str | None = None,
        *,
        owed: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The one failure path of :meth:`run_turn` and of a spoken turn. Never raises.

        Three steps, each on its own so one failing does not stop the next: the usage row
        (marked failed), then the ``run_error`` event and the ``technical_error`` Escalation
        (one open Escalation per Application, so a repeat failure does not open another),
        then the fallback reply.

        An owed turn (ADR 0073) whose reply did not go out is kept for the recovery instead:
        its next attempt is scheduled, the Escalation opens only once its attempts are spent,
        and the fallback (the holding message) goes out once per outage."""
        if owed is not None and not attempt.replied and not turn.system:
            return self._owed_failed(conn, app, turn, failure, attempt, owed)
        if owed is not None:
            self._resolve_owed(conn, owed["message_id"])  # the reply went out
        now = self.clock.now()
        log.warning("run failed for %s (%s): %s", app["id"], failure.kind, failure)
        try:
            with transaction(conn):
                record_usage(
                    conn,
                    application=app,
                    profile=profile or self.run_client.profile,
                    environment=self.environment,
                    now=now,
                    failure=failure,
                )
        except Exception:
            log.exception("could not record the failed run for %s", app["id"])
        self._close_tool_calls(conn, app["id"], failure.run_id or attempt.run_id, attempt, now)
        cause = self._cause(failure, app["id"])
        try:
            with transaction(conn):
                repo.add_event(
                    conn,
                    candidate_id=app["candidate_id"],
                    application_id=app["id"],
                    kind="run_error",
                    actor="system",
                    data={
                        "kind": failure.kind,
                        "cause": cause,
                        "run_id": failure.run_id,
                        "system_turn": turn.system,
                    },
                    now=now,
                )
                repo.open_queue_item(
                    conn,
                    app["id"],
                    "escalation",
                    reason="technical_error",
                    note=f"Agent run {failure.kind} ({failure.run_id or 'no run id'})"
                    + _cause_note(cause),
                    now=now,
                )
        except Exception:
            log.exception("could not open the technical_error Escalation for %s", app["id"])
        if turn.system or attempt.replied:
            return None
        try:
            current = repo.require_application(conn, app["id"])
            if current["control"] == "human":
                return None
            if turn.booking_link is not None:
                # ADR 0064: the failed run still owes the Candidate the link they asked for.
                return self._send_booking_link(conn, current, turn, failure.run_id)
            return self.sender.send(
                conn,
                current,
                text=fallback_text(current["language"]),
                modality=modality,
                channel=turn.channel,
            ).message
        except Exception:
            log.exception("could not send the fallback reply for %s", app["id"])
            return None

    def _cause(self, failure: RunFailed, application_id: str) -> str:
        """The failure's cause code (ADR 0078), after telling the outage alarm. Never raises."""
        try:
            cause = classify(failure)
            self.alarms.record_failure(cause, application_id)
        except Exception:
            log.exception("could not classify the failed run for %s", application_id)
            return OTHER
        return cause

    def _owed_failed(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        turn: TurnRequest,
        failure: RunFailed,
        attempt: _Attempt,
        owed: dict[str, Any],
    ) -> dict[str, Any] | None:
        """:meth:`_run_failed` for an owed turn whose reply did not go out. Never raises."""
        now = self.clock.now()
        attempts = owed["attempts"] + 1
        log.warning(
            "run failed for %s (%s, attempt %d): %s", app["id"], failure.kind, attempts, failure
        )
        try:
            with transaction(conn):
                record_usage(
                    conn,
                    application=app,
                    profile=self.run_client.profile,
                    environment=self.environment,
                    now=now,
                    failure=failure,
                )
        except Exception:
            log.exception("could not record the failed run for %s", app["id"])
        self._close_tool_calls(conn, app["id"], failure.run_id or attempt.run_id, attempt, now)
        exhausted = attempts >= MAX_TURN_ATTEMPTS or owed["status"] == "exhausted"
        key = owed["idempotency_key"]
        if failure.final:
            # Hermes replays a failed run under its key for a day: a new attempt needs a new one.
            key = f"{_RETRY_KEY.sub('', key)}~r{attempts}"
        wait = OWED_BACKOFF[min(attempts, len(OWED_BACKOFF)) - 1]
        cause = self._cause(failure, app["id"])
        try:
            with transaction(conn):
                repo.add_event(
                    conn,
                    candidate_id=app["candidate_id"],
                    application_id=app["id"],
                    kind="run_error",
                    actor="system",
                    data={
                        "kind": failure.kind,
                        "cause": cause,
                        "run_id": failure.run_id,
                        "system_turn": False,
                    },
                    now=now,
                )
                repo.record_owed_failure(
                    conn,
                    owed["message_id"],
                    attempts=attempts,
                    status="exhausted" if exhausted else "owed",
                    idempotency_key=key,
                    next_attempt_at=iso(now + wait),
                    last_error=failure.kind,
                    now=iso(now),
                )
                if exhausted:
                    repo.exhaust_owed_turns(conn, app["id"], iso(now))
                    repo.open_queue_item(
                        conn,
                        app["id"],
                        "escalation",
                        reason="technical_error",
                        note=(
                            f"Agent run {failure.kind} ({failure.run_id or 'no run id'}),"
                            f" {attempts} attempts" + _cause_note(cause)
                        ),
                        now=now,
                    )
        except Exception:
            log.exception("could not record the failed attempt for %s", app["id"])
        try:
            current = repo.require_application(conn, app["id"])
            if current["control"] == "human":
                self._drop_owed(conn, app["id"])
                return None
            if repo.holding_sent(conn, app["id"]):
                return None  # one holding message per outage
            if turn.booking_link is not None:
                # ADR 0064: the failed run still owes the Candidate the link they asked for.
                sent = self._send_booking_link(conn, current, turn, failure.run_id)
            else:
                sent = self.sender.send(
                    conn,
                    current,
                    text=fallback_text(current["language"]),
                    channel=turn.channel,
                ).message
            if sent is not None:
                with transaction(conn):
                    repo.mark_holding_sent(conn, app["id"], iso(now))
            return sent
        except Exception:
            log.exception("could not send the holding message for %s", app["id"])
            return None

    # ------------------------------------------------------------------ events (Q41)

    def action_for_event(self, conn: sqlite3.Connection, application_id: str, kind: str) -> Action:
        """Turn an event into the next action. A ``run`` action's turn is the caller's to run."""
        handler = self.event_actions.get(kind)
        if handler is None:
            return Action("none", reason="no_action")
        return handler(conn, application_id)

    def _after_approved(self, conn: sqlite3.Connection, application_id: str) -> Action:
        app = repo.get_application(conn, application_id)
        if app is None or app["status"] != "open" or app["stage"] != "approved":
            return Action("none", reason="not_approved")
        if app["control"] == "human":
            return Action("none", reason="human_control")
        if app["channel"] == "voice":
            # No messaging channel reaches a voice-only Candidate between calls (ADR 0059): on
            # their next call the agent reads `approved` in the record and offers slots aloud.
            return Action("none", reason="voice_no_messaging")
        guard = self.sender.check(
            conn,
            app,
            proactive=True,
            template=False,
            quiet_hours_exempt=candidate_recently_active(app, self.clock.now()),
        )
        if guard is None:
            self._log_system_turn(conn, app, APPROVED_TURN)
            turn = TurnRequest(
                application_id=app["id"],
                input=APPROVED_TURN,
                idempotency_key=f"system:approved:{app['id']}:{app['approved_at']}",
                system=True,
                channel=app["channel"],
            )
            return Action("run", turn=turn)
        if guard.status == "not_now":
            return Action("not_now", reason=guard.reason)
        if guard.reason != "outside_window":
            return Action("none", reason=guard.reason)
        return self._send_interview_offer(conn, app)

    def _send_interview_offer(self, conn: sqlite3.Connection, app: dict[str, Any]) -> Action:
        now = self.clock.now()
        try:
            offer = self.interview_offer(conn, app["id"], now)
            options = [{"id": s["slot_ref"], "label": s["label"]} for s in offer["slots"]]
            options.append({"id": "other", "label": offer["other_label"]})
            interactive = valid_interactive(
                {"style": "buttons" if len(options) <= 3 else "list", "options": options}
            )
            language = "en" if app["language"] == "en" else "es"
            text = INTERVIEW_OFFER_TEXT[language].format(booking_url=offer["booking_url"])
        except Exception:
            log.exception("interview offer failed for %s", app["id"])
            repo.open_queue_item(
                conn,
                app["id"],
                "escalation",
                reason="technical_error",
                note="Interview-offer template could not be built",
                now=now,
            )
            return Action("none", reason="offer_failed")
        sent = self.sender.send(
            conn,
            app,
            text=text,
            channel=app["channel"],
            interactive=interactive,
            template_name=INTERVIEW_OFFER_TEMPLATE,
            proactive=True,
        )
        if sent.status != "sent":
            return Action("none", reason=sent.reason)
        return Action("template", message=sent.message)

    def _log_system_turn(self, conn: sqlite3.Connection, app: dict[str, Any], text: str) -> None:
        """Log the system turn as a ``system_turn`` event, never as a message, so it stays
        out of the Candidate-visible transcript."""
        repo.add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=app["id"],
            kind=SYSTEM_TURN_EVENT,
            actor="system",
            data={"turn": text},
            now=self.clock.now(),
        )
