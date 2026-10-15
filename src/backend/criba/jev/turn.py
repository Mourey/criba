"""The router's Jev step (ADR 0051): read one typed Candidate message before the agent runs.

For each typed inbound message (never a button tap, a media-only message, an Opt-out, a system
turn, or a message under a Takeover) the router calls :meth:`JevStep.read` once, with a hard
timeout, before the Hermes run. The step:

1. calls Jev (one request, ten questions) and records the call in the usage ledger as kind
   ``jev``, failures included;
2. decides the language in code (:func:`criba.jev.policy.decide_language`: an explicit request
   for a language first, ADR 0061, then the language the message is written in) and stores a
   change with ``repo.set_candidate_language``. The agent then reads it from ``get_application`` as
   every turn, which is how the decided language reaches the run;
3. decides the tone (:func:`criba.jev.policy.decide_tone`): a ``tone_hint`` event holding codes
   only (ADR 0035), which ``get_application`` renders for this turn; and, above the higher
   threshold, an Escalation through the normal path;
4. stores the signals of the message (:mod:`criba.jev.store`);
5. counts an unreadable message (ADR 0063, :func:`criba.jev.policy.unreadable_state`) as a
   miss on the pending question, whether or not the agent then calls a tool, and at
   :data:`~criba.jev.policy.UNREADABLE_RUN` unreadable messages in a row raises the
   ``unreadable`` Escalation that hands the conversation to the Recruiters
   (``StepResult.hold``): the router then sends a fixed message and skips the run.

A spoken turn reads Jev after its reply is stored instead (spec voice-agent spoken-checks R9):
``decide_language=False`` skips steps 2 and 5, and ``reply_message_id`` (the stored reply) keys the
``tone_hint``, so the hint applies to the Candidate's next turn. The usage row, the hint, the
Escalation and the signals (keyed on the Candidate's message) are written as for a typed turn,
except that the display-only ``intent`` and ``malice`` readings (ADR 0071) are asked on every
call but stored as NULL for a spoken turn: nothing shows a spoken turn's inference to anyone.

It never raises: a Jev failure, a timeout or a bug leaves the turn exactly as it would have
been without Jev. A failure is recorded as a failure and never as a calm reading.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from criba.db import repo
from criba.db.connection import transaction
from criba.jev import store
from criba.jev.battery import JevInput, Turn
from criba.jev.client import JevFailed, JevPort
from criba.jev.policy import (
    THRESHOLDS,
    UNREADABLE_RUN,
    Thresholds,
    decide_tone,
    unreadable_state,
)
from criba.jev.policy import decide_language as decide_language_policy
from criba.tools.application import ESCALATE_AFTER_MISSES
from criba.tools.common import next_field

log = logging.getLogger(__name__)

# The note on an Escalation raised from Jev: a fixed text, never the Candidate's words.
ESCALATION_NOTE = (
    "Raised automatically from the tone of the Candidate's latest message (ADR 0051). "
    "Read the conversation."
)
# The notes on the Escalations an unreadable run raises (ADR 0063): fixed text, no Candidate words.
HOLD_NOTE = (
    "Unreadable messages in a row (ADR 0063). The agent has stopped replying: take over, or "
    "dismiss to give the conversation back to the agent. Read the conversation."
)
MISSES_NOTE = "Third miss on the pending question, the last one an unreadable message (ADR 0063)."
_AGENT_SIDE = ("agent", "human", "system")
# The policy's reason for a language change, as stored in message_signals.language_reason.
_STORED_REASON = {
    "first_message": "first_message",
    "switch": "switch",
    "request": "request",
    "request_same": "variant",
    "same_language": "variant",
}


@dataclass(frozen=True)
class StepResult:
    """What the step did, for tests and logs."""

    called: bool
    ok: bool = False
    language_set: str | None = None
    hint: str | None = None
    escalation: str | None = None
    unreadable: bool = False  # this message counted as an unreadable miss (ADR 0063)
    hold: bool = False  # the Recruiters have the conversation now: no agent run (ADR 0063)


class JevStep:
    def __init__(
        self,
        port: JevPort,
        *,
        environment: str,
        thresholds: Thresholds = THRESHOLDS,
    ) -> None:
        self.port = port
        self.environment = environment
        self.thresholds = thresholds

    def read(
        self,
        conn: sqlite3.Connection,
        app: Mapping[str, Any],
        *,
        message_id: int,
        text: str,
        now: datetime,
        decide_language: bool = True,
        reply_message_id: int | None = None,
    ) -> StepResult:
        """Read ``text`` (the Candidate's message ``message_id``). Never raises.

        ``decide_language=False`` (post-reply mode) never changes the Candidate's language;
        ``reply_message_id``, when given, is the message the ``tone_hint`` is keyed on."""
        try:
            return self._read(
                conn,
                app,
                message_id=message_id,
                text=text,
                now=now,
                decide_language=decide_language,
                reply_message_id=reply_message_id,
            )
        except Exception:
            log.exception("jev step failed for %s; the turn goes ahead without it", app["id"])
            return StepResult(called=True)

    def _read(
        self,
        conn: sqlite3.Connection,
        app: Mapping[str, Any],
        *,
        message_id: int,
        text: str,
        now: datetime,
        decide_language: bool,
        reply_message_id: int | None,
    ) -> StepResult:
        first = self._is_first(conn, app["id"], message_id)
        stored = app.get("language")
        request = JevInput(
            message=text,
            current_language=stored or self._default_language(app),
            country=app.get("country"),
            recent_turns=self._recent_turns(conn, app["id"], message_id),
        )
        try:
            result = self.port.read(request)
        except JevFailed as failure:
            log.warning(
                "jev call failed for %s (%s, %d ms)", app["id"], failure, failure.latency_ms
            )
            with transaction(conn):
                store.record_usage(
                    conn,
                    application=app,
                    environment=self.environment,
                    now=now,
                    requested_model=self.port.model,
                    failure=failure,
                )
                store.record_signals(
                    conn,
                    message_id=message_id,
                    application=app,
                    now=now,
                    status=failure.kind,
                    first_message=first,
                )
            return StepResult(called=True)
        log.info(
            "jev %s id=%s tokens=%d/%d cost=%s latency_ms=%d",
            result.model,
            result.request_id,
            result.input_tokens,
            result.output_tokens,
            result.cost_usd,
            result.latency_ms,
        )
        reading = result.reading
        # The intent and malice readings are stored for a typed turn only (ADR 0071).
        stored_reading = (
            reading
            if decide_language
            else replace(reading, intent=None, intent_confidence=None, malice=None)
        )
        decision = (
            decide_language_policy(
                stored, app.get("country"), reading, first=first, thresholds=self.thresholds
            )
            if decide_language
            else None
        )
        target = decision.target if decision else None
        reason = _STORED_REASON.get(decision.reason) if decision and target else None
        tone = decide_tone(reading, self.thresholds)
        escalation = tone.escalation if app.get("status") == "open" else None
        unreadable = (
            decide_language
            and app.get("status") == "open"
            and unreadable_state(text, reading.unreadable, self.thresholds) == "unreadable"
        )
        hold = False
        with transaction(conn):
            store.record_usage(
                conn,
                application=app,
                environment=self.environment,
                now=now,
                requested_model=self.port.model,
                result=result,
            )
            if target is not None:
                repo.set_candidate_language(conn, app["candidate_id"], target, now=now)
            if tone.hint is not None:
                repo.add_event(
                    conn,
                    candidate_id=app["candidate_id"],
                    application_id=app["id"],
                    kind="tone_hint",
                    actor="system",
                    data={
                        "code": tone.hint,
                        "signals": list(tone.signals),
                        "message_id": (
                            message_id if reply_message_id is None else reply_message_id
                        ),
                    },
                    now=now,
                )
            if escalation is not None:
                repo.open_queue_item(
                    conn,
                    app["id"],
                    "escalation",
                    reason=escalation,
                    note=ESCALATION_NOTE,
                    now=now,
                )
            store.record_signals(
                conn,
                message_id=message_id,
                application=app,
                now=now,
                status="ok",
                model=result.model,
                policy_version=self.thresholds.version,
                reading=stored_reading,
                first_message=first,
                language_set=target,
                language_reason=reason,
                fired=tone.signals,
                hint=tone.hint,
                escalation=escalation,
            )
            if unreadable:
                hold = self._unreadable_miss(conn, app, message_id=message_id, now=now)
        return StepResult(
            called=True,
            ok=True,
            language_set=target,
            hint=tone.hint,
            escalation=repo.HOLD_REASON if hold else escalation,
            unreadable=unreadable,
            hold=hold,
        )

    def _unreadable_miss(
        self,
        conn: sqlite3.Connection,
        app: Mapping[str, Any],
        *,
        message_id: int,
        now: datetime,
    ) -> bool:
        """Count an unreadable message (ADR 0063); True when it hands the conversation to the
        Recruiters. A miss on the pending question while one is pending (the ``answer_invalid``
        event holds the message id and the reason ``unreadable``; ``record_answer`` on the same
        message adds no second miss), and at the third unreadable message in a row the
        ``unreadable`` Escalation. Otherwise the third miss raises the usual Escalation, as
        ``record_answer`` would."""
        attempts = 0
        field = next_field(dict(app)) if app.get("stage") in repo.SCREENING_STAGES else None
        if field is not None:
            attempts = repo.record_invalid_answer(
                conn, app["id"], field, message_id=message_id, reason="unreadable", now=now
            )
        run = store.unreadable_run(conn, app["id"], message_id, self.thresholds) + 1
        if run >= UNREADABLE_RUN:
            repo.hold_for_recruiters(conn, app["id"], note=HOLD_NOTE, now=now)
            return True
        if attempts >= ESCALATE_AFTER_MISSES:
            repo.open_queue_item(
                conn,
                app["id"],
                "escalation",
                reason="repeated_invalid_answers",
                note=MISSES_NOTE,
                now=now,
            )
        return False

    @staticmethod
    def _default_language(app: Mapping[str, Any]) -> str:
        return "es-MX" if app.get("country") == "MX" else "es-ES"

    @staticmethod
    def _is_first(conn: sqlite3.Connection, application_id: str, message_id: int) -> bool:
        """True when no earlier typed message of this Application came from the Candidate."""
        return not repo.has_earlier_inbound_text(conn, application_id, message_id)

    @staticmethod
    def _recent_turns(
        conn: sqlite3.Connection, application_id: str, message_id: int
    ) -> tuple[Turn, ...]:
        rows = repo.recent_nonempty_messages(conn, application_id, message_id, 3)
        return tuple(
            Turn("assistant" if r["sender"] in _AGENT_SIDE else "candidate", r["body"])
            for r in reversed(rows)
        )
