"""A message is stored on the channel it arrived on, and so is its reply (ADR 0059, 0021).

A Candidate has at most one open Application, so the same person (same phone) can type in the
web chat while their open Application came in by a call, or speak on a call while it came in by
text. The Application keeps the channel it started on; each message and its reply take the
channel of the inbound message, and the flow follows it: typed turns run on the text profile
with Jev before the run, spoken turns on the voice profile.

Every test runs on the fake Hermes of ``conftest.py``: no model, no ElevenLabs.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from criba.channel.config import ChannelSettings
from criba.channel.copy import PRIVACY_URL
from criba.channel.router import TurnRequest, _Attempt
from criba.channel.run_client import RunClient, RunResult
from criba.channel.sender import fallback_text
from criba.channel.service import Channel, DeltaQueue, SpokenTurnRequest
from criba.channel.webchat import chat_router
from criba.db import connect, repo
from criba.db.connection import iso
from criba.tools import call_tool, pending_choice
from criba.tools.funnel import options as funnel_options

from .conftest import API_KEY, BASE_URL, NOW, FakeHermes, Fakes, drain, start

PHONE = "+34612345600"
VOICE_PROFILE = "criba-recruiter-voice"


class RecordingJev:
    """Stands in for the Jev step: records each typed message it reads, holds nothing."""

    def __init__(self) -> None:
        self.read_texts: list[str] = []

    def read(self, conn: Any, app: Any, *, message_id: int, text: str, now: Any) -> Any:
        self.read_texts.append(text)
        return SimpleNamespace(hold=False)


@pytest.fixture
def text_hermes() -> FakeHermes:
    return FakeHermes()


@pytest.fixture
def voice_hermes() -> FakeHermes:
    hermes = FakeHermes(profile=VOICE_PROFILE)
    hermes.reply = lambda text: f"Vale: {text.rsplit(chr(10), 1)[-1]}"
    return hermes


@pytest.fixture
def two_flows(
    db_path: Path, clock: Any, fakes: Fakes, text_hermes: FakeHermes, voice_hermes: FakeHermes
) -> Channel:
    def client(hermes: FakeHermes) -> RunClient:
        return RunClient(
            base_url=BASE_URL,
            profile=hermes.profile,
            api_key=API_KEY,
            http=hermes.client(),
            sleep=lambda _: None,
        )

    return Channel(
        connect=lambda: connect(db_path),
        run_client=client(text_hermes),
        voice_run_client=client(voice_hermes),
        clock=clock,
        settings=ChannelSettings(db_path=str(db_path), environment="test"),
        pending_choice=fakes.pending_choice,
        interview_offer=fakes.interview_offer,
    )


@pytest.fixture
def chat(two_flows: Channel) -> TestClient:
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/chat")
    app.state.criba_channel = two_flows
    return TestClient(app)


def _candidate(
    conn: Any, channel: str, conversation_id: str, *, phone: str = PHONE, country: str = "ES"
) -> dict[str, Any]:
    candidate, _ = repo.get_or_create_candidate(
        conn,
        client_id="grupo_sazon",
        channel=channel,
        external_id=conversation_id,
        phone=phone,
        country=country,
    )
    return candidate


def _rows(conn: Any, candidate_id: str) -> list[tuple[str, str, str]]:
    stored = conn.execute(
        "SELECT direction, channel, modality FROM messages WHERE candidate_id = ? ORDER BY id",
        (candidate_id,),
    ).fetchall()
    return [(r["direction"], r["channel"], r["modality"]) for r in stored]


def _voice_request(
    conn: Any, candidate: dict[str, Any], conversation_id: str, text: str
) -> SpokenTurnRequest:
    """A call's utterance: the voice conversation and session rows, and the request."""
    conn.execute(
        "INSERT INTO candidate_identities (candidate_id, channel, external_id, created_at)"
        " SELECT ?, 'voice', ?, ? WHERE NOT EXISTS"
        " (SELECT 1 FROM candidate_identities WHERE channel = 'voice' AND external_id = ?)",
        (candidate["id"], conversation_id, iso(NOW), conversation_id),
    )
    session_id = f"vs_{conversation_id}"
    conn.execute(
        "INSERT INTO voice_sessions (id, conversation_id, candidate_id, country, started_at)"
        " VALUES (?, ?, ?, 'ES', ?)",
        (session_id, conversation_id, candidate["id"], iso(NOW)),
    )
    return SpokenTurnRequest(
        conversation_id=conversation_id,
        candidate_id=candidate["id"],
        voice_session_id=session_id,
        message_id=f"vc_{session_id}_1",
        text=text,
    )


