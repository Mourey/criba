"""The spoken checks on the spoken-turn worker (spec voice-agent spoken-checks R9.2, R9.3,
R13.4, R14.2, R15.2, R15.3).

The channel is built like ``conftest.spoken_channel`` but with the real ``pending_choice``
and tap recording (as ``test_tap_recording.py`` builds them) and a real :class:`SpokenChecker`
over a fake Jev port. No model, Jev or ElevenLabs is called. Worker tests wait on the queue,
an event or a bounded poll (at most ``WAIT_S``); none sleeps to order threads.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from criba.channel.config import ChannelSettings
from criba.channel.copy import opt_out_confirmation, spoken_reask
from criba.channel.service import (
    Channel,
    DeltaQueue,
    SpokenDecision,
    SpokenEvent,
    SpokenTurnRequest,
)
from criba.channel.spoken import SpokenChecker
from criba.db import connect, iso, repo
from criba.jev import JevInput, JevResult
from criba.jev.policy import TONE_HINT_TEXT
from criba.jev.store import list_signals
from criba.jev.turn import JevStep
from criba.screening.clock import FakeClock
from criba.summary import DeterministicSummaryClient
from criba.tools import call_tool, pending_choice
from criba.tools.choices import TAP_RESULT_PREFIX
from criba.wiring import build_runtime

from ..jev.conftest import FakeJev, reading
from ..test_wiring import FakeRuns, _env, _seed
from .conftest import NOW, WAIT_S, FakeHermes, GatedRunClient, drain, sse, voice_session

TRANSCRIPTS = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "tests"
    / "fixtures"
    / "spikes"
    / "004-spanish-voice"
    / "results"
    / "transcripts_realtime.json"
)


def said(call: str, turn: int, condition: str = "clean") -> str:
    rows = json.loads(TRANSCRIPTS.read_text(encoding="utf-8"))
    [text] = [
        r["transcript"]
        for r in rows
        if (r["call"], r["turn"], r["condition"]) == (call, turn, condition)
    ]
    return str(text)


class RecordingJev(FakeJev):
    """``FakeJev`` that snapshots the spoken queue's events at each call and signals it."""

    def __init__(self, queued: list[SpokenEvent]) -> None:
        super().__init__()
        self.queued = queued
        self.snapshots: list[list[SpokenEvent]] = []
        self.called = threading.Event()

    def read(self, request: JevInput) -> JevResult:
        self.snapshots.append(list(self.queued))
        try:
            return super().read(request)
        finally:
            self.called.set()


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[SpokenEvent]:
    """Every event a ``DeltaQueue`` is given, in order, shared across queues."""
    seen: list[SpokenEvent] = []
    put = DeltaQueue.put

    def recording_put(self: DeltaQueue, event: SpokenEvent) -> None:
        seen.append(event)
        put(self, event)

    monkeypatch.setattr(DeltaQueue, "put", recording_put)
    return seen


@pytest.fixture
def port(queued: list[SpokenEvent]) -> RecordingJev:
    return RecordingJev(queued)


@pytest.fixture
def checked(db_path: Path, clock: FakeClock, gated: GatedRunClient, port: RecordingJev) -> Channel:
    """The real pending choice, tap recording and ``SpokenChecker``; Jev on the fake port. The
    router holds the same step, so a pre-run read would show as a second port call."""
    step = JevStep(port, environment="test")
    holder: dict[str, Channel] = {}
    checker = SpokenChecker(
        pending=lambda c, a: holder["channel"].router.pending_interactive(c, a), jev=step
    )
    channel = Channel(
        connect=lambda: connect(db_path),
        run_client=gated,
        clock=clock,
        settings=ChannelSettings(db_path=str(db_path), environment="test"),
        pending_choice=pending_choice,
        interview_offer=lambda conn, application_id, now: {},
        jev=step,
        spoken_checks=checker,
    )
    holder["channel"] = channel
    return channel


ANSWERS = [
    ("full_name", "Ana García"),
    ("zone", ["zone_es_madrid_chamberi"]),
    ("driver_license", "yes"),
    ("availability", ["full_time"]),
]


