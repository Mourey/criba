"""Summary prose that could not be written is retried, and its state is visible (ADR 0072)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from criba.db import connect, repo
from criba.summary import PROSE_FAILED, PROSE_RETRY_DELAYS, PROSE_RETRYING, SummaryWriter


def writer(client, clock) -> SummaryWriter:
    return SummaryWriter(client, clock, environment="test")


def stored(conn, application_id: str) -> dict:
    return repo.require_application(conn, application_id)


def test_a_failed_prose_is_marked_retrying_with_the_next_try(
    conn, clock, client, qualified_application
) -> None:
    app = qualified_application(conn)
    client.error = httpx.ConnectTimeout("slow")
    done = writer(client, clock).write(conn, app["id"])
    structured = done["summary_structured"]
    assert done["summary_prose"] is None and structured["stage"] == "qualified"
    assert structured["prose_status"] == PROSE_RETRYING
    assert structured["prose_retry"] == {
        "attempts": 1,
        "trigger": "finished",
        "next_at": "2026-10-12T20:05:00Z",
    }


def test_the_retry_waits_for_its_time_then_writes_the_prose(
    conn, clock, client, qualified_application
) -> None:
    app = qualified_application(conn)
    w = writer(client, clock)
    client.error = httpx.ConnectTimeout("slow")
    w.write(conn, app["id"])
    client.error = None

    clock.advance(minutes=4)
    assert w.retry_due(conn) == 0
    clock.advance(minutes=1)
    assert w.retry_due(conn) == 1

    done = stored(conn, app["id"])
    assert done["summary_prose"] == client.text
    assert "prose_status" not in done["summary_structured"]
    assert "prose_retry" not in done["summary_structured"]
    assert client.requests[-1].structured.get("prose_status") is None
    assert w.retry_due(conn) == 0


def test_the_tries_are_bounded_and_end_failed(conn, clock, client, qualified_application) -> None:
    app = qualified_application(conn)
    w = writer(client, clock)
    client.error = httpx.ConnectTimeout("slow")
    w.write(conn, app["id"])
    events = len(repo.list_events(conn, app["id"]))
    for delay in PROSE_RETRY_DELAYS:
        clock.advance(seconds=delay.total_seconds())
        assert w.retry_due(conn) == 1
    structured = stored(conn, app["id"])["summary_structured"]
    assert structured["prose_status"] == PROSE_FAILED
    assert structured["prose_retry"] == {"attempts": 4, "trigger": "finished"}
    clock.advance(days=1)
    assert w.retry_due(conn) == 0
    assert len(client.requests) == 4
    # a failed retry adds no summary_written event; only the ledger records the calls
    assert len(repo.list_events(conn, app["id"])) == events


def test_a_rejected_request_is_failed_at_once(conn, clock, client, qualified_application) -> None:
    app = qualified_application(conn)
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    client.error = httpx.HTTPStatusError(
        "bad", request=request, response=httpx.Response(400, request=request, json={})
    )
    done = writer(client, clock).write(conn, app["id"])
    assert done["summary_structured"]["prose_status"] == PROSE_FAILED


def test_a_server_error_is_retried(conn, clock, client, qualified_application) -> None:
    app = qualified_application(conn)
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    client.error = httpx.HTTPStatusError(
        "down", request=request, response=httpx.Response(503, request=request, json={})
    )
    done = writer(client, clock).write(conn, app["id"])
    assert done["summary_structured"]["prose_status"] == PROSE_RETRYING


def test_a_retry_survives_a_restart(
    conn, clock, client, qualified_application, tmp_path: Path
) -> None:
    app = qualified_application(conn)
    client.error = httpx.ConnectTimeout("slow")
    writer(client, clock).write(conn, app["id"])
    client.error = None
    clock.advance(minutes=5)
    # A new writer and a new connection: the state is in the database, not in memory.
    fresh = connect(tmp_path / "criba.db")
    try:
        assert writer(client, clock).retry_due(fresh) == 1
    finally:
        fresh.close()
    assert stored(conn, app["id"])["summary_prose"] == client.text


def test_a_summary_rewritten_meanwhile_is_kept(
    conn, clock, client, qualified_application, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = qualified_application(conn)
    w = writer(client, clock)
    client.error = httpx.ConnectTimeout("slow")
    w.write(conn, app["id"])
    clock.advance(minutes=5)

    original = w._prose
    reopened: list[bool] = []

    def reopen_meanwhile(c, application_id, structured, trigger):
        if not reopened:  # the retry's call: a Reopen writes a new summary while it waits
            reopened.append(True)
            clock.advance(seconds=1)
            client.error = None
            w.write(c, application_id, trigger="reopened")
            client.text = "Late answer that must not overwrite."
        return original(c, application_id, structured, trigger)

    monkeypatch.setattr(w, "_prose", reopen_meanwhile)
    w.retry_due(conn)
    done = stored(conn, app["id"])
    assert done["summary_prose"] == "Candidate looks fine. Nothing odd."
    assert "prose_status" not in done["summary_structured"]


def test_the_transcript_failing_still_stores_the_structured_part(
    conn, clock, client, qualified_application, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = qualified_application(conn)

    def broken(*_args):
        raise RuntimeError("transcript is broken")

    monkeypatch.setattr("criba.summary._transcript", broken)
    done = writer(client, clock).write(conn, app["id"])
    assert done["summary_structured"]["stage"] == "qualified"
    assert done["summary_structured"]["prose_status"] == PROSE_RETRYING


def test_a_ledger_failure_leaves_the_summary_stored(
    conn, clock, client, qualified_application, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = qualified_application(conn)
    w = writer(client, clock)

    def broken(*_args):
        raise RuntimeError("ledger is broken")

    monkeypatch.setattr(w, "_record_usage", broken)
    done = w.write(conn, app["id"])
    assert done["summary_prose"] == client.text
    assert stored(conn, app["id"])["summary_prose"] == client.text


def test_one_failing_retry_does_not_stop_the_others(
    conn, clock, client, qualified_application, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = qualified_application(conn)
    w = writer(client, clock)
    client.error = httpx.ConnectTimeout("slow")
    w.write(conn, first["id"])
    clock.advance(minutes=5)
    calls: list[str] = []

    def broken(_conn, application_id, _now):
        calls.append(application_id)
        raise RuntimeError("retry is broken")

    monkeypatch.setattr(w, "_retry", broken)
    assert w.retry_due(conn) == 0 and calls == [first["id"]]


def test_without_a_client_nothing_is_retried_and_no_state_is_set(
    conn, clock, qualified_application
) -> None:
    app = qualified_application(conn)
    done = writer(None, clock).write(conn, app["id"])
    assert "prose_status" not in done["summary_structured"]
    assert writer(None, clock).retry_due(conn) == 0


def test_the_prose_state_keeps_the_contract_shape(
    conn, clock, client, contract, qualified_application
) -> None:
    app = qualified_application(conn)
    client.error = httpx.ConnectTimeout("slow")
    writer(client, clock).write(conn, app["id"])
    exported = repo.export_application(conn, app["id"])["summary"]
    schema = {"$ref": "#/components/schemas/Summary", "components": contract["components"]}
    errors = list(Draft202012Validator(schema).iter_errors(exported))
    assert not errors, [e.message for e in errors]
    assert exported["structured"]["prose_status"] == PROSE_RETRYING


def test_the_scheduler_runs_the_retries(conn, clock, client, qualified_application) -> None:
    from criba.scheduler import Scheduler

    app = qualified_application(conn)
    w = writer(client, clock)
    client.error = httpx.ConnectTimeout("slow")
    w.write(conn, app["id"])
    client.error = None
    clock.advance(seconds=PROSE_RETRY_DELAYS[0].total_seconds())
    scheduler = Scheduler(
        connect=lambda: conn,
        clock=clock,
        send_nudge=lambda *_: False,
        dispatch=lambda *_: None,
        summary=w,
    )
    scheduler.run_due_jobs(conn)
    assert stored(conn, app["id"])["summary_prose"] == client.text


def test_the_delays_grow() -> None:
    assert list(PROSE_RETRY_DELAYS) == sorted(PROSE_RETRY_DELAYS)
    assert PROSE_RETRY_DELAYS[0] >= timedelta(minutes=1)
