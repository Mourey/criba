"""The plugin forwards ``{tool, args, session_id}`` to ``POST /internal/tools/{name}``, with the
shared secret, and returns what the backend says (or a contract-shaped error). Tested against a
recording stub backend, from the host Python; the same path is exercised inside the container by
test_container.py.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import pytest

from .conftest import RecordingBackend, Reply

Handler = Callable[..., str]


@pytest.fixture
def handlers(plugin: ModuleType) -> dict[str, Handler]:
    class Ctx:
        def __init__(self) -> None:
            self.handlers: dict[str, Handler] = {}

        def register_tool(self, name: str, handler: Handler, **_: Any) -> None:
            self.handlers[name] = handler

    ctx = Ctx()
    plugin.register(ctx)
    return ctx.handlers


def call(handlers: dict[str, Handler], tool: str, args: dict[str, Any], session_id: Any) -> Any:
    return json.loads(handlers[tool](args, task_id=session_id, session_id=session_id))


def test_forwards_the_exact_request_the_contract_describes(
    handlers: dict[str, Handler], backend: RecordingBackend, contract: dict[str, Any]
) -> None:
    args = {"field": "driver_license", "value": "sí, tengo carnet"}
    result = call(handlers, "record_answer", args, "app_01JA2B3C4D5E6F7G8H9J0K")

    assert result == {"ok": True, "value": {}}
    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.method == "POST"
    assert request.path == "/internal/tools/record_answer"
    assert request.headers["authorization"] == "Bearer unit-test-secret"
    assert request.headers["content-type"] == "application/json"
    assert request.body == {
        "tool": "record_answer",
        "args": args,
        "session_id": "app_01JA2B3C4D5E6F7G8H9J0K",
    }
    # And the body is a valid envelope under the frozen contract.
    jsonschema.validate(request.body, contract)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_application", {}),
        ("record_answer", {"field": "availability", "value": ["full_time", "weekends"]}),
        ("request_review", {"note": "Tengo licencia vigente."}),
        ("raise_escalation", {"reason": "unanswered_question", "note": "¿Dan moto?"}),
        ("finish_screening", {}),
        ("record_backout", {"kind": "withdrawn", "note": "Encontró otro trabajo."}),
        ("offer_interview_slots", {"reschedule": True}),
        ("book_interview", {"slot_ref": "so_7f3k2"}),
    ],
)
def test_every_tool_posts_to_its_own_path(
    handlers: dict[str, Handler],
    backend: RecordingBackend,
    contract: dict[str, Any],
    tool: str,
    args: dict[str, Any],
) -> None:
    call(handlers, tool, args, "app_X")
    request = backend.requests[-1]
    assert request.path == f"/internal/tools/{tool}"
    assert request.body == {"tool": tool, "args": args, "session_id": "app_X"}
    jsonschema.validate(request.body, contract)


def test_non_ascii_args_survive_the_round_trip(
    handlers: dict[str, Handler], backend: RecordingBackend
) -> None:
    call(handlers, "record_answer", {"field": "full_name", "value": "María Núñez-Ibáñez"}, "app_1")
    assert backend.requests[0].body["args"]["value"] == "María Núñez-Ibáñez"


def test_session_id_comes_from_the_dispatcher_never_from_args(
    handlers: dict[str, Handler], backend: RecordingBackend
) -> None:
    """A model-supplied application_id (spike 2, finding 7) is forwarded as an ordinary
    argument, for the backend's schema check to reject; it never becomes the session."""
    call(
        handlers,
        "record_answer",
        {"field": "zone", "value": "Centro", "application_id": "app_VICTIM", "session_id": "evil"},
        "app_REAL",
    )
    body = backend.requests[0].body
    assert body["session_id"] == "app_REAL"
    assert body["args"]["application_id"] == "app_VICTIM"
    assert set(body) == {"tool", "args", "session_id"}


def test_http_200_tool_error_passes_through_unchanged(
    handlers: dict[str, Handler], backend: RecordingBackend
) -> None:
    error = {
        "ok": False,
        "error": "invalid_value",
        "hint": "The name needs at least a first name and a surname.",
        "attempt": 1,
    }
    backend.reply = lambda _r: Reply(200, error)
    assert call(handlers, "record_answer", {"field": "full_name", "value": "Ana"}, "app_1") == error


def test_http_422_invalid_args_passes_through(
    handlers: dict[str, Handler], backend: RecordingBackend
) -> None:
    error = {"ok": False, "error": "invalid_args", "hint": "Unexpected property application_id."}
    backend.reply = lambda _r: Reply(422, error)
    assert call(handlers, "record_answer", {"bogus": 1}, "app_1") == error