class Call:
    """One voice session on a voice conversation, with its open voice Application."""

    def __init__(self, conn: Any, channel: Channel, conversation_id: str) -> None:
        self.conn = conn
        self.channel = channel
        self.conversation_id = conversation_id
        self.session_id, candidate = voice_session(conn, conversation_id)
        self.candidate_id = candidate["id"]
        self.n = 0
        self.setup_done = 0

    def open(self, clock: FakeClock, *, pending: str | None = None) -> dict[str, Any]:
        """Open the voice Application with a hello and record the answers before ``pending``."""
        candidate = repo.get_candidate(self.conn, self.candidate_id)
        inbound = self.channel.router.accept_inbound(
            self.conn,
            candidate,
            channel="voice",
            message_id=f"m_{self.conversation_id}",
            text="hola",
        )
        app_id = inbound.application_id
        assert app_id is not None
        for name, value in ANSWERS:
            if name == pending:
                break
            status, body = call_tool(
                self.conn,
                "record_answer",
                {"field": name, "value": value},
                app_id,
                now=clock.now(),
            )
            assert status == 200 and body["ok"], body
        self.setup_done = repo.last_event_id(self.conn, app_id)
        return repo.require_application(self.conn, app_id)

    def answers(self) -> list[dict[str, Any]]:
        """The ``answer_recorded`` events after :meth:`open`'s own answers."""
        app_id = self.app()["id"]
        return [
            e
            for e in repo.list_events(self.conn, app_id, after_id=self.setup_done)
            if e["kind"] == "answer_recorded"
        ]

    def request(self, text: str, *, n: int | None = None) -> SpokenTurnRequest:
        if n is None:
            self.n += 1
            n = self.n
        return SpokenTurnRequest(
            conversation_id=self.conversation_id,
            candidate_id=self.candidate_id,
            voice_session_id=self.session_id,
            message_id=f"vc_{self.session_id}_{n}",
            text=text,
        )

    def say(self, text: str) -> tuple[SpokenTurnRequest, list[SpokenEvent]]:
        request = self.request(text)
        return request, self.speak(request)

    def speak(self, request: SpokenTurnRequest) -> list[SpokenEvent]:
        events = drain(self.channel.run_spoken_turn(request))
        workers_done()
        return events

    def app(self) -> dict[str, Any]:
        app = repo.get_open_application(self.conn, self.candidate_id)
        assert app is not None
        return app

    def rows(self) -> list[dict[str, Any]]:
        found = self.conn.execute(
            "SELECT * FROM messages WHERE candidate_id = ? ORDER BY id", (self.candidate_id,)
        ).fetchall()
        return [dict(r) for r in found]

    def replies(self) -> list[dict[str, Any]]:
        return [
            r
            for r in self.rows()
            if r["direction"] == "outbound" and not (r["client_message_id"] or "").startswith("vd_")
        ]


def wait_until(condition: Callable[[], bool], what: str) -> None:
    """Poll in-process state with a bounded wait (to observe, never to order threads)."""
    deadline = time.monotonic() + WAIT_S
    pause = threading.Event()
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        pause.wait(0.005)


def workers_done() -> None:
    """Every spoken worker has returned, ``after_reply`` included."""
    wait_until(
        lambda: not any(t.name == "criba-spoken" and t.is_alive() for t in threading.enumerate()),
        "the spoken workers to end",
    )


def spoken_text(events: list[SpokenEvent]) -> str:
    return "".join(e.text for e in events if e.kind == "text")


def events_of(conn: Any, app_id: str, kind: str) -> list[dict[str, Any]]:
    return [e for e in repo.list_events(conn, app_id) if e["kind"] == kind]


def last_line(hermes: FakeHermes) -> str:
    return hermes.inputs[-1].rsplit("\n", 1)[-1]


def application_block(run_input: str) -> dict[str, Any]:
    first = run_input.split("\n", 1)[0]
    assert first.startswith("[application] ")
    return dict(json.loads(first[len("[application] ") :]))


# --------------------------------------------------------------------------- R9.2, R9.3


def test_jev_runs_once_after_the_first_delta(
    conn: Any, checked: Channel, port: RecordingJev
) -> None:
    call = Call(conn, checked, "wc_first_delta")
    request, events = call.say("Hola, me llamo Ana y vivo en Madrid")
    assert events[-1].kind == "done"
    assert port.called.is_set()
    assert len(port.requests) == 1
    assert port.requests[0].message == request.text
    [snapshot] = port.snapshots
    assert any(e.kind == "text" for e in snapshot)  # the first delta was queued first
    assert snapshot[-1].kind == "done"


