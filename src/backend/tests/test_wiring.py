"""The integrator's wiring (``criba/wiring.py``, ``criba/app.py``): one clock, one booking
link, the summary on finish, Approval through the scheduler, and the database at startup.

The whole app runs in-process with its lifespan; Hermes is a fake run client, the summary
is the deterministic client. No model is called.
"""

from __future__ import annotations

import collections.abc
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from criba.api.booking import ToolsBookingService
from criba.api.clock import SimClock
from criba.app import create_app
from criba.channel.copy import qualified_close
from criba.channel.run_client import RunResult
from criba.db import connect, migrate, repo
from criba.db.seed import seed, seed_users
from criba.screening.clock import FakeClock
from criba.summary import DeterministicSummaryClient
from criba.tools import http as tools_http
from criba.tools import tap_answer
from criba.tools.booking import chat_invite_attachments
from criba.tools.choices import booking_link_of
from criba.wiring import (
    ChannelCandidateSender,
    Runtime,
    build_runtime,
    jobs_run,
    prepare_database,
    reopened_by_answer,
    summary_trigger,
)

# Friday 2026-10-09 10:00 UTC: 12:00 in Madrid, outside quiet hours.
NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
TOOLS_SECRET = "test-tools-secret-0123456789"
PASSWORD = "test-seed-password"
GOOD_ANSWERS = [
    ("full_name", "Ana García"),
    ("zone", ["zone_es_madrid_chamberi"]),
    ("driver_license", "yes"),
    ("availability", ["full_time"]),
    ("preferred_schedule", ["morning"]),
    ("experience_years", 2),
    ("experience_platforms", ["glovo"]),
    ("start_date", "2026-10-12"),
]


@dataclass
class FakeRuns:
    """The Hermes run client: records each turn, answers with a fixed line."""

    profile: str = "criba-recruiter"
    inputs: list[tuple[str, str]] = field(default_factory=list)
    ran: threading.Event = field(default_factory=threading.Event)

    def run(self, *, session_id: str, input: str, idempotency_key: str) -> RunResult:
        self.inputs.append((session_id, input))
        self.ran.set()
        return RunResult(
            run_id=f"run_{len(self.inputs)}",
            status="completed",
            output="De acuerdo.",
            usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            runtime={"provider": "stub", "model": "stub-model"},
        )

    def stream(
        self, *, session_id: str, input: str, idempotency_key: str
    ) -> collections.abc.Generator[str, None, RunResult]:
        raise AssertionError("typed turns never stream")