def test_result_is_a_json_string_with_unicode_intact(
    handlers: dict[str, Handler], backend: RecordingBackend
) -> None:
    ok = {"ok": True, "value": {"status": "knockout", "message": "Gracias, Luis. Por ahora… ¿Sí?"}}
    backend.reply = lambda _r: Reply(200, ok)
    raw = handlers["record_answer"]({"field": "zone", "value": "x"}, session_id="app_1")
    assert isinstance(raw, str)
    assert "¿Sí?" in raw
    assert json.loads(raw) == ok


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 502, 503])
def test_other_http_statuses_become_an_internal_error(
    handlers: dict[str, Handler], backend: RecordingBackend, contract: dict[str, Any], status: int
) -> None:
    backend.reply = lambda _r: Reply(status, {"detail": "nope"})
    result = call(handlers, "get_application", {}, "app_1")
    assert result["ok"] is False
    assert result["error"] == "internal"
    assert str(status) in result["hint"]
    jsonschema.validate(result, {"$ref": "#/$defs/tool_error", "$defs": contract["$defs"]})


@pytest.mark.parametrize(
    "body", [b"not json", b"[1, 2]", b'{"value": {}}', b'{"ok": "yes"}', b"", b"\xff\xfe"]
)
def test_unreadable_200_body_becomes_an_internal_error(
    handlers: dict[str, Handler], backend: RecordingBackend, body: bytes
) -> None:
    backend.reply = lambda _r: Reply(200, body)
    result = call(handlers, "get_application", {}, "app_1")
    assert result["ok"] is False
    assert result["error"] == "internal"


def test_unreachable_backend_becomes_an_internal_error_not_an_exception(
    handlers: dict[str, Handler], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRIBA_BACKEND_URL", "http://127.0.0.1:9")  # discard port
    monkeypatch.setenv("CRIBA_TOOLS_SECRET", "s")
    started = time.monotonic()
    result = call(handlers, "get_application", {}, "app_1")
    assert result["ok"] is False
    assert result["error"] == "internal"
    assert "do not guess" in result["hint"]
    assert time.monotonic() - started < 5


def test_a_slow_backend_times_out_with_an_internal_error(
    handlers: dict[str, Handler], backend: RecordingBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRIBA_TOOLS_TIMEOUT", "0.3")
    backend.reply = lambda _r: Reply(200, {"ok": True, "value": {}}, delay=1.5)
    started = time.monotonic()
    result = call(handlers, "get_application", {}, "app_1")
    assert result["error"] == "internal"
    assert time.monotonic() - started < 1.2


@pytest.mark.parametrize("session_id", [None, ""])
def test_no_session_id_never_reaches_the_backend(
    handlers: dict[str, Handler], backend: RecordingBackend, session_id: Any
) -> None:
    result = json.loads(handlers["get_application"]({}, session_id=session_id))
    assert result["ok"] is False
    assert result["error"] == "unknown_session"
    assert backend.requests == []


def test_missing_secret_is_an_internal_error_and_sends_nothing(
    handlers: dict[str, Handler],
    backend: RecordingBackend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("CRIBA_TOOLS_SECRET")
    monkeypatch.setenv("CRIBA_TOOLS_SECRET_FILE", str(tmp_path / "does-not-exist"))
    result = call(handlers, "get_application", {}, "app_1")
    assert result["error"] == "internal"
    assert backend.requests == []


def test_secret_is_read_from_the_compose_secret_file(
    handlers: dict[str, Handler],
    backend: RecordingBackend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret_file = tmp_path / "criba_tools_secret"
    secret_file.write_text("from-the-file\n", encoding="utf-8")
    monkeypatch.delenv("CRIBA_TOOLS_SECRET")
    monkeypatch.setenv("CRIBA_TOOLS_SECRET_FILE", str(secret_file))
    call(handlers, "get_application", {}, "app_1")
    assert backend.requests[0].headers["authorization"] == "Bearer from-the-file"

    secret_file.write_text("rotated\n", encoding="utf-8")  # picked up without a restart
    call(handlers, "get_application", {}, "app_1")
    assert backend.requests[1].headers["authorization"] == "Bearer rotated"


def test_the_secret_never_appears_in_logs(
    handlers: dict[str, Handler], backend: RecordingBackend, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG", logger="criba_tools")
    backend.reply = lambda _r: Reply(401, {"detail": "bad secret"})
    call(handlers, "record_answer", {"field": "full_name", "value": "María Núñez"}, "app_1")
    text = caplog.text
    assert "unit-test-secret" not in text
    assert "María" not in text, "Candidate answers must not be logged"
    assert "record_answer" in text
    assert "app_1" in text


def test_default_backend_url_matches_the_compose_service(client_module: ModuleType) -> None:
    assert client_module.DEFAULT_BACKEND_URL == "http://backend:8000"
    assert client_module.DEFAULT_SECRET_FILE == "/run/secrets/criba_tools_secret"
