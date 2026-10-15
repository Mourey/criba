"""Checks against the live ``hermes`` container (``pytest -m hermes``). No model calls.

Start it first:  docker compose -p <project> up -d --build hermes
and, if the project is not ``criba``, set COMPOSE_PROJECT_NAME to match.

The container is healthy, the plugin is loaded, the configuration the agent actually resolves
is the one in src/hermes/, each profile has its own secrets, and a tool call goes from Hermes's
dispatcher to the backend with the right session_id.
"""

from __future__ import annotations

import hmac
import json
import re
from types import ModuleType
from typing import Any

import jsonschema
import pytest

from .conftest import (
    CONTRACT_PATH,
    HERMES_DATA,
    HERMES_SRC,
    PLUGIN_DIR,
    PROFILE_SKILLS,
    PROFILES,
    SKILLS_SRC,
    HermesContainer,
)

pytestmark = pytest.mark.hermes

ENV_FILE = ".env"  # Hermes's per-home secrets file; written by src/docker/hermes/entrypoint.sh


def same(a: str, b: str) -> bool:
    """Compare secrets without letting pytest print them on failure."""
    return hmac.compare_digest(a.encode(), b.encode())


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def last_boot(log: str) -> str:
    """The part of gateway.log since the gateway last started."""
    return log[log.rfind("Starting Hermes Gateway...") :]


def schema_differences(actual: Any, expected: Any, path: str = "") -> list[str]:
    """Where two schemas differ, ignoring what Hermes adds when it registers a tool: an empty
    `required` list."""
    if isinstance(actual, dict) and isinstance(expected, dict):
        found: list[str] = []
        for key in sorted(set(actual) | set(expected)):
            if key == "required" and not actual.get(key) and not expected.get(key):
                continue
            if key not in actual or key not in expected:
                side = "missing in Hermes" if key not in actual else "extra in Hermes"
                found.append(f"{path}/{key} ({side})")
            else:
                found += schema_differences(actual[key], expected[key], f"{path}/{key}")
        return found
    return [] if actual == expected else [f"{path} ({actual!r} != {expected!r})"]


# --- health ----------------------------------------------------------------------------------


def test_container_is_healthy_and_serves_the_api(hermes_container: HermesContainer) -> None:
    status, body = hermes_container.http("GET", "/health")
    assert status == 200, body


def test_only_the_api_server_platform_starts(hermes_container: HermesContainer) -> None:
    """No messaging adapter may start, whatever the environment holds (spike 2, finding 12)."""
    boot = last_boot(hermes_container.read_file(f"{HERMES_DATA}/logs/gateway.log"))
    connecting = re.findall(r"Connecting to (\w+)\.\.\.", boot)
    assert connecting == ["api_server"], connecting
    assert "Gateway running with 1 platform(s)" in boot


def test_all_profiles_are_served_by_the_one_gateway(hermes_container: HermesContainer) -> None:
    boot = last_boot(hermes_container.read_file(f"{HERMES_DATA}/logs/gateway.log"))
    ticking = re.search(r"Cron scheduler will tick \d+ profile\(s\): (\[.*?\])", boot)
    assert ticking, "the gateway did not report its profiles"
    for name in PROFILES:
        assert f"'{name}'" in ticking.group(1)


def test_container_environment_carries_no_host_credentials(
    hermes_container: HermesContainer,
) -> None:
    """Compose lists every variable explicitly. A host token (TELEGRAM_BOT_TOKEN once started a
    Telegram adapter in a spike) must not be there, and no secret rides in the environment."""
    names = {
        line.partition("=")[0] for line in hermes_container.exec("env").splitlines() if "=" in line
    }
    offenders = sorted(
        n for n in names if re.search(r"TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL", n)
    )
    assert offenders == [], f"secrets in the container environment: {offenders}"
    assert not any(n.startswith(("TELEGRAM", "DISCORD", "SLACK", "WHATSAPP")) for n in names)
    assert hermes_container.exec("printenv", "HERMES_HOME").strip() == HERMES_DATA


# --- the plugin and the lean toolset ---------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_plugin_is_loaded_and_the_toolset_is_lean(
    introspection: dict[str | None, dict[str, Any]], contract: dict[str, Any], profile: str
) -> None:
    seen = introspection[profile]
    assert seen["toolsets"] == ["criba-tools"]
    assert [t["name"] for t in seen["tools"]] == sorted(contract["x-tools"])
    assert {t["toolset"] for t in seen["tools"]} == {"criba-tools"}
    # Nothing else: no memory, session_search, terminal, file, browser, web, skills or
    # delegation tool.
    assert len(seen["tools"]) == 9  # the eight of Wave 0 plus search_zones (spec 006a)
    schema_tokens = len(json.dumps(seen["definitions"], ensure_ascii=False)) / 4
    assert schema_tokens < 2000, f"tool schemas are about {schema_tokens:.0f} tokens"


