"""Assertion helpers over the Application, its events and usage (spec 004 task 5, ADR 0017).

Scenarios assert the case record, never the agent's prose: final stage and Outcome, answers,
the order fields were first recorded, events, tool calls, the Knockout template and link, the
AI disclosure in the first message, nudges, and reply latency.

Inputs are plain dicts, as the REST API returns them (`contracts/api.openapi.yaml`):

- `app`: the `ApplicationDetail` (`GET /api/applications/{id}`); `find_application` fetches it by
  the persona's phone number. It carries `answers`, `events`, `messages`, `summary`, `candidate`.
- `run`: the driver's `RunResult` (transcript with timings), for latency and reply language.
- `usage`: optional rows of the `usage` table (`contracts/schema.sql`).
- `tool_calls`: optional list of tool names the agent called, for example from the stub model's
  request log. The REST API has no tool-call record, so without it tool calls are inferred from
  the events they leave behind (`TOOL_EVENTS`, and `finish_screening` from a Qualified
  `screening_finished`, see `finished_qualified`).

What the backend records for a Knockout (`criba.tools.application._knock_out`,
`repo.close_application`, `criba.tools.knockout.knockout_message`):

- a `knockout` event, actor `agent`, `data` = `{reason, template, criterion, stage}`, plus
  `message_id` (the agent message that carried the text) once the reply is sent. The text
  itself is never in the event (Q38: no Candidate free text in events);
- then a `screening_finished` event with `data` = `{"result": "knockout"}`. Abandoned and
  Withdrawn closes write one too (`{"result": "abandoned" | "withdrawn"}`); a Qualified screening
  writes `{"result": "qualified"}`. Only the last is a `finish_screening` call;
- the Knockout text goes out as the agent's ordinary reply (`content_type` `text`, no
  `template_name`), the message `data.message_id` names. The template name only appears in
  `data.template`.

Knockout template naming rule (the backend's, `KNOCKOUT_TEMPLATE_RE`):
`knockout_<criterion>[_<reason>].<locale>`, for example `knockout_zone.es-MX`,
`knockout_driver_license.en`, `knockout_driver_license_pending.es-ES`. The locale is one of
`es-ES`, `es-MX`, `en` and comes after the dot. The `_<reason>` suffix exists only for the
variants that differ from the criterion's default (driver_license: expired, suspended, pending).
The older `knockout_<locale>` spelling (no criterion, no dot) is still read, kept on purpose:
accepted for older records. The fake web chat writes the current spelling.

Every `check_*` returns a list of failure strings; every `assert_*` raises one `AssertionError`
naming all of them. `check_persona` / `assert_persona` run everything the persona expects.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

import httpx

from sim.detect import EMOJI_RE, URL_RE, guess_language, has_ai_disclosure, normalize
from sim.driver import RunResult
from sim.personas.schema import FIELD_ORDER, AnswerCheck, Expect, Persona

# Events a tool call leaves on the Application. `get_application` and `search_zones` leave none
# of their own (the second reads reference tables; the city list it may offer is the same
# `answer_ambiguous` record_answer writes, ADR 0062), so they cannot be checked from the record.
# Knockouts come from record_answer and leave a `knockout` event too.
# `finish_screening` is not listed: `screening_finished` is also logged when a Knockout,
# Abandoned or Withdrawn closes the Application, so `inferred_tools` reads it through
# `finished_qualified` instead.
TOOL_EVENTS: dict[str, tuple[str, ...]] = {
    "record_answer": (
        "answer_recorded",
        "answer_invalid",
        "answer_ambiguous",
        "knockout",
    ),
    "request_review": ("review_requested",),
    "raise_escalation": ("escalation_raised",),
    "record_backout": ("withdrawn", "opted_out"),
    "offer_interview_slots": ("slots_offered",),
    "book_interview": ("interview_booked",),
}
UNOBSERVABLE_TOOLS = frozenset({"get_application", "search_zones"})

FIRST_REPLY_LIMIT_S = 5.0  # Phase 1 section 6: first reply within 5 s, later ones within 10 s


class Failures(list):
    """A list of failure messages that reads as a bool (empty means pass)."""


# ---- fetching -----------------------------------------------------------------------


async def find_application(
    http: httpx.AsyncClient, phone: str, *, include_cost: bool = False
) -> dict[str, Any]:
    """Fetch the newest Application's detail for a Candidate phone number.

    `http` must carry a Recruiter or Operator session (cookie `criba_session`). Raises
    LookupError when none matches.
    """
    listing = await http.get(
        "/api/applications", params={"q": phone, "sort": "-created_at", "limit": 20}
    )
    listing.raise_for_status()
    match = next(
        (i for i in listing.json()["items"] if (i["candidate"].get("phone") or "") == phone),
        None,
    )
    if match is None:
        raise LookupError(f"no Application for {phone}")
    detail = await http.get(f"/api/applications/{match['id']}")
    detail.raise_for_status()
    body = detail.json()
    if not include_cost:
        body.pop("cost", None)
    return body


# ---- views over the record -----------------------------------------------------------


def events_of(app: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(app.get("events", []), key=lambda e: e["id"])


def event_kinds(app: dict[str, Any]) -> list[str]:
    return [e["kind"] for e in events_of(app)]


def recorded_order(app: dict[str, Any]) -> list[str]:
    """Fields in the order they were first recorded.

    From `answer_recorded` events carrying `data.field`. The failed Knock-out field is among
    them: the agent records it (`answer_recorded`) before closing, and the `knockout` event
    names a `criterion`, never a `field`. When no event names its field, falls back to
    `answer_status.recorded_at`, which cannot separate answers recorded in the same instant.
    """
    order: list[str] = []
    named = False
    for event in events_of(app):
        data = event.get("data") or {}
        if event["kind"] == "answer_recorded" and data.get("field"):
            named = True
            if data["field"] not in order:
                order.append(data["field"])
    if named:
        return order
    status = [
        s
        for s in app.get("answer_status", [])
        if s.get("recorded_at") and s.get("status") in ("valid", "flagged", "knockout")
    ]
    status.sort(key=lambda s: (s["recorded_at"], FIELD_ORDER.index(s["field"])))
    return [s["field"] for s in status]


def finished_qualified(app: dict[str, Any]) -> bool:
    """True when a `screening_finished` event says screening ended Qualified.

    `repo.mark_qualified` (the `finish_screening` tool) writes `{"result": "qualified"}`;
    `repo.close_application` writes `{"result": <outcome>}` for a Knockout, Abandoned or
    Withdrawn close; the fake web chat writes the same `{"result": ...}` shape. A payload with
    `{"stage": "qualified"}` instead of `result` is accepted for older records.
    When the payload names neither, fall back to the Application: Qualified is not an Outcome,
    so it counts only when there is no `outcome` and no `knockout` event.
    """
    kinds = event_kinds(app)
    for event in events_of(app):
        if event["kind"] != "screening_finished":
            continue
        data = event.get("data") or {}
        verdict = data.get("result") or data.get("stage")  # `stage`: older records
        if verdict is not None:
            if verdict == "qualified":
                return True
        elif app.get("outcome") is None and "knockout" not in kinds:
            return True
    return False


def inferred_tools(app: dict[str, Any]) -> set[str]:
    kinds = set(event_kinds(app))
    tools = {tool for tool, produced in TOOL_EVENTS.items() if kinds & set(produced)}
    if finished_qualified(app):
        tools.add("finish_screening")
    return tools


def app_language(app: dict[str, Any]) -> str | None:
    structured = (app.get("summary") or {}).get("structured") or {}
    return structured.get("language") or (app.get("candidate") or {}).get("language")


def _agent_messages(app: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in app.get("messages", []) if m["direction"] == "outbound"]


# ---- stage, Outcome, answers ---------------------------------------------------------


def check_stage(app: dict[str, Any], expect: Expect) -> Failures:
    out = Failures()
    for name, want, got in (
        ("stage", expect.stage, app.get("stage")),
        ("status", expect.status, app.get("status")),
        ("outcome", expect.outcome, app.get("outcome")),
        (
            "knockout_criterion",
            expect.knockout_criterion,
            app.get("knockout_criterion"),
        ),
    ):
        if want != got:
            out.append(f"{name}: expected {want!r}, got {got!r}")
    if expect.final_language and app_language(app) != expect.final_language:
        out.append(f"language: expected {expect.final_language!r}, got {app_language(app)!r}")
    if expect.final_country:
        got = app_country(app)
        if got != expect.final_country:
            out.append(f"country: expected {expect.final_country!r}, got {got!r}")
        elsewhere = [
            loc.get("id")
            for loc in app.get("locations") or []
            if loc.get("country") != expect.final_country
        ]
        if elsewhere:
            out.append(f"locations outside {expect.final_country}: {elsewhere}")
    return out


def app_country(app: dict[str, Any]) -> str | None:
    """The Application's country: the Candidate's, unless a recorded Zone moved it (ADR 0057;
    the zone's `answer_recorded` event says so, and the API shows no Application country)."""
    country = (app.get("candidate") or {}).get("country")
    for event in events_of(app):
        moved = (event.get("data") or {}).get("country_changed")
        if event["kind"] == "answer_recorded" and moved:
            country = moved.get("to")
    return country


def _canonical(value: Any) -> Any:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return sorted(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(value)
    return value


def _matches(check: AnswerCheck, got: Any) -> bool:
    if check.non_null:
        return got not in (None, "", [])
    if isinstance(got, dict) and not isinstance(check.value, dict):
        # A Zone is a ZoneRef; compare by name or id.
        return check.value in (got.get("name"), got.get("id"))
    return _canonical(got) == _canonical(check.value)


def check_fields(app: dict[str, Any], expect: Expect) -> Failures:
    out = Failures()
    answers = app.get("answers") or {}
    for name, check in expect.fields.items():
        got = answers.get(name)
        if not _matches(check, got):
            want = "a value" if check.non_null else repr(check.value)
            out.append(f"answers.{name}: expected {want}, got {got!r}")
    for name in expect.fields_unanswered:
        got = answers.get(name)
        if got not in (None, [], ""):
            out.append(f"answers.{name}: expected unanswered, got {got!r}")
    return out


def check_field_order(app: dict[str, Any], expect: Expect) -> Failures:
    if not expect.field_order:
        return Failures()
    got = recorded_order(app)
    if got != list(expect.field_order):
        return Failures([f"field order: expected {list(expect.field_order)}, got {got}"])
    return Failures()


# ---- events and tools ------------------------------------------------------------------


def _is_subsequence(wanted: Sequence[str], seq: Sequence[str]) -> bool:
    it = iter(seq)
    return all(any(item == w for item in it) for w in wanted)


def check_events(app: dict[str, Any], expect: Expect) -> Failures:
    out = Failures()
    kinds = event_kinds(app)
    if expect.events and not _is_subsequence(expect.events, kinds):
        out.append(f"events: expected {list(expect.events)} in that order within {kinds}")
    for item in expect.event_counts:
        count = kinds.count(item.kind)
        if count != item.count:
            out.append(f"events: expected {item.count} x {item.kind}, got {count}")
    for kind in expect.forbidden_events:
        # A persona that forbids `screening_finished` means "screening did not finish
        # Qualified": the backend also logs one when a Knockout, Abandoned or Withdrawn closes.
        if kind == "screening_finished" and not finished_qualified(app):
            continue
        if kind in kinds:
            out.append(f"events: forbidden {kind} present ({kinds.count(kind)}x)")
    if expect.nudges is not None:
        count = kinds.count("nudge_sent")
        if count != expect.nudges:
            out.append(f"nudges: expected {expect.nudges}, got {count}")
    return out


def check_tools(
    app: dict[str, Any], expect: Expect, tool_calls: Iterable[str] | None = None
) -> Failures:
    """Required and forbidden tools, from `tool_calls` or inferred from events."""
    out = Failures()
    called = set(tool_calls) if tool_calls is not None else inferred_tools(app)
    for tool in expect.tools:
        if tool in UNOBSERVABLE_TOOLS and tool_calls is None:
            continue
        if tool not in called:
            out.append(f"tools: expected a {tool} call, saw {sorted(called)}")
    for tool in expect.forbidden_tools:
        if tool in called:
            out.append(f"tools: forbidden {tool} was called")
    return out


# ---- messages: disclosure, Knockout template, text hygiene, language ----------------------


def agent_texts(app: dict[str, Any] | None, run: RunResult | None) -> list[str]:
    """Outbound texts, from the record when given, else from the transcript."""
    if app is not None and _agent_messages(app):
        return [m["body"] for m in _agent_messages(app)]
    return [e.text for e in (run.agent_entries if run else [])]


def check_ai_disclosure(
    app: dict[str, Any] | None = None, run: RunResult | None = None
) -> Failures:
    """The first agent message says it is an AI, a bot or a virtual assistant."""
    texts = agent_texts(app, run)
    if not texts:
        return Failures(["ai disclosure: the agent sent no message"])
    if not has_ai_disclosure(texts[0]):
        return Failures(["ai disclosure: the first agent message does not disclose AI use"])
    return Failures()


KNOCKOUT_TEMPLATE_RE = re.compile(
    r"^knockout_(?P<criterion>zone|driver_license)(?:_(?P<reason>[a-z]+))?"
    r"\.(?P<locale>es-ES|es-MX|en)$"
)
_LEGACY_TEMPLATE_RE = re.compile(r"(?:^|[_-])(?P<locale>es-ES|es-MX|en)$", re.IGNORECASE)
_LOCALES = {"es-es": "es-ES", "es-mx": "es-MX", "en": "en"}


def parse_knockout_template(name: str | None) -> dict[str, str | None] | None:
    """`knockout_<criterion>[_<reason>].<locale>` into its parts, or None when it does not fit."""
    match = KNOCKOUT_TEMPLATE_RE.match(name or "")
    return match.groupdict() if match else None


def template_locale(name: str | None) -> str | None:
    """Locale in a Knockout template name: the backend's `.es-MX` suffix, else the legacy
    `knockout_es-MX` spelling (accepted for older records)."""
    if not name:
        return None
    parsed = parse_knockout_template(name)
    if parsed:
        return parsed["locale"]
    legacy = _LEGACY_TEMPLATE_RE.search(name)
    return _LOCALES[legacy["locale"].lower()] if legacy else None


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _last_knockout_event(app: dict[str, Any]) -> dict[str, Any] | None:
    found = [e for e in events_of(app) if e["kind"] == "knockout"]
    return found[-1] if found else None


def _from_agent(message: dict[str, Any]) -> bool:
    return message["direction"] == "outbound" and message.get("sender", "agent") == "agent"


def _run_around(messages: list[dict[str, Any]], anchor: dict[str, Any]) -> list[dict[str, Any]]:
    """The agent messages sent in one go around `anchor`: bounded by the Candidate's messages."""
    index = messages.index(anchor)
    start = index
    while start > 0 and messages[start - 1]["direction"] != "inbound":
        start -= 1
    end = index
    while end + 1 < len(messages) and messages[end + 1]["direction"] != "inbound":
        end += 1
    return [m for m in messages[start : end + 1] if _from_agent(m)]


def _knockout_reply(
    app: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """`(messages, the Knockout message)`: the agent's reply to the Knockout as stored.

    The backend sends the Knockout text as an ordinary agent message (content type `text`),
    and the `knockout` event's `data.message_id` names that message. It is the anchor; the
    reply is the run of agent messages around it, up to the Candidate's neighbouring messages
    (timestamps can tie under a frozen sim clock, so order and ids decide, not time). Older
    records carry the text as `data.message` instead and anchor on the matching body. A
    template-typed message named *knockout* anchors the same way, for a channel that sends a
    real template. Without any of these, the reply is the agent messages sent after the event.
    """
    messages = sorted(app.get("messages", []), key=lambda m: m["id"])
    knockout = _last_knockout_event(app)
    anchor: dict[str, Any] | None = None
    data = (knockout or {}).get("data") or {}
    message_id = data.get("message_id")
    if message_id is not None:
        anchor = next((m for m in messages if m["id"] == message_id and _from_agent(m)), None)
    text = data.get("message")  # records written before the backend stopped copying it
    if anchor is None and text:
        anchor = next(
            (
                m
                for m in reversed(messages)
                if _from_agent(m) and normalize(m["body"]) == normalize(text)
            ),
            None,
        )
    if anchor is None:
        anchor = next(
            (
                m
                for m in reversed(messages)
                if _from_agent(m)
                and m.get("content_type") == "template"
                and "knockout" in (m.get("template_name") or "").lower()
            ),
            None,
        )
    if anchor is not None:
        return _run_around(messages, anchor), anchor
    if knockout is None:
        return [], None
    at = _when(knockout.get("created_at"))
    reply: list[dict[str, Any]] = []
    for m in messages:
        sent = _when(m.get("created_at"))
        after = at is not None and sent is not None and sent > at
        if m["direction"] == "inbound":
            if after or reply:
                break  # the Candidate wrote again before any reply, or the reply is over
            continue
        if not reply and not (_from_agent(m) and after):
            continue
        if _from_agent(m):
            reply.append(m)
    return reply, (reply[0] if reply else None)


def _template_name(app: dict[str, Any], message: dict[str, Any]) -> str | None:
    """The Knockout's template name: on the message if it is a template, else in the data of
    the `knockout` event (the backend's case), else of a `template_sent` event."""
    if message.get("template_name"):
        return message["template_name"]
    for kind in ("knockout", "template_sent"):
        for event in reversed(events_of(app)):
            data = event.get("data") or {}
            if event["kind"] == kind and data.get("template"):
                return data["template"]
    return None


def _template_language(app: dict[str, Any], message: dict[str, Any]) -> str | None:
    """Language of the Knockout template: the template name first (on the message, else the
    `knockout` event's `data.template`), then the language in the `knockout` or `template_sent`
    event data, and the detector on the prose as a last resort."""
    if locale := template_locale(message.get("template_name")):
        return locale
    for event in reversed(events_of(app)):
        data = event.get("data") or {}
        if event["kind"] not in ("knockout", "template_sent"):
            continue
        if locale := template_locale(data.get("template")):
            return locale
        value = data.get("language") or data.get("locale") or data.get("template_language")
        if value:
            return value
    body = normalize(message.get("body", ""))
    if guess_language(message.get("body", "")) == "en":
        return "en"
    if "licencia de manejo" in body or "sucursal" in body or "manejo" in body:
        return "es-MX"
    if "carnet" in body:
        return "es-ES"
    return None


def check_knockout_template(app: dict[str, Any], expect: Expect) -> Failures:
    """One Knockout message, in the right language, with a link and no emoji."""
    if expect.knockout_template is None:
        return Failures()
    out = Failures()
    found, message = _knockout_reply(app)
    if not found or message is None:
        if "knockout" not in event_kinds(app):
            return Failures(["knockout template: no knockout happened"])
        return Failures(["knockout template: no agent message after the knockout in the record"])
    if len(found) != 1:
        out.append(f"knockout template: expected one message, got {len(found)}")
    body = message.get("body", "")
    if not URL_RE.search(body):
        out.append("knockout template: the message has no link")
    if EMOJI_RE.search(body):
        out.append("knockout template: the message contains an emoji")
    language = _template_language(app, message)
    if language is None:
        out.append("knockout template: could not tell the template's language")
    elif language != expect.knockout_template and not (
        language == "es" and expect.knockout_template.startswith("es")
    ):
        out.append(f"knockout template: expected {expect.knockout_template}, got {language}")
    prose = guess_language(body)
    if prose is not None and prose != expect.knockout_template[:2]:
        out.append(
            f"knockout template: expected {expect.knockout_template}, but the message "
            f"reads as {'English' if prose == 'en' else 'Spanish'}"
        )
    name = _template_name(app, message)
    parsed = parse_knockout_template(name)
    if name and name.startswith("knockout_") and "." in name and parsed is None:
        out.append(
            f"knockout template: name {name!r} does not fit "
            "knockout_<criterion>[_<reason>].<locale>"
        )
    if parsed and expect.knockout_criterion and parsed["criterion"] != expect.knockout_criterion:
        out.append(
            f"knockout template: name {name!r} is for {parsed['criterion']}, "
            f"expected {expect.knockout_criterion}"
        )
    return out


def check_forbidden_text(
    expect: Expect, app: dict[str, Any] | None = None, run: RunResult | None = None
) -> Failures:
    out = Failures()
    texts = agent_texts(app, run)
    labels = [e.sender_label or "" for e in (run.agent_entries if run else [])]
    haystack = [normalize(t) for t in texts + labels]
    for needle in expect.forbidden_agent_text:
        if any(normalize(needle) in text for text in haystack):
            out.append(f"agent text: forbidden {needle!r} appears in an agent message")
    return out


def check_language_follows(run: RunResult, expect: Expect) -> Failures:
    """Each agent reply is in the language of the Candidate's latest message."""
    if not expect.reply_languages_follow_latest:
        return Failures()
    out = Failures()
    latest: str | None = None
    for entry in run.entries:
        if entry.direction == "inbound":
            latest = entry.lang
            continue
        if entry.sender != "agent" or latest is None:
            continue
        got = guess_language(entry.text)
        if got is not None and got != latest:
            out.append(
                f"language: agent message {entry.id} is {got}, the Candidate last wrote {latest}"
            )
    return out


def check_reply_language(run: RunResult, expect: Expect) -> Failures:
    """Every agent reply is in `expect.reply_language` (a message whose language the crude
    guess cannot tell is left alone)."""
    if expect.reply_language is None:
        return Failures()
    out = Failures()
    for entry in run.entries:
        if entry.direction == "inbound" or entry.sender != "agent":
            continue
        got = guess_language(entry.text)
        if got is not None and got != expect.reply_language:
            out.append(
                f"language: agent message {entry.id} is {got}, every reply should be "
                f"{expect.reply_language}"
            )
    return out


def check_opt_out(app: dict[str, Any], expect: Expect) -> Failures:
    got = bool((app.get("candidate") or {}).get("opted_out"))
    if got != expect.candidate_opted_out:
        return Failures([f"opted_out: expected {expect.candidate_opted_out}, got {got}"])
    return Failures()


# ---- latency and usage ------------------------------------------------------------------


def reply_latencies(run: RunResult) -> list[float]:
    """Seconds from each Candidate message to the first agent message it drew."""
    return [t.latency_first_s for t in run.turns if t.latency_first_s is not None]


def check_latency(run: RunResult, expect: Expect | float | None = None) -> Failures:
    """First reply within 5 s, later replies within `max_reply_latency_s` (default 10 s).

    Measured by the driver on its own clock (send to first agent message), so it holds under
    a moved sim clock. Turns that never got a reply are failures too.
    """
    limit = expect.max_reply_latency_s if isinstance(expect, Expect) else float(expect or 10.0)
    out = Failures()
    first = True
    for turn in run.turns:
        if turn.latency_first_s is None:
            if turn.timed_out:
                out.append(f"latency: no reply to {turn.sent.purpose} (timed out)")
            continue
        allowed = min(FIRST_REPLY_LIMIT_S, limit) if first else limit
        first = False
        if turn.latency_first_s > allowed:
            out.append(
                f"latency: reply to {turn.sent.purpose} took {turn.latency_first_s:.2f}s "
                f"(limit {allowed:.1f}s)"
            )
    return out


def percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values")
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def check_usage(
    usage: Sequence[dict[str, Any]],
    *,
    max_latency_ms: float | None = None,
    environment: str | None = "sim",
    require_rows: bool = True,
) -> Failures:
    """Usage rows for the run: present, all `ok`, in the right environment, within latency."""
    out = Failures()
    if not usage:
        return Failures(["usage: no rows"]) if require_rows else out
    bad = [r for r in usage if r.get("status", "ok") != "ok"]
    if bad:
        out.append(f"usage: {len(bad)} row(s) with status error or timeout")
    if environment:
        stray = [r for r in usage if r.get("environment") not in (None, environment)]
        if stray:
            out.append(f"usage: {len(stray)} row(s) outside environment {environment!r}")
    if max_latency_ms is not None:
        latencies = [r["latency_ms"] for r in usage if r.get("latency_ms") is not None]
        if latencies and (p95 := percentile(latencies, 0.95)) > max_latency_ms:
            out.append(f"usage: p95 latency {p95:.0f} ms exceeds {max_latency_ms:.0f} ms")
    return out


# ---- everything a persona expects ----------------------------------------------------------


def check_persona(
    persona: Persona,
    app: dict[str, Any],
    run: RunResult | None = None,
    usage: Sequence[dict[str, Any]] | None = None,
    tool_calls: Iterable[str] | None = None,
) -> Failures:
    """All checks for `persona.expect`; transcript and usage checks run when those are given."""
    expect = persona.expect
    out = Failures()
    out += check_stage(app, expect)
    out += check_fields(app, expect)
    out += check_field_order(app, expect)
    out += check_events(app, expect)
    out += check_tools(app, expect, tool_calls)
    out += check_opt_out(app, expect)
    out += check_knockout_template(app, expect)
    if expect.ai_disclosure:
        out += check_ai_disclosure(app, run)
    out += check_forbidden_text(expect, app, run)
    if run is not None:
        out += check_latency(run, expect)
        out += check_language_follows(run, expect)
        out += check_reply_language(run, expect)
    if usage is not None:
        out += check_usage(usage, max_latency_ms=expect.max_reply_latency_s * 1000)
    return out


def _raise(label: str, failures: Failures) -> None:
    if failures:
        raise AssertionError(f"{label}:\n  - " + "\n  - ".join(failures))


def assert_persona(
    persona: Persona,
    app: dict[str, Any],
    run: RunResult | None = None,
    usage: Sequence[dict[str, Any]] | None = None,
    tool_calls: Iterable[str] | None = None,
) -> None:
    _raise(persona.id, check_persona(persona, app, run, usage, tool_calls))


def assert_stage(app: dict[str, Any], expect: Expect) -> None:
    _raise("stage", check_stage(app, expect))


def assert_fields(app: dict[str, Any], expect: Expect) -> None:
    _raise("fields", check_fields(app, expect))


def assert_field_order(app: dict[str, Any], expect: Expect) -> None:
    _raise("field order", check_field_order(app, expect))


def assert_events(app: dict[str, Any], expect: Expect) -> None:
    _raise("events", check_events(app, expect))


def assert_tools(
    app: dict[str, Any], expect: Expect, tool_calls: Iterable[str] | None = None
) -> None:
    _raise("tools", check_tools(app, expect, tool_calls))


def assert_ai_disclosure(app: dict[str, Any] | None = None, run: RunResult | None = None) -> None:
    _raise("ai disclosure", check_ai_disclosure(app, run))


def assert_knockout_template(app: dict[str, Any], expect: Expect) -> None:
    _raise("knockout template", check_knockout_template(app, expect))


def assert_latency(run: RunResult, expect: Expect | float | None = None) -> None:
    _raise("latency", check_latency(run, expect))


def assert_usage(usage: Sequence[dict[str, Any]], **kwargs: Any) -> None:
    _raise("usage", check_usage(usage, **kwargs))
