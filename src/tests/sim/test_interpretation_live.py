"""Opt-in live check: does a real model turn the personas' words into the canonical values?

The stub model looks words up (`sim.stub_llm.interpret`); this check asks a real model to do the
interpreting, with the Screener skill, the SOUL and the tool schemas Hermes would send, and
scores what it calls against the same persona values. It is the phrase check W3-3's model
comparison runs (spec 006b task 8, ADR 0042, decided answer 6).

Off by default and never in CI: the one test that calls a model needs ``CRIBA_LIVE_MODEL=1``.
Everything else here (building the request, scoring a reply, the spend guard) is plain code with
a fake transport and runs with the rest of the suite, so this file cannot rot unseen::

    CRIBA_LIVE_MODEL=1 OPENROUTER_API_KEY=... \\
        uv run --project src/backend pytest src/tests/sim/test_interpretation_live.py -q -s

Environment:

- ``CRIBA_LIVE_MODEL_ID``: the OpenRouter model (default: the Recruiter profile's);
- ``OPENROUTER_API_KEY``, or ``secrets/openrouter_api_key.txt``;
- ``CRIBA_LIVE_BUDGET_USD``: stop after this much spend (default 0.50). W3-3 keeps all its
  real-model runs within a $5 total, so a run says what it spent and W3-3 adds it up;
- ``CRIBA_LIVE_LIMIT``: run only the first N cases;
- ``CRIBA_LIVE_MIN_PASS``: the pass rate the test asks for (default 0.9);
- ``CRIBA_LIVE_REPORT_JSON``: write every case's result to this file.

A case passes when the model's first call for the field carries the persona's value, or, for the
Zone, when it searches the place the persona names. A message the persona maps to ``null`` (or
to nothing to search) passes when the model records nothing but ``null`` and searches nothing:
asking a follow-up in words is fine. The live check asks for one completion per message, so the
model's next tool round is not played.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from sim.personas.loader import load_personas
from sim.stub_llm.interpret import resolve_date

REPO_ROOT = Path(__file__).resolve().parents[3]
SOUL = REPO_ROOT / "src" / "hermes" / "profiles" / "criba-recruiter" / "SOUL.md"
PROFILE = REPO_ROOT / "src" / "hermes" / "profiles" / "criba-recruiter" / "config.yaml"
SKILL = REPO_ROOT / "src" / "skills" / "criba" / "screener" / "SKILL.md"
CONTRACT_PY = REPO_ROOT / "src" / "hermes" / "plugins" / "criba_tools" / "contract.py"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
TODAY = date(2026, 10, 9)  # a Friday, as in the skill's examples
WEEKDAY = "fri"

LIVE = os.environ.get("CRIBA_LIVE_MODEL") == "1"

# Words the skill's examples map to nothing yet: the right move is a follow-up or a null.
SKILL_FOLLOW_UPS = (
    ("start_date", "en cuanto me den la moto", "es"),
    ("start_date", "next week", "en"),
    ("zone", "por la salida a Toluca", "es"),
)


@dataclass(frozen=True)
class Case:
    id: str
    field: str
    text: str
    lang: str
    country: str
    expected: Any  # the canonical value, or the Zone query; None: map to nothing
    covers: bool = False

    @property
    def unmappable(self) -> bool:
        return self.expected is None


@dataclass
class Result:
    case: Case
    passed: bool
    detail: str
    cost: float = 0.0


def cases(directory: Path | None = None) -> list[Case]:
    """One case per distinct (field, words) the seed personas say, plus the skill's follow-ups."""
    personas = load_personas(directory) if directory else load_personas()
    found: dict[tuple[str, str], Case] = {}
    for persona in personas.values():
        for entry in persona.interpretations():
            key = (entry.field, entry.text)
            if key in found:
                continue
            expected = entry.query if entry.field == "zone" else entry.value
            if entry.field == "start_date":
                expected = resolve_date(expected, TODAY)
            found[key] = Case(
                id=f"{persona.id}:{entry.field}:{len(found)}",
                field=entry.field,
                text=entry.text,
                lang=entry.lang,
                country=persona.identity.country,
                expected=expected,
            )
    for field_name, text, lang in SKILL_FOLLOW_UPS:
        found.setdefault(
            (field_name, text),
            Case(f"skill:{field_name}:{len(found)}", field_name, text, lang, "MX", None),
        )
    return list(found.values())


# ---- the request ------------------------------------------------------------------------------


