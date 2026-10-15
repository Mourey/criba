"""The booking confirmation on the web chat: real ``pending_choice`` and booking tools, a
fake Hermes whose run books the slot named in the Candidate's message (as the agent would
with ``book_interview``)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from criba.channel.config import ChannelSettings
from criba.channel.service import Channel
from criba.channel.webchat import chat_router
from criba.db import connect, iso, repo
from criba.screening.clock import FakeClock
from criba.tools import call_tool, pending_choice, verify_booking_token
from criba.tools.choices import TAP_RESULT_PREFIX

from .conftest import FakeHermes, candidate_for, make_run_client, run_retries, start

GOOD_ANSWERS: list[tuple[str, Any]] = [
    ("full_name", "Ana García"),
    ("zone", ["zone_es_madrid_chamberi"]),
    ("driver_license", "yes"),
    ("availability", ["full_time"]),
    ("preferred_schedule", ["morning"]),
    ("experience_years", 2),
    ("experience_platforms", ["glovo"]),
    ("start_date", "2026-10-12"),
]


@pytest.fixture(autouse=True)
def booking_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "tools_secret.txt"
    path.write_text("test-tools-secret\n", encoding="utf-8")
    monkeypatch.setenv("CRIBA_TOOLS_SECRET_FILE", str(path))
    monkeypatch.delenv("CRIBA_BOOKING_SECRET_FILE", raising=False)


@pytest.fixture
def real_client(db_path: Path, clock: FakeClock, hermes: FakeHermes) -> TestClient:
    channel = Channel(
        connect=lambda: connect(db_path),
        run_client=make_run_client(hermes),
        clock=clock,
        settings=ChannelSettings(db_path=str(db_path), environment="test"),
        pending_choice=pending_choice,
    )
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/chat")
    app.state.criba_channel = channel
    return TestClient(app)


def tool(conn: Any, clock: FakeClock, app_id: str, name: str, **args: Any) -> dict[str, Any]:
    status, body = call_tool(conn, name, args, app_id, now=clock.now())
    assert status == 200 and body["ok"], body
    return body["value"]


def send(client: TestClient, conv: str, text: str) -> None:
    resp = client.post(
        f"/api/chat/conversations/{conv}/messages",
        json={"message_id": str(uuid.uuid4()), "text": text},
    )
    assert resp.status_code == 202, resp.text


def approved_conversation(
    client: TestClient, conn: Any, clock: FakeClock, hermes: FakeHermes
) -> tuple[str, str, list[dict[str, Any]]]:
    """A web chat conversation whose Application is Approved and has slots on offer."""
    conv = start(client)
    hermes.reply = lambda text: "Hola"
    send(client, conv, "hola")
    candidate = candidate_for(conn, conv)
    app_id = repo.get_open_application(conn, candidate["id"])["id"]
    for field, value in GOOD_ANSWERS:
        assert tool(conn, clock, app_id, "record_answer", field=field, value=value)["status"] == (
            "accepted"
        )
    tool(conn, clock, app_id, "finish_screening")
    conn.execute(
        "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
        " VALUES ('user_rec', 'grupo_sazon', 'rec@example.test', 'Rec', 'recruiter', 'x', ?)",
        ("2026-10-09T00:00:00Z",),
    )
    repo.mark_approved(conn, app_id, "user_rec", now=clock.now())
    slots = tool(conn, clock, app_id, "offer_interview_slots")["slots"]
    return conv, app_id, slots


def books_the_ref(db_path: Path, clock: FakeClock, app_id: str, results: list) -> Any:
    def reply(text: str) -> str:
        own = connect(db_path)
        try:
            results.append(tool(own, clock, app_id, "book_interview", slot_ref=text))
        finally:
            own.close()
        return "¡Listo! Tu entrevista está confirmada."

    return reply


def last_agent_message(client: TestClient, conv: str) -> dict[str, Any]:
    page = client.get(f"/api/chat/conversations/{conv}/messages").json()
    return [m for m in page["messages"] if m["sender"] == "agent"][-1]


def test_the_booking_confirmation_carries_no_slot_options(
    real_client: TestClient, conn, clock: FakeClock, hermes: FakeHermes, db_path: Path
) -> None:
    conv, app_id, slots = approved_conversation(real_client, conn, clock, hermes)
    booked: list[dict[str, Any]] = []
    hermes.reply = books_the_ref(db_path, clock, app_id, booked)
    send(real_client, conv, slots[0]["slot_ref"])
    assert booked and booked[0]["stage"] == "interview_booked"
    confirmation = last_agent_message(real_client, conv)
    assert confirmation["text"].startswith("¡Listo!")
    assert confirmation["interactive"] is None


def test_the_booking_confirmation_carries_the_ics_invite(
    real_client: TestClient, conn, clock: FakeClock, hermes: FakeHermes, db_path: Path
) -> None:
    conv, app_id, slots = approved_conversation(real_client, conn, clock, hermes)
    booked: list[dict[str, Any]] = []
    hermes.reply = books_the_ref(db_path, clock, app_id, booked)
    send(real_client, conv, slots[0]["slot_ref"])
    assert booked[0]["ics_sent"] is True
    confirmation = last_agent_message(real_client, conv)
    (invite,) = confirmation["attachments"]
    assert invite["type"] == "ics" and invite["filename"] == "entrevista.ics"
    prefix, suffix = "/api/public/booking/", "/invite.ics"
    assert invite["url"].startswith(prefix) and invite["url"].endswith(suffix)
    token = invite["url"][len(prefix) : -len(suffix)]
    assert verify_booking_token(token, now=clock.now()) == app_id

    # Only the confirmation carries it; earlier and later agent messages do not.
    hermes.reply = lambda text: "De nada."
    send(real_client, conv, "gracias")
    page = real_client.get(f"/api/chat/conversations/{conv}/messages").json()
    with_invite = [m["id"] for m in page["messages"] if m["attachments"]]
    assert with_invite == [confirmation["id"]]


def test_a_released_booking_drops_the_invite(
    real_client: TestClient, conn, clock: FakeClock, hermes: FakeHermes, db_path: Path
) -> None:
    conv, app_id, slots = approved_conversation(real_client, conn, clock, hermes)
    hermes.reply = books_the_ref(db_path, clock, app_id, [])
    send(real_client, conv, slots[0]["slot_ref"])
    conn.execute(
        "UPDATE bookings SET status = 'released', released_at = created_at"
        " WHERE application_id = ?",
        (app_id,),
    )
    assert last_agent_message(real_client, conv)["attachments"] == []


def test_slot_buttons_expire_by_the_channels_clock_not_the_wall_clock(
    real_client: TestClient, conn, clock: FakeClock, hermes: FakeHermes
) -> None:
    """In sim mode the shared clock runs ahead of the wall: offers must lapse by that clock."""
    conv, app_id, _ = approved_conversation(real_client, conn, clock, hermes)
    # Move the channel's clock decades ahead of wall time, with the offers made "now".
    clock.set(datetime(2090, 1, 5, 10, 0, tzinfo=UTC))
    conn.execute(
        "UPDATE slot_offers SET expires_at = ? WHERE application_id = ?",
        (iso(clock.now() + timedelta(hours=48)), app_id),
    )
    hermes.reply = lambda text: "¿Cuál te viene mejor?"
    send(real_client, conv, "hola")
    assert last_agent_message(real_client, conv)["interactive"] is not None

    # 49 hours of sim time later the offers are over. The wall clock is still decades
    # before their expiry, so reading it would keep the buttons live.
    clock.advance(hours=49)
    send(real_client, conv, "sigo aquí")
    assert last_agent_message(real_client, conv)["interactive"] is None


def test_a_slot_tap_is_booked_before_the_run_and_the_reply_carries_the_invite(
    real_client: TestClient, conn, clock: FakeClock, hermes: FakeHermes
) -> None:
    """ADR 0050: the backend books the tapped slot itself; the agent gets the book_interview
    result as a ``[tap result]`` and only writes the confirmation, which carries the .ics."""
    conv, app_id, slots = approved_conversation(real_client, conn, clock, hermes)
    hermes.reply = lambda text: "¿Qué hora te viene mejor?"
    send(real_client, conv, "vale")
    offer = last_agent_message(real_client, conv)
    assert offer["interactive"]["options"][0]["id"] == slots[0]["slot_ref"]

    hermes.reply = lambda text: "¡Listo! Tu entrevista está confirmada."
    resp = real_client.post(
        f"/api/chat/conversations/{conv}/replies",
        json={
            "message_id": str(uuid.uuid4()),
            "in_reply_to": offer["id"],
            "option_id": slots[0]["slot_ref"],
        },
    )
    assert resp.status_code == 202, resp.text
    sent = hermes.inputs[-1]
    assert sent.startswith(TAP_RESULT_PREFIX)
    result = json.loads(sent[len(TAP_RESULT_PREFIX) :])
    assert result["ok"] is True and result["value"]["stage"] == "interview_booked"
    assert result["value"]["ics_sent"] is True
    assert repo.require_application(conn, app_id)["stage"] == "interview_booked"
    confirmation = last_agent_message(real_client, conv)
    assert confirmation["text"].startswith("¡Listo!")
    assert [a["type"] for a in confirmation["attachments"]] == ["ics"]
    assert confirmation["interactive"] is None


# --- "Otro horario" sends the booking link (ADR 0064) ----------------------------------


def tap_other(client: TestClient, conv: str) -> dict[str, Any]:
    """Tap "Otro horario" on the last agent message, which carries the slot buttons."""
    last = last_agent_message(client, conv)
    assert last["interactive"]["options"][-1]["id"] == "other"
    resp = client.post(
        f"/api/chat/conversations/{conv}/replies",
        json={"message_id": str(uuid.uuid4()), "in_reply_to": last["id"], "option_id": "other"},
    )
    assert resp.status_code == 202, resp.text
    return last_agent_message(client, conv)


def at_the_slot_buttons(real_client, conn, clock, hermes):
    conv, app_id, slots = approved_conversation(real_client, conn, clock, hermes)
    hermes.reply = lambda text: "Elige un horario."
    send(real_client, conv, "hola")  # the run attaches the slot buttons to this reply
    return conv, app_id, slots


def token_in(text: str) -> str:
    return text[text.index("http") :].split()[0].rsplit("/book/", 1)[1]


def test_other_time_sends_the_link_whatever_the_agent_writes(
    real_client: TestClient, conn, clock: FakeClock, hermes: FakeHermes
) -> None:
    """The real Haiku reply asked "¿Qué día y hora te vendrían mejor?" and sent no link."""
    conv, app_id, slots = at_the_slot_buttons(real_client, conn, clock, hermes)
    hermes.reply = lambda text: "¿Qué día y hora te vendrían mejor? Lo anoto y lo coordino."
    reply = tap_other(real_client, conv)
    assert hermes.inputs[-1].startswith(TAP_RESULT_PREFIX)
    assert verify_booking_token(token_in(reply["text"]), now=clock.now()) == app_id
    assert "Lo anoto" not in reply["text"]
    # The slot offer is still valid: its buttons stay on the reply, and a slot still books.
    ids = [o["id"] for o in reply["interactive"]["options"]]
    assert ids == [s["slot_ref"] for s in slots] + ["other"]
    booked = tool(conn, clock, app_id, "book_interview", slot_ref=slots[0]["slot_ref"])
    assert booked["stage"] == "interview_booked"


def test_other_time_keeps_the_agents_own_words_when_they_carry_the_link(
    real_client: TestClient, conn, clock: FakeClock, hermes: FakeHermes
) -> None:
    conv, _, _ = at_the_slot_buttons(real_client, conn, clock, hermes)

    def relays(text: str) -> str:
        assert text.startswith(TAP_RESULT_PREFIX)
        value = json.loads(text[len(TAP_RESULT_PREFIX) :])["value"]
        return f"¡Claro! Mira todos los horarios aquí: {value['booking_url']}"

    hermes.reply = relays
    reply = tap_other(real_client, conv)
    assert reply["text"].startswith("¡Claro! Mira todos los horarios aquí: http")


def test_other_time_sends_the_link_when_the_run_says_nothing(
    real_client: TestClient, conn, clock: FakeClock, hermes: FakeHermes
) -> None:
    conv, app_id, _ = at_the_slot_buttons(real_client, conn, clock, hermes)
    hermes.reply = lambda text: ""
    silent = tap_other(real_client, conv)
    assert verify_booking_token(token_in(silent["text"]), now=clock.now()) == app_id


def test_other_time_sends_the_link_when_the_run_fails(
    real_client: TestClient, conn, clock: FakeClock, hermes: FakeHermes
) -> None:
    conv, app_id, _ = at_the_slot_buttons(real_client, conn, clock, hermes)
    hermes.final_status = "failed"
    failed = tap_other(real_client, conv)
    assert "dame un momento" not in failed["text"].lower()
    assert verify_booking_token(token_in(failed["text"]), now=clock.now()) == app_id
    run_retries(real_client.app.state.criba_channel, clock)  # still failing (ADR 0073)
    escalation = conn.execute(
        "SELECT reason FROM queue_items WHERE application_id = ? AND kind = 'escalation'",
        (app_id,),
    ).fetchone()
    assert escalation["reason"] == "technical_error"  # the failure is still reported


def test_other_time_in_english_sends_the_english_message(
    real_client: TestClient, conn, clock: FakeClock, hermes: FakeHermes
) -> None:
    conv, app_id, _ = at_the_slot_buttons(real_client, conn, clock, hermes)
    conn.execute("UPDATE applications SET language = 'en' WHERE id = ?", (app_id,))
    hermes.reply = lambda text: "Which day works best?"
    reply = tap_other(real_client, conv)
    assert reply["text"].startswith("Of course, here you can see every open time")
    assert "/book/" in reply["text"]