def _speak(
    channel: Channel, conn: Any, candidate: dict[str, Any], conversation_id: str, text: str
) -> tuple[SpokenTurnRequest, list[Any]]:
    """One spoken turn of a call on a voice conversation of this Candidate."""
    request = _voice_request(conn, candidate, conversation_id, text)
    return request, drain(channel.run_spoken_turn(request))


def test_a_typed_message_on_a_voice_application_is_stored_on_the_chat(
    conn: Any,
    chat: TestClient,
    two_flows: Channel,
    text_hermes: FakeHermes,
    voice_hermes: FakeHermes,
) -> None:
    jev = RecordingJev()
    router: Any = two_flows.router
    router.jev = jev
    # The Candidate called first: the open Application is a voice one.
    candidate = _candidate(conn, "voice", "wv_first")
    voice_app = repo.start_application(conn, candidate["id"], "voice", now=NOW)

    # Later they type in the web chat, same phone.
    conversation = start(chat, phone=PHONE)
    sent = chat.post(
        f"/api/chat/conversations/{conversation}/messages",
        json={"message_id": "typed_1", "text": "hola, sigo aqui"},
    )
    assert sent.status_code == 202, sent.text

    # One open Application, still the voice one; the message and its reply are on the chat.
    open_app = repo.get_open_application(conn, candidate["id"])
    assert open_app is not None and open_app["id"] == voice_app["id"]
    assert _rows(conn, candidate["id"]) == [
        ("inbound", "webchat", "typed"),
        ("outbound", "webchat", "typed"),
    ]
    # The text flow: the text profile ran it, the voice profile did not, Jev read it first.
    assert len(text_hermes.posts) == 1 and voice_hermes.posts == []
    assert jev.read_texts == ["hola, sigo aqui"]
    # The chat shows the reply, because it is on the chat's channel.
    polled = chat.get(f"/api/chat/conversations/{conversation}/messages", params={"after": 0})
    assert [m["sender"] for m in polled.json()["messages"]] == ["candidate", "agent"]


def test_a_typed_message_between_calls_keeps_each_transcript_on_its_channel(
    conn: Any,
    chat: TestClient,
    two_flows: Channel,
    text_hermes: FakeHermes,
    voice_hermes: FakeHermes,
) -> None:
    """The same phone: a call, a typed message, a second call. Each stays on its own channel
    and each spoken utterance still replays its own reply."""
    candidate = _candidate(conn, "voice", "wv_a")
    _speak(two_flows, conn, candidate, "wv_a", "me llamo Ana")
    conversation = start(chat, phone=PHONE)
    chat.post(
        f"/api/chat/conversations/{conversation}/messages",
        json={"message_id": "typed_2", "text": "una duda"},
    )
    request, _ = _speak(two_flows, conn, candidate, "wv_b", "sigo aqui")

    assert [(d, c) for d, c, _ in _rows(conn, candidate["id"])] == [
        ("inbound", "voice"),
        ("outbound", "voice"),  # the spoken disclosure
        ("outbound", "voice"),
        ("inbound", "webchat"),
        ("outbound", "webchat"),
        ("inbound", "voice"),
        ("outbound", "voice"),  # the second call's disclosure
        ("outbound", "voice"),
    ]
    assert len(text_hermes.posts) == 1 and len(voice_hermes.posts) == 2
    again = drain(two_flows.run_spoken_turn(request))
    assert "Vale: sigo aqui" in "".join(e.text for e in again if e.kind == "text")
    assert len(voice_hermes.posts) == 2


