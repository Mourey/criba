"""Jev router settings, read from the environment the Compose file sets (ADR 0051).

| Variable | Default | Meaning |
|---|---|---|
| ``CRIBA_JEV`` | ``auto`` | ``on``, ``off`` or ``auto`` (see :func:`jev_enabled`) |
| ``CRIBA_JEV_URL`` | OpenRouter's decisions endpoint | where the questions go (the stub in sim) |
| ``CRIBA_JEV_MODEL`` | ``typesafe/jev-1.13`` | the decisions model |
| ``CRIBA_JEV_TIMEOUT_S`` | ``1.2`` | give up on one call after this long |
| ``CRIBA_OPENROUTER_API_KEY_FILE`` | unset | the key (shared with the summary client) |
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"
# Measured on 2026-10-10 (docs/evidence/jev-calibration.md): the call is one small request
# with answers in parallel, 0.3 to 0.5 s; a turn that waits longer goes ahead without Jev.
DEFAULT_TIMEOUT_S = 1.2

JEV_ENV = "CRIBA_JEV"
URL_ENV = "CRIBA_JEV_URL"
MODEL_ENV = "CRIBA_JEV_MODEL"
TIMEOUT_ENV = "CRIBA_JEV_TIMEOUT_S"
KEY_FILE_ENV = "CRIBA_OPENROUTER_API_KEY_FILE"

_ON = ("1", "on", "true", "yes")
_OFF = ("0", "off", "false", "no")


@dataclass(frozen=True)
class JevSettings:
    enabled: bool = False
    url: str = OPENROUTER_DECISIONS_URL
    model: str = DEFAULT_MODEL
    timeout_s: float = DEFAULT_TIMEOUT_S
    key_file: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> JevSettings:
        source = os.environ if env is None else env
        try:
            timeout = float(source.get(TIMEOUT_ENV, DEFAULT_TIMEOUT_S))
        except ValueError:
            timeout = DEFAULT_TIMEOUT_S
        return cls(
            enabled=jev_enabled(source),
            url=source.get(URL_ENV, "").strip() or OPENROUTER_DECISIONS_URL,
            model=source.get(MODEL_ENV, "").strip() or DEFAULT_MODEL,
            timeout_s=timeout if timeout > 0 else DEFAULT_TIMEOUT_S,
            key_file=source.get(KEY_FILE_ENV) or None,
        )

    def read_key(self) -> str:
        """The OpenRouter key, or '' when there is none (the router is then off)."""
        return _read_key(self.key_file)


def _read_key(path: str | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def jev_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the Jev router decides the reply language and reads the tone (ADR 0051).

    ``CRIBA_JEV=on`` / ``off`` is explicit (``on`` still needs a key, or a stub URL in sim). In
    ``auto``, the default: outside sim mode it is on when the OpenRouter key file holds a key; in
    sim mode (``CRIBA_ENV=sim``) it is on only when ``CRIBA_JEV_URL`` names the stub, so the real
    service is never reached from a test rig. The agent's own language report counts only when
    this is False (:func:`criba.tools.application.get_application`).
    """
    source = os.environ if env is None else env
    flag = source.get(JEV_ENV, "auto").strip().lower() or "auto"
    if flag in _OFF:
        return False
    sim = source.get("CRIBA_ENV", "").strip().lower() == "sim"
    stub_url = bool(source.get(URL_ENV, "").strip())
    if sim:
        return stub_url
    if stub_url and flag in _ON:
        return True
    return bool(_read_key(source.get(KEY_FILE_ENV)))
