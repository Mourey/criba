"""Fixtures for the summary tests: a migrated, seeded database, a fake clock, and a
guard that fails the test if anything tries a real HTTP request (no model calls)."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from criba.db import connect, migrate, repo
from criba.db.seed import CLIENT_ID, seed, seed_users
from criba.screening.clock import FakeClock
from criba.summary import ProseRequest, ProseResult

T0 = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
PUEBLA_CENTRO = "zone_mx_puebla_centro"
OPENAPI = Path(__file__).resolve().parents[4] / "contracts" / "api.openapi.yaml"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self, request):
        raise AssertionError(f"unexpected network call to {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator:
    connection = connect(tmp_path / "criba.db")
    migrate(connection)
    seed(connection, slots_from=date(2026, 10, 12), days=7, now=T0)
    seed_users(connection, "synthetic-test-password", now=T0)
    yield connection
    connection.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    return yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))


class RecordingClient:
    """A stub Sonnet: returns canned prose and remembers what it was asked."""

    model = "stub-sonnet"

    def __init__(self, text: str = "Candidate looks fine. Nothing odd.") -> None:
        self.text = text
        self.requests: list[ProseRequest] = []
        self.error: Exception | None = None

    def write_prose(self, request: ProseRequest) -> ProseResult:
        self.requests.append(request)
        if self.error:
            raise self.error
        return ProseResult(
            text=self.text, model=self.model, input_tokens=900, output_tokens=80, latency_ms=1200
        )


@pytest.fixture
def client() -> RecordingClient:
    return RecordingClient()


ANSWERS = [
    ("full_name", "Luis Hernández"),
    ("zone", PUEBLA_CENTRO),
    ("driver_license", True),
    ("availability", ["full_time"]),
    ("preferred_schedule", ["morning", "afternoon"]),
    ("experience_years", 2),
    ("experience_platforms", ["rappi", "didi_food"]),
    ("start_date", "2026-10-14"),
]


@pytest.fixture
def new_application():
    def make(conn, *, external_id: str = "conv-1", at: datetime = T0) -> dict:
        candidate, _ = repo.get_or_create_candidate(
            conn,
            client_id=CLIENT_ID,
            channel="webchat",
            external_id=external_id,
            phone="+525512345678",
            language="es-MX",
            now=at,
        )
        app, _ = repo.resolve_application(conn, candidate["id"], "webchat", now=at)
        return app

    return make


@pytest.fixture
def qualified_application(new_application):
    """Luis, fully screened: every answer recorded, stages stepped through, then Qualified."""

    def make(conn, *, at: datetime = T0) -> dict:
        app = new_application(conn, at=at)
        repo.add_message(
            conn,
            candidate_id=app["candidate_id"],
            application_id=app["id"],
            channel="webchat",
            direction="inbound",
            sender="candidate",
            body="Hola, busco trabajo de repartidor",
            now=at,
        )
        for field, value in ANSWERS:
            repo.record_answer(conn, app["id"], field, value, now=at)
        repo.set_stage(conn, app["id"], "confirm", now=at)
        return repo.mark_qualified(conn, app["id"], now=at)

    return make
