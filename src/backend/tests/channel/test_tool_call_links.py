"""Each ``tool_calls`` row is linked to its run when the run returns or fails (ADR 0069).

The backend cannot know a run's id while its tools run, so the router links by Application and
time window: the rows with no run id and no closed mark that started between the run's start and
the moment it was stored. A failure with no run id closes its rows instead, so a later run never
claims them. Tool calls are written through ``trace.record`` from inside the fake Hermes, while
a run is in flight."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from criba.channel import router as router_module
from criba.channel.service import Channel, SpokenTurnRequest
from criba.db import connect, repo
from criba.screening.clock import FakeClock
from criba.tools import trace

from .conftest import (
    FakeHermes,
    GatedRunClient,
    candidate_for,
    drain,
    first_message,
    run_retries,
    sse,
    start,
    voice_session,
)

RUN_SECONDS = 5


class ToolCallingHermes(FakeHermes):
    """A fake Hermes whose agent calls tools: ``during_run(run_id)`` is called once for each new
    run, right after Hermes accepted it, which is while the run is in flight."""

    def __init__(self) -> None:
        super().__init__()
        self.during_run: Callable[[str], None] = lambda run_id: None

    def handler(self, request: httpx.Request) -> httpx.Response:
        response = super().handler(request)
        replayed = "Idempotency-Replayed" in response.headers
        if request.method == "POST" and response.status_code == 202 and not replayed:
            self.during_run(list(self.runs)[-1])
        return response


@pytest.fixture
def hermes() -> ToolCallingHermes:
    return ToolCallingHermes()


def call(
    db_path: Path, application_id: str, clock: FakeClock, tool: str = "get_application"
) -> None:
    """One tool call, written as ``/internal/tools`` writes it, at the clock's time."""
    trace.record(
        lambda: connect(db_path),
        tool=tool,
        args={},
        session_id=application_id,
        payload={"ok": True, "value": {}},
        started_at=clock.now(),
        duration_ms=1,
        now=clock.now(),
    )


def calls_during_run(
    hermes: ToolCallingHermes,
    db_path: Path,
    clock: FakeClock,
    application_id: Callable[[], str],
    *,
    count: int = 3,
    also: Callable[[], None] = lambda: None,
) -> None:
    """The agent makes ``count`` calls on the Application while the run is in flight, then the
    run takes ``RUN_SECONDS``."""

    def during_run(run_id: str) -> None:
        for _ in range(count):
            call(db_path, application_id(), clock)
        also()
        clock.advance(seconds=RUN_SECONDS)

    hermes.during_run = during_run


def links(conn: Any, application_id: str | None = None) -> list[tuple[str | None, int]]:
    """``(run_id, run_closed)`` of the rows, oldest first."""
    sql = "SELECT run_id, run_closed FROM tool_calls"
    params: tuple[str, ...] = ()
    if application_id is not None:
        sql += " WHERE application_id = ?"
        params = (application_id,)
    return [tuple(r) for r in conn.execute(sql + " ORDER BY id", params)]


def send(client: TestClient, conv: str, text: str) -> None:
    resp = client.post(
        f"/api/chat/conversations/{conv}/messages",
        json={"message_id": str(uuid.uuid4()), "text": text},
    )
    assert resp.status_code == 202, resp.text


def last_reply(client: TestClient, conv: str) -> str:
    page = client.get(f"/api/chat/conversations/{conv}/messages").json()
    return [m for m in page["messages"] if m["direction"] == "outbound"][-1]["text"]


def app_of(db_path: Path, conv: str) -> Callable[[], str]:
    """The conversation's open Application, read on a connection of its own (the fake Hermes
    calls this from another thread, and the Application exists only once a message arrived)."""

    def find() -> str:
        with closing(connect(db_path)) as other:
            app = repo.get_open_application(other, candidate_for(other, conv)["id"])
        assert app is not None
        return app["id"]

    return find


# --------------------------------------------------------------------------- typed runs


def test_a_run_with_three_tool_calls_gives_them_its_run_id(
    client, conn, hermes, db_path, clock
) -> None:
    conv = start(client)
    app_id = app_of(db_path, conv)
    calls_during_run(hermes, db_path, clock, app_id)
    send(client, conv, "hola")
    assert links(conn) == [("run_1", 0)] * 3


