"""The flows of the live smoke run.

Each flow is one conversation (STOP has a second, free one) through the web chat API, then the
Operator REST API to read the record back. A flow passes when its checks pass, and the checks are
on the record (stage, events, answers, queue items, attachments), never on the agent's exact
words. Where a check looks at prose it is the summary: written, and complete.

A flow that raises is reported as an error with its message; it is never retried and no check is
loosened to make it pass.

The flows take a `LiveStack` (`live.client`), so a test can hand them a fake with canned
conversations. `run_flow` wraps one flow: times it, catches its error and charges its usage to
the spend guard whatever happened.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from criba.channel.copy import OPT_OUT_CONFIRMATION, PRIVACY_URL, UNREADABLE_HANDOVER
from criba.summary import DEFAULT_SUMMARY_MODEL as SUMMARY_MODEL
from criba.summary_text import leaked_codes
from live import personas
from live.client import Charge, LiveStack, Turn, summary_prose
from sim import asserts
from sim.detect import URL_RE, guess_language, has_ai_disclosure, zone_options
from sim.driver import Entry, RunResult
from sim.personas.schema import Persona
from sim.spend import SpendRefused
from sim.stack import new_phone, usage_of

TEXT_LIMIT = 280
FIRST_REPLY_TARGET_S = 5.0
LATER_REPLY_TARGET_S = 10.0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Latency:
    """Seconds from the Candidate's message (or tap, or Approval) to the agent's first reply.

    `model` is False for replies that code writes without a model call (the STOP confirmation).
    """

    label: str
    seconds: float
    model: bool = True


@dataclass
class FlowResult:
    id: str
    title: str
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    latencies: list[Latency] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    conversations: int = 0
    charge: Charge = field(default_factory=Charge)
    elapsed_s: float = 0.0
    error: str | None = None
    not_run: str | None = None
    cost_unknown: bool = False

    @property
    def status(self) -> str:
        if self.not_run:
            return "not run"
        if self.error:
            return "error"
        return "pass" if self.checks and all(c.ok for c in self.checks) else "fail"

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, bool(ok), detail))
        return bool(ok)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def timed(self, label: str, seconds: float | None, *, model: bool = True) -> None:
        if seconds is not None:
            self.latencies.append(Latency(label, round(seconds, 3), model))

    def record_run(self, run: RunResult, phone: str) -> None:
        """Take a driver run into the result: phone, transcript and reply latencies."""
        self.phones.append(phone)
        self.conversations += 1
        self.transcript += transcript_of(run.entries)
        for turn in run.turns:
            self.timed(f"reply to {turn.sent.purpose}", turn.latency_first_s)


def clip(text: str, limit: int = TEXT_LIMIT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _extras(
    interactive: dict[str, Any] | None,
    links: Sequence[dict[str, Any]],
    attachments: Sequence[dict[str, Any]],
) -> str:
    parts: list[str] = []
    if interactive:
        labels = " | ".join(str(o.get("label")) for o in interactive.get("options", []))
        parts.append(f"{interactive.get('style', 'buttons')}: {labels}")
    parts += [f"link {link.get('url')}" for link in links]
    parts += [f"attachment {a.get('type')} {a.get('filename') or ''}".strip() for a in attachments]
    return f" [{'; '.join(parts)}]" if parts else ""


def transcript_of(entries: Iterable[Entry]) -> list[str]:
    """One line per message of a driver transcript."""
    lines = []
    for e in entries:
        if e.direction == "inbound":
            who = "Candidate"
        else:
            who = {"human": "Team", "system": "System"}.get(e.sender, "Agent")
        tap = " (tapped)" if e.reply_to else ""
        lines.append(f"{who}{tap}: {clip(e.text)}{_extras(e.interactive, e.links, e.attachments)}")
    return lines


def transcript_of_messages(messages: Iterable[dict[str, Any]]) -> list[str]:
    """One line per outbound chat message the flow read itself."""
    return [
        f"Agent: {clip(m.get('text', ''))}"
        f"{_extras(m.get('interactive'), m.get('links') or [], m.get('attachments') or [])}"
        for m in messages
    ]


def record_checks(
    res: FlowResult, persona: Persona, app: dict[str, Any], run: RunResult, ended: str
) -> None:
    """The persona's expectations over the record, one check per kind (no latency: see report)."""
    expect = persona.expect
    res.check(
        f"conversation ran to its end (ended: {run.ended})",
        run.ended == ended,
        f"expected {ended}, the driver ended with {run.ended}",
    )
    groups: list[tuple[str, Sequence[str]]] = [
        (f"stage {expect.stage}, {expect.status}", asserts.check_stage(app, expect)),
        ("answers recorded", asserts.check_fields(app, expect)),
        ("fields collected in order", asserts.check_field_order(app, expect)),
        ("events on the record", asserts.check_events(app, expect)),
        ("tools called (read from events)", asserts.check_tools(app, expect)),
    ]
    if expect.ai_disclosure:
        groups.append(("AI disclosure in the first message", asserts.check_ai_disclosure(app, run)))
    if expect.knockout_template:
        groups.append(
            (
                f"Knockout message in {expect.knockout_template} with the requirements link",
                asserts.check_knockout_template(app, expect),
            )
        )
    groups.append(("Criba never named", asserts.check_forbidden_text(expect, app, run)))
    if expect.final_language or expect.reply_language or expect.reply_languages_follow_latest:
        groups.append(
            (
                "reply language",
                [
                    *asserts.check_language_follows(run, expect),
                    *asserts.check_reply_language(run, expect),
                ],
            )
        )
    if expect.candidate_opted_out:
        groups.append(("Candidate marked Opted out", asserts.check_opt_out(app, expect)))
    for name, failures in groups:
        res.check(name, not failures, "; ".join(failures))
    res.note(
        f"Application {app.get('id')}: stage {app.get('stage')}, status {app.get('status')}, "
        f"events {event_summary(app)}"
    )


def event_summary(app: dict[str, Any]) -> str:
    """The Application's event kinds in order of first appearance, with counts."""
    counts = Counter(asserts.event_kinds(app))
    return ", ".join(kind if n == 1 else f"{kind} x{n}" for kind, n in counts.items())


def _last_id(run: RunResult) -> int:
    return max((e.id or 0 for e in run.entries), default=0)


# ---- the flows -----------------------------------------------------------------------------


