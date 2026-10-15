"""The scheduler purges message signals past the retention period (ADR 0051)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from criba.db import repo
from criba.db.connection import iso
from criba.jev import store
from criba.jev.battery import Reading
from criba.tools import trace

from .conftest import T0


def keep_signal(conn, app, *, at, body: str = "hola") -> None:
    message, _ = repo.add_message(
        conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body=body,
        now=at,
    )
    reading = Reading("es", 1.0, 0.0, {"frustrated": 0.0, "wants_human": 0.0})
    store.record_signals(
        conn, message_id=message["id"], application=app, now=at, status="ok", reading=reading
    )


def test_old_signals_are_purged_on_a_run_and_recent_ones_stay(
    scheduler, conn, clock, start_app
) -> None:
    app = start_app(conn)
    keep_signal(conn, app, at=T0 - timedelta(days=181))
    keep_signal(conn, app, at=T0 - timedelta(days=179))
    report = scheduler.run_due_jobs()
    assert report.retention.signals_purged == 1
    assert report.as_dict()["retention"] == {"signals_purged": 1}
    assert len(store.list_signals(conn, app["id"])) == 1
    assert scheduler.run_due_jobs().retention.signals_purged == 0


def test_the_period_is_a_setting(
    scheduler, conn, start_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRIBA_SIGNALS_RETENTION_DAYS", "30")
    app = start_app(conn)
    keep_signal(conn, app, at=T0 - timedelta(days=31))
    keep_signal(conn, app, at=T0 - timedelta(days=29))
    assert scheduler.run_due_jobs().retention.signals_purged == 1


def test_a_bad_setting_falls_back_to_180_days(monkeypatch: pytest.MonkeyPatch) -> None:
    for bad in ("soon", "0", "-5"):
        monkeypatch.setenv("CRIBA_SIGNALS_RETENTION_DAYS", bad)
        assert store.retention_days() == 180


def test_a_failed_purge_does_not_stop_the_other_jobs(
    scheduler, conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("disk")

    monkeypatch.setattr("criba.scheduler.service.purge_signals", boom)
    assert scheduler.run_due_jobs().retention.signals_purged == 0


def keep_tool_call(conn, app, *, at, tool: str = "get_application") -> None:
    conn.execute(
        "INSERT INTO tool_calls (application_id, tool, started_at, duration_ms, outcome,"
        " created_at) VALUES (?, ?, ?, 3, 'ok', ?)",
        (app["id"], tool, iso(at), iso(at)),
    )


def tool_call_count(conn) -> int:
    return conn.execute("SELECT count(*) FROM tool_calls").fetchone()[0]


def test_old_tool_calls_are_purged_on_a_run_and_recent_ones_stay(
    scheduler, conn, clock, start_app
) -> None:
    app = start_app(conn)
    keep_tool_call(conn, app, at=T0 - timedelta(days=181))
    keep_tool_call(conn, app, at=T0 - timedelta(days=179))
    report = scheduler.run_due_jobs()
    assert tool_call_count(conn) == 1
    kept = conn.execute("SELECT created_at FROM tool_calls").fetchone()[0]
    assert kept == iso(T0 - timedelta(days=179))
    assert report.as_dict()["retention"] == {"signals_purged": 0}
    scheduler.run_due_jobs()
    assert tool_call_count(conn) == 1


def test_tool_calls_follow_the_signals_retention_setting(
    scheduler, conn, start_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRIBA_SIGNALS_RETENTION_DAYS", "30")
    app = start_app(conn)
    keep_tool_call(conn, app, at=T0 - timedelta(days=31))
    keep_tool_call(conn, app, at=T0 - timedelta(days=29))
    scheduler.run_due_jobs()
    assert tool_call_count(conn) == 1


def test_purge_tool_calls_takes_the_period_and_counts_the_rows(conn, start_app) -> None:
    app = start_app(conn)
    for days in (10, 5, 1):
        keep_tool_call(conn, app, at=T0 - timedelta(days=days))
    assert trace.purge_tool_calls(conn, T0, 7) == 1
    assert trace.purge_tool_calls(conn, T0, 7) == 0
    assert trace.purge_tool_calls(conn, T0, 2) == 1
    assert tool_call_count(conn) == 1


def test_a_failed_tool_call_purge_leaves_the_signals_purge_and_the_rows(
    scheduler, conn, start_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("disk")

    app = start_app(conn)
    keep_signal(conn, app, at=T0 - timedelta(days=181))
    keep_tool_call(conn, app, at=T0 - timedelta(days=181))
    monkeypatch.setattr("criba.scheduler.service.purge_tool_calls", boom)
    report = scheduler.run_due_jobs()
    assert report.retention.signals_purged == 1
    assert store.list_signals(conn, app["id"]) == []
    assert tool_call_count(conn) == 1
    assert report.as_dict().keys() == {"deferred", "stalled", "flags", "retention"}


def test_a_failed_signals_purge_does_not_stop_the_tool_call_purge(
    scheduler, conn, start_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("disk")

    app = start_app(conn)
    keep_tool_call(conn, app, at=T0 - timedelta(days=181))
    monkeypatch.setattr("criba.scheduler.service.purge_signals", boom)
    scheduler.run_due_jobs()
    assert tool_call_count(conn) == 0


def test_deleting_an_application_deletes_its_tool_calls(conn, start_app) -> None:
    other = start_app(conn)
    conn.execute(
        "INSERT INTO candidates (id, client_id, created_at, updated_at)"
        " VALUES ('cand_bare', 'grupo_sazon', 'x', 'x')"
    )
    conn.execute(
        "INSERT INTO applications (id, candidate_id, client_id, channel, created_at, updated_at)"
        " VALUES ('app_bare', 'cand_bare', 'grupo_sazon', 'webchat', 'x', 'x')"
    )
    bare = {"id": "app_bare"}
    keep_tool_call(conn, bare, at=T0)
    keep_tool_call(conn, bare, at=T0)
    keep_tool_call(conn, other, at=T0)
    conn.execute("DELETE FROM applications WHERE id = 'app_bare'")
    left = conn.execute("SELECT application_id FROM tool_calls").fetchall()
    assert [r[0] for r in left] == [other["id"]]


def keep_intent_signal(conn, app, *, at) -> None:
    """A stored signal that carries the intent reading and the malice answer (ADR 0071)."""
    message, _ = repo.add_message(
        conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body="hola",
        now=at,
    )
    reading = Reading(
        "es",
        1.0,
        0.0,
        {"frustrated": 0.0, "wants_human": 0.0},
        intent="steering",
        intent_confidence=0.93,
        malice=0.97,
    )
    store.record_signals(
        conn, message_id=message["id"], application=app, now=at, status="ok", reading=reading
    )


def test_a_signal_past_the_period_is_purged_with_its_intent_and_malice(
    scheduler, conn, start_app
) -> None:
    app = start_app(conn)
    keep_intent_signal(conn, app, at=T0 - timedelta(days=181))
    keep_intent_signal(conn, app, at=T0 - timedelta(days=179))
    assert (
        conn.execute("SELECT count(*) FROM message_signals WHERE malice = 0.97").fetchone()[0] == 2
    )
    assert scheduler.run_due_jobs().retention.signals_purged == 1
    [kept] = store.list_signals(conn, app["id"])
    assert (kept["intent"], kept["intent_confidence"], kept["malice"]) == ("steering", 0.93, 0.97)
    assert (
        conn.execute("SELECT count(*) FROM message_signals WHERE malice = 0.97").fetchone()[0] == 1
    )
