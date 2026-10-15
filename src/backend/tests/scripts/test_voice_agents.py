"""The ElevenLabs agents as code (spec voice-agent agents-config, AG1-AG7, AG10, R18).

``criba.channel.voice_agents_config`` holds the desired config and the drift comparison;
``src/scripts/voice_agents.py`` is the CLI over it, loaded by path as ``test_select_tests.py``
loads its script. Every request goes to an in-memory fake of the ElevenLabs API on an
``httpx.MockTransport``; an autouse guard makes any real HTTP transport raise, and no test
reads ``secrets/``. The key, the LLM secret and the URLs are sentinels written to
``tmp_path`` files.
"""

from __future__ import annotations

import copy as copy_module
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import httpx
import pytest

from criba.channel import copy
from criba.channel import voice_agents_config as vac
from criba.channel.voice_agents_config import AgentCheckError, desired_agent
from criba.channel.voice_config import VoiceSettings

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "src" / "scripts" / "voice_agents.py"
KEY_JSON = (
    REPO / "src" / "tests" / "fixtures" / "spikes" / "004-spanish-voice" / "listening" / "key.json"
)

API_KEY = "sentinel-elevenlabs-key"
LLM_SECRET = "sentinel-voice-llm-secret"
BASE_URL = "https://voice.example.test"
WEBHOOK_URL = "https://hooks.example.test/api/voice/webhooks/post-call"
SENTINELS = (API_KEY, LLM_SECRET, "voice.example.test", "hooks.example.test")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """AG10: a request that escapes the fake transport fails the test."""

    def refuse(self: Any, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"real network request attempted: {request.method}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


def voice_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    key = tmp_path / "elevenlabs_api_key.txt"
    key.write_text(API_KEY + "\n", encoding="utf-8")
    llm = tmp_path / "criba_voice_llm_secret.txt"
    llm.write_text(LLM_SECRET + "\n", encoding="utf-8")
    env = {
        "CRIBA_VOICE": "on",
        "CRIBA_VOICE_AGENT_ES": "agent_es_1",
        "CRIBA_VOICE_AGENT_MX": "agent_mx_1",
        "CRIBA_VOICE_PUBLIC_BASE_URL": BASE_URL,
        "CRIBA_ELEVENLABS_API_KEY_FILE": str(key),
        "CRIBA_VOICE_LLM_SECRET_FILE": str(llm),
    }
    env.update(extra)
    return env


@pytest.fixture
def settings(tmp_path: Path) -> VoiceSettings:
    return VoiceSettings.from_env(voice_env(tmp_path))


def leaves(tree: Any, prefix: str = "") -> dict[str, Any]:
    """Dotted path -> value for every leaf; an empty dict or list is a leaf."""
    if isinstance(tree, dict) and tree:
        out: dict[str, Any] = {}
        for key, value in tree.items():
            out.update(leaves(value, f"{prefix}.{key}" if prefix else key))
        return out
    return {prefix: tree}


def get_path(tree: Any, path: str) -> Any:
    for part in path.split("."):
        tree = tree[part]
    return tree


def set_path(tree: dict[str, Any], path: str, value: Any) -> None:
    *parents, last = path.split(".")
    for part in parents:
        tree = tree.setdefault(part, {})
    tree[last] = value


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = json.loads(json.dumps(value))


class FakeElevenLabs:
    """The ElevenLabs paths the module uses, in memory, field names from the published
    OpenAPI (step 1's record). Any other path raises, so an unmodelled call fails the test."""

    def __init__(self) -> None:
        self.agents: dict[str, dict[str, Any]] = {}
        self.secrets: list[dict[str, Any]] = []
        self.webhooks: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.bodies: list[Any] = []
        self.page_size = 100
        self.fail: dict[tuple[str, str], str] = {}
        self.transport = httpx.MockTransport(self.handle)
        self._next = 0

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method != "GET"]

    def write_paths(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.writes()]

    def new_id(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}_new_{self._next}"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content) if request.content else None
        self.bodies.append(body)
        method, path = request.method, request.url.path
        assert request.url.host == "api.elevenlabs.io"
        assert API_KEY not in str(request.url)
        if request.headers.get("xi-api-key") != API_KEY:
            return httpx.Response(401, json={"detail": "bad key"})
        failure = self.fail.get((method, path))
        if failure == "500":
            return httpx.Response(500, json={"detail": "boom"})
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if failure == "nonjson":
            return httpx.Response(200, text="<html>not json</html>")
        if failure == "connect":
            raise httpx.ConnectError("refused", request=request)
        if failure == "array":
            return httpx.Response(200, json=[])
        if failure == "empty":
            return httpx.Response(200, json={})
        if failure == "endless":
            return httpx.Response(200, json={"secrets": [], "next_cursor": "again"})
        parts = path.strip("/").split("/")
        if parts[:3] == ["v1", "convai", "agents"]:
            return self.agents_path(method, parts[3:], request, body)
        if parts[:3] == ["v1", "convai", "secrets"]:
            return self.secrets_path(method, parts[3:], body)
        if parts == ["v1", "workspace", "webhooks"] and method == "GET":
            return httpx.Response(200, json={"webhooks": self.webhooks})
        raise AssertionError(f"unmodelled ElevenLabs request: {method} {path}")

    def agents_path(
        self, method: str, rest: list[str], request: httpx.Request, body: Any
    ) -> httpx.Response:
        if not rest and method == "GET":
            start = int(request.url.params.get("cursor") or 0)
            size = min(int(request.url.params.get("page_size") or 30), self.page_size)
            items = list(self.agents.items())
            page = items[start : start + size]
            more = start + size < len(items)
            return httpx.Response(
                200,
                json={
                    "agents": [
                        {"agent_id": aid, "name": a["name"], "voice_id": "", "tags": []}
                        for aid, a in page
                    ],
                    "has_more": more,
                    "next_cursor": str(start + size) if more else None,
                },
            )
        if rest == ["create"] and method == "POST":
            agent_id = self.new_id("agent")
            self.agents[agent_id] = {"agent_id": agent_id, **body}
            return httpx.Response(200, json={"agent_id": agent_id})
        if len(rest) == 1 and method in ("GET", "PATCH"):
            agent = self.agents.get(rest[0])
            if agent is None:
                return httpx.Response(404, json={"detail": "not found"})
            if method == "PATCH":
                deep_merge(agent, body)
            return httpx.Response(200, json=agent)
        raise AssertionError(f"unmodelled agents request: {method} {rest}")

    def secrets_path(self, method: str, rest: list[str], body: Any) -> httpx.Response:
        if not rest and method == "GET":
            listed = [{k: v for k, v in s.items() if k != "value"} for s in self.secrets]
            return httpx.Response(200, json={"secrets": listed, "next_cursor": None})
        if not rest and method == "POST":
            assert body["type"] == "new"
            secret = {
                "type": "stored",
                "secret_id": self.new_id("secret"),
                "name": body["name"],
                "value": body["value"],
                "used_by": {},
            }
            self.secrets.append(secret)
            return httpx.Response(200, json={k: secret[k] for k in ("type", "secret_id", "name")})
        if len(rest) == 1 and method == "PATCH":
            assert body["type"] == "update"
            for secret in self.secrets:
                if secret["secret_id"] == rest[0]:
                    secret["value"] = body["value"]
                    return httpx.Response(
                        200, json={k: secret[k] for k in ("type", "secret_id", "name")}
                    )
            return httpx.Response(404, json={"detail": "not found"})
        raise AssertionError(f"unmodelled secrets request: {method} {rest}")


