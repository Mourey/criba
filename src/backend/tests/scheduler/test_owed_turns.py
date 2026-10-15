"""The scheduler and owed turns (ADR 0073): no Stalled clock while we owe the Candidate a reply,
and the turn recovery called every tick and, first, at startup."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from criba.db import connect, iso, repo
from criba.scheduler import Scheduler
from criba.scheduler.service import running

T0 = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)  # 14:00 in Mexico City


def owe(conn: Any, app: dict[str, Any], *, status: str = "owed") -> None:
    """Record the Candidate's last message as owed, as the router does when it accepts it."""
    message_id = repo.max_message_id(conn, app["id"])
    repo.add_owed_turn(
        conn,
        message_pk=message_id,
        application_id=app["id"],
        channel="webchat",
        turn_input="hola",
        idempotency_key="m1",
        after_event_id=None,
        booking=None,
        typed=True,
        next_attempt_at=iso(T0),
        now=iso(T0),
    )
    if status == "exhausted":
        repo.exhaust_owed_turns(conn, app["id"], iso(T0))


def test_no_nudge_and_no_abandoned_while_a_reply_is_owed(
    conn, scheduler, clock, sender, start_app
) -> None:
    app = start_app(conn, stage="zone")
    owe(conn, app)
    clock.advance(days=8)
    report = scheduler.run_due_jobs(conn)
    assert sender.requests == []
    assert report.stalled.stalled_marked == 0 and report.stalled.abandoned == 0
    after = repo.require_application(conn, app["id"])
    assert after["status"] == "open" and after["stalled_since"] is None


def test_an_exhausted_turn_holds_the_clock_while_its_escalation_is_open(
    conn, scheduler, clock, sender, start_app
) -> None:
    app = start_app(conn, stage="zone")
    owe(conn, app, status="exhausted")
    item = repo.open_queue_item(conn, app["id"], "escalation", reason="technical_error", now=T0)
    clock.advance(hours=2)
    scheduler.run_due_jobs(conn)
    assert sender.requests == []

    # Once a person dismissed it, the follow-up is the ladder's again.
    conn.execute(
        "UPDATE queue_items SET status = 'resolved', resolution = 'dismissed', resolved_at = ?"
        " WHERE id = ?",
        (iso(T0), item["id"]),
    )
    scheduler.run_due_jobs(conn)
    assert [r.step for r in sender.requests] == [1]


def test_the_stalled_clock_runs_again_once_the_reply_went_out(
    conn, scheduler, clock, sender, start_app
) -> None:
    app = start_app(conn, stage="zone")
    owe(conn, app)
    clock.advance(hours=2)
    scheduler.run_due_jobs(conn)
    assert sender.requests == []
    repo.resolve_owed_turn(conn, repo.max_message_id(conn, app["id"]))
    scheduler.run_due_jobs(conn)
    assert [r.step for r in sender.requests] == [1]


def test_each_tick_resumes_the_owed_turns_that_are_due(
    db_path: Path, conn, clock, sender, dispatch
) -> None:
    calls: list[bool] = []
    scheduler = Scheduler(
        connect=lambda: connect(db_path),
        clock=clock,
        send_nudge=sender,
        dispatch=dispatch,
        resume_turns=calls.append,
    )
    scheduler.run_due_jobs(conn)
    assert calls == [False]


def test_a_failing_recovery_leaves_the_tick_alone(db_path: Path, conn, clock, sender, dispatch):
    def broken(startup: bool) -> None:
        raise RuntimeError("boom")

    scheduler = Scheduler(
        connect=lambda: connect(db_path),
        clock=clock,
        send_nudge=sender,
        dispatch=dispatch,
        resume_turns=broken,
    )
    report = scheduler.run_due_jobs(conn)
    assert report.stalled.errors == 0


def test_the_recovery_loop_resumes_everything_first_then_what_is_due(
    db_path: Path, conn, clock, sender, dispatch
) -> None:
    calls: list[bool] = []
    scheduler = Scheduler(
        connect=lambda: connect(db_path),
        clock=clock,
        send_nudge=sender,
        dispatch=dispatch,
        resume_turns=calls.append,
    )

    async def two_ticks() -> None:
        task = asyncio.create_task(scheduler.turn_loop(interval_seconds=0.01))
        while len(calls) < 2:
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(two_ticks())
    assert calls[:2] == [True, False]


def test_running_starts_the_recovery_loop_at_startup(
    db_path: Path, conn, clock, sender, dispatch
) -> None:
    calls: list[bool] = []
    scheduler = Scheduler(
        connect=lambda: connect(db_path),
        clock=clock,
        send_nudge=sender,
        dispatch=dispatch,
        resume_turns=calls.append,
    )

    async def lifespan() -> None:
        async with running(scheduler, 3600.0):
            while not calls:
                await asyncio.sleep(0.01)

    asyncio.run(lifespan())
    assert calls[0] is True
