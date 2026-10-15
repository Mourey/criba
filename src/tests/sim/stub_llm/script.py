"""Script format for the stub model, its loader, and validation against contracts/tools.json.

A script is a YAML file with an ordered list of rules. For each chat completion request the
server picks the first rule whose `match` holds and replays its `respond` block: text, tool
calls in OpenAI format, an optional `usage` override, an artificial delay, or an HTTP error.
Matching is stateless (it looks only at the `messages` array), so many conversations can share
one server, which is what load mode needs.

Example::

    name: demo
    rules:
      - name: first-turn
        match: {user_turn: 1, step: 0}
        respond:
          tool_calls: [{name: get_application}]
      - name: greet
        match: {last_tool: get_application, tool_json: {stage: greeting}}
        respond:
          content: {es: "Hola, soy un asistente virtual (IA).", en: "Hi, I am an AI assistant."}
    fallback:
      content: "..."
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

log = logging.getLogger("sim.stub_llm")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Match(_Strict):
    """Conditions on the request. Every key that is set must hold (AND)."""

    user_turn: int | list[int] | None = Field(
        default=None, description="Number of user messages in the history (1 = first)."
    )
    step: int | list[int] | None = Field(
        default=None,
        description="Assistant tool-call rounds since the last user message (0 = fresh turn).",
    )
    last_role: Literal["user", "tool"] | None = None
    last_tool: str | None = Field(
        default=None, description="Name of the tool whose result is the last message."
    )
    tool_json: dict[str, Any] = Field(
        default_factory=dict,
        description="Dotted paths into the last tool result (JSON). A path is looked up at the "
        "root, then under `value` (the tools.json response envelope). A list means any of.",
    )
    tool_json_contains: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Dotted paths into the last tool result, as for `tool_json`, whose value as "
        "text contains any of the listed strings (case-insensitive). A missing path fails.",
    )
    tool_json_absent: list[str] = Field(
        default_factory=list,
        description="Dotted paths, as for `tool_json`, that must be missing from the last tool "
        "result. The backend leaves out a key that is null or false (turn-size.md), so a "
        "missing key is how a rule says `interview: null`.",
    )
    user_contains: list[str] = Field(
        default_factory=list,
        description="Any of these, case-insensitive, in the last user message.",
    )
    user_regex: str | None = Field(default=None, description="re.search on the last user message.")
    user_lang: Literal["es", "en"] | None = Field(
        default=None,
        description="Crude language of the latest user message that shows one.",
    )
    prev_assistant_contains: list[str] = Field(
        default_factory=list,
        description="Any of these in the most recent assistant text (to detect 'confirm once').",
    )
    system_contains: list[str] = Field(
        default_factory=list, description="Any of these in the system prompt."
    )
    interprets: str | None = Field(
        default=None,
        description="A field name. Holds when the interpreter knows the last user message for "
        "that field: a scripted persona message (for the Zone: one with a search query).",
    )
    history_tool: str | None = Field(
        default=None,
        description="A result of this tool appears anywhere in the history (the latest one is "
        "readable as {{history.<tool>.<path>}}).",
    )
    marker: Literal["optout_request"] | None = Field(
        default=None,
        description="The latest user message carries this backend block (a spoken turn's "
        "[optout_request] line, sim-stub SM2).",
    )

    @model_validator(mode="after")
    def _regex_compiles(self) -> Match:
        if self.user_regex is not None:
            try:
                re.compile(self.user_regex)
            except re.error as exc:
                raise ValueError(
                    f"user_regex {self.user_regex!r} is not a valid regex: {exc}"
                ) from exc
        return self


class ToolCallSpec(_Strict):
    name: str
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="String values may use templates; a value that is exactly one placeholder "
        "keeps the placeholder's type.",
    )
    id: str | None = Field(default=None, description="Defaults to a deterministic call id.")
    omit_empty: list[str] = Field(
        default_factory=list,
        description="Arguments left out of the call when they render to null or an empty "
        "string (for example `language` after a button tap).",
    )


class UsageSpec(_Strict):
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)


class ErrorSpec(_Strict):
    status: int = Field(ge=400, le=599)
    message: str = "stub error"
    type: str = "stub_error"


class Respond(_Strict):
    content: str | dict[str, str] | None = Field(
        default=None, description="Text, or a map from language (es, en) to text."
    )
    tool_calls: list[ToolCallSpec] = Field(default_factory=list)
    usage: UsageSpec | None = None
    delay_ms: int = Field(default=0, ge=0, description="Wait before replying (simulated latency).")
    error: ErrorSpec | None = None

    @model_validator(mode="after")
    def _something_to_say(self) -> Respond:
        if self.content is None and not self.tool_calls and self.error is None:
            raise ValueError("respond needs content, tool_calls or error")
        if self.error is not None and (self.content is not None or self.tool_calls):
            raise ValueError("respond.error cannot be combined with content or tool_calls")
        return self


class Rule(_Strict):
    name: str | None = None
    match: Match = Field(default_factory=Match)
    respond: Respond


class PriceSpec(_Strict):
    prompt: float = Field(ge=0, description="USD per million prompt tokens.")
    completion: float = Field(ge=0, description="USD per million completion tokens.")


class Defaults(_Strict):
    models: list[str] = Field(
        default_factory=lambda: ["stub-model", "stub-haiku", "stub-sonnet"],
        description="Ids listed by /v1/models. Chat requests may name any model.",
    )
    chunk_chars: int = Field(default=24, ge=1, description="Text characters per SSE content chunk.")
    context_length: int = Field(
        default=200_000,
        ge=1024,
        description="Context window reported by /v1/models and /api/show. Hermes reads it from "
        "/v1/models (model_metadata.py _resolve_endpoint_context_length) and then skips its "
        "Ollama /api/show probe.",
    )
    cache_ratio: float = Field(
        default=0.8,
        ge=0,
        le=1,
        description="Share of prompt tokens reported as cached once the conversation has history.",
    )
    min_prompt_tokens: int = Field(
        default=0,
        ge=0,
        description="Floor for the prompt token estimate (large system prompts).",
    )
    price_per_mtok: PriceSpec | None = Field(
        default=None,
        description="When set, usage also carries an OpenRouter-style `cost`.",
    )
    always_usage_in_stream: bool = Field(
        default=True,
        description="Send the final usage chunk even without stream_options.include_usage, as "
        "OpenRouter does.",
    )


class Script(_Strict):
    name: str = "unnamed"
    defaults: Defaults = Field(default_factory=Defaults)
    rules: list[Rule] = Field(default_factory=list)
    fallback: Respond = Field(
        default_factory=lambda: Respond(content="(stub: no rule matched)"),
        description="Used when no rule matches.",
    )


class ScriptError(ValueError):
    """The script is invalid (YAML, schema, or an unknown tool)."""


class ToolsContract:
    """Tool names and input shapes from contracts/tools.json."""

    def __init__(self, tools: dict[str, dict[str, Any]]):
        self.tools = tools

    @classmethod
    def load(cls, path: str | Path) -> ToolsContract:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        defs = data["$defs"]
        tools: dict[str, dict[str, Any]] = {}
        for name, refs in data["x-tools"].items():
            input_schema = defs[refs["input"].rsplit("/", 1)[-1]]
            tools[name] = {
                "properties": set(input_schema.get("properties", {})),
                "required": set(input_schema.get("required", [])),
            }
        return cls(tools)

    @classmethod
    def discover(cls) -> ToolsContract | None:
        """Env STUB_LLM_TOOLS_JSON, else contracts/tools.json found above this file."""
        env = os.environ.get("STUB_LLM_TOOLS_JSON")
        if env:
            return cls.load(env)
        for parent in Path(__file__).resolve().parents:
            candidate = parent / "contracts" / "tools.json"
            if candidate.is_file():
                return cls.load(candidate)
        return None

    def check(self, call: ToolCallSpec, where: str) -> None:
        spec = self.tools.get(call.name)
        if spec is None:
            raise ScriptError(f"{where}: unknown tool {call.name!r}; known: {sorted(self.tools)}")
        extra = set(call.arguments) - spec["properties"]
        if extra:
            raise ScriptError(f"{where}: {call.name} has no argument(s) {sorted(extra)}")
        missing = spec["required"] - set(call.arguments)
        if missing:
            raise ScriptError(f"{where}: {call.name} needs argument(s) {sorted(missing)}")


def validate_tools(script: Script, contract: ToolsContract) -> None:
    for i, rule in enumerate(script.rules):
        for call in rule.respond.tool_calls:
            contract.check(call, f"rule {rule.name or i}")
    for call in script.fallback.tool_calls:
        contract.check(call, "fallback")


def parse_script(raw: Any, contract: ToolsContract | None = None) -> Script:
    try:
        script = Script.model_validate(raw)
    except ValueError as exc:
        raise ScriptError(str(exc)) from exc
    if contract is not None:
        validate_tools(script, contract)
    return script


def load_script(path: str | Path, contract: ToolsContract | None = None) -> Script:
    """Load a YAML (or JSON) script. With `contract`, every tool call is checked against it."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScriptError(f"{path.name}: invalid YAML: {exc}") from exc
    try:
        return parse_script(raw, contract)
    except ScriptError as exc:
        raise ScriptError(f"{path.name}: {exc}") from exc