def _load_contract_module() -> Any:
    spec = importlib.util.spec_from_file_location("criba_tools_contract", CONTRACT_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


def tool_schemas() -> list[dict[str, Any]]:
    """The tools exactly as the Hermes plugin registers them, in the OpenAI format."""
    specs = _load_contract_module().tool_specs()
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.parameters,
            },
        }
        for s in specs
    ]


def system_prompt() -> str:
    skill = re.sub(r"\A---\n.*?\n---\n", "", SKILL.read_text(encoding="utf-8"), flags=re.DOTALL)
    return SOUL.read_text(encoding="utf-8").rstrip() + "\n\n" + skill.strip()


def request_messages(case: Case) -> list[dict[str, Any]]:
    """A fresh turn at the moment the model has read `get_application` and must act."""
    language = "en" if case.lang == "en" else ("es-MX" if case.country == "MX" else "es-ES")
    state = {
        "stage": "name" if case.field == "full_name" else case.field,
        "status": "open",
        "control": "agent",
        "language": language,
        "country": case.country,
        "next_field": case.field,
        "today": TODAY.isoformat(),
        "weekday": WEEKDAY,
    }
    # null or false keys, and the answers while a field is still to ask, are not sent (turn-size.md)
    return [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": case.text},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_application",
                        "arguments": json.dumps({"language": language}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "get_application",
            "content": json.dumps({"ok": True, "value": state}),
        },
    ]


# ---- scoring ----------------------------------------------------------------------------------


def _fold(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


def _calls(message: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except ValueError:
            arguments = {}
        calls.append((function.get("name", ""), arguments if isinstance(arguments, dict) else {}))
    return calls


def _same_value(expected: Any, got: Any) -> bool:
    if isinstance(expected, list) and isinstance(got, list):
        return sorted(map(str, expected)) == sorted(map(str, got))
    if isinstance(expected, int | float) and isinstance(got, int | float):
        return abs(float(expected) - float(got)) < 1e-9
    return bool(expected == got)


def _query_matches(expected: str, got: str) -> bool:
    """The agent may drop words ("Madrid, Chamberí" -> "Chamberí") but must keep the place."""
    wanted = [w for w in _fold(expected).split() if len(w) > 2]
    seen = _fold(got)
    return bool(wanted) and any(w in seen for w in wanted)


def score(case: Case, message: dict[str, Any]) -> tuple[bool, str]:
    calls = _calls(message)
    own = [a for n, a in calls if n == "record_answer" and a.get("field") == case.field]
    searches = [a for n, a in calls if n == "search_zones"]
    if case.unmappable:
        wrong = [a for a in own if a.get("value") is not None] + searches
        return (not wrong, "asked or recorded null" if not wrong else f"called {wrong}")
    if case.field == "zone":
        if not searches:
            return False, f"no search_zones call (calls: {[n for n, _ in calls]})"
        query = str(searches[0].get("query", ""))
        ok = _query_matches(str(case.expected), query)
        return ok, f"searched {query!r}, expected the place of {case.expected!r}"
    if not own:
        return (
            False,
            f"no record_answer for {case.field} (calls: {[n for n, _ in calls]})",
        )
    got = own[0].get("value")
    return _same_value(case.expected, got), f"recorded {got!r}, expected {case.expected!r}"


# ---- running ----------------------------------------------------------------------------------

Send = Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]


def run_cases(selected: Iterable[Case], send: Send, budget_usd: float) -> tuple[list[Result], bool]:
    """Play each case with `send`; stop once the spend passes `budget_usd`. Returns the results
    and whether the budget stopped the run early."""
    tools = tool_schemas()
    results: list[Result] = []
    spent = 0.0
    for case in selected:
        if spent >= budget_usd:
            return results, True
        response = send(request_messages(case), tools)
        cost = float((response.get("usage") or {}).get("cost") or 0.0)
        spent += cost
        message = (response.get("choices") or [{}])[0].get("message") or {}
        passed, detail = score(case, message)
        results.append(Result(case, passed, detail, cost))
    return results, False


def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    path = REPO_ROOT / "secrets" / "openrouter_api_key.txt"
    if not key and path.is_file():
        key = path.read_text(encoding="utf-8").strip()
    return key


def default_model() -> str:
    config = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    return str(config["model"]["default"])


def openrouter_sender(model: str, key: str) -> Send:
    client = httpx.Client(timeout=90)

    def send(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        response = client.post(
            API_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": messages,
                "tools": tools,
                "temperature": 0,
                "usage": {"include": True},
            },
        )
        response.raise_for_status()
        return dict(response.json())

    return send


@pytest.mark.live_model  # collected only with CRIBA_LIVE_MODEL=1 (sim/conftest.py)
def test_a_real_model_interprets_the_personas_words() -> None:
    key = api_key()
    assert key, "set OPENROUTER_API_KEY or secrets/openrouter_api_key.txt"
    model = os.environ.get("CRIBA_LIVE_MODEL_ID") or default_model()
    selected = cases()[: int(os.environ.get("CRIBA_LIVE_LIMIT") or 10_000)]
    budget = float(os.environ.get("CRIBA_LIVE_BUDGET_USD") or 0.50)
    results, stopped = run_cases(selected, openrouter_sender(model, key), budget)
    report = summarise(model, results, stopped, budget)
    print("\nlive interpretation:", json.dumps(report["summary"], indent=2))
    out = os.environ.get("CRIBA_LIVE_REPORT_JSON")
    if out:
        Path(out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    failed = [r for r in results if not r.passed]
    for r in failed:
        print(f"  FAIL {r.case.id} {r.case.text!r}: {r.detail}")
    assert not stopped, f"stopped at the ${budget} budget after {len(results)} cases"
    rate = float(os.environ.get("CRIBA_LIVE_MIN_PASS") or 0.9)
    assert report["summary"]["pass_rate"] >= rate, report["summary"]


def summarise(model: str, results: list[Result], stopped: bool, budget: float) -> dict[str, Any]:
    passed = sum(r.passed for r in results)
    return {
        "summary": {
            "model": model,
            "cases": len(results),
            "passed": passed,
            "pass_rate": passed / len(results) if results else 0.0,
            "spent_usd": round(sum(r.cost for r in results), 6),
            "budget_usd": budget,
            "stopped_by_budget": stopped,
        },
        "results": [
            {
                "id": r.case.id,
                "field": r.case.field,
                "text": r.case.text,
                "passed": r.passed,
                "detail": r.detail,
                "cost_usd": r.cost,
            }
            for r in results
        ],
    }


# ---- the parts that run with the suite --------------------------------------------------------


def reply(*calls: tuple[str, dict[str, Any]], cost: float = 0.001) -> dict[str, Any]:
    return {
        "usage": {"cost": cost},
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"c{i}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for i, (name, args) in enumerate(calls)
                    ],
                }
            }
        ],
    }