def test_root_profile_has_no_tools_at_all(
    introspection: dict[str | None, dict[str, Any]],
) -> None:
    assert introspection[None]["tools"] == []
    assert "criba-tools" not in json.dumps(introspection[None]["config"]["plugins"])


@pytest.mark.parametrize("profile", PROFILES)
def test_schemas_in_the_image_equal_the_contract(
    introspection: dict[str | None, dict[str, Any]], contract_module: ModuleType, profile: str
) -> None:
    """The image bakes contracts/tools.json at build time. A stale image would show here."""
    expected = {s.name: s.parameters for s in contract_module.tool_specs()}
    actual = {
        d["function"]["name"]: d["function"]["parameters"]
        for d in introspection[profile]["definitions"]
    }
    assert actual.keys() == expected.keys()
    differences = [
        f"{name}{path}"
        for name in expected
        for path in schema_differences(actual[name], expected[name])
    ]
    assert differences == []


@pytest.mark.parametrize("profile", PROFILES)
def test_no_identity_property_in_any_schema_the_agent_sees(
    introspection: dict[str | None, dict[str, Any]], profile: str
) -> None:
    text = json.dumps(introspection[profile]["definitions"])
    assert "application_id" not in text
    assert "candidate_id" not in text


@pytest.mark.parametrize("profile", PROFILES)
def test_effective_configuration_is_the_intended_one(
    introspection: dict[str | None, dict[str, Any]], profile: str
) -> None:
    config = introspection[profile]["config"]
    assert config["memory"]["memory_enabled"] is False
    assert config["memory"]["user_profile_enabled"] is False
    assert config["plugins"]["enabled"] == ["criba-tools"]
    assert config["platform_toolsets"] == {"api_server": ["criba-tools"]}
    assert str(config["tools"]["tool_search"]["enabled"]).lower() == "off"
    assert {"memory", "session_search"} <= set(config["agent"]["disabled_toolsets"])
    assert config["agent"]["max_turns"] == 20
    assert config["auxiliary"]["openrouter_model"] == "anthropic/claude-haiku-5.5"
    assert config["auxiliary"]["title_generation"]["enabled"] is False
    assert config["auxiliary"]["background_review"]["enabled"] is False
    assert config["terminal"]["cwd"] == "/opt/data/workspace"


def test_effective_models(
    introspection: dict[str | None, dict[str, Any]], hermes_container: HermesContainer
) -> None:
    recruiter = introspection["criba-recruiter"]["config"]["model"]
    assert recruiter["default"] == "anthropic/claude-haiku-5.5"
    assert recruiter["provider"] == "openrouter"

    sim = introspection["criba-recruiter-sim"]["config"]["model"]
    assert sim["provider"] == "custom"
    assert sim["base_url"] == hermes_container.exec("printenv", "CRIBA_SIM_LLM_BASE_URL").strip()
    assert sim["default"] == hermes_container.exec("printenv", "CRIBA_SIM_LLM_MODEL").strip()
    assert "@@" not in json.dumps(sim), "an unfilled placeholder reached the config"

    # The voice flow's own profiles (ADR 0059): the same models as their text twins.
    assert introspection["criba-recruiter-voice"]["config"]["model"] == recruiter
    assert introspection["criba-recruiter-voice-sim"]["config"]["model"] == sim


def test_every_messaging_platform_is_disabled_in_every_profile(
    introspection: dict[str | None, dict[str, Any]],
) -> None:
    for profile, seen in introspection.items():
        platforms = seen["config"]["platforms"]
        assert platforms, profile
        enabled = sorted(name for name, cfg in platforms.items() if cfg.get("enabled"))
        assert enabled == [], (profile, enabled)


# --- identity and skills installed from the sources ------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_soul_is_the_identity_plus_the_screener_skill(
    hermes_container: HermesContainer, profile: str
) -> None:
    """Each profile's own skill: the voice profiles get the Voice Screener (ADR 0059)."""
    installed = hermes_container.read_file(f"{HERMES_DATA}/profiles/{profile}/SOUL.md")
    soul = (HERMES_SRC / "profiles" / profile / "SOUL.md").read_text(encoding="utf-8")
    skill_path = PROFILE_SKILLS[profile]
    skill = (SKILLS_SRC / skill_path / "SKILL.md").read_text(encoding="utf-8")
    body = skill.split("\n---\n", 1)[1]
    assert installed == soul + "\n" + body
    assert "name: screener" not in installed, "frontmatter leaked into the prompt"
    name = skill_path.rsplit("/", 1)[1]
    assert f"name: {name}" not in installed, "frontmatter leaked into the prompt"
    assert "application_id" not in installed


