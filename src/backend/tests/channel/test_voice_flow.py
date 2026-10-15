"""Voice is its own flow (ADR 0059): its own Hermes profile, its own `voice` channel, no voice
rules in the run input, and a voice-only conversation that starts a call.

Every test runs on the fake Hermes of ``conftest.py`` through the real run client: no model, no
ElevenLabs. Secrets are low-entropy words written to ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient

from criba.api.sim import SimSettingsError, check_sim_settings
from criba.channel.config import ChannelSettings
from criba.channel.elevenlabs import FakeElevenLabsClient
from criba.channel.router import APPLICATION_BLOCK_RULE, spoken_run_input
from criba.channel.run_client import RunClient
from criba.channel.sender import Sender
from criba.channel.service import Channel, SpokenTurnRequest
from criba.channel.voice_config import VoiceSettings
from criba.channel.voice_session import VoiceRuntime
from criba.db import connect, repo
from criba.db.connection import iso
from criba.screening.clock import FakeClock

from .conftest import (
    API_KEY,
    BASE_URL,
    NOW,
    FakeHermes,
    Fakes,
    drain,
    start,
    voice_session,
)

VOICE_PROFILE = "criba-recruiter-voice"
CONTRACT = Path(__file__).resolve().parents[4] / "contracts" / "webchat.openapi.yaml"


# --------------------------------------------------------------------------- the profile


@pytest.mark.parametrize(
    ("env", "text", "voice"),
    [
        ({}, "criba-recruiter", "criba-recruiter-voice"),
        ({"CRIBA_HERMES_PROFILE": "criba-recruiter-sim"}, "criba-recruiter-sim",
         "criba-recruiter-voice-sim"),
        ({"CRIBA_HERMES_VOICE_PROFILE": "criba-recruiter-voice-sim"}, "criba-recruiter",
         "criba-recruiter-voice-sim"),
    ],
)  # fmt: skip
def test_settings_name_the_voice_profile(env: dict[str, str], text: str, voice: str) -> None:
    settings = ChannelSettings.from_env(env)
    assert (settings.hermes_profile, settings.hermes_voice_profile) == (text, voice)
    assert ChannelSettings().hermes_voice_profile == "criba-recruiter-voice"


def test_from_env_runs_spoken_turns_on_the_voice_profile(tmp_path: Path) -> None:
    key = tmp_path / "key.txt"
    key.write_text("profile-key-for-tests\n", encoding="utf-8")
    settings = ChannelSettings(db_path=str(tmp_path / "x.db"), hermes_api_key_file=str(key))
    channel = Channel.from_env(settings=settings)
    assert channel.router.run_client.profile == "criba-recruiter"
    assert channel.voice_run_client.profile == VOICE_PROFILE


def test_an_injected_run_client_serves_both_flows(tmp_path: Path) -> None:
    """Tests and the sim harness inject one fake; nothing reads a key file then."""
    injected = FakeHermes()
    client = RunClient(base_url=BASE_URL, profile="x", api_key=API_KEY, http=injected.client())
    settings = ChannelSettings(db_path=str(tmp_path / "x.db"), hermes_api_key_file="/missing")
    channel = Channel.from_env(settings=settings, run_client=client)
    assert channel.voice_run_client is client and channel.router.run_client is client


@pytest.mark.parametrize(
    ("env", "refused"),
    [
        ({"CRIBA_ENV": "sim", "CRIBA_HERMES_PROFILE": "criba-recruiter-sim",
          "CRIBA_HERMES_VOICE_PROFILE": "criba-recruiter-voice"}, True),
        ({"CRIBA_HERMES_PROFILE": "criba-recruiter",
          "CRIBA_HERMES_VOICE_PROFILE": "criba-recruiter-voice-sim"}, True),
        ({"CRIBA_ENV": "sim", "CRIBA_HERMES_PROFILE": "criba-recruiter-sim",
          "CRIBA_HERMES_VOICE_PROFILE": "criba-recruiter-voice-sim"}, False),
        ({"CRIBA_ENV": "sim", "CRIBA_HERMES_PROFILE": "criba-recruiter-sim"}, False),
        ({"CRIBA_HERMES_VOICE_PROFILE": "criba-recruiter-voice"}, False),
    ],
)  # fmt: skip
def test_sim_mode_covers_the_voice_profile(env: dict[str, str], refused: bool) -> None:
    """ADR 0036 on the second profile: sim traffic never reaches a real model, and production
    traffic never runs on the stub."""
    if refused:
        with pytest.raises(SimSettingsError, match="CRIBA_HERMES_VOICE_PROFILE"):
            check_sim_settings(env)
    else:
        check_sim_settings(env)


@pytest.fixture
def text_hermes() -> FakeHermes:
    return FakeHermes()


@pytest.fixture
def voice_hermes() -> FakeHermes:
    hermes = FakeHermes(profile=VOICE_PROFILE)
    hermes.reply = lambda text: f"Vale: {text.rsplit(chr(10), 1)[-1]}"
    return hermes


@pytest.fixture
def flow_channel(
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


def _utterance(conn: Any, text: str = "me llamo Ana") -> SpokenTurnRequest:
    session_id, candidate = voice_session(conn, "wv_flow")
    return SpokenTurnRequest(
        conversation_id="wv_flow",
        candidate_id=candidate["id"],
        voice_session_id=session_id,
        message_id=f"vc_{session_id}_1",
        text=text,
    )


def test_a_spoken_turn_runs_on_the_voice_profile_only(
    conn: Any, flow_channel: Channel, text_hermes: FakeHermes, voice_hermes: FakeHermes
) -> None:
    request = _utterance(conn)
    events = drain(flow_channel.run_spoken_turn(request))

    assert events[-1].kind == "done"
    assert text_hermes.posts == []
    assert len(voice_hermes.posts) == 1
    app = repo.get_open_application(conn, request.candidate_id)
    assert app is not None
    # Its own Hermes session: the voice profile's, keyed by the Application id.
    assert voice_hermes.posts[0]["body"]["session_id"] == app["id"]
    assert voice_hermes.posts[0]["idempotency_key"] == request.message_id
    usage = conn.execute(
        "SELECT profile FROM usage WHERE application_id = ?", (app["id"],)
    ).fetchall()
    assert [row["profile"] for row in usage] == [VOICE_PROFILE]


def test_a_failed_spoken_run_is_recorded_under_the_voice_profile(
    conn: Any, flow_channel: Channel, voice_hermes: FakeHermes
) -> None:
    voice_hermes.down = True
    request = _utterance(conn)
    drain(flow_channel.run_spoken_turn(request))
    app = repo.get_open_application(conn, request.candidate_id)
    assert app is not None
    usage = conn.execute(
        "SELECT profile, status FROM usage WHERE application_id = ?", (app["id"],)
    ).fetchall()
    assert [row["profile"] for row in usage] == [VOICE_PROFILE]
    assert usage[0]["status"] != "ok"


def test_a_spoken_run_input_carries_no_voice_rules(
    conn: Any, flow_channel: Channel, voice_hermes: FakeHermes
) -> None:
    request = _utterance(conn)
    drain(flow_channel.run_spoken_turn(request))
    sent = voice_hermes.posts[0]["body"]["input"].split("\n")
    assert sent[0].startswith("[application] {")
    assert sent[1:] == [APPLICATION_BLOCK_RULE, "[voice]", "me llamo Ana"]
    app = repo.get_open_application(conn, request.candidate_id)
    asked = spoken_run_input(conn, app, "baja", optout_request=True, now=NOW).split("\n")
    assert asked[1:] == [APPLICATION_BLOCK_RULE, "[voice]", "[optout_request]", "baja"]
    assert not any(line.startswith("- ") for line in asked)


def test_a_typed_turn_still_runs_on_the_text_profile(
    conn: Any, flow_channel: Channel, text_hermes: FakeHermes, voice_hermes: FakeHermes
) -> None:
    candidate, _ = repo.get_or_create_candidate(
        conn, client_id="grupo_sazon", channel="webchat", external_id="wc_typed", country="ES"
    )
    inbound = flow_channel.router.accept_inbound(
        conn, candidate, channel="webchat", message_id="m_typed", text="hola"
    )
    assert inbound.turn is not None
    flow_channel.run_turn(inbound.turn)
    assert [p["body"]["input"] for p in text_hermes.posts] == ["hola"]
    assert voice_hermes.posts == []


def test_spoken_turns_are_recorded_on_the_voice_channel(conn: Any, flow_channel: Channel) -> None:
    request = _utterance(conn)
    drain(flow_channel.run_spoken_turn(request))
    app = repo.get_open_application(conn, request.candidate_id)
    assert app is not None and app["channel"] == "voice"
    stored = conn.execute(
        "SELECT direction, channel, modality FROM messages WHERE candidate_id = ? ORDER BY id",
        (request.candidate_id,),
    ).fetchall()
    assert [tuple(r) for r in stored] == [
        ("inbound", "voice", "spoken"),
        ("outbound", "voice", "spoken"),  # the spoken disclosure
        ("outbound", "voice", "spoken"),
    ]


def test_a_resent_utterance_replays_from_the_voice_channel(
    conn: Any, flow_channel: Channel, voice_hermes: FakeHermes
) -> None:
    request = _utterance(conn)
    first = drain(flow_channel.run_spoken_turn(request))
    again = drain(flow_channel.run_spoken_turn(request))
    said = "".join(e.text for e in first if e.kind == "text")
    assert "".join(e.text for e in again if e.kind == "text") == said
    assert len(voice_hermes.posts) == 1


def test_the_voice_adapter_stores_without_delivering(conn: Any) -> None:
    sender = Sender(clock=FakeClock(NOW))
    assert set(sender.adapters) == {"webchat", "whatsapp", "voice"}
    assert sender.adapters["voice"].deliver({"id": 1}) is None


def test_approval_on_a_voice_application_runs_nothing(conn: Any, flow_channel: Channel) -> None:
    """No messaging channel reaches a voice-only Candidate between calls (ADR 0059)."""
    candidate, _ = repo.get_or_create_candidate(
        conn, client_id="grupo_sazon", channel="voice", external_id="wv_ok", country="ES"
    )
    app = repo.start_application(conn, candidate["id"], "voice", now=NOW)
    conn.execute(
        "UPDATE applications SET stage = 'approved', approved_at = ? WHERE id = ?",
        (iso(NOW), app["id"]),
    )
    action = flow_channel.router.action_for_event(conn, app["id"], "approved")
    assert (action.kind, action.reason) == ("none", "voice_no_messaging")
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM messages WHERE application_id = ?", (app["id"],)
        ).fetchone()[0]
        == 0
    )


# --------------------------------------------------------------------------- the endpoints


@pytest.fixture(scope="module")
def components() -> dict[str, Any]:
    return dict(yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["components"])


def check(components: dict[str, Any], name: str, value: object) -> None:
    schema = {"$ref": f"#/components/schemas/{name}", "components": components}
    jsonschema.Draft202012Validator(schema).validate(value)


def test_a_voice_conversation_starts_on_the_voice_channel(
    client: TestClient, conn: Any, components: dict[str, Any]
) -> None:
    check(components, "StartConversationRequest", {"country": "ES", "channel": "voice"})
    resp = client.post("/api/chat/conversations", json={"country": "ES", "channel": "voice"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    check(components, "Conversation", body)
    assert body["channel"] == "voice"
    assert repo.find_candidate_by_identity(conn, "voice", body["conversation_id"]) is not None
    assert repo.find_candidate_by_identity(conn, "webchat", body["conversation_id"]) is None
    reread = client.get(f"/api/chat/conversations/{body['conversation_id']}")
    assert reread.status_code == 200 and reread.json()["channel"] == "voice"


def test_a_web_chat_conversation_is_unchanged(
    client: TestClient, conn: Any, components: dict[str, Any]
) -> None:
    conversation_id = start(client)
    reread = client.get(f"/api/chat/conversations/{conversation_id}").json()
    check(components, "Conversation", reread)
    assert reread["channel"] == "webchat"
    sent = client.post(
        f"/api/chat/conversations/{conversation_id}/messages",
        json={"message_id": "c1", "text": "hola"},
    )
    assert sent.status_code == 202


def test_a_voice_conversation_takes_no_typed_message_or_tap(client: TestClient, conn: Any) -> None:
    conversation_id = client.post(
        "/api/chat/conversations", json={"country": "ES", "channel": "voice"}
    ).json()["conversation_id"]
    typed = client.post(
        f"/api/chat/conversations/{conversation_id}/messages",
        json={"message_id": "c1", "text": "hola"},
    )
    tap = client.post(
        f"/api/chat/conversations/{conversation_id}/replies",
        json={"message_id": "c2", "in_reply_to": 1, "option_id": "yes"},
    )
    assert (typed.status_code, tap.status_code) == (404, 404)
    assert typed.json()["error"] == "not_found"
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_a_voice_conversation_reads_its_transcript(
    client: TestClient, conn: Any, flow_channel: Channel, components: dict[str, Any]
) -> None:
    client.app.state.criba_channel = flow_channel
    conversation_id = client.post(
        "/api/chat/conversations", json={"country": "ES", "channel": "voice"}
    ).json()["conversation_id"]
    candidate = repo.find_candidate_by_identity(conn, "voice", conversation_id)
    assert candidate is not None
    session_id, _ = voice_session(conn, conversation_id)
    request = SpokenTurnRequest(
        conversation_id=conversation_id,
        candidate_id=candidate["id"],
        voice_session_id=session_id,
        message_id=f"vc_{session_id}_1",
        text="me llamo Ana",
    )
    drain(flow_channel.run_spoken_turn(request))

    page = client.get(f"/api/chat/conversations/{conversation_id}/messages").json()
    check(components, "MessagePage", page)
    assert [(m["direction"], m["modality"]) for m in page["messages"]] == [
        ("inbound", "spoken"),
        ("outbound", "spoken"),
        ("outbound", "spoken"),
    ]
    assert page["messages"][0]["text"] == "me llamo Ana"
    reread = client.get(f"/api/chat/conversations/{conversation_id}").json()
    assert reread["cursor"] == page["cursor"]


def _voice(tmp_path: Path) -> VoiceRuntime:
    env: dict[str, str] = {
        "CRIBA_VOICE": "on",
        "CRIBA_ENV": "test",
        "CRIBA_VOICE_AGENT_ES": "agent-es",
        "CRIBA_VOICE_AGENT_MX": "agent-mx",
        "CRIBA_VOICE_PUBLIC_BASE_URL": "https://voice.example",
    }
    for name, var in (
        ("elevenlabs_api_key", "CRIBA_ELEVENLABS_API_KEY_FILE"),
        ("criba_voice_secret", "CRIBA_VOICE_SECRET_FILE"),
        ("criba_voice_llm_secret", "CRIBA_VOICE_LLM_SECRET_FILE"),
        ("elevenlabs_webhook_secret", "CRIBA_ELEVENLABS_WEBHOOK_SECRET_FILE"),
    ):
        path = tmp_path / f"{name}.txt"
        path.write_text(f"voice-secret-for-tests-{name}\n", encoding="utf-8")
        env[var] = str(path)
    return VoiceRuntime(settings=VoiceSettings.from_env(env), client=FakeElevenLabsClient())


def test_a_call_starts_only_on_a_voice_conversation(
    client: TestClient, conn: Any, tmp_path: Path
) -> None:
    client.app.state.criba_voice = _voice(tmp_path)
    voice_conversation = client.post(
        "/api/chat/conversations", json={"country": "ES", "channel": "voice"}
    ).json()["conversation_id"]
    chat_conversation = start(client)

    started = client.post(f"/api/chat/conversations/{voice_conversation}/voice-session")
    refused = client.post(f"/api/chat/conversations/{chat_conversation}/voice-session")

    assert started.status_code == 201, started.text
    assert refused.status_code == 409
    assert refused.json()["error"] == "voice_refused"
    assert "chat" not in refused.json()["message"].lower()
    rows = conn.execute("SELECT conversation_id FROM voice_sessions").fetchall()
    assert [r["conversation_id"] for r in rows] == [voice_conversation]


def test_a_call_is_refused_while_a_text_application_is_open(
    client: TestClient, conn: Any, tmp_path: Path
) -> None:
    """Continuing a text Application by voice is out of scope (ADR 0059): the same Candidate
    (by phone) with an open web chat Application gets the generic 409."""
    client.app.state.criba_voice = _voice(tmp_path)
    phone = "+34612345678"
    chat = start(client, phone=phone)
    sent = client.post(
        f"/api/chat/conversations/{chat}/messages", json={"message_id": "c1", "text": "hola"}
    )
    assert sent.status_code == 202
    voice = client.post(
        "/api/chat/conversations", json={"country": "ES", "channel": "voice", "phone": phone}
    ).json()["conversation_id"]
    resp = client.post(f"/api/chat/conversations/{voice}/voice-session")
    assert resp.status_code == 409
    assert resp.json()["error"] == "voice_refused"
    assert conn.execute("SELECT COUNT(*) FROM voice_sessions").fetchone()[0] == 0


def test_a_call_is_refused_while_a_text_application_can_reopen(
    client: TestClient, conn: Any, tmp_path: Path
) -> None:
    """A call would reopen an Abandoned web chat Application (or reach a closed Knockout one)
    within 30 days and speak on the chat's channel: a text Application continued by voice, so
    it gets the same 409 (ADR 0059)."""
    client.app.state.criba_voice = _voice(tmp_path)
    phone = "+34612345679"
    chat = start(client, phone=phone)
    candidate = repo.find_candidate_by_identity(conn, "webchat", chat)
    assert candidate is not None
    app = repo.start_application(conn, candidate["id"], "webchat", now=NOW)
    conn.execute(
        "UPDATE applications SET status = 'closed', outcome = 'abandoned', closed_at = ?"
        " WHERE id = ?",
        (iso(NOW), app["id"]),
    )
    voice = client.post(
        "/api/chat/conversations", json={"country": "ES", "channel": "voice", "phone": phone}
    ).json()["conversation_id"]
    resp = client.post(f"/api/chat/conversations/{voice}/voice-session")
    assert resp.status_code == 409
    assert resp.json()["error"] == "voice_refused"