def test_a_spoken_turn_on_a_text_application_is_stored_on_voice(
    conn: Any, two_flows: Channel, text_hermes: FakeHermes, voice_hermes: FakeHermes
) -> None:
    """The reverse: the open Application started in the web chat and an utterance reaches it
    (a call is not started on such an Application, ADR 0059, but a call already live, or a text
    Application opened meanwhile, can). The utterance and its reply are voice rows, so the
    replay finds them and the chat transcript gains no spoken line."""
    candidate = _candidate(conn, "webchat", "wc_text_first")
    text_app = repo.start_application(conn, candidate["id"], "webchat", now=NOW)

    request, events = _speak(two_flows, conn, candidate, "wv_late", "me llamo Ana")

    assert events[-1].kind == "done"
    open_app = repo.get_open_application(conn, candidate["id"])
    assert open_app is not None and open_app["id"] == text_app["id"]
    assert _rows(conn, candidate["id"]) == [
        ("inbound", "voice", "spoken"),
        ("outbound", "voice", "spoken"),  # the spoken disclosure
        ("outbound", "voice", "spoken"),
    ]
    assert text_hermes.posts == [] and len(voice_hermes.posts) == 1
    again = drain(two_flows.run_spoken_turn(request))
    said = "".join(e.text for e in events if e.kind == "text")
    assert "".join(e.text for e in again if e.kind == "text") == said
    assert len(voice_hermes.posts) == 1


def test_a_failed_typed_run_and_a_stop_answer_on_the_chat_too(
    conn: Any, chat: TestClient, text_hermes: FakeHermes
) -> None:
    """The fallback line after a failed run and the opt-out confirmation are replies as well."""
    candidate = _candidate(conn, "voice", "wv_fail")
    repo.start_application(conn, candidate["id"], "voice", now=NOW)
    conversation = start(chat, phone=PHONE)

    text_hermes.down = True
    chat.post(
        f"/api/chat/conversations/{conversation}/messages",
        json={"message_id": "typed_3", "text": "hola"},
    )
    chat.post(
        f"/api/chat/conversations/{conversation}/messages",
        json={"message_id": "typed_4", "text": "BAJA"},
    )

    assert _rows(conn, candidate["id"]) == [
        ("inbound", "webchat", "typed"),
        ("outbound", "webchat", "typed"),  # "dame un momento"
        ("inbound", "webchat", "typed"),
        ("outbound", "webchat", "typed"),  # the opt-out confirmation
    ]


def _poll(chat: TestClient, conversation: str) -> list[dict[str, Any]]:
    resp = chat.get(f"/api/chat/conversations/{conversation}/messages", params={"after": 0})
    assert resp.status_code == 200, resp.text
    messages: list[dict[str, Any]] = resp.json()["messages"]
    return messages


def _type(chat: TestClient, conversation: str, message_id: str, text: str) -> None:
    resp = chat.post(
        f"/api/chat/conversations/{conversation}/messages",
        json={"message_id": message_id, "text": text},
    )
    assert resp.status_code == 202, resp.text


def test_the_first_typed_reply_on_a_voice_application_carries_the_privacy_link(
    conn: Any, chat: TestClient, text_hermes: FakeHermes
) -> None:
    """The link follows the reply, not the Application's channel (ADR 0068, 0070)."""
    candidate = _candidate(conn, "voice", "wv_link")
    repo.start_application(conn, candidate["id"], "voice", now=NOW)
    text_hermes.reply = lambda text: "Gracias, seguimos con tus datos."
    conversation = start(chat, phone=PHONE)

    _type(chat, conversation, "typed_link", "hola")

    (reply,) = [m for m in _poll(chat, conversation) if m["sender"] == "agent"]
    assert PRIVACY_URL in reply["text"]


def test_the_spoken_disclosure_counts_for_a_later_typed_reply(
    conn: Any, chat: TestClient, two_flows: Channel, text_hermes: FakeHermes
) -> None:
    """A call said the AI disclosure aloud, so the typed reply does not repeat it (ADR 0044),
    but nothing on a call showed the privacy link, so the first typed reply carries it."""
    candidate = _candidate(conn, "voice", "wv_said")
    _speak(two_flows, conn, candidate, "wv_said", "me llamo Ana")
    text_hermes.reply = lambda text: "Gracias, seguimos con tus datos."
    conversation = start(chat, phone=PHONE)

    _type(chat, conversation, "typed_said", "hola")

    (reply,) = [m for m in _poll(chat, conversation) if m["sender"] == "agent"]
    assert reply["text"].startswith("Gracias, seguimos con tus datos.")
    assert "asistente virtual" not in reply["text"] and PRIVACY_URL in reply["text"]