SECRET_ID = "secret_llm_1"


def live_agent(country: vac.Country, settings: VoiceSettings) -> dict[str, Any]:
    """A live agent that matches the desired config, with the extra fields and defaults
    ElevenLabs adds (unowned, so never drift)."""
    agent = json.loads(json.dumps(desired_agent(country, settings)))
    agent["agent_id"] = settings.agent_es if country == "ES" else settings.agent_mx
    prompt = agent["conversation_config"]["agent"]["prompt"]
    prompt["custom_llm"]["api_key"] = {"secret_id": SECRET_ID}
    prompt["custom_llm"]["request_headers"] = {}
    prompt["temperature"] = 0.0
    prompt["built_in_tools"]["end_call"] = {
        **prompt["built_in_tools"]["end_call"],
        "description": "Ends the call.",
        "response_timeout_secs": 20,
    }
    agent["conversation_config"]["tts"]["stability"] = 0.5
    agent["conversation_config"]["asr"] = {"quality": "high", "keywords": []}
    agent["conversation_config"]["turn"]["soft_timeout_config"]["randomize_fillers"] = False
    platform = agent["platform_settings"]
    platform["call_limits"] = {"agent_concurrency_limit": -1, "bursting_enabled": True}
    platform["privacy"]["delete_audio"] = False
    platform["workspace_overrides"]["webhooks"]["send_audio"] = None
    platform["workspace_overrides"]["webhooks"]["transcript_format"] = "json"
    agent["metadata"] = {"created_at_unix_secs": 1}
    agent["phone_numbers"] = []
    agent["whatsapp_accounts"] = []
    agent["workflow"] = {"nodes": {}, "edges": {}, "prevent_subagent_loops": False}
    return agent


@pytest.fixture
def fake(settings: VoiceSettings) -> FakeElevenLabs:
    """The fake account with both agents matching and the LLM secret present."""
    fake = FakeElevenLabs()
    fake.secrets.append(
        {
            "type": "stored",
            "secret_id": SECRET_ID,
            "name": "criba_voice_llm_secret",
            "value": LLM_SECRET,
            "used_by": {},
        }
    )
    for country in ("ES", "MX"):
        agent = live_agent(country, settings)
        fake.agents[agent["agent_id"]] = agent
    return fake


# ---------------------------------------------------------------------------
# Step 2: the desired config (AG1, AG3-AG6, AG2's payload)
# ---------------------------------------------------------------------------


def test_payload_fields(settings: VoiceSettings) -> None:
    for country in ("ES", "MX"):
        body = desired_agent(country, settings)
        conv = body["conversation_config"]
        agent = conv["agent"]
        prompt = agent["prompt"]
        assert agent["language"] == "es"
        assert agent["disable_first_message_interruptions"] is True
        assert conv["language_presets"] == {}
        assert prompt["llm"] == "custom-llm"
        assert prompt["custom_llm"] == {
            "url": BASE_URL + "/api/voice/v1",
            "model_id": "criba-voice",
            "api_key": {"secret_name": "criba_voice_llm_secret"},
            "api_type": "chat_completions",
        }
        assert "request_headers" not in prompt["custom_llm"]
        assert prompt["backup_llm_config"] == {"preference": "disabled"}
        assert set(k for k, v in prompt["built_in_tools"].items() if v is not None) == {"end_call"}
        assert prompt["built_in_tools"]["end_call"]["params"] == {"system_tool_type": "end_call"}
        for name in ("tool_ids", "mcp_server_ids", "native_mcp_server_ids", "knowledge_base"):
            assert prompt[name] == []
        assert "workflow" not in body
        assert conv["tts"]["model_id"] == "eleven_v4_turbo"
        assert conv["tts"]["text_normalisation_type"] == "elevenlabs"
        assert conv["turn"]["soft_timeout_config"]["timeout_seconds"] == 1.0
        assert conv["conversation"]["max_duration_seconds"] == 1200
        platform = body["platform_settings"]
        assert platform["auth"] == {"enable_auth": True, "allowlist": []}
        assert platform["privacy"] == {"record_voice": False, "retention_days": 1}
        webhooks = platform["workspace_overrides"]["webhooks"]
        assert webhooks["events"] == ["transcript"]
        assert "post_call_webhook_id" not in webhooks
        assert webhooks.get("send_audio") is not True
        assert "call_limits" not in platform
        assert "asr" not in conv


def test_payload_strips_a_trailing_slash(tmp_path: Path) -> None:
    env = voice_env(tmp_path, CRIBA_VOICE_PUBLIC_BASE_URL=BASE_URL + "/")
    body = desired_agent("ES", VoiceSettings.from_env(env))
    assert body["conversation_config"]["agent"]["prompt"]["custom_llm"]["url"] == (
        BASE_URL + "/api/voice/v1"
    )


