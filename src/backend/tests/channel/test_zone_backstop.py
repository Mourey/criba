"""The router's Zone backstop (ADR 0076): a typed message at the Zone question that the agent's run
left unhandled still ends in the funnel, the Zone Knockout or an Escalation.

Replays the demo chat app_01M4RPWTFKGP1CKJ48TS0MS1K7 (real Haiku, 2026-10-12): an MX web chat in
English; for "I live in Texas!" the agent searched with ``country`` ES and MX, found nothing, wrote
prose and recorded nothing, seven times.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from criba.channel.config import ChannelSettings
from criba.channel.service import Channel
from criba.channel.webchat import chat_router
from criba.db import connect, repo
from criba.tools import call_tool, pending_choice

from .conftest import FakeHermes, candidate_for, make_run_client, start
from .test_zone_funnel_reply import messages, send

PROSE = "Texas isn't one of our zones. We only hire in Spain and Mexico. Where in Mexico?"
OUTSIDE = "none of our locations deliver in your area"  # the English Zone Knockout
QUESTION = "We hire delivery drivers in these cities: Ciudad de México, Guadalajara, Monterrey"


@pytest.fixture
def client(db_path: Path, clock, hermes: FakeHermes) -> TestClient:
    channel = Channel(
        connect=lambda: connect(db_path),
        run_client=make_run_client(hermes),
        clock=clock,
        settings=ChannelSettings(db_path=str(db_path), environment="test"),
        pending_choice=pending_choice,
        interview_offer=lambda conn, application_id, now: {},
    )
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/chat")
    app.state.criba_channel = channel
    return TestClient(app)


def at_zone_in_english(client: TestClient, conn, clock) -> tuple[str, str]:
    conv = start(client, country="MX", locale="en")
    send(client, conv, "Hi, I saw your delivery job")
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    status, body = call_tool(
        conn,
        "record_answer",
        {"field": "full_name", "value": "Cody Walker"},
        app["id"],
        now=clock.now(),
    )
    assert status == 200 and body["ok"], body
    return conv, app["id"]


def haiku(db_path: Path, clock, app_id: str, *countries: str):
    """The demo's agent: one ``search_zones`` per country, nothing recorded, prose."""

    def reply(text: str) -> str:
        own = connect(db_path)
        try:
            for country in countries:
                args = {"query": "Texas", "country": country}
                call_tool(own, "search_zones", args, app_id, now=clock.now())
        finally:
            own.close()
        return PROSE

    return reply


def test_the_demo_pattern_now_gets_the_funnel_then_the_knockout(
    client: TestClient, conn, clock, hermes: FakeHermes, db_path: Path
) -> None:
    conv, app_id = at_zone_in_english(client, conn, clock)
    hermes.reply = haiku(db_path, clock, app_id, "ES", "MX")
    send(client, conv, "I live in Texas!")
    funnel = messages(client, conv)[-1]
    assert funnel["text"].startswith(QUESTION) and PROSE not in funnel["text"]
    assert funnel["interactive"]["options"][-1]["label"] == "None of these"

    send(client, conv, "I live in Texas!")
    knockout = messages(client, conv)[-1]
    app = repo.require_application(conn, app_id)
    assert (app["status"], app["outcome"], app["knockout_criterion"]) == (
        "closed",
        "knockout",
        "zone",
    )
    assert PROSE not in knockout["text"] and OUTSIDE in knockout["text"]
    event = repo.latest_event(conn, app_id, "knockout")
    assert event is not None and f'"message_id":{knockout["id"]}' in event["data"]


def test_a_foreign_search_alone_gets_the_funnel_after_the_run(
    client: TestClient, conn, clock, hermes: FakeHermes, db_path: Path
) -> None:
    conv, app_id = at_zone_in_english(client, conn, clock)
    hermes.reply = haiku(db_path, clock, app_id, "ES")
    send(client, conv, "I live in Texas!")
    last = messages(client, conv)[-1]
    assert last["text"].startswith(QUESTION)
    assert last["interactive"]["style"] == "list"


def test_an_empty_run_after_the_funnel_still_sends_the_knockout(
    client: TestClient, conn, clock, hermes: FakeHermes, db_path: Path
) -> None:
    conv, app_id = at_zone_in_english(client, conn, clock)
    hermes.reply = haiku(db_path, clock, app_id, "MX")
    send(client, conv, "I live in Texas!")
    search = haiku(db_path, clock, app_id, "MX")

    def silent(text: str) -> str:
        search(text)
        return ""

    hermes.reply = silent
    send(client, conv, "I live in Texas!")
    last = messages(client, conv)[-1]
    assert last["sender"] == "agent" and OUTSIDE in last["text"]
    event = repo.latest_event(conn, app_id, "knockout")
    assert event is not None and f'"message_id":{last["id"]}' in event["data"]


def test_prose_with_no_tool_at_all_ends_in_the_funnel_then_an_escalation(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> None:
    conv, app_id = at_zone_in_english(client, conn, clock)
    hermes.reply = lambda text: PROSE
    send(client, conv, "I live in Texas!")
    assert messages(client, conv)[-1]["text"] == PROSE  # the one follow-up
    send(client, conv, "I live in Texas!")
    assert messages(client, conv)[-1]["text"].startswith(QUESTION)
    for _ in range(3):
        send(client, conv, "I live in Texas!")
    app = repo.require_application(conn, app_id)
    assert app["invalid_attempts"] == 3 and app["status"] == "open"
    assert repo.has_open_queue_item(conn, app_id, "escalation")
