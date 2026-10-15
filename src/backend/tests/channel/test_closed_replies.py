"""Code-guaranteed replies on a closed Application (ADR 0072).

The first message after a Knockout is answered by code's closed note; the first reply of a new
Application after a Withdrawn one opens with a welcome back.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from criba.channel.copy import WELCOME_BACK, knockout_closed, welcome_back
from criba.db import connect, repo
from criba.tools import call_tool

from .conftest import FakeHermes, candidate_for, start


def send(client: TestClient, conv: str, text: str) -> None:
    body = {"message_id": str(uuid.uuid4()), "text": text}
    resp = client.post(f"/api/chat/conversations/{conv}/messages", json=body)
    assert resp.status_code == 202, resp.text


def last_reply(conn, application_id: str) -> str:
    return repo.list_messages(conn, application_id)[-1]["body"]


def knock_out(conn, conv: str, clock, criterion: str = "driver_license") -> dict:
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    if criterion == "driver_license":
        repo.record_answer(
            conn, app["id"], "driver_license", False, raw_input="no", now=clock.now()
        )
    return repo.close_application(
        conn, app["id"], "knockout", knockout_criterion=criterion, now=clock.now()
    )


@pytest.mark.parametrize("criterion", ["driver_license", "zone"])
def test_the_first_message_after_a_knockout_gets_the_closed_note(
    client: TestClient, conn, clock, hermes: FakeHermes, criterion: str
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    closed = knock_out(conn, conv, clock, criterion)
    clock.advance(days=1)
    hermes.reply = lambda _text: "No me ha quedado claro. ¿Me lo dices de otra forma?"
    send(client, conv, "hola")

    app = repo.require_application(conn, closed["id"])
    assert hermes.posts[-1]["body"]["session_id"] == closed["id"]  # the agent still ran
    assert last_reply(conn, closed["id"]) == knockout_closed(
        criterion, app["language"], app["country"]
    )

    # The second message is the agent's again.
    send(client, conv, "vale, gracias")
    assert last_reply(conn, closed["id"]) == "No me ha quedado claro. ¿Me lo dices de otra forma?"


def test_a_review_requested_in_the_run_keeps_the_agents_reply(
    client: TestClient, conn, clock, hermes: FakeHermes, db_path
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    closed = knock_out(conn, conv, clock)
    clock.advance(days=1)

    def review(_text: str) -> str:
        own = connect(db_path)
        try:
            status, _ = call_tool(
                own, "request_review", {"note": "x"}, closed["id"], now=clock.now()
            )
            assert status == 200
        finally:
            own.close()
        return "Una persona del equipo lo revisará."

    hermes.reply = review
    send(client, conv, "revisión")
    assert last_reply(conn, closed["id"]) == "Una persona del equipo lo revisará."


def test_a_valid_new_answer_that_reopens_keeps_the_agents_reply(
    client: TestClient, conn, clock, hermes: FakeHermes, db_path
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    closed = knock_out(conn, conv, clock)
    clock.advance(days=1)

    def reopen(_text: str) -> str:
        own = connect(db_path)
        try:
            args = {"field": "driver_license", "value": "yes"}
            status, body = call_tool(own, "record_answer", args, closed["id"], now=clock.now())
            assert status == 200, body
        finally:
            own.close()
        return "¡Qué bien! Sigamos."

    hermes.reply = reopen
    send(client, conv, "ya tengo carnet")
    assert repo.require_application(conn, closed["id"])["status"] == "open"
    assert last_reply(conn, closed["id"]) == "¡Qué bien! Sigamos."


def test_an_empty_run_after_a_knockout_still_sends_the_closed_note(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    closed = knock_out(conn, conv, clock)
    clock.advance(days=1)
    hermes.reply = lambda _text: ""
    send(client, conv, "¿por qué?")
    app = repo.require_application(conn, closed["id"])
    assert last_reply(conn, closed["id"]) == knockout_closed(
        "driver_license", app["language"], app["country"]
    )


def test_a_knockout_linked_to_its_message_counts_messages_after_it(
    channel, conn, clock, client: TestClient
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    closed = knock_out(conn, conv, clock)
    reply = repo.list_messages(conn, closed["id"])[-1]
    repo.link_knockout_message(conn, closed["id"], after_event_id=0, message_id=reply["id"])
    knockout = conn.execute(
        "SELECT id, data, created_at FROM events WHERE application_id = ? AND kind = 'knockout'",
        (closed["id"],),
    ).fetchone()
    count = channel.router._messages_since_knockout
    assert count(conn, closed["id"], knockout) == 0
    send(client, conv, "hola")
    assert count(conn, closed["id"], knockout) == 1


def test_the_closed_note_check_never_raises(channel, conn) -> None:
    app = {"id": "app_missing", "status": "closed", "outcome": "knockout"}
    assert channel.router._closed_note(conn, {**app, "status": "open"}, None, 0) is None
    assert channel.router._closed_note(conn, app, None, 0) is None  # no Knockout event
    assert channel.router._closed_note(None, app, None, 0) is None  # a broken connection


def test_after_withdrawn_a_new_application_opens_with_a_welcome_back(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    first = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    repo.close_application(conn, first["id"], "withdrawn", actor="agent", now=clock.now())
    clock.advance(days=3)
    hermes.reply = lambda _text: "¡Hola! Soy el asistente virtual (IA). ¿Cómo te llamas?"
    send(client, conv, "hola otra vez")

    fresh = repo.get_open_application(conn, first["candidate_id"])
    assert fresh is not None and fresh["id"] != first["id"]
    reply = last_reply(conn, fresh["id"])
    assert reply.startswith(welcome_back(fresh["language"], fresh["country"]) + "\n")
    assert "¿Cómo te llamas?" in reply

    send(client, conv, "Ana Ruiz")
    assert not last_reply(conn, fresh["id"]).startswith(
        welcome_back(fresh["language"], fresh["country"])
    )


def test_a_new_application_after_another_outcome_has_no_welcome_back(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    first = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    repo.close_application(conn, first["id"], "abandoned", actor="system", now=clock.now())
    clock.advance(days=31)
    send(client, conv, "hola otra vez")
    fresh = repo.get_open_application(conn, first["candidate_id"])
    assert fresh is not None and fresh["id"] != first["id"]
    reply = last_reply(conn, fresh["id"])
    assert not any(line in reply for line in WELCOME_BACK.values())


def test_the_welcome_back_check_never_raises(channel) -> None:
    app = {"id": "x", "candidate_id": "y"}
    assert channel.router._welcoming_back(None, app, "texto") == "texto"
