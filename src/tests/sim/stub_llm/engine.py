"""Rule matching, templating and usage estimation for the stub model. Pure functions, no I/O."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sim.stub_llm.interpret import Interpreter, default_interpreter
from sim.stub_llm.script import Defaults, Match, Respond, Rule, Script, ToolCallSpec

_EN_WORDS = frozenset(
    {
        *("the", "i", "my", "is", "are", "am", "yes", "can", "please", "english"),
        *("have", "has", "do", "you", "your", "and", "to", "in", "of", "it", "that"),
        *("this", "with", "for", "would", "like", "want", "hello", "hi", "thanks"),
        *("thank", "sorry", "license", "licence", "driver"),
    }
)
_ES_WORDS = frozenset(
    {
        *("el", "la", "los", "las", "de", "que", "y", "en", "mi", "soy", "tengo"),
        *("vivo", "si", "sí", "por", "para", "con", "un", "una", "hola", "gracias"),
        *("licencia", "carnet", "quiero", "puedo", "estoy", "es", "no", "lo", "se"),
    }
)
_WORD = re.compile(r"[a-záéíóúñü']+", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_WHOLE_PLACEHOLDER = re.compile(r"^\{\{\s*([^{}]+?)\s*\}\}$")
_MATCH_TRIM = " .,;:!?¡¿\"'"
# The backend records a tap on an answer option itself (ADR 0043) and sends the agent the
# record_answer result after this prefix, to obey as if it had made the call. A tapped slot is
# booked the same way and comes as the book_interview result (ADR 0050).
TAP_RESULT_PREFIX = "[tap result] "
# The criba-tools plugin appends the Candidate's local date to the latest user message (ADR 0055).
# The stub reads it as a model would, and never takes it for the Candidate's words.
_DATE_LINE = re.compile(
    r"\s*\[criba date\] Candidate's local today: [a-z]+ (\d{4}-\d{2}-\d{2});[^\n]*$"
)
SLOT_ERRORS = frozenset({"slot_not_offered", "slot_unavailable"})
# The blocks the backend writes ahead of a spoken turn (spoken-turn R10.1, log decisions 17, 45),
# matched exactly as it writes them. The stub reads them as a model would: instructions and
# record, never the Candidate's words (sim-stub SM1).
APPLICATION_MARKER = "[application]"
APPLICATION_RULE_START = "This [application] block"
VOICE_MARKER = "[voice]"
VOICE_RULE_BULLET = "- "
OPTOUT_REQUEST_MARKER = "[optout_request]"
# The note the router puts ahead of a typed message read while the Zone is the question (ADR 0068):
# one line, instruction to the agent, never the Candidate's words.
ZONE_MARKER = "[zone]"


def _text_of(content: Any) -> str:
    """Message content is a string or a list of OpenAI content parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


def split_date_line(text: str) -> tuple[str, date | None]:
    """The user text without the plugin's date line, and the date the line gave (or None)."""
    found = _DATE_LINE.search(text)
    if found is None:
        return text, None
    return text[: found.start()], date.fromisoformat(found.group(1))


def strip_markers(text: str) -> tuple[str, frozenset[str]]:
    """The user text without the blocks the backend puts ahead of a spoken turn (spoken-turn's
    ``spoken_run_input``), and which blocks it carried: ``application``, ``voice``,
    ``optout_request``, ``zone``. Only leading lines are read, and only while each is one of:

    - ``[application] ...``, with the next line too when it is the block's rule
      (``This [application] block ...``);
    - ``[voice]``, with every following line that starts with ``- `` (the voice rules);
    - ``[optout_request]``;
    - ``[zone] ...``, one line (the router's Zone note, ADR 0068).

    The blocks are instructions and record, not the Candidate's words. Text with no leading
    block comes back unchanged; a marker after the first Candidate line is kept."""
    lines = text.split("\n")
    found: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(APPLICATION_MARKER):
            found.add("application")
            i += 1
            if i < len(lines) and lines[i].startswith(APPLICATION_RULE_START):
                i += 1
        elif line.strip() == VOICE_MARKER:
            found.add("voice")
            i += 1
            while i < len(lines) and lines[i].startswith(VOICE_RULE_BULLET):
                i += 1
        elif line.strip() == OPTOUT_REQUEST_MARKER:
            found.add("optout_request")
            i += 1
        elif line.startswith(ZONE_MARKER):
            found.add("zone")
            i += 1
        else:
            break
    if not found:
        return text, frozenset()
    return "\n".join(lines[i:]), frozenset(found)