async def flow_qualified(stack: LiveStack, res: FlowResult) -> None:
    """1. An English-speaking Candidate completes Screening and is Qualified, with a summary."""
    persona = personas.qualified_english()
    run, phone = await stack.play(persona)
    res.record_run(run, phone)
    app = await stack.application(phone)
    if app.get("stage") == "qualified":
        app = await stack.application(phone, with_summary=True)
    record_checks(res, persona, app, run, "completed")
    prose = summary_prose(app)
    res.check("summary prose written", bool(prose), "the Application has no summary prose")
    res.check(
        "summary prose complete (ends on a full stop)",
        prose.endswith("."),
        f"ends with {prose[-40:]!r}" if prose else "no prose",
    )
    res.check(
        f"summary written by {SUMMARY_MODEL}",
        SUMMARY_MODEL in usage_of(app)["models"],
        f"models served: {', '.join(usage_of(app)['models']) or 'none'}",
    )
    if prose:
        leaks = leaked_codes(prose, ids=(str(app.get("id") or ""),))
        res.check(
            "summary prose is plain words (no stage, field or flag codes, no ids)",
            not leaks,
            f"internal codes in the prose: {leaks}",
        )
        res.check(
            "summary prose carries no communication notes (ADR 0056)",
            not any(h in prose for h in ("Cómo abordarlo", "How to approach"))
            and "communication_notes" not in ((app.get("summary") or {}).get("structured") or {}),
            "the notes are on the Application detail, not in the Summary",
        )
        res.note(f"Summary: {clip(prose, 600)}")


async def flow_knockout_review(stack: LiveStack, res: FlowResult) -> None:
    """2. No licence: a Knockout with the requirements link, then a review request."""
    persona = personas.knockout_then_review()
    run, phone = await stack.play(persona)
    res.record_run(run, phone)
    app = await stack.application(phone)
    record_checks(res, persona, app, run, "knockout")
    items = await stack.queue_items(app["id"], "review")
    res.check(
        "one Review queue item open",
        len(items) == 1,
        f"{len(items)} open Review items for {app['id']}",
    )
    if items:
        res.note(f"Review item {items[0]['id']} (status {items[0]['status']})")
    urls = [u for e in run.agent_entries if e.sender == "agent" for u in URL_RE.findall(e.text)]
    requirements = [u for u in urls if "privacidad" not in u]
    if requirements:
        res.note(f"Requirements link in the Knockout message: {requirements[0]}")


async def flow_cdmx_zone(stack: LiveStack, res: FlowResult) -> None:
    """3. 'Ciudad de México, Cuajimalpa' gets a list of CDMX Zones; the tapped one is recorded."""
    await _city_zone_list(stack, res, personas.cdmx_zone_list())


def _first_reply_to(run: RunResult, purpose: str) -> Entry | None:
    """The first agent message after the Candidate's first message sent for ``purpose``."""
    sent = next((i for i, e in enumerate(run.entries) if e.purpose == purpose), None)
    if sent is None:
        return None
    return next((e for e in run.entries[sent + 1 :] if e.direction == "outbound"), None)


async def flow_cdmx_unknown_place(stack: LiveStack, res: FlowResult) -> None:
    """3b. 'Ciudad de Mexico en Tepito' (demo chat, ADR 0062): the first reply carries the list."""
    await _city_zone_list(stack, res, personas.cdmx_unknown_place_list(), first_reply=True)


async def _city_zone_list(
    stack: LiveStack, res: FlowResult, persona: Persona, *, first_reply: bool = False
) -> None:
    """A city named with a place that is not a Zone: the city's Zones as a list, one tapped.

    With ``first_reply``, the agent's first reply to the Zone answer must carry the list (the
    demo chat's agent said "Del listado..." with none attached, and the Candidate asked which).
    """
    run, phone = await stack.play(persona)
    res.record_run(run, phone)
    app = await stack.application(phone)
    record_checks(res, persona, app, run, "stopped")
    offers = [(e, zone_options(e.interactive)) for e in run.agent_entries if e.interactive]
    offers = [(e, options) for e, options in offers if options]
    res.check("a list of Zones was offered", bool(offers), "no message offered Zone options")
    if first_reply:
        reply = _first_reply_to(run, "answer:zone")
        res.check(
            "the first reply to the Zone answer carries the list",
            reply is not None and bool(zone_options(reply.interactive)),
            f"first reply: {reply.text[:120]!r}" if reply else "no reply to the Zone answer",
        )
    taps = [e for e in run.entries if e.direction == "inbound" and e.reply_to]
    res.check("a Zone was tapped", bool(taps), "the Candidate never tapped a Zone option")
    if offers:
        labels = [str(o.get("label")) for o in offers[0][1]]
        res.check(
            "the list has several CDMX Zones, Polanco among them",
            len(labels) >= 2 and any("polanco" in label.lower() for label in labels),
            f"offered: {', '.join(labels)}",
        )
        res.note(f"Zone list ({len(labels)}): {', '.join(labels)}")
    if taps:
        tapped = str(taps[0].reply_to["option_id"]) if taps[0].reply_to else ""
        zone = (app.get("answers") or {}).get("zone") or {}
        res.check(
            "the tapped Zone is the recorded Zone",
            bool(tapped) and zone.get("id") == tapped,
            f"tapped {tapped}, recorded {zone.get('id')} ({zone.get('name')})",
        )
        res.note(f"Recorded Zone: {zone.get('name')} ({zone.get('city')})")


MX_SERVICE_CITIES = ("Ciudad de México", "Guadalajara", "Monterrey", "Puebla")
FUNNEL_NONE = "city_none"


def _funnel_offers(run: RunResult) -> list[Entry]:
    """The agent messages that carry the Zone funnel's list (ADR 0068): cities and none of these."""
    return [
        e
        for e in run.agent_entries
        if e.interactive
        and any(str(o.get("id")) == FUNNEL_NONE for o in e.interactive.get("options", []))
    ]


def _funnel_checks(res: FlowResult, run: RunResult) -> Entry | None:
    """The checks both funnel flows share: the first agent message links the privacy notice (the
    Candidate opened with a name), and the Zone answer got the cities question with the first
    reply, once, naming exactly the backend's cities. Returns the message that carried it."""
    first = next((e for e in run.agent_entries if e.sender == "agent"), None)
    res.check(
        "the first agent message links the privacy notice",
        first is not None and PRIVACY_URL in first.text,
        f"first message: {clip(first.text) if first else 'none'}",
    )
    offers = _funnel_offers(run)
    res.check(
        "the cities were offered once, as a list ending with none of these",
        len(offers) == 1,
        f"{len(offers)} messages carried the cities list",
    )
    if not offers:
        return None
    offer = offers[0]
    reply = _first_reply_to(run, "answer:zone")
    res.check(
        "the first reply to the Zone answer is the cities question",
        reply is not None and reply.id == offer.id,
        f"first reply: {clip(reply.text) if reply else 'none'}",
    )
    labels = [str(o.get("label")) for o in (offer.interactive or {}).get("options", [])]
    res.check(
        "the list names every city with a Service Area and the question says them",
        labels[:-1] == list(MX_SERVICE_CITIES) and all(c in offer.text for c in MX_SERVICE_CITIES),
        f"list: {', '.join(labels)}; question: {clip(offer.text)}",
    )
    res.check(
        "the agent named no other area",
        not any(w in offer.text.lower() for w in ("spain", "texas", "dallas", "united states")),
        clip(offer.text),
    )
    res.note(f"Cities question: {clip(offer.text)}")
    return offer


