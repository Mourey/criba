"""Fixtures for the agent-tool tests: a migrated, seeded SQLite file and helpers that drive
Applications through the tools. All reference data is the SYNTHETIC seed."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from criba.app import create_app
from criba.channel.copy import qualified_close
from criba.db import connect, iso, migrate, repo
from criba.db.seed import seed
from criba.screening import FakeClock
from criba.tools import call_tool
from criba.tools import http as tools_http
from criba.tools.common import answers_view
from criba.tools.schema import contract

# Friday 2026-10-09 10:00 UTC (12:00 in Madrid, 04:00 in Mexico City).
NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
SECRET = "test-tools-secret"

# Structured values that pass every check, per country (ADR 0042: the agent interprets).
GOOD_ANSWERS: dict[str, list[tuple[str, Any]]] = {
    "ES": [
        ("full_name", "Ana García"),
        ("zone", ["zone_es_madrid_chamberi"]),
        ("driver_license", "yes"),
        ("availability", ["full_time"]),
        ("preferred_schedule", ["morning"]),
        ("experience_years", 2),
        ("experience_platforms", ["glovo"]),
        ("start_date", "2026-10-12"),  # Monday
    ],
    "MX": [
        ("full_name", "Luis Hernández"),
        ("zone", ["zone_mx_cdmx_polanco"]),
        ("driver_license", "yes"),
        ("availability", ["full_time"]),
        ("preferred_schedule", ["morning", "afternoon"]),
        ("experience_years", 0.5),
        ("experience_platforms", ["rappi"]),
        ("start_date", "2026-10-10"),  # tomorrow in Mexico City
    ],
}
# What the Candidate wrote for each of those values (``answer_all(say=True)``).
CANDIDATE_WORDS: dict[str, dict[str, str]] = {
    "ES": {
        "zone": "Chamberí, Madrid",
        "driver_license": "sí, tengo carnet",
        "availability": "jornada completa",
        "preferred_schedule": "por la mañana",
        "experience_years": "2 años",
        "experience_platforms": "glovo",
        "start_date": "el lunes",
    },
    "MX": {
        "zone": "Polanco",
        "driver_license": "sí tengo licencia",
        "availability": "tiempo completo",
        "preferred_schedule": "mañana y tarde",
        "experience_years": "6 meses",
        "experience_platforms": "rappi",
        "start_date": "mañana",
    },
}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "criba.db"
    conn = connect(path)
    migrate(conn)
    seed(conn, slots_from=date(2026, 10, 12), days=14, now=NOW)
    conn.execute(
        "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
        " VALUES ('user_rec', 'grupo_sazon', 'rec@example.test', 'Rec', 'recruiter', 'x', ?)",
        (iso(NOW),),
    )
    conn.close()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator:
    connection = connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def secret_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "criba_tools_secret.txt"
    path.write_text(SECRET + "\n", encoding="utf-8")
    monkeypatch.setenv("CRIBA_TOOLS_SECRET_FILE", str(path))
    monkeypatch.setenv("CRIBA_PUBLIC_BASE_URL", "https://empleo.gruposazon.example")
    monkeypatch.delenv("CRIBA_BOOKING_SECRET_FILE", raising=False)
    return path


@pytest.fixture
def client(db_path: Path, clock: FakeClock, secret_file: Path) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[tools_http.get_connection_factory] = lambda: lambda: connect(db_path)
    app.dependency_overrides[tools_http.get_clock] = lambda: clock
    # No lifespan: these tests need only the router, not the app's runtime.
    yield TestClient(app)


ANSWER_FIELDS = (
    "full_name",
    "zone",
    "driver_license",
    "availability",
    "preferred_schedule",
    "experience_years",
    "experience_platforms",
    "start_date",
)
# What a key that get_application leaves out means (the Screener skill says so).
MISSING_IN_GET_APPLICATION: dict[str, Any] = {
    "outcome": None,
    "knockout_criterion": None,
    "first_name": None,
    "invalid_attempts": 0,
    "reopened": False,
    "takeover_summary": None,
    "tone_hint": None,
    "approved": False,
    "interview": None,
}


class ModelView(dict):
    """A result read the way the model reads it: a key that was left out has its default.

    ``in`` and ``len`` still see what was really sent, so a test can assert that a key is
    absent. ``answers`` reads an unanswered field as ``None``."""

    def __init__(self, value: dict[str, Any], defaults: dict[str, Any]) -> None:
        super().__init__(value)
        self._defaults = defaults
        if isinstance(value.get("answers"), dict):
            self["answers"] = ModelView(value["answers"], dict.fromkeys(ANSWER_FIELDS))

    def __missing__(self, key: str) -> Any:
        if key in self._defaults:
            return self._defaults[key]
        raise KeyError(key)


class Tools:
    """Calls tools in-process, as the router does, at the clock's time."""

    def __init__(self, conn, clock: FakeClock) -> None:
        self.conn = conn
        self.clock = clock
        self._count = 0

    def new_app(
        self, *, country: str = "ES", language: str | None = None, external_id: str | None = None
    ) -> str:
        """A fresh Candidate with an Application at ``greeting``."""
        self._count += 1
        candidate, _ = repo.get_or_create_candidate(
            self.conn,
            client_id="grupo_sazon",
            channel="webchat",
            external_id=external_id or f"conv-{self._count}",
            country=country,
            language=language or ("es-MX" if country == "MX" else "es-ES"),
            now=self.clock.now(),
        )
        return repo.start_application(self.conn, candidate["id"], "webchat", now=self.clock.now())[
            "id"
        ]

    def app(self, app_id: str) -> dict[str, Any]:
        return repo.require_application(self.conn, app_id)

    def answers(self, app_id: str) -> dict[str, Any]:
        """The answers stored so far. get_application sends them only once every field is
        answered (docs/evidence/turn-size.md), so a test that checks them mid-flow asks here."""
        return answers_view(self.conn, self.app(app_id))

    def say(self, app_id: str, text: str) -> int:
        """The Candidate writes ``text``: an inbound message; returns its id."""
        app = self.app(app_id)
        message, _ = repo.add_message(
            self.conn,
            candidate_id=app["candidate_id"],
            application_id=app_id,
            channel="webchat",
            direction="inbound",
            sender="candidate",
            body=text,
            now=self.clock.now(),
        )
        return message["id"]

    def events(self, app_id: str, kind: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT data FROM events WHERE application_id = ? AND kind = ? ORDER BY id",
            (app_id, kind),
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def raw(self, app_id: str, tool: str, **args: Any) -> tuple[int, dict[str, Any]]:
        return call_tool(self.conn, tool, args, app_id, now=self.clock.now())

    def __call__(self, app_id: str, tool: str, **args: Any) -> dict[str, Any]:
        status, body = self.raw(app_id, tool, **args)
        assert status == 200, body
        if body["ok"]:
            assert_output(tool, body["value"])
        else:
            assert_tool_error(body)
        return body

    def ok(self, app_id: str, tool: str, **args: Any) -> dict[str, Any]:
        """The tool's value, as the model reads it: a get_application key that is not there is
        null or false, and an answer not given yet is null (docs/evidence/turn-size.md).
        ``"key" in value`` still tells whether it was sent."""
        body = self(app_id, tool, **args)
        assert body["ok"], body
        value = body["value"]
        if tool == "get_application":
            return ModelView(value, MISSING_IN_GET_APPLICATION)
        return value

    def err(self, app_id: str, tool: str, **args: Any) -> dict[str, Any]:
        body = self(app_id, tool, **args)
        assert not body["ok"], body
        return body

    def answer_all(self, app_id: str, country: str = "ES", *, say: bool = False) -> dict[str, Any]:
        """Record every good answer; with ``say``, each follows the Candidate's message."""
        value: dict[str, Any] = {}
        for field, answer in GOOD_ANSWERS[country]:
            if say:
                self.say(app_id, CANDIDATE_WORDS[country].get(field, str(answer)))
            value = self.ok(app_id, "record_answer", field=field, value=answer)
            assert value["status"] == "accepted", value
        return value

    def approve(self, app_id: str, country: str = "ES") -> None:
        self.answer_all(app_id, country)
        app = self.app(app_id)
        assert self.ok(app_id, "finish_screening") == {
            "stage": "qualified",
            "message": qualified_close(app["language"], country, app["full_name"].split()[0]),
        }
        repo.mark_approved(self.conn, app_id, "user_rec", now=self.clock.now())


@pytest.fixture
def tools(conn, clock: FakeClock, secret_file: Path) -> Tools:
    return Tools(conn, clock)


def _validator(ref: str) -> Draft202012Validator:
    defs = contract()["$defs"]
    return Draft202012Validator({"$defs": defs, "$ref": ref})


def assert_output(tool: str, value: dict[str, Any]) -> None:
    errors = list(_validator(f"#/$defs/{tool}_output").iter_errors(value))
    assert not errors, [e.message for e in errors]


def assert_tool_error(body: dict[str, Any]) -> None:
    errors = list(_validator("#/$defs/tool_error").iter_errors(body))
    assert not errors, [e.message for e in errors]