def test_jev_reads_matched_option_optout_request_and_run_failure_turns(
    conn: Any, checked: Channel, port: RecordingJev, clock: FakeClock, hermes: FakeHermes
) -> None:
    port.answer = lambda request: reading("en", 1.0, 0.99, frustrated=0.95)
    cases = [
        ("wc_read_option", "driver_license", said("es01", 2), None),
        ("wc_read_optout", "driver_license", said("es05", 2), None),
        ("wc_read_failure", None, "Vivo en Madrid", "run.failed"),
    ]
    for conversation_id, pending, text, failure in cases:
        call = Call(conn, checked, conversation_id)
        call.open(clock, pending=pending)
        language = call.app()["language"]
        before = len(port.requests)
        if failure is not None:
            hermes.event_scripts = [
                [sse(0, {"event": failure, "run_id": "run_x", "error": "model error"})]
            ]
        request, _ = call.say(text)
        inbound = [r for r in call.rows() if r["client_message_id"] == request.message_id]
        marker = "[optout_request]" in hermes.inputs[-1].split("\n")
        took = (inbound[0]["reply_option_id"], marker, failure is not None)
        assert (
            took
            == {
                "wc_read_option": ("yes", False, False),  # the tap path
                "wc_read_optout": (None, True, False),  # the marker
                "wc_read_failure": (None, False, True),  # the run-failure fallback
            }[conversation_id]
        )
        assert len(port.requests) == before + 1, conversation_id
        assert port.requests[-1].message == text
        app = call.app()
        assert app["language"] == language, conversation_id
        reply = call.replies()[-1]
        hint = events_of(conn, app["id"], "tone_hint")[-1]
        assert hint["data"]["message_id"] == reply["id"], conversation_id


def test_jev_is_not_called_for_an_optout_word_a_reask_a_takeover_or_a_resend(
    conn: Any, db_path: Path, checked: Channel, port: RecordingJev, clock: FakeClock
) -> None:
    word = Call(conn, checked, "wc_no_jev_word")
    word.say("Baja.")
    assert port.requests == []

    reask = Call(conn, checked, "wc_no_jev_reask")
    reask.open(clock, pending="driver_license")
    reask.say(said("mx01", 8, "moderate"))
    assert port.requests == []

    resend = Call(conn, checked, "wc_no_jev_resend")
    request, _ = resend.say("Hola")
    assert len(port.requests) == 1
    resend.speak(request)
    assert len(port.requests) == 1

    takeover = Call(conn, checked, "wc_no_jev_takeover")
    takeover.open(clock)
    take_over(db_path, takeover.app()["id"])
    _, events = takeover.say("¿Sigues ahí?")
    assert events[-1].kind == "end_call"
    assert len(port.requests) == 1


def take_over(db_path: Path, application_id: str) -> None:
    """A Takeover, written on a connection of its own as the Takeover API would."""
    other = connect(db_path)
    try:
        other.execute(
            "INSERT OR IGNORE INTO users (id, client_id, email, name, role, password_hash,"
            " created_at) VALUES ('usr_t', 'grupo_sazon', 't@example.test', 'T', 'recruiter',"
            " 'x', ?)",
            (iso(NOW),),
        )
        other.execute(
            "UPDATE applications SET control = 'human', control_user_id = 'usr_t' WHERE id = ?",
            (application_id,),
        )
    finally:
        other.close()


def test_a_post_reply_escalation_opens_a_queue_item(
    conn: Any, checked: Channel, port: RecordingJev
) -> None:
    port.answer = lambda request: reading("es", 1.0, wants_human=0.98)
    call = Call(conn, checked, "wc_escalation")
    call.say("Quiero hablar con una persona")
    app = call.app()
    item = conn.execute(
        "SELECT * FROM queue_items WHERE application_id = ? AND kind = 'escalation'", (app["id"],)
    ).fetchone()
    assert item is not None and item["status"] == "open" and item["reason"] == "wants_human"
    assert "persona" not in item["note"]


def test_a_spoken_turn_never_sets_the_language(
    conn: Any, checked: Channel, port: RecordingJev
) -> None:
    port.answer = lambda request: reading("en", 1.0, 0.99)
    call = Call(conn, checked, "wc_language")
    before = repo.get_candidate(conn, call.candidate_id)["language"]
    call.say("Hello, I'm looking for a delivery job")
    call.say("Can we speak English, please?")
    assert len(port.requests) == 2
    assert repo.get_candidate(conn, call.candidate_id)["language"] == before
    assert [s["language_set"] for s in list_signals(conn, call.app()["id"])] == [None, None]