def _user_text(content: Any) -> str:
    return split_date_line(strip_markers(_text_of(content))[0])[0]


def guess_lang(text: str) -> str | None:
    """'en' or 'es' when the text shows one, else None. Crude stopword vote."""
    words = [w.lower() for w in _WORD.findall(text)]
    en = sum(w in _EN_WORDS and w not in _ES_WORDS for w in words)
    es = sum(w in _ES_WORDS and w not in _EN_WORDS for w in words)
    if en == es:
        return None
    return "en" if en > es else "es"


def get_path(obj: Any, path: str) -> Any:
    """Dotted lookup (dict keys, list indices). Tried at the root, then under `value`."""

    def walk(node: Any) -> Any:
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                raise KeyError(part)
        return node

    try:
        return walk(obj)
    except KeyError:
        if isinstance(obj, dict) and isinstance(obj.get("value"), dict):
            return walk(obj["value"])
        raise


@dataclass
class Context:
    """What the rules can see, derived from the request's messages."""

    messages: list[dict[str, Any]]
    user_turn: int = 0
    step: int = 0
    last_role: str = ""
    last_user: str = ""
    user_lang: str = "es"
    locale: str = ""  # the stored language get_application returns: es-ES, es-MX or en
    last_tool: str | None = None
    tool_json: Any = None
    prev_assistant: str = ""
    system: str = ""
    line_today: date | None = None  # from the plugin's date line on the latest user message
    markers: frozenset[str] = frozenset()  # the backend's blocks on the latest user message
    tool_results: dict[str, Any] = field(default_factory=dict)  # latest JSON per tool name
    extra: dict[str, Any] = field(default_factory=dict)
    interp: Interpreter = field(default_factory=default_interpreter)

    @classmethod
    def from_messages(
        cls, messages: list[dict[str, Any]], interp: Interpreter | None = None
    ) -> Context:
        ctx = cls(messages=messages)
        if interp is not None:
            ctx.interp = interp
        last_user_index = -1
        for i, msg in enumerate(messages):
            role = msg.get("role")
            if role == "user":
                ctx.user_turn += 1
                last_user_index = i
            elif role in ("system", "developer"):
                ctx.system += _text_of(msg.get("content")) + "\n"
        if last_user_index >= 0:
            text, ctx.markers = strip_markers(_text_of(messages[last_user_index].get("content")))
            ctx.last_user, ctx.line_today = split_date_line(text)
        ctx.step = sum(
            1
            for msg in messages[last_user_index + 1 :]
            if msg.get("role") == "assistant" and msg.get("tool_calls")
        )
        ctx.last_role = messages[-1].get("role", "") if messages else ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and _text_of(msg.get("content")).strip():
                ctx.prev_assistant = _text_of(msg.get("content"))
                break
        for msg in reversed(messages):
            if msg.get("role") == "user":
                lang = guess_lang(_user_text(msg.get("content")))
                if lang:
                    ctx.user_lang = lang
                    break
        for msg in messages:
            if msg.get("role") != "tool":
                continue
            name = msg.get("name") or _tool_name_for(messages, msg.get("tool_call_id"))
            try:
                ctx.tool_results[name] = json.loads(_text_of(msg.get("content")))
            except ValueError:
                continue
        # The Screener skill: reply in the `language` get_application returns. It is the stored
        # language, so a one-word answer in another language ("Mañana") changed nothing.
        record = ctx.tool_results.get("get_application") or {}
        stored = (record.get("value") if isinstance(record.get("value"), dict) else record).get(
            "language"
        )
        if isinstance(stored, str) and stored:
            ctx.user_lang = "en" if stored == "en" else "es"
            ctx.locale = stored
        if ctx.last_role == "tool":
            last = messages[-1]
            ctx.last_tool = last.get("name") or _tool_name_for(messages, last.get("tool_call_id"))
            try:
                ctx.tool_json = json.loads(_text_of(last.get("content")))
            except ValueError:
                ctx.tool_json = None
        elif ctx.last_role == "user" and ctx.last_user.startswith(TAP_RESULT_PREFIX):
            try:
                tapped = json.loads(ctx.last_user[len(TAP_RESULT_PREFIX) :])
            except ValueError:
                tapped = None
            if tapped is not None:
                tool = _tapped_tool(tapped)
                ctx.last_role, ctx.last_tool, ctx.tool_json = ("tool", tool, tapped)
                ctx.tool_results[tool] = tapped
        return ctx


