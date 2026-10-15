"""A tap on an answer option is recorded by the backend before the agent runs (ADR 0043), and
the first agent message always discloses the AI (ADR 0044)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from criba.channel.config import ChannelSettings
from criba.channel.service import Channel
from criba.channel.webchat import chat_router
from criba.db import connect, repo
from criba.tools import call_tool, pending_choice
from criba.tools.choices import TAP_RESULT_PREFIX

from .conftest import FakeHermes, candidate_for, first_message, make_run_client, start

TOLUCA = "zone_mx_toluca_toluca"


@pytest.fixture
def channel(db_path: Path, clock, hermes: FakeHermes) -> Channel:
    """The real pending_choice and tap recording (the default), over a fake Hermes."""
    return Channel(
        connect=lambda: connect(db_path),
        run_client=make_run_client(hermes),
        clock=clock,
        settings=ChannelSettings(db_path=str(db_path), environment="test"),
        pending_choice=pending_choice,
        interview_offer=lambda conn, application_id, now: {},
    )


@pytest.fixture
def client(channel: Channel) -> TestClient:
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/chat")
    app.state.criba_channel = channel
    return TestClient(app)


def send(client: TestClient, conv: str, text: str) -> None:
    body = {"message_id": str(uuid.uuid4()), "text": text}
    assert client.post(f"/api/chat/conversations/{conv}/messages", json=body).status_code == 202


def record(conn, app_id: str, clock, field: str, value) -> dict:
    status, body = call_tool(
        conn, "record_answer", {"field": field, "value": value}, app_id, now=clock.now()
    )
    assert status == 200 and body["ok"], body
    return body["value"]


def at_zone_confirmation(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> tuple[str, str, int]:
    """A Mexican Candidate asked "¿Vives en Toluca?": the Sí/No buttons are on the last message."""
    conv = start(client, country="MX", locale="es-MX")
    send(client, conv, "hola")
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    record(conn, app["id"], clock, "full_name", "Luis Hernández")
    record(conn, app["id"], clock, "zone", [TOLUCA])
    send(client, conv, "por la salida a Toluca")  # the run attaches the pending buttons
    question = client.get(f"/api/chat/conversations/{conv}/messages").json()["messages"][-1]
    assert [o["id"] for o in question["interactive"]["options"]] == [TOLUCA, "no"]
    return conv, app["id"], question["id"]


def tap(client: TestClient, conv: str, in_reply_to: int, option_id: str) -> str:
    message_id = str(uuid.uuid4())
    resp = client.post(
        f"/api/chat/conversations/{conv}/replies",
        json={"message_id": message_id, "in_reply_to": in_reply_to, "option_id": option_id},
    )
    assert resp.status_code == 202, resp.text
    return message_id


def test_the_agent_receives_the_recorded_result_for_a_tap(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> None:
    conv, app_id, question = at_zone_confirmation(client, conn, clock, hermes)
    tap(client, conv, question, "no")
    sent = hermes.inputs[-1]
    assert sent.startswith(TAP_RESULT_PREFIX)
    body = json.loads(sent[len(TAP_RESULT_PREFIX) :])
    assert body["ok"] is False and body["reason"] == "zone_not_confirmed"
    assert repo.require_application(conn, app_id)["invalid_attempts"] == 0


def test_a_knockout_tap_links_the_agents_reply_to_the_knockout(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> None:
    """The Knockout is written before the run starts, so the run must still link its reply."""
    conv, app_id, question = at_zone_confirmation(client, conn, clock, hermes)
    hermes.reply = lambda text: "Gracias por tu interés. Por ahora no cubrimos tu zona."
    tap(client, conv, question, TOLUCA)
    assert json.loads(hermes.inputs[-1][len(TAP_RESULT_PREFIX) :])["value"]["status"] == "knockout"
    app = repo.require_application(conn, app_id)
    assert app["status"] == "closed" and app["outcome"] == "knockout"
    event = conn.execute(
        "SELECT data FROM events WHERE application_id = ? AND kind = 'knockout'", (app_id,)
    ).fetchone()
    reply = repo.list_messages(conn, app_id)[-1]
    assert reply["sender"] == "agent"
    assert json.loads(event["data"])["message_id"] == reply["id"]


def test_a_tap_that_is_not_an_answer_reaches_the_agent_as_its_id(
    client: TestClient, conn, hermes: FakeHermes, channel: Channel
) -> None:
    """An option the backend cannot map to a field or an offered slot (here a slot id that was
    never offered) is the agent's to handle."""
    channel.router.pending_choice = lambda conn, application_id, *, now: {
        "style": "buttons",
        "options": [{"id": "so_abc", "label": "Lunes 10:00"}, {"id": "other", "label": "Otro"}],
    }
    conv = start(client)
    send(client, conv, "hola")
    question = client.get(f"/api/chat/conversations/{conv}/messages").json()["messages"][-1]
    tap(client, conv, question["id"], "so_abc")
    assert hermes.inputs[-1] == "so_abc"