def test_an_empty_run_on_a_voice_application_sends_the_funnel_question_to_the_chat(
    db_path: Path,
    clock: Any,
    fakes: Fakes,
    conn: Any,
    text_hermes: FakeHermes,
    voice_hermes: FakeHermes,
) -> None:
    """The Zone funnel's fixed question and its buttons (ADR 0068) are a reply: the chat gets
    them, with the privacy link, when the run wrote nothing."""
    channel = _channel(db_path, clock, text_hermes, voice_hermes, pending_choice)
    app_client = FastAPI()
    app_client.include_router(chat_router, prefix="/api/chat")
    app_client.state.criba_channel = channel
    chat = TestClient(app_client)
    candidate = _candidate(conn, "voice", "wv_funnel", phone="+525512345678", country="MX")
    app = repo.start_application(conn, candidate["id"], "voice", now=NOW)
    status, body = call_tool(
        conn,
        "record_answer",
        {"field": "full_name", "value": "Luis Hernández"},
        app["id"],
        now=NOW,
    )
    assert status == 200 and body["ok"], body
    # The tools offer no funnel on a voice Application (`_funnel_applies` in
    # tools/application.py), so the run's offer is written as the tool would write it; the
    # reply that follows is the router's.
    text_hermes.reply = _offers_funnel(db_path, app["id"])
    conversation = start(chat, phone="+525512345678", country="MX", locale="es-MX")

    _type(chat, conversation, "typed_funnel", "I live in Texas!")

    assert _rows(conn, candidate["id"]) == [
        ("inbound", "webchat", "typed"),
        ("outbound", "webchat", "typed"),
    ]
    last = _poll(chat, conversation)[-1]
    assert last["sender"] == "agent" and len(last["interactive"]["options"]) == 5
    assert PRIVACY_URL in last["text"]


def _offers_funnel(db_path: Path, application_id: str, then: str = "") -> Any:
    """A fake agent reply that leaves the Zone funnel's offer in the record and writes ``then``."""

    def reply(text: str) -> str:
        own = connect(db_path)
        try:
            app = repo.require_application(own, application_id)
            found = funnel_options(own, app)
            assert found is not None
            repo.add_event(
                own,
                candidate_id=app["candidate_id"],
                application_id=application_id,
                kind="answer_ambiguous",
                actor="agent",
                data={"field": "zone", "options": found, "funnel": True},
                now=NOW,
            )
        finally:
            own.close()
        return then

    return reply


def _channel(
    db_path: Path, clock: Any, text: FakeHermes, voice: FakeHermes, choice: Any
) -> Channel:
    def client(hermes: FakeHermes) -> RunClient:
        return RunClient(
            base_url=BASE_URL,
            profile=hermes.profile,
            api_key=API_KEY,
            http=hermes.client(),
            sleep=lambda _: None,
        )

    return Channel(
        connect=lambda: connect(db_path),
        run_client=client(text),
        voice_run_client=client(voice),
        clock=clock,
        settings=ChannelSettings(db_path=str(db_path), environment="test"),
        pending_choice=choice,
        interview_offer=lambda conn, application_id, now: {},
    )


def test_an_empty_spoken_run_on_a_text_application_speaks_the_funnel_question_on_voice(
    db_path: Path,
    clock: Any,
    conn: Any,
    text_hermes: FakeHermes,
    voice_hermes: FakeHermes,
) -> None:
    """The reverse of the funnel test above: the fixed question after an empty run answers an
    utterance, so it is a spoken voice row, with no privacy link (nothing on a call shows one)."""
    channel = _channel(db_path, clock, text_hermes, voice_hermes, pending_choice)
    candidate = _candidate(conn, "webchat", "wc_funnel", phone="+525512345679", country="MX")
    app = repo.start_application(conn, candidate["id"], "webchat", now=NOW)
    status, body = call_tool(
        conn,
        "record_answer",
        {"field": "full_name", "value": "Luis Hernández"},
        app["id"],
        now=NOW,
    )
    assert status == 200 and body["ok"], body
    voice_hermes.reply = _offers_funnel(db_path, app["id"])

    _, events = _speak(channel, conn, candidate, "wv_funnel", "vivo en Texas")

    assert events[-1].kind == "done"
    assert text_hermes.posts == [] and len(voice_hermes.posts) == 1
    stored = conn.execute(
        "SELECT direction, channel, modality, body, interactive FROM messages"
        " WHERE candidate_id = ? ORDER BY id",
        (candidate["id"],),
    ).fetchall()
    assert [(r["direction"], r["channel"], r["modality"]) for r in stored] == [
        ("inbound", "voice", "spoken"),
        ("outbound", "voice", "spoken"),  # the spoken disclosure
        ("outbound", "voice", "spoken"),  # the funnel question
    ]
    question = stored[-1]
    assert question["interactive"] is not None and PRIVACY_URL not in question["body"]
    # The call hears the same question, as a text event, before the turn ends.
    spoken = [e.text for e in events if e.kind == "text"]
    assert spoken == [question["body"]]


