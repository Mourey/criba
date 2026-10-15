"""The signed post-call webhook (spec voice-agent session-webhook: SE11, SE12; parent R19).

Fixtures are the probe's eight webhook captures
(``src/tests/fixtures/spikes/005-elevenlabs-custom-llm``),
rebuilt with planted markers, serialized with ``indent=1`` (whitespace a re-serializer would
not reproduce) and signed in the test. No ElevenLabs call and no real model. Secret test
values are low-entropy words written to ``tmp_path`` files.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import sqlite3
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
import jsonschema
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from criba.channel.elevenlabs import FakeElevenLabsClient
from criba.channel.service import Channel
from criba.channel.voice_config import VoiceSettings
from criba.channel.voice_session import VoiceRuntime, end_voice_session
from criba.channel.voice_webhook import (
    MAX_BODY_BYTES,
    SIGNATURE_HEADER,
    TOLERANCE_S,
    voice_webhook_router,
)
from criba.db import repo
from criba.db.connection import iso

from .conftest import NOW

ROOT = Path(__file__).resolve().parents[4]
CAPTURES = ROOT / "src" / "tests" / "fixtures" / "spikes" / "005-elevenlabs-custom-llm" / "captures"
CAPTURE_IDS = ("004", "010", "015", "020", "024", "027", "030", "033")
CONTRACT = ROOT / "contracts" / "voice.openapi.yaml"
PATH = "/api/voice/webhooks/post-call"
LOGGER = "criba.channel.voice_webhook"
SECRET_VAR = "CRIBA_ELEVENLABS_WEBHOOK_SECRET_FILE"

# Low-entropy words, joined at runtime so no literal looks like a credential.
WEBHOOK_SECRET = "-".join(("webhook", "secret", "for", "tests"))
OTHER_SECRET = "-".join(("another", "secret", "for", "tests"))
TRANSCRIPT_MARKER = "transcript-marker-for-tests"
TOKEN_MARKER = "voice-token-marker-for-tests"

AGENT_ES = "agent-es-for-tests"
AGENT_MX = "agent-mx-for-tests"
CONV = "conv-eleven-for-tests"
SESSION = "vs_" + "1" * 32
OTHER_SESSION = "vs_" + "2" * 32
T0 = int(NOW.timestamp())
UNAUTHORIZED = {"error": "unauthorized", "message": "Invalid credentials."}
NOT_FOUND = {"error": "not_found", "message": "Not found."}
TOO_LARGE = {"error": "payload_too_large", "message": "Request body too large."}
INVALID = {"error": "validation_error", "message": "Invalid webhook payload."}
RECEIVED = {"status": "received"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    return loaded


def _validate(contract: dict[str, Any], name: str, value: object) -> None:
    schema = {"$ref": f"#/components/schemas/{name}", "components": contract["components"]}
    jsonschema.Draft202012Validator(schema).validate(value)


def _response_schema(contract: dict[str, Any], status: int) -> dict[str, Any]:
    """The JSON schema ``voicePostCallWebhook`` declares for ``status``."""
    response = contract["paths"][PATH]["post"]["responses"][str(status)]
    if "$ref" in response:
        response = contract["components"]["responses"][response["$ref"].rsplit("/", 1)[1]]
    schema: dict[str, Any] = response["content"]["application/json"]["schema"]
    return schema


def _placeholder(value: str) -> bool:
    return value.startswith("<") and value.endswith(">")


def _plant(node: Any) -> Any:
    """Every redacted placeholder string becomes the transcript marker."""
    if isinstance(node, dict):
        return {key: _plant(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_plant(value) for value in node]
    if isinstance(node, str) and _placeholder(node):
        return TRANSCRIPT_MARKER
    return node


def _set_path(body: dict[str, Any], dotted: str, value: Any) -> None:
    keys = dotted.split("__")
    node = body
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    if value is _DROP:
        node.pop(keys[-1], None)
    else:
        node[keys[-1]] = value


_DROP = object()


def sign(raw: bytes, *, secret: str = WEBHOOK_SECRET, t: int = T0) -> str:
    digest = hmac.new(secret.encode("utf-8"), str(t).encode("ascii") + b"." + raw, hashlib.sha256)
    return f"t={t},v0={digest.hexdigest()}"


def capture_body(capture: str = "004") -> dict[str, Any]:
    loaded = json.loads((CAPTURES / f"{capture}-post-call-webhook.json").read_text("utf-8"))
    body: dict[str, Any] = copy.deepcopy(loaded["body"])
    return body


def signed_webhook(
    capture: str = "004",
    *,
    agent_id: str = AGENT_ES,
    conversation_id: str = CONV,
    secret: str = WEBHOOK_SECRET,
    t: int = T0,
    **overrides: Any,
) -> tuple[bytes, dict[str, str]]:
    """A capture's body with our ids and planted markers, validated against
    ``PostCallWebhook``, serialized with ``indent=1`` and signed. ``overrides`` set (or,
    with ``_DROP``, remove) a value by its ``__``-joined path, after validation."""
    body = _plant(capture_body(capture))
    body["data"]["agent_id"] = agent_id
    body["data"]["conversation_id"] = conversation_id
    client_data = body["data"].setdefault("conversation_initiation_client_data", {}) or {}
    body["data"]["conversation_initiation_client_data"] = client_data
    client_data.setdefault("custom_llm_extra_body", {})["criba_voice_token"] = TOKEN_MARKER
    schema = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    _validate(schema, "PostCallWebhook", body)
    for dotted, value in overrides.items():
        _set_path(body, dotted, value)
    raw = json.dumps(body, indent=1).encode("utf-8")
    return raw, {SIGNATURE_HEADER: sign(raw, secret=secret, t=t)}


def signed_raw(raw: bytes, *, secret: str = WEBHOOK_SECRET, t: int = T0) -> dict[str, str]:
    return {SIGNATURE_HEADER: sign(raw, secret=secret, t=t)}


def _settings(tmp_path: Path, **overrides: str | None) -> VoiceSettings:
    files = {
        "CRIBA_ELEVENLABS_API_KEY_FILE": ("api_key.txt", "api-key-for-tests"),
        "CRIBA_VOICE_SECRET_FILE": ("voice_secret.txt", "voice-secret-for-tests"),
        SECRET_VAR: ("webhook_secret.txt", WEBHOOK_SECRET),
    }
    env: dict[str, str] = {
        "CRIBA_VOICE": "on",
        "CRIBA_VOICE_AGENT_ES": AGENT_ES,
        "CRIBA_VOICE_AGENT_MX": AGENT_MX,
        "CRIBA_ENV": "test",
    }
    for variable, (name, value) in files.items():
        path = tmp_path / name
        path.write_text(f"{value}\n", encoding="utf-8")
        env[variable] = str(path)
    for variable, value in overrides.items():
        if value is None:
            env.pop(variable, None)
        else:
            env[variable] = value
    return VoiceSettings.from_env(env)


def make_app(channel: Channel, settings: VoiceSettings) -> FastAPI:
    app = FastAPI()
    app.include_router(voice_webhook_router, prefix="/api/voice")
    app.state.criba_channel = channel
    app.state.criba_voice = VoiceRuntime(settings, FakeElevenLabsClient())
    return app


@pytest.fixture
def settings(tmp_path: Path) -> VoiceSettings:
    return _settings(tmp_path)


@pytest.fixture
def app(channel: Channel, settings: VoiceSettings) -> FastAPI:
    return make_app(channel, settings)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def post(client: TestClient, raw: bytes, headers: dict[str, str]) -> Any:
    return client.post(PATH, content=raw, headers={"Content-Type": "application/json", **headers})


def asgi_post(app: FastAPI, chunks: list[bytes], headers: dict[str, str]) -> tuple[int, Any, int]:
    """POST ``chunks`` straight to the ASGI app, no network. Returns the status, the JSON
    body and how many chunks the app took from ``receive``."""
    taken = 0
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal taken
        if taken < len(chunks):
            taken += 1
            return {
                "type": "http.request",
                "body": chunks[taken - 1],
                "more_body": taken < len(chunks),
            }
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": PATH,
        "raw_path": PATH.encode("ascii"),
        "root_path": "",
        "query_string": b"",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }

    async def run() -> None:
        await app(scope, receive, send)

    anyio.run(run)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return int(start["status"]), json.loads(body), taken


def _candidate(conn: sqlite3.Connection, external_id: str = "wc_" + "a" * 32) -> str:
    candidate, _ = repo.get_or_create_candidate(
        conn,
        client_id="grupo_sazon",
        channel="webchat",
        external_id=external_id,
        country="ES",
        language="es-ES",
        now=NOW,
    )
    return str(candidate["id"])


def plant_session(
    conn: sqlite3.Connection,
    session_id: str = SESSION,
    *,
    elevenlabs_conversation_id: str | None = CONV,
    candidate_id: str | None = None,
) -> str:
    """A ``voice_sessions`` row for ``elevenlabs_conversation_id``; returns its Candidate."""
    candidate = candidate_id or _candidate(conn)
    conn.execute(
        "INSERT INTO voice_sessions (id, conversation_id, candidate_id, country,"
        " elevenlabs_conversation_id, started_at) VALUES (?, ?, ?, 'ES', ?, ?)",
        (
            session_id,
            "wc_" + "a" * 32,
            candidate,
            elevenlabs_conversation_id,
            iso(NOW - timedelta(minutes=1)),
        ),
    )
    return candidate


def speech_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM usage WHERE kind = 'speech' ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def session_row(conn: sqlite3.Connection, session_id: str = SESSION) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM voice_sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row)


def assert_nothing_written(conn: sqlite3.Connection, session_id: str = SESSION) -> None:
    assert speech_rows(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 0
    session = session_row(conn, session_id)
    assert session["ended_at"] is None
    assert session["seconds"] is None


# ---------------------------------------------------------------------------
# Verification (SE11)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"CRIBA_VOICE": "off"},
        {"CRIBA_VOICE_AGENT_ES": ""},
        {"CRIBA_VOICE_AGENT_MX": None},
        {"CRIBA_ELEVENLABS_API_KEY_FILE": None},
    ],
    ids=["off", "no-es-agent", "no-mx-agent", "no-api-key"],
)
def test_voice_off_is_404(
    channel: Channel,
    conn: sqlite3.Connection,
    tmp_path: Path,
    overrides: dict[str, str | None],
) -> None:
    plant_session(conn)
    client = TestClient(make_app(channel, _settings(tmp_path, **overrides)))
    raw, headers = signed_webhook()

    response = post(client, raw, headers)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert_nothing_written(conn)


@pytest.mark.parametrize("capture", CAPTURE_IDS)
def test_every_capture_verifies(client: TestClient, capture: str) -> None:
    loaded = json.loads((CAPTURES / f"{capture}-post-call-webhook.json").read_text("utf-8"))
    assert loaded["header_analysis"]["signature"]["hmac_sha256_verified"] is True
    raw, headers = signed_webhook(capture, conversation_id=f"conv-{capture}")

    response = post(client, raw, headers)

    assert response.status_code == 200
    assert response.json() == RECEIVED


def _flip_hex(header: str) -> str:
    last = header[-1]
    return header[:-1] + ("0" if last != "0" else "1")


def _bad_signatures() -> list[tuple[str, Callable[[bytes, str], tuple[bytes, dict[str, str]]]]]:
    def pair(raw: bytes, header: str | None) -> tuple[bytes, dict[str, str]]:
        return raw, ({} if header is None else {SIGNATURE_HEADER: header})

    def hex_of(header: str) -> str:
        return header.split(",v0=")[1]

    return [
        ("no-header", lambda raw, good: pair(raw, None)),
        ("empty-header", lambda raw, good: pair(raw, "")),
        ("wrong-secret", lambda raw, good: pair(raw, sign(raw, secret=OTHER_SECRET))),
        ("one-hex-digit", lambda raw, good: pair(raw, _flip_hex(good))),
        ("body-changed", lambda raw, good: pair(raw.replace(b'"done"', b'"dona"'), good)),
        (
            "re-serialized",
            lambda raw, good: pair(
                json.dumps(json.loads(raw), separators=(",", ":")).encode("utf-8"), good
            ),
        ),
        ("v1", lambda raw, good: pair(raw, good.replace(",v0=", ",v1="))),
        ("no-t", lambda raw, good: pair(raw, f"v0={hex_of(good)}")),
        ("non-digit-t", lambda raw, good: pair(raw, f"t=17x,v0={hex_of(good)}")),
        ("uppercase-hex", lambda raw, good: pair(raw, f"t={T0},v0={hex_of(good).upper()}")),
        ("third-part", lambda raw, good: pair(raw, f"{good},v0={hex_of(good)}")),
        ("space-after-comma", lambda raw, good: pair(raw, good.replace(",", ", "))),
    ]


@pytest.mark.parametrize(
    "case", [c for _, c in _bad_signatures()], ids=[n for n, _ in _bad_signatures()]
)
def test_bad_signatures_are_401(
    client: TestClient,
    conn: sqlite3.Connection,
    case: Callable[[bytes, str], tuple[bytes, dict[str, str]]],
) -> None:
    plant_session(conn)
    raw, headers = signed_webhook()
    sent, bad = case(raw, headers[SIGNATURE_HEADER])

    response = post(client, sent, bad)

    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED
    assert_nothing_written(conn)


@pytest.mark.parametrize(
    ("offset", "status"),
    [(-TOLERANCE_S, 200), (TOLERANCE_S, 200), (-TOLERANCE_S - 1, 401), (TOLERANCE_S + 1, 401)],
)
def test_the_window_is_five_minutes(
    client: TestClient, conn: sqlite3.Connection, offset: int, status: int
) -> None:
    assert TOLERANCE_S == 300
    plant_session(conn)
    raw, headers = signed_webhook(t=T0 + offset, agent_id="agent-not-ours")

    response = post(client, raw, headers)

    assert response.status_code == status
    if status == 401:
        assert response.json() == UNAUTHORIZED
    assert_nothing_written(conn)


KIB = 1024


def test_the_body_cap_ignores_content_length(app: FastAPI, conn: sqlite3.Connection) -> None:
    assert MAX_BODY_BYTES == 256 * KIB
    plant_session(conn)
    chunk = b" " * (64 * KIB)

    lying = [chunk] * 4 + [b" " * (300 * KIB - 4 * 64 * KIB)]
    status, body, taken = asgi_post(
        app, lying, {"content-length": "100", SIGNATURE_HEADER: sign(b"".join(lying))}
    )
    assert (status, body) == (413, TOO_LARGE)
    assert taken <= 5
    assert_nothing_written(conn)

    over = [chunk] * 4 + [b" "]
    status, body, _ = asgi_post(
        app,
        over,
        {"content-length": str(MAX_BODY_BYTES + 1), SIGNATURE_HEADER: sign(b"".join(over))},
    )
    assert (status, body) == (413, TOO_LARGE)
    assert_nothing_written(conn)

    exact = [chunk] * 4
    status, body, _ = asgi_post(
        app,
        exact,
        {"content-length": str(MAX_BODY_BYTES), SIGNATURE_HEADER: sign(b"x")},
    )
    assert (status, body) == (401, UNAUTHORIZED)
    assert_nothing_written(conn)


@pytest.mark.parametrize("how", ["unset", "missing-file", "empty-file"])
def test_an_empty_secret_refuses_every_call(
    channel: Channel,
    conn: sqlite3.Connection,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    how: str,
) -> None:
    plant_session(conn)
    secret_path = tmp_path / "empty_webhook_secret.txt"
    if how == "empty-file":
        secret_path.write_text("\n", encoding="utf-8")
    value = None if how == "unset" else str(secret_path)
    client = TestClient(make_app(channel, _settings(tmp_path, **{SECRET_VAR: value})))
    raw, headers = signed_webhook()

    for _ in range(2):
        caplog.clear()
        with caplog.at_level("DEBUG", logger=LOGGER):
            response = post(client, raw, headers)
        assert response.status_code == 401
        assert response.json() == UNAUTHORIZED
        errors = [r for r in caplog.records if r.name == LOGGER and r.levelname == "ERROR"]
        assert len(errors) == 1
        message = errors[0].getMessage()
        assert SECRET_VAR in message
        assert str(secret_path) not in message
        assert WEBHOOK_SECRET not in message
    assert_nothing_written(conn)


@pytest.mark.parametrize(
    "raw_or_overrides",
    [
        b"not json",
        b"\xff\xfe not utf-8",
        b"[]",
        b'"post_call_transcription"',
        b'{"type": 7, "data": {}}',
        {"data__conversation_id": _DROP},
        {"data__conversation_id": ""},
        {"data__agent_id": ""},
        {"data__agent_id": 12},
        {"data": "not an object"},
        {"data__metadata": _DROP},
        {"data__metadata__call_duration_secs": -1},
        {"data__metadata__call_duration_secs": "24"},
        {"data__metadata__call_duration_secs": True},
        {"data__metadata__call_duration_secs": 24.5},
        {"data__metadata__call_duration_secs": _DROP},
        {"data__metadata__call_duration_secs": 10**400},
    ],
    ids=[
        "not-json",
        "not-utf8",
        "array",
        "string",
        "type-not-string",
        "no-conversation-id",
        "empty-conversation-id",
        "empty-agent-id",
        "numeric-agent-id",
        "data-not-object",
        "no-metadata",
        "negative-duration",
        "string-duration",
        "boolean-duration",
        "float-duration",
        "no-duration",
        "duration-past-int64",
    ],
)
def test_a_bad_payload_after_the_signature_is_400(
    client: TestClient, conn: sqlite3.Connection, raw_or_overrides: bytes | dict[str, Any]
) -> None:
    plant_session(conn)
    if isinstance(raw_or_overrides, bytes):
        raw, headers = raw_or_overrides, signed_raw(raw_or_overrides)
    else:
        raw, headers = signed_webhook(**raw_or_overrides)

    response = post(client, raw, headers)

    assert response.status_code == 400
    assert response.json() == INVALID
    assert_nothing_written(conn)


def test_the_order_of_checks(
    channel: Channel, app: FastAPI, client: TestClient, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    plant_session(conn)
    oversize = [b" " * (64 * KIB)] * 5

    off = make_app(channel, _settings(tmp_path, CRIBA_VOICE="off"))
    assert asgi_post(off, oversize, {})[:2] == (404, NOT_FOUND)

    assert asgi_post(app, oversize, {})[:2] == (413, TOO_LARGE)

    response = post(client, b"not json", {})
    assert (response.status_code, response.json()) == (401, UNAUTHORIZED)

    stale = post(client, b"not json", signed_raw(b"not json", t=T0 - TOLERANCE_S - 1))
    assert (stale.status_code, stale.json()) == (401, UNAUTHORIZED)

    wrong = post(client, b"not json", signed_raw(b"not json", secret=OTHER_SECRET))
    assert (wrong.status_code, wrong.json()) == (401, UNAUTHORIZED)
    assert_nothing_written(conn)


def test_a_foreign_agent_is_ignored(client: TestClient, conn: sqlite3.Connection) -> None:
    plant_session(conn)
    raw, headers = signed_webhook(agent_id="agent-of-someone-else")

    response = post(client, raw, headers)

    assert response.status_code == 200
    assert response.json() == RECEIVED
    assert_nothing_written(conn)


def test_other_event_types_are_ignored(client: TestClient, conn: sqlite3.Connection) -> None:
    plant_session(conn)
    audio = json.dumps(
        {"type": "post_call_audio", "event_timestamp": T0, "data": {"conversation_id": CONV}},
        indent=1,
    ).encode("utf-8")
    failure = json.dumps(
        {
            "type": "call_initiation_failure",
            "event_timestamp": T0,
            "data": {"agent_id": AGENT_ES, "conversation_id": CONV, "failure_reason": "busy"},
        },
        indent=1,
    ).encode("utf-8")

    for raw in (audio, failure):
        response = post(client, raw, signed_raw(raw))
        assert response.status_code == 200
        assert response.json() == RECEIVED
    assert_nothing_written(conn)


def test_error_bodies_match_the_contract(
    channel: Channel,
    client: TestClient,
    conn: sqlite3.Connection,
    tmp_path: Path,
    contract: dict[str, Any],
) -> None:
    raw, headers = signed_webhook()
    off = TestClient(make_app(channel, _settings(tmp_path, CRIBA_VOICE="off")))
    oversize = b" " * (MAX_BODY_BYTES + 1)
    answers = [
        post(client, raw, headers),
        post(client, raw, {}),
        post(off, raw, headers),
        post(client, oversize, signed_raw(oversize)),
        post(client, b"[]", signed_raw(b"[]")),
    ]

    assert [a.status_code for a in answers] == [200, 401, 404, 413, 400]
    for answer in answers:
        schema = {
            **_response_schema(contract, answer.status_code),
            "components": contract["components"],
        }
        jsonschema.Draft202012Validator(schema).validate(answer.json())
    _validate(contract, "WebhookAck", answers[0].json())
    for answer in answers[1:]:
        _validate(contract, "VoiceError", answer.json())


# ---------------------------------------------------------------------------
# The ledger write (SE12)
# ---------------------------------------------------------------------------


def spoken_turn(
    conn: sqlite3.Connection,
    candidate_id: str,
    application_id: str | None,
    client_message_id: str,
) -> None:
    repo.add_message(
        conn,
        candidate_id=candidate_id,
        application_id=application_id,
        channel="voice",
        direction="inbound",
        sender="candidate",
        body="spoken turn",
        client_message_id=client_message_id,
        modality="spoken",
        now=NOW,
    )


def test_a_valid_call_writes_one_speech_row(client: TestClient, conn: sqlite3.Connection) -> None:
    candidate = plant_session(conn)
    application = repo.start_application(conn, candidate, "webchat", now=NOW)
    spoken_turn(conn, candidate, application["id"], f"vc_{SESSION}_1")
    raw, headers = signed_webhook("004")

    response = post(client, raw, headers)

    assert response.status_code == 200
    assert response.json() == RECEIVED
    [row] = speech_rows(conn)
    row.pop("id")
    assert row == {
        "ts": iso(NOW),
        "environment": "test",
        "source": "live",
        "dataset": None,
        "client_id": None,
        "location_id": None,
        "candidate_id": candidate,
        "application_id": application["id"],
        "stage": None,
        "profile": None,
        "run_id": None,
        "session_id": SESSION,
        "kind": "speech",
        "provider": "elevenlabs",
        "requested_model": None,
        "model": "elevenlabs-convai",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "audio_seconds": 24.0,
        "est_cost_usd": 0.032,
        "reconciled_cost_usd": None,
        "latency_ms": None,
        "status": "ok",
        "error": None,
    }
    session = session_row(conn)
    assert session["ended_at"] == iso(NOW)
    assert session["seconds"] == 24.0


def test_the_price_follows_the_setting(
    channel: Channel, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    plant_session(conn)
    client = TestClient(make_app(channel, _settings(tmp_path, CRIBA_VOICE_COST_PER_MIN="0.5")))
    raw, headers = signed_webhook("004")

    assert post(client, raw, headers).status_code == 200

    [row] = speech_rows(conn)
    assert row["est_cost_usd"] == 0.2


def test_a_replay_writes_nothing_new(client: TestClient, conn: sqlite3.Connection) -> None:
    plant_session(conn)
    raw, headers = signed_webhook("004")
    later_raw, later_headers = signed_webhook("004", t=T0 + 60)

    answers = [post(client, raw, headers), post(client, raw, headers)]
    answers.append(post(client, later_raw, later_headers))

    assert [(a.status_code, a.json()) for a in answers] == [(200, RECEIVED)] * 3
    assert len(speech_rows(conn)) == 1
    assert session_row(conn)["seconds"] == 24.0


def test_a_session_ended_by_a_takeover_still_gets_its_row(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    plant_session(conn)
    ended = NOW - timedelta(seconds=30)
    end_voice_session(conn, SESSION, now=ended)
    raw, headers = signed_webhook("010")

    assert post(client, raw, headers).status_code == 200

    [row] = speech_rows(conn)
    assert row["audio_seconds"] == 34.0
    session = session_row(conn)
    assert session["ended_at"] == iso(ended)
    assert session["seconds"] is None


def test_no_spoken_turn_leaves_the_application_null(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    candidate = plant_session(conn)
    repo.start_application(conn, candidate, "webchat", now=NOW)
    raw, headers = signed_webhook("004")

    assert post(client, raw, headers).status_code == 200

    [row] = speech_rows(conn)
    assert row["candidate_id"] == candidate
    assert row["application_id"] is None


def test_the_application_comes_from_this_sessions_turns(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    candidate = plant_session(conn)
    plant_session(conn, OTHER_SESSION, elevenlabs_conversation_id=None, candidate_id=candidate)
    earlier = repo.start_application(conn, candidate, "webchat", now=NOW)
    repo.close_application(conn, earlier["id"], "abandoned", now=NOW)
    current = repo.start_application(conn, candidate, "webchat", now=NOW)
    spoken_turn(conn, candidate, earlier["id"], f"vc_{OTHER_SESSION}_1")
    spoken_turn(conn, candidate, earlier["id"], f"vcx{SESSION}_1")
    spoken_turn(conn, candidate, earlier["id"], f"vc_{SESSION}x1")
    spoken_turn(conn, candidate, None, f"vc_{SESSION}_9")
    raw, headers = signed_webhook("004")

    assert post(client, raw, headers).status_code == 200
    [row] = speech_rows(conn)
    assert row["application_id"] is None

    conn.execute("DELETE FROM usage")
    conn.execute("UPDATE voice_sessions SET ended_at = NULL, seconds = NULL")
    spoken_turn(conn, candidate, current["id"], f"vc_{SESSION}_2")
    assert post(client, raw, headers).status_code == 200
    [row] = speech_rows(conn)
    assert row["application_id"] == current["id"]


def test_an_unknown_conversation_writes_nothing(
    client: TestClient, conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    plant_session(conn, elevenlabs_conversation_id="conv-somebody-else")
    raw, headers = signed_webhook("004")

    with caplog.at_level("DEBUG", logger=LOGGER):
        response = post(client, raw, headers)

    assert response.status_code == 200
    assert response.json() == RECEIVED
    assert_nothing_written(conn)
    warnings = [r for r in caplog.records if r.name == LOGGER and r.levelname == "WARNING"]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    for value in (CONV, AGENT_ES, SESSION):
        assert value not in message


def _windows(data: bytes, size: int = 16) -> set[bytes]:
    return {data[i : i + size] for i in range(max(0, len(data) - size + 1))}


def test_no_payload_value_is_stored_or_logged(
    client: TestClient, conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    candidate = plant_session(conn)
    application = repo.start_application(conn, candidate, "webchat", now=NOW)
    spoken_turn(conn, candidate, application["id"], f"vc_{SESSION}_1")
    raw, headers = signed_webhook("004")
    signature_hex = headers[SIGNATURE_HEADER].split(",v0=")[1]
    sent: list[tuple[bytes, dict[str, str], int]] = [(raw, headers, 200), (raw, headers, 200)]
    for _, case in _bad_signatures():
        bad_raw, bad_headers = case(raw, headers[SIGNATURE_HEADER])
        sent.append((bad_raw, bad_headers, 401))
    bad_payload, _ = signed_webhook("004", data__metadata__call_duration_secs=-1)
    sent.append((bad_payload, signed_raw(bad_payload), 400))
    oversize = raw + b" " * (MAX_BODY_BYTES - len(raw) + 1)
    sent.append((oversize, signed_raw(oversize), 413))
    unknown, _ = signed_webhook("010", conversation_id="conv-nobody")
    sent.append((unknown, signed_raw(unknown), 200))

    with caplog.at_level("DEBUG"):
        for body, body_headers, status in sent:
            assert post(client, body, body_headers).status_code == status

    assert len(speech_rows(conn)) == 1
    raw_windows = _windows(raw)
    forbidden = [TRANSCRIPT_MARKER, TOKEN_MARKER, CONV, AGENT_ES, signature_hex]
    assert any(r.name == LOGGER for r in caplog.records), "the scan saw no webhook record"
    for record in caplog.records:
        text = record.getMessage() + (record.exc_text or "")
        for value in forbidden:
            assert value not in text, record.name
        assert not (_windows(text.encode("utf-8")) & raw_windows), record.name
    for line in conn.iterdump():
        assert TRANSCRIPT_MARKER not in line
        assert TOKEN_MARKER not in line


# ---------------------------------------------------------------------------
# The mount (integrator commit to ``criba/app.py``)
# ---------------------------------------------------------------------------


def test_the_webhook_path_reaches_its_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the real app with voice off, the path answers this router's ``VoiceError`` 404,
    not FastAPI's ``{"detail": "Not Found"}`` for an unmounted path."""
    from criba.app import create_app
    from criba.wiring import build_runtime

    key = tmp_path / "hermes_key.txt"
    key.write_text("test-key\n", encoding="utf-8")
    for variable in ("CRIBA_ENV", "CRIBA_HERMES_PROFILE", "CRIBA_VOICE"):
        monkeypatch.delenv(variable, raising=False)
    env = {
        "CRIBA_DB_PATH": str(tmp_path / "criba.db"),
        "CRIBA_ENV": "test",
        "CRIBA_HERMES_API_KEY_FILE": str(key),
    }
    raw, headers = signed_webhook()

    with TestClient(create_app(lambda: build_runtime(env))) as real:
        response = post(real, raw, headers)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.parametrize(
    "failure",
    [sqlite3.OperationalError("database is locked"), RuntimeError("boom")],
    ids=["busy", "unexpected"],
)
def test_a_write_that_fails_on_our_side_asks_for_a_retry(
    client: TestClient,
    conn: sqlite3.Connection,
    contract: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    """No bare 500: a ``rate_limited`` VoiceError with ``Retry-After`` (the contract's one "try
    again later"), nothing written, and nothing of the error in the body."""
    from criba.channel import voice_webhook

    plant_session(conn)

    def fail(*_: Any) -> None:
        raise failure

    monkeypatch.setattr(voice_webhook, "_record_speech", fail)
    raw, headers = signed_webhook("004")

    response = post(client, raw, headers)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == str(voice_webhook.RETRY_AFTER_S)
    _validate(contract, "VoiceError", response.json())
    assert response.json()["error"] == "rate_limited"
    assert "boom" not in response.text and "locked" not in response.text
    assert_nothing_written(conn)
