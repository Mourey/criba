"""Inbound after a Knockout (ADR 0033): the router no longer reopens it.

The message lands on the closed Application and the agent runs on its session, so a
"revisión" can become a Review; Abandoned still reopens on any message.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from criba.channel.copy import knockout_closed
from criba.db import connect, repo
from criba.tools import call_tool

from .conftest import FakeHermes, candidate_for, start


def knockout_closed_note() -> str:
    return knockout_closed("driver_license", "es-ES", "ES")


def send(client: TestClient, conv: str, text: str) -> None:
    body = {"message_id": str(uuid.uuid4()), "text": text}
    resp = client.post(f"/api/chat/conversations/{conv}/messages", json=body)
    assert resp.status_code == 202, resp.text


def knock_out(conn, conv: str, clock) -> dict:
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    repo.record_answer(conn, app["id"], "driver_license", False, raw_input="no", now=clock.now())
    return repo.close_application(
        conn, app["id"], "knockout", knockout_criterion="driver_license", now=clock.now()
    )


def test_revision_after_a_knockout_runs_the_agent_on_the_closed_application(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    closed = knock_out(conn, conv, clock)
    clock.advance(days=2)
    send(client, conv, "revisión")

    app = repo.require_application(conn, closed["id"])
    assert app["status"] == "closed" and app["outcome"] == "knockout"
    assert app["reopened_count"] == 0
    assert hermes.posts[-1]["body"]["session_id"] == closed["id"]
    assert hermes.inputs[-1] == "revisión"
    messages = repo.list_messages(conn, closed["id"])
    # This fake agent requested no Review in its run, so the first message after the Knockout
    # is answered by code's closed note (ADR 0072), which says how to ask for one.
    assert [m["body"] for m in messages[-2:]] == ["revisión", knockout_closed_note()]
    assert len(repo.list_candidate_applications(conn, closed["candidate_id"])) == 1

    args = {"note": "sí tengo"}
    status, body = call_tool(conn, "request_review", args, closed["id"], now=clock.now())
    assert status == 200 and body == {"ok": True, "value": {"status": "review_requested"}}


def test_after_30_days_a_message_starts_a_new_application(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    closed = knock_out(conn, conv, clock)
    clock.advance(days=30, seconds=1)
    send(client, conv, "hola otra vez")

    fresh = repo.get_open_application(conn, closed["candidate_id"])
    assert fresh is not None and fresh["id"] != closed["id"]
    assert hermes.posts[-1]["body"]["session_id"] == fresh["id"]
    assert repo.require_application(conn, closed["id"])["status"] == "closed"


def test_abandoned_still_reopens_on_any_message(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    repo.close_application(conn, app["id"], "abandoned", actor="system", now=clock.now())
    clock.advance(days=3)
    send(client, conv, "perdón, sigo aquí")

    reopened = repo.require_application(conn, app["id"])
    assert reopened["status"] == "open" and reopened["reopened_count"] == 1
    assert hermes.posts[-1]["body"]["session_id"] == app["id"]


def test_an_abandoned_reopen_is_reported_after_the_commit(
    client: TestClient, channel, conn, clock, hermes: FakeHermes, db_path
) -> None:
    """The router's ``on_reopened`` fires once, for the reopening message only, and sees
    the Application already open from a fresh connection (the transaction committed)."""
    seen: list[tuple[str, str]] = []

    def on_reopened(application_id: str) -> None:
        own = connect(db_path)
        try:
            seen.append((application_id, repo.require_application(own, application_id)["status"]))
        finally:
            own.close()

    channel.router.on_reopened = on_reopened
    conv = start(client)
    send(client, conv, "hola")
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    repo.close_application(conn, app["id"], "abandoned", actor="system", now=clock.now())
    clock.advance(days=3)
    send(client, conv, "perdón, sigo aquí")
    send(client, conv, "¿seguimos?")
    assert seen == [(app["id"], "open")]


def test_a_knockout_message_is_not_reported_as_reopened(
    client: TestClient, channel, conn, clock, hermes: FakeHermes
) -> None:
    seen: list[str] = []
    channel.router.on_reopened = seen.append
    conv = start(client)
    send(client, conv, "hola")
    knock_out(conn, conv, clock)
    clock.advance(days=2)
    send(client, conv, "revisión")
    assert seen == []
