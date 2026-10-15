"""The channel service: one object that wires the router, run client and sender.

The web chat endpoints, the scheduler (W2-C) and the Takeover API (W2-D) all go through
a :class:`Channel`. Each public method opens its own SQLite connection, so it is safe to
call from a background task or another thread.

Entry points for other lanes:

- :meth:`Channel.handle_event`: run the Q41 action for an event. The scheduler calls
  it, never the Approval API directly: Approval goes to
  ``Scheduler.submit_action(conn, application_id, "approved")``, which holds it past
  quiet hours and dispatches it here once (:func:`criba.channel.scheduling.dispatcher`).
  In quiet hours the answer is a plain ``not_now``; the channel never holds.
- :meth:`Channel.send` (W2-D Takeover messages): send through the sender's guards. Pass
  ``proactive=True`` for anything the Candidate did not just prompt. The scheduler's
  nudges go through :class:`criba.channel.scheduling.NudgeSender`.
- :meth:`Channel.run_turn`: run the agent for a :class:`~criba.channel.router.TurnRequest`.
- :meth:`Channel.run_spoken_turn` (spec voice-agent spoken-turn R5, R6, R16): one spoken
  utterance on a worker thread of its own, streaming into a :class:`DeltaQueue` that the
  custom-LLM response (:mod:`criba.channel.voice`) only reads. The spoken checks plug in
  through :class:`SpokenChecks` (``Channel(spoken_checks=...)``; None is a no-op). Voice is
  its own flow (ADR 0059): spoken turns run on the voice profile's run client
  (``Channel(voice_run_client=...)``, ``criba-recruiter-voice`` from env), never the chat's.

- :meth:`Channel.resume_owed_turns` (ADR 0073): run the owed turns that are due again, each
  Application's on a thread of its own under its turn lock. The scheduler calls it every few
  seconds, and first at startup, when it resumes every owed turn the last process left.

Run state, per Application and in memory: a turn is *queued* from the moment the web
chat accepts the message that starts it (:meth:`Channel.turn_accepted`) and *running*
while :meth:`Channel.run_turn` works. ``agent_typing`` is "running"; ``run_finished`` is
"neither queued nor running" (:meth:`Channel.run_finished`). Turns of one Application run
one at a time (:meth:`Channel.run_turn`): a turn waiting for the previous one counts as
running.
"""

from __future__ import annotations

import itertools
import logging
import queue
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from criba.channel.config import ChannelSettings
from criba.channel.copy import spoken_handover
from criba.channel.ports import (
    InterviewOfferFn,
    PendingChoiceFn,
    RecordTapFn,
    default_interview_offer,
    default_pending_choice,
    default_record_tap,
)
from criba.channel.router import (
    Action,
    MessageIdReused,
    Router,
    TurnRequest,
    _Attempt,
    spoken_run_input,
)
from criba.channel.run_client import RunClient, RunClientProtocol, RunFailed, RunResult
from criba.channel.sender import Sender, SendResult, fallback_text
from criba.channel.voice_session import (
    end_voice_session,
    link_spoken_disclosure,
    store_spoken_disclosure,
)
from criba.db import repo
from criba.db.connection import connect, iso, transaction
from criba.jev.turn import JevStep
from criba.screening.clock import Clock, SystemClock

log = logging.getLogger(__name__)

# Voice is its own channel (ADR 0059): spoken turns, their replies and an Application a call
# starts are recorded on it.
SPOKEN_CHANNEL = "voice"
_WAIT_SLICE_S = 0.05  # how often a waiting duplicate looks whether a newer one displaced it


@dataclass
class _Inflight:
    lock: threading.Lock
    counts: dict[str, int]  # running turns, and turns waiting for the Application's turn lock
    queued: dict[str, int] = field(default_factory=dict)  # accepted, not started yet
    # One run at a time per Application (= one Hermes session). Created on first use and
    # dropped when no turn of that Application is running or waiting.
    turn_locks: dict[str, threading.Lock] = field(default_factory=dict)
    # Spoken utterances in flight, by message id: the owning worker's Event, set when it ends.
    spoken_turns: dict[str, threading.Event] = field(default_factory=dict)
    # The one waiter slot per message id: the generation of the latest duplicate (decision 43).
    spoken_waiters: dict[str, int] = field(default_factory=dict)
    waiter_generations: Iterator[int] = field(default_factory=lambda: itertools.count(1))
    # Applications whose owed turns a recovery thread is running (ADR 0073).
    resuming: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Spoken turns: the request, the queue, the spoken-checks hook
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpokenTurnRequest:
    """One spoken utterance. Every id comes from the verified voice token (R5.1)."""

    conversation_id: str
    candidate_id: str
    voice_session_id: str
    message_id: str  # vc_<voice_session_id>_<n>
    text: str