def _tapped_tool(result: Any) -> str:
    """Which call the backend made for a tap: ``book_interview`` for a slot (ADR 0050, its
    result carries the Interview or a slot error), ``booking_link`` for "other time" (ADR 0064,
    no tool: the backend's own answer), else ``record_answer`` (ADR 0043)."""
    if not isinstance(result, dict):
        return "record_answer"
    value = result.get("value")
    if isinstance(value, dict) and value.get("status") == "booking_link":
        return "booking_link"
    if isinstance(value, dict) and "interview" in value:
        return "book_interview"
    if result.get("reason") in SLOT_ERRORS or result.get("error") in SLOT_ERRORS:
        return "book_interview"
    return "record_answer"


def _tool_name_for(messages: list[dict[str, Any]], call_id: str | None) -> str | None:
    if not call_id:
        return None
    for msg in reversed(messages):
        for call in msg.get("tool_calls") or []:
            if call.get("id") == call_id:
                return (call.get("function") or {}).get("name")
    return None


def _as_list(value: int | list[int]) -> list[int]:
    return value if isinstance(value, list) else [value]


def matches(match: Match, ctx: Context) -> bool:
    if match.user_turn is not None and ctx.user_turn not in _as_list(match.user_turn):
        return False
    if match.step is not None and ctx.step not in _as_list(match.step):
        return False
    if match.last_role is not None and ctx.last_role != match.last_role:
        return False
    if match.last_tool is not None and ctx.last_tool != match.last_tool:
        return False
    for path, expected in match.tool_json.items():
        if ctx.tool_json is None:
            return False
        try:
            actual = get_path(ctx.tool_json, path)
        except KeyError:
            return False
        if actual not in (expected if isinstance(expected, list) else [expected]):
            return False
    for path in match.tool_json_absent:
        if ctx.tool_json is None:
            return False
        try:
            get_path(ctx.tool_json, path)
        except KeyError:
            continue
        return False
    for path, needles in match.tool_json_contains.items():
        if ctx.tool_json is None:
            return False
        try:
            text = str(get_path(ctx.tool_json, path)).lower()
        except KeyError:
            return False
        if not any(n.lower() in text for n in needles):
            return False
    if match.history_tool is not None and match.history_tool not in ctx.tool_results:
        return False
    if match.marker is not None and match.marker not in ctx.markers:
        return False
    if match.interprets is not None and not ctx.interp.interprets(match.interprets, ctx.last_user):
        return False
    lowered = ctx.last_user.lower()
    if match.user_contains and not any(s.lower() in lowered for s in match.user_contains):
        return False
    if match.user_regex is not None and not re.search(match.user_regex, ctx.last_user):
        return False
    if match.user_lang is not None and ctx.user_lang != match.user_lang:
        return False
    if match.prev_assistant_contains and not any(
        s.lower() in ctx.prev_assistant.lower() for s in match.prev_assistant_contains
    ):
        return False
    return not (
        match.system_contains
        and not any(s.lower() in ctx.system.lower() for s in match.system_contains)
    )


