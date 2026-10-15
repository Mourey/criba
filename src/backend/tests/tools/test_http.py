"""POST /internal/tools/{name}: shared secret, envelope, args schema, session resolution
(spec 002 task 11, backend half; contracts/tools.json $defs/tool_request and tool_response)."""

from __future__ import annotations

import pytest

from criba.tools import dispatch

from .conftest import GOOD_ANSWERS

AUTH = {"Authorization": "Bearer test-tools-secret"}


def post(client, tool: str, args=None, session_id: str = "app_x", headers=AUTH, **extra):
    body = {"tool": tool, "args": {} if args is None else args, "session_id": session_id}
    body.update(extra)
    return client.post(f"/internal/tools/{tool}", json=body, headers=headers)


@pytest.fixture
def app_id(client, db_path) -> str:
    from criba.db import connect, repo

    conn = connect(db_path)
    candidate, _ = repo.get_or_create_candidate(
        conn, client_id="grupo_sazon", channel="webchat", external_id="http-1", country="ES"
    )
    app_id = repo.start_application(conn, candidate["id"], "webchat")["id"]
    conn.close()
    return app_id


def test_round_trip(client, app_id) -> None:
    response = post(client, "get_application", session_id=app_id)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["value"]["next_field"] == "full_name"
    response = post(
        client,
        "record_answer",
        {"field": "full_name", "value": "Ana García"},
        session_id=app_id,
    )
    assert response.status_code == 200
    assert response.json()["value"]["status"] == "accepted"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Basic test-tools-secret"},
        {"Authorization": "Bearer "},
        {"Authorization": "test-tools-secret"},
    ],
)
def test_bad_secret_is_401(client, app_id, headers) -> None:
    response = post(client, "get_application", session_id=app_id, headers=headers)
    assert response.status_code == 401
    assert "ok" not in response.json()


def test_missing_secret_file_refuses_everything(client, app_id, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CRIBA_TOOLS_SECRET_FILE", str(tmp_path / "missing.txt"))
    assert post(client, "get_application", session_id=app_id).status_code == 401
    empty = tmp_path / "empty.txt"
    empty.write_text("\n")
    monkeypatch.setenv("CRIBA_TOOLS_SECRET_FILE", str(empty))
    assert post(client, "get_application", session_id=app_id).status_code == 401


def test_unknown_tool_is_404(client) -> None:
    response = client.post(
        "/internal/tools/delete_everything",
        json={"tool": "delete_everything", "args": {}, "session_id": "x"},
        headers=AUTH,
    )
    assert response.status_code == 404


def test_unknown_tool_without_secret_is_401(client) -> None:
    response = client.post("/internal/tools/delete_everything", json={})
    assert response.status_code == 401


def test_unknown_session(client) -> None:
    response = post(client, "get_application", session_id="app_nope")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "unknown_session"


def test_injected_application_id_is_invalid_args(client, app_id) -> None:
    """The model obeys injected ids (spike 2): identity only ever comes from session_id."""
    response = post(
        client,
        "record_answer",
        {"field": "full_name", "value": "Ana García", "application_id": app_id},
        session_id=app_id,
    )
    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "invalid_args"
    assert "application_id" in body["hint"]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("record_answer", {"field": "full_name"}),
        ("record_answer", {"field": "salary", "value": "1000"}),
        ("record_answer", {"field": "full_name", "value": ""}),
        ("record_answer", {"field": "full_name", "value": "x" * 501}),
        ("record_answer", {"field": "availability", "value": []}),
        ("record_answer", {"field": "availability", "value": [1]}),
        ("get_application", {"candidate_id": "cand_1"}),
        ("raise_escalation", {"reason": "bored"}),
        ("record_backout", {"kind": "withdrawn", "note": 3}),
        ("offer_interview_slots", {"reschedule": "yes"}),
        ("book_interview", {}),
        ("book_interview", {"slot_ref": "x" * 65}),
    ],
)
def test_args_failing_the_schema_are_422(client, app_id, tool, args) -> None:
    response = post(client, tool, args, session_id=app_id)
    assert response.status_code == 422, response.json()
    assert response.json()["error"] == "invalid_args"


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"tool": "get_application", "args": {}},
        {"tool": "get_application", "args": {}, "session_id": ""},
        {"tool": "get_application", "args": [], "session_id": "app_x"},
        {"tool": "record_answer", "args": {}, "session_id": "app_x"},
        {"tool": "get_application", "args": {}, "session_id": "app_x", "application_id": "a"},
    ],
)
def test_malformed_envelope_is_422(client, body) -> None:
    response = client.post("/internal/tools/get_application", json=body, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_args"


def test_body_that_is_not_json_is_422(client) -> None:
    response = client.post(
        "/internal/tools/get_application",
        content=b"{not json",
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_args"


def test_unexpected_failure_is_an_internal_tool_error(client, app_id, monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setitem(dispatch.TOOLS, "get_application", boom)
    response = post(client, "get_application", session_id=app_id)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "internal"
    assert "disk" not in body["hint"]


def test_router_uses_the_injected_clock(client, app_id, clock) -> None:
    clock.advance(days=3)  # Monday 12 October
    response = post(client, "get_application", session_id=app_id)
    assert response.json()["value"]["today"] == "2026-10-12"
    past = post(
        client, "record_answer", {"field": "start_date", "value": "2026-10-11"}, session_id=app_id
    )
    assert past.json()["reason"] == "past_start_date"
    # get_application sends the answers once every field is answered (turn-size.md).
    for field, value in GOOD_ANSWERS["ES"]:
        if field != "start_date":
            post(client, "record_answer", {"field": field, "value": value}, session_id=app_id)
    post(client, "record_answer", {"field": "start_date", "value": "2026-10-12"}, session_id=app_id)
    response = post(client, "get_application", session_id=app_id)
    assert response.json()["value"]["answers"]["start_date"] == "2026-10-12"