def test_countries_differ_in_four_fields(settings: VoiceSettings) -> None:
    es = leaves(desired_agent("ES", settings))
    mx = leaves(desired_agent("MX", settings))
    assert es.keys() == mx.keys()
    differing = {path for path in es if es[path] != mx[path]}
    # AG1: only these may differ. The first message is each country's copy.py line, which
    # session-start wrote with the same words for es-ES and es-MX today.
    assert differing <= {
        "name",
        "conversation_config.tts.voice_id",
        "conversation_config.turn.soft_timeout_config.message",
        "conversation_config.turn.soft_timeout_config.additional_soft_timeout_messages",
        "conversation_config.agent.first_message",
    }
    assert {
        "name",
        "conversation_config.tts.voice_id",
        "conversation_config.turn.soft_timeout_config.message",
        "conversation_config.turn.soft_timeout_config.additional_soft_timeout_messages",
    } <= differing
    assert es["name"] == "criba-screener-es"
    assert mx["name"] == "criba-screener-mx"


def test_first_message_is_the_spoken_disclosure(settings: VoiceSettings) -> None:
    for country, tag in (("ES", "es-ES"), ("MX", "es-MX")):
        first = desired_agent(country, settings)["conversation_config"]["agent"]["first_message"]
        assert first == copy.spoken_disclosure("es", country)
        assert first == copy.SPOKEN_DISCLOSURE[tag]
        assert "(IA)" in first
        assert copy.discloses_ai(first)


def test_voice_ids_are_voice_a(settings: VoiceSettings) -> None:
    key = json.loads(KEY_JSON.read_text(encoding="utf-8"))
    for country in ("ES", "MX"):
        voice = desired_agent(country, settings)["conversation_config"]["tts"]["voice_id"]
        assert voice == key["agent_voices"][country]["A"]["voice_id"]


def test_overrides_off_except_extra_body(settings: VoiceSettings) -> None:
    overrides = desired_agent("ES", settings)["platform_settings"]["overrides"]
    assert overrides["custom_llm_extra_body"] is True
    assert overrides["enable_conversation_initiation_client_data_from_webhook"] is False
    assert overrides["enable_starting_workflow_node_id_from_client"] is False
    assert overrides["enable_procedure_ids_from_client"] is False
    flags = leaves(overrides["conversation_config_override"])
    # Every flag the published schema names (read in step 1), all false.
    assert {
        "agent.first_message",
        "agent.language",
        "agent.max_conversation_duration_message",
        "agent.prompt.prompt",
        "agent.prompt.llm",
        "agent.prompt.tool_ids",
        "agent.prompt.tool_ids_strict",
        "agent.prompt.native_mcp_server_ids",
        "agent.prompt.knowledge_base",
        "tts.voice_id",
        "tts.model_id",
        "tts.supported_voices",
        "tts.stability",
        "tts.speed",
        "tts.similarity_boost",
        "tts.pronunciation_dictionary_locators",
        "conversation.text_only",
        "conversation.max_duration_seconds",
        "asr.keywords",
        "turn.soft_timeout_config.message",
        "turn.soft_timeout_config.additional_soft_timeout_messages",
    } <= flags.keys()
    assert set(flags.values()) == {False}


@pytest.mark.parametrize(
    "base", [None, "", "   ", "http://voice.example.test", "https://", "ftp://x"]
)
def test_unsafe_base_url_refused(tmp_path: Path, base: str | None) -> None:
    env = voice_env(tmp_path)
    if base is None:
        del env["CRIBA_VOICE_PUBLIC_BASE_URL"]
    else:
        env["CRIBA_VOICE_PUBLIC_BASE_URL"] = base
    with pytest.raises(AgentCheckError) as caught:
        desired_agent("ES", VoiceSettings.from_env(env))
    assert "voice.example.test" not in str(caught.value)


def test_unknown_country_refused(settings: VoiceSettings) -> None:
    with pytest.raises(AgentCheckError):
        desired_agent(cast(Any, "FR"), settings)


def test_fillers(settings: VoiceSettings) -> None:
    for country in ("ES", "MX"):
        soft = desired_agent(country, settings)["conversation_config"]["turn"][
            "soft_timeout_config"
        ]
        phrases = [soft["message"], *soft["additional_soft_timeout_messages"]]
        assert len(phrases) == 3
        for phrase in phrases:
            assert phrase.strip()
            assert not re.search(r"\d", phrase)
            assert "?" not in phrase and "¿" not in phrase
            assert not re.search(r"https?://|www\.|\.\w{2,3}/", phrase)
        assert soft["use_llm_generated_message"] is False
        assert soft["timeout_seconds"] == 1.0
        # Owner decision 63: ElevenLabs keeps the cap at 3, so 3 is the desired value.
        assert soft["max_soft_timeouts_per_generation"] == 3
    assert vac.FILLERS["ES"] != vac.FILLERS["MX"]


# ---------------------------------------------------------------------------
# Step 3: the drift comparison (AG2, AG3, AG6, AG7)
# ---------------------------------------------------------------------------

OVR = "platform_settings.overrides"
CCO = OVR + ".conversation_config_override"
PROMPT = "conversation_config.agent.prompt"
WEBHOOKS = "platform_settings.workspace_overrides.webhooks"


def es_agent(fake: FakeElevenLabs) -> dict[str, Any]:
    return fake.agents["agent_es_1"]


def test_matching_agent_has_no_drift(settings: VoiceSettings, fake: FakeElevenLabs) -> None:
    assert vac.agent_drift(settings, transport=fake.transport) == []
    assert fake.requests, "the check read the live agents"
    assert {r.method for r in fake.requests} == {"GET"}
    paths = {r.url.path for r in fake.requests}
    assert {"/v1/convai/agents/agent_es_1", "/v1/convai/agents/agent_mx_1"} <= paths


@pytest.mark.parametrize(
    "path,value",
    [
        ("conversation_config.tts.stability", 0.9),
        (PROMPT + ".temperature", 0.7),
        ("conversation_config.asr.keywords", ["Zona Norte"]),
        ("platform_settings.call_limits.bursting_enabled", False),
        ("platform_settings.privacy.delete_audio", True),
        (WEBHOOKS + ".post_call_webhook_id", "webhook_someone"),
        ("conversation_config.vad", {"background_voice_detection": True}),
        ("metadata.created_at_unix_secs", 99),
    ],
)
def test_unowned_field_is_not_drift(
    settings: VoiceSettings, fake: FakeElevenLabs, path: str, value: Any
) -> None:
    set_path(es_agent(fake), path, value)
    assert vac.agent_drift(settings, transport=fake.transport) == []


