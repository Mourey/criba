"""An OpenRouter outage is classified and raises one system alarm (ADR 0078).

The run failure is classified from the error text Hermes returns ("HTTP <status>: <message>",
`agent/api_error_summary.py:71-73,196` in v0.21.6): Hermes passes no structured status. The
alarm opens on classified failures (3 Applications in 5 minutes, or one for credits and a
rejected key), clears when a run succeeds, and a scheduled check of the key's `limit_remaining`
raises the same alarm. Candidates keep the holding message and the per-Application
Escalation; the alarm is in addition. No real model and no real network: fake Hermes and a
mock transport."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from criba.channel.outage import (
    DEFAULT_FLOOR_USD,
    OPENROUTER_KEY_URL,
    KeyCheck,
    OutageAlarms,
    classify,
)
from criba.channel.run_client import RunFailed
from criba.channel.scheduling import build_scheduler
from criba.channel.sender import FALLBACK_TEXT
from criba.channel.service import Channel
from criba.db import repo
from criba.screening.clock import FakeClock

from .conftest import NOW, FakeHermes, candidate_for, run_retries, start

FALLBACKS = set(FALLBACK_TEXT.values())
SECRET = "sk-or-v1-do-not-log-this-key"


# ------------------------------------------------------------------ classify


@pytest.mark.parametrize(
    ("message", "final", "cause"),
    [
        ("run failed: HTTP 402: Insufficient credits", True, "credits"),
        ("run.failed: HTTP 402: This request requires more credits", True, "credits"),
        ("run failed: HTTP 429: Rate limit exceeded", True, "rate_limited"),
        ("run failed: HTTP 401: No auth credentials found", True, "key_rejected"),
        ("run failed: HTTP 503: No available providers", True, "model_unavailable"),
        ("run failed: HTTP 502: Bad gateway", True, "model_unavailable"),
        ("run failed: HTTP 404: No endpoints found for x/y", True, "model_unavailable"),
        (
            "run failed: Hermes can't reach the model provider. You may be offline.",
            True,
            "model_unavailable",
        ),
        ("run failed: HTTP 400: Bad request", True, "other"),
        ("run failed: model error", True, "other"),
        # Hermes refusing the POST (saturation) is not OpenRouter's answer.
        ("run not accepted: HTTP 429", True, "other"),
        ("hermes unreachable: connection refused", False, "other"),
        ("run timeout: stream", False, "other"),
    ],
)
def test_classify_reads_the_error_text_hermes_returns(
    message: str, final: bool, cause: str
) -> None:
    assert classify(RunFailed(message, final=final)) == cause


# ------------------------------------------------------------------ the alarm


@pytest.fixture
def alarms(clock: FakeClock) -> OutageAlarms:
    return OutageAlarms(clock)


def test_credits_alarm_opens_on_the_first_failure(alarms: OutageAlarms) -> None:
    alarms.record_failure("credits", "app-1")
    [alarm] = alarms.open()
    assert (alarm.cause, alarm.sources, alarm.since) == ("credits", ("runs",), NOW)


def test_rate_limit_needs_three_applications_in_five_minutes(
    alarms: OutageAlarms, clock: FakeClock
) -> None:
    for _ in range(4):
        alarms.record_failure("rate_limited", "app-1")  # one Application failing again
    alarms.record_failure("rate_limited", "app-2")
    assert alarms.open() == []
    alarms.record_failure("rate_limited", "app-3")
    assert [a.cause for a in alarms.open()] == ["rate_limited"]


def test_failures_older_than_the_window_do_not_count(
    alarms: OutageAlarms, clock: FakeClock
) -> None:
    alarms.record_failure("model_unavailable", "app-1")
    alarms.record_failure("model_unavailable", "app-2")
    clock.advance(minutes=6)
    alarms.record_failure("model_unavailable", "app-3")
    assert alarms.open() == []


def test_other_failures_never_raise_the_alarm(alarms: OutageAlarms) -> None:
    for n in range(5):
        alarms.record_failure("other", f"app-{n}")
    assert alarms.open() == []


def test_one_alarm_however_many_applications_fail(alarms: OutageAlarms) -> None:
    for n in range(6):
        alarms.record_failure("credits", f"app-{n}")
    assert len(alarms.open()) == 1


def test_a_successful_run_clears_the_run_alarm(alarms: OutageAlarms) -> None:
    alarms.record_failure("credits", "app-1")
    alarms.run_succeeded()
    assert alarms.open() == []
    # The window is cleared too: two more rate-limit failures do not reopen on old ones.
    alarms.record_failure("rate_limited", "app-1")
    alarms.record_failure("rate_limited", "app-2")
    alarms.run_succeeded()
    alarms.record_failure("rate_limited", "app-3")
    assert alarms.open() == []


def test_the_key_alarm_survives_a_successful_run_and_shares_the_cause(
    alarms: OutageAlarms,
) -> None:
    alarms.set_key_state("credits")
    alarms.record_failure("credits", "app-1")
    [alarm] = alarms.open()
    assert alarm.sources == ("runs", "key")
    alarms.run_succeeded()
    [alarm] = alarms.open()
    assert alarm.sources == ("key",)
    alarms.set_key_state(None)
    assert alarms.open() == []


def test_raising_and_clearing_are_logged_once(
    alarms: OutageAlarms, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="criba.channel.outage"):
        alarms.record_failure("credits", "app-1")
        alarms.record_failure("credits", "app-2")
        alarms.run_succeeded()
        alarms.run_succeeded()
    messages = [r.getMessage() for r in caplog.records]
    assert [r.levelno for r in caplog.records] == [logging.ERROR, logging.INFO]
    assert "credits" in messages[0] and "credits" in messages[1]


# ------------------------------------------------------------------ the router


def send(client: TestClient, conv: str, text: str = "hola") -> None:
    resp = client.post(
        f"/api/chat/conversations/{conv}/messages",
        json={"message_id": str(uuid.uuid4()), "text": text},
    )
    assert resp.status_code == 202, resp.text


def open_escalations(conn: Any) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT reason, note FROM queue_items WHERE kind = 'escalation' AND status = 'open'"
        " ORDER BY created_at, id"
    ).fetchall()
    return [(r["reason"], r["note"]) for r in rows]


def test_an_outage_raises_one_alarm_and_the_candidates_keep_their_own_handling(
    client: TestClient, conn: Any, channel: Channel, clock: FakeClock, hermes: FakeHermes
) -> None:
    hermes.final_status = "failed"
    hermes.run_error = "HTTP 429: Rate limit exceeded"
    convs = [start(client) for _ in range(3)]
    for conv in convs:
        send(client, conv)
    run_retries(channel, clock)

    [alarm] = channel.router.alarms.open()
    assert (alarm.cause, alarm.sources) == ("rate_limited", ("runs",))
    # Each Candidate was told once and has the Escalation of ADR 0073, tagged with the cause.
    escalations = open_escalations(conn)
    assert len(escalations) == 3
    assert all(reason == "technical_error" for reason, _ in escalations)
    assert all("rate_limited" in note for _, note in escalations)
    for conv in convs:
        page = client.get(f"/api/chat/conversations/{conv}/messages").json()
        sent = [m["text"] for m in page["messages"] if m["direction"] == "outbound"]
        assert sum(t in FALLBACKS for t in sent) == 1
    causes = {
        json.loads(e["data"])["cause"]
        for e in conn.execute("SELECT data FROM events WHERE kind = 'run_error'").fetchall()
    }
    assert causes == {"rate_limited"}


def test_a_run_that_succeeds_clears_the_alarm(
    client: TestClient, channel: Channel, clock: FakeClock, hermes: FakeHermes
) -> None:
    hermes.final_status = "failed"
    hermes.run_error = "HTTP 402: Insufficient credits"
    conv = start(client)
    send(client, conv)
    assert [a.cause for a in channel.router.alarms.open()] == ["credits"]

    hermes.final_status = "completed"
    send(client, conv, "ya estoy aqui")
    assert channel.router.alarms.open() == []


def test_a_failure_that_is_not_an_outage_raises_nothing(
    client: TestClient, conn: Any, channel: Channel, hermes: FakeHermes
) -> None:
    hermes.final_status = "failed"
    conv = start(client)
    send(client, conv)
    assert channel.router.alarms.open() == []
    event = conn.execute("SELECT data FROM events WHERE kind = 'run_error'").fetchone()
    assert json.loads(event["data"])["cause"] == "other"
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    assert app is not None


# ------------------------------------------------------------------ the key check


def key_response(body: Any = None, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body if body is not None else {})


class FakeOpenRouter:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answer: httpx.Response | Exception = key_response(
            {"data": {"limit": 20, "limit_remaining": 18.5, "usage_daily": 0.4}}
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def openrouter() -> FakeOpenRouter:
    return FakeOpenRouter()


@pytest.fixture
def key_check(openrouter: FakeOpenRouter, alarms: OutageAlarms, clock: FakeClock) -> KeyCheck:
    return KeyCheck(key=SECRET, alarms=alarms, clock=clock, http=openrouter.client())


def test_a_healthy_key_raises_nothing_and_sends_the_key_as_a_bearer(
    key_check: KeyCheck, openrouter: FakeOpenRouter, alarms: OutageAlarms
) -> None:
    key_check.run_if_due()
    assert alarms.open() == []
    [request] = openrouter.requests
    assert str(request.url) == OPENROUTER_KEY_URL
    assert request.headers["authorization"] == f"Bearer {SECRET}"


def test_a_low_balance_raises_the_credits_alarm_and_a_top_up_clears_it(
    key_check: KeyCheck, openrouter: FakeOpenRouter, alarms: OutageAlarms, clock: FakeClock
) -> None:
    openrouter.answer = key_response({"data": {"limit_remaining": DEFAULT_FLOOR_USD - 0.01}})
    key_check.run_if_due()
    [alarm] = alarms.open()
    assert (alarm.cause, alarm.sources) == ("credits", ("key",))

    openrouter.answer = key_response({"data": {"limit_remaining": 50}})
    clock.advance(hours=1)
    key_check.run_if_due()
    assert alarms.open() == []


def test_a_key_without_a_limit_is_not_low(
    key_check: KeyCheck, openrouter: FakeOpenRouter, alarms: OutageAlarms
) -> None:
    openrouter.answer = key_response({"data": {"limit": None, "limit_remaining": None}})
    key_check.run_if_due()
    assert alarms.open() == []


def test_a_rejected_key_raises_the_key_rejected_alarm(
    key_check: KeyCheck, openrouter: FakeOpenRouter, alarms: OutageAlarms
) -> None:
    openrouter.answer = key_response({"error": {"message": "User not found."}}, status=401)
    key_check.run_if_due()
    assert [a.cause for a in alarms.open()] == ["key_rejected"]


@pytest.mark.parametrize(
    "answer",
    [httpx.ConnectError("down"), key_response(status=503), key_response({"data": "?"})],
)
def test_an_answer_that_says_nothing_about_the_key_keeps_the_state(
    key_check: KeyCheck,
    openrouter: FakeOpenRouter,
    alarms: OutageAlarms,
    clock: FakeClock,
    answer: httpx.Response | Exception,
) -> None:
    alarms.set_key_state("credits")
    openrouter.answer = answer
    key_check.run_if_due()
    assert [a.cause for a in alarms.open()] == ["credits"]


def test_the_check_runs_once_an_hour(
    key_check: KeyCheck, openrouter: FakeOpenRouter, clock: FakeClock
) -> None:
    key_check.run_if_due()
    clock.advance(minutes=59)
    key_check.run_if_due()
    assert len(openrouter.requests) == 1
    clock.advance(minutes=1)
    key_check.run_if_due()
    assert len(openrouter.requests) == 2


def test_the_key_is_never_logged(
    key_check: KeyCheck,
    openrouter: FakeOpenRouter,
    caplog: pytest.LogCaptureFixture,
    clock: FakeClock,
) -> None:
    with caplog.at_level(logging.DEBUG):
        openrouter.answer = httpx.ConnectError(f"cannot reach with {SECRET}")
        key_check.run_if_due()
        clock.advance(hours=1)
        openrouter.answer = key_response({"data": {"limit_remaining": 0.5}})
        key_check.run_if_due()
    assert SECRET not in caplog.text


def test_the_scheduler_runs_the_key_check_and_survives_its_failure(
    channel: Channel, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[int] = []

    def run_if_due() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    scheduler = build_scheduler(channel, key_check=run_if_due)
    scheduler.run_due_jobs()  # the failure is logged, the tick completes
    scheduler.run_due_jobs()
    assert len(calls) == 2
    assert "key check failed" in caplog.text
