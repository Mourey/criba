"""API auth, role scoping, cost removal, Claim and Takeover races, sessions (spec 003 task 17).

Runs against the **real app** (``criba.app.create_app`` with ``criba.wiring.build_runtime``)
served by uvicorn on a loopback port, on a file-backed SQLite database in WAL mode, seeded at
startup with the synthetic users (``CRIBA_SEED=1``). Clients are real ``httpx`` clients, so
the cookies are real ``Set-Cookie`` headers and the races are real concurrent requests.

Hermes is a fake run client and the summary is the deterministic one: no model is called.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import json
import secrets
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from criba.api.auth import hash_token
from criba.app import create_app
from criba.channel.run_client import RunResult
from criba.db import connect, repo
from criba.db.connection import iso
from criba.screening.clock import FakeClock
from criba.summary import DeterministicSummaryClient
from criba.wiring import Runtime, build_runtime

# Friday 2026-10-09 10:00 UTC: inside working hours in Madrid and Mexico.
NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
PASSWORD = secrets.token_urlsafe(12)
REPO_ROOT = Path(__file__).resolve().parents[3]
SESSION_TTL_HOURS = 6

OPERATOR = "operator@criba.example"
PUEBLA = "recruiter.puebla@sazon.example"
MADRID = "recruiter.madrid@sazon.example"
THROTTLED = "recruiter.throttle@sazon.example"
RACERS = [f"recruiter.puebla.race{i}@sazon.example" for i in range(8)]

PUEBLA_CENTRO = "zone_mx_puebla_centro"
MADRID_CHAMBERI = "zone_es_madrid_chamberi"
LOC_MADRID = "loc_es_madrid_chamberi"

# Operator-only keys (ADR 0019) and the usage figures that live only inside them.
COST_KEYS = {"cost", "cost_usd", "est_cost_usd", "reconciled_cost_usd"}
USAGE_KEYS = {
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "p50_latency_ms",
    "models",
    "tokens",
    "model_mix",
    "spend_by_environment",
}


# ---------------------------------------------------------------------------
# The served app
# ---------------------------------------------------------------------------


@dataclass
class FakeRuns:
    """Stands in for Hermes: every run completes with one fixed line."""

    profile: str = "criba-recruiter"
    inputs: list[tuple[str, str]] = field(default_factory=list)

    def run(self, *, session_id: str, input: str, idempotency_key: str) -> RunResult:
        self.inputs.append((session_id, input))
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
class Server:
    base_url: str
    runtime: Runtime
    db_path: Path
    clients: list[httpx.Client] = field(default_factory=list)

    def conn(self):
        return connect(self.db_path)

    def client(self, email: str | None = None) -> httpx.Client:
        client = httpx.Client(base_url=self.base_url, timeout=30)
        self.clients.append(client)
        if email:
            response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
            assert response.status_code == 200, response.text
        return client


def _write(path: Path, text: str) -> str:
    path.write_text(text + "\n", encoding="utf-8")
    return str(path)


def _env(root: Path) -> dict[str, str]:
    return {
        "CRIBA_DB_PATH": str(root / "criba.db"),
        "CRIBA_ENV": "test",
        "CRIBA_SEED": "1",
        "CRIBA_SEED_PASSWORD_FILE": _write(root / "seed.txt", PASSWORD),
        "CRIBA_TOOLS_SECRET_FILE": _write(root / "tools.txt", secrets.token_hex(16)),
        "CRIBA_BOOKING_SECRET_FILE": _write(root / "booking.txt", secrets.token_hex(16)),
        "CRIBA_HERMES_API_KEY_FILE": _write(root / "hermes.txt", "test-hermes-key"),
        "CRIBA_SESSION_TTL_HOURS": str(SESSION_TTL_HOURS),
        "CRIBA_SCHEDULER_INTERVAL_S": "3600",
    }


def _add_users(db_path: Path) -> None:
    """Extra synthetic Recruiters with the Puebla scope: the racers and a throttle target."""
    conn = connect(db_path)
    try:
        pw = conn.execute("SELECT password_hash FROM users WHERE id = 'usr_operator'").fetchone()
        scope = [
            r[0]
            for r in conn.execute(
                "SELECT location_id FROM user_locations WHERE user_id = 'usr_rec_puebla'"
            )
        ]
        for email in [*RACERS, THROTTLED]:
            uid = "usr_" + email.split("@")[0].replace(".", "_")
            conn.execute(
                "INSERT OR IGNORE INTO users (id, client_id, email, name, role, password_hash,"
                " created_at) VALUES (?, 'grupo_sazon', ?, ?, 'recruiter', ?, ?)",
                (uid, email, f"Racer {email.split('@')[0]}", pw[0], iso(NOW)),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO user_locations (user_id, location_id) VALUES (?, ?)",
                [(uid, loc) for loc in scope],
            )
    finally:
        conn.close()


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    root = tmp_path_factory.mktemp("api_auth")
    env = _env(root)
    holder: list[Runtime] = []

    def factory() -> Runtime:
        runtime = build_runtime(
            env,
            run_client=FakeRuns(),
            clock=FakeClock(NOW),
            summary_client=DeterministicSummaryClient(),
        )
        # Short-lived streams with a quick poll, so a test can read a whole stream.
        ctx = runtime.api_context
        ctx.settings = dataclasses.replace(
            ctx.settings, sse_poll_seconds=0.05, sse_max_seconds=1.5, sse_heartbeat_seconds=0.5
        )
        holder.append(runtime)
        return runtime

    with pytest.MonkeyPatch.context() as mp:
        # Anything that still reads os.environ sees the same configuration.
        for name in ("CRIBA_PUBLIC_BASE_URL", "CRIBA_COOKIE_SECURE", "CRIBA_HERMES_PROFILE"):
            mp.delenv(name, raising=False)
        for name, value in env.items():
            mp.setenv(name, value)
        config = uvicorn.Config(
            create_app(factory), host="127.0.0.1", port=0, log_level="warning", lifespan="on"
        )
        uv = uvicorn.Server(config)
        thread = threading.Thread(target=uv.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        while not uv.started:
            assert thread.is_alive() and time.monotonic() < deadline, "server did not start"
            time.sleep(0.02)
        port = uv.servers[0].sockets[0].getsockname()[1]
        _add_users(Path(env["CRIBA_DB_PATH"]))
        srv = Server(f"http://127.0.0.1:{port}", holder[0], Path(env["CRIBA_DB_PATH"]))
        try:
            yield srv
        finally:
            for client in srv.clients:
                client.close()
            uv.should_exit = True
            thread.join(timeout=10)


# ---------------------------------------------------------------------------
# Data builders (straight through the repository, on the served database)
# ---------------------------------------------------------------------------


def make_application(
    srv: Server, *, zone: str = PUEBLA_CENTRO, qualified: bool = True, name: str = "Luis Hernández"
) -> dict[str, Any]:
    conn = srv.conn()
    try:
        country = "ES" if zone.startswith("zone_es_") else "MX"
        digits = f"{secrets.randbelow(10**8):08d}"
        candidate, _ = repo.get_or_create_candidate(
            conn,
            client_id="grupo_sazon",
            channel="webchat",
            external_id=uuid.uuid4().hex,
            phone=("+346" if country == "ES" else "+5255") + digits,
            country=country,
            language="es-ES" if country == "ES" else "es-MX",
            now=NOW,
        )
        app = repo.start_application(conn, candidate["id"], "webchat", now=NOW)
        app_id = app["id"]
        repo.record_answer(conn, app_id, "full_name", name, raw_input=name, now=NOW)
        repo.record_answer(conn, app_id, "zone", zone, raw_input="Centro", now=NOW)
        if not qualified:
            return repo.require_application(conn, app_id)
        repo.record_answer(conn, app_id, "driver_license", True, raw_input="sí", now=NOW)
        repo.record_answer(conn, app_id, "availability", ["full_time"], now=NOW)
        repo.record_answer(conn, app_id, "preferred_schedule", ["morning"], now=NOW)
        repo.record_answer(conn, app_id, "experience_years", 2, now=NOW)
        repo.record_answer(conn, app_id, "experience_platforms", ["rappi"], now=NOW)
        repo.record_answer(conn, app_id, "start_date", "2026-10-12", start_date_far=False, now=NOW)
        return repo.mark_qualified(conn, app_id, now=NOW)
    finally:
        conn.close()


def add_usage(srv: Server, application_id: str) -> None:
    conn = srv.conn()
    try:
        conn.execute(
            "INSERT INTO usage (ts, environment, source, kind, provider, model, input_tokens,"
            " output_tokens, cache_read_tokens, cache_write_tokens, est_cost_usd, latency_ms,"
            " status, application_id, reconciled_cost_usd) VALUES (?, 'test', 'live', 'run',"
            " 'stub', 'anthropic/claude-haiku-5.5', 1000, 50, 700, 0, 0.002, 1500, 'ok', ?,"
            " 0.0021)",
            (iso(NOW), application_id),
        )
    finally:
        conn.close()


def open_item(srv: Server, application_id: str, kind: str, **kw: Any) -> dict[str, Any]:
    conn = srv.conn()
    try:
        return repo.open_queue_item(conn, application_id, kind, now=NOW, **kw)  # type: ignore[arg-type]
    finally:
        conn.close()


def query(srv: Server, sql: str, *params: Any) -> list[Any]:
    conn = srv.conn()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def keys_in(value: Any, found: set[str] | None = None) -> set[str]:
    found = set() if found is None else found
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(k)
            keys_in(v, found)
    elif isinstance(value, list):
        for item in value:
            keys_in(item, found)
    return found


def read_stream(
    client: httpx.Client, path: str, headers: dict[str, str] | None = None
) -> tuple[int, list[dict[str, Any]]]:
    """Read a whole stream (the test server closes each one after 1.5 s)."""
    frames: list[dict[str, Any]] = []
    with client.stream("GET", path, headers=headers or {}, timeout=10) as response:
        if response.status_code != 200:
            response.read()
            return response.status_code, [response.json()]
        current: dict[str, Any] = {}
        for line in response.iter_lines():
            if line.startswith("event:"):
                current["event"] = line[6:].strip()
            elif line.startswith("id:"):
                current["id"] = line[3:].strip()
            elif line.startswith("data:"):
                current["data"] = json.loads(line[5:].strip())
            elif line == "" and current:
                frames.append(current)
                current = {}
    return 200, frames


def stream_while(
    client: httpx.Client, path: str, action, delay: float = 0.3
) -> tuple[int, list[dict[str, Any]]]:
    """Read ``path`` in a thread while ``action`` runs after ``delay`` seconds."""
    result: list[Any] = []
    reader = threading.Thread(target=lambda: result.append(read_stream(client, path)))
    reader.start()
    time.sleep(delay)
    action()
    reader.join(timeout=10)
    return result[0]


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


class Contract:
    def __init__(self, path: Path) -> None:
        self.spec = yaml.safe_load(path.read_text())
        self.ops: dict[str, dict[str, Any]] = {}
        for item in self.spec["paths"].values():
            for op in item.values():
                if isinstance(op, dict) and "operationId" in op:
                    self.ops[op["operationId"]] = op

    def _resolve(self, node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            target: Any = self.spec
            for part in node["$ref"].removeprefix("#/").split("/"):
                target = target[part]
            node = target
        return node

    def conforms(self, response: httpx.Response, operation_id: str) -> Any:
        documented = self.ops[operation_id]["responses"]
        status = str(response.status_code)
        assert status in documented, f"{operation_id}: {status} undocumented: {response.text}"
        content = self._resolve(documented[status]).get("content", {})
        if "application/json" not in content:
            return None
        schema = {**self._resolve(content["application/json"]["schema"])}
        schema["components"] = self.spec["components"]
        body = response.json()
        errors = list(Draft202012Validator(schema).iter_errors(body))
        assert not errors, f"{operation_id}: " + "; ".join(e.message for e in errors[:5])
        return body


@pytest.fixture(scope="module")
def contract() -> Contract:
    return Contract(REPO_ROOT / "contracts" / "api.openapi.yaml")


# ---------------------------------------------------------------------------
# Role scoping
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scoped(server: Server) -> dict[str, Any]:
    """One Puebla and one Madrid Application, each Qualified with an open Approval."""
    puebla = make_application(server, zone=PUEBLA_CENTRO)
    madrid = make_application(server, zone=MADRID_CHAMBERI, name="Ana García")
    return {"puebla": puebla["id"], "madrid": madrid["id"]}


def test_recruiter_lists_only_applications_at_their_locations(server, scoped, contract):
    recruiter = server.client(PUEBLA)
    body = contract.conforms(recruiter.get("/api/applications?limit=200"), "listApplications")
    ids = {a["id"] for a in body["items"]}
    assert scoped["puebla"] in ids and scoped["madrid"] not in ids
    assert body["total"] == len(body["items"])
    scope = {loc["id"] for loc in recruiter.get("/api/auth/me").json()["locations"]}
    for item in body["items"]:
        assert {loc["id"] for loc in item["locations"]} & scope
    # A filter naming another Location narrows nothing into view.
    outside = recruiter.get("/api/applications", params={"location_id": LOC_MADRID}).json()
    assert outside["items"] == [] and outside["total"] == 0

    operator = server.client(OPERATOR)
    every = contract.conforms(operator.get("/api/applications?limit=200"), "listApplications")
    assert {scoped["puebla"], scoped["madrid"]} <= {a["id"] for a in every["items"]}


def test_recruiter_reads_in_scope_and_gets_404_elsewhere(server, scoped, contract):
    recruiter = server.client(PUEBLA)
    inside, outside = scoped["puebla"], scoped["madrid"]
    contract.conforms(recruiter.get(f"/api/applications/{inside}"), "getApplication")
    contract.conforms(recruiter.get(f"/api/applications/{inside}/export"), "exportApplication")
    for path, operation in (
        (f"/api/applications/{outside}", "getApplication"),
        (f"/api/applications/{outside}/export", "exportApplication"),
    ):
        response = recruiter.get(path)
        assert response.status_code == 404, path
        assert contract.conforms(response, operation)["error"] == "not_found"
    status, frames = read_stream(recruiter, f"/api/applications/{outside}/stream")
    assert status == 404 and frames[0]["error"] == "not_found"
    assert read_stream(recruiter, f"/api/applications/{inside}/stream")[0] == 200

    operator = server.client(OPERATOR)
    for app_id in (inside, outside):
        assert operator.get(f"/api/applications/{app_id}").status_code == 200
        assert operator.get(f"/api/applications/{app_id}/export").status_code == 200


def test_every_action_on_an_out_of_scope_application_is_a_404(server, contract):
    """Every Application-scoped write answers 404 `not_found`, and changes nothing."""
    outside = make_application(server, zone=MADRID_CHAMBERI)["id"]
    approval = query(
        server, "SELECT id FROM queue_items WHERE application_id = ? AND kind = 'approval'", outside
    )[0][0]
    review = open_item(server, outside, "review")["id"]
    escalation = open_item(server, outside, "escalation", reason="wants_human")["id"]
    before = query(server, "SELECT count(*) FROM events WHERE application_id = ?", outside)[0][0]

    recruiter = server.client(PUEBLA)
    base = f"/api/applications/{outside}"
    calls = [
        ("POST", f"{base}/claim", None, "claimApplication"),
        ("DELETE", f"{base}/claim", None, "releaseClaim"),
        ("POST", f"{base}/claim/takeover", None, "takeOverIdleClaim"),
        ("POST", f"{base}/takeover", None, "startTakeover"),
        ("POST", f"{base}/takeover/messages", {"text": "Hola"}, "sendTakeoverMessage"),
        ("POST", f"{base}/takeover/handback", {}, "handBack"),
        ("POST", f"{base}/rejection", {"reason": "role_filled"}, "rejectApplication"),
        ("POST", f"{base}/booking-link", None, "createBookingLink"),
        ("POST", f"/api/queue/approvals/{approval}", {"decision": "approved"}, "resolveApproval"),
        ("POST", f"/api/queue/reviews/{review}", {"decision": "upheld"}, "resolveReview"),
        (
            "POST",
            f"/api/queue/escalations/{escalation}",
            {"decision": "dismissed"},
            "resolveEscalation",
        ),
    ]
    for method, path, body, operation in calls:
        response = recruiter.request(method, path, json=body)
        assert response.status_code == 404, f"{method} {path}: {response.text}"
        assert contract.conforms(response, operation)["error"] == "not_found"

    after = query(server, "SELECT count(*) FROM events WHERE application_id = ?", outside)[0][0]
    assert after == before
    app = query(server, "SELECT * FROM applications WHERE id = ?", outside)[0]
    assert (app["claim_user_id"], app["control"], app["status"]) == (None, "agent", "open")
    open_items = query(
        server,
        "SELECT count(*) FROM queue_items WHERE application_id = ? AND status = 'open'",
        outside,
    )[0][0]
    assert open_items == 3


def test_queue_list_and_stream_are_scoped(server, scoped, contract):
    recruiter = server.client(PUEBLA)
    body = contract.conforms(recruiter.get("/api/queue?limit=200"), "listQueue")
    apps = {i["application"]["id"] for i in body["items"]}
    assert scoped["puebla"] in apps and scoped["madrid"] not in apps

    inside = make_application(server, zone=PUEBLA_CENTRO, qualified=False)["id"]
    outside = make_application(server, zone=MADRID_CHAMBERI, qualified=False)["id"]

    def open_both():
        open_item(server, outside, "escalation", reason="wants_human")
        open_item(server, inside, "escalation", reason="wants_human")

    status, frames = stream_while(recruiter, "/api/queue/stream", open_both)
    assert status == 200
    seen = {f["data"]["data"]["application_id"] for f in frames if f.get("event") == "queue"}
    assert inside in seen and outside not in seen
    for f in frames:
        assert f["event"] == "queue" and f["data"]["type"] == "queue"

    operator = server.client(OPERATOR)
    all_apps = {
        i["application"]["id"] for i in operator.get("/api/queue?limit=200").json()["items"]
    }
    assert {scoped["puebla"], scoped["madrid"], inside, outside} <= all_apps


def test_results_locations_and_slots_are_scoped(server, scoped, contract):
    recruiter = server.client(PUEBLA)
    operator = server.client(OPERATOR)
    mine = contract.conforms(recruiter.get("/api/metrics/results"), "getResults")
    every = contract.conforms(operator.get("/api/metrics/results"), "getResults")
    in_scope = recruiter.get("/api/applications", params={"limit": 200}).json()["total"]
    assert mine["applications"] == in_scope
    assert every["applications"] > mine["applications"]

    scope = {loc["id"] for loc in recruiter.get("/api/auth/me").json()["locations"]}
    locations = contract.conforms(recruiter.get("/api/locations"), "listLocations")["items"]
    assert {loc["id"] for loc in locations} == scope
    assert len(operator.get("/api/locations").json()["items"]) > len(scope)

    slots = contract.conforms(recruiter.get("/api/slots"), "listSlots")["items"]
    assert slots and {s["location_id"] for s in slots} <= scope
    assert recruiter.get("/api/slots", params={"location_id": LOC_MADRID}).json()["items"] == []


def test_slot_writes_outside_the_scope_are_refused(server, contract):
    """Creating at another Location, and editing or cancelling another Location's slot, are all
    404 with the same error code, as for any record outside the scope."""
    recruiter = server.client(PUEBLA)
    operator = server.client(OPERATOR)
    start = NOW + timedelta(days=30)
    times = {"starts_at": iso(start), "ends_at": iso(start + timedelta(minutes=30))}
    response = recruiter.post("/api/slots", json={"location_id": LOC_MADRID, "slots": [times]})
    assert response.status_code == 404
    assert contract.conforms(response, "createSlots")["error"] == "not_found"
    unknown = recruiter.post("/api/slots", json={"location_id": "loc_nowhere", "slots": [times]})
    assert (unknown.status_code, unknown.json()) == (404, response.json())

    created = operator.post("/api/slots", json={"location_id": LOC_MADRID, "slots": [times]})
    slot_id = contract.conforms(created, "createSlots")["items"][0]["id"]
    later = {
        "starts_at": iso(start + timedelta(hours=1)),
        "ends_at": iso(start + timedelta(hours=2)),
    }
    response = recruiter.patch(f"/api/slots/{slot_id}", json=later)
    assert response.status_code == 404
    contract.conforms(response, "updateSlot")
    response = recruiter.delete(f"/api/slots/{slot_id}")
    assert response.status_code == 404
    contract.conforms(response, "cancelSlot")
    assert (
        query(server, "SELECT cancelled_at FROM interview_slots WHERE id = ?", slot_id)[0][0]
        is None
    )


# ---------------------------------------------------------------------------
# Cost fields
# ---------------------------------------------------------------------------


def _no_cost(label: str, value: Any) -> None:
    leaked = keys_in(value) & (COST_KEYS | USAGE_KEYS)
    assert not leaked, f"{label} leaks {sorted(leaked)} to a Recruiter"


def test_recruiters_never_receive_cost_fields_anywhere(server, contract):
    app_id = make_application(server, zone=PUEBLA_CENTRO)["id"]
    add_usage(server, app_id)
    open_item(server, app_id, "escalation", reason="wants_human")
    recruiter = server.client(PUEBLA)
    operator = server.client(OPERATOR)

    # The Operator does get them, so the walk below would see them if they leaked.
    assert operator.get(f"/api/applications/{app_id}").json()["cost"]["est_cost_usd"] > 0

    for path in (
        "/api/auth/me",
        "/api/locations",
        "/api/applications?limit=200",
        f"/api/applications/{app_id}",
        f"/api/applications/{app_id}/export",
        "/api/queue?limit=200",
        "/api/queue?status=resolved",
        "/api/slots",
        "/api/metrics/results",
    ):
        response = recruiter.get(path)
        assert response.status_code == 200, path
        _no_cost(path, response.json())

    # Mutations, and the live streams while they happen.
    base = f"/api/applications/{app_id}"
    responses: list[tuple[str, httpx.Response]] = []

    def act():
        for label, method, path, body in (
            ("claim", "POST", f"{base}/claim", None),
            ("takeover", "POST", f"{base}/takeover", None),
            ("message", "POST", f"{base}/takeover/messages", {"text": "Hola, soy del equipo."}),
            ("handback", "POST", f"{base}/takeover/handback", {}),
        ):
            responses.append((label, recruiter.request(method, path, json=body)))

    status, frames = stream_while(recruiter, f"{base}/stream", act)
    assert status == 200
    assert {f["event"] for f in frames} >= {"message", "event", "control"}
    for label, response in responses:
        assert response.status_code in (200, 201), f"{label}: {response.text}"
        _no_cost(label, response.json())
    for f in frames:
        _no_cost(f"stream {f['event']}", f["data"])

    approval = query(
        server,
        "SELECT id FROM queue_items WHERE application_id = ? AND kind = 'approval'"
        " AND status = 'open'",
        app_id,
    )[0][0]

    def approve():
        responses.append(
            (
                "approval",
                recruiter.post(f"/api/queue/approvals/{approval}", json={"decision": "approved"}),
            )
        )

    status, queue_frames = stream_while(recruiter, "/api/queue/stream", approve)
    assert status == 200 and queue_frames
    for f in queue_frames:
        _no_cost("queue stream", f["data"])
    link = recruiter.post(f"{base}/booking-link")
    assert link.status_code == 201, link.text
    responses.append(("booking-link", link))
    other = make_application(server, zone=PUEBLA_CENTRO)["id"]
    add_usage(server, other)
    rejection = recruiter.post(
        f"/api/applications/{other}/rejection", json={"reason": "role_filled"}
    )
    assert rejection.status_code == 200, rejection.text
    responses.append(("rejection", rejection))
    for label, response in responses:
        _no_cost(label, response.json())
    _no_cost("closed detail", recruiter.get(f"/api/applications/{other}").json())


def test_economics_and_comparison_are_operator_only(server, contract):
    recruiter = server.client(PUEBLA)
    operator = server.client(OPERATOR)
    for path, operation in (
        ("/api/metrics/economics", "getEconomics"),
        ("/api/metrics/comparison", "getModelComparison"),
    ):
        refused = recruiter.get(path)
        assert refused.status_code == 403
        assert contract.conforms(refused, operation)["error"] == "forbidden"
        allowed = operator.get(path)
        contract.conforms(allowed, operation)
        # 404 for the comparison means "no dataset recorded yet", never a refusal.
        assert allowed.status_code == 200 or (
            path.endswith("comparison") and allowed.status_code == 404
        )


# ---------------------------------------------------------------------------
# Races
# ---------------------------------------------------------------------------


def _race(server: Server, emails: list[str], method: str, path: str) -> list[httpx.Response]:
    clients = [server.client(email) for email in emails]
    barrier = threading.Barrier(len(clients))

    def fire(client: httpx.Client) -> httpx.Response:
        barrier.wait()
        return client.request(method, path)

    with ThreadPoolExecutor(len(clients)) as pool:
        return list(pool.map(fire, clients))


def _user_id(server: Server, email: str) -> str:
    return query(server, "SELECT id FROM users WHERE email = ?", email)[0][0]


def test_concurrent_claims_have_exactly_one_winner(server, contract):
    app_id = make_application(server, zone=PUEBLA_CENTRO)["id"]
    emails = [*RACERS, PUEBLA, OPERATOR]
    responses = _race(server, emails, "POST", f"/api/applications/{app_id}/claim")

    winners = [e for e, r in zip(emails, responses, strict=True) if r.status_code == 200]
    assert len(winners) == 1, [r.status_code for r in responses]
    for response in responses:
        body = contract.conforms(response, "claimApplication")
        if response.status_code != 200:
            assert response.status_code == 409
            assert body["error"] == "already_claimed"
    holder = query(server, "SELECT claim_user_id FROM applications WHERE id = ?", app_id)[0][0]
    assert holder == _user_id(server, winners[0])
    claims = query(
        server,
        "SELECT count(*) FROM events WHERE application_id = ? AND kind = 'claim_taken'",
        app_id,
    )[0][0]
    assert claims == 1


def test_concurrent_takeovers_have_exactly_one_winner(server, contract):
    app_id = make_application(server, zone=PUEBLA_CENTRO)["id"]
    emails = [*RACERS, PUEBLA, OPERATOR]
    responses = _race(server, emails, "POST", f"/api/applications/{app_id}/takeover")

    winners = [e for e, r in zip(emails, responses, strict=True) if r.status_code == 200]
    assert len(winners) == 1, [r.status_code for r in responses]
    for response in responses:
        body = contract.conforms(response, "startTakeover")
        if response.status_code != 200:
            assert response.status_code == 409
            assert body["error"] == "control_held"
    row = query(server, "SELECT control, control_user_id FROM applications WHERE id = ?", app_id)
    assert tuple(row[0]) == ("human", _user_id(server, winners[0]))
    started = query(
        server,
        "SELECT count(*) FROM events WHERE application_id = ? AND kind = 'takeover_started'",
        app_id,
    )[0][0]
    notices = query(
        server,
        "SELECT count(*) FROM messages WHERE application_id = ? AND sender = 'system'",
        app_id,
    )[0][0]
    assert (started, notices) == (1, 1)


def test_concurrent_take_over_of_an_idle_claim_has_one_winner(server, contract):
    app_id = make_application(server, zone=PUEBLA_CENTRO)["id"]
    conn = server.conn()
    try:
        repo.claim_application(conn, app_id, "usr_rec_puebla", now=NOW - timedelta(days=8))
    finally:
        conn.close()
    responses = _race(server, RACERS, "POST", f"/api/applications/{app_id}/claim/takeover")

    winners = [e for e, r in zip(RACERS, responses, strict=True) if r.status_code == 200]
    assert len(winners) == 1, [(r.status_code, r.text) for r in responses]
    for response in responses:
        body = contract.conforms(response, "takeOverIdleClaim")
        if response.status_code != 200:
            assert (response.status_code, body["error"]) == (409, "already_claimed")
    holder = query(server, "SELECT claim_user_id FROM applications WHERE id = ?", app_id)[0][0]
    assert holder == _user_id(server, winners[0])


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _set_cookie(response: httpx.Response) -> SimpleCookie:
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie


def test_login_cookie_flags(server, contract):
    client = server.client()
    response = client.post("/api/auth/login", json={"email": PUEBLA, "password": PASSWORD})
    contract.conforms(response, "login")
    raw = response.headers["set-cookie"]
    morsel = _set_cookie(response)["criba_session"]
    assert len(morsel.value) >= 32
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "lax"
    assert morsel["path"] == "/"
    assert int(morsel["max-age"]) == SESSION_TTL_HOURS * 3600
    assert "secure" not in raw.lower()
    # Only the hash is stored, with the configured lifetime.
    row = query(
        server,
        "SELECT created_at, expires_at FROM auth_sessions WHERE id = ?",
        hash_token(morsel.value),
    )[0]
    created, expires = (datetime.fromisoformat(v.replace("Z", "+00:00")) for v in row)
    assert expires - created == timedelta(hours=SESSION_TTL_HOURS)


def test_cookie_is_secure_when_configured(tmp_path):
    env = {**_env(tmp_path), "CRIBA_COOKIE_SECURE": "1"}
    with pytest.MonkeyPatch.context() as mp:
        for name, value in env.items():
            mp.setenv(name, value)
        app = create_app(
            lambda: build_runtime(
                env,
                run_client=FakeRuns(),
                clock=FakeClock(NOW),
                summary_client=DeterministicSummaryClient(),
            )
        )
        with TestClient(app, base_url="https://testserver") as client:
            response = client.post(
                "/api/auth/login", json={"email": OPERATOR, "password": PASSWORD}
            )
    assert response.status_code == 200, response.text
    assert _set_cookie(response)["criba_session"]["secure"] is True


def test_an_expired_session_is_refused_everywhere(server, scoped):
    client = server.client(PUEBLA)
    token = client.cookies["criba_session"]
    assert client.get("/api/auth/me").status_code == 200
    conn = server.conn()
    try:
        conn.execute(
            "UPDATE auth_sessions SET expires_at = ? WHERE id = ?",
            (iso(datetime.now(UTC) - timedelta(seconds=1)), hash_token(token)),
        )
    finally:
        conn.close()
    for path in (
        "/api/auth/me",
        "/api/applications",
        f"/api/applications/{scoped['puebla']}",
        "/api/queue",
        "/api/metrics/results",
    ):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"] == "unauthorized"
    for path in ("/api/queue/stream", f"/api/applications/{scoped['puebla']}/stream"):
        assert read_stream(client, path)[0] == 401, path


def test_an_open_stream_ends_when_the_session_is_revoked(server, scoped):
    client = server.client(PUEBLA)
    token = client.cookies["criba_session"]
    other = server.client()
    other.cookies.set("criba_session", token)
    started = time.monotonic()
    status, _ = stream_while(
        client,
        f"/api/applications/{scoped['puebla']}/stream",
        lambda: other.post("/api/auth/logout"),
        delay=0.2,
    )
    assert status == 200
    # Closed at the next poll, well before the stream's 1.5 s lifetime.
    assert time.monotonic() - started < 1.2


def test_logout_revokes_the_session_and_is_idempotent(server, contract):
    client = server.client(PUEBLA)
    token = client.cookies["criba_session"]
    response = client.post("/api/auth/logout")
    contract.conforms(response, "logout")
    assert response.status_code == 204
    assert (
        'criba_session=""' in response.headers["set-cookie"]
        or "Max-Age=0" in response.headers["set-cookie"]
    )
    replay = server.client()
    replay.cookies.set("criba_session", token)
    assert replay.get("/api/auth/me").status_code == 401
    assert replay.post("/api/auth/logout").status_code == 204
    assert server.client().post("/api/auth/logout").status_code == 204
    revoked = query(server, "SELECT revoked_at FROM auth_sessions WHERE id = ?", hash_token(token))[
        0
    ][0]
    assert revoked is not None


def test_login_throttle_locks_one_email_only(server, contract):
    client = server.client()
    for _ in range(5):
        response = client.post("/api/auth/login", json={"email": THROTTLED, "password": "wrong"})
        assert response.status_code == 401
    # Locked: even the right password is refused, with the contract's 401 shape.
    locked = client.post("/api/auth/login", json={"email": THROTTLED.upper(), "password": PASSWORD})
    assert contract.conforms(locked, "login")["error"] == "unauthorized"
    assert "set-cookie" not in locked.headers
    # Another user logs in as usual.
    ok = client.post("/api/auth/login", json={"email": MADRID, "password": PASSWORD})
    assert ok.status_code == 200
