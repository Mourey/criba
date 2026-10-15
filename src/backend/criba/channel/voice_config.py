"""Voice settings, read from the environment the Compose file sets (spec voice-agent
session-config: SE1, SE2, SE13).

| Variable | Default | Meaning |
|---|---|---|
| ``CRIBA_VOICE`` | off | on only when exactly ``on`` (trimmed, any case) |
| ``CRIBA_VOICE_AGENT_ES`` | ``''`` | ElevenLabs agent id for Spain |
| ``CRIBA_VOICE_AGENT_MX`` | ``''`` | ElevenLabs agent id for Mexico |
| ``CRIBA_VOICE_PUBLIC_BASE_URL`` | ``''`` | public base URL ElevenLabs calls back on |
| ``CRIBA_VOICE_MAX_CONCURRENT`` | ``5`` | concurrent voice sessions; integer above 0 |
| ``CRIBA_VOICE_DAILY_MINUTES`` | ``120`` | voice minutes per day; integer above 0 |
| ``CRIBA_VOICE_COST_PER_MIN`` | ``0.08`` | cost per minute; finite, 0 or more |
| ``CRIBA_ENV`` | ``prod`` | through ``usage_environment``; tags usage rows |
| ``CRIBA_ELEVENLABS_API_KEY_FILE`` | unset | file holding the ElevenLabs API key |
| ``CRIBA_VOICE_SECRET_FILE`` | unset | file holding the voice-session token key |
| ``CRIBA_VOICE_LLM_SECRET_FILE`` | unset | file holding the custom-LLM bearer |
| ``CRIBA_ELEVENLABS_WEBHOOK_SECRET_FILE`` | unset | file holding the post-call webhook key |

Parsing fails closed: a rejected number is held as 0 and its variable is named in
``invalid``, which keeps ``voice_available`` False. Warnings name the variable, never
its value. Secrets are never held in the settings: only their file paths are, and
``read_secret`` reads the file on every call.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from criba.channel.config import usage_environment

logger = logging.getLogger(__name__)

SecretName = Literal[
    "elevenlabs_api_key",
    "criba_voice_secret",
    "criba_voice_llm_secret",
    "elevenlabs_webhook_secret",
]

_MAX_CONCURRENT_DEFAULT = 5
_DAILY_MINUTES_DEFAULT = 120
_COST_PER_MIN_DEFAULT = 0.08


@dataclass(frozen=True)
class VoiceSettings:
    enabled: bool
    agent_es: str
    agent_mx: str
    public_base_url: str
    max_concurrent: int
    daily_minutes: int
    cost_per_min: float
    environment: str
    elevenlabs_api_key_file: str
    voice_secret_file: str
    voice_llm_secret_file: str
    elevenlabs_webhook_secret_file: str
    invalid: tuple[str, ...]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> VoiceSettings:
        env = os.environ if env is None else env
        invalid: list[str] = []
        max_concurrent = _positive_int(
            env, "CRIBA_VOICE_MAX_CONCURRENT", _MAX_CONCURRENT_DEFAULT, invalid
        )
        daily_minutes = _positive_int(
            env, "CRIBA_VOICE_DAILY_MINUTES", _DAILY_MINUTES_DEFAULT, invalid
        )
        cost_per_min = _price(env, "CRIBA_VOICE_COST_PER_MIN", _COST_PER_MIN_DEFAULT, invalid)
        return cls(
            enabled=_flag(env.get("CRIBA_VOICE")),
            agent_es=env.get("CRIBA_VOICE_AGENT_ES", ""),
            agent_mx=env.get("CRIBA_VOICE_AGENT_MX", ""),
            public_base_url=env.get("CRIBA_VOICE_PUBLIC_BASE_URL", ""),
            max_concurrent=max_concurrent,
            daily_minutes=daily_minutes,
            cost_per_min=cost_per_min,
            environment=usage_environment(env.get("CRIBA_ENV")),
            elevenlabs_api_key_file=env.get("CRIBA_ELEVENLABS_API_KEY_FILE", ""),
            voice_secret_file=env.get("CRIBA_VOICE_SECRET_FILE", ""),
            voice_llm_secret_file=env.get("CRIBA_VOICE_LLM_SECRET_FILE", ""),
            elevenlabs_webhook_secret_file=env.get("CRIBA_ELEVENLABS_WEBHOOK_SECRET_FILE", ""),
            invalid=tuple(invalid),
        )

    def read_secret(self, name: SecretName) -> str:
        """The named secret, stripped; '' when its path is unset or the file is missing,
        unreadable or empty. Read on every call, never cached, never logged."""
        path = {
            "elevenlabs_api_key": self.elevenlabs_api_key_file,
            "criba_voice_secret": self.voice_secret_file,
            "criba_voice_llm_secret": self.voice_llm_secret_file,
            "elevenlabs_webhook_secret": self.elevenlabs_webhook_secret_file,
        }[name]
        if not path:
            return ""
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return ""


def voice_available(settings: VoiceSettings) -> bool:
    """True only when voice is on, both agent ids are set, no value was rejected, and the
    ElevenLabs API key and the voice-session secret are non-empty. Never raises."""
    return bool(
        settings.enabled
        and settings.agent_es
        and settings.agent_mx
        and not settings.invalid
        and settings.read_secret("elevenlabs_api_key")
        and settings.read_secret("criba_voice_secret")
    )


def _flag(raw: str | None) -> bool:
    value = (raw or "").strip().lower()
    if value == "on":
        return True
    if value not in ("", "off"):
        logger.warning("CRIBA_VOICE is not 'on' or 'off': voice stays off")
    return False


def _positive_int(env: Mapping[str, str], variable: str, default: int, invalid: list[str]) -> int:
    raw = env.get(variable)
    if raw is None:
        return default
    try:
        value = int(raw.strip(), 10)
    except ValueError:
        value = 0
    if value > 0:
        return value
    invalid.append(variable)
    logger.warning("%s must be a whole number above zero: voice is unavailable", variable)
    return 0


def _price(env: Mapping[str, str], variable: str, default: float, invalid: list[str]) -> float:
    raw = env.get(variable)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        value = -1.0
    if math.isfinite(value) and value >= 0:
        return value
    invalid.append(variable)
    logger.warning("%s must be a finite number of zero or more: voice is unavailable", variable)
    return 0.0
