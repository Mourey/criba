"""End to end on the sim stack: personas through the real backend, Hermes and the stub model.

Skipped unless a sim stack is up. Bring one up under its own project name, then point the
tests at its web port and give them the seed password (``secrets/criba_seed_password.txt``)::

    CRIBA_WEB_PORT=18095 docker compose -p criba-e2e \\
        -f docker-compose.yml -f docker-compose.sim.yml up -d --build --wait
    CRIBA_E2E_BASE_URL=http://localhost:18095 \\
    CRIBA_E2E_SEED_PASSWORD_FILE=secrets/criba_seed_password.txt \\
        uv run --project src/backend pytest src/tests/e2e -q -m e2e

No real model is called: sim mode runs the stub model and the deterministic summary. The sim
clock is global to the backend, so these tests run one persona at a time, each with a
fresh phone number, and reset the clock first.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from criba.channel.copy import opt_out_confirmation
from sim import asserts
from sim.driver import PersonaDriver, RunResult
from sim.personas.loader import load_personas
from sim.personas.schema import Persona

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get("CRIBA_E2E_BASE_URL", "")
OPERATOR = "operator@criba.example"


def _seed_password() -> str:
    path = os.environ.get("CRIBA_E2E_SEED_PASSWORD_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get("CRIBA_E2E_SEED_PASSWORD", "")


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


def _phone(country: str) -> str:
    digits = f"{random.randrange(10**8):08d}"
    return f"+346{digits}" if country == "ES" else f"+5255{digits}"


def _persona(persona_id: str) -> Persona:
    return load_personas()[persona_id]


async def _operator(http: httpx.AsyncClient) -> None:
    password = _seed_password()
    if not password:
        pytest.skip("set CRIBA_E2E_SEED_PASSWORD_FILE (the stack's seed password)")
    response = await http.post("/api/auth/login", json={"email": OPERATOR, "password": password})
    assert response.status_code == 200, response.text


async def _play(persona: Persona) -> tuple[RunResult, dict[str, Any], str]:
    phone = _phone(persona.identity.country)
    driver = PersonaDriver.for_base_url(BASE_URL, reply_timeout_s=60.0, poll_wait_s=5)
    try:
        await driver.clock.reset()  # type: ignore[union-attr]
        run = await driver.run(persona, phone=phone)
    finally:
        await driver.aclose()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=15) as http:
        await _operator(http)
        app = await _settled(http, phone)
    return run, app, phone


async def _settled(http: httpx.AsyncClient, phone: str, wait_s: float = 10.0) -> dict[str, Any]:
    """The Application once the background work after the last turn (summary) has landed."""
    deadline = time.monotonic() + wait_s
    app = await asserts.find_application(http, phone)
    while time.monotonic() < deadline and app.get("summary") is None:
        await asyncio.sleep(0.5)
        app = await asserts.find_application(http, phone)
    return app


def _report(run: RunResult, app: dict[str, Any]) -> str:
    lines = [f"ended={run.ended} stage={app.get('stage')} status={app.get('status')}"]
    lines += [f"  {e.sender}: {e.text[:100]!r}" for e in run.entries]
    return "\n".join(lines)


def test_qualified_persona_reaches_qualified_with_a_summary() -> None:
    persona = _persona("qualified_es")
    run, app, _ = asyncio.run(_play(persona))
    failures = asserts.check_persona(persona, app, run)
    assert not failures, f"{failures}\n{_report(run, app)}"
    assert app.get("summary") is not None, "no summary after finish_screening"


def test_no_license_persona_ends_in_a_knockout() -> None:
    persona = _persona("no_license_mx")
    run, app, _ = asyncio.run(_play(persona))
    failures = asserts.check_persona(persona, app, run)
    assert not failures, f"{failures}\n{_report(run, app)}"


def _knockout_without_dispute(zone_text: str) -> Persona:
    """no_license_mx with `zone_text` as its first Zone answer, stopping at the Knockout.

    Leaves out the dispute and the assertions that depend on the gaps above. Keeps the Zone
    clarification the answer causes, if any, and what it expects of it.
    """
    base = _persona("no_license_mx")
    zone = base.answers["zone"][0].model_copy(update={"text": zone_text})
    ambiguous = zone_text.strip().lower() == "puebla"
    expect = base.expect.model_copy(
        update={
            "events": ["application_started", "answer_recorded"]
            + (["answer_ambiguous"] if ambiguous else [])
            + ["knockout"],
            "event_counts": [
                c for c in base.expect.event_counts if ambiguous or c.kind == "knockout"
            ],
            "tools": ["record_answer"],
            # close_application logs screening_finished (result knockout), which the sim
            # infers as a finish_screening call: not forbidden here.
            "forbidden_events": ["nudge_sent", "escalation_raised", "reopened"],
            "forbidden_tools": [],
            # The agent relays the Knockout template's text as its own message (content
            # type text); nothing sends it as a template message (backlog). Checked below
            # from the knockout event instead.
            "knockout_template": None,
        }
    )
    return base.model_copy(
        update={
            "answers": {**base.answers, "zone": [zone, *base.answers["zone"][1:]]},
            "interrupts": [],
            "expect": expect,
        }
    )


async def _play_to_knockout(persona: Persona) -> tuple[RunResult, dict[str, Any]]:
    run, app, _ = await _play(persona)
    failures = asserts.check_persona(persona, app, run)
    assert not failures, f"{failures}\n{_report(run, app)}"
    assert app.get("summary") is not None, "no summary after the Knockout"
    return run, app


def _assert_license_knockout_template(app: dict[str, Any]) -> None:
    knockout = next(e for e in asserts.events_of(app) if e["kind"] == "knockout")
    template = knockout["data"]["template"]  # e.g. knockout_driver_license_pending.es-MX
    assert template.startswith("knockout_driver_license"), knockout
    assert template.endswith(".es-MX"), knockout


def test_no_license_knockout_with_one_zone_and_no_dispute() -> None:
    """no_license_mx answering one Zone ("Centro, Puebla") and stopping at the Knockout.

    The Knockout itself: the Application closes as a driver_license Knockout with the es-MX
    template and a summary.
    """
    _, app = asyncio.run(_play_to_knockout(_knockout_without_dispute("Centro, Puebla")))
    _assert_license_knockout_template(app)
    assert "answer_ambiguous" not in asserts.event_kinds(app)


def test_ambiguous_zone_is_resolved_then_the_license_knocks_out() -> None:
    """ "Puebla" names three Zones: the backend offers them, the driver taps one, screening goes
    on, and the license answer is the Knockout (not the Zone clarification)."""
    run, app = asyncio.run(_play_to_knockout(_knockout_without_dispute("Puebla")))
    assert app["knockout_criterion"] == "driver_license", _report(run, app)
    assert asserts.event_kinds(app).count("answer_ambiguous") == 1
    assert asserts.event_kinds(app).count("knockout") == 1
    tapped = [e for e in run.entries if e.reply_to]
    assert [e.reply_to["option_id"] for e in tapped] == ["zone_mx_puebla_centro"]
    _assert_license_knockout_template(app)


def test_zone_freeform_toluca_confirmation_rejected_then_cdmx_city_list_selection() -> None:
    """zone_freeform_mx (spec 006, ADR 0042): "por la salida a Toluca" finds the uncovered town
    Toluca, which gets the Sí/No confirmation (not the wrong direct Knockout the old parser
    gave); the Candidate taps No, which counts no miss. "Ciudad de México, Cuajimalpa" is
    searched as the city, the backend offers the city's Zones as a list (Q45, no miss), and the
    Candidate taps one."""
    persona = _persona("zone_freeform_mx")
    run, app, _ = asyncio.run(_play(persona))
    failures = asserts.check_persona(persona, app, run)
    assert not failures, f"{failures}\n{_report(run, app)}"
    kinds = asserts.event_kinds(app)
    assert "knockout" not in kinds and app["outcome"] is None, _report(run, app)
    assert kinds.count("answer_invalid") == 0, kinds  # the No tap and the list are no miss
    assert kinds.count("answer_ambiguous") == 3, kinds  # confirmation, its clearing, the list
    tapped = [e.reply_to["option_id"] for e in run.entries if e.reply_to]
    assert len(tapped) == 2 and tapped[1].startswith("zone_mx_cdmx_"), tapped
    assert tapped[0] == "no", tapped  # the Toluca confirmation
    assert app["answers"]["zone"]["id"] == tapped[1]


def test_approval_runs_the_system_turn_and_offers_slots() -> None:
    persona = _persona("qualified_es")
    run, app, _ = asyncio.run(_play(persona))
    assert app["stage"] == "qualified", _report(run, app)

    async def approve_and_read() -> tuple[list[dict[str, Any]], list[str]]:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=15) as http:
            await _operator(http)
            queue = (await http.get("/api/queue", params={"kind": "approval"})).json()
            item = next(i for i in queue["items"] if i["application"]["id"] == app["id"])
            decided = await http.post(
                f"/api/queue/approvals/{item['id']}", json={"decision": "approved"}
            )
            assert decided.status_code == 200, decided.text
            url = f"/api/chat/conversations/{run.conversation_id}/messages"
            after = max(e.id or 0 for e in run.entries)
            deadline = time.monotonic() + 60
            messages: list[dict[str, Any]] = []
            while time.monotonic() < deadline and not messages:
                page = await http.get(url, params={"after": after, "wait": 5})
                messages = page.json()["messages"]
            await asyncio.sleep(1)  # let a trailing message land
            page = await http.get(url, params={"after": after})
            detail = (await http.get(f"/api/applications/{app['id']}")).json()
            return page.json()["messages"], asserts.event_kinds(detail)

    messages, kinds = asyncio.run(approve_and_read())
    # The wiring: Approval -> scheduler -> channel -> system turn -> agent run -> reply.
    assert "approved" in kinds and "system_turn" in kinds, kinds
    assert [m for m in messages if m["sender"] == "agent"], "no agent reply to the system turn"
    assert all("[system]" not in m["text"] for m in messages), messages
    offer = next((m for m in messages if m["interactive"]), None)
    if offer is None or "slots_offered" not in kinds:
        pytest.xfail(
            "the stub screener script has no rule for '[system] approved', so the agent "
            f"never calls offer_interview_slots (reply: {messages[-1]['text']!r})"
        )
    assert any(o["id"].startswith("so_") for o in offer["interactive"]["options"]), offer


def test_stop_opts_out_without_a_run() -> None:
    """Q42: a STOP-only first message marks the Candidate Opted out and confirms once,
    with no agent run and no Application."""

    async def scenario() -> list[dict[str, Any]]:
        phone = _phone("ES")
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=15) as http:
            conv = await http.post(
                "/api/chat/conversations",
                json={
                    "country": "ES",
                    "phone": phone,
                    "locale": "es-ES",
                    "client": "simulator",
                },
            )
            conv_id = conv.json()["conversation_id"]
            sent = await http.post(
                f"/api/chat/conversations/{conv_id}/messages",
                json={"message_id": f"e2e-{phone}-1", "text": "STOP"},
            )
            assert sent.status_code == 202, sent.text
            # Long enough for a run to have answered, had one started.
            await asyncio.sleep(3)
            page = await http.get(f"/api/chat/conversations/{conv_id}/messages")
            await _operator(http)
            with pytest.raises(LookupError):
                await asserts.find_application(http, phone)
            return page.json()["messages"]

    messages = asyncio.run(scenario())
    # One confirmation, from the system and none from the agent: no run started.
    assert [m["direction"] for m in messages] == ["inbound", "outbound"], messages
    assert [m["sender"] for m in messages] == ["candidate", "system"], messages
    # The opt-out confirmation is sent only once the Candidate is marked Opted out; with no
    # Application there is no record the API exposes, so the confirmation is the evidence.
    assert messages[1]["text"] == opt_out_confirmation("es-ES", "ES"), messages[1]
