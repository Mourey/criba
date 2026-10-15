"""The app's mounts and startup wiring (integrator-owned ``criba/app.py``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from criba.api.sim import SimSettingsError
from criba.app import create_app
from criba.wiring import build_runtime


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The real app with its lifespan, on a temporary database. Hermes is never called."""
    key = tmp_path / "hermes_key.txt"
    key.write_text("test-key\n", encoding="utf-8")
    monkeypatch.delenv("CRIBA_ENV", raising=False)
    monkeypatch.delenv("CRIBA_HERMES_PROFILE", raising=False)
    env = {
        "CRIBA_DB_PATH": str(tmp_path / "criba.db"),
        "CRIBA_ENV": "test",
        "CRIBA_HERMES_API_KEY_FILE": str(key),
    }
    with TestClient(create_app(lambda: build_runtime(env))) as test_client:
        yield test_client


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/applications"), ("POST", "/api/applications/abc/claim"), ("GET", "/api/queue")],
)
def test_api_router_is_the_real_one_and_wants_a_session(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/chat/conversations"),
        ("GET", "/api/chat/conversations/abc"),
        ("GET", "/api/chat/conversations/abc/messages"),
        ("GET", "/api/sim/clock"),
    ],
)
def test_no_route_is_a_501_placeholder(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path).status_code != 501


def test_chat_paths_reach_the_webchat_router_not_the_api_router(client: TestClient) -> None:
    # The /api router would answer 401 (no session); the web chat router answers its own.
    response = client.post("/api/chat/conversations", json={"country": "XX"})
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
    missing = client.get("/api/chat/conversations/wc_nope")
    assert missing.status_code == 404
    assert missing.json() == {"error": "not_found", "message": "Conversation not found."}


def test_voice_path_reaches_the_voice_router_not_the_api_router(client: TestClient) -> None:
    # Voice is off here: the voice router answers its own 404 body. Unmounted, FastAPI would
    # answer {"detail": "Not Found"}, and the /api router a 401.
    response = client.post("/api/voice/v1/chat/completions", json={"messages": []})
    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "message": "Not found."}


def test_internal_tools_router_is_mounted(client: TestClient) -> None:
    assert client.post("/internal/tools/get_application").status_code == 401


def test_unmounted_path_is_404(client: TestClient) -> None:
    assert client.get("/nope").status_code == 404


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "/internal/tools/x/boom",  # one segment more than the tools route's /{name}
            {"ok": False, "error": "internal", "hint": "Unexpected backend error."},
        ),
        ("/api/chat/boom", {"error": "internal", "message": "Unexpected error."}),
        ("/boom", {"error": "internal", "message": "Unexpected error."}),
    ],
)
def test_an_error_no_router_answers_is_a_json_500_in_the_surface_shape(
    path: str, expected: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    """The last net (``app.unexpected_error``): logged with its traceback server-side, and
    nothing of it (nor any request text) in the body."""
    app = create_app(lambda: build_runtime({}))

    def boom() -> None:
        raise RuntimeError("secret detail: mi DNI es 12345678Z")

    app.add_api_route(path, boom, methods=["POST"])
    with caplog.at_level("ERROR", logger="criba.app"):
        response = TestClient(app, raise_server_exceptions=False).post(path, json={"t": "hola"})
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == expected
    [record] = [r for r in caplog.records if r.name == "criba.app"]
    assert record.exc_info is not None and isinstance(record.exc_info[1], RuntimeError)
    assert "12345678Z" not in record.getMessage()


def test_startup_refuses_a_sim_profile_without_sim_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRIBA_ENV", raising=False)
    monkeypatch.setenv("CRIBA_HERMES_PROFILE", "criba-recruiter-sim")
    built: list[bool] = []

    def factory():  # never reached: the check runs first
        built.append(True)
        raise AssertionError("runtime built despite the sim mismatch")

    with pytest.raises(SimSettingsError), TestClient(create_app(factory)):
        pass
    assert built == []


def test_health_alarms_lists_the_open_system_alarms(client: TestClient) -> None:
    assert client.get("/health/alarms").json() == {"alarms": []}
    alarms = client.app.state.criba_runtime.channel.router.alarms
    alarms.record_failure("credits", "app-1")
    [alarm] = client.get("/health/alarms").json()["alarms"]
    assert (alarm["cause"], alarm["sources"]) == ("credits", ["runs"])
    assert alarm["since"].startswith("20")