@dataclass
class Stack:
    client: TestClient
    runtime: Runtime
    runs: FakeRuns
    db_path: Path

    def conn(self):
        return connect(self.db_path)

    def tool(self, app_id: str, name: str, **args: Any) -> dict[str, Any]:
        response = self.client.post(
            f"/internal/tools/{name}",
            json={"tool": name, "args": args, "session_id": app_id},
            headers={"Authorization": f"Bearer {TOOLS_SECRET}"},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def login(self) -> None:
        response = self.client.post(
            "/api/auth/login", json={"email": "operator@criba.example", "password": PASSWORD}
        )
        assert response.status_code == 200, response.text

    def new_application(self) -> str:
        conn = self.conn()
        try:
            now = self.runtime.clock.now()
            candidate, _ = repo.get_or_create_candidate(
                conn,
                client_id="grupo_sazon",
                channel="webchat",
                external_id=f"wc_{time.monotonic_ns()}",
                country="ES",
                language="es-ES",
                now=now,
            )
            return repo.start_application(conn, candidate["id"], "webchat", now=now)["id"]
        finally:
            conn.close()

    def qualify(self, app_id: str) -> None:
        for name, value in GOOD_ANSWERS:
            body = self.tool(app_id, "record_answer", field=name, value=value)
            assert body["ok"] and body["value"]["status"] == "accepted", body
        assert self.tool(app_id, "finish_screening")["value"] == {
            "stage": "qualified",
            "message": qualified_close("es-ES", "ES", "Ana"),
        }

    def approve(self, app_id: str) -> None:
        conn = self.conn()
        try:
            repo.mark_approved(conn, app_id, "usr_operator", now=self.runtime.clock.now())
        finally:
            conn.close()

    def application(self, app_id: str) -> dict[str, Any]:
        conn = self.conn()
        try:
            return repo.require_application(conn, app_id)
        finally:
            conn.close()


def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **extra: str) -> dict[str, str]:
    files = {
        "CRIBA_TOOLS_SECRET_FILE": ("tools.txt", TOOLS_SECRET),
        "CRIBA_BOOKING_SECRET_FILE": ("booking.txt", "test-booking-secret-0123456789"),
        "CRIBA_HERMES_API_KEY_FILE": ("hermes.txt", "test-hermes-key"),
        "CRIBA_SEED_PASSWORD_FILE": ("seed.txt", PASSWORD),
    }
    values = {"CRIBA_DB_PATH": str(tmp_path / "criba.db"), "CRIBA_ENV": "test", **extra}
    for name, (filename, content) in files.items():
        path = tmp_path / filename
        path.write_text(content + "\n", encoding="utf-8")
        values[name] = str(path)
    monkeypatch.delenv("CRIBA_SEED", raising=False)
    monkeypatch.delenv("CRIBA_PUBLIC_BASE_URL", raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


def _seed(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        migrate(conn)
        seed(conn, slots_from=date(2026, 10, 12), days=14, now=NOW)
        seed_users(conn, PASSWORD, now=NOW)
    finally:
        conn.close()


@pytest.fixture
def make_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Stack]]:
    clients: list[TestClient] = []

    def make(clock: Any = None, **env_extra: str) -> Stack:
        env = _env(tmp_path, monkeypatch, **env_extra)
        _seed(tmp_path / "criba.db")
        runs = FakeRuns()
        holder: list[Runtime] = []

        def factory() -> Runtime:
            runtime = build_runtime(
                env,
                run_client=runs,
                clock=clock or FakeClock(NOW),
                summary_client=DeterministicSummaryClient(),
            )
            holder.append(runtime)
            return runtime

        client = TestClient(create_app(factory))
        client.__enter__()
        clients.append(client)
        return Stack(client, holder[0], runs, tmp_path / "criba.db")

    yield make
    for client in clients:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# One clock
# ---------------------------------------------------------------------------


def test_sim_mode_shares_one_sim_clock_everywhere(make_stack):
    stack = make_stack(clock=None, CRIBA_ENV="sim", CRIBA_HERMES_PROFILE="criba-recruiter-sim")
    # Built from the environment alone, as in Compose: sim mode means a SimClock.
    sim = build_runtime(os.environ, run_client=FakeRuns(), prepare_db=False)
    assert isinstance(sim.clock, SimClock)
    for user in (
        sim.channel.clock,
        sim.channel.router.clock,
        sim.channel.sender.clock,
        sim.scheduler._clock,
        sim.scheduler.deferred._clock,
        sim.summary._clock,
        sim.api_context.clock,
    ):
        assert user is sim.clock
    # The running app hands its runtime's clock to every router.
    app = stack.client.app
    shared = stack.runtime.clock
    assert app.dependency_overrides[tools_http.get_clock]() is shared  # type: ignore[attr-defined]
    assert app.state.api_context.clock is shared  # type: ignore[attr-defined]
    assert app.state.criba_channel.clock is shared  # type: ignore[attr-defined]