def _mixed_funnel(
    db_path: Path, clock: Any, conn: Any, text: FakeHermes, voice: FakeHermes, suffix: str
) -> tuple[Channel, dict[str, Any], dict[str, Any]]:
    """A text Application at the Zone question and a channel with the real pending choice."""
    channel = _channel(db_path, clock, text, voice, pending_choice)
    candidate = _candidate(
        conn, "webchat", f"wc_{suffix}", phone=f"+52551234{suffix[-4:]}", country="MX"
    )
    app = repo.start_application(conn, candidate["id"], "webchat", now=NOW)
    status, body = call_tool(
        conn,
        "record_answer",
        {"field": "full_name", "value": "Luis Hernández"},
        app["id"],
        now=NOW,
    )
    assert status == 200 and body["ok"], body
    return channel, candidate, app


def test_the_call_hears_a_streamed_reply_and_not_the_stored_funnel_question(
    db_path: Path, conn: Any, clock: Any, text_hermes: FakeHermes, voice_hermes: FakeHermes
) -> None:
    """The fixed message is said only when the run said nothing: a run that streamed words was
    heard, whatever is stored after it."""
    channel, candidate, app = _mixed_funnel(db_path, clock, conn, text_hermes, voice_hermes, "1001")
    voice_hermes.reply = _offers_funnel(db_path, app["id"], then="Vale, un momento.")

    _, events = _speak(channel, conn, candidate, "wv_heard", "vivo en Texas")

    spoken = "".join(e.text for e in events if e.kind == "text")
    assert spoken == "Vale, un momento." and events[-1].kind == "done"
    last = conn.execute(
        "SELECT body FROM messages WHERE candidate_id = ? ORDER BY id DESC LIMIT 1",
        (candidate["id"],),
    ).fetchone()
    assert last["body"] != spoken  # the question is stored, not said


def test_a_whitespace_only_stream_still_speaks_the_funnel_question(
    db_path: Path, conn: Any, clock: Any, text_hermes: FakeHermes, voice_hermes: FakeHermes
) -> None:
    channel, candidate, app = _mixed_funnel(db_path, clock, conn, text_hermes, voice_hermes, "1002")
    voice_hermes.reply = _offers_funnel(db_path, app["id"], then="   ")

    _, events = _speak(channel, conn, candidate, "wv_blank", "vivo en Texas")

    question = conn.execute(
        "SELECT body FROM messages WHERE candidate_id = ? ORDER BY id DESC LIMIT 1",
        (candidate["id"],),
    ).fetchone()["body"]
    assert [e.text for e in events if e.kind == "text" and e.text.strip()] == [question]


@pytest.mark.parametrize("fails", [False, True])
def test_a_booking_link_is_stored_on_a_call_but_never_read_aloud(
    db_path: Path,
    conn: Any,
    clock: Any,
    text_hermes: FakeHermes,
    voice_hermes: FakeHermes,
    fails: bool,
) -> None:
    """The mixed case: a text Application, a live call and an "other time" utterance. The link
    is a typed row (ADR 0064); the call hears the usual fallback line, not a URL."""
    channel, candidate, app = _mixed_funnel(db_path, clock, conn, text_hermes, voice_hermes, "1003")
    voice_hermes.down = fails  # an empty run otherwise
    voice_hermes.reply = lambda text: ""
    request = _voice_request(conn, candidate, "wv_link", "otro horario")
    inbound = channel.router.accept_inbound(
        conn,
        candidate,
        channel="voice",
        message_id=request.message_id,
        text=request.text,
        modality="spoken",
    )
    turn = inbound.turn
    assert turn is not None
    turn.booking_link = ("https://book.example/x", "Elige hora: https://book.example/x")
    queue = DeltaQueue()

    channel._run_spoken(
        conn,
        app["id"],
        turn,
        request,
        queue,
        message_pk=inbound.message["id"],
        optout_request=False,
    )

    events = drain(queue)
    assert not any("book.example" in e.text for e in events if e.kind == "text")
    assert [e.text for e in events if e.kind == "text"] == [fallback_text(app["language"])]
    stored = conn.execute(
        "SELECT channel, modality, body FROM messages WHERE candidate_id = ? AND direction ="
        " 'outbound' ORDER BY id DESC LIMIT 1",
        (candidate["id"],),
    ).fetchone()
    assert "book.example" in stored["body"] and stored["channel"] == "voice"