def select(script: Script, ctx: Context) -> tuple[str, Respond]:
    """First matching rule, else the fallback. Returns (rule name, respond block).

    The matching rule's `user_regex` named groups become `{{match.<group>}}` (in ctx.extra)."""
    for i, rule in enumerate(script.rules):
        if matches(rule.match, ctx):
            if rule.match.user_regex is not None:
                found = re.search(rule.match.user_regex, ctx.last_user)
                ctx.extra["match"] = (found.groupdict() if found else {}) or {}
            return _rule_name(rule, i), rule.respond
    return "fallback", script.fallback


def _rule_name(rule: Rule, index: int) -> str:
    return rule.name or f"rule-{index}"


def _lookup(name: str, ctx: Context) -> Any:
    if name == "user":
        return ctx.last_user
    if name == "user_turn":
        return ctx.user_turn
    if name == "step":
        return ctx.step
    if name == "lang":
        return ctx.user_lang
    if name == "language":
        if ctx.user_turn > 1 and len(ctx.last_user.split()) < 2:
            return None  # a one-word answer never switches the language (Screener skill)
        return ctx.interp.api_language(ctx.last_user, guess_lang(ctx.last_user))
    if name == "zone_query":
        return ctx.interp.zone_query(ctx.last_user)
    if name == "zone_ids":
        return _zone_ids(ctx)
    if name == "interpret" or name.startswith("interpret."):
        field_name = name[len("interpret.") :] if "." in name else _next_field(ctx)
        if not field_name:
            return None
        return ctx.interp.value(field_name, ctx.last_user, _today(ctx))
    if name.startswith("match."):
        group = ctx.extra.get("match", {}).get(name[len("match.") :])
        return (group or "").strip().strip(_MATCH_TRIM).strip()
    if name.startswith("history."):
        tool, _, path = name[len("history.") :].partition(".")
        if tool in ctx.tool_results and path:
            try:
                return get_path(ctx.tool_results[tool], path)
            except KeyError:
                return ""
        return ""
    if name.startswith("tool.") and ctx.tool_json is not None:
        try:
            return get_path(ctx.tool_json, name[len("tool.") :])
        except KeyError:
            return ""
    return ""


def _next_field(ctx: Context) -> str | None:
    """`next_field` of the latest tool result that has one (get_application, record_answer)."""
    for source in (ctx.tool_json, ctx.tool_results.get("get_application")):
        if source is None:
            continue
        try:
            found = get_path(source, "next_field")
        except KeyError:
            continue
        return found if isinstance(found, str) else None
    return None


def _today(ctx: Context) -> date | None:
    """The Candidate's local today: the plugin's date line (ADR 0055), else the latest
    get_application result."""
    if ctx.line_today is not None:
        return ctx.line_today
    app = ctx.tool_results.get("get_application")
    try:
        return date.fromisoformat(str(get_path(app, "today"))) if app else None
    except (KeyError, ValueError):
        return None


def _zone_ids(ctx: Context) -> list[str] | None:
    """Every Zone id the last `search_zones` returned (spec 006b), or None to record `null`:
    nothing found, or `more` results than it lists (the agent asks again instead)."""
    if ctx.last_tool != "search_zones" or ctx.tool_json is None:
        return None
    try:
        zones = get_path(ctx.tool_json, "zones")
    except KeyError:
        return None
    try:
        more = bool(get_path(ctx.tool_json, "more"))
    except KeyError:
        more = False
    ids = [z["zone_id"] for z in zones if isinstance(z, dict) and "zone_id" in z]
    return None if more or not ids else ids


def render_value(value: Any, ctx: Context) -> Any:
    """Substitute {{user}}, {{user_turn}}, {{step}}, {{lang}}, {{tool.<path>}},
    {{history.<tool>.<path>}} and {{match.<group>}} (a named group of the rule's `user_regex`,
    trimmed of spaces and punctuation at both ends). The interpreter adds {{language}} (for
    `get_application`), {{interpret}} / {{interpret.<field>}} (the canonical value for
    `record_answer`), {{zone_query}} (for `search_zones`) and {{zone_ids}} (the ids of the last
    `search_zones`, or null)."""
    if isinstance(value, str):
        whole = _WHOLE_PLACEHOLDER.match(value)
        if whole:
            return _lookup(whole.group(1), ctx)
        return _PLACEHOLDER.sub(lambda m: str(_lookup(m.group(1), ctx)), value)
    if isinstance(value, dict):
        return {k: render_value(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, ctx) for v in value]
    return value