@pytest.mark.parametrize(
    "path,value",
    [
        (CCO + ".agent.first_message", True),
        (CCO + ".agent.prompt.prompt", True),
        (CCO + ".tts.voice_id", True),
        (CCO + ".agent.brand_new_flag", True),
        (CCO + ".brand_new_section.anything", True),
        (OVR + ".custom_llm_extra_body", False),
        (OVR + ".enable_conversation_initiation_client_data_from_webhook", True),
        (OVR + ".enable_starting_workflow_node_id_from_client", True),
        (OVR + ".enable_procedure_ids_from_client", True),
        (OVR + ".enable_something_new_from_client", True),
    ],
)
def test_override_flags_are_drift(
    settings: VoiceSettings, fake: FakeElevenLabs, path: str, value: Any
) -> None:
    set_path(es_agent(fake), path, value)
    lines = vac.agent_drift(settings, transport=fake.transport)
    assert len(lines) == 1
    assert lines[0].startswith(f"ES {path}: ")


def test_missing_override_tree_is_not_drift(settings: VoiceSettings, fake: FakeElevenLabs) -> None:
    """A flag ElevenLabs leaves out is at its default, false."""
    del es_agent(fake)["platform_settings"]["overrides"]["conversation_config_override"]
    del es_agent(fake)["platform_settings"]["overrides"]["enable_procedure_ids_from_client"]
    assert vac.agent_drift(settings, transport=fake.transport) == []


# ElevenLabs' exact default workflow on every new agent (owner decision 63).
EL_DEFAULT_WORKFLOW: dict[str, Any] = {
    "edges": {},
    "nodes": {"start_node": {"type": "start", "position": {"x": 0, "y": 0}, "edge_order": []}},
    "subgraphs": {},
    "prevent_subagent_loops": False,
}


def test_elevenlabs_default_workflow_is_not_drift(
    settings: VoiceSettings, fake: FakeElevenLabs
) -> None:
    """Owner decision 63: only start nodes and no edges counts as no workflow."""
    es_agent(fake)["workflow"] = copy_module.deepcopy(EL_DEFAULT_WORKFLOW)
    assert vac.agent_drift(settings, transport=fake.transport) == []


def test_workflow_with_an_edge_is_drift(settings: VoiceSettings, fake: FakeElevenLabs) -> None:
    workflow = copy_module.deepcopy(EL_DEFAULT_WORKFLOW)
    workflow["edges"] = {"e1": {"source": "start_node", "target": "start_node"}}
    es_agent(fake)["workflow"] = workflow
    drift = vac.read_drift(settings, transport=fake.transport)
    assert [(d.path, d.fixable) for d in drift] == [("workflow", False)]


def test_workflow_with_subgraph_content_is_drift(
    settings: VoiceSettings, fake: FakeElevenLabs
) -> None:
    workflow = copy_module.deepcopy(EL_DEFAULT_WORKFLOW)
    workflow["subgraphs"] = {"s1": {"nodes": {}}}
    es_agent(fake)["workflow"] = workflow
    drift = vac.read_drift(settings, transport=fake.transport)
    assert [(d.path, d.fixable) for d in drift] == [("workflow", False)]


def test_workflow_with_a_non_start_node_is_drift(
    settings: VoiceSettings, fake: FakeElevenLabs
) -> None:
    workflow = copy_module.deepcopy(EL_DEFAULT_WORKFLOW)
    workflow["nodes"]["n2"] = {"type": "end"}
    es_agent(fake)["workflow"] = workflow
    drift = vac.read_drift(settings, transport=fake.transport)
    assert [(d.path, d.fixable) for d in drift] == [("workflow", False)]


def _add_tool(agent: dict[str, Any], name: str) -> None:
    agent["conversation_config"]["agent"]["prompt"]["built_in_tools"][name] = {
        "type": "system",
        "name": name,
        "params": {"system_tool_type": name},
    }


def _set(path: str, value: Any) -> Any:
    return lambda agent: set_path(agent, path, value)


UNSAFE: list[tuple[str, Any]] = [
    (PROMPT + ".backup_llm_config.preference", _set(PROMPT + ".backup_llm_config", {})),
    (
        PROMPT + ".backup_llm_config.preference",
        _set(PROMPT + ".backup_llm_config.preference", "default"),
    ),
    (PROMPT + ".built_in_tools.transfer_to_number", lambda a: _add_tool(a, "transfer_to_number")),
    (PROMPT + ".built_in_tools.language_detection", lambda a: _add_tool(a, "language_detection")),
    (PROMPT + ".built_in_tools.end_call", _set(PROMPT + ".built_in_tools.end_call", None)),
    (PROMPT + ".tool_ids", _set(PROMPT + ".tool_ids", ["tool_1"])),
    (PROMPT + ".mcp_server_ids", _set(PROMPT + ".mcp_server_ids", ["mcp_1"])),
    (PROMPT + ".native_mcp_server_ids", _set(PROMPT + ".native_mcp_server_ids", ["n_1"])),
    (PROMPT + ".knowledge_base", _set(PROMPT + ".knowledge_base", [{"type": "file", "id": "kb"}])),
    # Owner decision 63: a lone start node is ElevenLabs' default (no workflow); a second
    # node is still drift.
    (
        "workflow",
        _set("workflow.nodes", {"start": {"type": "start"}, "n2": {"type": "end"}}),
    ),
    (
        "conversation_config.language_presets",
        _set("conversation_config.language_presets", {"en": {}}),
    ),
    ("conversation_config.agent.language", _set("conversation_config.agent.language", "en")),
    ("phone_numbers", _set("phone_numbers", [{"provider": "twilio", "phone_number_id": "p"}])),
    ("whatsapp_accounts", _set("whatsapp_accounts", [{"phone_number_id": "w"}])),
    ("platform_settings.auth.enable_auth", _set("platform_settings.auth.enable_auth", False)),
    (
        "platform_settings.auth.allowlist",
        _set("platform_settings.auth.allowlist", [{"hostname": "x"}]),
    ),
    (
        "platform_settings.privacy.retention_days",
        _set("platform_settings.privacy.retention_days", -1),
    ),
    (
        "platform_settings.privacy.record_voice",
        _set("platform_settings.privacy.record_voice", True),
    ),
    (
        "platform_settings.privacy.record_voice",
        lambda a: a["platform_settings"]["privacy"].pop("record_voice"),
    ),
    (WEBHOOKS + ".events", _set(WEBHOOKS + ".events", ["transcript", "audio"])),
    (WEBHOOKS + ".events", _set(WEBHOOKS + ".events", ["transcript", "unredacted_audio"])),
    (WEBHOOKS + ".send_audio", _set(WEBHOOKS + ".send_audio", True)),
    (
        "conversation_config.tts.model_id",
        _set("conversation_config.tts.model_id", "eleven_flash_v2_5"),
    ),
    (
        "conversation_config.turn.soft_timeout_config.use_llm_generated_message",
        _set("conversation_config.turn.soft_timeout_config.use_llm_generated_message", True),
    ),
    (
        "conversation_config.conversation.max_duration_seconds",
        _set("conversation_config.conversation.max_duration_seconds", 3600),
    ),
    (PROMPT + ".custom_llm.url", _set(PROMPT + ".custom_llm.url", "https://stale.example.test/x")),
    ("name", _set("name", "renamed-in-console")),
]


