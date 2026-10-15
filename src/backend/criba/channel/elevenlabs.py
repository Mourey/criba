"""The ElevenLabs client behind a protocol (spec voice-agent session-start SE3).

One call: mint a WebRTC conversation token for an agent, ``GET
/v1/convai/conversation/token?agent_id=<id>`` with the API key in the ``xi-api-key``
header, which answers ``{token, conversation_id}`` (spike 005; voice log decision 46).

- :class:`HttpElevenLabsClient` reads the key with ``settings.read_secret`` on every call
  and sends it only in its header, never in a URL.
- :class:`FakeElevenLabsClient` mints nothing; :func:`elevenlabs_client` returns it whenever
  ``settings.environment`` is ``sim`` or ``test``, so neither the sim stack nor a test calls
  ElevenLabs.
- :class:`ElevenLabsError` carries the HTTP status at most: never the key, the URL (it holds
  the agent id), the request headers or the response body. It is raised ``from None``
  outside any ``except`` block, so it has no ``__cause__`` or ``__context__``. Nothing here logs.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from criba.channel.voice_config import VoiceSettings

TOKEN_PATH = "/v1/convai/conversation/token"
KEY_HEADER = "xi-api-key"


class ElevenLabsError(Exception):
    """ElevenLabs could not mint a token. The message holds the status code at most."""


@dataclass(frozen=True)
class ConversationToken:
    """A minted WebRTC conversation token and the ElevenLabs conversation id it opens."""

    token: str
    conversation_id: str


class ElevenLabsClient(Protocol):
    def conversation_token(self, agent_id: str) -> ConversationToken: ...


class HttpElevenLabsClient:
    """The real client. One short-lived ``httpx.Client`` per call."""

    def __init__(
        self,
        settings: VoiceSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://api.elevenlabs.io",
        timeout_s: float = 5.0,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.base_url = base_url
        self.timeout_s = timeout_s

    def conversation_token(self, agent_id: str) -> ConversationToken:
        key = self.settings.read_secret("elevenlabs_api_key")
        if not key:
            raise ElevenLabsError("ElevenLabs API key is not set") from None
        status, payload, failed = self._get(agent_id, key)
        if failed:
            raise ElevenLabsError("ElevenLabs request failed") from None
        if status != 200:
            raise ElevenLabsError(f"ElevenLabs answered {status}") from None
        token = payload.get("token") if isinstance(payload, dict) else None
        conversation = payload.get("conversation_id") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise ElevenLabsError("ElevenLabs reply has no token") from None
        if not isinstance(conversation, str) or not conversation:
            raise ElevenLabsError("ElevenLabs reply has no conversation id") from None
        return ConversationToken(token=token, conversation_id=conversation)

    def _get(self, agent_id: str, key: str) -> tuple[int, Any, bool]:
        """``(status, json or None, failed)``. Every exception is swallowed here, so the
        caller raises :class:`ElevenLabsError` with no exception chained to it."""
        try:
            with httpx.Client(
                transport=self.transport, base_url=self.base_url, timeout=self.timeout_s
            ) as http:
                response = http.get(
                    TOKEN_PATH, params={"agent_id": agent_id}, headers={KEY_HEADER: key}
                )
        except Exception:
            return 0, None, True
        if response.status_code != 200:
            return response.status_code, None, False
        try:
            return 200, response.json(), False
        except ValueError:
            return 200, None, False


class FakeElevenLabsClient:
    """A synthetic client for sim and tests. Each call records the agent id; it raises
    ``fail`` when set. The conversation id is ``conversation_id`` when given, else a fresh
    ``fake-conv-<16 hex>`` per call, so a restarted sim backend never reuses one."""

    def __init__(
        self, token: str = "fake-conversation-token", *, conversation_id: str | None = None
    ) -> None:
        self.token = token
        self.conversation_id = conversation_id
        self.calls: list[str] = []
        self.conversation_ids: list[str] = []
        self.fail: Exception | None = None

    def conversation_token(self, agent_id: str) -> ConversationToken:
        self.calls.append(agent_id)
        if self.fail is not None:
            raise self.fail
        conversation = self.conversation_id or f"fake-conv-{secrets.token_hex(8)}"
        self.conversation_ids.append(conversation)
        return ConversationToken(token=self.token, conversation_id=conversation)


def elevenlabs_client(settings: VoiceSettings) -> ElevenLabsClient:
    """The fake under ``sim`` and ``test``, whatever else is set; the HTTP client otherwise."""
    if settings.environment in ("sim", "test"):
        return FakeElevenLabsClient()
    return HttpElevenLabsClient(settings)


__all__ = [
    "ConversationToken",
    "ElevenLabsClient",
    "ElevenLabsError",
    "FakeElevenLabsClient",
    "HttpElevenLabsClient",
    "elevenlabs_client",
]
