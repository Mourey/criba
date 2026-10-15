"""Q42: a STOP / BAJA from a Candidate with no open Application marks them Opted out and
confirms, without creating, starting or reopening any Application."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from criba.channel import copy
from criba.db import repo

from .conftest import FakeHermes, candidate_for, start


def send(client: TestClient, conv: str, text: str) -> None:
    body = {"message_id": str(uuid.uuid4()), "text": text}
    resp = client.post(f"/api/chat/conversations/{conv}/messages", json=body)
    assert resp.status_code == 202, resp.text


def applications(conn, conv: str) -> list[dict]:
    return repo.list_candidate_applications(conn, candidate_for(conn, conv)["id"])


def transcript(conn, conv: str) -> list[dict]:
    """Every message of the Candidate, whatever Application (or none) it belongs to."""
    rows = conn.execute(
        "SELECT * FROM messages WHERE candidate_id = ? ORDER BY id",
        (candidate_for(conn, conv)["id"],),
    ).fetchall()
    return [dict(r) for r in rows]


def events(conn, conv: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM events WHERE candidate_id = ? ORDER BY id",
        (candidate_for(conn, conv)["id"],),
    ).fetchall()
    return [dict(r) for r in rows]


def opted_out(conn, conv: str) -> bool:
    return repo.get_candidate(conn, candidate_for(conn, conv)["id"])["opted_out"]


def add_recruiter(conn) -> None:
    conn.execute(
        "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
        " VALUES ('u1', 'grupo_sazon', 'r@example.com', 'R', 'recruiter', 'x', '2026-10-09')"
    )


def assert_confirmed_without_application(conn, conv: str, locale: str = "es-ES") -> None:
    assert opted_out(conn, conv) is True
    stop, confirmation = transcript(conn, conv)[-2:]
    assert stop["direction"] == "inbound" and stop["application_id"] is None
    assert confirmation["direction"] == "outbound" and confirmation["sender"] == "system"
    assert confirmation["application_id"] is None
    assert confirmation["body"] == copy.OPT_OUT_CONFIRMATION[locale]
    opt_events = [e for e in events(conn, conv) if e["kind"] == "opted_out"]
    assert len(opt_events) == 1 and opt_events[0]["application_id"] is None


@pytest.mark.parametrize("text", ["STOP", "baja"])
def test_stop_from_an_unknown_sender_creates_no_application(
    client: TestClient, conn, hermes: FakeHermes, text: str
) -> None:
    conv = start(client)
    assert applications(conn, conv) == []

    send(client, conv, text)

    assert applications(conn, conv) == []
    assert hermes.posts == []
    assert_confirmed_without_application(conn, conv)
    assert "application_started" not in [e["kind"] for e in events(conn, conv)]


@pytest.mark.parametrize(("country", "locale"), [("ES", "es-ES"), ("MX", "es-MX"), ("ES", "en")])
def test_the_confirmation_without_an_application_follows_the_candidates_language(
    client: TestClient, conn, country: str, locale: str
) -> None:
    conv = start(client, country=country, locale=locale)
    send(client, conv, "STOP")
    assert applications(conn, conv) == []
    assert_confirmed_without_application(conn, conv, locale)


def test_the_confirmation_reaches_the_web_chat_reader(client: TestClient, conn) -> None:
    conv = start(client)
    send(client, conv, "STOP")
    resp = client.get(f"/api/chat/conversations/{conv}/messages")
    assert resp.status_code == 200, resp.text
    assert copy.OPT_OUT_CONFIRMATION["es-ES"] in resp.text


@pytest.mark.parametrize("outcome", ["withdrawn", "rejection"])
def test_stop_after_a_closed_application_starts_no_new_one(
    client: TestClient, conn, clock, hermes: FakeHermes, outcome: str
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    if outcome == "rejection":
        add_recruiter(conn)
        repo.close_application(
            conn,
            app["id"],
            "rejection",
            rejection_reason="other",
            rejection_note="no fit",
            actor="user",
            user_id="u1",
            now=clock.now(),
        )
    else:
        repo.close_application(conn, app["id"], "withdrawn", now=clock.now())
    clock.advance(days=2)
    posts = len(hermes.posts)

    send(client, conv, "STOP")

    [only] = applications(conn, conv)
    assert only["id"] == app["id"] and only["status"] == "closed"
    assert only["outcome"] == outcome
    assert len(hermes.posts) == posts
    assert_confirmed_without_application(conn, conv)
    assert repo.get_open_application(conn, candidate_for(conn, conv)["id"]) is None


def test_stop_within_30_days_of_an_abandoned_application_does_not_reopen_it(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    repo.close_application(conn, app["id"], "abandoned", actor="system", now=clock.now())
    clock.advance(days=3)
    posts = len(hermes.posts)

    send(client, conv, "STOP")

    [only] = applications(conn, conv)
    assert only["status"] == "closed" and only["outcome"] == "abandoned"
    assert "reopened" not in [e["kind"] for e in events(conn, conv)]
    assert len(hermes.posts) == posts
    assert_confirmed_without_application(conn, conv)


def test_stop_after_a_knockout_does_not_reopen_it(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    repo.record_answer(conn, app["id"], "driver_license", False, raw_input="no", now=clock.now())
    repo.close_application(
        conn, app["id"], "knockout", knockout_criterion="driver_license", now=clock.now()
    )
    clock.advance(days=2)
    posts = len(hermes.posts)

    send(client, conv, "STOP")

    [only] = applications(conn, conv)
    assert only["status"] == "closed" and only["outcome"] == "knockout"
    assert len(hermes.posts) == posts  # the agent is not run on the closed Application
    assert_confirmed_without_application(conn, conv)


def test_stop_with_an_open_application_keeps_todays_behaviour(
    client: TestClient, conn, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    posts = len(hermes.posts)

    send(client, conv, "STOP")

    assert opted_out(conn, conv) is True
    assert len(hermes.posts) == posts
    assert len(applications(conn, conv)) == 1
    messages = repo.list_messages(conn, app["id"])
    assert messages[-2]["body"] == "STOP" and messages[-2]["application_id"] == app["id"]
    assert messages[-1]["body"] == copy.OPT_OUT_CONFIRMATION["es-ES"]
    [event] = [e for e in events(conn, conv) if e["kind"] == "opted_out"]
    assert event["application_id"] == app["id"]


def test_a_later_message_lifts_the_opt_out_and_starts_an_application(
    client: TestClient, conn, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "STOP")
    assert applications(conn, conv) == []

    send(client, conv, "hola")

    assert opted_out(conn, conv) is False
    assert "opt_out_lifted" in [e["kind"] for e in events(conn, conv)]
    [started] = applications(conn, conv)
    assert started["status"] == "open"
    assert hermes.inputs == ["hola"]


def test_a_later_message_reopens_an_abandoned_application_after_a_stop(
    client: TestClient, conn, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    repo.close_application(conn, app["id"], "abandoned", actor="system", now=clock.now())
    clock.advance(days=3)
    send(client, conv, "STOP")
    assert applications(conn, conv)[0]["status"] == "closed"

    send(client, conv, "hola otra vez")

    assert opted_out(conn, conv) is False
    [again] = applications(conn, conv)
    assert again["id"] == app["id"] and again["status"] == "open"
    assert hermes.inputs[-1] == "hola otra vez"


def test_stop_only_candidates_add_no_application_rows(client: TestClient, conn) -> None:
    """The results metrics (``api/metrics.py``) count Application rows only."""
    before = conn.execute("SELECT count(*) FROM applications").fetchone()[0]
    for _ in range(3):
        send(client, start(client), "STOP")
    assert conn.execute("SELECT count(*) FROM applications").fetchone()[0] == before
