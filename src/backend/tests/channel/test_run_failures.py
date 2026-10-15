"""Every way a Candidate-triggered run can fail ends the same way (W3-1 P2, ADR 0073): one
holding reply, a failed usage row, an owed turn the recovery runs again, and no 500; the
``technical_error`` Escalation opens once the retries are spent. System turns and Takeovers
keep their rules: no fallback reply, and nothing to the Candidate."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from criba.channel import router as router_module
from criba.channel.sender import FALLBACK_TEXT
from criba.channel.service import Channel
from criba.db import iso, repo

from .conftest import NOW, FakeHermes, candidate_for, first_message, run_retries, start

FALLBACKS = set(FALLBACK_TEXT.values())


def send(client: TestClient, conv: str, text: str, message_id: str | None = None) -> None:
    resp = client.post(
        f"/api/chat/conversations/{conv}/messages",
        json={"message_id": message_id or str(uuid.uuid4()), "text": text},
    )
    assert resp.status_code == 202, resp.text


def outbound(client: TestClient, conv: str) -> list[dict[str, Any]]:
    page = client.get(f"/api/chat/conversations/{conv}/messages").json()
    return [m for m in page["messages"] if m["direction"] == "outbound"]


def fallbacks(client: TestClient, conv: str) -> int:
    return sum(m["text"] in FALLBACKS for m in outbound(client, conv))


def escalations(conn, application_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM queue_items WHERE application_id = ? AND kind = 'escalation'",
        (application_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def app_for(conn, conv: str) -> dict[str, Any]:
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    assert app is not None
    return app


def assert_one_incident(
    client: TestClient, conn, conv: str, retries: Callable[[], Any] | None = None
) -> dict[str, Any]:
    """The first failure: one holding message, an owed turn, no Escalation yet. With
    ``retries`` (the failure goes on), the retries are spent and the Escalation opens."""
    app = app_for(conn, conv)
    assert fallbacks(client, conv) == 1
    # Owed, not escalated: the recovery runs the turn again first (ADR 0073).
    assert escalations(conn, app["id"]) == []
    [owed] = repo.owed_turns_of(conn, app["id"])
    assert (owed["attempts"], owed["status"], owed["holding_sent"]) == (1, "owed", 1)
    errors = [e for e in repo.list_events(conn, app["id"]) if e["kind"] == "run_error"]
    assert errors, "no run_error event"
    # The cause is a code (ADR 0078); the rest is ids and codes only, never Candidate text
    # (ADR 0035).
    assert {e["data"].pop("cause") for e in errors} == {"other"}
    assert all(set(e["data"]) == {"kind", "run_id", "system_turn"} for e in errors)
    statuses = [r[0] for r in conn.execute("SELECT status FROM usage").fetchall()]
    assert "error" in statuses
    if retries is not None:
        retries()
        [item] = escalations(conn, app["id"])
        assert (item["status"], item["reason"]) == ("open", "technical_error")
        assert fallbacks(client, conv) == 1
    return app


def assert_next_message_works(client: TestClient, conn, conv: str, escalated: int = 0) -> None:
    """The next message first runs the owed one, then its own: both answered, in order. The
    owed one carries the first message's privacy notice."""
    send(client, conv, "Madrid")
    assert outbound(client, conv)[-1]["text"] == "Recibido (IA): Madrid"
    texts = [m["text"] for m in outbound(client, conv)]
    assert texts[-2:] == [first_message("Recibido (IA): hola"), "Recibido (IA): Madrid"]
    assert fallbacks(client, conv) == 1
    assert len(escalations(conn, app_for(conn, conv)["id"])) == escalated
    assert repo.owed_turns_of(conn, app_for(conn, conv)["id"]) == []


def fail_once(target: Callable[..., Any], exc: Exception) -> Callable[..., Any]:
    calls = {"n": 0}

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise exc
        return target(*args, **kwargs)

    return wrapper


# --------------------------------------------------------------------------- failure types


