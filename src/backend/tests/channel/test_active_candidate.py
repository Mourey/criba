"""ADR 0046 through the real channel: the Approval message reaches a Candidate who wrote in the
last 60 minutes even in quiet hours, and nothing else does. Fakes only at the edges (Hermes)."""

from __future__ import annotations

from datetime import datetime, timedelta

from criba.channel.router import APPROVED_TURN
from criba.channel.scheduling import NudgeSender, build_scheduler
from criba.channel.service import Channel
from criba.db import repo
from criba.scheduler import ACTION_APPROVED, NudgeRequest

from .conftest import FakeHermes
from .test_scheduling import MADRID, candidate_app, outbound

NIGHT = datetime(2026, 10, 9, 23, 30, tzinfo=MADRID)  # quiet hours in Madrid


def candidate_writes(conn, app: dict, at: datetime) -> None:
    repo.add_message(
        conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        channel=app["channel"],
        direction="inbound",
        sender="candidate",
        body="¿ya me han aprobado?",
        now=at,
    )


def held(conn) -> list[tuple[str, str, int]]:
    cursor = conn.execute("SELECT action, status, attempts FROM held_actions ORDER BY id")
    return [tuple(r) for r in cursor]


def test_approval_at_night_for_a_candidate_who_wrote_two_minutes_ago_runs_one_system_turn(
    conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    app = candidate_app(conn, "webchat", stage="approved")
    hermes.reply = lambda text: "¡Buenas noticias! ¿Cuándo te viene bien la entrevista?"
    candidate_writes(conn, app, NIGHT - timedelta(minutes=2))
    clock.set(NIGHT)
    scheduler = build_scheduler(channel)

    assert scheduler.submit_action(conn, app["id"], ACTION_APPROVED) == "dispatched"
    assert hermes.inputs.count(APPROVED_TURN) == 1
    [sent] = outbound(conn, app["id"])
    assert sent["body"].startswith("¡Buenas noticias!")
    assert held(conn) == []

    clock.set(datetime(2026, 10, 10, 9, 0, tzinfo=MADRID))
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 0
    assert hermes.inputs.count(APPROVED_TURN) == 1  # never a second time


def test_the_same_approval_61_minutes_after_the_last_message_is_held_until_nine(
    conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    app = candidate_app(conn, "webchat", stage="approved")
    candidate_writes(conn, app, NIGHT - timedelta(minutes=61))
    clock.set(NIGHT)
    scheduler = build_scheduler(channel)

    assert scheduler.submit_action(conn, app["id"], ACTION_APPROVED) == "deferred"
    assert APPROVED_TURN not in hermes.inputs and outbound(conn, app["id"]) == []

    clock.set(datetime(2026, 10, 10, 9, 0, tzinfo=MADRID))
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 1
    assert hermes.inputs.count(APPROVED_TURN) == 1
    assert held(conn) == [("approved", "dispatched", 1)]


def test_a_held_approval_is_sent_on_the_next_run_when_the_candidate_writes_at_night(
    conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    app = candidate_app(conn, "webchat", stage="approved")
    scheduler = build_scheduler(channel)
    clock.set(NIGHT)
    assert scheduler.submit_action(conn, app["id"], ACTION_APPROVED) == "deferred"

    clock.advance(minutes=20)
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 0
    candidate_writes(conn, app, clock.now())
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 1
    assert hermes.inputs.count(APPROVED_TURN) == 1
    assert len(outbound(conn, app["id"])) == 1

    assert scheduler.run_due_jobs(conn).deferred.dispatched == 0
    assert hermes.inputs.count(APPROVED_TURN) == 1


def test_a_nudge_in_quiet_hours_is_still_refused_for_a_candidate_who_just_wrote(
    conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    app = candidate_app(conn, "webchat", stage="driver_license")
    candidate_writes(conn, app, NIGHT - timedelta(minutes=2))
    clock.set(NIGHT)
    request = NudgeRequest(
        application_id=app["id"],
        candidate_id=app["candidate_id"],
        channel="webchat",
        language="es-ES",
        stage=app["stage"],
        step=1,
        kind="nudge",
        template=None,
    )

    assert NudgeSender(channel)(conn, request) is False
    assert outbound(conn, app["id"]) == []
    sent = channel.sender.send(conn, app, channel=app["channel"], text="hola", proactive=True)
    assert (sent.status, sent.reason) == ("not_now", "quiet_hours")


def test_an_opted_out_candidate_who_just_wrote_gets_nothing(
    conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    app = candidate_app(conn, "webchat", stage="approved")
    candidate_writes(conn, app, NIGHT - timedelta(minutes=2))
    repo.mark_opted_out(conn, app["candidate_id"], now=NIGHT - timedelta(minutes=1))
    clock.set(NIGHT)

    build_scheduler(channel).submit_action(conn, app["id"], ACTION_APPROVED)
    assert APPROVED_TURN not in hermes.inputs
    assert [m for m in outbound(conn, app["id"]) if m["sender"] == "agent"] == []
