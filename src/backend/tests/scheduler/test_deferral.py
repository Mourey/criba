"""Quiet-hours deferral of router actions (Q41); the first use is the action after Approval."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from criba.db import connect, repo
from criba.scheduler import ACTION_APPROVED, Scheduler, running
from tests.scheduler.conftest import FakeDispatch

# 09:00 in Mexico City, when the night's quiet hours end.
MORNING = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)


def silent_since(clock, minutes: int = 120) -> datetime:
    """When the Candidate last wrote: by default two hours ago, outside the 60-minute window
    that exempts the Approval message from quiet hours (ADR 0046)."""
    return clock.now() - timedelta(minutes=minutes)


def approved_application(conn, make_qualified, clock):
    app = make_qualified(conn, at=silent_since(clock))
    return repo.mark_approved(conn, app["id"], "usr_rec_puebla", now=clock.now())


def test_an_action_after_approval_during_quiet_hours_is_held_then_run(
    conn, scheduler, clock, dispatch, make_qualified
) -> None:
    clock.set(datetime(2026, 10, 13, 4, 30, tzinfo=UTC))  # 22:30 local
    app = approved_application(conn, make_qualified, clock)

    assert scheduler.submit_action(conn, app["id"], ACTION_APPROVED) == "deferred"
    assert dispatch.calls == []
    [held] = scheduler.deferred.pending(conn)
    assert (held.application_id, held.action, held.run_at) == (app["id"], "approved", MORNING)

    clock.set(datetime(2026, 10, 13, 14, 59, tzinfo=UTC))  # 08:59 local
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 0
    assert dispatch.calls == []

    clock.set(MORNING)  # 09:00 local
    report = scheduler.run_due_jobs(conn)
    assert report.deferred.dispatched == 1
    assert dispatch.calls == [(app["id"], "approved")]
    assert scheduler.deferred.pending(conn) == []

    scheduler.run_due_jobs(conn)
    assert len(dispatch.calls) == 1  # run once


def test_an_action_outside_quiet_hours_runs_at_once(
    conn, scheduler, clock, dispatch, make_qualified
) -> None:
    app = approved_application(conn, make_qualified, clock)  # 14:00 local
    assert scheduler.submit_action(conn, app["id"], ACTION_APPROVED) == "dispatched"
    assert dispatch.calls == [(app["id"], "approved")]
    assert scheduler.deferred.pending(conn) == []


def test_an_action_that_sends_no_message_is_never_held(
    conn, scheduler, clock, dispatch, make_qualified
) -> None:
    clock.set(datetime(2026, 10, 13, 8, 0, tzinfo=UTC))  # 02:00 local
    app = approved_application(conn, make_qualified, clock)
    result = scheduler.submit_action(conn, app["id"], "log_only", messages_candidate=False)
    assert result == "dispatched" and dispatch.calls == [(app["id"], "log_only")]


def test_the_same_action_is_held_once(conn, scheduler, clock, dispatch, make_qualified) -> None:
    clock.set(datetime(2026, 10, 13, 4, 30, tzinfo=UTC))
    app = approved_application(conn, make_qualified, clock)
    scheduler.submit_action(conn, app["id"], ACTION_APPROVED)
    clock.advance(hours=1)
    scheduler.submit_action(conn, app["id"], ACTION_APPROVED)
    assert len(scheduler.deferred.pending(conn)) == 1
    clock.set(MORNING)
    scheduler.run_due_jobs(conn)
    assert dispatch.calls == [(app["id"], "approved")]


def test_a_held_action_is_dropped_if_the_application_closed_meanwhile(
    conn, scheduler, clock, dispatch, make_qualified
) -> None:
    clock.set(datetime(2026, 10, 13, 4, 30, tzinfo=UTC))
    app = approved_application(conn, make_qualified, clock)
    scheduler.submit_action(conn, app["id"], ACTION_APPROVED)
    repo.close_application(
        conn,
        app["id"],
        "rejection",
        rejection_reason="role_filled",
        actor="user",
        user_id="usr_rec_puebla",
        now=clock.now(),
    )
    clock.set(MORNING)
    report = scheduler.run_due_jobs(conn)
    assert report.deferred.dropped == 1 and dispatch.calls == []
    assert scheduler.deferred.pending(conn) == []


def test_a_held_action_is_dropped_if_the_candidate_opted_out(
    conn, scheduler, clock, dispatch, make_qualified
) -> None:
    clock.set(datetime(2026, 10, 13, 4, 30, tzinfo=UTC))
    app = approved_application(conn, make_qualified, clock)
    scheduler.submit_action(conn, app["id"], ACTION_APPROVED)
    repo.mark_opted_out(conn, app["candidate_id"], now=clock.now())
    clock.set(MORNING)
    assert scheduler.run_due_jobs(conn).deferred.dropped == 1
    assert dispatch.calls == []


def test_a_failing_dispatch_is_retried_then_given_up(
    conn, scheduler, clock, dispatch, make_qualified
) -> None:
    clock.set(datetime(2026, 10, 13, 4, 30, tzinfo=UTC))
    app = approved_application(conn, make_qualified, clock)
    scheduler.submit_action(conn, app["id"], ACTION_APPROVED)
    clock.set(MORNING)
    dispatch.failures_left = 2
    assert scheduler.run_due_jobs(conn).deferred.failed == 1
    assert len(scheduler.deferred.pending(conn)) == 1
    assert scheduler.run_due_jobs(conn).deferred.failed == 1
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 1
    assert dispatch.calls == [(app["id"], "approved")]

    # Three failures in a row and it is dropped.
    again = approved_application_two(conn, make_qualified, clock)
    clock.set(datetime(2026, 10, 14, 4, 30, tzinfo=UTC))
    scheduler.submit_action(conn, again["id"], ACTION_APPROVED)
    clock.set(datetime(2026, 10, 14, 15, 0, tzinfo=UTC))
    dispatch.failures_left = 3
    for _ in range(3):
        scheduler.run_due_jobs(conn)
    assert scheduler.deferred.pending(conn) == []
    assert len(dispatch.calls) == 1


def approved_application_two(conn, make_qualified, clock):
    app = make_qualified(conn, at=silent_since(clock), external_id="conv-2")
    return repo.mark_approved(conn, app["id"], "usr_rec_puebla", now=clock.now())


def test_madrid_and_mexico_hold_for_their_own_mornings(
    conn, scheduler, clock, dispatch, start_app, make_qualified
) -> None:
    clock.set(datetime(2026, 10, 13, 5, 0, tzinfo=UTC))  # 07:00 in Madrid, 23:00 in Mexico City
    es = start_app(
        conn, at=silent_since(clock), external_id="es", phone="+34600111222", stage="confirm"
    )
    es = repo.mark_qualified(conn, es["id"], now=clock.now())  # no Zone yet: country decides
    mx = make_qualified(conn, at=silent_since(clock), external_id="mx")
    assert scheduler.submit_action(conn, es["id"], ACTION_APPROVED) == "deferred"
    assert scheduler.submit_action(conn, mx["id"], ACTION_APPROVED) == "deferred"
    run_at = {h.application_id: h.run_at for h in scheduler.deferred.pending(conn)}
    assert run_at == {
        es["id"]: datetime(2026, 10, 13, 7, 0, tzinfo=UTC),
        mx["id"]: MORNING,
    }

    clock.set(datetime(2026, 10, 13, 7, 0, tzinfo=UTC))
    scheduler.run_due_jobs(conn)
    assert dispatch.calls == [(es["id"], "approved")]


def test_the_loop_runs_due_jobs_until_cancelled(scheduler, conn, clock, sender, start_app) -> None:
    start_app(conn, stage="zone")
    clock.advance(hours=1)

    async def scenario() -> None:
        async with running(scheduler, 0.01):
            for _ in range(200):
                if sender.requests:
                    break
                await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert len(sender.requests) == 1


def restarted(db_path, clock, dispatch) -> Scheduler:
    """A new Scheduler on the same database file: what a process restart leaves."""
    return Scheduler(
        connect=lambda: connect(db_path),
        clock=clock,
        send_nudge=lambda conn, request: True,
        dispatch=dispatch,
    )


def held_rows(conn) -> list[tuple[str, str, int]]:
    rows = conn.execute("SELECT action, status, attempts FROM held_actions ORDER BY id")
    return [tuple(r) for r in rows]


def test_a_held_action_survives_a_restart_and_runs_at_nine_local(
    conn, db_path, scheduler, clock, dispatch, make_qualified
) -> None:
    clock.set(datetime(2026, 10, 13, 4, 30, tzinfo=UTC))  # 22:30 local
    app = approved_application(conn, make_qualified, clock)
    assert scheduler.submit_action(conn, app["id"], ACTION_APPROVED) == "deferred"
    assert held_rows(conn) == [("approved", "held", 0)]
    del scheduler  # the old process is gone; only the database remains

    after_dispatch = FakeDispatch()
    fresh = restarted(db_path, clock, after_dispatch)
    other = connect(db_path)
    try:
        [held] = fresh.deferred.pending(other)
        assert (held.application_id, held.action, held.run_at) == (app["id"], "approved", MORNING)

        clock.set(datetime(2026, 10, 13, 14, 59, tzinfo=UTC))  # 08:59 local
        assert fresh.run_due_jobs(other).deferred.dispatched == 0
        assert after_dispatch.calls == []

        clock.set(MORNING)  # 09:00 local
        assert fresh.run_due_jobs(other).deferred.dispatched == 1
        assert after_dispatch.calls == [(app["id"], "approved")]
        assert held_rows(other) == [("approved", "dispatched", 1)]
    finally:
        other.close()
    assert dispatch.calls == []  # the first process never dispatched it


def test_a_dispatched_action_is_never_dispatched_again(
    conn, db_path, scheduler, clock, dispatch, make_qualified
) -> None:
    clock.set(datetime(2026, 10, 13, 4, 30, tzinfo=UTC))
    app = approved_application(conn, make_qualified, clock)
    scheduler.submit_action(conn, app["id"], ACTION_APPROVED)
    clock.set(MORNING)
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 1

    # Another run, and a restarted Scheduler, see a dispatched row and do nothing.
    clock.advance(hours=2)
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 0
    second = FakeDispatch()
    assert restarted(db_path, clock, second).run_due_jobs(conn).deferred.dispatched == 0
    assert dispatch.calls == [(app["id"], "approved")] and second.calls == []
    assert held_rows(conn) == [("approved", "dispatched", 1)]


def test_resubmitting_after_a_dispatch_holds_a_new_row(
    conn, scheduler, clock, dispatch, make_qualified
) -> None:
    clock.set(datetime(2026, 10, 13, 4, 30, tzinfo=UTC))
    app = approved_application(conn, make_qualified, clock)
    scheduler.submit_action(conn, app["id"], ACTION_APPROVED)
    scheduler.submit_action(conn, app["id"], ACTION_APPROVED)
    assert held_rows(conn) == [("approved", "held", 0)]  # one held row at most
    clock.set(MORNING)
    scheduler.run_due_jobs(conn)

    clock.set(datetime(2026, 10, 14, 4, 30, tzinfo=UTC))
    scheduler.submit_action(conn, app["id"], ACTION_APPROVED)
    assert held_rows(conn) == [("approved", "dispatched", 1), ("approved", "held", 0)]


def test_dropped_and_failed_are_recorded_on_the_row(
    conn, scheduler, clock, dispatch, make_qualified
) -> None:
    clock.set(datetime(2026, 10, 13, 4, 30, tzinfo=UTC))
    closed = approved_application(conn, make_qualified, clock)
    failing = approved_application_two(conn, make_qualified, clock)
    scheduler.submit_action(conn, closed["id"], ACTION_APPROVED)
    scheduler.submit_action(conn, failing["id"], ACTION_APPROVED)
    repo.mark_opted_out(conn, closed["candidate_id"], now=clock.now())

    clock.set(MORNING)
    dispatch.failures_left = 3
    for _ in range(4):
        scheduler.run_due_jobs(conn)

    [dropped] = scheduler.deferred.history(conn, closed["id"])
    assert (dropped.status, dropped.attempts) == ("dropped", 0)
    [failed] = scheduler.deferred.history(conn, failing["id"])
    assert (failed.status, failed.attempts) == ("failed", 3)
    assert failed.last_error == "RuntimeError: router failed"
    assert dispatch.calls == [] and scheduler.deferred.pending(conn) == []


def test_the_table_enforces_one_held_row_and_the_closed_sets(conn, start_app) -> None:
    app = start_app(conn)
    insert = (
        "INSERT INTO held_actions (application_id, action, status, not_before, created_at,"
        " updated_at) VALUES (?, ?, ?, 'x', 'x', 'x')"
    )
    conn.execute(insert, (app["id"], "approved", "held"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, (app["id"], "approved", "held"))
    conn.execute(insert, (app["id"], "approved", "dispatched"))  # history rows are not limited
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, (app["id"], "approved", "paused"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, (app["id"], "reject", "held"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("no_such_app", "approved", "dispatched"))
