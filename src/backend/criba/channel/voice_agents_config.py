"""The ElevenLabs agents as code (spec voice-agent agents-config, AG1-AG7; R18).

One config builds the ES and MX agents (:func:`desired_agent`). They differ only in the
name, the voice id, the filler phrases and the first message. ``src/scripts/voice_agents.py``
is the CLI over this module; the backend's ``AgentDriftCache`` runs :func:`agent_drift`.
"""

from __future__ import annotations

import copy as copy_module
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final, Literal
from urllib.parse import urlsplit

import httpx

from criba.channel import copy
from criba.channel.voice_config import VoiceSettings

Country = Literal["ES", "MX"]
COUNTRIES: Final[tuple[Country, Country]] = ("ES", "MX")

AGENT_NAMES: Final[dict[str, str]] = {"ES": "criba-screener-es", "MX": "criba-screener-mx"}
LLM_SECRET_NAME: Final = "criba_voice_llm_secret"
LLM_PATH: Final = "/api/voice/v1"
LLM_MODEL_ID: Final = "criba-voice"

# Voice A per country, copied from src/tests/fixtures/spikes/004-spanish-voice/listening/key.json
# (agent_voices.<country>.A.voice_id) until the panel rates (OQ 1). The backend image holds
# only `criba`, so the value is a constant here and the test compares it with key.json.
VOICE_IDS: Final[dict[str, str]] = {"ES": "UOIqAnmS11Reiei1Ytkc", "MX": "htFfPSZGJwjBv1CL0aMD"}

# SYNTHETIC: filler phrases spoken while the custom LLM has not answered (AG5). No number,
# no question, no URL; the owner tunes them after R26.
FILLERS: Final[dict[str, tuple[str, str, str]]] = {
    "ES": ("Un momento, por favor.", "Vale, lo estoy mirando.", "Gracias por esperar."),
    "MX": ("Permíteme tantito.", "Claro, lo estoy revisando.", "Gracias por tu paciencia."),
}

END_CALL_TOOL: Final[dict[str, Any]] = {
    "type": "system",
    "name": "end_call",
    "description": "",
    "params": {"system_tool_type": "end_call"},
}
OTHER_BUILT_IN_TOOLS: Final = (
    "transfer_to_agent",
    "language_detection",
    "transfer_to_number",
    "skip_turn",
    "play_keypad_touch_tone",
    "voicemail_detection",
)

# Every flag under platform_settings.overrides.conversation_config_override that the
# published OpenAPI names (ConversationConfigClientOverrideConfig, read 2026-10-11). The
# drift check walks the whole live tree, so a flag added later and turned on is drift too.
OVERRIDE_FLAGS: Final = (
    "asr.keywords",
    "turn.soft_timeout_config.message",
    "turn.soft_timeout_config.additional_soft_timeout_messages",
    "tts.model_id",
    "tts.voice_id",
    "tts.supported_voices",
    "tts.stability",
    "tts.speed",
    "tts.similarity_boost",
    "tts.pronunciation_dictionary_locators",
    "conversation.text_only",
    "conversation.max_duration_seconds",
    "agent.first_message",
    "agent.language",
    "agent.max_conversation_duration_message",
    "agent.prompt.prompt",
    "agent.prompt.llm",
    "agent.prompt.tool_ids",
    "agent.prompt.tool_ids_strict",
    "agent.prompt.native_mcp_server_ids",
    "agent.prompt.knowledge_base",
)


class AgentCheckError(Exception):
    """The agents cannot be checked or written: a missing key, an unset or unsafe variable,
    an HTTP error, a timeout or an unparseable body. Never means "no drift". The message
    names the problem, never a secret or a URL."""


def llm_url(settings: VoiceSettings) -> str:
    """``CRIBA_VOICE_PUBLIC_BASE_URL`` without a trailing ``/``, plus ``/api/voice/v1``.
    ElevenLabs appends ``/chat/completions`` (``api_type`` ``chat_completions``)."""
    base = settings.public_base_url.strip().rstrip("/")
    parts = urlsplit(base)
    if parts.scheme != "https" or not parts.hostname:
        raise AgentCheckError("CRIBA_VOICE_PUBLIC_BASE_URL must be set to an https:// URL")
    return base + LLM_PATH


def _nested(dotted: dict[str, Any]) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for path, value in dotted.items():
        node = tree
        *parents, last = path.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[last] = value
    return tree


