"""The custom-LLM endpoint (spec voice-agent spoken-turn R4, R5.1, R5.3, R12, R16.4).

Request bodies are built from probe's captures
(``src/tests/fixtures/spikes/005-elevenlabs-custom-llm/captures``):
each ``<text:len=N>`` placeholder becomes test text and the voice token one minted with
``make_voice_token``. Every frame and error body is validated against
``contracts/voice.openapi.yaml``. Whole-response tests use ``TestClient``, which buffers the body;
tests that act mid-stream drive the ASGI app directly (:func:`asgi_stream`), with no network.
Secrets are low-entropy words written to ``tmp_path`` files. No model, no ElevenLabs.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
import jsonschema
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from criba.channel import voice as voice_module
from criba.channel.copy import spoken_handover
from criba.channel.elevenlabs import FakeElevenLabsClient
from criba.channel.sender import fallback_text
from criba.channel.service import Channel
from criba.channel.voice import (
    END_CALL_ARGUMENTS,
    FIRST_CHUNK,
    LINK_PHRASE,
    MODEL,
    SpeechScrubber,
    voice_router,
)
from criba.channel.voice_config import VoiceSettings
from criba.channel.voice_session import VoiceRuntime, make_voice_token
from criba.channel.webchat import RATE_LIMIT, RATE_WINDOW_S, _RateLimiter, chat_router
from criba.db import repo
from criba.db.connection import iso
from criba.tools.booking import make_booking_token

from .conftest import NOW, FakeHermes, GatedRunClient, sse, voice_session

ROOT = Path(__file__).resolve().parents[4]
CONTRACT = ROOT / "contracts" / "voice.openapi.yaml"
CAPTURES = ROOT / "src" / "tests" / "fixtures" / "spikes" / "005-elevenlabs-custom-llm" / "captures"
PATH = "/api/voice/v1/chat/completions"
LLM_SECRET = "voice-llm-secret-for-tests"
VOICE_SECRET = "voice-token-secret-for-tests"
CONV = "wc_voicellm"
SAID = "me llamo Ana y vivo en Madrid"
_PLACEHOLDER = re.compile(r"^<text:len=\d+>$")
# The capture with this many user entries (system first, as ElevenLabs sends it).
CAPTURE_BY_USERS = {1: "001", 2: "017", 3: "018", 4: "019"}


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def components() -> dict[str, Any]:
    return dict(yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["components"])


def check(components: dict[str, Any], name: str, value: object) -> None:
    schema = {"$ref": f"#/components/schemas/{name}", "components": components}
    jsonschema.Draft202012Validator(schema).validate(value)


def voice_settings(
    tmp_path: Path, *, llm: str | None = LLM_SECRET, enabled: str = "on", voice: str = VOICE_SECRET
) -> VoiceSettings:
    files = {"elevenlabs_api_key": "elevenlabs-key-for-tests", "voice": voice}
    paths = {}
    for name, value in files.items():
        path = tmp_path / f"{name}.txt"
        path.write_text(value, encoding="utf-8")
        paths[name] = str(path)
    llm_path = ""
    if llm is not None:
        llm_file = tmp_path / "llm.txt"
        llm_file.write_text(llm, encoding="utf-8")
        llm_path = str(llm_file)
    return VoiceSettings.from_env(
        {
            "CRIBA_VOICE": enabled,
            "CRIBA_VOICE_AGENT_ES": "agent_es_test",
            "CRIBA_VOICE_AGENT_MX": "agent_mx_test",
            "CRIBA_ENV": "test",
            "CRIBA_ELEVENLABS_API_KEY_FILE": paths["elevenlabs_api_key"],
            "CRIBA_VOICE_SECRET_FILE": paths["voice"],
            "CRIBA_VOICE_LLM_SECRET_FILE": llm_path,
        }
    )


def build_app(channel: Channel, settings: VoiceSettings) -> FastAPI:
    """As ``conftest.client`` builds its own: the chat router, the voice router, the state."""
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/chat")
    app.include_router(voice_router, prefix="/api/voice")
    app.state.criba_channel = channel
    app.state.criba_voice = VoiceRuntime(settings=settings, client=FakeElevenLabsClient())
    return app


@pytest.fixture
def app(spoken_channel: Channel, tmp_path: Path) -> FastAPI:
    return build_app(spoken_channel, voice_settings(tmp_path))


@pytest.fixture
def vclient(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def session(conn: Any) -> tuple[str, dict[str, Any]]:
    return voice_session(conn, CONV)


def token_for(session_id: str, *, expires_in: int = 1800, conversation: str = CONV) -> str:
    return make_voice_token(
        conversation,
        session_id,
        NOW + timedelta(seconds=expires_in),
        secret=VOICE_SECRET.encode("utf-8"),
    )


def fill(value: Any) -> Any:
    """Every ``<text:len=N>`` placeholder of a capture becomes plain test text."""
    if isinstance(value, dict):
        return {k: fill(v) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v) for v in value]
    if isinstance(value, str) and _PLACEHOLDER.match(value):
        return "texto de prueba"
    return value


def request_body(users: list[str], token: str | None, *, capture: str | None = None) -> dict:
    """A capture's body with the given user utterances (oldest first) and the voice token."""
    name = capture or CAPTURE_BY_USERS[len(users)]
    raw = json.loads((CAPTURES / f"{name}-custom-llm-request.json").read_text(encoding="utf-8"))
    body = fill(copy.deepcopy(raw["body"]))
    for tool in body.get("tools", []):
        tool["function"]["parameters"]["required"] = ["reason"]  # the placeholder hid it
    said = iter(users)
    for message in body["messages"]:
        if message["role"] == "user":
            message["content"] = next(said)
        elif message["role"] == "assistant":
            message["content"] = "Vale."
    if token is None:
        del body["elevenlabs_extra_body"]["criba_voice_token"]
    else:
        body["elevenlabs_extra_body"]["criba_voice_token"] = token
    return body


