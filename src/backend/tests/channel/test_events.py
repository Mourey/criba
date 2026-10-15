"""Router event-to-action table (Q41): the turn after Approval."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from criba.channel.router import APPROVED_TURN
from criba.channel.service import Channel
from criba.db import iso, repo

from .conftest import NOW, FakeHermes, Fakes, candidate_for, start

OFFER = {
    "slots": [
        {"slot_ref": "so_1", "label": "mar 13 · 10:00", "interview": {}},
        {"slot_ref": "so_2", "label": "mar 13 · 16:00", "interview": {}},
    ],
    "booking_url": "https://empleo.gruposazon.example/book/tok123",
    "other_label": "Otro horario",
}


def approve(conn, application_id: str) -> None:
    conn.execute(
        "UPDATE applications SET stage = 'approved', approved_at = ? WHERE id = ?",
        (iso(NOW), application_id),
    )


def webchat_app(client: TestClient, conn) -> tuple[str, dict]:
    conv = start(client)
    client.post(
        f"/api/chat/conversations/{conv}/messages", json={"message_id": "m0", "text": "hola"}
    )
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    approve(conn, app["id"])
    return conv, app


def whatsapp_app(conn, last_inbound: datetime | None) -> dict:
    candidate, _ = repo.get_or_create_candidate(
        conn, client_id="grupo_sazon", channel="whatsapp", external_id="34600", country="ES"
    )
    app = repo.start_application(conn, candidate["id"], "whatsapp", now=NOW)
    if last_inbound:
        conn.execute(
            "UPDATE applications SET last_inbound_at = ? WHERE id = ?",
            (iso(last_inbound), app["id"]),
        )
    approve(conn, app["id"])
    return app


def test_approved_on_web_chat_runs_a_hidden_system_turn(
    client: TestClient, conn, channel: Channel, hermes: FakeHermes
) -> None:
    conv, app = webchat_app(client, conn)
    hermes.reply = lambda text: "¡Buenas noticias! ¿Cuándo te viene bien la entrevista?"

    action = channel.handle_event(app["id"], "approved")

    assert action.kind == "run"
    assert hermes.posts[-1]["body"] == {"input": APPROVED_TURN, "session_id": app["id"]}
    assert hermes.posts[-1]["idempotency_key"] == f"system:approved:{app['id']}:{iso(NOW)}"
    messages = client.get(f"/api/chat/conversations/{conv}/messages").json()["messages"]
    texts = [m["text"] for m in messages]
    assert APPROVED_TURN not in texts
    assert texts[-1].startswith("¡Buenas noticias!")
    assert all(m["sender"] != "system" for m in messages)
    assert conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 2


def test_the_system_turn_is_an_event_and_never_a_message(
    client: TestClient, conn, channel: Channel, hermes: FakeHermes
) -> None:
    conv, app = webchat_app(client, conn)
    hermes.reply = lambda text: "¡Buenas noticias! ¿Cuándo te viene bien la entrevista?"
    channel.handle_event(app["id"], "approved")

    [event] = [e for e in repo.list_events(conn, app["id"]) if e["kind"] == "system_turn"]
    assert event["actor"] == "system" and event["data"] == {"turn": APPROVED_TURN}
    stored = [m["body"] for m in repo.list_messages(conn, app["id"])]
    assert APPROVED_TURN not in stored
    transcript = client.get(f"/api/chat/conversations/{conv}/messages").json()["messages"]
    assert all(APPROVED_TURN not in m["text"] for m in transcript)


def test_approved_twice_never_runs_twice(conn, client: TestClient, channel, hermes) -> None:
    _, app = webchat_app(client, conn)
    channel.handle_event(app["id"], "approved")
    channel.handle_event(app["id"], "approved")
    assert hermes.inputs.count(APPROVED_TURN) == 2  # same Idempotency-Key both times
    assert len(hermes.runs) == 2  # the first message's run plus one system run


def test_approved_in_quiet_hours_is_a_plain_not_now(conn, client, channel, clock, hermes) -> None:
    """The channel never holds: it answers not_now, and the scheduler holds the action
    (tests/channel/test_scheduling.py)."""
    _, app = webchat_app(client, conn)
    clock.set(datetime(2026, 10, 9, 23, 0, tzinfo=ZoneInfo("Europe/Madrid")))
    action = channel.handle_event(app["id"], "approved")
    assert (action.kind, action.reason) == ("not_now", "quiet_hours")
    assert not hasattr(action, "not_before")
    assert APPROVED_TURN not in hermes.inputs
    clock.set(datetime(2026, 10, 10, 9, 0, tzinfo=ZoneInfo("Europe/Madrid")))
    assert channel.handle_event(app["id"], "approved").kind == "run"


def test_whatsapp_inside_window_runs(conn, channel: Channel, hermes: FakeHermes) -> None:
    app = whatsapp_app(conn, last_inbound=NOW - timedelta(hours=3))
    action = channel.handle_event(app["id"], "approved")
    assert action.kind == "run"
    assert hermes.inputs == [APPROVED_TURN]
    assert action.message is not None and action.message["channel"] == "whatsapp"


def test_whatsapp_outside_window_sends_template_without_a_run(
    conn, channel: Channel, hermes: FakeHermes, fakes: Fakes
) -> None:
    fakes.offer = OFFER
    app = whatsapp_app(conn, last_inbound=NOW - timedelta(hours=30))
    action = channel.handle_event(app["id"], "approved")
    assert action.kind == "template"
    assert hermes.posts == []
    message = action.message
    assert message["content_type"] == "template" and message["template_name"] == "interview_offer"
    assert OFFER["booking_url"] in message["body"]
    assert message["interactive"] == {
        "style": "buttons",
        "options": [
            {"id": "so_1", "label": "mar 13 · 10:00"},
            {"id": "so_2", "label": "mar 13 · 16:00"},
            {"id": "other", "label": "Otro horario"},
        ],
    }
    outbox = channel.sender.adapters["whatsapp"].outbox  # type: ignore[attr-defined]
    assert outbox == [message]


def test_template_links_show_on_web_chat_transcript(client, conn, channel) -> None:
    conv = start(client)
    client.post(f"/api/chat/conversations/{conv}/messages", json={"message_id": "a", "text": "x"})
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    channel.send(
        app["id"], f"Reserva aquí: {OFFER['booking_url']}", template_name="interview_offer"
    )
    last = client.get(f"/api/chat/conversations/{conv}/messages").json()["messages"][-1]
    assert last["links"] == [{"url": OFFER["booking_url"], "label": OFFER["booking_url"]}]


def test_offer_failure_raises_an_escalation(conn, channel: Channel) -> None:
    app = whatsapp_app(conn, last_inbound=None)
    action = channel.handle_event(app["id"], "approved")
    assert (action.kind, action.reason) == ("none", "offer_failed")
    item = conn.execute(
        "SELECT reason FROM queue_items WHERE application_id = ? AND kind = 'escalation'",
        (app["id"],),
    ).fetchone()
    assert item["reason"] == "technical_error"


def test_no_action_when_not_approved_taken_over_or_opted_out(
    conn, client, channel: Channel, hermes: FakeHermes
) -> None:
    _, app = webchat_app(client, conn)
    assert channel.handle_event(app["id"], "stage_changed").reason == "no_action"

    conn.execute("UPDATE applications SET stage = 'qualified' WHERE id = ?", (app["id"],))
    assert channel.handle_event(app["id"], "approved").reason == "not_approved"
    approve(conn, app["id"])

    repo.mark_opted_out(conn, app["candidate_id"])
    assert channel.handle_event(app["id"], "approved").reason == "opted_out"
    repo.lift_opt_out(conn, app["candidate_id"])

    conn.execute(
        "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
        " VALUES ('u1', 'grupo_sazon', 'r@example.com', 'R', 'recruiter', 'x', '2026-10-09')"
    )
    conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'u1' WHERE id = ?",
        (app["id"],),
    )
    assert channel.handle_event(app["id"], "approved").reason == "human_control"
    assert APPROVED_TURN not in hermes.inputs


def test_failed_system_turn_escalates_without_a_fallback_message(
    conn, client, channel: Channel, hermes: FakeHermes
) -> None:
    conv, app = webchat_app(client, conn)
    before = len(client.get(f"/api/chat/conversations/{conv}/messages").json()["messages"])
    hermes.final_status = "failed"
    action = channel.handle_event(app["id"], "approved")
    assert action.kind == "run" and action.message is None
    after = client.get(f"/api/chat/conversations/{conv}/messages").json()["messages"]
    assert len(after) == before
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM queue_items WHERE application_id = ? AND kind = 'escalation'",
            (app["id"],),
        ).fetchone()[0]
        == 1
    )
