"""Stalled nudges, the template, and Abandoned, driven by the Clock (Phase 1 §4.1)."""

from datetime import UTC, datetime, timedelta

from criba.db import repo
from criba.scheduler import STALLED_TEMPLATE

T0 = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)  # 14:00 in Mexico City


def test_nothing_happens_before_an_hour(conn, scheduler, clock, sender, start_app) -> None:
    app = start_app(conn, stage="zone")
    clock.advance(minutes=59)
    report = scheduler.run_due_jobs(conn)
    assert sender.requests == []
    assert report.stalled.stalled_marked == 0
    assert repo.require_application(conn, app["id"])["stalled_since"] is None


def test_the_first_nudge_goes_out_at_one_hour_and_marks_the_application_stalled(
    conn, scheduler, clock, sender, start_app, kinds
) -> None:
    app = start_app(conn, stage="zone")
    clock.advance(hours=1)
    report = scheduler.run_due_jobs(conn)

    assert report.stalled.stalled_marked == 1 and report.stalled.nudges_sent == 1
    [request] = sender.requests
    assert (request.application_id, request.step, request.kind) == (app["id"], 1, "nudge")
    assert request.stage == "zone" and request.template is None
    assert request.language == "es-MX" and request.channel == "webchat"

    after = repo.require_application(conn, app["id"])
    assert after["nudges_sent"] == 1
    assert after["stalled_since"] == "2026-10-12T21:00:00Z"
    assert kinds(conn, app["id"])[-2:] == ["stalled", "nudge_sent"]


def test_running_again_does_not_nudge_twice(conn, scheduler, clock, sender, start_app) -> None:
    start_app(conn, stage="zone")
    clock.advance(hours=1)
    scheduler.run_due_jobs(conn)
    clock.advance(minutes=30)
    report = scheduler.run_due_jobs(conn)
    assert len(sender.requests) == 1
    assert report.stalled.nudges_sent == 0 and report.stalled.stalled_marked == 0


def test_the_full_ladder_is_nudge_last_nudge_template_then_abandoned(
    conn, scheduler, clock, sender, start_app, kinds
) -> None:
    app = start_app(conn, stage="availability")
    for hours in (1, 20, 48):
        clock.set(T0 + timedelta(hours=hours))
        scheduler.run_due_jobs(conn)
    assert [(r.step, r.kind) for r in sender.requests] == [
        (1, "nudge"),
        (2, "last_nudge"),
        (3, "template"),
    ]
    assert [r.template for r in sender.requests] == [None, None, STALLED_TEMPLATE]
    assert repo.require_application(conn, app["id"])["nudges_sent"] == 3

    clock.set(T0 + timedelta(days=6, hours=23))
    scheduler.run_due_jobs(conn)
    assert repo.require_application(conn, app["id"])["status"] == "open"
    assert len(sender.requests) == 3  # at most 3 nudges

    clock.set(T0 + timedelta(days=7))
    report = scheduler.run_due_jobs(conn)
    closed = repo.require_application(conn, app["id"])
    assert report.stalled.abandoned == 1
    assert (closed["status"], closed["outcome"], closed["stage"]) == (
        "closed",
        "abandoned",
        "availability",
    )
    assert closed["closed_at"] == "2026-10-19T20:00:00Z"
    assert len(sender.requests) == 3  # Abandoned sends no message
    assert kinds(conn, app["id"]).count("nudge_sent") == 3
    abandoned = [e for e in repo.list_events(conn, app["id"]) if e["kind"] == "abandoned"]
    assert [e["actor"] for e in abandoned] == ["system"]


def test_a_long_jump_sends_only_the_latest_due_step(
    conn, scheduler, clock, sender, start_app, kinds
) -> None:
    app = start_app(conn, stage="zone")
    clock.advance(hours=72)
    scheduler.run_due_jobs(conn)
    assert [r.step for r in sender.requests] == [3]
    assert repo.require_application(conn, app["id"])["nudges_sent"] == 3
    assert kinds(conn, app["id"]).count("nudge_sent") == 1


def test_abandoned_application_gets_a_summary(conn, scheduler, clock, start_app) -> None:
    app = start_app(conn, stage="zone")
    clock.advance(days=8)
    scheduler.run_due_jobs(conn)
    closed = repo.require_application(conn, app["id"])
    assert closed["outcome"] == "abandoned"
    assert closed["summary_structured"]["outcome"] == "abandoned"
    assert closed["summary_prose"]
    assert closed["summary_at"] == "2026-10-20T20:00:00Z"


def test_a_reply_restarts_the_ladder(conn, scheduler, clock, sender, start_app, say) -> None:
    app = start_app(conn, stage="zone")
    clock.advance(hours=1)
    scheduler.run_due_jobs(conn)
    assert repo.require_application(conn, app["id"])["nudges_sent"] == 1

    clock.advance(hours=1)
    say(conn, app, at=clock.now(), body="perdón, estaba ocupado")
    clock.advance(minutes=30)
    report = scheduler.run_due_jobs(conn)
    assert report.stalled.nudges_sent == 0
    after = repo.require_application(conn, app["id"])
    assert (after["nudges_sent"], after["stalled_since"]) == (0, None)

    clock.advance(minutes=30)
    scheduler.run_due_jobs(conn)
    assert [r.step for r in sender.requests] == [1, 1]