async def flow_texas_funnel(stack: LiveStack, res: FlowResult) -> None:
    """3c. The demo chat of 2026-10-12 (ADR 0068): "I live in Texas!" matches no Zone. The cities
    are asked once; "None of these" is the Zone Knockout, from the template, with no Zone stored."""
    persona = personas.texas_funnel_none()
    run, phone = await stack.play(persona)
    res.record_run(run, phone)
    app = await stack.application(phone)
    record_checks(res, persona, app, run, "knockout")
    _funnel_checks(res, run)
    taps = [e for e in run.entries if e.direction == "inbound" and e.reply_to]
    res.check(
        "None of these was tapped",
        bool(taps) and str((taps[0].reply_to or {}).get("option_id")) == FUNNEL_NONE,
        f"taps: {[(t.reply_to or {}).get('option_id') for t in taps]}",
    )
    zone = next((s for s in app.get("answer_status") or [] if s.get("field") == "zone"), {})
    res.check(
        "the Zone is the failed check, no Zone stored, the Candidate's words kept",
        (app.get("answers") or {}).get("zone") is None
        and zone.get("status") == "knockout"
        and "texas" in str(zone.get("raw_input") or "").lower(),
        f"zone answer {(app.get('answers') or {}).get('zone')}, status {zone}",
    )
    last = next((e for e in reversed(run.agent_entries) if e.sender == "agent"), None)
    res.check(
        "the Knockout message was the last reply and asked nothing",
        last is not None and not last.interactive and "?" not in last.text,
        f"last message: {clip(last.text) if last else 'none'}",
    )


async def flow_texas_typed(stack: LiveStack, res: FlowResult) -> None:
    """3d. ADR 0068, the demo chat's second message: after the cities list the Candidate types
    "I live in the united states" instead of tapping. A second out-of-area answer is the same Zone
    Knockout, and the cities are not asked twice."""
    persona = personas.texas_funnel_typed()
    run, phone = await stack.play(persona, max_turns=6)
    res.record_run(run, phone)
    app = await stack.application(phone)
    record_checks(res, persona, app, run, "knockout")
    _funnel_checks(res, run)
    taps = [e for e in run.entries if e.direction == "inbound" and e.reply_to]
    res.check("nothing was tapped, the second place was typed", not taps, f"{len(taps)} taps")
    zone = next((s for s in app.get("answer_status") or [] if s.get("field") == "zone"), {})
    res.check(
        "the Zone is the failed check, no Zone stored, the second place kept",
        (app.get("answers") or {}).get("zone") is None
        and zone.get("status") == "knockout"
        and "united states" in str(zone.get("raw_input") or "").lower(),
        f"zone answer {(app.get('answers') or {}).get('zone')}, status {zone}",
    )


async def flow_dallas_funnel(stack: LiveStack, res: FlowResult) -> None:
    """3d. ADR 0068, the other answer: "I live in Dallas" gets the cities list, the Candidate taps
    Monterrey and gets Monterrey's Zones, and the tapped Zone is recorded. No Knockout."""
    persona = personas.dallas_funnel_city()
    run, phone = await stack.play(persona)
    res.record_run(run, phone)
    app = await stack.application(phone)
    record_checks(res, persona, app, run, "stopped")
    _funnel_checks(res, run)
    taps = [str((e.reply_to or {}).get("option_id")) for e in run.entries if e.reply_to]
    res.check(
        "a city was tapped, then one of its Zones",
        len(taps) == 2
        and taps[0].startswith("city_mx_monterrey")
        and taps[1].startswith("zone_mx_monterrey_"),
        f"taps: {taps}",
    )
    zone = (app.get("answers") or {}).get("zone") or {}
    res.check(
        "the tapped Zone is the recorded Zone",
        len(taps) == 2 and zone.get("id") == taps[1],
        f"tapped {taps[-1:]}, recorded {zone.get('id')} ({zone.get('name')})",
    )
    res.note(f"Recorded Zone: {zone.get('name')} ({zone.get('city')})")


@dataclass
class ApprovedOffer:
    """A Qualified Application the Operator Approved, and the slot offer that came back."""

    app: dict[str, Any]
    conversation: str
    offer: dict[str, Any]  # the message carrying the buttons
    slots: list[dict[str, Any]]


async def _approved_with_offer(stack: LiveStack, res: FlowResult) -> ApprovedOffer | None:
    """Play the booking persona to Qualified, approve it, and read the slot offer. None, with
    the failing check recorded, when any step does not happen."""
    persona = personas.qualified_for_booking()
    run, phone = await stack.play(persona)
    res.record_run(run, phone)
    app = await stack.application(phone)
    record_checks(res, persona, app, run, "completed")
    approvals = await stack.queue_items(app["id"], "approval")
    if not res.check(
        "one Approval queue item open",
        len(approvals) == 1,
        f"{len(approvals)} open Approval items for {app['id']}",
    ):
        return None
    conversation = run.conversation_id
    started = time.perf_counter()
    decided = await stack.approve(approvals[0]["id"])
    if not res.check(
        "Approval accepted by the Operator API", decided.status_code == 200, decided.text[:200]
    ):
        return None
    offered = await stack.read_turn(conversation, _last_id(run), started=started)
    res.transcript += transcript_of_messages(offered.messages)
    res.timed("Approval to slot offer", offered.first_s)
    offer = offered.interactive()
    slots = [o for o in (offer or {}).get("interactive", {}).get("options", []) if _is_slot(o)]
    if not res.check(
        "slot offer arrives with buttons",
        bool(slots),
        "no slot options after the Approval"
        + (f" (messages: {len(offered.messages)})" if not offer else ""),
    ):
        return None
    res.note("Slots offered: " + " | ".join(str(o.get("label")) for o in slots))
    assert offer is not None
    return ApprovedOffer(app, conversation, offer, slots)