def case_of(field_name: str, expected: Any, text: str = "x", **extra: Any) -> Case:
    return Case("t", field_name, text, extra.get("lang", "es"), "ES", expected)


def test_the_cases_cover_every_field_and_the_follow_ups() -> None:
    found = cases()
    assert {c.field for c in found} == {
        "full_name",
        "zone",
        "driver_license",
        "availability",
        "preferred_schedule",
        "experience_years",
        "experience_platforms",
        "start_date",
    }
    assert len({(c.field, c.text) for c in found}) == len(found)
    assert any(c.unmappable for c in found)
    assert [c.text for c in found if c.field == "start_date" and c.unmappable]


def test_relative_dates_become_dates_for_the_given_today() -> None:
    monday = next(c for c in cases() if c.field == "start_date" and c.text == "el lunes")
    assert monday.expected == "2026-10-12"


def test_the_request_is_a_fresh_turn_after_get_application() -> None:
    case = case_of("start_date", "2026-10-12", "el lunes")
    messages = request_messages(case)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool"]
    assert messages[1]["content"] == "el lunes"
    state = json.loads(messages[3]["content"])["value"]
    assert state["next_field"] == "start_date"
    assert state["today"] == "2026-10-09" and state["weekday"] == "fri"
    assert "structured value, never their words" in messages[0]["content"]
    assert "SYNTHETIC" in messages[0]["content"]  # the SOUL and the skill both ride along
    assert not messages[0]["content"].startswith("---")  # no frontmatter


def test_the_tools_are_the_ones_the_plugin_registers() -> None:
    names = [t["function"]["name"] for t in tool_schemas()]
    assert "search_zones" in names and "record_answer" in names
    text = json.dumps(tool_schemas())
    assert "application_id" not in text and "$ref" not in text


@pytest.mark.parametrize(
    ("expected", "got", "ok"),
    [
        (["part_time", "weekends"], ["weekends", "part_time"], True),
        (["part_time"], ["part_time", "weekends"], False),
        (0.5, 0.5, True),
        (1, 1.0, True),
        ("yes", "yes", True),
        ("yes", "pending", False),
        ("2026-10-12", "2026-10-13", False),
    ],
)
def test_values_compare_as_the_backend_reads_them(expected, got, ok) -> None:
    case = case_of("availability", expected)
    verdict = score(
        case,
        reply(("record_answer", {"field": "availability", "value": got}))["choices"][0]["message"],
    )
    assert verdict[0] is ok


