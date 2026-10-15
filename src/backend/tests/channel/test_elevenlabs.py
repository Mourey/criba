"""The ElevenLabs client behind a protocol (spec voice-agent session-start SE3).

No real ElevenLabs call: the HTTP client runs on an ``httpx.MockTransport``. Secret test
values are low-entropy words written to ``tmp_path``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import httpx
import pytest

from criba.channel.elevenlabs import (
    ConversationToken,
    ElevenLabsError,
    FakeElevenLabsClient,
    HttpElevenLabsClient,
    elevenlabs_client,
)
from criba.channel.voice_config import VoiceSettings

API_KEY = "elevenlabs-key-for-tests"
PLANTED = "planted-body-word"
AGENT = "agent-es-for-tests"


def _settings(tmp_path: Path, env: str = "prod", *, key: str | None = API_KEY) -> VoiceSettings:
    values = {"CRIBA_VOICE": "on", "CRIBA_ENV": env}
    if key is not None:
        path = tmp_path / "elevenlabs_api_key.txt"
        path.write_text(f"{key}\n", encoding="utf-8")
        values["CRIBA_ELEVENLABS_API_KEY_FILE"] = str(path)
    return VoiceSettings.from_env(values)


@pytest.mark.parametrize("env", ["sim", "test"])
def test_sim_and_test_settings_get_the_fake(env: str, tmp_path: Path) -> None:
    assert isinstance(elevenlabs_client(_settings(tmp_path, env)), FakeElevenLabsClient)


@pytest.mark.parametrize("env", ["prod", "staging", ""])
def test_prod_settings_get_the_http_client(env: str, tmp_path: Path) -> None:
    assert isinstance(elevenlabs_client(_settings(tmp_path, env)), HttpElevenLabsClient)


def test_the_key_travels_only_in_its_header(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"token": "minted-token", "conversation_id": "conv_1"})

    client = HttpElevenLabsClient(
        _settings(tmp_path), transport=httpx.MockTransport(handler), base_url="https://el.test"
    )
    result = client.conversation_token(AGENT)

    assert result == ConversationToken(token="minted-token", conversation_id="conv_1")
    (request,) = seen
    assert request.method == "GET"
    assert request.url.path == "/v1/convai/conversation/token"
    assert dict(request.url.params) == {"agent_id": AGENT}
    assert request.headers["xi-api-key"] == API_KEY
    assert API_KEY not in str(request.url)
    others = {k: v for k, v in request.headers.items() if k != "xi-api-key"}
    assert API_KEY not in str(others)


def test_the_key_is_read_on_every_call(tmp_path: Path) -> None:
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["xi-api-key"])
        return httpx.Response(200, json={"token": "t", "conversation_id": f"c{len(keys)}"})

    settings = _settings(tmp_path)
    client = HttpElevenLabsClient(settings, transport=httpx.MockTransport(handler))
    client.conversation_token(AGENT)
    (tmp_path / "elevenlabs_api_key.txt").write_text("rotated-key-word\n", encoding="utf-8")
    client.conversation_token(AGENT)
    assert keys == [API_KEY, "rotated-key-word"]


def _assert_clean(exc: ElevenLabsError, *, status: int | None) -> None:
    for text in (str(exc), repr(exc)):
        assert API_KEY not in text
        assert PLANTED not in text
        assert AGENT not in text
        assert "://" not in text
        assert "/v1/" not in text
    if status is not None:
        assert str(status) in str(exc)
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True
    assert exc.__context__ is None


@pytest.mark.parametrize("status", [401, 500])
def test_errors_carry_the_status_only(status: int, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, json={"detail": f"{PLANTED} {API_KEY} {request.url}"}, headers={"x-k": API_KEY}
        )

    client = HttpElevenLabsClient(_settings(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(ElevenLabsError) as info:
        client.conversation_token(AGENT)
    _assert_clean(info.value, status=status)


def test_a_transport_error_carries_nothing(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"{PLANTED} {request.url}", request=request)

    client = HttpElevenLabsClient(_settings(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(ElevenLabsError) as info:
        client.conversation_token(AGENT)
    _assert_clean(info.value, status=None)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_a_missing_key_refuses_before_any_request(key: str | None, tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"token": "t", "conversation_id": "c"})

    client = HttpElevenLabsClient(
        _settings(tmp_path, key=key), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ElevenLabsError):
        client.conversation_token(AGENT)
    assert calls == []


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, json={"conversation_id": "c"}),
        httpx.Response(200, json={"token": "", "conversation_id": "c"}),
        httpx.Response(200, json={"token": 7, "conversation_id": "c"}),
        httpx.Response(200, json={"token": "t"}),
        httpx.Response(200, json={"token": "t", "conversation_id": ""}),
        httpx.Response(200, json=["token"]),
        httpx.Response(200, content=f"not json {PLANTED}".encode()),
    ],
    ids=[
        "no-token",
        "empty-token",
        "number",
        "no-conversation",
        "empty-conversation",
        "list",
        "text",
    ],
)
def test_a_reply_without_a_token_is_an_error(reply: httpx.Response, tmp_path: Path) -> None:
    client = HttpElevenLabsClient(
        _settings(tmp_path), transport=httpx.MockTransport(lambda request: reply)
    )
    with pytest.raises(ElevenLabsError) as info:
        client.conversation_token(AGENT)
    _assert_clean(info.value, status=None)


def test_the_fake_records_calls_and_can_fail() -> None:
    fake = FakeElevenLabsClient()
    first = fake.conversation_token("agent-a")
    second = fake.conversation_token("agent-b")
    assert fake.calls == ["agent-a", "agent-b"]
    assert first.token == second.token == "fake-conversation-token"
    assert first.conversation_id and second.conversation_id
    assert first.conversation_id != second.conversation_id
    assert fake.conversation_ids == [first.conversation_id, second.conversation_id]

    fixed = FakeElevenLabsClient(
        "other-token",
        conversation_id="conv-fixed",
    )
    assert fixed.conversation_token("x") == ConversationToken("other-token", "conv-fixed")
    assert fixed.conversation_token("y") == ConversationToken("other-token", "conv-fixed")

    fake.fail = ElevenLabsError("ElevenLabs answered 503")
    with pytest.raises(ElevenLabsError):
        fake.conversation_token("agent-c")
    assert fake.calls == ["agent-a", "agent-b", "agent-c"]
    assert len(fake.conversation_ids) == 2


def test_the_conversation_token_is_frozen() -> None:
    token = ConversationToken(token="t", conversation_id="c")
    field = dataclasses.fields(token)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(token, field, "other")