def test_without_a_recorder_the_tap_is_the_bare_id(
    client: TestClient, conn, clock, hermes: FakeHermes, channel: Channel
) -> None:
    conv, _, question = at_zone_confirmation(client, conn, clock, hermes)
    channel.router.record_tap = None
    tap(client, conv, question, "no")
    assert hermes.inputs[-1] == "no"


def test_a_recorder_that_fails_leaves_the_bare_id(
    client: TestClient, conn, clock, hermes: FakeHermes, channel: Channel
) -> None:
    conv, _, question = at_zone_confirmation(client, conn, clock, hermes)

    def broken(*args, **kwargs):
        raise RuntimeError("disk I/O")

    channel.router.record_tap = broken
    tap(client, conv, question, "no")
    assert hermes.inputs[-1] == "no"


# --------------------------------------------------------------------------- AI disclosure


def test_the_first_agent_message_discloses_the_ai(
    client: TestClient, conn, hermes: FakeHermes
) -> None:
    hermes.reply = lambda text: "¡Hola! Soy el asistente virtual de Grupo Sazón. ¿Cómo te llamas?"
    conv = start(client)
    send(client, conv, "hola")
    hermes.reply = lambda text: "Gracias. ¿En qué zona vives?"
    send(client, conv, "Ana López")
    texts = [
        m["text"] for m in client.get(f"/api/chat/conversations/{conv}/messages").json()["messages"]
    ]
    assert texts[1] == first_message(
        "¡Hola! Soy el asistente virtual (IA) de Grupo Sazón. ¿Cómo te llamas?"
    )
    assert texts[3] == "Gracias. ¿En qué zona vives?"  # said once, not on every message


def test_a_first_message_with_no_disclosure_gets_the_sentence(
    client: TestClient, conn, hermes: FakeHermes
) -> None:
    hermes.reply = lambda text: "¿Cómo te llamas?"
    conv = start(client, country="ES")
    send(client, conv, "hola")
    first = client.get(f"/api/chat/conversations/{conv}/messages").json()["messages"][1]["text"]
    assert first == first_message("Soy un asistente virtual (IA) de Grupo Sazón. ¿Cómo te llamas?")


def test_a_failing_disclosure_check_sends_the_text_as_written(
    client: TestClient, conn, hermes: FakeHermes, monkeypatch: pytest.MonkeyPatch
) -> None:
    from criba.channel import router as router_module

    def broken(*args, **kwargs):
        raise RuntimeError("regex blew up")

    monkeypatch.setattr(router_module, "with_ai_disclosure", broken)
    hermes.reply = lambda text: "¿Cómo te llamas?"
    conv = start(client)
    send(client, conv, "hola")
    first = client.get(f"/api/chat/conversations/{conv}/messages").json()["messages"][1]["text"]
    assert first == "¿Cómo te llamas?"


def test_the_default_recorder_is_a_no_op_without_the_tools(
    conn, clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from criba.channel import ports

    monkeypatch.setattr(ports, "_lookup", lambda module, name: None)
    assert ports.default_record_tap(conn, "app_x", "yes", now=clock.now()) is None
    assert ports.default_pending_choice(conn, "app_x", now=clock.now()) is None