@pytest.mark.parametrize("profile", PROFILES)
def test_plugin_and_skill_are_installed_in_the_profile_home(
    hermes_container: HermesContainer, profile: str
) -> None:
    home = f"{HERMES_DATA}/profiles/{profile}"
    baked = hermes_container.read_file(f"{home}/plugins/criba_tools/tools.json")
    assert baked == CONTRACT_PATH.read_text(encoding="utf-8")
    listing = hermes_container.exec("ls", f"{home}/plugins/criba_tools").split()
    assert {"__init__.py", "client.py", "contract.py", "plugin.yaml", "tools.json"} <= set(listing)
    for filename in ("__init__.py", "client.py", "contract.py", "plugin.yaml"):
        installed = hermes_container.read_file(f"{home}/plugins/criba_tools/{filename}")
        assert installed == (PLUGIN_DIR / filename).read_text(encoding="utf-8")
    if PROFILE_SKILLS[profile] == "criba/screener":
        assert hermes_container.exec("ls", f"{home}/skills/criba/screener").split() == ["SKILL.md"]
    skill_dir = f"{home}/skills/{PROFILE_SKILLS[profile]}"
    assert hermes_container.exec("ls", skill_dir).split() == ["SKILL.md"]
    assert hermes_container.exec("ls", f"{home}/skills/criba").split() == [
        PROFILE_SKILLS[profile].rsplit("/", 1)[1]
    ], "a profile installs only the skill it lists"
    # The root profile gets neither: it has no job.
    assert "criba_tools" not in hermes_container.exec("ls", f"{HERMES_DATA}/plugins")


# --- secrets ---------------------------------------------------------------------------------


def test_each_profile_has_its_own_key_and_the_right_provider_key(
    hermes_container: HermesContainer,
) -> None:
    api_key = hermes_container.secret("hermes_api_server_key")
    openrouter_key = hermes_container.secret("openrouter_api_key")
    tools_secret = hermes_container.secret("criba_tools_secret")

    root = parse_env(hermes_container.read_file(f"{HERMES_DATA}/{ENV_FILE}"))
    assert root["API_SERVER_ENABLED"] == "true"
    assert root["API_SERVER_HOST"] == "0.0.0.0"
    assert root["API_SERVER_PORT"] == "8642"
    assert len(root["API_SERVER_KEY"]) >= 16
    assert not same(root["API_SERVER_KEY"], api_key), "root and profile keys must differ"
    assert same(root["OPENROUTER_API_KEY"], openrouter_key)
    assert same(root["CRIBA_TOOLS_SECRET"], tools_secret)

    recruiter = parse_env(
        hermes_container.read_file(f"{HERMES_DATA}/profiles/criba-recruiter/{ENV_FILE}")
    )
    assert same(recruiter["API_SERVER_KEY"], api_key)
    assert same(recruiter["OPENROUTER_API_KEY"], openrouter_key)

    sim = parse_env(
        hermes_container.read_file(f"{HERMES_DATA}/profiles/criba-recruiter-sim/{ENV_FILE}")
    )
    assert same(sim["API_SERVER_KEY"], api_key)
    assert "OPENROUTER_API_KEY" not in sim, "the simulator must not be able to reach OpenRouter"

    voice = parse_env(
        hermes_container.read_file(f"{HERMES_DATA}/profiles/criba-recruiter-voice/{ENV_FILE}")
    )
    assert same(voice["API_SERVER_KEY"], api_key)
    assert same(voice["OPENROUTER_API_KEY"], openrouter_key)

    voice_sim = parse_env(
        hermes_container.read_file(f"{HERMES_DATA}/profiles/criba-recruiter-voice-sim/{ENV_FILE}")
    )
    assert same(voice_sim["API_SERVER_KEY"], api_key)
    assert "OPENROUTER_API_KEY" not in voice_sim, "a sim profile must not reach OpenRouter"

    for env in (root, recruiter, sim, voice, voice_sim):
        assert not [n for n in env if n.startswith(("TELEGRAM", "DISCORD", "SLACK", "WHATSAPP"))]


