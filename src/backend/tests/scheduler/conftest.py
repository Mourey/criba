"""Fixtures for the scheduler tests: a migrated, seeded database and fakes for the
two things W2-B provides (the sender and the router's dispatch).

T0 is 14:00 in Mexico City (20:00 UTC), chosen so the 1-hour, 20-hour and 48-hour
nudges all land outside quiet hours; quiet-hours tests move the clock explicitly.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from criba.db import connect, migrate, repo
from criba.db.seed import CLIENT_ID, seed, seed_users
from criba.scheduler import NudgeRequest, Scheduler
from criba.screening.clock import FakeClock
from criba.summary import DeterministicSummaryClient, SummaryWriter

T0 = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
PUEBLA_CENTRO = "zone_mx_puebla_centro"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests never call a model: any real HTTP request fails the test."""

    def refuse(self, request):
        raise AssertionError(f"unexpected network call to {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "criba.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator:
    connection = connect(db_path)
    migrate(connection)
    seed(connection, slots_from=date(2026, 10, 12), days=7, now=T0)
    seed_users(connection, "synthetic-test-password", now=T0)
    yield connection
    connection.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


class FakeSender:
    """Stands in for W2-B's sender: records requests, and can decline or fail."""

    def __init__(self) -> None:
        self.requests: list[NudgeRequest] = []
        self.accept = True
        self.fail_for: set[str] = set()

    def __call__(self, conn, request: NudgeRequest) -> bool:
        if request.application_id in self.fail_for:
            raise RuntimeError("sender is down")
        if not self.accept:
            return False
        self.requests.append(request)
        return True


class FakeDispatch:
    """Stands in for W2-B's router dispatch of an action."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failures_left = 0

    def __call__(self, conn, application_id: str, action: str) -> None:
        if self.failures_left:
            self.failures_left -= 1
            raise RuntimeError("router failed")
        self.calls.append((application_id, action))


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def dispatch() -> FakeDispatch:
    return FakeDispatch()


@pytest.fixture
def scheduler(db_path: Path, clock: FakeClock, sender: FakeSender, dispatch: FakeDispatch):
    return Scheduler(
        connect=lambda: connect(db_path),
        clock=clock,
        send_nudge=sender,
        dispatch=dispatch,
        summary=SummaryWriter(DeterministicSummaryClient(), clock, environment="test"),
    )


@pytest.fixture
def start_app():
    """Start an Application for a Candidate who has just written; optionally at `stage`."""

    def start(
        conn,
        *,
        external_id: str = "conv-1",
        phone: str | None = "+525512345678",
        language: str = "es-MX",
        at: datetime = T0,
        stage: str | None = None,
    ) -> dict:
        candidate, _ = repo.get_or_create_candidate(
            conn,
            client_id=CLIENT_ID,
            channel="webchat",
            external_id=external_id,
            phone=phone,
            language=language,
            now=at,
        )
        app, _ = repo.resolve_application(conn, candidate["id"], "webchat", now=at)
        _say(conn, app, at=at)
        if stage:
            repo.set_stage(conn, app["id"], stage, now=at)
        return repo.require_application(conn, app["id"])

    return start


def _say(conn, app: dict, *, at: datetime, body: str = "hola") -> None:
    repo.add_message(
        conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body=body,
        now=at,
    )


@pytest.fixture
def say():
    """The Candidate writes: `say(conn, app, at=...)`."""
    return _say


@pytest.fixture
def make_qualified(start_app):
    def make(conn, *, at: datetime = T0, **kwargs) -> dict:
        app = start_app(conn, at=at, stage="confirm", **kwargs)
        repo.record_answer(conn, app["id"], "zone", PUEBLA_CENTRO, now=at)
        return repo.mark_qualified(conn, app["id"], now=at)

    return make


@pytest.fixture
def kinds():
    def event_kinds(conn, application_id: str) -> list[str]:
        return [e["kind"] for e in repo.list_events(conn, application_id)]

    return event_kinds
