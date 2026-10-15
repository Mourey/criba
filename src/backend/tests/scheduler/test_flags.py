"""Qualified, waiting (14 days unclaimed) and Claimed, idle (7 days)."""

from datetime import UTC, datetime, timedelta

from criba.db import repo

T0 = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
RECRUITER = "usr_rec_puebla"


def test_an_unclaimed_qualified_application_is_flagged_after_14_days(
    conn, scheduler, clock, make_qualified, kinds
) -> None:
    app = make_qualified(conn)
    clock.set(T0 + timedelta(days=14) - timedelta(seconds=1))
    assert scheduler.run_due_jobs(conn).flags.qualified_waiting_set == 0
    assert repo.require_application(conn, app["id"])["qualified_waiting"] is False

    clock.set(T0 + timedelta(days=14))
    assert scheduler.run_due_jobs(conn).flags.qualified_waiting_set == 1
    assert repo.require_application(conn, app["id"])["qualified_waiting"] is True
    assert kinds(conn, app["id"])[-1] == "flag_qualified_waiting"


def test_the_flag_is_set_once(conn, scheduler, clock, make_qualified, kinds) -> None:
    app = make_qualified(conn)
    clock.advance(days=15)
    scheduler.run_due_jobs(conn)
    clock.advance(days=1)
    assert scheduler.run_due_jobs(conn).flags.qualified_waiting_set == 0
    assert kinds(conn, app["id"]).count("flag_qualified_waiting") == 1


def test_a_claim_clears_qualified_waiting(conn, scheduler, clock, make_qualified) -> None:
    app = make_qualified(conn)
    clock.advance(days=15)
    scheduler.run_due_jobs(conn)
    repo.claim_application(conn, app["id"], RECRUITER, now=clock.now())
    assert repo.require_application(conn, app["id"])["qualified_waiting"] is False


def test_a_claimed_application_is_never_flagged_waiting(
    conn, scheduler, clock, make_qualified
) -> None:
    app = make_qualified(conn)
    repo.claim_application(conn, app["id"], RECRUITER, now=T0)
    repo.touch_claim(conn, app["id"], RECRUITER, now=T0 + timedelta(days=13))
    clock.set(T0 + timedelta(days=15))
    scheduler.run_due_jobs(conn)
    assert repo.require_application(conn, app["id"])["qualified_waiting"] is False


def test_approval_clears_a_stale_waiting_flag(conn, scheduler, clock, make_qualified) -> None:
    app = make_qualified(conn)
    clock.advance(days=15)
    scheduler.run_due_jobs(conn)
    repo.mark_approved(conn, app["id"], RECRUITER, now=clock.now())
    report = scheduler.run_due_jobs(conn)
    assert report.flags.cleared == 1
    assert repo.require_application(conn, app["id"])["qualified_waiting"] is False


def test_a_claim_with_no_action_is_flagged_idle_after_7_days(
    conn, scheduler, clock, make_qualified, kinds
) -> None:
    app = make_qualified(conn)
    repo.claim_application(conn, app["id"], RECRUITER, now=T0)
    clock.set(T0 + timedelta(days=7) - timedelta(seconds=1))
    assert scheduler.run_due_jobs(conn).flags.claimed_idle_set == 0

    clock.set(T0 + timedelta(days=7))
    assert scheduler.run_due_jobs(conn).flags.claimed_idle_set == 1
    after = repo.require_application(conn, app["id"])
    assert after["claimed_idle"] is True
    event = repo.list_events(conn, app["id"])[-1]
    assert event["kind"] == "flag_claimed_idle"
    assert event["data"]["user_id"] == RECRUITER


def test_a_recruiter_action_resets_the_idle_clock(conn, scheduler, clock, make_qualified) -> None:
    app = make_qualified(conn)
    repo.claim_application(conn, app["id"], RECRUITER, now=T0)
    repo.touch_claim(conn, app["id"], RECRUITER, now=T0 + timedelta(days=5))
    clock.set(T0 + timedelta(days=9))
    scheduler.run_due_jobs(conn)
    assert repo.require_application(conn, app["id"])["claimed_idle"] is False

    clock.set(T0 + timedelta(days=12))
    scheduler.run_due_jobs(conn)
    assert repo.require_application(conn, app["id"])["claimed_idle"] is True

    repo.touch_claim(conn, app["id"], RECRUITER, now=clock.now())
    assert repo.require_application(conn, app["id"])["claimed_idle"] is False


def test_releasing_the_claim_and_closing_clear_the_flags(
    conn, scheduler, clock, make_qualified
) -> None:
    app = make_qualified(conn)
    repo.claim_application(conn, app["id"], RECRUITER, now=T0)
    clock.advance(days=8)
    scheduler.run_due_jobs(conn)
    repo.release_claim(conn, app["id"], RECRUITER, now=clock.now())
    assert repo.require_application(conn, app["id"])["claimed_idle"] is False


def test_running_the_flag_job_twice_changes_nothing(conn, scheduler, clock, make_qualified) -> None:
    make_qualified(conn)
    clock.advance(days=20)
    first = scheduler.run_due_jobs(conn)
    second = scheduler.run_due_jobs(conn)
    assert first.flags.qualified_waiting_set == 1
    assert (second.flags.qualified_waiting_set, second.flags.claimed_idle_set) == (0, 0)