@pytest.mark.parametrize("path,mutate", UNSAFE)
def test_unsafe_live_fields_are_drift(
    settings: VoiceSettings, fake: FakeElevenLabs, path: str, mutate: Any
) -> None:
    mutate(es_agent(fake))
    lines = vac.agent_drift(settings, transport=fake.transport)
    assert [line.split(":")[0] for line in lines] == [f"ES {path}"]


def test_secret_reference_drift(settings: VoiceSettings, fake: FakeElevenLabs) -> None:
    key_path = PROMPT + ".custom_llm.api_key"
    set_path(es_agent(fake), key_path, {"secret_id": "secret_someone_else"})
    lines = vac.agent_drift(settings, transport=fake.transport)
    assert lines == [f"ES {key_path}: <redacted> -> <redacted>"]
    fake.secrets.clear()
    lines = vac.agent_drift(settings, transport=fake.transport)
    assert [line.split(":")[0] for line in lines] == [f"ES {key_path}", f"MX {key_path}"]


def test_secret_lookup_reads_every_page(
    settings: VoiceSettings, fake: FakeElevenLabs, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = [
        {"secrets": [{"type": "stored", "secret_id": "s0", "name": "other"}], "next_cursor": "2"},
        {
            "secrets": [{"type": "stored", "secret_id": SECRET_ID, "name": vac.LLM_SECRET_NAME}],
            "next_cursor": None,
        },
    ]
    original = fake.secrets_path

    def paged(method: str, rest: list[str], body: Any) -> httpx.Response:
        if method == "GET" and not rest:
            cursor = fake.requests[-1].url.params.get("cursor")
            return httpx.Response(200, json=pages[1 if cursor == "2" else 0])
        return original(method, rest, body)

    monkeypatch.setattr(fake, "secrets_path", paged)
    assert vac.agent_drift(settings, transport=fake.transport) == []


@pytest.mark.parametrize(
    "where,failure",
    [
        (("GET", "/v1/convai/agents/agent_es_1"), "500"),
        (("GET", "/v1/convai/agents/agent_mx_1"), "timeout"),
        (("GET", "/v1/convai/agents/agent_es_1"), "nonjson"),
        (("GET", "/v1/convai/secrets"), "500"),
        (("GET", "/v1/convai/secrets"), "nonjson"),
        (("GET", "/v1/convai/secrets"), "connect"),
        (("GET", "/v1/convai/secrets"), "empty"),
        (("GET", "/v1/convai/secrets"), "endless"),
        (("GET", "/v1/convai/agents/agent_es_1"), "array"),
    ],
)
def test_errors_raise(
    settings: VoiceSettings, fake: FakeElevenLabs, where: tuple[str, str], failure: str
) -> None:
    fake.fail[where] = failure
    with pytest.raises(AgentCheckError) as caught:
        vac.agent_drift(settings, transport=fake.transport)
    for sentinel in SENTINELS:
        assert sentinel not in str(caught.value)


def test_errors_raise_on_a_missing_agent(settings: VoiceSettings, fake: FakeElevenLabs) -> None:
    del fake.agents["agent_mx_1"]
    with pytest.raises(AgentCheckError):
        vac.agent_drift(settings, transport=fake.transport)


@pytest.mark.parametrize(
    "change",
    [
        {"CRIBA_ELEVENLABS_API_KEY_FILE": ""},
        {"CRIBA_ELEVENLABS_API_KEY_FILE": "/nonexistent/key.txt"},
        {"CRIBA_VOICE_AGENT_ES": ""},
        {"CRIBA_VOICE_AGENT_MX": ""},
        {"CRIBA_VOICE_PUBLIC_BASE_URL": "http://voice.example.test"},
    ],
)
def test_errors_raise_before_any_request(
    tmp_path: Path, fake: FakeElevenLabs, change: dict[str, str]
) -> None:
    settings = VoiceSettings.from_env(voice_env(tmp_path, **change))
    with pytest.raises(AgentCheckError):
        vac.agent_drift(settings, transport=fake.transport)
    assert fake.requests == []


def test_drift_lines_hold_no_secret(settings: VoiceSettings, fake: FakeElevenLabs) -> None:
    agent = es_agent(fake)
    set_path(agent, PROMPT + ".custom_llm.url", "https://stale.voice.example.test/api/voice/v1")
    set_path(agent, PROMPT + ".custom_llm.api_key", {"secret_id": "secret_other"})
    set_path(agent, WEBHOOKS + ".events", ["audio"])
    set_path(agent, "conversation_config.tts.model_id", "eleven_flash_v2_5")
    lines = vac.agent_drift(settings, transport=fake.transport)
    assert len(lines) == 4
    for line in lines:
        assert re.fullmatch(r"(ES|MX) [\w.]+: .+ -> .+", line), line
        for sentinel in (*SENTINELS, "example.test", SECRET_ID, "secret_other"):
            assert sentinel not in line, line
    assert f"ES {PROMPT}.custom_llm.url: <redacted> -> <redacted>" in lines


# ---------------------------------------------------------------------------
# Step 4: the CLI (AG1, AG6, AG7, decision 18)
# ---------------------------------------------------------------------------

VOICE_VARIABLES = (
    "CRIBA_VOICE",
    "CRIBA_VOICE_AGENT_ES",
    "CRIBA_VOICE_AGENT_MX",
    "CRIBA_VOICE_PUBLIC_BASE_URL",
    "CRIBA_ELEVENLABS_API_KEY_FILE",
    "CRIBA_VOICE_LLM_SECRET_FILE",
    "CRIBA_ENV",
)


@pytest.fixture(scope="module")
def cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("voice_agents", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["voice_agents"] = module
    spec.loader.exec_module(module)
    return module


class Run:
    """Runs ``main(argv, transport=fake)`` with the environment set like Compose would."""

    def __init__(
        self,
        cli: ModuleType,
        fake: FakeElevenLabs,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        self.cli, self.fake, self.monkeypatch, self.capsys = cli, fake, monkeypatch, capsys
        self.tmp_path = tmp_path
        self.outputs: list[str] = []

    def __call__(self, *argv: str, **env_change: str | None) -> tuple[int, str, str]:
        env = voice_env(self.tmp_path)
        for name in VOICE_VARIABLES:
            self.monkeypatch.delenv(name, raising=False)
        for name, value in env_change.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        for name, value in env.items():
            self.monkeypatch.setenv(name, value)
        code = self.cli.main(list(argv), transport=self.fake.transport)
        out, err = self.capsys.readouterr()
        self.outputs += [out, err]
        return code, out, err


@pytest.fixture
def run(
    cli: ModuleType,
    fake: FakeElevenLabs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> Run:
    return Run(cli, fake, monkeypatch, capsys, tmp_path)


def test_dry_run_writes_nothing(run: Run, fake: FakeElevenLabs) -> None:
    assert run() == (0, "", "")
    set_path(es_agent(fake), "conversation_config.tts.model_id", "eleven_flash_v2_5")
    code, out, _ = run(CRIBA_VOICE_AGENT_MX="")
    assert code == 0
    assert out.splitlines() == [
        'ES conversation_config.tts.model_id: "eleven_flash_v2_5" -> "eleven_v4_turbo"',
        "MX agent: <missing> -> <create>",
    ]
    assert fake.requests and fake.writes() == []


def test_check_exit_codes(run: Run, fake: FakeElevenLabs, settings: VoiceSettings) -> None:
    assert run("--check") == (0, "", "")
    set_path(es_agent(fake), "platform_settings.privacy.record_voice", True)
    set_path(fake.agents["agent_mx_1"], CCO + ".agent.language", True)
    code, out, _ = run("--check")
    assert code == 1
    assert out.splitlines() == vac.agent_drift(settings, transport=fake.transport)
    assert len(out.splitlines()) == 2
    fake.fail[("GET", "/v1/convai/agents/agent_mx_1")] = "500"
    code, out, err = run("--check")
    assert (code, out) == (2, "")
    assert "HTTP 500" in err
    with pytest.raises(AgentCheckError):
        vac.agent_drift(settings, transport=fake.transport)
    del fake.fail[("GET", "/v1/convai/agents/agent_mx_1")]
    code, _, err = run("--check", CRIBA_VOICE_AGENT_ES="")
    assert code == 2 and "CRIBA_VOICE_AGENT_ES" in err
    assert fake.writes() == []


def test_check_and_apply_are_exclusive(run: Run, fake: FakeElevenLabs) -> None:
    for argv in (
        ("--check", "--apply"),
        ("--rotate-llm-secret",),
        ("--check", "--rotate-llm-secret"),
    ):
        with pytest.raises(SystemExit) as caught:
            run(*argv)
        assert caught.value.code == 2
    assert fake.requests == []


def test_apply_patches_only_drift(run: Run, fake: FakeElevenLabs) -> None:
    assert run("--apply") == (0, "", "")
    assert fake.writes() == []
    agent = es_agent(fake)
    set_path(agent, "conversation_config.tts.model_id", "eleven_flash_v2_5")
    set_path(agent, "platform_settings.privacy.record_voice", True)
    set_path(agent, CCO + ".agent.first_message", True)
    _add_tool(agent, "skip_turn")
    before = json.loads(json.dumps(agent))
    code, out, _ = run("--apply")
    assert (code, out) == (0, "")
    assert fake.write_paths() == [("PATCH", "/v1/convai/agents/agent_es_1")]
    body = fake.bodies[fake.requests.index(fake.writes()[0])]
    changed = {
        path
        for path, value in leaves(body).items()
        if leaves(before).get(path, vac.MISSING) != value
    }
    assert changed == {
        "conversation_config.tts.model_id",
        "platform_settings.privacy.record_voice",
        CCO + ".agent.first_message",
        PROMPT + ".built_in_tools.skip_turn",
    }
    assert set(body) == {"conversation_config", "platform_settings"}
    patched_at = fake.requests.index(fake.writes()[0])
    assert any(
        r.method == "GET" and r.url.path == "/v1/convai/agents/agent_es_1"
        for r in fake.requests[patched_at + 1 :]
    ), "re-read after the write"
    # A drift a PATCH cannot clear stays: the re-read still drifts, exit 1.
    fake.agents["agent_mx_1"]["phone_numbers"] = [{"provider": "twilio", "phone_number_id": "p"}]
    set_path(fake.agents["agent_mx_1"], "conversation_config.agent.language", "en")
    code, out, _ = run("--apply")
    assert code == 1
    assert out.splitlines() == ["MX phone_numbers: <1 item(s)> -> <0 item(s)>"]


def test_apply_keeps_unowned_nested_keys(run: Run, fake: FakeElevenLabs) -> None:
    set_path(es_agent(fake), WEBHOOKS + ".post_call_webhook_id", "webhook_kept")
    set_path(es_agent(fake), WEBHOOKS + ".events", ["transcript", "audio"])
    assert run("--apply")[0] == 0
    body = fake.bodies[fake.requests.index(fake.writes()[0])]
    sent = get_path(body, WEBHOOKS)
    assert sent["post_call_webhook_id"] == "webhook_kept"
    assert sent["events"] == ["transcript"]
    assert get_path(es_agent(fake), WEBHOOKS + ".post_call_webhook_id") == "webhook_kept"


def test_apply_creates_once(run: Run, fake: FakeElevenLabs) -> None:
    # The MX agent exists, but its id is not set: refuse, write nothing.
    code, out, err = run("--apply", CRIBA_VOICE_AGENT_MX="")
    assert (code, out) == (2, "")
    assert "criba-screener-mx" in err
    assert fake.writes() == []
    del fake.agents["agent_mx_1"]
    code, out, _ = run("--apply", CRIBA_VOICE_AGENT_MX="")
    assert code == 0
    assert fake.write_paths() == [("POST", "/v1/convai/agents/create")]
    (new_id,) = [aid for aid in fake.agents if aid.startswith("agent_new_")]
    assert out.splitlines() == [f"MX created agent {new_id}: set CRIBA_VOICE_AGENT_MX to it"]
    created = fake.agents[new_id]
    assert created["name"] == "criba-screener-mx"
    assert get_path(created, PROMPT + ".custom_llm.api_key") == {"secret_id": SECRET_ID}
    # Run again before the owner sets the id: the guard refuses a second agent.
    code, _, _ = run("--apply", CRIBA_VOICE_AGENT_MX="")
    assert code == 2
    assert fake.write_paths() == [("POST", "/v1/convai/agents/create")]


def test_duplicate_guard_reads_every_page(run: Run, fake: FakeElevenLabs) -> None:
    mx = fake.agents.pop("agent_mx_1")
    for n in range(4):
        fake.agents[f"agent_other_{n}"] = {"name": f"someone-else-{n}"}
    fake.agents["agent_mx_1"] = mx
    fake.page_size = 2
    code, _, err = run("--apply", CRIBA_VOICE_AGENT_MX="")
    assert code == 2 and "criba-screener-mx" in err
    assert fake.writes() == []
    cursors = [
        r.url.params.get("cursor")
        for r in fake.requests
        if r.method == "GET" and r.url.path == "/v1/convai/agents"
    ]
    assert cursors == [None, "2", "4"]


def test_llm_secret_created_then_reused(run: Run, fake: FakeElevenLabs) -> None:
    fake.secrets.clear()
    code, out, _ = run("--apply")
    assert (code, out) == (0, "")
    assert fake.write_paths() == [
        ("POST", "/v1/convai/secrets"),
        ("PATCH", "/v1/convai/agents/agent_es_1"),
        ("PATCH", "/v1/convai/agents/agent_mx_1"),
    ]
    assert fake.bodies[fake.requests.index(fake.writes()[0])] == {
        "type": "new",
        "name": "criba_voice_llm_secret",
        "value": LLM_SECRET,
    }
    (secret,) = fake.secrets
    assert get_path(es_agent(fake), PROMPT + ".custom_llm.api_key") == {
        "secret_id": secret["secret_id"]
    }
    writes = len(fake.writes())
    assert run("--apply") == (0, "", "")
    assert len(fake.writes()) == writes
    assert run("--apply", "--rotate-llm-secret") == (0, "", "")
    assert fake.write_paths()[writes:] == [("PATCH", f"/v1/convai/secrets/{secret['secret_id']}")]
    assert fake.bodies[fake.requests.index(fake.writes()[-1])] == {
        "type": "update",
        "name": "criba_voice_llm_secret",
        "value": LLM_SECRET,
    }
    assert not any(LLM_SECRET in text for text in run.outputs)


def test_apply_refuses_without_the_llm_secret(run: Run, fake: FakeElevenLabs) -> None:
    code, _, err = run("--apply", CRIBA_VOICE_LLM_SECRET_FILE=None)
    assert code == 2 and "CRIBA_VOICE_LLM_SECRET_FILE" in err
    assert fake.requests == []


@pytest.mark.parametrize(
    "change",
    [
        {"CRIBA_VOICE_PUBLIC_BASE_URL": None},
        {"CRIBA_VOICE_PUBLIC_BASE_URL": ""},
        {"CRIBA_VOICE_PUBLIC_BASE_URL": "http://voice.example.test"},
        {"CRIBA_ELEVENLABS_API_KEY_FILE": None},
    ],
)
@pytest.mark.parametrize("mode", [(), ("--check",), ("--apply",)])
def test_unsafe_base_url_exits_before_any_request(
    run: Run, fake: FakeElevenLabs, change: dict[str, str | None], mode: tuple[str, ...]
) -> None:
    code, out, err = run(*mode, **change)
    assert (code, out) == (2, "")
    assert err
    assert fake.requests == []


def test_post_call_webhook(run: Run, fake: FakeElevenLabs) -> None:
    fake.webhooks += [
        {"webhook_id": "webhook_other", "webhook_url": "https://other.example.test/hook"},
        {"webhook_id": "webhook_ours", "webhook_url": WEBHOOK_URL},
    ]
    assert run("--check") == (0, "", "")
    code, out, _ = run("--check", "--post-call-webhook-url", WEBHOOK_URL)
    assert code == 1
    assert out.splitlines() == [
        f"ES {WEBHOOKS}.post_call_webhook_id: <missing> -> <redacted>",
        f"MX {WEBHOOKS}.post_call_webhook_id: <missing> -> <redacted>",
    ]
    code, out, _ = run("--apply", "--post-call-webhook-url", WEBHOOK_URL)
    assert (code, out) == (0, "")
    for agent_id in ("agent_es_1", "agent_mx_1"):
        assert get_path(fake.agents[agent_id], WEBHOOKS + ".post_call_webhook_id") == "webhook_ours"
        assert get_path(fake.agents[agent_id], WEBHOOKS + ".events") == ["transcript"]
    assert {path for _, path in fake.write_paths()} == {
        "/v1/convai/agents/agent_es_1",
        "/v1/convai/agents/agent_mx_1",
    }
    assert run("--check", "--post-call-webhook-url", WEBHOOK_URL) == (0, "", "")
    # No webhook with that URL: exit 2 with the owner step, nothing written.
    writes = len(fake.writes())
    fake.webhooks.pop()
    code, out, err = run("--apply", "--post-call-webhook-url", WEBHOOK_URL)
    assert (code, out) == (2, "")
    assert "Owner step" in err
    assert len(fake.writes()) == writes
    # An http:// URL is refused before any request.
    fake.requests.clear()
    code, _, _ = run("--check", "--post-call-webhook-url", "http://hooks.example.test/x")
    assert code == 2 and fake.requests == []


def test_no_secret_in_output(run: Run, fake: FakeElevenLabs) -> None:
    fake.webhooks.append({"webhook_id": "webhook_ours", "webhook_url": WEBHOOK_URL})
    set_path(es_agent(fake), PROMPT + ".custom_llm.url", "https://stale.voice.example.test/x")
    set_path(es_agent(fake), PROMPT + ".custom_llm.api_key", {"secret_id": "secret_other"})
    hook = ("--post-call-webhook-url", WEBHOOK_URL)
    run()
    run(*hook)
    run("--check", *hook)
    run("--apply", "--rotate-llm-secret", *hook)
    run("--check", *hook)
    fake.secrets.clear()
    run("--apply", CRIBA_VOICE_AGENT_MX="")
    fake.fail[("GET", "/v1/convai/agents/agent_es_1")] = "timeout"
    run("--check")
    run("--check", CRIBA_VOICE_PUBLIC_BASE_URL="http://voice.example.test")
    assert any(text for text in run.outputs)
    for text in run.outputs:
        for sentinel in (*SENTINELS, "example.test"):
            assert sentinel not in text, text


def test_apply_patches_a_top_level_field(run: Run, fake: FakeElevenLabs) -> None:
    es_agent(fake)["name"] = "renamed-in-console"
    assert run("--apply") == (0, "", "")
    assert fake.bodies[fake.requests.index(fake.writes()[0])] == {"name": "criba-screener-es"}
    assert es_agent(fake)["name"] == "criba-screener-es"


def test_apply_creates_with_the_post_call_webhook(run: Run, fake: FakeElevenLabs) -> None:
    fake.webhooks.append({"webhook_id": "webhook_ours", "webhook_url": WEBHOOK_URL})
    del fake.agents["agent_mx_1"]
    code, _, _ = run("--apply", "--post-call-webhook-url", WEBHOOK_URL, CRIBA_VOICE_AGENT_MX="")
    assert code == 0
    (new_id,) = [aid for aid in fake.agents if aid.startswith("agent_new_")]
    assert get_path(fake.agents[new_id], WEBHOOKS + ".post_call_webhook_id") == "webhook_ours"


@pytest.mark.parametrize(
    "where,failure",
    [
        (("GET", "/v1/workspace/webhooks"), "empty"),
        (("POST", "/v1/convai/secrets"), "empty"),
        (("POST", "/v1/convai/agents/create"), "empty"),
        (("PATCH", "/v1/convai/agents/agent_es_1"), "500"),
    ],
)
def test_apply_errors_exit_2(
    run: Run, fake: FakeElevenLabs, where: tuple[str, str], failure: str
) -> None:
    fake.webhooks.append({"webhook_id": "webhook_ours", "webhook_url": WEBHOOK_URL})
    fake.secrets.clear()
    del fake.agents["agent_mx_1"]
    fake.fail[where] = failure
    code, out, err = run("--apply", "--post-call-webhook-url", WEBHOOK_URL, CRIBA_VOICE_AGENT_MX="")
    assert (code, out) == (2, "")
    assert err.startswith("voice_agents: ")


# ---------------------------------------------------------------------------
# Step 5: the drift cache in build_runtime (decisions 25, 39)
# ---------------------------------------------------------------------------


def runtime_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """The environment ``test_wiring.py`` builds (secret files in ``tmp_path``), plus voice."""
    values = {"CRIBA_DB_PATH": str(tmp_path / "criba.db"), "CRIBA_JEV": "off"}
    for name, (filename, content) in {
        "CRIBA_TOOLS_SECRET_FILE": ("tools.txt", "test-tools-secret-0123456789"),
        "CRIBA_BOOKING_SECRET_FILE": ("booking.txt", "test-booking-secret-0123456789"),
        "CRIBA_HERMES_API_KEY_FILE": ("hermes.txt", "test-hermes-key"),
        "CRIBA_VOICE_SECRET_FILE": ("voice.txt", "test-voice-secret-0123456789"),
    }.items():
        path = tmp_path / filename
        path.write_text(content + "\n", encoding="utf-8")
        values[name] = str(path)
    return {**voice_env(tmp_path), **values, **extra}


def test_runtime_wires_the_drift_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from criba import wiring
    from criba.channel.voice_session import AgentDriftCache
    from criba.summary import DeterministicSummaryClient

    verdict: dict[str, Any] = {"result": []}
    checked: list[VoiceSettings] = []

    def fake_drift(settings: VoiceSettings) -> list[str]:
        checked.append(settings)
        result = verdict["result"]
        if isinstance(result, Exception):
            raise result
        return list(result)

    monkeypatch.setattr(wiring, "agent_drift", fake_drift)
    started: list[AgentDriftCache] = []
    original_start = AgentDriftCache.start

    def start(self: AgentDriftCache) -> None:
        started.append(self)
        original_start(self)

    monkeypatch.setattr(AgentDriftCache, "start", start)

    def build(**extra: str) -> wiring.Runtime:
        return wiring.build_runtime(
            runtime_env(tmp_path, **extra),
            summary_client=DeterministicSummaryClient(),
            prepare_db=False,
        )

    try:
        prod = build(CRIBA_ENV="prod")
        assert prod.voice is not None
        drift = prod.voice.drift
        assert isinstance(drift, AgentDriftCache)
        assert started == [drift]
        assert drift.interval_s == 600
        drift.refresh()
        assert drift.ok()
        assert checked and checked[-1] is prod.voice.settings
        verdict["result"] = ["ES platform_settings.privacy.record_voice: true -> false"]
        drift.refresh()
        assert not drift.ok()
        verdict["result"] = AgentCheckError("read agent: HTTP 500")
        drift.refresh()
        assert not drift.ok()
        verdict["result"] = []
        drift.refresh()
        assert drift.ok()
        for extra in ({"CRIBA_ENV": "sim"}, {"CRIBA_ENV": "test"}, {"CRIBA_VOICE": "off"}):
            other = build(**{"CRIBA_ENV": "prod", **extra})
            assert other.voice is not None and other.voice.drift is None
        assert len(started) == 1
    finally:
        for cache in started:
            cache.stop()