def pick_content(content: str | dict[str, str] | None, ctx: Context) -> str | None:
    if content is None:
        return None
    if isinstance(content, dict):
        # A rule may word a line per Spanish variant (es-ES, es-MX), as the skill does for
        # the Zone question: Spain says barrio, Mexico colonia.
        content = (
            content.get(ctx.locale)
            or content.get(ctx.user_lang)
            or content.get("es")
            or next(iter(content.values()))
        )
    return str(render_value(content, ctx))


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string, as in the OpenAI format


@dataclass
class Reply:
    rule: str
    content: str | None
    tool_calls: list[ToolCall]
    usage: dict[str, Any]
    delay_ms: int
    error: dict[str, Any] | None

    @property
    def finish_reason(self) -> str:
        return "tool_calls" if self.tool_calls else "stop"


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def build_usage(
    ctx: Context,
    defaults: Defaults,
    content: str | None,
    tool_calls: list[ToolCall],
    tools_param: Any,
    override: Respond | None = None,
) -> dict[str, Any]:
    """A realistic `usage` block: estimated from sizes, with cached and reasoning detail."""
    spec = override.usage if override else None
    prompt_text = json.dumps(ctx.messages, ensure_ascii=False) + (
        json.dumps(tools_param, ensure_ascii=False) if tools_param else ""
    )
    prompt = max(_estimate_tokens(prompt_text), defaults.min_prompt_tokens)
    completion = _estimate_tokens(
        (content or "") + "".join(c.arguments + c.name for c in tool_calls)
    )
    if spec is not None and spec.prompt_tokens is not None:
        prompt = spec.prompt_tokens
    if spec is not None and spec.completion_tokens is not None:
        completion = spec.completion_tokens
    has_history = ctx.user_turn > 1 or ctx.step > 0
    cached = int(prompt * defaults.cache_ratio) if has_history else 0
    if spec is not None and spec.cached_tokens is not None:
        cached = spec.cached_tokens
    cached = min(cached, prompt)
    usage: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": cached},
        "completion_tokens_details": {"reasoning_tokens": 0},
    }
    if defaults.price_per_mtok is not None:
        price = defaults.price_per_mtok
        billable = prompt - cached
        usage["cost"] = round(
            (billable * price.prompt + cached * price.prompt * 0.1 + completion * price.completion)
            / 1_000_000,
            8,
        )
    return usage


def make_call_id(ctx: Context, index: int) -> str:
    digest = hashlib.sha1(
        f"{len(ctx.messages)}:{ctx.user_turn}:{ctx.step}:{ctx.last_user}:{index}".encode()
    ).hexdigest()
    return f"call_{digest[:20]}"


def _arguments(spec: ToolCallSpec, ctx: Context) -> dict[str, Any]:
    rendered = render_value(spec.arguments, ctx)
    return {k: v for k, v in rendered.items() if not (k in spec.omit_empty and v in (None, ""))}


def respond_to(
    script: Script,
    messages: list[dict[str, Any]],
    tools_param: Any = None,
    interpreter: Interpreter | None = None,
) -> Reply:
    ctx = Context.from_messages(messages, interpreter)
    rule_name, block = select(script, ctx)
    if block.error is not None:
        error = {
            "message": str(render_value(block.error.message, ctx)),
            "type": block.error.type,
            "code": block.error.status,
        }
        return Reply(
            rule_name,
            None,
            [],
            {},
            block.delay_ms,
            {"status": block.error.status, **error},
        )
    content = pick_content(block.content, ctx)
    calls = [
        ToolCall(
            id=spec.id or make_call_id(ctx, i),
            name=spec.name,
            arguments=json.dumps(_arguments(spec, ctx), ensure_ascii=False),
        )
        for i, spec in enumerate(block.tool_calls)
    ]
    usage = build_usage(ctx, script.defaults, content, calls, tools_param, block)
    return Reply(rule_name, content, calls, usage, block.delay_ms, None)