@dataclass(frozen=True)
class SpokenEvent:
    kind: Literal["text", "end_call", "done"]
    text: str = ""


class DeltaQueue:
    """What a spoken worker streams: text events, then exactly one terminal event (``done`` or
    ``end_call``). ``put`` never blocks and is ignored after the terminal event; ``get`` returns
    None on timeout. Nothing the database needs goes through here, so a queue nobody reads
    loses nothing (R6.5)."""

    def __init__(self) -> None:
        self._items: queue.SimpleQueue[SpokenEvent] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._ended = False

    @property
    def ended(self) -> bool:
        """True once the terminal event is queued."""
        return self._ended

    def put(self, event: SpokenEvent) -> None:
        with self._lock:
            if self._ended:
                return
            if event.kind != "text":
                self._ended = True
            self._items.put(event)

    def get(self, timeout_s: float) -> SpokenEvent | None:
        try:
            return self._items.get(timeout=max(0.0, timeout_s))
        except queue.Empty:
            return None


@dataclass(frozen=True)
class SpokenDecision:
    kind: Literal["plain", "option", "reask", "optout_request"]
    option_id: str | None = None
    reply: str | None = None


class SpokenChecks(Protocol):
    """The spoken-checks hook (``spoken-checks``' ``SpokenChecker``). ``before_accept`` runs
    after the message-id lookup and before ``accept_inbound``, never on a resend.
    ``after_reply`` runs once per run turn that stored a reply (the agent's, or the run-failure
    fallback), after ``done`` is queued and before the turn lock is released; a turn that
    stored none (empty output, a reply dropped by a Takeover) does not call it."""

    def before_accept(
        self, conn: sqlite3.Connection, candidate: dict[str, Any], *, text: str, now: datetime
    ) -> SpokenDecision: ...

    def after_reply(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        *,
        message_pk: int,
        reply_pk: int,
        text: str,
        now: datetime,
    ) -> None: ...


class NoSpokenChecks:
    """The default hook: every utterance is plain, nothing happens after the reply."""

    def before_accept(
        self, conn: sqlite3.Connection, candidate: dict[str, Any], *, text: str, now: datetime
    ) -> SpokenDecision:
        return SpokenDecision("plain")

    def after_reply(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        *,
        message_pk: int,
        reply_pk: int,
        text: str,
        now: datetime,
    ) -> None:
        return None


