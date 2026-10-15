"""Fixtures for the REST API tests: a seeded database, an app wired with fakes for the
other wave 2 lanes, logged-in clients, application builders and contract conformance.

Nothing here calls a model or Compose. Every password is generated per run.
"""

from __future__ import annotations

import secrets
import shutil
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from criba.api.booking import ToolsBookingService
from criba.api.context import ApiContext, ApiSettings
from criba.api.messages import StoredMessageSender
from criba.api.metrics import UsageComparison
from criba.api.routes import router
from criba.api.services import Services
from criba.db import repo
from criba.db.connection import connect, iso
from criba.db.migrator import migrate
from criba.db.seed import seed, seed_users
from criba.screening.clock import FakeClock

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
PASSWORD = secrets.token_urlsafe(12)
SECRET = b"test-secret-0123456789abcdef"
REPO_ROOT = Path(__file__).resolve().parents[4]

OPERATOR = "operator@criba.example"
PUEBLA = "recruiter.puebla@sazon.example"
PUEBLA_2 = "recruiter.puebla2@sazon.example"
LA_PAZ = "recruiter.lapaz@sazon.example"
MADRID = "recruiter.madrid@sazon.example"

UNCOVERED_ZONE = "zone_mx_cdmx_milpa_alta"  # in no Service Area: a Knockout by Zone
PUEBLA_CENTRO = "zone_mx_puebla_centro"  # covered by all three Puebla Locations
PUEBLA_ANGELOPOLIS = "zone_mx_puebla_angelopolis"  # Angelópolis and Centro, not La Paz
LOC_PUEBLA_CENTRO = "loc_mx_puebla_centro"
LOC_PUEBLA_LA_PAZ = "loc_mx_puebla_la_paz"
LOC_PUEBLA_ANGELOPOLIS = "loc_mx_puebla_angelopolis"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def template_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Migrated database with locations, slots and users, copied for every test."""
    path = tmp_path_factory.mktemp("template") / "criba.db"
    conn = connect(path)
    migrate(conn)
    seed(conn, slots_from=date(2026, 10, 12), days=7, now=NOW)
    seed_users(conn, PASSWORD, now=NOW)
    pw = conn.execute("SELECT password_hash FROM users WHERE id = 'usr_operator'").fetchone()[0]
    ts = iso(NOW)
    for uid, email, name, locations in (
        ("usr_rec_puebla_2", PUEBLA_2, "Recruiter Puebla 2", None),
        ("usr_rec_lapaz", LA_PAZ, "Recruiter La Paz", [LOC_PUEBLA_LA_PAZ]),
    ):
        conn.execute(
            "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
            " VALUES (?, 'grupo_sazon', ?, ?, 'recruiter', ?, ?)",
            (uid, email, name, pw, ts),
        )
        scope = locations or [
            r[0]
            for r in conn.execute(
                "SELECT location_id FROM user_locations WHERE user_id = 'usr_rec_puebla'"
            )
        ]
        conn.executemany(
            "INSERT INTO user_locations (user_id, location_id) VALUES (?, ?)",
            [(uid, loc) for loc in scope],
        )
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return path


# ---------------------------------------------------------------------------
# Fakes for the other lanes
# ---------------------------------------------------------------------------


class RecordingSender(StoredMessageSender):
    """Stores messages like the default and remembers what was sent."""

    def __init__(self) -> None:
        self.texts: list[tuple[str, str, str]] = []
        self.notices: list[tuple[str, str]] = []

    def send_human_text(self, conn, *, application_id, user_id, text, now):
        self.texts.append((application_id, user_id, text))
        return super().send_human_text(
            conn, application_id=application_id, user_id=user_id, text=text, now=now
        )

    def send_notice(self, conn, *, application_id, notice, now):
        self.notices.append((application_id, notice))
        return super().send_notice(conn, application_id=application_id, notice=notice, now=now)


@dataclass
class Env:
    app: FastAPI
    ctx: ApiContext
    clock: FakeClock
    session_clock: FakeClock
    db_path: Path
    sender: RecordingSender
    notified: list[tuple[str, str]] = field(default_factory=list)
    submitted: list[tuple[str, str, bool]] = field(default_factory=list)
    due_jobs: list[int] = field(default_factory=lambda: [0])
    _conns: list = field(default_factory=list)
    _clients: list = field(default_factory=list)

    @property
    def conn(self):
        if not self._conns:
            self._conns.append(connect(self.db_path))
        return self._conns[0]

    def client(self, email: str | None = None) -> TestClient:
        client = TestClient(self.app)
        self._clients.append(client)
        if email:
            response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
            assert response.status_code == 200, response.text
        return client

    def close(self) -> None:
        for client in self._clients:
            client.close()
        for conn in self._conns:
            conn.close()


def build_env(db_path: Path, **settings: Any) -> Env:
    clock = FakeClock(NOW)
    session_clock = FakeClock(datetime.now(UTC))
    sender = RecordingSender()
    notified: list[tuple[str, str]] = []
    submitted: list[tuple[str, str, bool]] = []
    due_jobs = [0]
    base = {
        "db_path": str(db_path),
        "env": "",
        "booking_secret": SECRET,
        "sse_poll_seconds": 0.05,
        "sse_max_seconds": 1.1,
        "sse_heartbeat_seconds": 0.3,
    }
    ctx = ApiContext(
        settings=ApiSettings(**{**base, **settings}),
        clock=clock,
        services=Services(
            sender=sender,
            booking=ToolsBookingService(),
            comparison=UsageComparison(),
            notify=lambda application_id, kind: notified.append((application_id, kind)),
            submit_action=lambda conn, application_id, action: submitted.append(
                (application_id, action, conn.in_transaction)
            ),
        ),
        session_clock=session_clock,
        run_due_jobs=lambda: due_jobs[0],
    )
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.api_context = ctx
    return Env(
        app=app,
        ctx=ctx,
        clock=clock,
        session_clock=session_clock,
        db_path=db_path,
        sender=sender,
        notified=notified,
        submitted=submitted,
        due_jobs=due_jobs,
    )


@pytest.fixture
def env(template_db: Path, tmp_path: Path) -> Iterator[Env]:
    db = tmp_path / "criba.db"
    shutil.copy(template_db, db)
    built = build_env(db)
    yield built
    built.close()


@pytest.fixture
def make_env(template_db: Path, tmp_path: Path) -> Iterator[Callable[..., Env]]:
    """Build an Env with custom settings (for example ``env="sim"``)."""
    created: list[Env] = []

    def factory(**settings: Any) -> Env:
        db = tmp_path / f"criba-{len(created)}.db"
        shutil.copy(template_db, db)
        built = build_env(db, **settings)
        created.append(built)
        return built

    yield factory
    for built in created:
        built.close()


@pytest.fixture
def operator(env: Env) -> TestClient:
    return env.client(OPERATOR)


@pytest.fixture
def puebla(env: Env) -> TestClient:
    return env.client(PUEBLA)


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


class Builder:
    def __init__(self, env: Env) -> None:
        self.env = env

    def application(
        self,
        *,
        zone: str = PUEBLA_CENTRO,
        name: str = "Luis Hernández",
        stage: str = "qualified",
        language: str = "es-MX",
        now: datetime | None = None,
        approver: str = "usr_rec_puebla",
    ) -> dict[str, Any]:
        """A synthetic Candidate and Application, driven through the repository."""
        conn = self.env.conn
        at = now or NOW
        digits = "".join(str(secrets.randbelow(10)) for _ in range(8))
        candidate, _ = repo.get_or_create_candidate(
            conn,
            client_id="grupo_sazon",
            channel="webchat",
            external_id=uuid.uuid4().hex,
            phone=f"+5255{digits}",
            country="MX",
            language=language,
            now=at,
        )
        app = repo.start_application(conn, candidate["id"], "webchat", now=at)
        app_id = app["id"]
        if stage == "greeting":
            return app
        repo.record_answer(conn, app_id, "full_name", name, raw_input=name, now=at)
        if stage == "knockout_zone":
            zone = UNCOVERED_ZONE
        repo.record_answer(conn, app_id, "zone", zone, raw_input="Centro", now=at)
        if stage == "knockout_zone":
            return repo.close_application(
                conn, app_id, "knockout", knockout_criterion="zone", now=at
            )
        repo.record_answer(conn, app_id, "driver_license", True, raw_input="sí tengo", now=at)
        if stage == "knockout_license":
            repo.record_answer(conn, app_id, "driver_license", False, raw_input="no", now=at)
            return repo.close_application(
                conn, app_id, "knockout", knockout_criterion="driver_license", now=at
            )
        repo.record_answer(conn, app_id, "availability", ["full_time"], now=at)
        repo.record_answer(conn, app_id, "preferred_schedule", ["morning"], now=at)
        repo.record_answer(conn, app_id, "experience_years", 2, now=at)
        repo.record_answer(conn, app_id, "experience_platforms", ["rappi"], now=at)
        repo.record_answer(conn, app_id, "start_date", "2026-10-12", start_date_far=False, now=at)
        app = repo.mark_qualified(conn, app_id, now=at)
        if stage == "approved":
            app = repo.mark_approved(conn, app_id, approver, now=at)
        return app

    def queue_item(self, app_id: str, kind: str, now: datetime = NOW, **kw: Any) -> dict[str, Any]:
        return repo.open_queue_item(self.env.conn, app_id, kind, now=now, **kw)  # type: ignore[arg-type]

    def usage(self, **kw: Any) -> None:
        row = {
            "ts": iso(NOW),
            "environment": "prod",
            "source": "live",
            "dataset": None,
            "kind": "run",
            "provider": "stub",
            "model": "anthropic/claude-haiku-5.5",
            "input_tokens": 1000,
            "output_tokens": 50,
            "cache_read_tokens": 700,
            "cache_write_tokens": 0,
            "est_cost_usd": 0.002,
            "latency_ms": 1500,
            "status": "ok",
            "application_id": None,
            "session_id": None,
            "run_id": None,
            "reconciled_cost_usd": None,
        }
        row.update(kw)
        cols = ",".join(row)
        self.env.conn.execute(
            f"INSERT INTO usage ({cols}) VALUES ({','.join('?' * len(row))})", list(row.values())
        )


@pytest.fixture
def build(env: Env) -> Builder:
    return Builder(env)


def at(minutes: float = 0, **kw: float) -> datetime:
    return NOW + timedelta(minutes=minutes, **kw)


# ---------------------------------------------------------------------------
# Contract conformance
# ---------------------------------------------------------------------------


class Contract:
    def __init__(self, path: Path) -> None:
        self.spec = yaml.safe_load(path.read_text())
        self.operations: dict[str, tuple[str, str, dict[str, Any]]] = {}
        for url, item in self.spec["paths"].items():
            for method, op in item.items():
                if isinstance(op, dict) and "operationId" in op:
                    self.operations[op["operationId"]] = (url, method, op)

    def _resolve(self, node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            target: Any = self.spec
            for part in node["$ref"].removeprefix("#/").split("/"):
                target = target[part]
            node = target
        return node

    def schema(self, name: str) -> dict[str, Any]:
        return {"$ref": f"#/components/schemas/{name}", "components": self.spec["components"]}

    def validate(self, name: str, instance: Any) -> None:
        errors = sorted(
            Draft202012Validator(self.schema(name)).iter_errors(instance), key=lambda e: e.path
        )
        assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:8])

    def conforms(self, response: Any, operation_id: str) -> Any:
        """Assert the status is documented and the JSON body matches its schema."""
        _, _, op = self.operations[operation_id]
        documented = op["responses"]
        assert str(response.status_code) in documented, (
            f"{operation_id}: {response.status_code} is not documented "
            f"({sorted(documented)}): {response.text[:300]}"
        )
        declared = self._resolve(documented[str(response.status_code)])
        content = declared.get("content", {})
        if "application/json" not in content:
            return None
        schema = self._resolve(content["application/json"]["schema"])
        body = response.json()
        wrapper = {**schema, "components": self.spec["components"]}
        errors = sorted(Draft202012Validator(wrapper).iter_errors(body), key=lambda e: e.path)
        assert not errors, f"{operation_id} {response.status_code}:\n" + "\n".join(
            f"{list(e.path)}: {e.message}" for e in errors[:8]
        )
        return body


@pytest.fixture(scope="session")
def contract() -> Contract:
    return Contract(REPO_ROOT / "contracts" / "api.openapi.yaml")


@pytest.fixture(scope="session")
def chat_contract() -> Contract:
    return Contract(REPO_ROOT / "contracts" / "webchat.openapi.yaml")