async def flow_booking(stack: LiveStack, res: FlowResult) -> None:
    """4. Approve a Qualified Application; the Candidate gets slots, taps one, is booked."""
    approved = await _approved_with_offer(stack, res)
    if approved is None:
        return
    app, conversation, offer = approved.app, approved.conversation, approved.offer
    chosen = approved.slots[0]
    booked = await stack.tap(conversation, int(offer["id"]), str(chosen["id"]))
    res.transcript.append(f"Candidate (tapped): {chosen.get('label')}")
    res.transcript += transcript_of_messages(booked.messages)
    res.timed("slot tap to confirmation", booked.first_s)
    invites = booked.attachments("ics")
    res.check(
        "booking confirmation carries the .ics",
        len(invites) == 1,
        f"{len(invites)} ics attachments in {len(booked.messages)} messages",
    )
    if invites:
        invite = await stack.download(invites[0]["url"])
        res.check(
            "the .ics downloads as a calendar event",
            invite.status_code == 200
            and invite.headers.get("content-type", "").startswith("text/calendar")
            and "BEGIN:VEVENT" in invite.text,
            f"{invite.status_code} {invite.headers.get('content-type')}",
        )
    detail = await stack.detail(app["id"])
    kinds = asserts.event_kinds(detail)
    res.check(
        "Interview booked on the record",
        detail.get("stage") == "interview_booked" and bool(detail.get("interview")),
        f"stage {detail.get('stage')}, interview {detail.get('interview')}",
    )
    res.check(
        "approved, slots offered and booked, in that order",
        _in_order(["approved", "slots_offered", "interview_booked"], kinds),
        f"events: {', '.join(kinds)}",
    )
    if detail.get("interview"):
        res.note(f"Interview: {detail['interview']}")


OTHER_TIME_TAPS = 3
OTHER_OPTION_ID = "other"


async def flow_other_time(stack: LiveStack, res: FlowResult) -> None:
    """4b. After Approval the Candidate taps "Otro horario" three times: every reply carries a
    booking link the booking page accepts and keeps the slot buttons, and a slot tapped last is
    still booked (ADR 0064)."""
    approved = await _approved_with_offer(stack, res)
    if approved is None:
        return
    conversation, offer = approved.conversation, approved.offer
    for n in range(1, OTHER_TIME_TAPS + 1):
        options = (offer.get("interactive") or {}).get("options", [])
        if not res.check(
            f"tap {n}: the buttons include the other-time option",
            any(o.get("id") == OTHER_OPTION_ID for o in options),
            f"options: {[o.get('id') for o in options]}",
        ):
            return
        turn = await stack.tap(conversation, int(offer["id"]), OTHER_OPTION_ID)
        res.transcript.append("Candidate (tapped): other time")
        res.transcript += transcript_of_messages(turn.messages)
        res.timed(f"other-time tap {n} to reply", turn.first_s)
        text = " ".join(str(m.get("text", "")) for m in turn.messages)
        urls = [u for u in URL_RE.findall(text) if "/book/" in u]
        if not res.check(f"tap {n}: the reply carries the booking link", bool(urls), clip(text)):
            return
        token = urls[0].rsplit("/book/", 1)[1].rstrip(".,;")
        page = await stack.download(f"/api/public/booking/{token}")
        res.check(
            f"tap {n}: the booking page accepts the link and lists slots",
            page.status_code == 200 and bool(page.json().get("slots")),
            f"{page.status_code} {page.text[:120]}",
        )
        next_offer = turn.interactive()
        slots = [o for o in (next_offer or {}).get("interactive", {}).get("options", [])]
        if not res.check(
            f"tap {n}: the slot buttons are still offered",
            any(_is_slot(o) for o in slots),
            f"options after the link: {[o.get('id') for o in slots]}",
        ):
            return
        assert next_offer is not None
        offer = next_offer
    chosen = next(o for o in offer["interactive"]["options"] if _is_slot(o))
    booked = await stack.tap(conversation, int(offer["id"]), str(chosen["id"]))
    res.transcript.append(f"Candidate (tapped): {chosen.get('label')}")
    res.transcript += transcript_of_messages(booked.messages)
    detail = await stack.detail(approved.app["id"])
    res.check(
        "a slot tapped after the link is booked",
        detail.get("stage") == "interview_booked" and bool(detail.get("interview")),
        f"stage {detail.get('stage')}, interview {detail.get('interview')}",
    )


def _in_order(wanted: Sequence[str], seen: Sequence[str]) -> bool:
    """True when `wanted` appears in `seen` in that relative order (others may interleave)."""
    it = iter(seen)
    return all(kind in it for kind in wanted)


def _is_slot(option: dict[str, Any]) -> bool:
    return str(option.get("id", "")).startswith("so_")


async def flow_stop(stack: LiveStack, res: FlowResult) -> None:
    """5. STOP: the Candidate is Opted out and told so; a STOP with no Application opens none."""
    confirmations = set(OPT_OUT_CONFIRMATION.values())

    # (a) mid-conversation: one real exchange, then STOP.
    phone = new_phone("ES")
    res.phones.append(phone)
    conversation, cursor = await stack.start("ES", "es-ES", phone)
    res.conversations += 1
    greeted = await stack.say(conversation, "Hola, vi vuestro anuncio de repartidor", cursor)
    res.transcript.append("Candidate: Hola, vi vuestro anuncio de repartidor")
    res.transcript += transcript_of_messages(greeted.messages)
    res.timed("reply to the opening", greeted.first_s)
    res.check(
        "the agent greeted and disclosed it is an AI",
        bool(greeted.messages) and has_ai_disclosure(greeted.messages[0]["text"]),
        "no greeting" if not greeted.messages else clip(greeted.messages[0]["text"]),
    )
    stopped = await stack.say(conversation, "STOP", greeted.cursor)
    res.transcript.append("Candidate: STOP")
    res.transcript += transcript_of_messages(stopped.messages)
    res.timed("reply to STOP", stopped.first_s, model=False)
    texts = [m["text"] for m in stopped.messages]
    res.check(
        "STOP confirmed once, in the fixed copy",
        len(texts) == 1 and texts[0] in confirmations,
        f"replies: {texts!r}",
    )
    app = await stack.application(phone)
    kinds = asserts.event_kinds(app)
    res.check(
        "Candidate marked Opted out",
        bool(app["candidate"]["opted_out"]) and "opted_out" in kinds,
        f"opted_out {app['candidate']['opted_out']}, events {', '.join(kinds)}",
    )
    res.check(
        "the open Application stays as it is (not withdrawn, no nudge)",
        app["status"] == "open" and not {"withdrawn", "nudge_sent"} & set(kinds),
        f"status {app['status']}, events {', '.join(kinds)}",
    )
    listed = await stack.applications_for(phone)
    res.check("STOP created no second Application", len(listed) == 1, f"{len(listed)} listed")

    # (b) STOP as the very first message: no Application is created, and it is still confirmed.
    cold_phone = new_phone("MX")
    res.phones.append(cold_phone)
    cold, cold_cursor = await stack.start("MX", "es-MX", cold_phone)
    res.conversations += 1
    answered = await stack.say(cold, "STOP", cold_cursor)
    res.transcript.append("Candidate (new conversation): STOP")
    res.transcript += transcript_of_messages(answered.messages)
    res.timed("reply to a first-message STOP", answered.first_s, model=False)
    cold_texts = [m["text"] for m in answered.messages]
    res.check(
        "a first-message STOP is confirmed",
        len(cold_texts) == 1 and cold_texts[0] in confirmations,
        f"replies: {cold_texts!r}",
    )
    created = await stack.applications_for(cold_phone)
    res.check("a first-message STOP created no Application", not created, f"{len(created)} listed")


