"""Outbound sender: quiet hours on both channels, the 24-hour window on WhatsApp only.

The quiet-hours rule itself is locked in ``tests/scheduler/test_quiet.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from criba.channel import sender as sender_module
from criba.channel.hours import inside_whatsapp_window
from criba.channel.sender import WhatsAppStubAdapter
from criba.db import iso, repo
from criba.screening.clock import FakeClock

from .conftest import NOW

MADRID = ZoneInfo("Europe/Madrid")


class Sender(sender_module.Sender):
    """The real sender, sending on the Application's own channel unless a test says otherwise
    (what every proactive call site spells out, ADR 0070): these tests are about the guards."""

    def send(self, conn: Any, application: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("channel", application["channel"])
        return super().send(conn, application, **kwargs)


def make_app(conn, channel: str, country: str = "ES", external_id: str = "x") -> dict:
    candidate, _ = repo.get_or_create_candidate(
        conn, client_id="grupo_sazon", channel=channel, external_id=external_id, country=country
    )
    return repo.start_application(conn, candidate["id"], channel, now=NOW)


def test_proactive_message_is_not_sent_in_quiet_hours_on_web_chat(conn) -> None:
    app = make_app(conn, "webchat")
    night = datetime(2026, 10, 9, 23, 0, tzinfo=MADRID)
    sender = Sender(FakeClock(night))
    result = sender.send(conn, app, text="¿Seguimos?", proactive=True)
    # A plain "not now": no retry time; the scheduler decides when to try again.
    assert (result.status, result.reason) == ("not_now", "quiet_hours")
    assert not hasattr(result, "not_before")
    assert repo.list_messages(conn, app["id"]) == []
    # A reply to a Candidate who just wrote is never held.
    assert sender.send(conn, app, text="Hola").status == "sent"


def test_quiet_hours_use_the_candidates_time_zone(conn) -> None:
    app = make_app(conn, "webchat", country="MX")
    # 23:00 in Madrid is 15:00 in Mexico City: not quiet for this Candidate.
    sender = Sender(FakeClock(datetime(2026, 10, 9, 23, 0, tzinfo=MADRID)))
    assert sender.send(conn, app, text="x", proactive=True).status == "sent"

    zone = conn.execute(
        "SELECT id FROM zones WHERE timezone = 'America/Monterrey' LIMIT 1"
    ).fetchone()
    conn.execute("UPDATE applications SET zone_id = ? WHERE id = ?", (zone["id"], app["id"]))
    app = repo.require_application(conn, app["id"])
    night_mty = datetime(2026, 10, 9, 23, 30, tzinfo=ZoneInfo("America/Monterrey"))
    assert Sender(FakeClock(night_mty)).send(conn, app, text="x", proactive=True).status == (
        "not_now"
    )


def test_whatsapp_window_and_template_guard(conn) -> None:
    app = make_app(conn, "whatsapp")
    outbox = WhatsAppStubAdapter()
    clock = FakeClock(NOW)
    sender = Sender(clock, {"whatsapp": outbox})

    blocked = sender.send(conn, app, text="Hola")
    assert (blocked.status, blocked.reason) == ("blocked", "outside_window")

    sent = sender.send(conn, app, text="Plantilla", template_name="interview_offer")
    assert sent.status == "sent" and sent.message["content_type"] == "template"
    assert outbox.outbox == [sent.message]
    kinds = [e["kind"] for e in repo.list_events(conn, app["id"])]
    assert "template_sent" in kinds

    conn.execute(
        "UPDATE applications SET last_inbound_at = ? WHERE id = ?",
        (iso(NOW - timedelta(hours=2)), app["id"]),
    )
    app = repo.require_application(conn, app["id"])
    assert inside_whatsapp_window(app, NOW)
    assert sender.send(conn, app, text="Hola").status == "sent"
    clock.advance(hours=23)
    assert sender.send(conn, app, text="Hola").status == "blocked"


def test_web_chat_has_no_window(conn) -> None:
    app = make_app(conn, "webchat")
    later = FakeClock(NOW + timedelta(days=5))
    assert Sender(later).send(conn, app, text="Hola").status == "sent"


def test_nothing_proactive_to_an_opted_out_candidate(conn) -> None:
    app = make_app(conn, "webchat")
    repo.mark_opted_out(conn, app["candidate_id"])
    sender = Sender(FakeClock(NOW))
    result = sender.send(conn, app, text="¿Seguimos?", proactive=True)
    assert (result.status, result.reason) == ("blocked", "opted_out")


def test_message_log_records_sender_and_interactive(conn) -> None:
    app = make_app(conn, "webchat")
    sender = Sender(FakeClock(NOW))
    interactive = {"style": "buttons", "options": [{"id": "yes", "label": "Sí"}]}
    agent = sender.send(conn, app, text="¿Carnet?", interactive=interactive, run_id="run_9")
    assert agent.message["content_type"] == "interactive"
    assert agent.message["interactive"] == interactive and agent.message["run_id"] == "run_9"
    conn.execute(
        "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
        " VALUES ('u1', 'grupo_sazon', 'r@example.com', 'R', 'recruiter', 'x', '2026-10-09')"
    )
    human = sender.send(conn, app, text="Hola, soy del equipo", sender="human", sender_user_id="u1")
    assert human.message["sender"] == "human" and human.message["sender_user_id"] == "u1"
    assert repo.require_application(conn, app["id"])["last_outbound_at"] == iso(NOW)


def test_send_defaults_to_typed(conn) -> None:
    app = make_app(conn, "webchat")
    sender = Sender(FakeClock(NOW))
    sent = sender.send(conn, app, text="Hola")
    assert sent.message["modality"] == "typed"
    candidate = repo.get_candidate(conn, app["candidate_id"])
    alone = sender.reply_without_application(conn, candidate, channel="webchat", text="Hecho")
    assert alone.message["modality"] == "typed"


def test_send_and_reply_without_application_mark_spoken(conn) -> None:
    app = make_app(conn, "webchat")
    sender = Sender(FakeClock(NOW))
    sent = sender.send(conn, app, text="Te escucho", modality="spoken")
    assert sent.message["modality"] == "spoken"
    candidate = repo.get_candidate(conn, app["candidate_id"])
    alone = sender.reply_without_application(
        conn, candidate, channel="webchat", text="Hecho", modality="spoken"
    )
    assert alone.message["modality"] == "spoken"
    stored = {
        row["id"]: row["modality"]
        for row in conn.execute("SELECT id, modality FROM messages").fetchall()
    }
    assert stored == {sent.message["id"]: "spoken", alone.message["id"]: "spoken"}


def test_send_stores_the_message_on_the_channel_it_is_given(conn) -> None:
    """A reply goes on the channel of the message it answers, not the Application's (ADR 0070)."""
    app = make_app(conn, "voice")
    real = sender_module.Sender(FakeClock(NOW))
    sent = real.send(conn, app, text="Hola", channel="webchat")
    assert sent.message["channel"] == "webchat"
    assert repo.require_application(conn, app["id"])["channel"] == "voice"


def test_send_needs_a_channel(conn) -> None:
    app = make_app(conn, "webchat")
    with pytest.raises(TypeError, match="channel"):
        unchecked: Any = sender_module.Sender(FakeClock(NOW))
        unchecked.send(conn, app, text="Hola")
