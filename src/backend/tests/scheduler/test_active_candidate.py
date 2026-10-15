"""ADR 0046: the Approval message skips quiet hours for a Candidate active in the last 60 minutes.

The exemption is for the `approved` action only. Each test builds the Candidate's last inbound
message explicitly (`minutes_ago`), because a Candidate who has just written is the case
under test.
"""

from datetime import UTC, datetime, timedelta

from criba.db import iso, repo
from criba.scheduler import ACTION_APPROVED, ACTIVE_CANDIDATE_WINDOW, candidate_recently_active
from criba.scheduler.deferral import skips_quiet_hours

# 07:38 in Mexico City, the real demo: quiet hours until 09:00 local.
QUIET_MORNING = datetime(2026, 10, 13, 13, 38, tzinfo=UTC)
NINE_LOCAL = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
APPROVER = "usr_rec_puebla"


def approve(conn, make_qualified, clock, *, minutes_ago: float, **kwargs) -> dict:
    """Approve at the clock's now; the Candidate last wrote `minutes_ago` earlier."""
    wrote = clock.now() - timedelta(minutes=minutes_ago)
    app = make_qualified(conn, at=wrote, **kwargs)
    return repo.mark_approved(conn, app["id"], APPROVER, now=clock.now())


def rows(conn) -> list[tuple[str, str, int]]:
    cursor = conn.execute("SELECT action, status, attempts FROM held_actions ORDER BY id")
    return [tuple(r) for r in cursor]


def test_a_candidate_who_wrote_two_minutes_ago_gets_the_offer_at_once(
    conn, scheduler, clock, dispatch, make_qualified
) -> None:
    clock.set(QUIET_MORNING)
    app = approve(conn, make_qualified, clock, minutes_ago=2)

    assert scheduler.submit_action(conn, app["id"], ACTION_APPROVED) == "dispatched"
    assert dispatch.calls == [(app["id"], "approved")]
    assert rows(conn) == []  # no held row at all

    clock.set(NINE_LOCAL)
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 0
    assert len(dispatch.calls) == 1


def test_a_candidate_silent_for_61_minutes_is_held_until_nine_local(
    conn, scheduler, clock, dispatch, make_qualified
) -> None:
    clock.set(QUIET_MORNING)
    app = approve(conn, make_qualified, clock, minutes_ago=61)

    assert scheduler.submit_action(conn, app["id"], ACTION_APPROVED) == "deferred"
    assert dispatch.calls == [] and rows(conn) == [("approved", "held", 0)]
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 0

    clock.set(NINE_LOCAL)
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 1
    scheduler.run_due_jobs(conn)
    assert dispatch.calls == [(app["id"], "approved")]
    assert rows(conn) == [("approved", "dispatched", 1)]


def test_the_boundary_is_inclusive_exactly_60_minutes_counts_60_and_one_second_does_not(
    conn, scheduler, clock, dispatch, make_qualified
) -> None:
    clock.set(QUIET_MORNING)
    at_edge = approve(conn, make_qualified, clock, minutes_ago=60)
    over = approve(conn, make_qualified, clock, minutes_ago=60 + 1 / 60, external_id="over")

    assert scheduler.submit_action(conn, at_edge["id"], ACTION_APPROVED) == "dispatched"
    assert scheduler.submit_action(conn, over["id"], ACTION_APPROVED) == "deferred"
    assert dispatch.calls == [(at_edge["id"], "approved")]


def test_a_held_approval_goes_out_on_the_next_run_after_the_candidate_writes(
    conn, scheduler, clock, dispatch, make_qualified, say
) -> None:
    clock.set(QUIET_MORNING)
    app = approve(conn, make_qualified, clock, minutes_ago=120)
    assert scheduler.submit_action(conn, app["id"], ACTION_APPROVED) == "deferred"
    clock.advance(minutes=10)
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 0  # still silent

    clock.advance(minutes=5)
    say(conn, app, at=clock.now())  # writes during quiet hours
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 1
    assert dispatch.calls == [(app["id"], "approved")]
    assert rows(conn) == [("approved", "dispatched", 1)]

    clock.set(NINE_LOCAL)
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 0
    assert len(dispatch.calls) == 1  # once, not again at 09:00


def test_a_held_approval_stays_held_if_the_candidates_last_message_is_over_an_hour_old(
    conn, scheduler, clock, dispatch, make_qualified, say
) -> None:
    clock.set(QUIET_MORNING)
    app = approve(conn, make_qualified, clock, minutes_ago=120)
    scheduler.submit_action(conn, app["id"], ACTION_APPROVED)
    say(conn, app, at=clock.now() - timedelta(minutes=90))
    assert scheduler.run_due_jobs(conn).deferred.dispatched == 0
    assert dispatch.calls == [] and rows(conn) == [("approved", "held", 0)]