async def flow_language(stack: LiveStack, res: FlowResult) -> None:
    """6. An English opener on a Mexican web chat gets an English reply; one Spanish word later
    does not switch it back (the Jev router decides the language, ADR 0051)."""
    phone = new_phone("MX")
    res.phones.append(phone)
    conversation, cursor = await stack.start("MX", "es-MX", phone)
    res.conversations += 1
    opener = "Hello, I'm looking for a delivery job"
    greeted = await stack.say(conversation, opener, cursor)
    res.transcript.append(f"Candidate: {opener}")
    res.transcript += transcript_of_messages(greeted.messages)
    res.timed("reply to the English opener", greeted.first_s)
    first = greeted.messages[0]["text"] if greeted.messages else ""
    res.check(
        "the reply to an English opener is in English",
        guess_language(first) == "en",
        clip(first) or "no reply",
    )
    app = await stack.application(phone)
    res.check(
        "the Application language is en",
        (app.get("candidate") or {}).get("language") == "en",
        f"language {(app.get('candidate') or {}).get('language')}",
    )
    res.check(
        "the Jev router served the call (a jev row in the ledger)",
        any(m.startswith("typesafe/jev") for m in usage_of(app)["models"]),
        f"models served: {', '.join(usage_of(app)['models']) or 'none'}",
    )
    word = "Mañana"
    answered = await stack.say(conversation, word, greeted.cursor)
    res.transcript.append(f"Candidate: {word}")
    res.transcript += transcript_of_messages(answered.messages)
    res.timed("reply to one Spanish word", answered.first_s)
    reply = answered.messages[0]["text"] if answered.messages else ""
    res.check(
        "one Spanish word does not switch the reply language",
        guess_language(reply) != "es",
        clip(reply) or "no reply",
    )
    after = await stack.application(phone)
    res.check(
        "the Application language is still en",
        (after.get("candidate") or {}).get("language") == "en",
        f"language {(after.get('candidate') or {}).get('language')}",
    )


PERSON_WORDS = re.compile(r"equipo|persona|alguien|humano|reclutador|disculp|perd[oó]n|lo siento")


async def flow_frustrated(stack: LiveStack, res: FlowResult) -> None:
    """7. A frustrated Candidate asking for a person gets a tone-adjusted reply and an
    Escalation, with codes only on the record (ADR 0029, 0035, 0051)."""
    phone = new_phone("MX")
    res.phones.append(phone)
    conversation, cursor = await stack.start("MX", "es-MX", phone)
    res.conversations += 1
    greeted = await stack.say(conversation, "Hola, busco chamba de repartidor", cursor)
    res.transcript.append("Candidate: Hola, busco chamba de repartidor")
    res.transcript += transcript_of_messages(greeted.messages)
    message = "ya te dije que cualquier dia!! quiero hablar con una persona"
    turn = await stack.say(conversation, message, greeted.cursor)
    res.transcript.append(f"Candidate: {message}")
    res.transcript += transcript_of_messages(turn.messages)
    res.timed("reply to a frustrated request for a person", turn.first_s)
    reply = " ".join(m["text"] for m in turn.messages)
    res.check(
        "the reply answers the request (the team, a person, an apology)",
        bool(PERSON_WORDS.search(reply.lower())),
        clip(reply) or "no reply",
    )
    app = await stack.application(phone)
    detail = await stack.detail(app["id"])
    items = [(i.get("kind"), i.get("reason")) for i in detail.get("open_queue_items") or []]
    res.check(
        "an Escalation is open for a person (reason wants_human)",
        ("escalation", "wants_human") in items,
        f"open items: {items}",
    )
    hints = [e for e in detail.get("events") or [] if e.get("kind") == "tone_hint"]
    res.check(
        "a tone hint is on the record with codes only",
        bool(hints) and set(hints[-1].get("data") or {}) <= {"code", "signals", "message_id"},
        f"tone_hint data: {[h.get('data') for h in hints]}",
    )
    res.check(
        "the message is flagged for the Recruiter, as a flag with no score",
        any("wants_human" in s.get("fired", []) for s in detail.get("message_signals") or []),
        f"message_signals: {detail.get('message_signals')}",
    )
    kinds = [n.get("kind") for n in detail.get("communication_notes") or []]
    res.check(
        "the communication notes say a person was asked for",
        "asked_for_person" in kinds,
        f"notes: {kinds}",
    )
    events = set(asserts.event_kinds(detail))
    res.check(
        "nothing about the Candidate was decided (still open, no Knockout or Rejection)",
        detail.get("status") == "open" and not {"knockout", "rejection"} & events,
        f"status {detail.get('status')}, events {', '.join(asserts.event_kinds(detail))}",
    )
    for note in detail.get("communication_notes") or []:
        res.note(f"Note: {note.get('text')}")


def _field_events(app: dict[str, Any], kind: str, field_name: str) -> list[dict[str, Any]]:
    return [
        e
        for e in asserts.events_of(app)
        if e["kind"] == kind and (e.get("data") or {}).get("field") == field_name
    ]


def _offers(run: RunResult, test: Callable[[list[str]], bool]) -> list[tuple[Entry, list[str]]]:
    """Agent messages whose options' ids pass `test`, with those ids."""
    found = []
    for e in run.agent_entries:
        ids = [str(o.get("id")) for o in (e.interactive or {}).get("options") or []]
        if ids and test(ids):
            found.append((e, ids))
    return found


