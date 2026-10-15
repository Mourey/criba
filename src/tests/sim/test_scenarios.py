"""Scenario suite (spec 004 task 6): every seed persona against the real sim stack.

Each persona is played by the driver (`sim.driver`) through the public web chat API of a stack
started with `docker-compose.sim.yml` (backend, Hermes on the `criba-recruiter-sim` profile,
the stub model), and checked with `sim.asserts.assert_persona`: the case record and the tool
calls, never the agent's wording (spec 004, decided defaults). Two more scenarios cover the
paths no persona walks to the end: Approval -> slots -> booking, and STOP.

Skipped unless a sim stack is reachable. Bring one up under its own project name and port::

    CRIBA_WEB_PORT=18100 docker compose -p criba-w3-1 \\
        -f docker-compose.yml -f docker-compose.sim.yml up -d --build --wait
    CRIBA_E2E_BASE_URL=http://localhost:18100 \\
    CRIBA_E2E_SEED_PASSWORD_FILE=secrets/criba_seed_password.txt \\
    COMPOSE_PROJECT_NAME=criba-w3-1 \\
        uv run --project src/backend pytest src/tests/sim/test_scenarios.py -q

One at a time: the sim clock is global to the backend, so personas that move time cannot run
in parallel. Each run resets the clock first and uses a fresh phone number, so a persistent
stack can be reused.

The stub model's request log (`/__stub/requests`, read with `docker compose exec` because the
stub publishes no port) gives the tool calls the agent really made, and the prompt Hermes sent
on every model call. From it each scenario also checks the input size of every model call:
at most `TURN_TOKEN_BUDGET` calibrated tokens (the "Watch the turn size" item of the task list).
The estimate is characters / 4
over what the model reads (message text, tool-call names and arguments, and
the tool schemas as JSON), times `REAL_PER_ESTIMATED`, the ratio of OpenRouter's real input
counts to that estimate (see `docs/evidence/turn-size.md`, "Real usage"): the plain
characters / 4 undercounted the real prompt by a third. No tokenizer is available offline in
this repo (tiktoken is not a dependency, and Claude's tokenizer is not public), so it stays an
approximation. `test_turn_size_report` prints p50 and max across the suite;
`docs/evidence/turn-size.md` records a run. Every scenario also checks that none of the
Hermes boilerplate the criba-tools plugin trims reached the model (`_prompt_leaks`), and that
no tool result of a finished turn reached it whole (`_history_leaks`), and that every call's
latest user message ends with the plugin's date line (`_date_line_gaps`, ADR 0055).
Without the log (`COMPOSE_PROJECT_NAME` unset or the stub unreachable) those checks are
skipped, unless `CRIBA_REQUIRE_HERMES=1`, which makes them fail.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any

import httpx
import pytest

from sim import asserts
from sim.driver import PersonaDriver, RunResult
from sim.personas.loader import load_personas
from sim.personas.schema import Persona

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get("CRIBA_E2E_BASE_URL", "")
OPERATOR = "operator@criba.example"
REPO_ROOT = Path(__file__).resolve().parents[3]
TURN_TOKEN_BUDGET = 10_000  # the limit for any model call, checked on every scenario
# The target for the longest call across the suite. The real count of a call is its
# calibrated estimate plus the replies and re-asks a real model writes, which the scripted
# stub keeps short, so the target leaves about 5 % under the hard limit.
TURN_TOKEN_TARGET = 9_500
# The longest flow of the personas: 12 turns, a nine-row city list from search_zones and the
# "¿Vives en Toluca?" confirmation. It was the last persona over 10,000 real tokens on Haiku
# (max 11,066), so its longest call has its own assertion against the same target.
LONGEST_FLOW_PERSONA = "zone_freeform_mx"
CHARS_PER_TOKEN = 4  # the plain estimate
# Real input tokens (OpenRouter's native_tokens_prompt, Haiku 5.5) per estimated token.
# language_switch_mx: 13,316 real against 8,712 estimated (1.53); p50 over 116 calls: 10,905
# against 7,227 (1.51). 1.5 is the fit; Sonnet 5.5 counted more (16,113 on
# confirm_correction_mx against 9,044), see turn-size.md.
REAL_PER_ESTIMATED = 1.5
PERSONAS = load_personas()


def _stack_is_sim() -> bool:
    if not BASE_URL:
        return False
    try:
        return httpx.get(f"{BASE_URL}/api/sim/clock", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


if not _stack_is_sim():
    pytest.skip(
        "no sim stack: set CRIBA_E2E_BASE_URL to a stack started with docker-compose.sim.yml",
        allow_module_level=True,
    )


def _seed_password() -> str:
    path = os.environ.get("CRIBA_E2E_SEED_PASSWORD_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get("CRIBA_E2E_SEED_PASSWORD", "")


def _phone(country: str) -> str:
    digits = f"{random.randrange(10**8):08d}"
    return f"+346{digits}" if country == "ES" else f"+5255{digits}"


# ---- the stub model's request log ------------------------------------------------------------

_STUB_CALL = """
import json, sys, urllib.request
req = urllib.request.Request("http://127.0.0.1:8000" + sys.argv[2], method=sys.argv[1])
print(urllib.request.urlopen(req, timeout=10).read().decode())
"""


class StubLog:
    """`/__stub/requests` of the Compose project's `stub-llm` service, through `docker compose
    exec` (the stub is on the internal network only)."""

    def __init__(self, project: str) -> None:
        self.project = project

    def _call(self, method: str, path: str) -> Any:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                self.project,
                "exec",
                "-T",
                "stub-llm",
                "python",
                "-c",
                _STUB_CALL,
                method,
                path,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"stub log {method} {path}: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def clear(self) -> None:
        self._call("DELETE", "/__stub/requests")

    def requests(self) -> list[dict[str, Any]]:
        return self._call("GET", "/__stub/requests?limit=1000")["requests"]


def _stub_log() -> StubLog | None:
    project = os.environ.get("COMPOSE_PROJECT_NAME")
    reason = "set COMPOSE_PROJECT_NAME to the sim stack's Compose project"
    if project:
        log = StubLog(project)
        try:
            log.requests()
            return log
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            reason = f"stub request log unreachable in project {project!r}: {exc}"
    if os.environ.get("CRIBA_REQUIRE_HERMES") == "1":
        pytest.fail(reason, pytrace=False)
    return None


@pytest.fixture(scope="module")
def stub_log() -> StubLog | None:
    return _stub_log()


# ---- turn size --------------------------------------------------------------------------------


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)


@dataclass
class CallSize:
    """The input of one model call, in characters, split by where it comes from."""

    persona: str
    system: int
    history: int
    tools: int
    messages: int

    @property
    def chars(self) -> int:
        return self.system + self.history + self.tools

    @property
    def estimated_tokens(self) -> int:
        """The plain characters / 4 estimate (what turn-size.md used to report)."""
        return math.ceil(self.chars / CHARS_PER_TOKEN)

    @property
    def tokens(self) -> int:
        """The estimate calibrated to the real tokenizer's count."""
        return math.ceil(self.chars / CHARS_PER_TOKEN * REAL_PER_ESTIMATED)


def call_size(persona: str, body: dict[str, Any]) -> CallSize:
    system = history = 0
    for message in body.get("messages", []):
        size = len(_text(message.get("content")))
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            size += len(function.get("name", "")) + len(function.get("arguments", ""))
        if message.get("role") in ("system", "developer"):
            system += size
        else:
            history += size
    tools = len(json.dumps(body.get("tools") or [], ensure_ascii=False))
    return CallSize(persona, system, history, tools, len(body.get("messages", [])))


@dataclass
class Measured:
    calls: list[CallSize] = field(default_factory=list)
    personas: set[str] = field(default_factory=set)


MEASURED = Measured()


def tool_calls_in(requests: list[dict[str, Any]]) -> list[str]:
    """Tool names the agent called, from the assistant messages Hermes sent back as history."""
    names: list[str] = []
    seen: set[str] = set()
    for request in requests:
        for message in request["body"].get("messages", []):
            for call in message.get("tool_calls") or []:
                call_id = call.get("id") or json.dumps(call, sort_keys=True)
                if call_id not in seen:
                    seen.add(call_id)
                    names.append((call.get("function") or {}).get("name", ""))
    return names


def _measure(persona: str, requests: list[dict[str, Any]]) -> list[CallSize]:
    sizes = [call_size(persona, r["body"]) for r in requests]
    MEASURED.calls.extend(sizes)
    MEASURED.personas.add(persona)
    return sizes


def _over_budget(sizes: list[CallSize]) -> list[str]:
    return [
        f"model call over {TURN_TOKEN_BUDGET} calibrated input tokens: {s.tokens} "
        f"(estimated {s.estimated_tokens}; system {s.system}, history {s.history}, "
        f"tools {s.tools} chars)"
        for s in sizes
        if s.tokens > TURN_TOKEN_BUDGET
    ]


# Hermes boilerplate that the criba-tools plugin's llm_request middleware removes
# (hermes/plugins/criba_tools/prompt_trim.py) and the two config switches turn off. If a
# Hermes upgrade rewords one, it comes back here before it costs tokens in production.
HERMES_BOILERPLATE = (
    "You run on Hermes Agent",
    "Mid-turn user steering",
    "Active Hermes profile",
    "Hermes runtime environment",
    "Conversation started:",
    "# Finishing the job",
    "# Parallel tool calls",
    "MEDIA:/absolute/path",
)


def _prompt_leaks(requests: list[dict[str, Any]]) -> list[str]:
    """Boilerplate found in the system prompt of any model call."""
    leaks: set[str] = set()
    for request in requests:
        messages = request["body"].get("messages") or []
        system = " ".join(
            _text(m.get("content")) for m in messages if m.get("role") in ("system", "developer")
        )
        leaks.update(marker for marker in HERMES_BOILERPLATE if marker in system)
    return [f"Hermes boilerplate reached the model: {marker!r}" for marker in sorted(leaks)]


MAX_STALE_RESULT_CHARS = 300


def _history_leaks(requests: list[dict[str, Any]]) -> list[str]:
    """Tool results of a finished turn that reached the model whole. The criba-tools plugin's
    history middleware (hermes/plugins/criba_tools/history_trim.py) shrinks every result before
    the latest user message to its outcome (and a short list of options), so none is long."""
    longest = 0
    for request in requests:
        messages = request["body"].get("messages") or []
        users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        before = messages[: users[-1]] if users else []
        for message in before:
            if message.get("role") == "tool":
                longest = max(longest, len(_text(message.get("content"))))
    if longest <= MAX_STALE_RESULT_CHARS:
        return []
    return [
        (
            f"a tool result of {longest} characters from a finished turn reached the model "
            f"(history_trim keeps them under {MAX_STALE_RESULT_CHARS})"
        )
    ]


DATE_MARKER = "[criba date]"


def _date_line_gaps(requests: list[dict[str, Any]]) -> list[str]:
    """Model calls whose latest user message does not end with the plugin's date line, or with
    the marker anywhere else (hermes/plugins/criba_tools/turn_date.py, ADR 0055): every call
    knows the Candidate's local date, and only the backend's line carries the marker."""
    gaps: list[str] = []
    for n, request in enumerate(requests):
        messages = request["body"].get("messages") or []
        users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        if not users:
            continue
        latest = _text(messages[users[-1]].get("content"))
        # The skill names the marker in the system prompt; the conversation carries it once.
        wire = " ".join(
            _text(m.get("content"))
            for m in messages
            if m.get("role") not in ("system", "developer")
        )
        tail = latest.rsplit(DATE_MARKER, 1)[-1]
        if DATE_MARKER not in latest or "\n" in tail or wire.count(DATE_MARKER) != 1:
            gaps.append(f"model call {n}: the date line is missing, misplaced or repeated")
        elif not _weekday_dates_right(tail):
            gaps.append(f"model call {n}: the date line's weekday dates are wrong: {tail!r}")
    return gaps[:3]


_LINE_TODAY = re.compile(r"local today: [a-z]+ (\d{4}-\d{2}-\d{2});")
_LINE_WEEKDAY = re.compile(r"([a-z]+) (\d{4}-\d{2}-\d{2}) / (\d{4}-\d{2}-\d{2})")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _weekday_dates_right(line: str) -> bool:
    """The line lists all seven weekdays, each with its next date strictly after today and the
    date a week later (ADR 0054's rule, ADR 0058), on the backend's (the sim's) clock."""
    today = _LINE_TODAY.search(line)
    listed = _LINE_WEEKDAY.findall(line)
    if not today or len(listed) != 7:
        return False
    day = date.fromisoformat(today.group(1))
    for name, first, later in listed:
        ahead = (_WEEKDAYS.index(name) - day.weekday()) % 7 or 7
        if first != (day + timedelta(days=ahead)).isoformat():
            return False
        if later != (day + timedelta(days=ahead + 7)).isoformat():
            return False
    return sorted(name for name, _, _ in listed) == sorted(_WEEKDAYS)


# ---- playing a persona ------------------------------------------------------------


async def _login(http: httpx.AsyncClient) -> None:
    password = _seed_password()
    if not password:
        pytest.skip("set CRIBA_E2E_SEED_PASSWORD_FILE (the stack's seed password)")
    response = await http.post("/api/auth/login", json={"email": OPERATOR, "password": password})
    assert response.status_code == 200, response.text


async def _settled(http: httpx.AsyncClient, phone: str, wait_s: float = 10.0) -> dict[str, Any]:
    """The Application once background work after the last turn (the summary) has landed."""
    deadline = time.monotonic() + wait_s
    app = await asserts.find_application(http, phone)
    while time.monotonic() < deadline and app.get("summary") is None:
        await asyncio.sleep(0.5)
        app = await asserts.find_application(http, phone)
    return app


@dataclass
class Played:
    run: RunResult
    app: dict[str, Any]
    phone: str
    requests: list[dict[str, Any]] | None


async def _play(persona: Persona, log: StubLog | None) -> Played:
    phone = _phone(persona.identity.country)
    driver = PersonaDriver.for_base_url(BASE_URL, reply_timeout_s=60.0, poll_wait_s=5)
    try:
        assert driver.clock is not None
        await driver.clock.reset()
        if log is not None:
            log.clear()
        run = await driver.run(persona, phone=phone)
    finally:
        await driver.aclose()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=15) as http:
        await _login(http)
        app = await _settled(http, phone)
    return Played(run, app, phone, log.requests() if log is not None else None)


def _report(played: Played) -> str:
    run, app = played.run, played.app
    lines = [f"ended={run.ended} stage={app.get('stage')} status={app.get('status')}"]
    lines.append(f"  events: {asserts.event_kinds(app)}")
    lines += [f"  {e.sender}[{e.purpose or ''}]: {e.text[:90]!r}" for e in run.entries]
    if played.requests is not None:
        lines.append(f"  stub rules: {[r['rule'] for r in played.requests]}")
    return "\n".join(lines)


def _check(persona: Persona, played: Played) -> list[str]:
    tool_calls = tool_calls_in(played.requests) if played.requests is not None else None
    failures = list(asserts.check_persona(persona, played.app, played.run, tool_calls=tool_calls))
    if played.requests is not None:
        failures += _over_budget(_measure(persona.id, played.requests))
        failures += _prompt_leaks(played.requests)
        failures += _history_leaks(played.requests)
        failures += _date_line_gaps(played.requests)
    return failures


def _asked(run: RunResult) -> list[str]:
    """Fields the persona answered because the agent asked them (`answer:<field>`)."""
    return [
        e.purpose.split(":", 1)[1] for e in run.entries if (e.purpose or "").startswith("answer:")
    ]


@pytest.mark.parametrize("persona_id", sorted(PERSONAS))
def test_persona(persona_id: str, stub_log: StubLog | None) -> None:
    persona = PERSONAS[persona_id]
    played = asyncio.run(_play(persona, stub_log))
    failures = _check(persona, played)
    # Several fields in one message (`covers`): the agent never asks for the others again.
    given = set(persona.opening.covers)
    for field_name, replies in persona.answers.items():
        for reply in replies:
            given |= set(reply.covers) - {field_name}
    asked_again = [f for f in _asked(played.run) if f in given]
    if asked_again:
        failures.append(f"asked again for fields given at once: {asked_again}")
    gap = KNOWN_GAPS.get(persona_id)
    known = [f for f in failures if gap and f.startswith(gap[0])]
    rest = [f for f in failures if f not in known]
    assert not rest, "\n".join(rest) + "\n" + _report(played)
    if known and gap:
        pytest.xfail(f"{gap[1]} ({known[0]})")


# Backend gaps a persona runs into: (failure prefix, reason). Only that failure is excused; every
# other check of the persona still has to pass. Empty since spec 006b: `get_application(language)`
# closed the last one (`opted_out_code_switch_es` and the language it ends in).
KNOWN_GAPS: dict[str, tuple[str, str]] = {}


# ---- Approval -> slots -> booking -------------------------------------------------------------


async def _read_new(
    http: httpx.AsyncClient, conversation_id: str, after: int, wait_s: float = 60
) -> list[dict[str, Any]]:
    """Messages after `after`, once the run they belong to has finished."""
    url = f"/api/chat/conversations/{conversation_id}/messages"
    deadline = time.monotonic() + wait_s
    messages: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        page = (await http.get(url, params={"after": after, "wait": 5})).json()
        messages += page["messages"]
        after = page["cursor"]
        if messages and page["run_finished"]:
            break
    return messages


def test_approval_offers_slots_and_a_tapped_slot_is_booked(
    stub_log: StubLog | None,
) -> None:
    """qualified_es to Qualified, a Recruiter Approves, the system turn offers slots as buttons,
    the Candidate taps one, and the backend books it before the agent confirms (ADR 0038, Q41,
    ADR 0050)."""
    persona = PERSONAS["qualified_es"]

    async def scenario() -> tuple[Played, dict[str, Any], list[dict], list[dict]]:
        played = await _play(persona, stub_log)
        assert played.app["stage"] == "qualified", _report(played)
        conv = played.run.conversation_id
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=15) as http:
            await _login(http)
            queue = (await http.get("/api/queue", params={"kind": "approval"})).json()
            item = next(i for i in queue["items"] if i["application"]["id"] == played.app["id"])
            if stub_log is not None:
                stub_log.clear()
            decided = await http.post(
                f"/api/queue/approvals/{item['id']}", json={"decision": "approved"}
            )
            assert decided.status_code == 200, decided.text
            after = max(e.id or 0 for e in played.run.entries)
            offered = await _read_new(http, conv, after)
            offer = next(m for m in offered if m.get("interactive"))
            slot = next(o for o in offer["interactive"]["options"] if o["id"].startswith("so_"))
            tapped = await http.post(
                f"/api/chat/conversations/{conv}/replies",
                json={
                    "message_id": f"w3-tap-{played.phone}",
                    "in_reply_to": offer["id"],
                    "option_id": slot["id"],
                },
            )
            assert tapped.status_code == 202, tapped.text
            booked = await _read_new(http, conv, offer["id"])
            detail = (await http.get(f"/api/applications/{played.app['id']}")).json()
        played.requests = stub_log.requests() if stub_log is not None else None
        return played, detail, offered, booked

    played, detail, offered, booked = asyncio.run(scenario())
    kinds = asserts.event_kinds(detail)
    assert detail["stage"] == "interview_booked", kinds
    for kind in ("approved", "system_turn", "slots_offered", "interview_booked"):
        assert kind in kinds, kinds
    assert detail.get("interview"), detail.get("interview")
    # The system turn never shows in the transcript; the Candidate sees the offer, then a reply.
    assert all("[system]" not in m["text"] for m in offered + booked)
    assert [m for m in booked if m["direction"] == "outbound" and m["sender"] == "agent"]
    if played.requests is not None:
        called = tool_calls_in(played.requests)
        assert "offer_interview_slots" in called, called
        # ADR 0050: the backend booked the tapped slot before the run; the agent got the
        # book_interview result as its input and only confirmed it.
        users = [
            _text(m.get("content"))
            for r in played.requests
            for m in r["body"].get("messages", [])
            if m.get("role") == "user"
        ]
        taps = [t for t in users if t.startswith("[tap result] ")]
        assert any('"interview_booked"' in t for t in taps), taps
        assert not _over_budget(_measure("approval_booking", played.requests))


def test_approval_other_time_tap_sends_the_booking_link_and_the_slots_still_book(
    stub_log: StubLog | None,
) -> None:
    """Tapping "Otro horario" after Approval puts the booking link in the chat whatever the
    model writes, keeps the slot buttons, and a slot tapped after it is booked (ADR 0064)."""
    persona = PERSONAS["qualified_es"]

    async def scenario() -> tuple[Played, dict[str, Any], dict, list[dict], list[dict]]:
        played = await _play(persona, stub_log)
        assert played.app["stage"] == "qualified", _report(played)
        conv = played.run.conversation_id
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=15) as http:
            await _login(http)
            queue = (await http.get("/api/queue", params={"kind": "approval"})).json()
            item = next(i for i in queue["items"] if i["application"]["id"] == played.app["id"])
            decided = await http.post(
                f"/api/queue/approvals/{item['id']}", json={"decision": "approved"}
            )
            assert decided.status_code == 200, decided.text
            after = max(e.id or 0 for e in played.run.entries)
            offered = await _read_new(http, conv, after)
            offer = next(m for m in offered if m.get("interactive"))
            options = offer["interactive"]["options"]
            assert options[-1]["id"] == "other", options
            other = await http.post(
                f"/api/chat/conversations/{conv}/replies",
                json={
                    "message_id": f"w3-other-{played.phone}",
                    "in_reply_to": offer["id"],
                    "option_id": "other",
                },
            )
            assert other.status_code == 202, other.text
            linked = await _read_new(http, conv, offer["id"])
            reply = next(m for m in linked if m["direction"] == "outbound")
            slot = next(o for o in reply["interactive"]["options"] if o["id"].startswith("so_"))
            tapped = await http.post(
                f"/api/chat/conversations/{conv}/replies",
                json={
                    "message_id": f"w3-slot-{played.phone}",
                    "in_reply_to": reply["id"],
                    "option_id": slot["id"],
                },
            )
            assert tapped.status_code == 202, tapped.text
            booked = await _read_new(http, conv, reply["id"])
            detail = (await http.get(f"/api/applications/{played.app['id']}")).json()
            token = reply["text"][reply["text"].index("http") :].split()[0].rsplit("/", 1)[1]
            page = await http.get(f"/api/public/booking/{token}")
            assert page.status_code == 200, page.text
        return played, detail, reply, linked, booked

    _, detail, reply, _linked, booked = asyncio.run(scenario())
    assert "/book/" in reply["text"], reply["text"]
    assert [o["id"] for o in reply["interactive"]["options"]][-1] == "other"
    assert detail["stage"] == "interview_booked", asserts.event_kinds(detail)
    assert [m for m in booked if m["direction"] == "outbound" and m["sender"] == "agent"]


# ---- STOP ------------------------------------------------------------


async def _conversation(http: httpx.AsyncClient, phone: str) -> str:
    response = await http.post(
        "/api/chat/conversations",
        json={
            "country": "ES",
            "phone": phone,
            "locale": "es-ES",
            "client": "simulator",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


async def _send(http: httpx.AsyncClient, conv: str, text: str, n: int) -> int:
    response = await http.post(
        f"/api/chat/conversations/{conv}/messages",
        json={"message_id": f"w3-{conv}-{n}", "text": text},
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_stop_mid_screening_opts_out_without_a_run(stub_log: StubLog | None) -> None:
    """STOP as the whole message, with an open Application: Opted out, one confirmation, no
    agent run, and the Application stays open where it was (Q16, Q42)."""

    async def scenario() -> tuple[dict[str, Any], list[dict], list[dict] | None]:
        phone = _phone("ES")
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=15) as http:
            await _login(http)
            conv = await _conversation(http, phone)
            after = await _send(http, conv, "Hola", 1)
            await _read_new(http, conv, after)
            after = await _send(http, conv, "Ana García", 2)
            await _read_new(http, conv, after)
            if stub_log is not None:
                stub_log.clear()
            after = await _send(http, conv, "STOP", 3)
            await asyncio.sleep(3)  # long enough for a run to have answered, had one started
            replies = (
                await http.get(f"/api/chat/conversations/{conv}/messages", params={"after": after})
            ).json()["messages"]
            app = await asserts.find_application(http, phone)
        return app, replies, stub_log.requests() if stub_log is not None else None

    app, replies, requests = asyncio.run(scenario())
    kinds = asserts.event_kinds(app)
    assert "opted_out" in kinds, kinds
    assert app["status"] == "open" and app["stage"] == "zone", (
        app["status"],
        app["stage"],
    )
    assert app["candidate"].get("opted_out") is True, app["candidate"]
    assert len([m for m in replies if m["direction"] == "outbound"]) == 1, replies
    assert "system_turn" not in kinds
    if requests is not None:
        assert requests == [], [r["rule"] for r in requests]


def test_stop_as_the_first_message_creates_no_application(
    stub_log: StubLog | None,
) -> None:
    """Q42: a STOP never creates or reopens an Application; the confirmation is stored
    without one, and no agent run starts."""

    async def scenario() -> tuple[list[dict], list[dict] | None, str]:
        phone = _phone("ES")
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=15) as http:
            await _login(http)
            conv = await _conversation(http, phone)
            if stub_log is not None:
                stub_log.clear()
            await _send(http, conv, "STOP", 1)
            await asyncio.sleep(3)
            messages = (await http.get(f"/api/chat/conversations/{conv}/messages")).json()[
                "messages"
            ]
            with pytest.raises(LookupError):
                await asserts.find_application(http, phone)
        return messages, stub_log.requests() if stub_log is not None else None, phone

    messages, requests, _ = asyncio.run(scenario())
    assert [m["direction"] for m in messages] == ["inbound", "outbound"], messages
    assert messages[1]["sender"] == "system"
    if requests is not None:
        assert requests == [], [r["rule"] for r in requests]


# ---- turn size across the suite ---------------------------------------------------------------


def test_turn_size_report(stub_log: StubLog | None) -> None:
    """p50 and max calibrated input tokens per model call (the characters / 4 estimate times
    `REAL_PER_ESTIMATED`), across every scenario above (or qualified_es alone when this test
    runs by itself). Fails over the budget, over the target for the longest call, and over the
    target for the longest call of `LONGEST_FLOW_PERSONA` on its own."""
    if stub_log is None:
        pytest.skip("no stub request log: set COMPOSE_PROJECT_NAME")
    alone = [] if MEASURED.calls else ["qualified_es"]
    if LONGEST_FLOW_PERSONA not in MEASURED.personas:
        alone.append(LONGEST_FLOW_PERSONA)
    for persona_id in alone:
        played = asyncio.run(_play(PERSONAS[persona_id], stub_log))
        assert played.requests is not None
        _measure(persona_id, played.requests)
    calls = MEASURED.calls
    tokens = sorted(c.tokens for c in calls)
    biggest = max(calls, key=lambda c: c.tokens)
    longest_flow = max(c.tokens for c in calls if c.persona == LONGEST_FLOW_PERSONA)
    summary = {
        "method": (
            f"chars/{CHARS_PER_TOKEN} over message text, tool calls and tool schemas, "
            f"times {REAL_PER_ESTIMATED}"
        ),
        "scenarios": sorted(MEASURED.personas),
        "model_calls": len(calls),
        "p50_tokens": median(tokens),
        "p90_tokens": tokens[int(0.9 * (len(tokens) - 1))],
        "p50_tokens_estimated": median(c.estimated_tokens for c in calls),
        "max_tokens": biggest.tokens,
        "max_tokens_estimated": biggest.estimated_tokens,
        "max_call": {
            "persona": biggest.persona,
            "messages": biggest.messages,
            "system_chars": biggest.system,
            "history_chars": biggest.history,
            "tools_chars": biggest.tools,
        },
        "median_split_chars": {
            "system": median(c.system for c in calls),
            "history": median(c.history for c in calls),
            "tools": median(c.tools for c in calls),
        },
        "longest_flow": {"persona": LONGEST_FLOW_PERSONA, "max_tokens": longest_flow},
        "max_tokens_by_persona": {
            persona: max(c.tokens for c in calls if c.persona == persona)
            for persona in sorted(MEASURED.personas)
        },
    }
    print("\nturn size:", json.dumps(summary, indent=2, ensure_ascii=False))
    out = os.environ.get("CRIBA_TURN_SIZE_JSON")
    if out:
        Path(out).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    assert biggest.tokens <= TURN_TOKEN_BUDGET, summary
    assert biggest.tokens <= TURN_TOKEN_TARGET, summary
    assert longest_flow <= TURN_TOKEN_TARGET, summary