def desired_agent(country: Country, settings: VoiceSettings) -> dict[str, Any]:
    """The agent body for ``country``. ``custom_llm.api_key`` holds the placeholder
    ``{"secret_name": "criba_voice_llm_secret"}``; the drift check and ``--apply`` resolve it
    to the workspace secret's ``{"secret_id": ...}``. Raises :class:`AgentCheckError` on an
    unknown country or an unset, empty or non-https public base URL."""
    if country not in COUNTRIES:
        raise AgentCheckError("country must be ES or MX")
    url = llm_url(settings)
    first, *more = FILLERS[country]
    built_in: dict[str, Any] = {name: None for name in OTHER_BUILT_IN_TOOLS}
    built_in["end_call"] = dict(END_CALL_TOOL)
    return {
        "name": AGENT_NAMES[country],
        "conversation_config": {
            "agent": {
                "first_message": copy.spoken_disclosure("es", country),
                "language": "es",
                "disable_first_message_interruptions": True,
                "prompt": {
                    "llm": "custom-llm",
                    "custom_llm": {
                        "url": url,
                        "model_id": LLM_MODEL_ID,
                        "api_key": {"secret_name": LLM_SECRET_NAME},
                        "api_type": "chat_completions",
                    },
                    "backup_llm_config": {"preference": "disabled"},
                    "built_in_tools": built_in,
                    "tool_ids": [],
                    "mcp_server_ids": [],
                    "native_mcp_server_ids": [],
                    "knowledge_base": [],
                },
            },
            "language_presets": {},
            "tts": {
                "model_id": "eleven_v4_turbo",
                "voice_id": VOICE_IDS[country],
                "text_normalisation_type": "elevenlabs",
            },
            "turn": {
                "soft_timeout_config": {
                    "timeout_seconds": 1.0,
                    "message": first,
                    "additional_soft_timeout_messages": list(more),
                    "use_llm_generated_message": False,
                    "max_soft_timeouts_per_generation": 3,  # ElevenLabs keeps 3 (decision 63)
                }
            },
            "conversation": {"max_duration_seconds": 1200},
        },
        "platform_settings": {
            "overrides": {
                "conversation_config_override": _nested({flag: False for flag in OVERRIDE_FLAGS}),
                "custom_llm_extra_body": True,
                "enable_conversation_initiation_client_data_from_webhook": False,
                "enable_starting_workflow_node_id_from_client": False,
                "enable_procedure_ids_from_client": False,
            },
            "auth": {"enable_auth": True, "allowlist": []},
            "privacy": {"record_voice": False, "retention_days": 1},
            "workspace_overrides": {"webhooks": {"events": ["transcript"]}},
        },
    }


# ---------------------------------------------------------------------------
# Reading ElevenLabs
# ---------------------------------------------------------------------------

API_BASE: Final = "https://api.elevenlabs.io"
TIMEOUT_S: Final = 10.0
PAGE_SIZE: Final = 100