def test_a_zone_passes_when_the_agent_searches_the_place() -> None:
    case = case_of("zone", "Madrid, Chamberí")
    for query in ("Chamberí", "chamberi madrid", "Madrid, Chamberí"):
        message = reply(("search_zones", {"query": query}))["choices"][0]["message"]
        assert score(case, message)[0], query
    for query in ("Toledo", ""):
        message = reply(("search_zones", {"query": query}))["choices"][0]["message"]
        assert not score(case, message)[0], query
    assert not score(case, {"content": "¿En qué barrio?"})[0]  # it must act, not ask


def test_an_unmappable_message_passes_on_a_follow_up_or_a_null_and_fails_on_a_guess() -> None:
    case = case_of("start_date", None, "en cuanto me den la moto")
    assert score(case, {"content": "¿Qué fecha aproximada?"})[0]
    null = reply(("record_answer", {"field": "start_date", "value": None}))
    assert score(case, null["choices"][0]["message"])[0]
    guess = reply(("record_answer", {"field": "start_date", "value": "2026-10-10"}))
    assert not score(case, guess["choices"][0]["message"])[0]
    zone = case_of("zone", None, "por la salida a Toluca")
    assert not score(zone, reply(("search_zones", {"query": "Toluca"}))["choices"][0]["message"])[0]


def test_run_cases_adds_up_the_spend_and_stops_at_the_budget() -> None:
    selected = [case_of("driver_license", "yes", f"sí {i}") for i in range(5)]
    sent: list[int] = []

    def send(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        sent.append(len(tools))
        return reply(("record_answer", {"field": "driver_license", "value": "yes"}), cost=0.02)

    results, stopped = run_cases(selected, send, budget_usd=0.05)
    assert stopped and len(results) == 3 and all(r.passed for r in results)
    assert sent == [9, 9, 9]
    results, stopped = run_cases(selected, send, budget_usd=1.0)
    assert not stopped and len(results) == 5
    report = summarise("m", results, stopped, 1.0)
    assert report["summary"]["pass_rate"] == 1.0
    assert report["summary"]["spent_usd"] == pytest.approx(0.1)


def test_a_model_that_answers_in_words_is_a_failure_not_an_error() -> None:
    results, _ = run_cases(
        [case_of("full_name", "Ana García")],
        lambda m, t: {"choices": [{"message": {"content": "Hola"}}]},
        budget_usd=1.0,
    )
    assert not results[0].passed and "no record_answer" in results[0].detail


def test_the_default_model_is_the_recruiters() -> None:
    assert default_model().startswith("anthropic/")


def test_the_live_test_is_marked_and_so_off_unless_asked_for() -> None:
    """The one test that spends money is marked `live_model`, which the sim conftest collects
    only with CRIBA_LIVE_MODEL=1: out of CI and out of every default run."""
    marks = getattr(test_a_real_model_interprets_the_personas_words, "pytestmark", [])
    assert [m.name for m in marks] == ["live_model"]
    assert LIVE is (os.environ.get("CRIBA_LIVE_MODEL") == "1")


class _Item:
    def __init__(self, name: str, live: bool) -> None:
        self.name, self.live = name, live

    def get_closest_marker(self, name: str) -> object | None:
        return object() if self.live and name == "live_model" else None


class _Hook:
    def __init__(self) -> None:
        self.deselected: list[Any] = []

    def pytest_deselected(self, items: list[Any]) -> None:
        self.deselected += items


class _Config:
    def __init__(self) -> None:
        self.hook = _Hook()


def test_the_conftest_deselects_live_tests_unless_the_switch_is_on(monkeypatch) -> None:
    from sim import conftest

    def collect() -> tuple[list[Any], Any]:
        items: list[Any] = [
            _Item("plain", False),
            _Item("live", True),
            _Item("other", False),
        ]
        config: Any = _Config()
        conftest.pytest_collection_modifyitems(config, items)
        return items, config

    monkeypatch.delenv("CRIBA_LIVE_MODEL", raising=False)
    items, config = collect()
    assert [i.name for i in items] == ["plain", "other"]
    assert [i.name for i in config.hook.deselected] == ["live"]
    monkeypatch.setenv("CRIBA_LIVE_MODEL", "0")
    assert [i.name for i in collect()[0]] == ["plain", "other"]
    monkeypatch.setenv("CRIBA_LIVE_MODEL", "1")
    items, config = collect()
    assert [i.name for i in items] == ["plain", "live", "other"]
    assert config.hook.deselected == []