async def flow_no_app(stack: LiveStack, res: FlowResult) -> None:
    """8. Years of delivery on their own, then "no app" (es-MX, es-ES, en): recorded as the
    empty list on the first answer, no miss, and the question offered a "no app" row (ADR 0053).
    The demo chat of 2026-10-10 asked three times."""
    for language in personas.NO_APP_ANSWERS:
        persona = personas.no_app(language)
        run, phone = await stack.play(persona)
        res.record_run(run, phone)
        app = await stack.application(phone)
        record_checks(res, persona, app, run, "stopped")
        platforms = (app.get("answers") or {}).get("experience_platforms")
        res.check(f"{language}: no app is recorded as []", platforms == [], f"recorded {platforms}")
        misses = _field_events(app, "answer_invalid", "experience_platforms")
        res.check(f"{language}: no miss on the platforms", not misses, f"{len(misses)} misses")
        rows = _offers(run, lambda ids: "none" in ids)
        res.check(
            f"{language}: the platforms question offered the no-app row",
            bool(rows),
            "no message carried the none option",
        )


def expected_start(reading: personas.DateReading, today: date) -> date:
    """The date a fair reading of the phrase gives, from the Candidate's local today."""
    if reading.days is not None:
        return today + timedelta(days=reading.days)
    if reading.month_day is not None:
        month, day = reading.month_day
        that = date(today.year, month, day)
        return that if that >= today else date(today.year + 1, month, day)
    assert reading.weekday is not None
    ahead = (reading.weekday - today.weekday()) % 7 or 7
    return today + timedelta(days=ahead + 7 * reading.weeks)


def local_today(country: str) -> date:
    zone = "America/Mexico_City" if country == "MX" else "Europe/Madrid"
    return datetime.now(ZoneInfo(zone)).date()