def test_an_active_candidate_who_opted_out_or_whose_application_closed_is_dropped(
    conn, scheduler, clock, dispatch, make_qualified, say
) -> None:
    clock.set(QUIET_MORNING)
    opted = approve(conn, make_qualified, clock, minutes_ago=120)
    closed = approve(conn, make_qualified, clock, minutes_ago=120, external_id="closed")
    scheduler.submit_action(conn, opted["id"], ACTION_APPROVED)
    scheduler.submit_action(conn, closed["id"], ACTION_APPROVED)
    assert len(rows(conn)) == 2

    for app in (opted, closed):
        say(conn, app, at=clock.now())  # both are awake and writing
    repo.mark_opted_out(conn, opted["candidate_id"], now=clock.now())
    repo.close_application(
        conn,
        closed["id"],
        "rejection",
        rejection_reason="role_filled",
        actor="user",
        user_id=APPROVER,
        now=clock.now(),
    )

    assert scheduler.run_due_jobs(conn).deferred.dropped == 2
    assert dispatch.calls == []
    assert rows(conn) == [("approved", "dropped", 0), ("approved", "dropped", 0)]


def test_the_retry_limit_still_applies_to_an_active_candidate(
    conn, scheduler, clock, dispatch, make_qualified, say
) -> None:
    clock.set(QUIET_MORNING)
    app = approve(conn, make_qualified, clock, minutes_ago=120)
    scheduler.submit_action(conn, app["id"], ACTION_APPROVED)
    say(conn, app, at=clock.now())
    dispatch.failures_left = 3
    for _ in range(4):
        scheduler.run_due_jobs(conn)
    assert rows(conn) == [("approved", "failed", 3)]
    assert dispatch.calls == []


def test_only_the_approved_action_is_exempt() -> None:
    app = {"last_inbound_at": iso(QUIET_MORNING - timedelta(minutes=1))}
    assert skips_quiet_hours(ACTION_APPROVED, app, QUIET_MORNING) is True
    assert skips_quiet_hours("stalled_nudge", app, QUIET_MORNING) is False
    assert skips_quiet_hours(ACTION_APPROVED, {"last_inbound_at": None}, QUIET_MORNING) is False


def test_the_window_is_one_named_constant_and_a_future_message_counts_as_active() -> None:
    assert timedelta(minutes=60) == ACTIVE_CANDIDATE_WINDOW
    ahead = {"last_inbound_at": iso(QUIET_MORNING + timedelta(minutes=1))}  # clock moved back
    assert candidate_recently_active(ahead, QUIET_MORNING) is True


def test_mexico_city_and_madrid_each_use_their_own_local_clock(
    conn, scheduler, clock, dispatch, start_app, make_qualified
) -> None:
    def approved_in_madrid(*, minutes_ago: int, external_id: str, phone: str) -> dict:
        app = start_app(
            conn,
            at=clock.now() - timedelta(minutes=minutes_ago),
            external_id=external_id,
            phone=phone,
            stage="confirm",
        )  # no Zone: the country decides the time zone
        qualified = repo.mark_qualified(conn, app["id"], now=clock.now())
        return repo.mark_approved(conn, qualified["id"], APPROVER, now=clock.now())

    # 05:00 UTC is 23:00 in Mexico City and 07:00 in Madrid: quiet in both, and both are awake.
    clock.set(datetime(2026, 10, 13, 5, 0, tzinfo=UTC))
    mx = approve(conn, make_qualified, clock, minutes_ago=5, external_id="mx")
    es = approved_in_madrid(minutes_ago=5, external_id="es", phone="+34600111222")
    assert scheduler.submit_action(conn, mx["id"], ACTION_APPROVED) == "dispatched"
    assert scheduler.submit_action(conn, es["id"], ACTION_APPROVED) == "dispatched"

    # 14:00 UTC is 08:00 in Mexico City (quiet) and 16:00 in Madrid (awake). A silent
    # Candidate is held only where it is still night; an active one is not held at all.
    clock.set(datetime(2026, 10, 13, 14, 0, tzinfo=UTC))
    mx_silent = approve(conn, make_qualified, clock, minutes_ago=120, external_id="mx-silent")
    mx_active = approve(conn, make_qualified, clock, minutes_ago=3, external_id="mx-active")
    es_silent = approved_in_madrid(minutes_ago=120, external_id="es-silent", phone="+34600333444")
    assert scheduler.submit_action(conn, mx_silent["id"], ACTION_APPROVED) == "deferred"
    assert scheduler.submit_action(conn, mx_active["id"], ACTION_APPROVED) == "dispatched"
    assert scheduler.submit_action(conn, es_silent["id"], ACTION_APPROVED) == "dispatched"
    [held] = scheduler.deferred.pending(conn)
    assert (held.application_id, held.run_at) == (mx_silent["id"], NINE_LOCAL)