def test_secret_files_are_private_to_the_hermes_user(hermes_container: HermesContainer) -> None:
    for path in (
        f"{HERMES_DATA}/{ENV_FILE}",
        f"{HERMES_DATA}/profiles/criba-recruiter/{ENV_FILE}",
        f"{HERMES_DATA}/profiles/criba-recruiter-sim/{ENV_FILE}",
        f"{HERMES_DATA}/profiles/criba-recruiter-voice/{ENV_FILE}",
        f"{HERMES_DATA}/profiles/criba-recruiter-voice-sim/{ENV_FILE}",
    ):
        assert hermes_container.exec("stat", "-c", "%U %a", path).strip() == "hermes 600"


def test_api_keys_are_per_profile(hermes_container: HermesContainer) -> None:
    """`/p/<profile>/` accepts the profile's own key and rejects the root key (docs:
    multi-profile-gateways.md, 'Authentication follows the profile named in the URL')."""
    api_key = hermes_container.secret("hermes_api_server_key")
    root_key = parse_env(hermes_container.read_file(f"{HERMES_DATA}/{ENV_FILE}"))["API_SERVER_KEY"]
    for profile in PROFILES:
        path = f"/p/{profile}/v1/runs/run_does_not_exist"
        status, body = hermes_container.http("GET", path, key=api_key)
        assert status == 404, (profile, status, body)
        assert body["error"]["code"] == "run_not_found"
        assert hermes_container.http("GET", path, key=root_key)[0] == 401
        assert hermes_container.http("GET", path, key="x" * 40)[0] == 401
        assert hermes_container.http("GET", path)[0] == 401
    # An unknown profile is a 404 at the route, not a fallback to another profile.
    assert hermes_container.http("GET", "/p/ghost/v1/runs/x", key=api_key)[0] == 404


# --- the plugin forwards correctly, through Hermes's own dispatcher ---------------------------


def _forward(
    hermes_container: HermesContainer, profile: str, scenario: dict[str, Any]
) -> dict[str, Any]:
    scenario = {"secret": "container-test-secret", **scenario}
    return hermes_container.python_script("forward.py", json.dumps(scenario), profile=profile)


@pytest.mark.parametrize("profile", PROFILES)
def test_plugin_forwards_to_a_recording_backend_through_the_dispatcher(
    hermes_container: HermesContainer, contract: dict[str, Any], profile: str
) -> None:
    args = {"field": "zone", "value": "vivo en el centro"}
    out = _forward(
        hermes_container,
        profile,
        {
            "calls": [
                {"tool": "record_answer", "args": args, "session_id": "app_container_A"},
                {"tool": "get_application", "args": {}, "session_id": "app_container_B"},
            ],
            "responses": {
                "record_answer": {
                    "status": 200,
                    "body": {
                        "ok": False,
                        "error": "invalid_value",
                        "hint": "Ask again.",
                        "attempt": 1,
                    },
                }
            },
        },
    )
    first, second = out["requests"]
    assert first["method"] == "POST"
    assert first["path"] == "/internal/tools/record_answer"
    assert first["authorization"] == "Bearer container-test-secret"
    assert first["content_type"] == "application/json"
    assert first["body"] == {
        "tool": "record_answer",
        "args": args,
        "session_id": "app_container_A",
    }
    assert second["path"] == "/internal/tools/get_application"
    assert second["body"] == {
        "tool": "get_application",
        "args": {},
        "session_id": "app_container_B",
    }
    for request in (first, second):
        jsonschema.validate(request["body"], contract)

    # The model gets exactly what the backend answered.
    assert out["results"][0] == {
        "ok": False,
        "error": "invalid_value",
        "hint": "Ask again.",
        "attempt": 1,
    }
    assert out["results"][1] == {"ok": True, "value": {"stub": "get_application"}}


def test_backend_failure_reaches_the_model_as_an_internal_error(
    hermes_container: HermesContainer,
) -> None:
    out = _forward(
        hermes_container,
        "criba-recruiter",
        {
            "calls": [{"tool": "finish_screening", "args": {}, "session_id": "app_container_C"}],
            "responses": {"finish_screening": {"status": 500, "body": {"detail": "boom"}}},
        },
    )
    result = out["results"][0]
    assert result["ok"] is False
    assert result["error"] == "internal"
    assert "500" in result["hint"]


def test_a_call_without_a_session_never_reaches_the_backend(
    hermes_container: HermesContainer,
) -> None:
    out = _forward(
        hermes_container,
        "criba-recruiter",
        {"calls": [{"tool": "get_application", "args": {}, "session_id": None}]},
    )
    assert out["requests"] == []
    assert out["results"][0]["error"] == "unknown_session"