class Channel:
    def __init__(
        self,
        *,
        connect: Callable[[], sqlite3.Connection],
        run_client: RunClientProtocol,
        clock: Clock | None = None,
        settings: ChannelSettings | None = None,
        sender: Sender | None = None,
        pending_choice: PendingChoiceFn = default_pending_choice,
        interview_offer: InterviewOfferFn = default_interview_offer,
        record_tap: RecordTapFn = default_record_tap,
        jev: JevStep | None = None,
        spoken_checks: SpokenChecks | None = None,
        voice_run_client: RunClientProtocol | None = None,
    ) -> None:
        self.settings = settings or ChannelSettings()
        # Spoken turns run here; a test that passes none gets the text client.
        self.voice_run_client: RunClientProtocol = voice_run_client or run_client
        self.spoken_checks: SpokenChecks = spoken_checks or NoSpokenChecks()
        self.clock = clock or SystemClock()
        self._connect = connect
        self.sender = sender or Sender(self.clock)
        self.router = Router(
            run_client=run_client,
            sender=self.sender,
            clock=self.clock,
            pending_choice=pending_choice,
            interview_offer=interview_offer,
            environment=self.settings.environment,
            record_tap=record_tap,
            jev=jev,
            # The accepting request has the whole run, and a margin, before the recovery may
            # take its turn (ADR 0073).
            owed_lease=timedelta(seconds=self.settings.run_timeout_s + 60),
        )
        self._inflight = _Inflight(threading.Lock(), {})

    @classmethod
    def from_env(cls, **overrides: Any) -> Channel:
        settings = overrides.pop("settings", None) or ChannelSettings.from_env()

        def run_client_for(profile: str) -> RunClient:
            return RunClient(
                base_url=settings.hermes_url,
                profile=profile,
                api_key=settings.read_api_key(),
                timeout_s=settings.run_timeout_s,
                poll_interval_s=settings.poll_interval_s,
            )

        given = overrides.pop("run_client", None)
        run_client = given or run_client_for(settings.hermes_profile)
        # A caller that injects one run client (tests, the sim harness) gets it for both flows.
        voice_run_client = overrides.pop("voice_run_client", None) or (
            given or run_client_for(settings.hermes_voice_profile)
        )
        return cls(
            connect=lambda: connect(settings.db_path),
            run_client=run_client,
            voice_run_client=voice_run_client,
            settings=settings,
            **overrides,
        )

    def new_connection(self) -> sqlite3.Connection:
        """A new connection to the channel's database; the caller closes it."""
        return self._connect()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ runs

    def is_running(self, application_id: str | None) -> bool:
        if application_id is None:
            return False
        with self._inflight.lock:
            return self._inflight.counts.get(application_id, 0) > 0

    def run_finished(self, application_ids: Iterable[str]) -> bool:
        """True when no turn is queued or running for any of these Applications."""
        with self._inflight.lock:
            return not any(
                self._inflight.counts.get(a, 0) or self._inflight.queued.get(a, 0)
                for a in application_ids
            )

    def turn_accepted(self, turn: TurnRequest) -> None:
        """Mark ``turn`` queued until ``run_turn(turn, accepted=True)`` picks it up."""
        with self._inflight.lock:
            queued = self._inflight.queued
            queued[turn.application_id] = queued.get(turn.application_id, 0) + 1

    def run_turn(self, turn: TurnRequest, *, accepted: bool = False) -> dict[str, Any] | None:
        """Run one turn. ``accepted``: the turn was marked by :meth:`turn_accepted`.

        Turns of one Application run one at a time, each after the previous one has sent
        its reply. Two runs at once on one Hermes session both read the same next field
        from ``get_application``, so two quick messages ("Ana López", then "Madrid") were
        recorded against the wrong fields (W3-1)."""
        with self._holding_turn(turn.application_id, accepted=accepted), self.connection() as conn:
            return self.router.run_turn(conn, turn)

    def resume_owed_turns(self, *, startup: bool = False, background: bool = True) -> list[str]:
        """Run again the owed turns that are due (ADR 0073); at ``startup`` every owed turn,
        whatever its lease: nothing of this process runs them yet. Each Application's turns run
        oldest first under its turn lock, on a thread of its own unless ``background`` is off
        (tests). An Application with a turn queued or running here is left to that turn, which
        runs the owed ones before its own. Returns the Applications started."""
        with self.connection() as conn:
            due = repo.due_owed_applications(conn, iso(self.clock.now()), ignore_lease=startup)
        started: list[str] = []
        for app_id in due:
            with self._inflight.lock:
                busy = (
                    app_id in self._inflight.resuming
                    or self._inflight.counts.get(app_id, 0) > 0
                    or self._inflight.queued.get(app_id, 0) > 0
                )
                if busy:
                    continue
                self._inflight.resuming.add(app_id)
            started.append(app_id)
            if background:
                threading.Thread(
                    target=self._resume, args=(app_id,), name="criba-resume", daemon=True
                ).start()
            else:
                self._resume(app_id)
        return started

    def _resume(self, app_id: str) -> None:
        try:
            with self._holding_turn(app_id, accepted=False), self.connection() as conn:
                self.router.resume(conn, app_id)
        except Exception:
            log.exception("resuming the owed turns of %s failed", app_id)
        finally:
            with self._inflight.lock:
                self._inflight.resuming.discard(app_id)

    @contextmanager
    def _holding_turn(self, app_id: str, *, accepted: bool) -> Iterator[None]:
        """Count a turn as running (no longer queued, if ``accepted``) and hold the
        Application's turn lock for it; typed and spoken turns share the lock (R6.1)."""
        counts = self._inflight.counts
        with self._inflight.lock:
            if accepted:
                queued = self._inflight.queued
                queued[app_id] = queued.get(app_id, 0) - 1
                if queued[app_id] <= 0:
                    del queued[app_id]
            counts[app_id] = counts.get(app_id, 0) + 1
            turn_lock = self._inflight.turn_locks.setdefault(app_id, threading.Lock())
        try:
            with turn_lock:
                yield
        finally:
            with self._inflight.lock:
                counts[app_id] -= 1
                if counts[app_id] <= 0:
                    del counts[app_id]
                    self._inflight.turn_locks.pop(app_id, None)

    def await_turn(self, application_id: str, *, timeout_s: float) -> bool:
        """Wait up to ``timeout_s`` until no turn of the Application holds its turn lock, without
        running anything. True when the lock was free, or became free in time."""
        with self._inflight.lock:
            turn_lock = self._inflight.turn_locks.get(application_id)
        if turn_lock is None:
            return True
        if not turn_lock.acquire(timeout=max(0.0, timeout_s)):
            return False
        turn_lock.release()
        return True

    # ------------------------------------------------------------------ spoken turns

    def run_spoken_turn(self, request: SpokenTurnRequest) -> DeltaQueue:
        """Start one spoken turn on a new daemon thread and return its queue at once (R6.1).
        The worker opens its own connection, closes it when it ends, and queues exactly one
        terminal event whatever happens (R6.6)."""
        events = DeltaQueue()
        worker = threading.Thread(
            target=self._spoken_worker, args=(request, events), name="criba-spoken", daemon=True
        )
        worker.start()
        return events

    def speak(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        text: str,
        queue: DeltaQueue,
        *,
        sender: Literal["agent", "system"] = "system",
    ) -> dict[str, Any] | None:
        """Store a fixed spoken reply on ``app`` (a reply to the Candidate, so never held) and
        queue its text. Returns the stored message, or None if the sender refused it."""
        sent = self.sender.send(
            conn,
            app,
            text=text,
            sender=sender,
            proactive=False,
            modality="spoken",
            channel=SPOKEN_CHANNEL,
        )
        queue.put(SpokenEvent("text", text))
        return sent.message

    def _spoken_worker(self, request: SpokenTurnRequest, events: DeltaQueue) -> None:
        conn: sqlite3.Connection | None = None
        try:
            conn = self.new_connection()
            self._spoken_turn(conn, request, events)
        except Exception as exc:
            # The type only: the message may carry Candidate text (parent concern 21).
            log.error("spoken turn failed: %s", type(exc).__name__)
            events.put(SpokenEvent("text", fallback_text(None)))
        finally:
            events.put(SpokenEvent("done"))
            if conn is not None:
                conn.close()

    def _spoken_turn(
        self, conn: sqlite3.Connection, request: SpokenTurnRequest, events: DeltaQueue
    ) -> None:
        """The in-flight registry first (risk 18): a request whose id another worker owns is
        a duplicate of an utterance in flight and waits for it; otherwise this worker owns the
        id until it ends."""
        message_id = request.message_id
        mine: threading.Event | None = None
        with self._inflight.lock:
            owner = self._inflight.spoken_turns.get(message_id)
            if owner is None:
                mine = threading.Event()
                self._inflight.spoken_turns[message_id] = mine
        if mine is None:
            self._replay_when_done(conn, request, events, owner)
            return
        try:
            self._own_spoken_turn(conn, request, events)
        finally:
            with self._inflight.lock:
                self._inflight.spoken_turns.pop(message_id, None)
            mine.set()

    def _own_spoken_turn(
        self, conn: sqlite3.Connection, request: SpokenTurnRequest, events: DeltaQueue
    ) -> None:
        """R5.2-R5.4 and R6.2: the lookup, the hook, ``accept_inbound``, the answers with no
        run, then the run under the turn lock."""
        known = _inbound(conn, request.message_id)
        if known is not None:
            if known["candidate_id"] == request.candidate_id and known["body"] == request.text:
                self._replay_when_done(conn, request, events, None)
            else:  # R5.4: a reused id; nothing is written
                events.put(SpokenEvent("text", fallback_text(_language(conn, known))))
            return
        candidate = repo.get_candidate(conn, request.candidate_id)
        if candidate is None:
            events.put(SpokenEvent("text", fallback_text(None)))
            return
        decision = self.spoken_checks.before_accept(
            conn, candidate, text=request.text, now=self.clock.now()
        )
        try:
            inbound = self.router.accept_inbound(
                conn,
                candidate,
                channel=SPOKEN_CHANNEL,
                message_id=request.message_id,
                text=request.text,
                content_type="text",
                reply_option_id=decision.option_id if decision.kind == "option" else None,
                modality="spoken",
            )
        except MessageIdReused:
            events.put(SpokenEvent("text", fallback_text(candidate["language"])))
            return
        if inbound.reason == "duplicate":  # lost a race with the same id
            self._replay_when_done(conn, request, events, None)
            return
        app = repo.get_application(conn, inbound.application_id or "")
        if app is not None:
            # The caller heard the disclosure whatever this turn turns out to be (an opt-out, a
            # Takeover, a re-ask or a run): it joins the Application the turn landed on.
            link_spoken_disclosure(conn, request.voice_session_id, app)
        if inbound.reason == "opt_out":
            if inbound.reply is not None:
                events.put(SpokenEvent("text", inbound.reply["body"]))
            return
        if app is None:
            events.put(SpokenEvent("text", fallback_text(candidate["language"])))
            return
        if inbound.reason == "human_control":  # R16.3
            self._end_call(conn, app, request, events)
            return
        if decision.kind == "reask":  # R14: stored and spoken, no run
            self.speak(conn, app, decision.reply or "", events)
            return
        turn = inbound.turn
        assert turn is not None
        store_spoken_disclosure(conn, request.voice_session_id, app, now=self.clock.now())
        self.turn_accepted(turn)
        with self._holding_turn(app["id"], accepted=True):
            self._run_spoken(
                conn,
                app["id"],
                turn,
                request,
                events,
                message_pk=inbound.message["id"],
                optout_request=decision.kind == "optout_request",
            )

    def _run_spoken(
        self,
        conn: sqlite3.Connection,
        application_id: str,
        turn: TurnRequest,
        request: SpokenTurnRequest,
        events: DeltaQueue,
        *,
        message_pk: int,
        optout_request: bool,
    ) -> None:
        """Under the turn lock (R6.2 step 5, R6.4, R16.1, R16.2): ``control`` before the run
        and before each delta, the stream, ``store_reply``, the terminal event, then
        ``after_reply``."""
        app = repo.require_application(conn, application_id)
        if app["control"] == "human":
            self._end_call(conn, app, request, events)
            return
        router = self.router
        attempt = _Attempt()
        reply: dict[str, Any] | None = None
        taken_over = False
        heard = False  # a streamed delta reached the call
        try:
            run_input = spoken_run_input(
                conn, app, turn.input, optout_request=optout_request, now=self.clock.now()
            )
            events_before = (
                turn.after_event_id
                if turn.after_event_id is not None
                else repo.last_event_id(conn, app["id"])
            )
            attempt.started_at = self.clock.now()
            # The voice profile's own Hermes session for this Application (ADR 0059).
            stream = self.voice_run_client.stream(
                session_id=app["id"], input=run_input, idempotency_key=turn.idempotency_key
            )
            while True:
                try:
                    delta = next(stream)
                except StopIteration as stop:
                    result: RunResult = stop.value
                    break
                if not taken_over and _control(conn, app["id"]) == "human":
                    taken_over = True  # R16.1: no agent text once `human` was read
                if not taken_over:
                    events.put(SpokenEvent("text", delta))
                    heard = heard or bool(delta.strip())
            reply = router.store_reply(
                conn,
                app,
                turn,
                result,
                events_before=events_before,
                attempt=attempt,
                modality="spoken",
                profile=self.voice_run_client.profile,
            )
            if reply is not None and not heard and not taken_over:
                # A run that wrote nothing: the fixed message the router owed (the Zone funnel's
                # question, ADR 0068) is stored, and the call hears it as well. The booking link
                # of an "other time" tap is a URL: stored for the chat, never read aloud.
                line = fallback_text(app.get("language")) if turn.booking_link else reply["body"]
                events.put(SpokenEvent("text", line))
        except Exception as exc:
            if isinstance(exc, RunFailed):
                failure = exc
            else:
                log.error("spoken run failed for %s: %s", app["id"], type(exc).__name__)
                failure = RunFailed(
                    f"internal error: {type(exc).__name__}",
                    run_id=attempt.run_id,
                    latency_ms=attempt.latency_ms,
                )
            reply = router._run_failed(
                conn,
                app,
                turn,
                failure,
                attempt,
                modality="spoken",
                profile=self.voice_run_client.profile,
            )
            if reply is not None and turn.booking_link is None:
                events.put(SpokenEvent("text", reply["body"]))
            elif not attempt.replied and _control(conn, app["id"]) != "human":
                events.put(SpokenEvent("text", fallback_text(app.get("language"))))
        if taken_over or _control(conn, app["id"]) == "human":
            self._end_call(conn, repo.require_application(conn, app["id"]), request, events)
            return
        events.put(SpokenEvent("done"))
        if reply is None:
            return
        try:
            self.spoken_checks.after_reply(
                conn,
                app,
                message_pk=message_pk,
                reply_pk=reply["id"],
                text=request.text,
                now=self.clock.now(),
            )
        except Exception as exc:
            log.warning("spoken after_reply failed for %s: %s", app["id"], type(exc).__name__)

    def _end_call(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        request: SpokenTurnRequest,
        events: DeltaQueue,
    ) -> None:
        """R16.2: the handover line (stored and spoken), the session ended, then ``end_call``."""
        line = spoken_handover(app.get("language"), app.get("country"))
        self.speak(conn, app, line, events)
        with transaction(conn):
            end_voice_session(conn, request.voice_session_id, now=self.clock.now())
        events.put(SpokenEvent("end_call"))

    def _replay_when_done(
        self,
        conn: sqlite3.Connection,
        request: SpokenTurnRequest,
        events: DeltaQueue,
        owner: threading.Event | None,
    ) -> None:
        """R5.3 with decision 43. Take the one waiter slot for this id; a newer duplicate
        displaces this one, which then ends with no text. Wait for the owning worker, then for
        the Application's turn, together up to ``run_timeout_s``; then replay the stored reply,
        or ``fallback_text`` when there is none."""
        message_id = request.message_id
        with self._inflight.lock:
            generation = next(self._inflight.waiter_generations)
            self._inflight.spoken_waiters[message_id] = generation

        def displaced() -> bool:
            with self._inflight.lock:
                return self._inflight.spoken_waiters.get(message_id) != generation

        deadline = time.monotonic() + self.settings.run_timeout_s

        def slice_s() -> float:
            return min(_WAIT_SLICE_S, max(0.0, deadline - time.monotonic()))

        try:
            if owner is not None:
                while not owner.wait(slice_s()):
                    if displaced():
                        return
                    if time.monotonic() >= deadline:
                        break
            known = _inbound(conn, message_id)
            if known is not None and known["application_id"] is not None:
                while not self.await_turn(known["application_id"], timeout_s=slice_s()):
                    if displaced():
                        return
                    if time.monotonic() >= deadline:
                        break
            if displaced():
                return
            reply = _stored_reply(conn, known) if known is not None else None
            if reply is not None:
                events.put(SpokenEvent("text", reply["body"]))
            else:
                language = _language(conn, known) if known is not None else None
                events.put(SpokenEvent("text", fallback_text(language)))
        finally:
            with self._inflight.lock:
                if self._inflight.spoken_waiters.get(message_id) == generation:
                    del self._inflight.spoken_waiters[message_id]

    # ------------------------------------------------------------------ events and sends

    def handle_event(self, application_id: str, kind: str) -> Action:
        """Decide and carry out the next action for an event (Q41)."""
        with self.connection() as conn:
            action = self.router.action_for_event(conn, application_id, kind)
        if action.kind == "run" and action.turn is not None:
            action.message = self.run_turn(action.turn)
        return action

    def send(
        self,
        application_id: str,
        text: str,
        *,
        sender: Literal["agent", "human", "system"] = "agent",
        sender_user_id: str | None = None,
        interactive: dict[str, Any] | None = None,
        template_name: str | None = None,
        proactive: bool = False,
    ) -> SendResult:
        with self.connection() as conn:
            app = repo.require_application(conn, application_id)
            return self.sender.send(
                conn,
                app,
                text=text,
                channel=app["channel"],
                sender=sender,
                sender_user_id=sender_user_id,
                interactive=interactive,
                template_name=template_name,
                proactive=proactive,
            )


def _inbound(conn: sqlite3.Connection, message_id: str) -> dict[str, Any] | None:
    """The stored utterance with this message id, if any."""
    row = repo.inbound_by_client_message_id(conn, SPOKEN_CHANNEL, message_id)
    return dict(row) if row else None


def _stored_reply(conn: sqlite3.Connection, inbound: dict[str, Any]) -> dict[str, Any] | None:
    """The reply to an inbound row (R5.3, risk 7): the Candidate's first outbound ``agent`` or
    ``system`` voice message after it and before their next inbound row, never the stored
    spoken disclosure (``client_message_id`` starting ``vd_``)."""
    row = repo.stored_voice_reply(conn, inbound["candidate_id"], SPOKEN_CHANNEL, inbound["id"])
    return dict(row) if row else None


def _language(conn: sqlite3.Connection, inbound: dict[str, Any]) -> str | None:
    return repo.candidate_language(conn, inbound["candidate_id"])


def _control(conn: sqlite3.Connection, application_id: str) -> str | None:
    """``control`` as stored now, read on the caller's own connection."""
    return repo.application_control(conn, application_id)