def test_a_later_runs_calls_get_the_later_id_and_the_earlier_rows_keep_theirs(
    client, conn, hermes, db_path, clock
) -> None:
    conv = start(client)
    app_id = app_of(db_path, conv)
    calls_during_run(hermes, db_path, clock, app_id, count=2)
    send(client, conv, "hola")
    clock.advance(seconds=60)
    calls_during_run(hermes, db_path, clock, app_id, count=1)
    send(client, conv, "Madrid")
    assert links(conn) == [("run_1", 0)] * 2 + [("run_2", 0)]


def test_a_failed_run_that_has_a_run_id_links_its_calls(
    client, conn, hermes, db_path, clock
) -> None:
    conv = start(client)
    app_id = app_of(db_path, conv)
    hermes.final_status = "failed"
    calls_during_run(hermes, db_path, clock, app_id)
    send(client, conv, "hola")
    assert links(conn) == [("run_1", 0)] * 3
    assert [r["status"] for r in conn.execute("SELECT status FROM usage")] == ["error"]


def test_a_run_that_completed_and_failed_while_sending_keeps_the_links_it_made(
    client, conn, hermes, db_path, clock, channel: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = start(client)
    app_id = app_of(db_path, conv)
    calls_during_run(hermes, db_path, clock, app_id)
    real_send = channel.sender.send
    failures: list[int] = []

    def send_once_fails(*args: Any, **kwargs: Any) -> Any:
        if not failures:
            failures.append(1)
            raise OSError("adapter down")
        return real_send(*args, **kwargs)

    monkeypatch.setattr(channel.sender, "send", send_once_fails)
    send(client, conv, "hola")
    assert failures == [1]
    assert links(conn) == [("run_1", 0)] * 3
    assert [r["status"] for r in conn.execute("SELECT status FROM usage")] == ["error"]


def test_a_failed_run_with_no_run_id_closes_its_calls_and_the_next_run_does_not_claim_them(
    client, conn, hermes, db_path, clock
) -> None:
    conv = start(client)
    app_id = app_of(db_path, conv)
    hermes.accepted_run_id = {"id": "run_1"}  # a run id Hermes sent malformed: no run id
    calls_during_run(hermes, db_path, clock, app_id, count=2)
    send(client, conv, "hola")
    assert links(conn) == [(None, 1)] * 2

    # The next run starts in the same second the closed calls were made in.
    hermes.accepted_run_id = None
    clock.set(clock.now() - timedelta(seconds=RUN_SECONDS))
    calls_during_run(hermes, db_path, clock, app_id, count=1)
    send(client, conv, "Madrid")
    assert links(conn) == [(None, 1)] * 2 + [("run_2", 0)]


def test_a_call_after_its_runs_window_closed_stays_unlinked(
    client, conn, hermes, db_path, clock
) -> None:
    conv = start(client)
    app_id = app_of(db_path, conv)
    calls_during_run(hermes, db_path, clock, app_id, count=1)
    send(client, conv, "hola")
    clock.advance(seconds=30)
    call(db_path, app_id(), clock)  # a timed-out run still calling a tool
    clock.advance(seconds=60)
    calls_during_run(hermes, db_path, clock, app_id, count=1)
    send(client, conv, "Madrid")
    assert links(conn) == [("run_1", 0), (None, 0), ("run_2", 0)]


def test_another_applications_rows_in_the_same_window_stay_untouched(
    client, conn, hermes, db_path, clock
) -> None:
    conv, other_conv = start(client), start(client)
    app_id, other_id = app_of(db_path, conv), app_of(db_path, other_conv)
    send(client, other_conv, "hola")  # the first message creates the Application
    calls_during_run(
        hermes,
        db_path,
        clock,
        app_id,
        also=lambda: call(db_path, other_id(), clock),
    )
    send(client, conv, "hola")
    assert links(conn, app_id()) == [("run_2", 0)] * 3  # run_1 was the other conversation's
    assert links(conn, other_id()) == [(None, 0)]


def test_a_link_that_fails_leaves_the_reply_the_usage_row_and_the_run_unharmed(
    client, conn, hermes, db_path, clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = start(client)
    app_id = app_of(db_path, conv)
    calls_during_run(hermes, db_path, clock, app_id)

    def broken(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("disk I/O")

    monkeypatch.setattr(trace, "close_run", broken)
    send(client, conv, "hola")
    assert last_reply(client, conv) == first_message("Recibido (IA): hola")
    assert [tuple(r) for r in conn.execute("SELECT status, run_id FROM usage")] == [("ok", "run_1")]
    assert not [e for e in repo.list_events(conn, app_id()) if e["kind"] == "run_error"]
    assert links(conn) == [(None, 0)] * 3


def test_a_link_that_fails_on_a_failed_run_leaves_the_incident_as_it_is(
    client, conn, hermes, db_path, clock, channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = start(client)
    app_id = app_of(db_path, conv)
    hermes.final_status = "failed"
    calls_during_run(hermes, db_path, clock, app_id)

    def broken(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("disk I/O")

    monkeypatch.setattr(trace, "close_run", broken)
    send(client, conv, "hola")
    errors = [e for e in repo.list_events(conn, app_id()) if e["kind"] == "run_error"]
    assert [e["data"]["run_id"] for e in errors] == ["run_1"]
    assert [r["status"] for r in conn.execute("SELECT status FROM usage")] == ["error"]
    run_retries(channel, clock)  # Hermes keeps failing: each retry runs under a new key
    [item] = conn.execute(
        "SELECT status, reason FROM queue_items WHERE application_id = ? AND kind = 'escalation'",
        (app_id(),),
    ).fetchall()
    assert tuple(item) == ("open", "technical_error")
    statuses = [r["status"] for r in conn.execute("SELECT status FROM usage")]
    assert statuses == ["error"] * 4


def test_nothing_is_closed_when_the_failure_came_before_the_run_started(
    client, conn, hermes, db_path, clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = start(client)
    app_id = app_of(db_path, conv)
    send(client, conv, "hola")  # the first message creates the Application
    call(db_path, app_id(), clock)

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("jev down")

    monkeypatch.setattr(router_module.Router, "_read_with_jev", broken)
    send(client, conv, "Madrid")
    assert links(conn) == [(None, 0)]
    assert [r["status"] for r in conn.execute("SELECT status FROM usage")] == ["ok", "error"]


# --------------------------------------------------------------------------- spoken runs

SAID = "me llamo Ana y vivo en Madrid"


def utterance(conn: Any, *, n: int = 1, conversation_id: str = "wc_voice") -> SpokenTurnRequest:
    row = conn.execute(
        "SELECT id, candidate_id FROM voice_sessions WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        session_id, candidate = voice_session(conn, conversation_id)
        candidate_id = candidate["id"]
    else:
        session_id, candidate_id = row["id"], row["candidate_id"]
    return SpokenTurnRequest(
        conversation_id=conversation_id,
        candidate_id=candidate_id,
        voice_session_id=session_id,
        message_id=f"vc_{session_id}_{n}",
        text=SAID,
    )


def spoken_app(db_path: Path, candidate_id: str) -> Callable[[], str]:
    def find() -> str:
        with closing(connect(db_path)) as other:
            app = repo.get_open_application(other, candidate_id)
        assert app is not None
        return app["id"]

    return find


def test_a_spoken_turn_links_its_calls_the_same_way(
    conn, spoken_channel: Channel, hermes, db_path, clock
) -> None:
    first = utterance(conn, n=1)
    calls_during_run(hermes, db_path, clock, spoken_app(db_path, first.candidate_id))
    drain(spoken_channel.run_spoken_turn(first))
    clock.advance(seconds=60)
    calls_during_run(hermes, db_path, clock, spoken_app(db_path, first.candidate_id), count=1)
    drain(spoken_channel.run_spoken_turn(utterance(conn, n=2)))
    assert links(conn) == [("run_1", 0)] * 3 + [("run_2", 0)]


def test_a_failed_spoken_run_with_a_run_id_links_its_calls(
    conn, spoken_channel: Channel, hermes, db_path, clock, gated: GatedRunClient
) -> None:
    request = utterance(conn)
    calls_during_run(hermes, db_path, clock, spoken_app(db_path, request.candidate_id), count=2)
    hermes.event_scripts = [
        [": open\n\n", sse(0, {"event": "run.failed", "run_id": "run_1", "error": "model error"})]
    ]
    drain(spoken_channel.run_spoken_turn(request))
    assert links(conn) == [("run_1", 0)] * 2
    assert [r["status"] for r in conn.execute("SELECT status FROM usage")] == ["error"]
