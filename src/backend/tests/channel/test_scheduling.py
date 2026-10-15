"""The scheduler and the channel together (wave 2 seam 2): the scheduler holds, the
channel sends. Fakes only at the edges: Hermes (``FakeHermes``) and WhatsApp
(``WhatsAppStubAdapter``); W2-A's pending choice is the conftest fake."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from criba.channel import copy
from criba.channel.router import APPROVED_TURN
from criba.channel.scheduling import NotNow, build_scheduler, dispatcher
from criba.channel.service import Channel
from criba.db import iso, repo
from criba.scheduler import ACTION_APPROVED, STALLED_TEMPLATE

from .conftest import NOW, FakeHermes, Fakes

MADRID = ZoneInfo("Europe/Madrid")
BUTTONS = {
    "style": "buttons",
    "options": [{"id": "yes", "label": "Sí"}, {"id": "no", "label": "No"}],
}


def candidate_app(conn, channel: str, *, stage: str, external_id: str = "34600") -> dict:
    """An ES Candidate who wrote at NOW (12:00 in Madrid), at ``stage``."""
    candidate, _ = repo.get_or_create_candidate(
        conn, client_id="grupo_sazon", channel=channel, external_id=external_id, country="ES"
    )
    app = repo.start_application(conn, candidate["id"], channel, now=NOW)
    repo.add_message(
        conn,
        candidate_id=candidate["id"],
        application_id=app["id"],
        channel=channel,
        direction="inbound",
        sender="candidate",
        body="hola",
        now=NOW,
    )
    if stage == "approved":
        conn.execute(
            "UPDATE applications SET stage = 'approved', approved_at = ? WHERE id = ?",
            (iso(NOW), app["id"]),
        )
    else:
        repo.set_stage(conn, app["id"], stage, now=NOW)
    return repo.require_application(conn, app["id"])


def outbound(conn, application_id: str) -> list[dict]:
    return [m for m in repo.list_messages(conn, application_id) if m["direction"] == "outbound"]


def test_approved_in_quiet_hours_is_held_then_sent_once_at_nine_local(
    conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    app = candidate_app(conn, "webchat", stage="approved")
    scheduler = build_scheduler(channel)
    hermes.reply = lambda text: "¡Buenas noticias! ¿Cuándo te viene bien la entrevista?"

    clock.set(datetime(2026, 10, 9, 23, 30, tzinfo=MADRID))
    assert scheduler.submit_action(conn, app["id"], ACTION_APPROVED) == "deferred"
    assert APPROVED_TURN not in hermes.inputs and outbound(conn, app["id"]) == []

    clock.set(datetime(2026, 10, 10, 8, 59, tzinfo=MADRID))
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 0
    assert APPROVED_TURN not in hermes.inputs

    clock.set(datetime(2026, 10, 10, 9, 0, tzinfo=MADRID))
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 1
    assert hermes.inputs.count(APPROVED_TURN) == 1
    [sent] = outbound(conn, app["id"])
    assert sent["body"].startswith("¡Buenas noticias!") and sent["sender"] == "agent"

    clock.advance(hours=1)
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 0
    assert hermes.inputs.count(APPROVED_TURN) == 1
    assert len(outbound(conn, app["id"])) == 1


def test_a_not_now_answer_is_a_failed_attempt_for_the_scheduler(conn, channel, clock) -> None:
    app = candidate_app(conn, "webchat", stage="approved")
    clock.set(datetime(2026, 10, 9, 23, 30, tzinfo=MADRID))
    with pytest.raises(NotNow):
        dispatcher(channel)(conn, app["id"], ACTION_APPROVED)


def test_a_nudge_goes_out_through_the_channel_send(
    conn, channel: Channel, clock, hermes: FakeHermes, fakes: Fakes
) -> None:
    app = candidate_app(conn, "webchat", stage="driver_license")
    fakes.choice = BUTTONS
    scheduler = build_scheduler(channel)

    clock.advance(hours=1)
    assert scheduler.run_due_jobs(conn).stalled.nudges_sent == 1

    [nudge] = outbound(conn, app["id"])
    assert nudge["body"] == copy.NUDGE_COPY["nudge"]["es-ES"]
    assert nudge["sender"] == "agent" and nudge["content_type"] == "interactive"
    assert nudge["interactive"] == BUTTONS  # the pending question's buttons stay live
    assert hermes.posts == []  # code-sent copy, no model run
    events = [e for e in repo.list_events(conn, app["id"]) if e["kind"] == "nudge_sent"]
    assert [e["data"]["step"] for e in events] == [1]


def test_a_whatsapp_nudge_reaches_the_whatsapp_edge(conn, channel: Channel, clock) -> None:
    candidate_app(conn, "whatsapp", stage="availability")
    scheduler = build_scheduler(channel)
    outbox = channel.sender.adapters["whatsapp"].outbox  # type: ignore[attr-defined]

    clock.advance(hours=1)
    scheduler.run_due_jobs(conn)
    assert [m["body"] for m in outbox] == [copy.NUDGE_COPY["nudge"]["es-ES"]]


def test_the_48_hour_step_is_the_stalled_resume_template_outside_the_window(
    conn, channel: Channel, clock
) -> None:
    candidate_app(conn, "whatsapp", stage="availability")
    scheduler = build_scheduler(channel)
    outbox = channel.sender.adapters["whatsapp"].outbox  # type: ignore[attr-defined]

    clock.advance(hours=48)  # 12:00 in Madrid, long outside the 24-hour window
    assert scheduler.run_due_jobs(conn).stalled.nudges_sent == 1
    [message] = outbox
    assert message["content_type"] == "template"
    assert message["template_name"] == STALLED_TEMPLATE
    assert message["body"] == copy.NUDGE_COPY["template"]["es-ES"]


def test_a_free_form_nudge_outside_the_window_is_declined_and_retried(
    conn, channel: Channel, clock
) -> None:
    app = candidate_app(conn, "whatsapp", stage="availability")
    # Reopened at NOW (the silence starts then), but the last message is 25 hours old:
    # WhatsApp's window is shut, so a free-form nudge cannot go.
    conn.execute(
        "UPDATE applications SET last_inbound_at = ? WHERE id = ?",
        (iso(NOW - timedelta(hours=25)), app["id"]),
    )
    conn.execute("UPDATE applications SET last_reopened_at = ? WHERE id = ?", (iso(NOW), app["id"]))
    scheduler = build_scheduler(channel)
    clock.advance(hours=1)
    report = scheduler.run_due_jobs(conn).stalled
    assert (report.nudges_sent, report.nudges_declined) == (0, 1)
    assert repo.require_application(conn, app["id"])["nudges_sent"] == 0
    assert outbound(conn, app["id"]) == []