def auth(secret: str = LLM_SECRET) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def frames(text: str) -> list[str]:
    """The ``data:`` payloads of an event stream, in order."""
    out = []
    for block in text.split("\n\n"):
        if block.strip():
            assert block.startswith("data: "), block
            out.append(block.removeprefix("data: "))
    return out


def chunks(text: str) -> list[dict[str, Any]]:
    payloads = frames(text)
    assert payloads[-1] == "[DONE]"
    return [json.loads(p) for p in payloads[:-1]]


def content(text: str) -> str:
    return "".join(c["choices"][0]["delta"].get("content", "") for c in chunks(text))


def counts(conn: Any) -> tuple[int, int, int]:
    return tuple(
        conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("messages", "usage", "events")
    )


def post(vclient: TestClient, body: Any, headers: dict[str, Any] | None = None) -> Any:
    return vclient.post(PATH, json=body, headers=auth() if headers is None else headers)


async def _asgi(
    app: FastAPI, body: bytes, headers: dict[str, str], on_body: Callable[[bytes], None]
) -> list[bytes]:
    received = [{"type": "http.request", "body": body, "more_body": False}]
    never = anyio.Event()
    sent: list[bytes] = []

    async def receive() -> dict[str, Any]:
        if received:
            return received.pop(0)
        await never.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            sent.append(chunk)
            on_body(chunk)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": PATH,
        "raw_path": PATH.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    return sent


def asgi_stream(
    app: FastAPI,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
    on_body: Callable[[bytes], None] = lambda chunk: None,
) -> str:
    """Drive the app directly under anyio: ``on_body`` sees each body chunk as it is sent."""
    raw = json.dumps(body).encode()
    hdrs = {"content-type": "application/json", **(headers or auth())}
    sent = anyio.run(_asgi, app, raw, hdrs, on_body)
    return b"".join(sent).decode()


# --------------------------------------------------------------------------- R4.1, R4.2


def test_flag_off_is_404(
    conn: Any, spoken_channel: Channel, tmp_path: Path, session: tuple[str, dict]
) -> None:
    app = build_app(spoken_channel, voice_settings(tmp_path, enabled="off"))
    before = counts(conn)
    resp = TestClient(app).post(PATH, json=request_body([SAID], token_for(session[0])))
    assert resp.status_code == 404
    assert resp.json() == {"error": "not_found", "message": "Not found."}
    assert counts(conn) == before