def test_unexpected_exception_in_the_run_client(
    client, conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    hermes.crash = RuntimeError("transport bug with Candidate words: hola")
    send(client, conv, "hola")

    [row] = conn.execute("SELECT status, run_id, error FROM usage").fetchall()
    assert (row["status"], row["run_id"]) == ("error", None)
    assert row["error"] == "internal error: RuntimeError"  # no exception message
    # Still crashing: the attempts are spent and the Escalation opens.
    assert_one_incident(client, conn, conv, retries=lambda: run_retries(channel, clock))
    item = escalations(conn, app_for(conn, conv)["id"])[0]
    assert "hola" not in (item["note"] or "")

    # The Candidate's next message runs the exhausted turn first, then its own.
    hermes.crash = None
    assert_next_message_works(client, conn, conv, escalated=1)


def test_malformed_usage_in_a_completed_run(client, conn, hermes: FakeHermes) -> None:
    conv = start(client)
    hermes.usage = ["not", "a", "dict"]  # record_usage cannot read it
    send(client, conv, "hola")

    assert_one_incident(client, conn, conv)
    assert not any(m["text"].startswith("Recibido") for m in outbound(client, conv))
    [row] = conn.execute("SELECT status, run_id FROM usage").fetchall()
    assert tuple(row) == ("error", "run_1")

    hermes.usage = {"input_tokens": 1}
    assert_next_message_works(client, conn, conv)


def test_malformed_run_id_is_a_run_failure(client, conn, hermes: FakeHermes) -> None:
    conv = start(client)
    hermes.accepted_run_id = {"id": "run_1"}
    send(client, conv, "hola")

    app = assert_one_incident(client, conn, conv)
    [error] = [e for e in repo.list_events(conn, app["id"]) if e["kind"] == "run_error"]
    assert error["data"]["run_id"] is None

    hermes.accepted_run_id = None
    assert_next_message_works(client, conn, conv)


def test_exception_while_sending_the_reply(
    client, conn, channel: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = start(client)
    monkeypatch.setattr(
        channel.sender, "send", fail_once(channel.sender.send, OSError("adapter down"))
    )
    send(client, conv, "hola")

    assert_one_incident(client, conn, conv)
    # The run's own usage row is marked failed, not doubled; its tokens stay.
    rows = conn.execute("SELECT status, input_tokens, run_id FROM usage").fetchall()
    assert [tuple(r) for r in rows] == [("error", 120, "run_1")]
    assert_next_message_works(client, conn, conv)


def test_exception_while_storing_the_run(client, conn, monkeypatch: pytest.MonkeyPatch) -> None:
    conv = start(client)
    monkeypatch.setattr(
        router_module,
        "record_usage",
        fail_once(router_module.record_usage, RuntimeError("disk I/O")),
    )
    send(client, conv, "hola")

    assert_one_incident(client, conn, conv)
    assert_next_message_works(client, conn, conv)


def test_exception_after_the_reply_went_out_escalates_without_a_fallback(
    client, conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = start(client)
    monkeypatch.setattr(
        repo,
        "link_knockout_message",
        fail_once(repo.link_knockout_message, RuntimeError("boom")),
    )
    send(client, conv, "hola")

    # The Candidate already has the agent's reply: no "dame un momento" after it.
    assert [m["text"] for m in outbound(client, conv)] == [first_message("Recibido (IA): hola")]
    app = app_for(conn, conv)
    assert len(escalations(conn, app["id"])) == 1
    rows = conn.execute("SELECT status FROM usage").fetchall()
    assert [r[0] for r in rows] == ["error"]


def test_repeat_failures_and_retries_open_one_escalation(
    client, conn, channel: Channel, clock, hermes: FakeHermes
) -> None:
    conv = start(client)
    hermes.crash = RuntimeError("down")
    send(client, conv, "hola", message_id="m1")
    send(client, conv, "hola", message_id="m1")  # a retry of the same message: no new run
    assert fallbacks(client, conv) == 1
    send(client, conv, "sigo aquí", message_id="m2")
    assert fallbacks(client, conv) == 1  # one holding message per outage (ADR 0073)
    assert run_retries(channel, clock) == 2  # m1's 3rd and 4th attempts; m2 waits behind it
    assert len(escalations(conn, app_for(conn, conv)["id"])) == 1
    assert fallbacks(client, conv) == 1


# --------------------------------------------------------------------------- the handler itself


def test_failure_handler_survives_its_own_steps_failing(
    client, conn, channel: Channel, clock, hermes: FakeHermes, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = start(client)
    hermes.crash = RuntimeError("down")

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("broken")

    # The ledger and the Escalation both fail: the fallback still goes out.
    monkeypatch.setattr(router_module, "record_usage", broken)
    monkeypatch.setattr(repo, "open_queue_item", broken)
    send(client, conv, "hola")
    assert fallbacks(client, conv) == 1
    monkeypatch.undo()

    # The fallback fails too: run_turn still returns, and the Escalation opens once the
    # retries are spent.
    hermes.crash = RuntimeError("down")
    monkeypatch.setattr(channel.sender, "send", broken)
    send(client, conv, "otra vez")
    run_retries(channel, clock)
    assert len(escalations(conn, app_for(conn, conv)["id"])) == 1


# --------------------------------------------------------------------------- unchanged rules


def take_over(conn, application_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO users (id, client_id, email, name, role, password_hash,"
        " created_at) VALUES ('u1', 'grupo_sazon', 'r@example.com', 'R', 'recruiter', 'x',"
        " '2026-10-09')"
    )
    conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'u1' WHERE id = ?",
        (application_id,),
    )


def test_system_turn_failure_escalates_without_a_reply(
    client, conn, channel: Channel, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    app = app_for(conn, conv)
    conn.execute(
        "UPDATE applications SET stage = 'approved', approved_at = ? WHERE id = ?",
        (iso(NOW), app["id"]),
    )
    before = outbound(client, conv)
    hermes.crash = RuntimeError("down")

    action = channel.handle_event(app["id"], "approved")

    assert action.kind == "run" and action.message is None
    assert outbound(client, conv) == before
    assert len(escalations(conn, app["id"])) == 1
    [error] = [e for e in repo.list_events(conn, app["id"]) if e["kind"] == "run_error"]
    assert error["data"]["system_turn"] is True


def test_takeover_during_a_failing_run_sends_nothing(
    client, conn, channel: Channel, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    app = app_for(conn, conv)
    before = outbound(client, conv)

    def run_then_crash(**kwargs: Any) -> Any:
        # A recruiter takes over while the run is in flight (the run is on another thread).
        with channel.connection() as other:
            take_over(other, app["id"])
        raise RuntimeError("down")

    monkeypatch.setattr(channel.router.run_client, "run", run_then_crash)
    send(client, conv, "¿sigues ahí?")

    assert outbound(client, conv) == before
    # The person answers now: the owed turn is dropped, nothing escalates (ADR 0073).
    assert len(escalations(conn, app["id"])) == 0
    assert repo.owed_turns_of(conn, app["id"]) == []