def test_quiet_hours_hold_the_nudge_until_nine(
    conn, scheduler, clock, sender, start_app, kinds
) -> None:
    # 21:30 in Mexico City: the 1-hour mark falls at 22:30.
    start = datetime(2026, 10, 13, 3, 30, tzinfo=UTC)
    clock.set(start)
    app = start_app(conn, at=start, stage="zone")

    clock.advance(hours=1)
    report = scheduler.run_due_jobs(conn)
    assert sender.requests == []
    assert report.stalled.nudges_held_quiet == 1
    assert report.stalled.stalled_marked == 1  # Stalled is a state; only the message waits
    assert repo.require_application(conn, app["id"])["nudges_sent"] == 0

    clock.set(datetime(2026, 10, 13, 14, 59, tzinfo=UTC))  # 08:59 local
    scheduler.run_due_jobs(conn)
    assert sender.requests == []

    clock.set(datetime(2026, 10, 13, 15, 0, tzinfo=UTC))  # 09:00 local
    scheduler.run_due_jobs(conn)
    assert [r.step for r in sender.requests] == [1]  # 11.5 hours of silence: step 1, not 2
    assert kinds(conn, app["id"]).count("stalled") == 1


def test_quiet_hours_use_the_candidates_own_zone(conn, scheduler, clock, sender, start_app) -> None:
    # 23:00 UTC is 01:00 in Madrid (quiet) but 17:00 in Mexico City.
    start = datetime(2026, 10, 12, 22, 0, tzinfo=UTC)
    clock.set(start)
    start_app(conn, at=start, external_id="mx", phone="+525512345678", stage="zone")
    start_app(conn, at=start, external_id="es", phone="+34600111222", stage="zone")
    clock.advance(hours=1)
    scheduler.run_due_jobs(conn)
    assert len(sender.requests) == 1
    assert repo.require_application(conn, sender.requests[0].application_id)["country"] == "MX"


def test_an_opted_out_candidate_gets_no_nudge_but_still_abandons(
    conn, scheduler, clock, sender, start_app
) -> None:
    app = start_app(conn, stage="zone")
    repo.mark_opted_out(conn, app["candidate_id"], now=T0)
    clock.advance(hours=30)
    scheduler.run_due_jobs(conn)
    assert sender.requests == []
    clock.advance(days=7)
    scheduler.run_due_jobs(conn)
    assert repo.require_application(conn, app["id"])["outcome"] == "abandoned"


def test_a_takeover_pauses_follow_up(conn, scheduler, clock, sender, start_app) -> None:
    app = start_app(conn, stage="zone")
    conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'usr_rec_puebla',"
        " control_since = ? WHERE id = ?",
        ("2026-10-12T20:30:00Z", app["id"]),
    )
    clock.advance(days=10)
    scheduler.run_due_jobs(conn)
    assert sender.requests == []
    assert repo.require_application(conn, app["id"])["status"] == "open"


def test_only_applications_in_screening_are_followed_up(
    conn, scheduler, clock, sender, make_qualified
) -> None:
    app = make_qualified(conn)
    clock.advance(days=10)
    scheduler.run_due_jobs(conn)
    assert sender.requests == []
    assert repo.require_application(conn, app["id"])["status"] == "open"


def test_a_sender_that_declines_leaves_no_trace_and_is_tried_again(
    conn, scheduler, clock, sender, start_app, kinds
) -> None:
    app = start_app(conn, stage="zone")
    clock.advance(hours=1)
    sender.accept = False
    report = scheduler.run_due_jobs(conn)
    assert report.stalled.nudges_declined == 1
    after = repo.require_application(conn, app["id"])
    assert after["nudges_sent"] == 0
    assert "nudge_sent" not in kinds(conn, app["id"])

    sender.accept = True
    clock.advance(minutes=5)
    scheduler.run_due_jobs(conn)
    assert [r.step for r in sender.requests] == [1]
    assert repo.require_application(conn, app["id"])["nudges_sent"] == 1


def test_a_failing_sender_does_not_stop_the_other_applications(
    conn, scheduler, clock, sender, start_app
) -> None:
    broken = start_app(conn, external_id="a", stage="zone")
    fine = start_app(conn, external_id="b", stage="zone")
    sender.fail_for = {broken["id"]}
    clock.advance(hours=1)
    report = scheduler.run_due_jobs(conn)
    assert report.stalled.errors == 1
    assert [r.application_id for r in sender.requests] == [fine["id"]]
    assert repo.require_application(conn, broken["id"])["nudges_sent"] == 0


def test_a_reopened_application_starts_its_ladder_again(
    conn, scheduler, clock, sender, start_app
) -> None:
    app = start_app(conn, stage="zone")
    clock.advance(days=8)
    scheduler.run_due_jobs(conn)
    assert repo.require_application(conn, app["id"])["outcome"] == "abandoned"
    sender.requests.clear()

    clock.advance(days=2)
    reopened = repo.reopen_application(conn, app["id"], now=clock.now())
    assert reopened["status"] == "open"
    scheduler.run_due_jobs(conn)
    assert sender.requests == []  # reopened just now: nothing is due
    clock.advance(hours=1)
    scheduler.run_due_jobs(conn)
    assert [r.step for r in sender.requests] == [1]
    assert repo.require_application(conn, app["id"])["status"] == "open"


def test_run_due_jobs_opens_its_own_connection(scheduler, conn, clock, sender, start_app) -> None:
    start_app(conn, stage="zone")
    clock.advance(hours=1)
    report = scheduler.run_due_jobs()
    assert report.stalled.nudges_sent == 1
    assert report.as_dict()["stalled"]["nudges_sent"] == 1