def test_sim_clock_endpoint_advances_the_shared_clock_and_runs_due_jobs(make_stack):
    clock = SimClock()
    stack = make_stack(clock=clock, CRIBA_ENV="sim", CRIBA_HERMES_PROFILE="criba-recruiter-sim")
    before = stack.runtime.clock.now()
    response = stack.client.post("/api/sim/clock/advance", json={"seconds": 3600})
    assert response.status_code == 200, response.text
    assert response.json()["offset_seconds"] == 3600
    assert isinstance(response.json()["due_jobs_run"], int)
    assert stack.runtime.clock.now() - before >= timedelta(seconds=3600)


# ---------------------------------------------------------------------------
# One booking link
# ---------------------------------------------------------------------------


def test_a_link_minted_by_the_tool_books_on_the_public_page(make_stack):
    stack = make_stack()
    app_id = stack.new_application()
    stack.qualify(app_id)
    stack.approve(app_id)
    offer = stack.tool(app_id, "offer_interview_slots")
    assert offer["ok"], offer
    url = offer["value"]["booking_url"]
    token = url.rsplit("/book/", 1)[1]

    page = stack.client.get(f"/api/public/booking/{token}")
    assert page.status_code == 200, page.text
    slot_id = page.json()["slots"][0]["slot_id"]
    booked = stack.client.post(f"/api/public/booking/{token}", json={"slot_id": slot_id})
    assert booked.status_code == 201, booked.text
    assert stack.application(app_id)["stage"] == "interview_booked"
    invite = stack.client.get(f"/api/public/booking/{token}/invite.ics")
    assert invite.status_code == 200
    assert "BEGIN:VCALENDAR" in invite.text


def test_the_other_time_link_books_on_the_public_page(make_stack):
    """ADR 0064: a tap on "Otro horario" is answered with a link the booking page accepts, and
    the slot offer it left behind still books."""
    stack = make_stack()
    app_id = stack.new_application()
    stack.qualify(app_id)
    stack.approve(app_id)
    offered = stack.tool(app_id, "offer_interview_slots")["value"]["slots"]
    conn = stack.conn()
    try:
        text = tap_answer(conn, app_id, "other", now=stack.runtime.clock.now())
    finally:
        conn.close()
    link = booking_link_of(text)
    assert link is not None and link[0] in link[1]
    token = link[0].rsplit("/book/", 1)[1]
    page = stack.client.get(f"/api/public/booking/{token}")
    assert page.status_code == 200, page.text
    assert page.json()["slots"]
    conn = stack.conn()
    try:
        booked = tap_answer(conn, app_id, offered[0]["slot_ref"], now=stack.runtime.clock.now())
    finally:
        conn.close()
    assert '"stage":"interview_booked"' in booked