def _date_ids(ids: list[str]) -> bool:
    return all(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", i) for i in ids)


def date_checks(
    res: FlowResult,
    reading: personas.DateReading,
    app: dict[str, Any],
    run: RunResult,
    today: date,
) -> None:
    """ADR 0054: an ambiguous phrase gets both dates as buttons and the tap is recorded; an
    unambiguous one is recorded with no question."""
    key = reading.key
    first = expected_start(reading, today)
    recorded = (app.get("answers") or {}).get("start_date")
    offers = _offers(run, _date_ids)
    misses = _field_events(app, "answer_invalid", "start_date")
    res.check(f"{key}: no miss on the start date", not misses, f"{len(misses)} misses")
    if not reading.ambiguous:
        res.check(f"{key}: no date choice asked", not offers, f"offered {offers[:1]}")
        res.check(f"{key}: recorded {first}", recorded == first.isoformat(), f"recorded {recorded}")
        return
    both = [first.isoformat(), (first + timedelta(days=7)).isoformat()]
    offered = offers[0][1] if offers else []
    res.check(f"{key}: both dates offered as buttons", offered == both, f"offered {offered}")
    want = both[reading.pick]
    res.check(f"{key}: the tapped date is recorded", recorded == want, f"recorded {recorded}")


async def flow_start_date(stack: LiveStack, res: FlowResult, *, ambiguous: bool) -> None:
    for reading in personas.DATE_READINGS:
        if reading.ambiguous != ambiguous:
            continue
        persona = personas.start_date_reading(reading)
        run, phone = await stack.play(persona)
        res.record_run(run, phone)
        app = await stack.application(phone)
        record_checks(res, persona, app, run, "stopped")
        today = local_today(persona.identity.country)
        date_checks(res, reading, app, run, today)
        res.note(
            f"{reading.key} ({today}): recorded {(app.get('answers') or {}).get('start_date')}"
        )


async def flow_start_date_choice(stack: LiveStack, res: FlowResult) -> None:
    """9. "El próximo domingo" (es-MX) and "next Sunday" (en): both Sundays come back as
    buttons and the tapped one is recorded (ADR 0054); the demo recorded tomorrow."""
    await flow_start_date(stack, res, ambiguous=True)


async def flow_start_date_plain(stack: LiveStack, res: FlowResult) -> None:
    """10. "Este domingo", "el domingo de la próxima semana", "mañana", "tomorrow works for me",
    "el lunes" and "20 de octubre" are recorded with no extra question (ADR 0054)."""
    await flow_start_date(stack, res, ambiguous=False)


async def flow_zone_country(stack: LiveStack, res: FlowResult) -> None:
    """11. The demo chat of 2026-10-10: English on an MX chat, "I live in Chamberi", "tomorrow
    works for me". The Application's country follows the Zone to Spain and English stays
    (ADR 0057); tomorrow is recorded on the first answer, with no question."""
    persona = personas.demo_zone_country()
    run, phone = await stack.play(persona)
    res.record_run(run, phone)
    app = await stack.application(phone)
    record_checks(res, persona, app, run, "stopped")
    today = local_today("ES")
    recorded = (app.get("answers") or {}).get("start_date")
    tomorrow = (today + timedelta(days=1)).isoformat()
    res.check(f"tomorrow recorded ({tomorrow})", recorded == tomorrow, f"recorded {recorded}")
    given = [e for e in run.entries if e.purpose == "answer:start_date"]
    res.check("the start date was given once", len(given) == 1, f"given {len(given)} times")
    misses = _field_events(app, "answer_invalid", "start_date")
    res.check("no miss on the start date", not misses, f"{len(misses)} misses")
    res.note(f"country {asserts.app_country(app)}, start date {recorded} ({today} in Madrid)")


LANGUAGE_REQUEST = "me preguntas en inglés porfi"
_BEFORE_AVAILABILITY = ("Me llamo Ramiro Salcedo", "Vivo en Chamberí, Madrid", "Sí, tengo carnet")


def _option_labels(message: dict[str, Any] | None) -> dict[str, str]:
    options = ((message or {}).get("interactive") or {}).get("options") or []
    return {str(o.get("id")): str(o.get("label")) for o in options}


async def flow_language_request(stack: LiveStack, res: FlowResult) -> None:
    """12. The demo chat of 2026-10-11: a Spanish Candidate on a web chat for Spain asks, in
    Spanish, for the questions in English ("me preguntas en inglés porfi"). Jev read the language
    the message was written IN, so the record stayed es-ES while the reply was English and the
    buttons Spanish. The request decides (ADR 0061): the next question, its buttons and the
    stored language are English on that turn, and a tap on an English button keeps them so."""
    phone = new_phone("ES")
    res.phones.append(phone)
    conversation, cursor = await stack.start("ES", "es-ES", phone)
    res.conversations += 1

    async def say(text: str, label: str) -> Turn:
        nonlocal cursor
        turn = await stack.say(conversation, text, cursor)
        cursor = turn.cursor
        res.transcript.append(f"Candidate: {text}")
        res.transcript += transcript_of_messages(turn.messages)
        res.timed(label, turn.first_s)
        return turn

    turn = await say("Hola, busco trabajo de repartidor", "reply to the Spanish opener")
    pending = list(_BEFORE_AVAILABILITY)
    while "full_time" not in _option_labels(turn.interactive()) and pending:
        turn = await say(pending.pop(0), "reply before the availability question")
    labels = _option_labels(turn.interactive())
    if not res.check(
        "the availability question comes with Spanish buttons first",
        labels.get("full_time", "").lower().startswith("jornada"),
        f"buttons {labels or 'none'}",
    ):
        return
    before = await stack.application(phone)
    res.check(
        "the Application language is es-ES before the request",
        (before.get("candidate") or {}).get("language") == "es-ES",
        f"language {(before.get('candidate') or {}).get('language')}",
    )

    asked = await say(LANGUAGE_REQUEST, "reply to the language request")
    reply = asked.messages[0]["text"] if asked.messages else ""
    res.check(
        "the reply to the request is in English",
        guess_language(reply) == "en",
        clip(reply) or "no reply",
    )
    after = await stack.application(phone)
    res.check(
        "the Application language is en (the request, not the language it is written in)",
        (after.get("candidate") or {}).get("language") == "en",
        f"language {(after.get('candidate') or {}).get('language')}",
    )
    offer = asked.interactive()
    labels = _option_labels(offer)
    res.check(
        "the buttons on that turn are English, as the reply and the record",
        labels.get("full_time") == "Full-time",
        f"buttons {labels or 'none'}",
    )
    res.check(
        "the Jev router served the calls (jev rows in the ledger)",
        any(m.startswith("typesafe/jev") for m in usage_of(after)["models"]),
        f"models served: {', '.join(usage_of(after)['models']) or 'none'}",
    )
    if not offer or "full_time" not in labels:
        return
    tapped = await stack.tap(conversation, int(offer["id"]), "full_time")
    res.transcript.append(f"Candidate (tapped): {labels['full_time']}")
    res.transcript += transcript_of_messages(tapped.messages)
    res.timed("tap on an English button", tapped.first_s)
    next_reply = tapped.messages[0]["text"] if tapped.messages else ""
    res.check(
        "the next question is in English",
        guess_language(next_reply) != "es",
        clip(next_reply) or "no reply",
    )
    last = await stack.application(phone)
    res.check(
        "the Application language is still en",
        (last.get("candidate") or {}).get("language") == "en",
        f"language {(last.get('candidate') or {}).get('language')}",
    )
    recorded = (last.get("answers") or {}).get("availability")
    res.check(
        "the tapped availability is recorded",
        recorded == ["full_time"] or recorded == "full_time",
        f"recorded {recorded}",
    )


# ---- running them --------------------------------------------------------------------------

FlowFn = Callable[[LiveStack, FlowResult], Awaitable[None]]

# The nonsense of the 2026-10-11 demo chat (ADR 0063).
NONSENSE = ("Blok loka ksks aaa", "asd as sssskkk", "aksda asdas asassjqw weedyas")
# How long the flow waits to be sure no reply comes while the Recruiters have the chat.
HOLD_WAIT_S = 12.0


async def flow_unreadable(stack: LiveStack, res: FlowResult) -> None:
    """12. Three unreadable messages at the name question: each counts as a miss, and the third
    hands the chat to the Recruiters in code, with a fixed message and no agent reply; a fourth
    message gets no reply at all (ADR 0063)."""
    phone = new_phone("MX")
    res.phones.append(phone)
    conversation, cursor = await stack.start("MX", "es-MX", phone)
    res.conversations += 1
    greeted = await stack.say(conversation, "Hola", cursor)
    res.transcript.append("Candidate: Hola")
    res.transcript += transcript_of_messages(greeted.messages)
    cursor = greeted.cursor
    last: list[dict[str, Any]] = []
    for text in NONSENSE:
        turn = await stack.say(conversation, text, cursor)
        res.transcript.append(f"Candidate: {text}")
        res.transcript += transcript_of_messages(turn.messages)
        res.timed("reply to an unreadable message", turn.first_s)
        cursor, last = turn.cursor, turn.messages
    texts = [m.get("text") for m in last]
    res.check(
        "the third unreadable message gets the fixed message, and nothing from the agent",
        texts == [UNREADABLE_HANDOVER["es-MX"]],
        "; ".join(clip(t or "") for t in texts) or "no reply",
    )
    app = await stack.application(phone)
    detail = await stack.detail(app["id"])
    items = [(i.get("kind"), i.get("reason")) for i in detail.get("open_queue_items") or []]
    res.check(
        "an unreadable Escalation is open",
        ("escalation", "unreadable") in items,
        f"open items: {items}",
    )
    misses = [e for e in detail.get("events") or [] if e.get("kind") == "answer_invalid"]
    reasons = [(e.get("data") or {}).get("reason") for e in misses]
    res.check(
        "each unreadable message counted once as a miss",
        len(misses) == 3 and reasons.count("unreadable") == 3,
        f"answer_invalid reasons: {reasons}",
    )
    started = time.perf_counter()
    await stack.client.send(conversation, "hola?? me entiendes")
    res.transcript.append("Candidate: hola?? me entiendes")
    quiet = await stack.read_turn(conversation, cursor, started=started, timeout_s=HOLD_WAIT_S)
    res.check(
        "a message while the Recruiters have the chat gets no reply",
        not quiet.messages,
        "; ".join(clip(m.get("text") or "") for m in quiet.messages) or "no reply",
    )
    after = await stack.detail(app["id"])
    events = set(asserts.event_kinds(after))
    res.check(
        "the Application stays open with nothing decided or recorded",
        after.get("status") == "open"
        and after.get("control") == "agent"
        and not {"knockout", "rejection", "answer_recorded"} & events,
        f"status {after.get('status')}, control {after.get('control')}, events "
        f"{', '.join(asserts.event_kinds(after))}",
    )


# Spain asks for the barrio, Mexico for the colonia (the Screener skill's Language rule). The
# demo chat of 2026-10-11 asked a Spain Candidate "¿En qué ciudad y colonia vives?".
ZONE_WORDING: dict[str, tuple[str, str, str]] = {
    "ES": ("es-ES", "Lucía Romero", "barrio"),
    "MX": ("es-MX", "Ramiro Rodríguez", "colonia"),
}


async def _zone_wording(stack: LiveStack, res: FlowResult, country: str) -> None:
    """Open a chat, give the name, and read the Zone question the agent asks next."""
    locale, name, word = ZONE_WORDING[country]
    phone = new_phone(country)
    res.phones.append(phone)
    conversation, cursor = await stack.start(country, locale, phone)
    res.conversations += 1
    opening = "Hola, quiero trabajar de repartidor"
    opened = await stack.say(conversation, opening, cursor)
    res.transcript.append(f"Candidate: {opening}")
    res.transcript += transcript_of_messages(opened.messages)
    res.timed("reply to the opening", opened.first_s)
    asked = await stack.say(conversation, name, opened.cursor)
    res.transcript.append(f"Candidate: {name}")
    res.transcript += transcript_of_messages(asked.messages)
    res.timed("reply to the name", asked.first_s)
    text = " ".join(str(m.get("text", "")) for m in asked.messages)
    res.note(f"{country} Zone question: {text}")
    res.check(f"the {country} Zone question says {word}", word in text.lower(), clip(text))
    if country == "ES":
        res.check("the Spain Zone question never says colonia", "colonia" not in text.lower())


async def flow_zone_wording_es(stack: LiveStack, res: FlowResult) -> None:
    """12. A Spain chat (es-ES): the Zone question says barrio, never the Mexican colonia."""
    await _zone_wording(stack, res, "ES")


async def flow_zone_wording_mx(stack: LiveStack, res: FlowResult) -> None:
    """12b. An MX chat (es-MX): the Zone question keeps colonia."""
    await _zone_wording(stack, res, "MX")


@dataclass(frozen=True)
class FlowSpec:
    id: str
    title: str
    run: FlowFn


FLOWS: tuple[FlowSpec, ...] = (
    FlowSpec(
        "qualified", "Qualified: an English-speaking Candidate completes Screening", flow_qualified
    ),
    FlowSpec(
        "knockout_review",
        "Knockout, then a Review request (no licence)",
        flow_knockout_review,
    ),
    FlowSpec(
        "cdmx_zone",
        "CDMX Zone list: Cuajimalpa gets the city's Zones, one is tapped",
        flow_cdmx_zone,
    ),
    FlowSpec(
        "cdmx_unknown_place",
        "Demo chat: 'Ciudad de Mexico en Tepito' gets the city's Zones with the first reply",
        flow_cdmx_unknown_place,
    ),
    FlowSpec(
        "zone_funnel_none",
        "Demo chat: 'I live in Texas!' gets the cities list; None of these is the Zone Knockout",
        flow_texas_funnel,
    ),
    FlowSpec(
        "zone_funnel_typed",
        "After the cities list a second place that matches no Zone, typed, is the Zone Knockout",
        flow_texas_typed,
    ),
    FlowSpec(
        "zone_funnel_city",
        "A place with no Zone gets the cities list; a city leads to its Zones, one is tapped",
        flow_dallas_funnel,
    ),
    FlowSpec("booking", "Approval, slot offer, booked Interview with .ics", flow_booking),
    FlowSpec(
        "other_time",
        "Otro horario: three taps each get the booking link, and a slot still books",
        flow_other_time,
    ),
    FlowSpec("stop", "STOP: opt-out confirmed, no Application created", flow_stop),
    FlowSpec(
        "language",
        "Language: an English opener gets English, one Spanish word does not switch it",
        flow_language,
    ),
    FlowSpec(
        "frustrated",
        "A frustrated Candidate asks for a person: tone-adjusted reply and an Escalation",
        flow_frustrated,
    ),
    FlowSpec("no_app", "No app: delivering on their own is [] on the first answer", flow_no_app),
    FlowSpec(
        "start_date_choice",
        "Next Sunday: both Sundays as buttons, the tapped one recorded",
        flow_start_date_choice,
    ),
    FlowSpec(
        "start_date_plain",
        "Unambiguous start dates are recorded with no extra question",
        flow_start_date_plain,
    ),
    FlowSpec(
        "zone_country",
        "Demo chat: an MX chat living in Chamberí moves to Spain; tomorrow is recorded",
        flow_zone_country,
    ),
    FlowSpec(
        "language_request",
        'Language request: "me preguntas en inglés porfi" gives English reply, buttons and record',
        flow_language_request,
    ),
    FlowSpec(
        "unreadable",
        "Demo chat: three unreadable messages hand the chat to the Recruiters",
        flow_unreadable,
    ),
    FlowSpec(
        "zone_wording_es",
        "Spain chat: the Zone question says barrio, never colonia",
        flow_zone_wording_es,
    ),
    FlowSpec(
        "zone_wording_mx",
        "MX chat: the Zone question keeps colonia",
        flow_zone_wording_mx,
    ),
)


def select(ids: Sequence[str] | None) -> list[FlowSpec]:
    """The flows named by `ids` (in the order given), or all of them."""
    if not ids:
        return list(FLOWS)
    by_id = {f.id: f for f in FLOWS}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise ValueError(f"unknown flow {', '.join(unknown)}; choose from {', '.join(by_id)}")
    return [by_id[i] for i in ids]


async def run_flow(stack: LiveStack, spec: FlowSpec) -> FlowResult:
    """Run one flow, then charge what it spent. Never raises except `SpendRefused`."""
    res = FlowResult(spec.id, spec.title)
    t0 = time.perf_counter()
    try:
        await spec.run(stack, res)
    except SpendRefused:
        raise
    except Exception as exc:  # a flow's failure is a result, not a crash
        res.error = f"{type(exc).__name__}: {exc}"[:400]
    res.elapsed_s = round(time.perf_counter() - t0, 3)
    await _charge(stack, res)
    return res


async def _charge(stack: LiveStack, res: FlowResult) -> None:
    """Count the flow's Applications against the cap and add up their cost."""
    total = Charge()
    for phone in res.phones:
        try:
            app = await stack.application(phone)
        except LookupError:
            continue  # no Application, so no usage (a STOP before any message)
        except Exception as exc:
            res.cost_unknown = True
            res.note(f"cost of {phone} unknown: {type(exc).__name__}: {exc}"[:200])
            continue
        one = stack.charge(app)
        total.cost_usd += one.cost_usd
        total.billed_usd += one.billed_usd
        total.guard_usd += one.guard_usd
        total.runs += one.runs
        total.models = sorted({*total.models, *one.models})
        for key, value in one.tokens.items():
            total.tokens[key] = total.tokens.get(key, 0) + value
    res.charge = total
    if total.runs:
        res.check(
            f"turns served by {stack.model}",
            stack.model in total.models,
            f"models served: {', '.join(total.models) or 'none'}",
        )


__all__ = [
    "FLOWS",
    "Check",
    "FlowResult",
    "FlowSpec",
    "Latency",
    "record_checks",
    "run_flow",
    "select",
    "transcript_of",
    "transcript_of_messages",
]