def test_a_reply_with_buttons_goes_to_the_chat(conn: Any, chat: TestClient, fakes: Fakes) -> None:
    candidate = _candidate(conn, "voice", "wv_buttons")
    repo.start_application(conn, candidate["id"], "voice", now=NOW)
    fakes.choice = {
        "style": "buttons",
        "options": [{"id": "yes", "label": "Sí"}, {"id": "no", "label": "No"}],
    }
    conversation = start(chat, phone=PHONE)

    _type(chat, conversation, "typed_buttons", "hola")

    last = _poll(chat, conversation)[-1]
    assert [o["id"] for o in last["interactive"]["options"]] == ["yes", "no"]
    assert _rows(conn, candidate["id"])[-1] == ("outbound", "webchat", "typed")


def test_the_unreadable_handover_goes_to_the_chat(
    conn: Any, chat: TestClient, two_flows: Channel, text_hermes: FakeHermes
) -> None:
    """The Jev hold (ADR 0063) answers with a fixed message, on the channel of the message."""
    router: Any = two_flows.router
    router.jev = SimpleNamespace(read=lambda *a, **k: SimpleNamespace(hold=True))
    candidate = _candidate(conn, "voice", "wv_hold")
    repo.start_application(conn, candidate["id"], "voice", now=NOW)
    conversation = start(chat, phone=PHONE)

    _type(chat, conversation, "typed_hold", "asdf")

    assert text_hermes.posts == []
    assert _rows(conn, candidate["id"]) == [
        ("inbound", "webchat", "typed"),
        ("outbound", "webchat", "typed"),
    ]
    assert _poll(chat, conversation)[-1]["sender"] == "system"


def test_the_booking_link_goes_to_the_channel_of_the_tap(conn: Any, two_flows: Channel) -> None:
    """ADR 0064: the link of an "other time" tap, whether the run wrote nothing or wrote
    something without it."""
    candidate = _candidate(conn, "voice", "wv_link_tap")
    app = repo.start_application(conn, candidate["id"], "voice", now=NOW)
    turn = TurnRequest(
        application_id=app["id"],
        input="tap",
        idempotency_key="tap_1",
        channel="webchat",
        booking_link=("https://book.example/x", "Elige hora: https://book.example/x"),
    )
    router = two_flows.router
    for output in ("", "Claro, te escribo luego."):
        reply = router.store_reply(
            conn,
            app,
            turn,
            RunResult("run_x", "completed", output),
            events_before=0,
            attempt=router_attempt(),
        )
        assert reply is not None and reply["channel"] == "webchat"
        assert "https://book.example/x" in reply["body"]


def router_attempt() -> Any:
    return _Attempt()


def test_a_takeover_ends_a_call_on_a_text_application_with_the_handover_on_voice(
    conn: Any, two_flows: Channel
) -> None:
    """`_end_call` speaks the handover (and `speak` stores it) on voice, whatever channel the
    Application started on."""
    candidate = _candidate(conn, "webchat", "wc_taken")
    app = repo.start_application(conn, candidate["id"], "webchat", now=NOW)
    conn.execute(
        "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
        " VALUES ('u1', 'grupo_sazon', 'r@example.com', 'R', 'recruiter', 'x', '2026-10-09')"
    )
    conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'u1' WHERE id = ?",
        (app["id"],),
    )

    _, events = _speak(two_flows, conn, candidate, "wv_taken", "hola")

    assert [e.kind for e in events][-1] == "end_call"
    assert _rows(conn, candidate["id"]) == [
        ("inbound", "voice", "spoken"),
        ("outbound", "voice", "spoken"),
    ]