def test_the_chat_invite_attachment_downloads_the_ics(make_stack):
    """The attachment the web chat puts on a booking confirmation is served by the public
    invite.ics endpoint: one signing key for the tool, the channel and the API."""
    stack = make_stack()
    app_id = stack.new_application()
    stack.qualify(app_id)
    stack.approve(app_id)
    ref = stack.tool(app_id, "offer_interview_slots")["value"]["slots"][0]["slot_ref"]
    assert stack.tool(app_id, "book_interview", slot_ref=ref)["value"]["ics_sent"] is True
    sent = stack.runtime.channel.send(app_id, "¡Listo!")
    assert sent.message is not None
    conn = stack.conn()
    try:
        (invite,) = chat_invite_attachments(conn, sent.message, now=stack.runtime.clock.now())
    finally:
        conn.close()
    response = stack.client.get(invite["url"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in response.text


def test_an_expired_tool_link_is_410(make_stack):
    clock = FakeClock(NOW)
    stack = make_stack(clock=clock)
    app_id = stack.new_application()
    stack.qualify(app_id)
    stack.approve(app_id)
    token = stack.tool(app_id, "offer_interview_slots")["value"]["booking_url"].rsplit("/", 1)[1]
    clock.advance(timedelta(days=7, seconds=1))
    response = stack.client.get(f"/api/public/booking/{token}")
    assert response.status_code == 410
    assert response.json()["error"] == "link_expired"


def test_a_tampered_tool_link_is_404(make_stack):
    stack = make_stack()
    app_id = stack.new_application()
    stack.qualify(app_id)
    stack.approve(app_id)
    token = stack.tool(app_id, "offer_interview_slots")["value"]["booking_url"].rsplit("/", 1)[1]
    response = stack.client.get(f"/api/public/booking/{token[:-2]}xx")
    assert response.status_code == 404


def test_the_booking_service_refuses_a_slot_outside_the_applications_locations(make_stack):
    from criba.api.services import SlotNotCovering

    stack = make_stack()
    app_id = stack.new_application()
    stack.qualify(app_id)
    stack.approve(app_id)
    conn = stack.conn()
    try:
        foreign = conn.execute(
            "SELECT s.id FROM interview_slots s JOIN locations l ON l.id = s.location_id"
            " WHERE l.country = 'MX' LIMIT 1"
        ).fetchone()["id"]
        with pytest.raises(SlotNotCovering):
            ToolsBookingService().book(
                conn, application_id=app_id, slot_id=foreign, via="booking_page", now=NOW
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Summary when Screening ends
# ---------------------------------------------------------------------------


def _wait(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_finish_screening_writes_the_summary(make_stack):
    stack = make_stack()
    app_id = stack.new_application()
    stack.qualify(app_id)
    assert _wait(lambda: stack.application(app_id)["summary_structured"] is not None)
    app = stack.application(app_id)
    assert app["summary_prose"]


def test_a_knockout_writes_the_summary(make_stack):
    stack = make_stack()
    app_id = stack.new_application()
    stack.tool(app_id, "record_answer", field="full_name", value="Ana García")
    stack.tool(app_id, "record_answer", field="zone", value=["zone_es_madrid_chamberi"])
    body = stack.tool(app_id, "record_answer", field="driver_license", value="no")
    assert body["value"]["status"] == "knockout", body
    assert _wait(lambda: stack.application(app_id)["summary_structured"] is not None)


def test_a_valid_answer_after_a_knockout_rewrites_the_summary_as_reopened(make_stack):
    """ADR 0033: the Reopen through record_answer rewrites the summary, once."""
    stack = make_stack()
    app_id = stack.new_application()
    stack.tool(app_id, "record_answer", field="full_name", value="Ana García")
    stack.tool(app_id, "record_answer", field="zone", value=["zone_es_madrid_chamberi"])
    stack.tool(app_id, "record_answer", field="driver_license", value="no")
    assert _wait(lambda: stack.application(app_id)["summary_structured"] is not None)
    assert stack.application(app_id)["summary_structured"]["outcome"] == "knockout"

    body = stack.tool(app_id, "record_answer", field="driver_license", value="yes")
    assert body["value"]["status"] == "accepted", body
    summary = stack.application(app_id)["summary_structured"]
    assert summary["outcome"] is None and summary["reopened_count"] == 1
    conn = stack.conn()
    try:
        assert reopened_by_answer(conn, app_id)
    finally:
        conn.close()

    stack.tool(app_id, "record_answer", field="availability", value=["full_time"])
    conn = stack.conn()
    try:
        assert not reopened_by_answer(conn, app_id)
    finally:
        conn.close()


def test_an_abandoned_reopen_is_not_a_reopen_by_answer(make_stack):
    stack = make_stack()
    app_id = stack.new_application()
    conn = stack.conn()
    try:
        now = stack.runtime.clock.now()
        repo.close_application(conn, app_id, "abandoned", actor="system", now=now)
        repo.reopen_application(conn, app_id, now=now)
    finally:
        conn.close()
    stack.tool(app_id, "record_answer", field="full_name", value="Ana García")
    conn = stack.conn()
    try:
        assert not reopened_by_answer(conn, app_id)
    finally:
        conn.close()


def test_a_chat_message_reopening_an_abandoned_application_rewrites_the_summary(make_stack):
    stack = make_stack()
    app_id = stack.new_application()
    conn = stack.conn()
    try:
        app = repo.require_application(conn, app_id)
        conversation = conn.execute(
            "SELECT external_id FROM candidate_identities WHERE candidate_id = ?",
            (app["candidate_id"],),
        ).fetchone()["external_id"]
        repo.close_application(conn, app_id, "abandoned", actor="system", now=NOW)
    finally:
        conn.close()
    assert stack.runtime.channel.router.on_reopened == stack.runtime.reopened

    response = stack.client.post(
        f"/api/chat/conversations/{conversation}/messages",
        json={"message_id": "m-reopen", "text": "perdón, sigo aquí"},
    )
    assert response.status_code == 202, response.text

    def rewritten() -> bool:
        summary = stack.application(app_id)["summary_structured"]
        return summary is not None and summary["reopened_count"] == 1

    assert _wait(rewritten)
    assert stack.application(app_id)["status"] == "open"


@pytest.mark.parametrize(
    ("tool", "value", "expected"),
    [
        ("finish_screening", {"stage": "qualified"}, "finished"),
        ("record_answer", {"status": "knockout"}, "finished"),
        ("record_answer", {"status": "accepted"}, None),
        ("record_backout", {"kind": "withdrawn", "status": "closed"}, "finished"),
        ("record_backout", {"kind": "opted_out", "status": "open"}, None),
        ("get_application", {}, None),
    ],
)
def test_summary_trigger(tool, value, expected):
    assert summary_trigger(tool, {"ok": True, "value": value}) == expected


# ---------------------------------------------------------------------------
# Approval goes through the scheduler and starts the system turn
# ---------------------------------------------------------------------------


def test_approval_through_the_api_runs_the_system_turn(make_stack):
    stack = make_stack()
    app_id = stack.new_application()
    stack.qualify(app_id)
    conn = stack.conn()
    try:
        item = conn.execute(
            "SELECT id FROM queue_items WHERE application_id = ? AND kind = 'approval'",
            (app_id,),
        ).fetchone()
    finally:
        conn.close()
    assert item is not None
    stack.login()
    response = stack.client.post(
        f"/api/queue/approvals/{item['id']}", json={"decision": "approved"}
    )
    assert response.status_code == 200, response.text
    assert stack.runs.ran.wait(5), "no agent run after the Approval"
    assert stack.runs.inputs[-1] == (app_id, "[system] approved")
    conn = stack.conn()
    try:
        kinds = [e["kind"] for e in repo.list_events(conn, app_id)]
        bodies = [m["body"] for m in repo.list_messages(conn, app_id)]
    finally:
        conn.close()
    assert "system_turn" in kinds
    assert "[system] approved" not in bodies


# ---------------------------------------------------------------------------
# Sender, scheduler report, database
# ---------------------------------------------------------------------------


def test_takeover_notice_and_text_go_through_the_channel_sender(make_stack):
    stack = make_stack()
    app_id = stack.new_application()
    sender = stack.runtime.api_context.services.sender
    assert isinstance(sender, ChannelCandidateSender)
    conn = stack.conn()
    try:
        notice = sender.send_notice(conn, application_id=app_id, notice="takeover_joined", now=NOW)
        text = sender.send_human_text(
            conn, application_id=app_id, user_id="usr_operator", text="Hola", now=NOW
        )
    finally:
        conn.close()
    assert notice is not None and notice["sender"] == "system"
    assert text["sender"] == "human" and text["body"] == "Hola"


def _set_channel(stack: Stack, app_id: str, channel: str, last_inbound_at: str | None) -> None:
    conn = stack.conn()
    try:
        conn.execute(
            "UPDATE applications SET channel = ?, last_inbound_at = ? WHERE id = ?",
            (channel, last_inbound_at, app_id),
        )
    finally:
        conn.close()


def test_team_text_and_joined_notice_ignore_quiet_hours(make_stack):
    """A person's Takeover reply is not proactive, nor is the notice that goes with it."""
    quiet = datetime(2026, 10, 9, 22, 30, tzinfo=UTC)  # 00:30 in Madrid
    stack = make_stack(clock=FakeClock(quiet))
    app_id = stack.new_application()
    stack.login()
    started = stack.client.post(f"/api/applications/{app_id}/takeover")
    assert started.status_code == 200, started.text
    sent = stack.client.post(f"/api/applications/{app_id}/takeover/messages", json={"text": "Hola"})
    assert sent.status_code == 201, sent.text
    conn = stack.conn()
    try:
        senders = [m["sender"] for m in repo.list_messages(conn, app_id)]
    finally:
        conn.close()
    assert senders == ["system", "human"]


def test_team_text_outside_the_whatsapp_window_is_409(make_stack):
    stack = make_stack()
    app_id = stack.new_application()
    _set_channel(stack, app_id, "whatsapp", (NOW - timedelta(hours=25)).isoformat())
    stack.login()
    started = stack.client.post(f"/api/applications/{app_id}/takeover")
    assert started.status_code == 200, started.text  # the free-text notice is dropped
    response = stack.client.post(
        f"/api/applications/{app_id}/takeover/messages", json={"text": "Hola"}
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "outside_whatsapp_window"
    conn = stack.conn()
    try:
        assert repo.list_messages(conn, app_id) == []
    finally:
        conn.close()

    # Inside the window the same text goes out.
    _set_channel(stack, app_id, "whatsapp", (NOW - timedelta(hours=1)).isoformat())
    response = stack.client.post(
        f"/api/applications/{app_id}/takeover/messages", json={"text": "Hola"}
    )
    assert response.status_code == 201, response.text


def test_jobs_run_counts_work_done():
    from criba.scheduler import RunReport

    report = RunReport()
    assert jobs_run(report) == 0
    report.stalled.nudges_sent = 2
    report.deferred.dispatched = 1
    report.stalled.errors = 5
    assert jobs_run(report) == 3


def test_prepare_database_migrates_and_seeds_on_request(tmp_path):
    db = tmp_path / "fresh.db"
    password = tmp_path / "pw.txt"
    password.write_text("pw-0123456789\n", encoding="utf-8")
    prepare_database({"CRIBA_DB_PATH": str(db)})
    conn = connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM locations").fetchone()[0] == 0
    finally:
        conn.close()
    env = {"CRIBA_DB_PATH": str(db), "CRIBA_SEED": "1", "CRIBA_SEED_PASSWORD_FILE": str(password)}
    prepare_database(env)
    prepare_database(env)  # idempotent
    conn = connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM locations").fetchone()[0] > 0
        users = conn.execute("SELECT count(*) FROM users").fetchone()[0]
        assert users > 1
    finally:
        conn.close()


def test_key_check_is_built_only_in_production_with_a_key(tmp_path: Path) -> None:
    from criba.channel.outage import OutageAlarms
    from criba.wiring import key_check_from_env

    clock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    alarms = OutageAlarms(clock)
    key = tmp_path / "openrouter.txt"
    key.write_text("k\n", encoding="utf-8")
    env = {"CRIBA_OPENROUTER_API_KEY_FILE": str(key), "CRIBA_OPENROUTER_KEY_FLOOR_USD": "7.5"}
    built = key_check_from_env(env, alarms=alarms, clock=clock)
    assert built is not None and built.floor_usd == 7.5
    for off in (
        {**env, "CRIBA_ENV": "sim"},
        {**env, "CRIBA_ENV": "test"},
        {"CRIBA_OPENROUTER_API_KEY_FILE": str(tmp_path / "missing.txt")},
        {},
    ):
        assert key_check_from_env(off, alarms=alarms, clock=clock) is None
