"""One failing phase of a scheduler tick does not skip the phases after it."""

from __future__ import annotations

import pytest

from criba.scheduler import service
from criba.scheduler.jobs import FlagReport


def test_a_raising_held_actions_phase_still_runs_stalled_and_flags(
    conn, scheduler, clock, sender, start_app, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    app = start_app(conn, stage="zone")

    def broken(_conn):
        _conn.execute("BEGIN")  # leaves a transaction open, as a crash mid-phase would
        raise RuntimeError("held actions are broken")

    monkeypatch.setattr(scheduler.deferred, "run_due", broken)
    flag_runs: list[str] = []

    def flags(_conn, _now) -> FlagReport:
        flag_runs.append("ran")
        return FlagReport(qualified_waiting_set=2)

    monkeypatch.setattr(service, "run_flags", flags)
    clock.advance(hours=1)

    report = scheduler.run_due_jobs(conn)

    assert report.deferred == service.DeferralReport()
    [request] = sender.requests
    assert request.application_id == app["id"] and report.stalled.nudges_sent == 1
    assert flag_runs == ["ran"] and report.flags.qualified_waiting_set == 2
    assert "scheduler phase held actions failed" in caplog.text


def test_a_raising_stalled_phase_still_runs_the_flags(
    conn, scheduler, clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args):
        raise RuntimeError("stalled is broken")

    monkeypatch.setattr(service, "run_stalled", broken)
    flag_runs: list[str] = []
    monkeypatch.setattr(
        service, "run_flags", lambda _c, _n: flag_runs.append("ran") or FlagReport()
    )

    report = scheduler.run_due_jobs(conn)

    assert report.stalled == service.StalledReport() and flag_runs == ["ran"]
    assert not conn.in_transaction
