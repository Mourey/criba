"""The web chat never answers with a bare 500 or FastAPI's ``{"detail": ...}``: every refusal and
every failure is the contract's ``Error`` (``contracts/webchat.openapi.yaml``), with nothing of
the exception or the Candidate's text in it (lane B, error envelope)."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from criba.channel.errors import INT64_MAX, guarded_route, is_busy
from criba.channel.service import Channel
from criba.channel.webchat import BUSY_RETRY_AFTER_S

from .conftest import start

ROOT = Path(__file__).resolve().parents[4]
CONTRACT = ROOT / "contracts" / "webchat.openapi.yaml"
SECRET_TEXT = "mi DNI es 12345678Z"


@pytest.fixture(scope="module")
def components() -> dict[str, Any]:
    return dict(yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["components"])


def assert_error(resp: Any, components: dict[str, Any], status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    schema = {"$ref": "#/components/schemas/Error", "components": components}
    jsonschema.Draft202012Validator(schema).validate(body)
    assert body["error"] == code
    assert "Traceback" not in resp.text and SECRET_TEXT not in resp.text


# --------------------------------------------------------------------------- integer bounds


@pytest.mark.parametrize(
    "raw",
    [
        b'{"message_id": "t1", "in_reply_to": 1e20, "option_id": "yes"}',
        b'{"message_id": "t2", "in_reply_to": 99999999999999999999, "option_id": "yes"}',
        b'{"message_id": "t3", "in_reply_to": 9223372036854775808, "option_id": "yes"}',
    ],
    ids=["float-1e20", "twenty-digits", "int64-max-plus-one"],
)
def test_a_tap_on_an_id_past_int64_is_a_422(
    client: TestClient, components: dict[str, Any], raw: bytes
) -> None:
    conv = start(client)
    resp = client.post(
        f"/api/chat/conversations/{conv}/replies",
        content=raw,
        headers={"content-type": "application/json"},
    )
    assert_error(resp, components, 422, "validation_error")


def test_a_tap_on_int64_max_is_an_unknown_message_not_a_crash(
    client: TestClient, components: dict[str, Any]
) -> None:
    conv = start(client)
    resp = client.post(
        f"/api/chat/conversations/{conv}/replies",
        json={"message_id": "t4", "in_reply_to": INT64_MAX, "option_id": "yes"},
    )
    assert_error(resp, components, 422, "validation_error")
    assert "in_reply_to" in resp.json()["message"]


@pytest.mark.parametrize(
    "query",
    [
        "after=99999999999999999999",
        "after=9223372036854775808",
        "after=1e20",
        "after=-1",
        "limit=abc",
        "limit=0",
        "wait=abc",
        "wait=99999999999999999999",
    ],
)
def test_a_bad_read_query_is_a_contract_422(
    client: TestClient, components: dict[str, Any], query: str
) -> None:
    conv = start(client)
    resp = client.get(f"/api/chat/conversations/{conv}/messages?{query}")
    assert_error(resp, components, 422, "validation_error")
    assert "detail" not in resp.json()


def test_int64_max_is_a_valid_cursor(client: TestClient) -> None:
    conv = start(client)
    resp = client.get(f"/api/chat/conversations/{conv}/messages?after={INT64_MAX}")
    assert resp.status_code == 200
    assert resp.json()["messages"] == []


# --------------------------------------------------------------------------- failures


def _failing_accept(channel: Channel, monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def fail(*_: Any, **__: Any) -> None:
        raise exc

    monkeypatch.setattr(channel.router, "accept_inbound", fail)


def test_a_locked_database_asks_the_client_to_send_again(
    client: TestClient, channel: Channel, components: dict[str, Any], monkeypatch
) -> None:
    conv = start(client)
    _failing_accept(channel, monkeypatch, sqlite3.OperationalError("database is locked"))
    resp = client.post(
        f"/api/chat/conversations/{conv}/messages",
        json={"message_id": "m1", "text": SECRET_TEXT},
    )
    assert_error(resp, components, 429, "rate_limited")
    assert resp.headers["Retry-After"] == str(BUSY_RETRY_AFTER_S)


def test_an_unexpected_error_is_a_json_500_logged_server_side(
    client: TestClient,
    channel: Channel,
    components: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    conv = start(client)
    _failing_accept(channel, monkeypatch, RuntimeError("boom"))
    with caplog.at_level(logging.ERROR, logger="criba.channel"):
        resp = client.post(
            f"/api/chat/conversations/{conv}/messages",
            json={"message_id": "m2", "text": SECRET_TEXT},
        )
    assert_error(resp, components, 500, "internal")
    assert "boom" not in resp.text
    [record] = [r for r in caplog.records if r.name == "criba.channel"]
    assert record.exc_info is not None and "boom" in str(record.exc_info[1])
    assert SECRET_TEXT not in caplog.text


def test_other_operational_errors_are_not_busy() -> None:
    assert is_busy(sqlite3.OperationalError("database is busy"))
    assert not is_busy(sqlite3.OperationalError("no such table: messages"))
    assert not is_busy(RuntimeError("database is locked"))


def test_an_http_exception_keeps_its_own_answer() -> None:
    router = APIRouter(
        route_class=guarded_route(
            invalid=lambda exc: pytest.fail("not a validation error"),
            busy=lambda: pytest.fail("not busy"),
            failed=lambda: pytest.fail("not a failure"),
        )
    )

    @router.get("/teapot")
    def teapot() -> None:
        raise HTTPException(status_code=418, detail="short and stout")

    app = FastAPI()
    app.include_router(router)
    resp = TestClient(app).get("/teapot")
    assert resp.status_code == 418