@pytest.mark.parametrize(
    "case", ["wrong", "missing", "empty-bearer", "non-ascii", "empty-secret", "unset-secret"]
)
def test_bad_bearer_is_401_with_no_write(
    conn: Any,
    spoken_channel: Channel,
    tmp_path: Path,
    session: tuple[str, dict],
    hermes: FakeHermes,
    case: str,
) -> None:
    secret = {"empty-secret": "", "unset-secret": None}.get(case, LLM_SECRET)
    app = build_app(spoken_channel, voice_settings(tmp_path, llm=secret))
    headers: dict[str, Any] = {
        "wrong": auth("voice-llm-secret-wrong"),
        "missing": {},
        "empty-bearer": {"Authorization": "Bearer "},
        "non-ascii": {"Authorization": "Bearer ñandú-secreto".encode()},
        "empty-secret": {"Authorization": "Bearer "},
        "unset-secret": auth(),
    }[case]
    before = counts(conn)
    resp = TestClient(app).post(
        PATH, json=request_body([SAID], token_for(session[0])), headers=headers
    )
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized", "message": "Invalid credentials."}
    assert counts(conn) == before
    assert hermes.posts == []


@pytest.mark.parametrize(
    "case", ["missing", "tampered", "expired", "webhook-ended", "empty-voice-secret"]
)
def test_bad_voice_token_is_401_with_no_write(
    conn: Any,
    spoken_channel: Channel,
    tmp_path: Path,
    hermes: FakeHermes,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    session_id, _ = voice_session(conn, CONV, ended=case == "webhook-ended")
    settings = voice_settings(tmp_path)
    if case == "empty-voice-secret":
        # The gate needs the voice secret, so an empty one is a 404 at the door; the token
        # check refuses it too, which this case reaches by letting the gate pass.
        settings = voice_settings(tmp_path, voice="")
        monkeypatch.setattr(voice_module, "voice_available", lambda s: True)
    token: str | None = {
        "missing": None,
        "tampered": token_for(session_id)[:-2] + "xx",
        "expired": token_for(session_id, expires_in=-60),
    }.get(case, token_for(session_id))
    before = counts(conn)
    resp = TestClient(build_app(spoken_channel, settings)).post(
        PATH, json=request_body([SAID], token), headers=auth()
    )
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized", "message": "Invalid credentials."}
    assert counts(conn) == before
    assert hermes.posts == []


@pytest.mark.parametrize("truthful_length", [True, False])
def test_body_over_the_cap_is_413(
    conn: Any, vclient: TestClient, session: tuple[str, dict], truthful_length: bool
) -> None:
    body = request_body([SAID], token_for(session[0]))
    body["padding"] = "x" * (600 * 1024)
    raw = json.dumps(body).encode()
    before = counts(conn)
    if truthful_length:
        resp = vclient.post(
            PATH, content=raw, headers={**auth(), "content-type": "application/json"}
        )
    else:

        def pieces() -> Iterator[bytes]:  # chunked: no Content-Length at all
            for start in range(0, len(raw), 64 * 1024):
                yield raw[start : start + 64 * 1024]

        resp = vclient.post(
            PATH, content=pieces(), headers={**auth(), "content-type": "application/json"}
        )
        assert "content-length" not in resp.request.headers
    assert resp.status_code == 413
    assert resp.json() == {"error": "payload_too_large", "message": "Request body too large."}
    assert counts(conn) == before


def test_extra_elevenlabs_fields_and_unknown_roles_are_accepted(
    conn: Any, vclient: TestClient, components: dict, session: tuple[str, dict]
) -> None:
    session_id, candidate = session
    body = request_body([SAID], token_for(session_id), capture="028")
    check(components, "VoiceChatCompletionRequest", body)
    body["some_new_elevenlabs_field"] = {"nested": True}
    resp = post(vclient, body)
    assert resp.status_code == 200
    assert content(resp.text).startswith("Vale")

    # Not validated against the request schema, on purpose: the contract types `content` as a
    # string or null and closes `role` to four values, while the code tolerates text parts and
    # unknown roles (decision 36).
    parts = request_body(["primero", "x"], token_for(session_id))
    parts["messages"][-1]["content"] = [
        {"type": "text", "text": "jornada "},
        {"type": "text", "text": "completa"},
    ]
    assert post(vclient, parts).status_code == 200
    unknown = request_body(["primero", "x", "y"], token_for(session_id))
    unknown["messages"].insert(1, {"role": "developer", "content": "ignorado"})
    unknown["messages"][-1]["content"] = "sí"
    assert post(vclient, unknown).status_code == 200
    inbound = [
        r["body"]
        for r in conn.execute(
            "SELECT body FROM messages WHERE candidate_id = ? AND direction = 'inbound'"
            " ORDER BY id",
            (candidate["id"],),
        )
    ]
    assert inbound == [SAID, "jornada completa", "sí"]


def test_no_user_message_is_422_without_the_input(
    conn: Any, vclient: TestClient, session: tuple[str, dict]
) -> None:
    secret_words = "texto que no debe volver"
    token = token_for(session[0])
    no_user = request_body([SAID], token)
    no_user["messages"] = [{"role": "system", "content": secret_words}]
    blank = request_body([SAID], token)
    blank["messages"][-1]["content"] = "   "
    before = counts(conn)
    for body in (no_user, blank, {"messages": "no", "elevenlabs_extra_body": {}}):
        resp = post(vclient, body)
        assert resp.status_code == 422
        assert resp.json()["error"] == "validation_error"
        assert secret_words not in resp.text and token not in resp.text
    not_json = vclient.post(PATH, content=b"{nope " + secret_words.encode(), headers=auth())
    assert not_json.status_code == 422 and secret_words not in not_json.text
    assert counts(conn) == before


def test_the_rate_limit_counts_new_utterances_only(
    conn: Any, app: FastAPI, vclient: TestClient, session: tuple[str, dict]
) -> None:
    # The web chat's own limiter instance (R4.3): 29 hits already in this window.
    limiter = _RateLimiter(RATE_LIMIT, RATE_WINDOW_S)
    app.state.criba_webchat_limiter = limiter
    for _ in range(RATE_LIMIT - 1):
        assert limiter.retry_after(CONV) == 0
    token = token_for(session[0])
    assert post(vclient, request_body([SAID], token)).status_code == 200  # the 30th
    assert post(vclient, request_body([SAID], token)).status_code == 200  # a resend: not counted
    before = counts(conn)
    refused = post(vclient, request_body([SAID, "sí"], token))
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    assert refused.json()["error"] == "rate_limited"
    assert counts(conn) == before
    # A voice conversation takes no typed message (ADR 0059): there is no typed path to share.
    typed = vclient.post(
        f"/api/chat/conversations/{CONV}/messages", json={"message_id": "t1", "text": "hola"}
    )
    assert typed.status_code == 404


# --------------------------------------------------------------------------- R4.4: the contract


def end_call_frames(text: str, components: dict) -> dict[str, Any]:
    calls = [c for c in chunks(text) if "tool_calls" in c["choices"][0]["delta"]]
    assert len(calls) == 1
    call = calls[0]["choices"][0]["delta"]["tool_calls"][0]
    assert (call["id"], call["type"], call["function"]["name"]) == (
        "call_end",
        "function",
        "end_call",
    )
    arguments = json.loads(call["function"]["arguments"])
    check(components, "EndCallArguments", arguments)
    assert arguments == END_CALL_ARGUMENTS and arguments["reason"]
    assert call["function"]["arguments"] == json.dumps(END_CALL_ARGUMENTS)
    assert chunks(text)[-1]["choices"][0]["finish_reason"] == "tool_calls"
    return arguments


def assert_stream(resp: Any, components: dict) -> list[dict[str, Any]]:
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.text.rstrip("\n").endswith("data: [DONE]")
    found = chunks(resp.text)
    for chunk in found:
        check(components, "ChatCompletionChunk", chunk)
        assert chunk["model"] == MODEL and chunk["id"].startswith("chatcmpl-")
    assert len({c["id"] for c in found}) == 1
    assert found[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    return found


def test_stream_frames_match_the_contract(
    conn: Any,
    app: FastAPI,
    vclient: TestClient,
    components: dict,
    session: tuple[str, dict],
    hermes: FakeHermes,
    gated: GatedRunClient,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, candidate = session
    token = token_for(session_id)
    success = post(vclient, request_body([SAID], token))
    assert assert_stream(success, components)[-1]["choices"][0]["finish_reason"] == "stop"
    replay = post(vclient, request_body([SAID], token))
    assert content(replay.text) == content(success.text)
    assert_stream(replay, components)

    hermes.reply = lambda text: ""
    post(vclient, request_body([SAID, "sí"], token))  # no reply stored
    fallback = post(vclient, request_body([SAID, "sí"], token))
    assert content(fallback.text) == fallback_text("es-ES")
    assert_stream(fallback, components)

    hermes.reply = lambda text: "Vale, sigo."
    monkeypatch.setattr(voice_module, "FIRST_CHUNK_S", 0.05)
    gated.hold = threading.Event()
    first_chunk = asgi_stream(
        app,
        request_body([SAID, "sí", "tres"], token),
        on_body=lambda chunk: FIRST_CHUNK.encode() in chunk and gated.hold.set(),
    )
    assert content(first_chunk).startswith(FIRST_CHUNK)
    for chunk in chunks(first_chunk):
        check(components, "ChatCompletionChunk", chunk)

    app_id = repo.get_open_application(conn, candidate["id"])["id"]
    conn.execute(
        "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
        " VALUES ('usr_c', 'grupo_sazon', 'c@example.test', 'C', 'recruiter', 'x', ?)",
        (iso(NOW),),
    )
    conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'usr_c' WHERE id = ?",
        (app_id,),
    )
    ended = post(vclient, request_body([SAID, "sí", "tres", "cuatro"], token))
    assert_stream(ended, components)
    end_call_frames(ended.text, components)
    assert content(ended.text) == spoken_handover(None, "ES")


def test_error_bodies_match_the_contract(
    conn: Any,
    spoken_channel: Channel,
    tmp_path: Path,
    components: dict,
    session: tuple[str, dict],
) -> None:
    token = token_for(session[0])
    on = TestClient(build_app(spoken_channel, voice_settings(tmp_path)))
    off = TestClient(build_app(spoken_channel, voice_settings(tmp_path, enabled="off")))
    big = request_body([SAID], token)
    big["padding"] = "x" * (600 * 1024)
    limited_app = build_app(spoken_channel, voice_settings(tmp_path))
    limiter = _RateLimiter(RATE_LIMIT, RATE_WINDOW_S)
    for _ in range(RATE_LIMIT):
        limiter.retry_after(CONV)
    limited_app.state.criba_webchat_limiter = limiter
    responses = {
        401: on.post(PATH, json=request_body([SAID], token), headers=auth("wrong-secret-x")),
        404: off.post(PATH, json=request_body([SAID], token), headers=auth()),
        413: on.post(PATH, json=big, headers=auth()),
        422: on.post(PATH, json={"messages": [], "elevenlabs_extra_body": {}}, headers=auth()),
        429: TestClient(limited_app).post(PATH, json=request_body([SAID], token), headers=auth()),
    }
    for status, resp in responses.items():
        assert resp.status_code == status
        check(components, "VoiceError", resp.json())


# --------------------------------------------------------------------------- R5.1, R5.3


def test_message_ids_follow_the_user_count(
    conn: Any, vclient: TestClient, session: tuple[str, dict], hermes: FakeHermes
) -> None:
    session_id, candidate = session
    token = token_for(session_id)
    said = ["hola", "me llamo Ana", "vivo en Madrid"]
    for n in range(1, 4):
        assert post(vclient, request_body(said[:n], token)).status_code == 200
        last = hermes.inputs[-1]
        assert last.rsplit("\n", 1)[-1] == said[n - 1]
        assert all(earlier not in last for earlier in said[: n - 1])
    ids = [
        r["client_message_id"]
        for r in conn.execute(
            "SELECT client_message_id FROM messages WHERE candidate_id = ?"
            " AND direction = 'inbound' ORDER BY id",
            (candidate["id"],),
        )
    ]
    assert ids == [f"vc_{session_id}_{n}" for n in (1, 2, 3)]


def scrubbed(text: str) -> str:
    scrubber = SpeechScrubber()
    return scrubber.feed(text) + scrubber.flush()


def test_a_resend_replays_byte_for_byte_before_scrubbing(
    conn: Any, vclient: TestClient, session: tuple[str, dict], hermes: FakeHermes
) -> None:
    session_id, candidate = session
    hermes.reply = lambda text: "Reserva aquí: https://criba.work/book/abc  y\tdime."
    token = token_for(session_id)
    first = post(vclient, request_body([SAID], token))
    stored = conn.execute(
        "SELECT body FROM messages WHERE candidate_id = ? AND direction = 'outbound'"
        " AND client_message_id IS NULL",
        (candidate["id"],),
    ).fetchone()["body"]
    assert stored == "Reserva aquí: https://criba.work/book/abc  y\tdime."
    replay = post(vclient, request_body([SAID], token))
    assert content(replay.text) == scrubbed(stored) == content(first.text)
    assert len(hermes.posts) == 1


def test_the_first_chunk_comes_before_a_slow_reply(
    app: FastAPI,
    session: tuple[str, dict],
    gated: GatedRunClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(voice_module, "FIRST_CHUNK_S", 0.05)
    gated.hold = threading.Event()
    text = asgi_stream(
        app,
        request_body([SAID], token_for(session[0])),
        on_body=lambda chunk: FIRST_CHUNK.encode() in chunk and gated.hold.set(),
    )
    contents = [c["choices"][0]["delta"].get("content") for c in chunks(text)]
    assert contents[:2] == ["", FIRST_CHUNK]
    assert "".join(c or "" for c in contents[2:]).startswith("Vale")
    assert contents.count(FIRST_CHUNK) == 1


def test_no_first_chunk_when_the_reply_is_fast(
    vclient: TestClient, session: tuple[str, dict]
) -> None:
    resp = post(vclient, request_body([SAID], token_for(session[0])))
    assert FIRST_CHUNK not in content(resp.text)
    assert content(resp.text) == f"Vale: {SAID}"


def test_the_response_deadline_ends_a_stalled_stream(
    spoken_channel: Channel,
    vclient: TestClient,
    session: tuple[str, dict],
    gated: GatedRunClient,
    components: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spoken_channel.settings = dataclasses.replace(spoken_channel.settings, run_timeout_s=0.1)
    monkeypatch.setattr(voice_module, "DEADLINE_GRACE_S", 0.1)
    monkeypatch.setattr(voice_module, "FIRST_CHUNK_S", 60.0)
    gated.hold = threading.Event()
    try:
        resp = post(vclient, request_body([SAID], token_for(session[0])))
    finally:
        gated.hold.set()
    assert_stream(resp, components)
    assert content(resp.text) == fallback_text(None)


# --------------------------------------------------------------------------- R12.1


def test_urls_and_booking_tokens_are_not_spoken(
    conn: Any, vclient: TestClient, session: tuple[str, dict], hermes: FakeHermes
) -> None:
    session_id, candidate = session
    # A real booking token, minted at test time so no token-shaped literal is in the source.
    token = make_booking_token(
        "app_test", NOW + timedelta(days=7), secret=b"booking-secret-for-tests"
    )
    reply = (
        f"Reserva aquí: https://criba.work/book/{token}. "
        "O mira [este enlace](https://criba.work/book/abc) y criba.work/book/x, "
        f"el código {token} y la invitación /api/booking/invite.ics por favor."
    )
    hermes.reply = lambda text: reply
    spoken = content(post(vclient, request_body([SAID], token_for(session_id))).text)
    for leaked in ("http", "criba.work", "/book/", token, ".ics", "](", "www"):
        assert leaked not in spoken
    assert spoken.count(LINK_PHRASE) == 5
    assert spoken.startswith(f"Reserva aquí: {LINK_PHRASE}. O mira {LINK_PHRASE}")
    stored = conn.execute(
        "SELECT body FROM messages WHERE candidate_id = ? AND direction = 'outbound'"
        " AND client_message_id IS NULL",
        (candidate["id"],),
    ).fetchone()["body"]
    assert stored == reply


def test_a_url_split_across_deltas_is_scrubbed(
    vclient: TestClient, session: tuple[str, dict], hermes: FakeHermes
) -> None:
    scrubber = SpeechScrubber()
    out = scrubber.feed("Entra en https://orb")
    assert out == "Entra en "
    out += scrubber.feed("io.work/bo")
    out += scrubber.feed("ok/abc ahora mismo")
    out += scrubber.flush()
    assert out == f"Entra en {LINK_PHRASE} ahora mismo"
    # Through the endpoint: many small deltas.
    hermes.reply = lambda text: "Entra en https://criba.work/book/abc ahora mismo."
    hermes.delta_chunks = 25
    spoken = content(post(vclient, request_body([SAID], token_for(session[0]))).text)
    assert spoken == f"Entra en {LINK_PHRASE} ahora mismo."


@pytest.mark.parametrize(
    "text",
    [
        "Te espero a las 5 p. m. en el local.",
        "Son 3.5 kilómetros.",
        "Moto, bici, etc. lo que tengas.",
        "A las 9 a.m. abrimos.",
        "Grupo Sazón S.L. te contrata.",
        "Pagan 1.200 € al mes.",
        "Dijo sí.No quiere más.",
        "Vale... entonces mañana.",
        "Horario: 10:30-14:00.",
        "¿Tienes el carné B? (sí/no)",
        "El Sr. García te llama.",
    ],
)
def test_near_misses_are_kept(text: str) -> None:
    assert scrubbed(text) == text


# --------------------------------------------------------------------------- R16.4


def test_a_takeover_ended_session_gets_the_line_and_end_call(
    conn: Any, vclient: TestClient, components: dict, hermes: FakeHermes
) -> None:
    session_id, candidate = voice_session(conn, CONV, ended=True)
    app = repo.start_application(conn, candidate["id"], "webchat", now=NOW)
    conn.execute(
        "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
        " VALUES ('usr_e', 'grupo_sazon', 'e@example.test', 'E', 'recruiter', 'x', ?)",
        (iso(NOW),),
    )
    conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'usr_e' WHERE id = ?",
        (app["id"],),
    )
    before = counts(conn)
    resp = post(vclient, request_body([SAID], token_for(session_id)))
    assert_stream(resp, components)
    end_call_frames(resp.text, components)
    assert content(resp.text) == spoken_handover(None, "ES")
    assert counts(conn) == before
    assert hermes.posts == []


# --------------------------------------------------------------------------- logs


def test_no_log_line_carries_text_or_secrets(
    conn: Any,
    vclient: TestClient,
    session: tuple[str, dict],
    hermes: FakeHermes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    reply = "Respuesta privada del agente"
    hermes.reply = lambda text: reply
    token = token_for(session[0])
    with caplog.at_level(logging.DEBUG):
        assert post(vclient, request_body([SAID], token)).status_code == 200
        assert post(vclient, request_body([SAID], token), auth("wrong-bearer-x")).status_code == 401
        hermes.event_scripts = [
            [sse(0, {"event": "run.failed", "run_id": "run_2", "error": "model error"})]
        ]
        failed = post(vclient, request_body([SAID, "segunda frase secreta"], token))
        assert failed.status_code == 200
    for secret in (SAID, "segunda frase secreta", reply, LLM_SECRET, "wrong-bearer-x", token):
        assert secret not in caplog.text


@pytest.mark.parametrize(
    "failure",
    [sqlite3.OperationalError("database is locked"), RuntimeError("boom")],
    ids=["busy", "unexpected"],
)
def test_a_turn_that_fails_on_our_side_speaks_the_fallback_line(
    vclient: TestClient,
    conn: Any,
    session: tuple[str, dict],
    components: dict,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    """No bare 500 (``VoiceError`` has no ``internal`` code): the contract's 200 stream with the
    fallback line, as for a run past its deadline; nothing stored, nothing of the error sent."""

    def fail(*_: Any) -> None:
        raise failure

    monkeypatch.setattr(voice_module, "_admit", fail)
    before = counts(conn)

    resp = post(vclient, request_body([SAID], token_for(session[0])))

    assert_stream(resp, components)
    assert content(resp.text) == fallback_text(None)
    assert chunks(resp.text)[-1]["choices"][0]["finish_reason"] == "stop"
    assert "boom" not in resp.text and "locked" not in resp.text
    assert counts(conn) == before