def test_the_hint_reaches_the_next_spoken_turn_only(
    conn: Any, checked: Channel, port: RecordingJev, hermes: FakeHermes
) -> None:
    readings = iter([reading("es", 1.0, frustrated=0.95)])
    port.answer = lambda request: next(readings, reading())
    call = Call(conn, checked, "wc_hint")
    call.say("¡Ya te lo he dicho tres veces!")
    assert "tone_hint" not in application_block(hermes.inputs[-1])
    call.say("Vivo en Madrid")
    assert application_block(hermes.inputs[-1])["tone_hint"] == TONE_HINT_TEXT["apologise"]
    call.say("En Chamberí")
    assert "tone_hint" not in application_block(hermes.inputs[-1])
    assert len(port.requests) == 3


# --------------------------------------------------------------------------- R13.4


def test_a_matched_option_takes_the_tap_path(
    conn: Any, checked: Channel, clock: FakeClock, hermes: FakeHermes, port: RecordingJev
) -> None:
    call = Call(conn, checked, "wc_tap")
    call.open(clock, pending="availability")
    request, events = call.say("Jornada completa.")
    assert events[-1].kind == "done"
    [answer] = call.answers()
    assert answer["data"]["field"] == "availability"
    inbound = [r for r in call.rows() if r["client_message_id"] == request.message_id]
    assert [(r["reply_option_id"], r["body"], r["content_type"]) for r in inbound] == [
        ("full_time", "Jornada completa.", "text")
    ]
    assert last_line(hermes).startswith(TAP_RESULT_PREFIX)
    assert last_line(hermes).startswith("[tap result] ")
    assert "\n[voice]\n" in hermes.inputs[-1]
    assert port.requests[-1].message == "Jornada completa."


def test_other_time_reaches_the_run_as_the_option_id(
    conn: Any, checked: Channel, clock: FakeClock, hermes: FakeHermes
) -> None:
    call = Call(conn, checked, "wc_other_time")
    app = call.open(clock)
    slot = conn.execute(
        "SELECT id FROM interview_slots WHERE starts_at > ? ORDER BY starts_at LIMIT 1",
        (iso(NOW),),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO slot_offers (ref, application_id, slot_id, offered_at, expires_at)"
        " VALUES ('so_test_1', ?, ?, ?, ?)",
        (app["id"], slot, iso(NOW), iso(NOW + timedelta(hours=48))),
    )
    options = checked.router.pending_interactive(conn, app["id"])["options"]
    assert [o["id"] for o in options] == ["so_test_1", "other"]
    request, _ = call.say("Otro horario, por favor.")
    inbound = [r for r in call.rows() if r["client_message_id"] == request.message_id]
    assert inbound[0]["reply_option_id"] == "other"
    assert last_line(hermes) == "other"
    booked = conn.execute("SELECT count(*) FROM bookings WHERE application_id = ?", (app["id"],))
    assert booked.fetchone()[0] == 0


def test_a_resend_records_no_second_answer(
    conn: Any, checked: Channel, clock: FakeClock, hermes: FakeHermes
) -> None:
    call = Call(conn, checked, "wc_resend_tap")
    call.open(clock, pending="availability")
    request, first = call.say("Jornada completa.")
    second = call.speak(request)
    assert spoken_text(second) == spoken_text(first)
    assert len(call.answers()) == 1
    assert len(hermes.posts) == 1


