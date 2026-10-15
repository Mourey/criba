"""When the Jev router is on (ADR 0051), and what the wiring builds from the environment."""

from __future__ import annotations

from pathlib import Path

import pytest

from criba.api.sim import SimSettingsError, check_sim_settings
from criba.jev import JevSettings, OpenRouterJev, jev_enabled
from criba.jev.config import DEFAULT_MODEL, DEFAULT_TIMEOUT_S, OPENROUTER_DECISIONS_URL
from criba.wiring import jev_step_from_env


@pytest.fixture
def key_file(tmp_path: Path) -> str:
    path = tmp_path / "openrouter_api_key.txt"
    path.write_text("k-test\n", encoding="utf-8")
    return str(path)


def test_off_without_a_key() -> None:
    assert jev_enabled({}) is False
    assert jev_enabled({"CRIBA_OPENROUTER_API_KEY_FILE": "/nonexistent"}) is False


def test_on_by_default_with_a_key(key_file: str) -> None:
    assert jev_enabled({"CRIBA_OPENROUTER_API_KEY_FILE": key_file}) is True
    assert jev_enabled({"CRIBA_OPENROUTER_API_KEY_FILE": key_file, "CRIBA_JEV": "auto"}) is True


@pytest.mark.parametrize("flag", ["off", "0", "false", "no", "OFF"])
def test_the_flag_turns_it_off(key_file: str, flag: str) -> None:
    assert jev_enabled({"CRIBA_OPENROUTER_API_KEY_FILE": key_file, "CRIBA_JEV": flag}) is False


def test_on_still_needs_a_key() -> None:
    assert jev_enabled({"CRIBA_JEV": "on"}) is False


def test_sim_mode_is_on_only_for_a_stub_url(key_file: str) -> None:
    sim = {"CRIBA_ENV": "sim", "CRIBA_OPENROUTER_API_KEY_FILE": key_file}
    assert jev_enabled(sim) is False  # a key never turns the real service on in sim
    assert jev_enabled({**sim, "CRIBA_JEV": "on"}) is False
    assert jev_enabled({**sim, "CRIBA_JEV_URL": "http://stub-llm:8000/api/alpha/decisions"})


def test_settings_defaults_and_overrides(key_file: str) -> None:
    default = JevSettings.from_env({"CRIBA_OPENROUTER_API_KEY_FILE": key_file})
    assert default.enabled and default.url == OPENROUTER_DECISIONS_URL
    assert default.model == DEFAULT_MODEL and default.timeout_s == DEFAULT_TIMEOUT_S
    assert default.read_key() == "k-test"
    custom = JevSettings.from_env(
        {"CRIBA_JEV_MODEL": "typesafe/jev-9", "CRIBA_JEV_TIMEOUT_S": "0.5"}
    )
    assert custom.model == "typesafe/jev-9" and custom.timeout_s == 0.5
    bad = JevSettings.from_env({"CRIBA_JEV_TIMEOUT_S": "soon"})
    assert bad.timeout_s == DEFAULT_TIMEOUT_S
    assert JevSettings().read_key() == ""


def test_the_wiring_builds_a_step_only_when_on(key_file: str) -> None:
    assert jev_step_from_env({}, environment="prod") is None
    step = jev_step_from_env({"CRIBA_OPENROUTER_API_KEY_FILE": key_file}, environment="prod")
    assert step is not None and isinstance(step.port, OpenRouterJev)
    assert step.environment == "prod"
    stub = {"CRIBA_ENV": "sim", "CRIBA_JEV_URL": "http://stub-llm:8000/api/alpha/decisions"}
    in_sim = jev_step_from_env(stub, environment="sim")
    assert in_sim is not None and isinstance(in_sim.port, OpenRouterJev)
    assert in_sim.port.url == stub["CRIBA_JEV_URL"]


def test_sim_mode_refuses_the_real_service() -> None:
    base = {"CRIBA_ENV": "sim", "CRIBA_HERMES_PROFILE": "criba-recruiter-sim"}
    check_sim_settings(base)  # no Jev URL: the router is off in sim
    check_sim_settings({**base, "CRIBA_JEV_URL": "http://stub-llm:8000/api/alpha/decisions"})
    with pytest.raises(SimSettingsError, match="Jev"):
        check_sim_settings({**base, "CRIBA_JEV_URL": OPENROUTER_DECISIONS_URL})
    with pytest.raises(SimSettingsError, match="Jev"):
        check_sim_settings({**base, "CRIBA_JEV_URL": "https://openrouter.ai/other"})
    with pytest.raises(SimSettingsError, match="Jev"):
        check_sim_settings({**base, "CRIBA_JEV_URL": "https://api.typesafe.ai/x"})
    with pytest.raises(SimSettingsError, match="Jev"):
        check_sim_settings({**base, "CRIBA_JEV_URL": "https://openrouter.ai.example.test/x"})
    check_sim_settings({**base, "CRIBA_JEV_URL": "http://localhost:8000/api/alpha/decisions"})
