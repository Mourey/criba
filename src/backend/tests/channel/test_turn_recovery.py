"""A Candidate's turn is never silently lost (ADR 0073): owed turns are recorded when a message is
accepted, retried with backoff under the same Idempotency-Key, resumed after a restart, run in
order before a newer message, never answered twice, dropped under a Takeover, and escalated
only once the retries are spent. No real model: the fake Hermes of conftest."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from criba.channel.config import ChannelSettings
from criba.channel.router import MAX_TURN_ATTEMPTS, OWED_BACKOFF, TurnRequest
from criba.channel.sender import FALLBACK_TEXT
from criba.channel.service import Channel
from criba.db import connect, repo
from criba.screening.clock import FakeClock

from .conftest import (
    FakeHermes,
    Fakes,
    candidate_for,
    first_message,
    make_run_client,
    run_retries,
    start,
)

FALLBACKS = set(FALLBACK_TEXT.values())


def send(client: TestClient, conv: str, text: str, message_id: str | None = None) -> str:
    message_id = message_id or str(uuid.uuid4())
    resp = client.post(
        f"/api/chat/conversations/{conv}/messages", json={"message_id": message_id, "text": text}
    )
    assert resp.status_code == 202, resp.text
    return message_id


def texts(client: TestClient, conv: str) -> list[str]:
    page = client.get(f"/api/chat/conversations/{conv}/messages").json()
    return [m["text"] for m in page["messages"] if m["direction"] == "outbound"]


def app_id(conn: Any, conv: str) -> str:
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    assert app is not None
    return str(app["id"])


def escalations(conn: Any, application_id: str) -> list[Any]:
    return conn.execute(
        "SELECT status, reason FROM queue_items WHERE application_id = ? AND kind = 'escalation'",
        (application_id,),
    ).fetchall()


def new_channel(db_path: Path, clock: FakeClock, hermes: FakeHermes, fakes: Fakes, **run: Any):
    return Channel(
        connect=lambda: connect(db_path),
        run_client=make_run_client(hermes, **run),
        clock=clock,
        settings=ChannelSettings(db_path=str(db_path), environment="test"),
        pending_choice=fakes.pending_choice,
        interview_offer=fakes.interview_offer,
    )


def accept_only(channel: Channel, conv: str, text: str, message_id: str) -> TurnRequest:
    """Store a message as the web chat does, without running its turn: a process that died
    right after accepting it."""
    with channel.connection() as conn:
        candidate = candidate_for(conn, conv)
        result = channel.router.accept_inbound(
            conn, candidate, channel="webchat", message_id=message_id, text=text
        )
    assert result.turn is not None
    return result.turn


# --------------------------------------------------------------------------- the record


def test_an_accepted_message_is_owed_until_its_reply_goes_out(
    client, conn, channel: Channel
) -> None:
    conv = start(client)
    turn = accept_only(channel, conv, "hola", "m1")
    [row] = repo.owed_turns_of(conn, turn.application_id)
    assert (row["message_id"], row["idempotency_key"], row["typed"]) == (turn.inbound_pk, "m1", 1)
    assert (row["status"], row["attempts"], row["run_input"]) == ("owed", 0, None)

    channel.run_turn(turn)
    assert repo.owed_turns_of(conn, turn.application_id) == []
    assert texts(client, conv) == [first_message("Recibido (IA): hola")]


def test_a_spoken_turn_is_never_owed(channel: Channel, conn) -> None:
    from .conftest import voice_session

    _, candidate = voice_session(conn, "wv_owed")
    result = channel.router.accept_inbound(
        conn, candidate, channel="voice", message_id="vc_1", text="hola", modality="spoken"
    )
    assert result.turn is not None and result.turn.inbound_pk is None
    assert repo.owed_turns_of(conn, result.turn.application_id) == []


# --------------------------------------------------------------------------- item 2: restart


def test_a_turn_lost_to_a_restart_is_resumed_at_startup(
    client, conn, db_path, clock, hermes: FakeHermes, fakes
) -> None:
    conv = start(client)
    accept_only(client.app.state.criba_channel, conv, "Ana López", "m1")
    assert texts(client, conv) == []

    restarted = new_channel(db_path, clock, hermes, fakes)
    assert restarted.resume_owed_turns(background=False) == []  # the lease still holds
    assert restarted.resume_owed_turns(startup=True, background=False) == [app_id(conn, conv)]

    assert texts(client, conv) == [first_message("Recibido (IA): Ana López")]
    assert [p["idempotency_key"] for p in hermes.posts] == ["m1"]
    assert repo.owed_turns_of(conn, app_id(conn, conv)) == []


def test_the_recovery_takes_a_turn_whose_lease_ran_out(
    client, conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    accept_only(channel, conv, "hola", "m1")
    clock.advance(seconds=channel.settings.run_timeout_s + 61)
    assert channel.resume_owed_turns(background=False) == [app_id(conn, conv)]
    assert texts(client, conv) == [first_message("Recibido (IA): hola")]


def test_a_restart_mid_run_replays_the_run_hermes_took(
    client, conn, db_path, clock, hermes: FakeHermes, fakes
) -> None:
    """The process died after POSTing: Hermes has the run under the message's key, and the
    resume gets that run back instead of starting a second one."""
    conv = start(client)
    channel = client.app.state.criba_channel
    turn = accept_only(channel, conv, "hola", "m1")
    with channel.connection() as own:
        row = repo.owed_turn(own, turn.inbound_pk)
        assert row is not None
        # The first attempt got as far as the POST.
        repo.set_owed_run_input(own, row["message_id"], "hola", "2026-10-09T10:00:00Z")
    hermes.handler(
        httpx.Request(
            "POST",
            "http://hermes:8642/p/criba-recruiter/v1/runs",
            headers={"Authorization": f"Bearer {hermes.key}", "Idempotency-Key": "m1"},
            json={"input": "hola", "session_id": turn.application_id},
        )
    )
    new_channel(db_path, clock, hermes, fakes).resume_owed_turns(startup=True, background=False)

    assert len(hermes.runs) == 1  # replayed, not run twice
    assert [p["idempotency_key"] for p in hermes.posts] == ["m1", "m1"]
    assert texts(client, conv) == [first_message("Recibido (IA): hola")]


# --------------------------------------------------------------------------- item 1: retries


def test_a_failed_turn_is_retried_with_backoff_and_answered(
    client, conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    hermes.down = True
    send(client, conv, "Ana López", message_id="m1")
    assert texts(client, conv) == [FALLBACK_TEXT["es"]]
    app = app_id(conn, conv)
    [row] = repo.owed_turns_of(conn, app)
    assert (row["attempts"], row["holding_sent"], row["last_error"]) == (1, 1, "error")

    clock.advance(OWED_BACKOFF[0] - timedelta(seconds=1))
    assert channel.resume_owed_turns(background=False) == []  # not due yet
    clock.advance(seconds=1)
    assert channel.resume_owed_turns(background=False) == [app]  # still down
    [row] = repo.owed_turns_of(conn, app)
    assert row["attempts"] == 2
    assert texts(client, conv) == [FALLBACK_TEXT["es"]]  # one holding message per outage

    hermes.down = False
    clock.advance(OWED_BACKOFF[1])
    assert channel.resume_owed_turns(background=False) == [app]
    assert texts(client, conv) == [FALLBACK_TEXT["es"], first_message("Recibido (IA): Ana López")]
    assert repo.owed_turns_of(conn, app) == []
    assert escalations(conn, app) == []
    assert [p["idempotency_key"] for p in hermes.posts] == ["m1"]  # the first never reached it


def test_retries_spent_open_one_escalation_and_the_owed_turn_waits(
    client, conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    hermes.down = True
    send(client, conv, "hola")
    assert run_retries(channel, clock) == MAX_TURN_ATTEMPTS - 1
    app = app_id(conn, conv)
    assert [tuple(r) for r in escalations(conn, app)] == [("open", "technical_error")]
    [row] = repo.owed_turns_of(conn, app)
    assert (row["status"], row["attempts"]) == ("exhausted", MAX_TURN_ATTEMPTS)
    assert channel.resume_owed_turns(background=False) == []  # no more automatic attempts
    assert sum(t in FALLBACKS for t in texts(client, conv)) == 1
    errors = [e for e in repo.list_events(conn, app) if e["kind"] == "run_error"]
    assert len(errors) == MAX_TURN_ATTEMPTS


def test_a_newer_message_runs_after_the_owed_one_in_order(client, conn, hermes: FakeHermes) -> None:
    """The sim's stuck greeting: the name's turn was lost, and "Madrid, Chamberí" was read
    as the name. Now the owed turn runs first, then the new message."""
    conv = start(client)
    hermes.down = True
    send(client, conv, "Ana López", message_id="m1")
    hermes.down = False
    send(client, conv, "Madrid, Chamberí", message_id="m2")

    assert texts(client, conv) == [
        FALLBACK_TEXT["es"],
        first_message("Recibido (IA): Ana López"),
        "Recibido (IA): Madrid, Chamberí",
    ]
    assert [p["idempotency_key"] for p in hermes.posts] == ["m1", "m2"]
    assert repo.owed_turns_of(conn, app_id(conn, conv)) == []


def test_a_newer_message_waits_while_the_owed_one_still_fails(
    client, conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    hermes.down = True
    send(client, conv, "Ana López", message_id="m1")
    send(client, conv, "Madrid", message_id="m2")
    assert texts(client, conv) == [FALLBACK_TEXT["es"]]
    app = app_id(conn, conv)
    assert [r["attempts"] for r in repo.owed_turns_of(conn, app)] == [2, 0]

    hermes.down = False
    clock.advance(OWED_BACKOFF[1])
    channel.resume_owed_turns(background=False)
    assert texts(client, conv)[1:] == [
        first_message("Recibido (IA): Ana López"),
        "Recibido (IA): Madrid",
    ]
    assert repo.owed_turns_of(conn, app) == []


# --------------------------------------------------------------------------- keys, replays


def test_a_timed_out_run_is_retried_under_the_same_key_and_counted_once(
    client, conn, db_path, clock, hermes: FakeHermes, fakes
) -> None:
    conv = start(client)
    channel = new_channel(db_path, clock, hermes, fakes, timeout_s=0.0)
    client.app.state.criba_channel = channel
    hermes.running_polls = 1  # the first poll says "running": past a 0 s timeout
    hermes.stop_loses_race = True  # the stop (ADR 0077) comes too late: the run completes
    send(client, conv, "hola", message_id="m1")
    [row] = conn.execute("SELECT status, run_id, input_tokens FROM usage").fetchall()
    assert tuple(row) == ("timeout", "run_1", 0)
    assert hermes.stops[0]["run_id"] == "run_1"

    clock.advance(OWED_BACKOFF[0])
    channel.resume_owed_turns(background=False)  # the run has finished meanwhile
    assert [p["idempotency_key"] for p in hermes.posts] == ["m1", "m1"]
    assert hermes.inputs[0] == hermes.inputs[1]  # the same bytes under the same key
    assert texts(client, conv) == [FALLBACK_TEXT["es"], first_message("Recibido (IA): hola")]
    rows = conn.execute("SELECT status, run_id, input_tokens FROM usage").fetchall()
    assert [tuple(r) for r in rows] == [("ok", "run_1", 120)]  # one row, the run's counts


def test_a_timed_out_run_hermes_stopped_is_answered_by_a_new_run(
    client, conn, db_path, clock, hermes: FakeHermes, fakes
) -> None:
    """The stop took (ADR 0077): the replay under the same key reports the run cancelled, which
    is final, so the next attempt takes a new key and its run answers. One holding message."""
    conv = start(client)
    channel = new_channel(db_path, clock, hermes, fakes, timeout_s=0.0)
    client.app.state.criba_channel = channel
    hermes.running_polls = 1
    send(client, conv, "hola", message_id="m1")
    assert [s["run_id"] for s in hermes.stops] == ["run_1"]

    hermes.running_polls = 0
    clock.advance(OWED_BACKOFF[0])
    channel.resume_owed_turns(background=False)  # replays run_1: cancelled, final
    clock.advance(OWED_BACKOFF[1])
    channel.resume_owed_turns(background=False)
    assert [p["idempotency_key"] for p in hermes.posts] == ["m1", "m1", "m1~r2"]
    assert texts(client, conv) == [FALLBACK_TEXT["es"], first_message("Recibido (IA): hola")]
    rows = conn.execute("SELECT run_id, status, input_tokens, error FROM usage ORDER BY id")
    rows = [tuple(r) for r in rows]
    assert [r[:3] for r in rows] == [("run_1", "error", 0), ("run_2", "ok", 120)]
    assert rows[0][3].startswith("[usage unknown]")  # the stopped run's spend is not zero


def test_a_run_hermes_reported_failed_is_retried_under_a_new_key(
    client, conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    hermes.final_status = "failed"
    send(client, conv, "hola", message_id="m1")
    hermes.final_status = "completed"
    clock.advance(OWED_BACKOFF[0])
    channel.resume_owed_turns(background=False)

    assert [p["idempotency_key"] for p in hermes.posts] == ["m1", "m1~r1"]
    assert texts(client, conv) == [FALLBACK_TEXT["es"], first_message("Recibido (IA): hola")]


def test_a_turn_the_recovery_answered_is_not_answered_again(
    client, conn, channel: Channel, hermes: FakeHermes
) -> None:
    conv = start(client)
    turn = accept_only(channel, conv, "hola", "m1")
    channel.resume_owed_turns(startup=True, background=False)
    assert channel.run_turn(turn) is None  # the accepting request comes late: nothing to do
    assert channel.resume_owed_turns(startup=True, background=False) == []
    assert texts(client, conv) == [first_message("Recibido (IA): hola")]
    assert len(hermes.posts) == 1


def test_a_retry_reuses_the_jev_reading(
    client, conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    reads: list[str] = []

    def read(conn: Any, app: Any, *, message_id: int, text: str, now: Any) -> Any:
        reads.append(text)
        return SimpleNamespace(hold=False)

    channel.router.jev = SimpleNamespace(read=read)
    conv = start(client)
    hermes.final_status = "failed"
    send(client, conv, "hola")
    hermes.final_status = "completed"
    clock.advance(OWED_BACKOFF[0])
    channel.resume_owed_turns(background=False)
    assert reads == ["hola"]
    assert texts(client, conv)[-1] == first_message("Recibido (IA): hola")


# --------------------------------------------------------------------------- Takeover


def take_over(conn: Any, application_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO users (id, client_id, email, name, role, password_hash,"
        " created_at) VALUES ('u1', 'grupo_sazon', 'r@example.com', 'R', 'recruiter', 'x',"
        " '2026-10-09')"
    )
    conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'u1' WHERE id = ?",
        (application_id,),
    )


def test_a_takeover_blocks_the_retry_and_drops_the_owed_turn(
    client, conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    hermes.down = True
    send(client, conv, "hola")
    app = app_id(conn, conv)
    take_over(conn, app)
    hermes.down = False
    clock.advance(OWED_BACKOFF[0])

    assert channel.resume_owed_turns(background=False) == [app]
    assert repo.owed_turns_of(conn, app) == []
    assert len(hermes.posts) == 0
    assert texts(client, conv) == [FALLBACK_TEXT["es"]]
    assert escalations(conn, app) == []


def test_the_recovery_skips_an_application_with_a_turn_in_flight(
    client, conn, channel: Channel, clock
) -> None:
    conv = start(client)
    turn = accept_only(channel, conv, "hola", "m1")
    channel.turn_accepted(turn)  # the accepting request will run it
    assert channel.resume_owed_turns(startup=True, background=False) == []
    channel.run_turn(turn, accepted=True)
    assert texts(client, conv) == [first_message("Recibido (IA): hola")]


def test_resume_in_the_background_answers_the_turn(client, conn, channel: Channel) -> None:
    conv = start(client)
    accept_only(channel, conv, "hola", "m1")
    [app] = channel.resume_owed_turns(startup=True)
    assert channel.await_turn(app, timeout_s=5.0)
    deadline = 50
    while repo.owed_turns_of(conn, app) and deadline:
        channel.await_turn(app, timeout_s=0.1)
        deadline -= 1
    assert texts(client, conv) == [first_message("Recibido (IA): hola")]


def test_a_failure_to_record_the_owed_turn_still_runs_it(
    client, conn, channel: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = start(client)

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(repo, "add_owed_turn", broken)
    send(client, conv, "hola")
    assert texts(client, conv) == [first_message("Recibido (IA): hola")]
