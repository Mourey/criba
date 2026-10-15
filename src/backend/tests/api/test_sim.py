"""Sim mode: the Q39 startup check and the test-only clock endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from criba.api.clock import SimClock
from criba.api.sim import SimSettingsError, check_sim_settings
from criba.screening.clock import Clock

# --- check_sim_settings (Q39): all four combinations -------------------------------------


def test_sim_env_with_a_sim_profile_is_allowed():
    check_sim_settings({"CRIBA_ENV": "sim", "CRIBA_HERMES_PROFILE": "criba-recruiter-sim"})


def test_neither_sim_env_nor_sim_profile_is_allowed():
    check_sim_settings({"CRIBA_ENV": "prod", "CRIBA_HERMES_PROFILE": "criba-recruiter"})
    check_sim_settings({"CRIBA_HERMES_PROFILE": "criba-recruiter"})
    check_sim_settings({})


def test_sim_env_with_a_real_profile_refuses_to_start():
    with pytest.raises(SimSettingsError, match="CRIBA_ENV=sim requires a -sim"):
        check_sim_settings({"CRIBA_ENV": "sim", "CRIBA_HERMES_PROFILE": "criba-recruiter"})


def test_sim_profile_without_sim_env_refuses_to_start():
    with pytest.raises(SimSettingsError, match="not 'sim'"):
        check_sim_settings({"CRIBA_ENV": "prod", "CRIBA_HERMES_PROFILE": "criba-recruiter-sim"})
    with pytest.raises(SimSettingsError):
        check_sim_settings({"CRIBA_HERMES_PROFILE": "criba-recruiter-sim"})


@pytest.mark.parametrize("profile", [None, "", "  "])
def test_sim_env_without_a_profile_refuses_to_start(profile):
    env = {"CRIBA_ENV": "sim"}
    if profile is not None:
        env["CRIBA_HERMES_PROFILE"] = profile
    with pytest.raises(SimSettingsError, match="unset"):
        check_sim_settings(env)


def test_the_check_ignores_case_and_whitespace_of_the_env_value():
    check_sim_settings({"CRIBA_ENV": " SIM ", "CRIBA_HERMES_PROFILE": "x-sim"})
    with pytest.raises(SimSettingsError):
        check_sim_settings({"CRIBA_ENV": "Sim", "CRIBA_HERMES_PROFILE": "x"})


def test_the_check_reads_the_process_environment_by_default(monkeypatch):
    monkeypatch.setenv("CRIBA_ENV", "sim")
    monkeypatch.setenv("CRIBA_HERMES_PROFILE", "criba-recruiter")
    with pytest.raises(SimSettingsError):
        check_sim_settings()
    monkeypatch.setenv("CRIBA_HERMES_PROFILE", "criba-recruiter-sim")
    check_sim_settings()


def test_the_compose_sim_override_passes_the_check():
    """docker-compose.sim.yml sets both values; keep it in step with the check."""
    from pathlib import Path

    import yaml

    override = yaml.safe_load(
        (Path(__file__).resolve().parents[4] / "docker-compose.sim.yml").read_text()
    )
    environment = override["services"]["backend"]["environment"]
    check_sim_settings(environment)


# --- SimClock ----------------------------------------------------------------------------


def test_sim_clock_only_moves_forward():
    clock = SimClock()
    assert isinstance(clock, Clock)
    real = datetime.now(UTC)
    assert abs((clock.now() - real).total_seconds()) < 2
    after = clock.advance(3600)
    assert timedelta(minutes=59) < after - real < timedelta(minutes=61)
    assert clock.offset_seconds == 3600
    with pytest.raises(ValueError):
        clock.advance(0)
    with pytest.raises(ValueError):
        clock.advance_to(datetime.now(UTC))
    with pytest.raises(ValueError):
        clock.advance_to(datetime(2030, 1, 1))  # naive
    target = datetime.now(UTC) + timedelta(days=7)
    clock.advance_to(target)
    assert abs((clock.now() - target).total_seconds()) < 2
    clock.reset()
    assert clock.offset_seconds == 0
    assert abs((clock.now() - datetime.now(UTC)).total_seconds()) < 2


# --- endpoints ---------------------------------------------------------------------------


def test_clock_endpoints_are_disabled_outside_sim(env, chat_contract):
    client = env.client()
    for method, path, body in (
        ("get", "/api/sim/clock", None),
        ("post", "/api/sim/clock/advance", {"seconds": 60}),
        ("post", "/api/sim/clock/reset", None),
    ):
        response = client.request(method.upper(), path, json=body)
        assert response.status_code == 404
        assert response.json() == {
            "error": "sim_disabled",
            "message": "The sim clock is available only when CRIBA_ENV=sim.",
        }
    assert env.due_jobs == [0]


def sim_env(make_env):
    env = make_env(env="sim")
    env.ctx.clock = SimClock()
    return env


def test_get_clock(make_env, chat_contract):
    env = sim_env(make_env)
    response = env.client().get("/api/sim/clock")
    body = chat_contract.conforms(response, "getSimClock")
    assert body["offset_seconds"] == 0
    assert body["due_jobs_run"] == 0
    assert abs((datetime.fromisoformat(body["now"]) - datetime.now(UTC)).total_seconds()) < 3


def test_advance_by_seconds_runs_due_jobs_and_reports_how_many(make_env, chat_contract):
    env = sim_env(make_env)
    calls = []
    env.due_jobs[0] = 3
    env.ctx.run_due_jobs = lambda: (calls.append(env.ctx.clock.offset_seconds), env.due_jobs[0])[1]
    response = env.client().post("/api/sim/clock/advance", json={"seconds": 3600})
    body = chat_contract.conforms(response, "advanceSimClock")
    assert body["offset_seconds"] == 3600
    assert body["due_jobs_run"] == 3
    assert calls == [3600]  # the jobs ran after the clock moved
    assert env.ctx.clock.offset_seconds == 3600


def test_advance_to_an_instant(make_env, chat_contract):
    env = sim_env(make_env)
    target = (datetime.now(UTC) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = env.client().post("/api/sim/clock/advance", json={"to": target})
    body = chat_contract.conforms(response, "advanceSimClock")
    assert body["now"][:16] == target[:16]
    assert abs(body["offset_seconds"] - 7 * 86400) < 3


def test_advance_never_goes_backwards(make_env, chat_contract):
    env = sim_env(make_env)
    client = env.client()
    client.post("/api/sim/clock/advance", json={"seconds": 7200})
    past = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = client.post("/api/sim/clock/advance", json={"to": past})
    assert response.status_code == 422
    chat_contract.conforms(response, "advanceSimClock")
    assert env.ctx.clock.offset_seconds == 7200


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"seconds": 5, "to": "2030-01-01T00:00:00Z"},
        {"seconds": 0},
        {"seconds": -5},
        {"seconds": 7776001},
        {"seconds": "soon"},
        {"to": "2030-01-01T00:00:00"},
        {"minutes": 5},
    ],
)
def test_advance_validates_its_body(make_env, chat_contract, payload):
    env = sim_env(make_env)
    response = env.client().post("/api/sim/clock/advance", json=payload)
    assert response.status_code == 422, payload
    assert chat_contract.conforms(response, "advanceSimClock")["error"] == "validation_error"
    assert env.ctx.clock.offset_seconds == 0


def test_advance_without_a_scheduler_reports_zero_jobs(make_env):
    env = sim_env(make_env)
    env.ctx.run_due_jobs = None
    body = env.client().post("/api/sim/clock/advance", json={"seconds": 60}).json()
    assert body["due_jobs_run"] == 0


def test_reset(make_env, chat_contract):
    env = sim_env(make_env)
    client = env.client()
    client.post("/api/sim/clock/advance", json={"seconds": 5000})
    response = client.post("/api/sim/clock/reset")
    body = chat_contract.conforms(response, "resetSimClock")
    assert body["offset_seconds"] == 0
    assert body["due_jobs_run"] == 0
    assert env.ctx.clock.offset_seconds == 0


def test_a_non_sim_clock_in_sim_mode_is_reported_not_ignored(make_env):
    env = make_env(env="sim")  # the fixture's FakeClock, not a SimClock
    response = env.client().get("/api/sim/clock")
    assert response.status_code == 500
    assert response.json()["error"] == "internal"


def test_context_from_env_installs_a_sim_clock_only_in_sim(tmp_path):
    from criba.api.context import ApiContext
    from criba.screening.clock import SystemClock

    sim = ApiContext.from_env({"CRIBA_ENV": "sim", "CRIBA_DB_PATH": str(tmp_path / "x.db")})
    prod = ApiContext.from_env({"CRIBA_DB_PATH": str(tmp_path / "x.db")})
    assert isinstance(sim.clock, SimClock) and sim.settings.is_sim
    assert isinstance(prod.clock, SystemClock) and not prod.settings.is_sim
    assert sim.settings.usage_environment == "sim" and prod.settings.usage_environment == "prod"