def client(settings: VoiceSettings, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """An ElevenLabs client with the key in the ``xi-api-key`` header, read from its file now.
    Raises :class:`AgentCheckError` before any request when the key is missing."""
    key = settings.read_secret("elevenlabs_api_key")
    if not key:
        raise AgentCheckError("the ElevenLabs API key is missing (CRIBA_ELEVENLABS_API_KEY_FILE)")
    return httpx.Client(
        base_url=API_BASE, headers={"xi-api-key": key}, timeout=TIMEOUT_S, transport=transport
    )


def call(
    http: httpx.Client,
    method: str,
    path: str,
    *,
    what: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One request; its JSON object, or :class:`AgentCheckError` naming ``what`` failed."""
    try:
        response = http.request(method, path, params=params, json=body)
    except httpx.TimeoutException:
        raise AgentCheckError(f"{what}: timed out") from None
    except httpx.HTTPError as exc:
        raise AgentCheckError(f"{what}: {type(exc).__name__}") from None
    if response.status_code >= 400:
        raise AgentCheckError(f"{what}: HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise AgentCheckError(f"{what}: unparseable body") from None
    if not isinstance(data, dict):
        raise AgentCheckError(f"{what}: unparseable body")
    return data


def _pages(http: httpx.Client, path: str, key: str, *, what: str) -> Iterator[dict[str, Any]]:
    """Every item of a cursor-paged list (``next_cursor``; ``has_more`` where given)."""
    cursor: str | None = None
    for _ in range(1000):
        params: dict[str, Any] = {"page_size": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor
        data = call(http, "GET", path, what=what, params=params)
        items = data.get(key)
        if not isinstance(items, list):
            raise AgentCheckError(f"{what}: unparseable body")
        yield from (item for item in items if isinstance(item, dict))
        cursor = data.get("next_cursor")
        if not cursor or data.get("has_more") is False:
            return
    raise AgentCheckError(f"{what}: too many pages")


def llm_secret_id(http: httpx.Client) -> str | None:
    """The id of the workspace secret named ``criba_voice_llm_secret``, or None."""
    for secret in _pages(http, "/v1/convai/secrets", "secrets", what="list secrets"):
        if secret.get("name") == LLM_SECRET_NAME and secret.get("secret_id"):
            return str(secret["secret_id"])
    return None


def read_agent(http: httpx.Client, agent_id: str) -> dict[str, Any]:
    return call(http, "GET", f"/v1/convai/agents/{agent_id}", what="read agent")


def agent_ids(settings: VoiceSettings) -> dict[Country, str]:
    return {"ES": settings.agent_es.strip(), "MX": settings.agent_mx.strip()}


# ---------------------------------------------------------------------------
# The comparison (AG2, AG3, AG6, AG7)
# ---------------------------------------------------------------------------

MISSING: Final = object()

_PROMPT: Final = "conversation_config.agent.prompt"
_OVERRIDES: Final = "platform_settings.overrides"
_OVERRIDE_TREE: Final = _OVERRIDES + ".conversation_config_override"
_WEBHOOKS: Final = "platform_settings.workspace_overrides.webhooks"
API_KEY_PATH: Final = _PROMPT + ".custom_llm.api_key"
BUILT_IN_PATH: Final = _PROMPT + ".built_in_tools"
WEBHOOK_ID_PATH: Final = _WEBHOOKS + ".post_call_webhook_id"
# URL- and secret-valued fields print <redacted> on both sides of a drift line.
REDACTED: Final = frozenset({_PROMPT + ".custom_llm.url", API_KEY_PATH, WEBHOOK_ID_PATH})
# Lists printed as they are; any other list or object prints as a count.
_PRINTED_LISTS: Final = frozenset(
    {
        _WEBHOOKS + ".events",
        "conversation_config.turn.soft_timeout_config.additional_soft_timeout_messages",
    }
)


@dataclass(frozen=True)
class Drift:
    """One owned field whose live value differs from the desired one. ``fixable`` is False
    when a PATCH of the agent cannot set it (a phone number, a WhatsApp account, a
    workflow with more than start nodes or with any edge): those stay drift until the owner
    removes them in ElevenLabs."""

    country: str
    path: str
    live: Any
    desired: Any
    fixable: bool = True

    def line(self) -> str:
        return (
            f"{self.country} {self.path}: "
            f"{_show(self.path, self.live)} -> {_show(self.path, self.desired)}"
        )


def _show(path: str, value: Any) -> str:
    if value is MISSING:
        return "<missing>"
    if path in REDACTED:
        return "<redacted>"
    if isinstance(value, list):
        if path in _PRINTED_LISTS and all(isinstance(item, str) for item in value):
            return json.dumps(value, ensure_ascii=False)
        return f"<{len(value)} item(s)>"
    if isinstance(value, dict):
        return f"<object with {len(value)} key(s)>"
    return json.dumps(value, ensure_ascii=False)


def _get(tree: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(tree, dict) or part not in tree:
            return MISSING
        tree = tree[part]
    return tree


def _pop(tree: dict[str, Any], path: str) -> Any:
    *parents, last = path.split(".")
    for part in parents:
        tree = tree[part]
    return tree.pop(last)


def _leaves(tree: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Dotted path and value of every leaf; an empty dict or list is a leaf."""
    if isinstance(tree, dict) and tree:
        for key, value in tree.items():
            yield from _leaves(value, f"{prefix}.{key}" if prefix else key)
    else:
        yield prefix, tree


def _same(path: str, live: Any, desired: Any) -> bool:
    if live is MISSING:
        # An empty list or object the API leaves out is empty; every other owned value
        # must be present (record_voice, for one, defaults to true).
        return desired in ([], {})
    if path == _WEBHOOKS + ".events" and isinstance(live, list):
        return sorted(map(str, live)) == sorted(desired)
    if isinstance(desired, bool) or isinstance(live, bool):
        return live is desired
    return bool(live == desired)


def compare(
    country: Country,
    live: dict[str, Any],
    desired: dict[str, Any],
    *,
    secret_id: str | None,
    webhook_id: str | None = None,
) -> list[Drift]:
    """The owned fields of ``live`` that differ from ``desired`` (AG7).

    Owned: every leaf of :func:`desired_agent`, the secret reference (``secret_id`` is the
    workspace secret's id, None when it does not exist), the whole override tree (any
    ``true`` flag, known or not, is drift), ``send_audio``, phone numbers, WhatsApp
    accounts and a workflow (ElevenLabs' default of only start nodes and no edges counts as
    none, decision 63). With ``webhook_id`` the post-call webhook id is owned too.
    ElevenLabs' other fields and defaults are not drift."""
    want = copy_module.deepcopy(desired)
    out: list[Drift] = []

    _pop(want, API_KEY_PATH)
    live_key = _get(live, API_KEY_PATH)
    live_secret = live_key.get("secret_id") if isinstance(live_key, dict) else None
    if secret_id is None or live_secret != secret_id:
        wanted_key = {"secret_id": secret_id} if secret_id else MISSING
        out.append(Drift(country, API_KEY_PATH, live_key, wanted_key, secret_id is not None))

    want_tools = _pop(want, BUILT_IN_PATH)
    live_tools = _get(live, BUILT_IN_PATH)
    live_tools = live_tools if isinstance(live_tools, dict) else {}
    if not live_tools.get("end_call"):
        end_call = live_tools.get("end_call", MISSING)
        out.append(Drift(country, BUILT_IN_PATH + ".end_call", end_call, want_tools["end_call"]))
    for name, tool in live_tools.items():
        if name != "end_call" and tool is not None:
            out.append(Drift(country, f"{BUILT_IN_PATH}.{name}", tool, None))

    _pop(want, _OVERRIDE_TREE)
    for path, value in _leaves(_get(live, _OVERRIDE_TREE), _OVERRIDE_TREE):
        if value is True:
            out.append(Drift(country, path, True, False))
    for path, value in _leaves(_get(live, _OVERRIDES), _OVERRIDES):
        if path.startswith(_OVERRIDE_TREE + ".") or path == _OVERRIDE_TREE:
            continue
        if value is True and _get(want, path) is MISSING:
            out.append(Drift(country, path, True, False))

    if webhook_id is not None:
        live_webhook = _get(live, WEBHOOK_ID_PATH)
        if live_webhook != webhook_id:
            out.append(Drift(country, WEBHOOK_ID_PATH, live_webhook, webhook_id))

    for path, value in _leaves(want):
        live_value = _get(live, path)
        if path.startswith(_OVERRIDES + ".") and value is False and live_value is MISSING:
            continue
        if not _same(path, live_value, value):
            out.append(Drift(country, path, live_value, value))

    if _get(live, _WEBHOOKS + ".send_audio") is True:
        out.append(Drift(country, _WEBHOOKS + ".send_audio", True, False))
    for path in ("phone_numbers", "whatsapp_accounts"):
        assigned = _get(live, path)
        if isinstance(assigned, list) and assigned:
            out.append(Drift(country, path, assigned, [], fixable=False))
    workflow = _get(live, "workflow")
    if isinstance(workflow, dict) and not _is_default_workflow(workflow):
        out.append(Drift(country, "workflow", workflow, MISSING, fixable=False))
    return out


def _is_default_workflow(workflow: dict[str, Any]) -> bool:
    """True for no workflow: ElevenLabs gives every agent one start node and no edges
    (owner decision 63). Any other node, any edge or any subgraph is a workflow the owner
    built."""
    if workflow.get("edges") or workflow.get("subgraphs"):
        return False
    nodes = workflow.get("nodes") or {}
    if not isinstance(nodes, dict):
        return False
    return all(isinstance(n, dict) and n.get("type") == "start" for n in nodes.values())


def desired_agents(settings: VoiceSettings) -> dict[Country, dict[str, Any]]:
    return {country: desired_agent(country, settings) for country in COUNTRIES}


def agent_drift(
    settings: VoiceSettings, *, transport: httpx.BaseTransport | None = None
) -> list[str]:
    """The drift lines of both agents (``<country> <dotted path>: <live> -> <desired>``);
    ``[]`` when both match. GET only. Raises :class:`AgentCheckError` when it cannot
    decide: a missing key, an unset agent id, an unset or unsafe base URL, an HTTP error,
    a timeout or an unparseable body. The post-call webhook id is not compared here
    (decision 24): it is a command-line value of ``voice_agents.py --check``."""
    return [drift.line() for drift in read_drift(settings, transport=transport)]


def read_drift(
    settings: VoiceSettings,
    *,
    transport: httpx.BaseTransport | None = None,
    webhook_url: str | None = None,
) -> list[Drift]:
    """:func:`agent_drift`'s comparison as :class:`Drift` values, optionally with the
    post-call webhook whose URL is ``webhook_url``."""
    desired = desired_agents(settings)
    ids = agent_ids(settings)
    for country, agent_id in ids.items():
        if not agent_id:
            raise AgentCheckError(f"CRIBA_VOICE_AGENT_{country} is not set")
    webhook_check(webhook_url)
    with client(settings, transport) as http:
        webhook_id = find_webhook(http, webhook_url) if webhook_url else None
        secret_id = llm_secret_id(http)
        drifts: list[Drift] = []
        for country in COUNTRIES:
            live = read_agent(http, ids[country])
            drifts += compare(
                country, live, desired[country], secret_id=secret_id, webhook_id=webhook_id
            )
        return drifts


# ---------------------------------------------------------------------------
# The post-call webhook (decisions 18, 40)
# ---------------------------------------------------------------------------


def webhook_check(url: str | None) -> None:
    """``--post-call-webhook-url`` must be an https URL; checked before any request."""
    if url is None:
        return
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise AgentCheckError("--post-call-webhook-url must be an https:// URL")


def find_webhook(http: httpx.Client, url: str | None) -> str:
    """The id of the existing workspace webhook whose URL is ``url``. The script never
    creates one: creating it returns a signing secret only the owner may store."""
    data = call(http, "GET", "/v1/workspace/webhooks", what="list webhooks")
    hooks = data.get("webhooks")
    if not isinstance(hooks, list):
        raise AgentCheckError("list webhooks: unparseable body")
    wanted = (url or "").strip()
    for hook in hooks:
        if isinstance(hook, dict) and hook.get("webhook_url") == wanted and hook.get("webhook_id"):
            return str(hook["webhook_id"])
    raise AgentCheckError(
        "no workspace webhook has the --post-call-webhook-url URL. Owner step: in ElevenLabs, "
        "add the post-call webhook with that URL (HMAC), copy its signing secret into "
        "secrets/elevenlabs_webhook_secret.txt, then re-run"
    )


# ---------------------------------------------------------------------------
# The CLI's modes (AG6): dry run and --apply. --check is read_drift.
# ---------------------------------------------------------------------------


def dry_run(
    settings: VoiceSettings,
    *,
    transport: httpx.BaseTransport | None = None,
    webhook_url: str | None = None,
) -> list[str]:
    """What ``--apply`` would change, as drift lines; GET only. An unset agent id prints
    ``<country> agent: <missing> -> <create>``."""
    desired = desired_agents(settings)
    webhook_check(webhook_url)
    ids = agent_ids(settings)
    with client(settings, transport) as http:
        webhook_id = find_webhook(http, webhook_url) if webhook_url else None
        secret_id = llm_secret_id(http)
        lines: list[str] = []
        for country in COUNTRIES:
            if not ids[country]:
                lines.append(f"{country} agent: <missing> -> <create>")
                continue
            live = read_agent(http, ids[country])
            found = compare(
                country, live, desired[country], secret_id=secret_id, webhook_id=webhook_id
            )
            lines += [drift.line() for drift in found]
        return lines


@dataclass(frozen=True)
class ApplyResult:
    """The agents ``--apply`` created (country -> new id, for the owner to set) and the
    drift lines its re-read still shows (empty when the agents now match)."""

    created: dict[str, str]
    remaining: list[str]


def patch_body(live: dict[str, Any], drifts: list[Drift]) -> dict[str, Any]:
    """A PATCH body that sets only the drifted fields. Each drifted field's parent object is
    sent as the live object with the drifted keys replaced, so keys we do not own in that
    object (a webhook id set by hand, say) are sent back unchanged (risk 4)."""
    parents: dict[str, dict[str, Any]] = {}
    body: dict[str, Any] = {}
    for drift in drifts:
        parent, _, key = drift.path.rpartition(".")
        if not parent:
            body[key] = drift.desired
            continue
        if parent not in parents:
            live_parent = _get(live, parent)
            parents[parent] = (
                copy_module.deepcopy(live_parent) if isinstance(live_parent, dict) else {}
            )
        parents[parent][key] = copy_module.deepcopy(drift.desired)
    for parent in sorted(parents, key=lambda path: path.count(".")):
        node = body
        *above, last = parent.split(".")
        for part in above:
            node = node.setdefault(part, {})
        node[last] = parents[parent]
    return body


def resolved_agent(
    desired: dict[str, Any], *, secret_id: str, webhook_id: str | None
) -> dict[str, Any]:
    """``desired`` with the secret reference resolved, ready to create."""
    body = copy_module.deepcopy(desired)
    _get(body, _PROMPT + ".custom_llm")["api_key"] = {"secret_id": secret_id}
    if webhook_id is not None:
        _get(body, _WEBHOOKS)["post_call_webhook_id"] = webhook_id
    return body


def refuse_duplicate(http: httpx.Client, name: str, country: Country) -> None:
    """AG6's guard: refuse to create when any agent, on any page, already has ``name``."""
    for agent in _pages(http, "/v1/convai/agents", "agents", what="list agents"):
        if agent.get("name") == name:
            raise AgentCheckError(
                f"an agent named {name} already exists: set CRIBA_VOICE_AGENT_{country} to "
                "its id instead of creating another"
            )


def ensure_llm_secret(http: httpx.Client, value: str, *, rotate: bool) -> str:
    """The workspace secret ``criba_voice_llm_secret``'s id: created from ``value`` when
    missing, its value replaced only with ``rotate``. The value is never returned or logged."""
    secret_id = llm_secret_id(http)
    if secret_id is None:
        made = call(
            http,
            "POST",
            "/v1/convai/secrets",
            what="create secret",
            body={"type": "new", "name": LLM_SECRET_NAME, "value": value},
        )
        if not made.get("secret_id"):
            raise AgentCheckError("create secret: unparseable body")
        return str(made["secret_id"])
    if rotate:
        call(
            http,
            "PATCH",
            f"/v1/convai/secrets/{secret_id}",
            what="update secret",
            body={"type": "update", "name": LLM_SECRET_NAME, "value": value},
        )
    return secret_id


def apply_agents(
    settings: VoiceSettings,
    *,
    transport: httpx.BaseTransport | None = None,
    rotate_llm_secret: bool = False,
    webhook_url: str | None = None,
) -> ApplyResult:
    """Create or patch both agents so they match (AG6).

    Every check that can refuse runs before the first write: the variables, the key, the LLM
    secret file, the webhook lookup and the duplicate-name guard. Then the LLM secret is
    created if missing (or its value replaced with ``rotate_llm_secret``); an agent with
    its id set gets one PATCH holding only its drifted fields, and one with its id unset is
    created. Each written agent is read again; what still drifts is returned."""
    desired = desired_agents(settings)
    webhook_check(webhook_url)
    ids = agent_ids(settings)
    value = settings.read_secret("criba_voice_llm_secret")
    if not value:
        raise AgentCheckError("the custom-LLM secret is missing (CRIBA_VOICE_LLM_SECRET_FILE)")
    with client(settings, transport) as http:
        webhook_id = find_webhook(http, webhook_url) if webhook_url else None
        for country in COUNTRIES:
            if not ids[country]:
                refuse_duplicate(http, AGENT_NAMES[country], country)
        secret_id = ensure_llm_secret(http, value, rotate=rotate_llm_secret)
        created: dict[str, str] = {}
        written: dict[Country, str] = {}
        remaining: list[str] = []
        for country in COUNTRIES:
            agent_id = ids[country]
            if not agent_id:
                body = resolved_agent(desired[country], secret_id=secret_id, webhook_id=webhook_id)
                made = call(
                    http, "POST", "/v1/convai/agents/create", what="create agent", body=body
                )
                if not made.get("agent_id"):
                    raise AgentCheckError("create agent: unparseable body")
                created[country] = written[country] = str(made["agent_id"])
                continue
            live = read_agent(http, agent_id)
            drifts = compare(
                country, live, desired[country], secret_id=secret_id, webhook_id=webhook_id
            )
            fixable = [drift for drift in drifts if drift.fixable]
            if not fixable:
                remaining += [drift.line() for drift in drifts]
            else:
                call(
                    http,
                    "PATCH",
                    f"/v1/convai/agents/{agent_id}",
                    what="update agent",
                    body=patch_body(live, fixable),
                )
                written[country] = agent_id
        for country, agent_id in written.items():
            live = read_agent(http, agent_id)
            drifts = compare(
                country, live, desired[country], secret_id=secret_id, webhook_id=webhook_id
            )
            remaining += [drift.line() for drift in drifts]
        return ApplyResult(created=created, remaining=remaining)
