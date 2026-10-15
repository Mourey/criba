"""Voice personas end to end on the sim stack (voice-agent sim-personas SM5, SM6, SM7; R24.2).

Each voice persona (`src/tests/sim/personas/voice/`) is played through the custom-LLM endpoint
by `sim.voice_driver.VoiceDriver`, as ElevenLabs would call it, and must reach its typed
counterpart's outcome. No real model and no ElevenLabs call: sim mode runs the stub model and
the fake ElevenLabs client. Collected only with ``CRIBA_E2E_BASE_URL`` (marker ``sim_stack``,
deselected otherwise, as `test_demo.py`); on a stack, a missing secret file variable fails with
the reason::

    CRIBA_WEB_PORT=18097 docker compose -p criba-e2e-voicepersonas \\
        -f docker-compose.yml -f docker-compose.sim.yml up -d --build --wait
    CRIBA_E2E_BASE_URL=http://localhost:18097 \\
    CRIBA_E2E_SEED_PASSWORD_FILE=secrets/criba_seed_password.txt \\
    CRIBA_E2E_VOICE_LLM_SECRET_FILE=secrets/criba_voice_llm_secret.txt \\
        uv run --project src/backend pytest src/tests/e2e -q -m e2e

The sim clock is global to the backend, so personas run one at a time, each with a fresh phone,
and reset the clock first. Outcomes are compared on `OUTCOME_KEYS` only: a Knockout persona
whose typed counterpart goes on past the Knockout (a dispute, a Reopen) is compared with that
typed persona played on the same stack without its interrupts (plan risk 2).
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from criba.channel.copy import SPOKEN_REASK
from sim import asserts
from sim.clock import CLOCK_PATH
from sim.driver import PersonaDriver
from sim.personas.schema import FIELD_ORDER, Persona
from sim.personas.voice import (
    OUTCOME_KEYS,
    VoicePersona,
    load_voice_personas,
    outcome_expect,
    typed_of,
)
from sim.stack import OPERATOR, new_phone, seed_password
from sim.voice_driver import StreamedTurn, VoiceDriver, VoiceRun, VoiceUnavailableError

pytestmark = [pytest.mark.e2e, pytest.mark.sim_stack]

BASE_URL = os.environ.get("CRIBA_E2E_BASE_URL", "")
SECRET_VARIABLE = "CRIBA_E2E_VOICE_LLM_SECRET_FILE"
VOICE_VARIABLES = (
    "CRIBA_VOICE",
    "CRIBA_VOICE_MAX_CONCURRENT",
    "CRIBA_VOICE_AGENT_ES",
    "CRIBA_VOICE_AGENT_MX",
    "CRIBA_VOICE_DAILY_MINUTES",
)
FORBIDDEN_IN_SPEECH = ("http", "www.", "so_")


def _stack_is_sim() -> bool:
    try:
        return httpx.get(f"{BASE_URL}{CLOCK_PATH}", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


PERSONAS = {p.id: p for p in load_voice_personas()}


@pytest.fixture(scope="module")
def llm_secret() -> str:
    """The stack's custom-LLM bearer, after the checks that say why the suite cannot run.

    Without ``CRIBA_E2E_BASE_URL`` the e2e conftest deselects these tests (marker
    ``sim_stack``); with it, a stack that is not in sim mode or a missing variable fails here
    with the reason. The secret is never printed.
    """
    if not _stack_is_sim():
        pytest.fail(
            f"no sim stack at CRIBA_E2E_BASE_URL={BASE_URL}: start one with docker-compose.sim.yml"
        )
    if not os.environ.get("CRIBA_E2E_SEED_PASSWORD_FILE"):
        pytest.fail("set CRIBA_E2E_SEED_PASSWORD_FILE (the stack's seed password)")
    path = os.environ.get(SECRET_VARIABLE)
    if not path:
        pytest.fail(
            f"set {SECRET_VARIABLE} (secrets/criba_voice_llm_secret.txt, the custom-LLM bearer)"
        )
    secret = Path(path).read_text(encoding="utf-8").strip()
    if not secret:
        pytest.fail(f"{SECRET_VARIABLE} names an empty file; the endpoint refuses an empty bearer")
    return secret


# ---- the record, read as the Operator --------------------------------------------------


def _operator() -> httpx.Client:
    http = httpx.Client(base_url=BASE_URL, timeout=15)
    response = http.post("/api/auth/login", json={"email": OPERATOR, "password": seed_password()})
    assert response.status_code == 200, f"Operator login -> {response.status_code}"
    return http


def _application(http: httpx.Client, phone: str) -> dict[str, Any] | None:
    listing = http.get("/api/applications", params={"q": phone, "sort": "-created_at", "limit": 20})
    listing.raise_for_status()
    match = next(
        (i for i in listing.json()["items"] if (i["candidate"].get("phone") or "") == phone),
        None,
    )
    if match is None:
        return None
    detail = http.get(f"/api/applications/{match['id']}")
    detail.raise_for_status()
    body: dict[str, Any] = detail.json()
    body.pop("cost", None)
    return body


def _settled(http: httpx.Client, phone: str, wait_s: float = 10.0) -> dict[str, Any]:
    """The Application once the work after the last turn (the summary) has landed."""
    deadline = time.monotonic() + wait_s
    app = _application(http, phone)
    while time.monotonic() < deadline and (app is None or app.get("summary") is None):
        time.sleep(0.5)
        app = _application(http, phone)
    assert app is not None, f"no Application for {phone}"
    return app


@dataclass
class Snapshot:
    """The record right after one turn."""

    said: str
    streamed: str
    kinds: list[str]
    opted_out: bool


def _snapshotter(http: httpx.Client, phone: str, into: list[Snapshot]) -> Any:
    def after_turn(run: VoiceRun, turn: StreamedTurn) -> None:
        app = _application(http, phone)
        kinds = asserts.event_kinds(app) if app else []
        opted = bool(((app or {}).get("candidate") or {}).get("opted_out"))
        into.append(Snapshot(run.said[-1], turn.text, kinds, opted))

    return after_turn


# ---- outcome checks ----------------------------------------------------------------------


def _report(persona: VoicePersona, run: VoiceRun, app: dict[str, Any] | None) -> str:
    app = app or {}
    lines = [f"{persona.id}: ended={run.ended} stage={app.get('stage')} status={app.get('status')}"]
    for said, turn in zip(run.said, run.turns, strict=False):
        lines.append(f"  candidate: {said[:100]!r}")
        lines.append(f"  agent:     {turn.text[:100]!r}")
    return "\n".join(lines)


def _outcome(app: dict[str, Any]) -> dict[str, Any]:
    """`OUTCOME_KEYS` read from an Application, canonical as `asserts` compares them."""
    answers = app.get("answers") or {}
    return {
        "stage": app.get("stage"),
        "status": app.get("status"),
        "outcome": app.get("outcome"),
        "knockout_criterion": app.get("knockout_criterion"),
        "fields": {
            f: asserts._canonical(answers.get(f))
            for f in FIELD_ORDER
            if answers.get(f) not in (None, "", [])
        },
        "fields_unanswered": sorted(f for f in FIELD_ORDER if answers.get(f) in (None, "", [])),
        "candidate_opted_out": bool((app.get("candidate") or {}).get("opted_out")),
    }


def _twin(typed: Persona) -> Persona:
    """The typed persona without its interrupts, stopping at the Knockout (as
    `test_sim_stack._knockout_without_dispute` does)."""
    expect = typed.expect
    if expect.outcome is None:
        expect = expect.model_copy(update={"outcome": "knockout"})
    return typed.model_copy(update={"interrupts": [], "expect": expect})


async def _play_twin(typed: Persona) -> tuple[str, str]:
    phone = new_phone(typed.identity.country)
    driver = PersonaDriver.for_base_url(BASE_URL, reply_timeout_s=60.0, poll_wait_s=5)
    try:
        run = await driver.run(_twin(typed), phone=phone)
    finally:
        await driver.aclose()
    return phone, run.ended


def _check_outcome(
    persona: VoicePersona, run: VoiceRun, app: dict[str, Any], http: httpx.Client
) -> list[str]:
    typed = typed_of(persona)
    if any(i.at == "after_knockout" for i in typed.interrupts):
        if run.ended != "knockout":
            return [f"the voice run ended {run.ended!r}, not at the Knockout"]
        phone, ended = asyncio.run(_play_twin(typed))
        twin = _settled(http, phone)
        assert ended == "knockout", f"the typed twin ended {ended!r}"
        got, want = _outcome(app), _outcome(twin)
        assert set(got) == set(OUTCOME_KEYS)
        return [
            f"{k}: voice {got[k]!r}, typed {want[k]!r}" for k in OUTCOME_KEYS if got[k] != want[k]
        ]
    expect = outcome_expect(persona)
    failures = [
        *asserts.check_stage(app, expect),
        *asserts.check_fields(app, expect),
        *asserts.check_opt_out(app, expect),
    ]
    kept = set(persona.expect_overrides)
    if kept & {"events", "event_counts", "nudges"}:
        failures += asserts.check_events(app, expect)
    if "tools" in kept:
        failures += asserts.check_tools(app, expect)
    if "knockout_template" in kept:
        failures += asserts.check_knockout_template(app, expect)
    unchecked = kept - {"events", "event_counts", "nudges", "tools", "knockout_template"}
    if unchecked:
        failures.append(f"expect_overrides {sorted(unchecked)} have no voice check here")
    return failures


def _check_modality(app: dict[str, Any], *, from_id: int = 0) -> list[str]:
    """Every Candidate message is spoken, and so is every message stored after one (its
    replies, the re-ask and the opt-out confirmation), up to the next inbound message."""
    failures = []
    previous: dict[str, Any] | None = None
    for message in sorted(app.get("messages") or [], key=lambda m: m["id"]):
        if message["id"] >= from_id:
            if message["direction"] == "inbound" and message["sender"] == "candidate":
                if message.get("modality") != "spoken":
                    failures.append(
                        f"Candidate message {message['id']} is {message.get('modality')}"
                    )
            elif (
                message["direction"] == "outbound"
                and previous is not None
                and previous["sender"] == "candidate"
                and message.get("modality") != "spoken"
            ):
                failures.append(
                    f"reply {message['id']} to spoken message {previous['id']} is "
                    f"{message.get('modality')}"
                )
        if message["direction"] == "inbound":
            previous = message
    return failures


def _check_speech(run: VoiceRun) -> list[str]:
    return [
        f"streamed turn {n} holds {needle!r}"
        for n, turn in enumerate(run.turns)
        for needle in FORBIDDEN_IN_SPEECH
        if needle in turn.text.lower()
    ]


# ---- moments -----------------------------------------------------------------------------


def _check_conflict(
    persona: VoicePersona, run: VoiceRun, snaps: list[Snapshot], app: dict[str, Any]
) -> list[str]:
    conflict = persona.moments.yes_no_conflict
    assert conflict is not None
    failures = []
    reask = SPOKEN_REASK[typed_of(persona).identity.locale]
    if [t.text for t in run.turns].count(reask) != 1:
        failures.append(f"expected one streamed re-ask {reask!r}")
    at = run.said.index(conflict.text)
    before, during, after = snaps[at - 1].kinds, snaps[at].kinds, snaps[at + 1].kinds
    if run.said[at + 1] != conflict.then:
        failures.append(f"after the conflict the Candidate said {run.said[at + 1]!r}")
    if snaps[at].streamed != reask:
        failures.append(f"the conflict drew {snaps[at].streamed!r}, not the re-ask")
    for kind in ("answer_recorded", "answer_ambiguous"):
        if during.count(kind) != before.count(kind):
            failures.append(f"the conflict turn added {kind}")
    if after.count("answer_ambiguous") != during.count("answer_ambiguous") + 1:
        failures.append("the clear answer did not clear the confirmation (one answer_ambiguous)")
    spoken = [
        m
        for m in app.get("messages") or []
        if m["direction"] == "inbound" and m["sender"] == "candidate"
    ]
    clear = [m for m in spoken if m.get("body") == conflict.then]
    if [m.get("reply_option_id") for m in clear] != ["no"]:
        failures.append(
            f"the clear answer was stored as {[m.get('reply_option_id') for m in clear]}"
        )
    zone = (app.get("answers") or {}).get("zone") or {}
    if not str(zone.get("id", "")).startswith("zone_mx_cdmx_"):
        failures.append(f"the Zone was not recorded from the list pick: {zone!r}")
    return failures


def _check_optout(persona: VoicePersona, run: VoiceRun, snaps: list[Snapshot]) -> list[str]:
    optout = persona.moments.optout
    assert optout is not None
    at = run.said.index(optout.phrase)
    failures = []
    if snaps[at].opted_out:
        failures.append("the opt-out phrase opted the Candidate out")
    if run.said[at + 1] != optout.then or not snaps[at + 1].opted_out:
        failures.append(f"{optout.then!r} did not opt the Candidate out")
    return failures


# ---- booking -----------------------------------------------------------------------------


def _approve(http: httpx.Client, app: dict[str, Any]) -> None:
    deadline = time.monotonic() + 15
    item = None
    while item is None and time.monotonic() < deadline:
        queue = http.get("/api/queue", params={"kind": "approval"}).json()
        item = next((i for i in queue["items"] if i["application"]["id"] == app["id"]), None)
        if item is None:
            time.sleep(0.5)
    assert item is not None, f"no approval item for {app['id']}"
    decided = http.post(f"/api/queue/approvals/{item['id']}", json={"decision": "approved"})
    assert decided.status_code == 200, decided.text


def _check_booking(
    driver: VoiceDriver,
    persona: VoicePersona,
    run: VoiceRun,
    app: dict[str, Any],
    http: httpx.Client,
    phone: str,
) -> list[str]:
    _approve(http, app)
    first_new = max([0, *[int(m["id"]) for m in run.messages]]) + 1
    driver.book(run, persona)
    booked = _settled(http, phone)
    failures = []
    if booked.get("stage") != "interview_booked":
        failures.append(f"after booking: stage {booked.get('stage')!r}")
    # A voice call has no written channel: no .ics invite until one exists (ADR 0059's open
    # question); the booking is read back aloud instead.
    invites = [
        a for m in run.messages for a in m.get("attachments") or [] if a.get("type") == "ics"
    ]
    if invites:
        failures.append(f"expected no .ics attachment on a voice call, got {len(invites)}")
    booking_said = next(
        m["id"]
        for m in sorted(booked.get("messages") or [], key=lambda m: m["id"])
        if m["direction"] == "inbound" and m.get("body") == run.said[-1]
    )
    failures += _check_modality(booked, from_id=max(first_new, booking_said))
    return failures


# ---- the test ----------------------------------------------------------------------------


@pytest.mark.parametrize("persona_id", sorted(PERSONAS))
def test_voice_persona_reaches_its_typed_outcome(persona_id: str, llm_secret: str) -> None:
    persona = PERSONAS[persona_id]
    country = typed_of(persona).identity.country
    phone = new_phone(country)
    http = _operator()
    client = httpx.Client(base_url=BASE_URL, timeout=15)
    snaps: list[Snapshot] = []
    driver = VoiceDriver(
        client,
        llm_secret=llm_secret,
        after_turn=_snapshotter(http, phone, snaps),
        poll_wait_s=5,
        reply_timeout_s=60,
    )
    try:
        assert http.post(f"{CLOCK_PATH}/reset").status_code == 200
        try:
            run = driver.play(persona, phone=phone)
        except VoiceUnavailableError as exc:
            pytest.fail(
                f"voice-session -> {exc.status} {exc.code}: voice is off on this sim stack; "
                f"check {', '.join(VOICE_VARIABLES)} and the voice secret files"
            )
        app = _settled(http, phone)
        failures = _check_outcome(persona, run, app, http)
        failures += _check_modality(app)
        failures += _check_speech(run)
        if persona.moments.yes_no_conflict is not None:
            failures += _check_conflict(persona, run, snaps, app)
        if persona.moments.optout is not None:
            failures += _check_optout(persona, run, snaps)
        if not failures and persona.booking is not None:
            failures += _check_booking(driver, persona, run, app, http, phone)
            failures += _check_speech(run)
        assert not failures, f"{failures}\n{_report(persona, run, app)}"
    finally:
        client.close()
        http.close()
