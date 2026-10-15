"""Channel settings, read from the environment the Compose file sets.

| Variable | Default | Meaning |
|---|---|---|
| ``CRIBA_DB_PATH`` | ``/data/criba.db`` | SQLite file |
| ``CRIBA_HERMES_URL`` | ``http://hermes:8642`` | Hermes API server |
| ``CRIBA_HERMES_PROFILE`` | ``criba-recruiter`` | profile behind ``/p/<profile>/v1/runs`` |
| ``CRIBA_HERMES_VOICE_PROFILE`` | the ``-voice`` twin | profile for spoken turns (ADR 0059) |
| ``CRIBA_HERMES_API_KEY_FILE`` | ``/run/secrets/hermes_api_server_key`` | the profile's key |
| ``CRIBA_ENV`` | ``prod`` | ``prod``, ``sim`` or ``test``; tags usage rows |
| ``CRIBA_CLIENT_ID`` | ``grupo_sazon`` | Client new Candidates belong to |
| ``CRIBA_RUN_TIMEOUT_S`` | ``60`` | give up on a run after this long |
| ``CRIBA_PERSONA_NAME`` | ``Sazón Empleo`` | ``sender_label`` of agent messages (SYNTHETIC) |
| ``CRIBA_TEAM_LABEL`` | ``Equipo de Grupo Sazón`` | ``sender_label`` of team messages |
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# The usage ledger's ``environment`` column.
UsageEnvironment = Literal["prod", "sim", "test"]


@dataclass(frozen=True)
class ChannelSettings:
    db_path: str = "/data/criba.db"
    hermes_url: str = "http://hermes:8642"
    hermes_profile: str = "criba-recruiter"
    # Voice calls run on their own profile (ADR 0059): criba-recruiter-voice, or its -sim twin.
    hermes_voice_profile: str = "criba-recruiter-voice"
    hermes_api_key_file: str = "/run/secrets/hermes_api_server_key"
    environment: UsageEnvironment = "prod"
    client_id: str = "grupo_sazon"
    run_timeout_s: float = 60.0
    poll_interval_s: float = 0.25
    persona_name: str = "Sazón Empleo"
    team_label: str = "Equipo de Grupo Sazón"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ChannelSettings:
        env = os.environ if env is None else env
        d = cls()
        profile = env.get("CRIBA_HERMES_PROFILE", d.hermes_profile)
        return cls(
            db_path=env.get("CRIBA_DB_PATH", d.db_path),
            hermes_url=env.get("CRIBA_HERMES_URL", d.hermes_url),
            hermes_profile=profile,
            hermes_voice_profile=env.get("CRIBA_HERMES_VOICE_PROFILE") or voice_profile(profile),
            hermes_api_key_file=env.get("CRIBA_HERMES_API_KEY_FILE", d.hermes_api_key_file),
            environment=usage_environment(env.get("CRIBA_ENV")),
            client_id=env.get("CRIBA_CLIENT_ID", d.client_id),
            run_timeout_s=float(env.get("CRIBA_RUN_TIMEOUT_S", d.run_timeout_s)),
            persona_name=env.get("CRIBA_PERSONA_NAME", d.persona_name),
            team_label=env.get("CRIBA_TEAM_LABEL", d.team_label),
        )

    def read_api_key(self) -> str:
        return Path(self.hermes_api_key_file).read_text(encoding="utf-8").strip()


SIM_SUFFIX = "-sim"


def voice_profile(text_profile: str) -> str:
    """The voice twin of a text profile: ``criba-recruiter`` -> ``criba-recruiter-voice``,
    ``criba-recruiter-sim`` -> ``criba-recruiter-voice-sim`` (ADR 0059)."""
    if text_profile.endswith(SIM_SUFFIX):
        return f"{text_profile.removesuffix(SIM_SUFFIX)}-voice{SIM_SUFFIX}"
    return f"{text_profile}-voice"


def usage_environment(value: str | None) -> UsageEnvironment:
    """Map ``CRIBA_ENV`` to the usage ledger's ``environment`` (prod, sim or test)."""
    if value == "sim":
        return "sim"
    if value == "test":
        return "test"
    return "prod"