def test_before_accept_never_raises(
    conn: Any, checked: Channel, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    call = Call(conn, checked, "wc_raises")
    call.open(clock, pending="driver_license")
    text = said("es01", 2)

    def broken(c: Any, application_id: str) -> Any:
        raise RuntimeError(f"pending failed on {text}")

    checker = SpokenChecker(pending=broken, jev=None)
    candidate = repo.get_candidate(conn, call.candidate_id)
    with caplog.at_level(logging.DEBUG):
        decision = checker.before_accept(conn, candidate, text=text, now=clock.now())
    assert decision == SpokenDecision("plain")
    assert "RuntimeError" in caplog.text and text not in caplog.text
    checker.after_reply(conn, call.app(), message_pk=1, reply_pk=2, text=text, now=clock.now())

    class BrokenStep:
        def read(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(f"jev failed on {text}")

    raising: Any = BrokenStep()
    with caplog.at_level(logging.DEBUG):
        SpokenChecker(pending=broken, jev=raising).after_reply(
            conn, call.app(), message_pk=1, reply_pk=2, text=text, now=clock.now()
        )
    assert "spoken after_reply failed: RuntimeError" in caplog.text and text not in caplog.text
    # With a working pending choice the same utterance is an option.
    good = SpokenChecker(pending=checked.router.pending_interactive, jev=None)
    assert good.before_accept(conn, candidate, text=text, now=clock.now()) == SpokenDecision(
        "option", option_id="yes"
    )


# --------------------------------------------------------------------------- R14.2


def test_a_conflict_is_reasked_with_no_answer_run_or_jev(
    conn: Any, checked: Channel, clock: FakeClock, gated: GatedRunClient, port: RecordingJev
) -> None:
    call = Call(conn, checked, "wc_reask")
    app = call.open(clock, pending="driver_license")
    text = said("mx01", 8, "moderate")
    request, events = call.say(text)
    copy = spoken_reask(app.get("language"), app.get("country"))
    assert copy == "Perdona, no te he oído bien. ¿Es sí o no?"
    assert (spoken_text(events), events[-1].kind) == (copy, "done")
    stored = call.rows()
    assert [(r["direction"], r["sender"], r["body"], r["modality"]) for r in stored[-2:]] == [
        ("inbound", "candidate", text, "spoken"),
        ("outbound", "system", copy, "spoken"),
    ]
    assert stored[-2]["reply_option_id"] is None
    assert call.answers() == []
    assert gated.streams == 0 and port.requests == []
    count = len(call.rows())
    assert spoken_text(call.speak(request)) == copy
    assert len(call.rows()) == count and gated.streams == 0


# --------------------------------------------------------------------------- R15.2, R15.3


def test_an_optout_phrase_reaches_the_run_with_the_marker(
    conn: Any, checked: Channel, clock: FakeClock, hermes: FakeHermes
) -> None:
    call = Call(conn, checked, "wc_phrase")
    call.open(clock, pending="driver_license")
    text = said("es05", 2)
    call.say(text)
    assert not repo.get_candidate(conn, call.candidate_id)["opted_out"]
    lines = hermes.inputs[-1].split("\n")
    assert lines.count("[optout_request]") == 1
    assert lines[-2:] == ["[optout_request]", text]
    assert call.answers() == []


@pytest.mark.parametrize("call_id,turn", [("es05", 1), ("es05", 3)])
def test_optout_words_opt_out_with_no_run_or_jev(
    conn: Any,
    checked: Channel,
    gated: GatedRunClient,
    port: RecordingJev,
    call_id: str,
    turn: int,
) -> None:
    call = Call(conn, checked, f"wc_word_{turn}")
    text = said(call_id, turn)
    _, events = call.say(text)
    candidate = repo.get_candidate(conn, call.candidate_id)
    assert candidate["opted_out"]
    confirmation = opt_out_confirmation(candidate["language"], candidate["country"], spoken=True)
    assert spoken_text(events) == confirmation
    assert [(r["sender"], r["body"]) for r in call.rows()] == [
        ("candidate", text),
        ("system", confirmation),
    ]
    assert gated.streams == 0 and port.requests == []


# --------------------------------------------------------------------------- decision 44


def test_runtime_wires_the_spoken_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    key = tmp_path / "openrouter.txt"
    key.write_text("test-word\n", encoding="utf-8")
    env = _env(tmp_path, monkeypatch, CRIBA_JEV="on", CRIBA_OPENROUTER_API_KEY_FILE=str(key))
    _seed(tmp_path / "criba.db")
    runtime = build_runtime(
        env,
        run_client=FakeRuns(),
        clock=clock,
        summary_client=DeterministicSummaryClient(),
        prepare_db=False,
    )
    channel = runtime.channel
    checker = channel.spoken_checks
    assert isinstance(checker, SpokenChecker)
    assert checker.jev is not None and checker.jev is channel.router.jev
    db = connect(tmp_path / "criba.db")
    try:
        call = Call(db, channel, "wc_wiring")
        app = call.open(clock, pending="driver_license")
        expected = channel.router.pending_interactive(db, app["id"])
        assert expected is not None
        assert checker.pending(db, app["id"]) == expected
    finally:
        db.close()
