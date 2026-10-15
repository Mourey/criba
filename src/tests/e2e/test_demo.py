"""The demo script as the Phase 2 acceptance test, through the API (spec 004 task 11, ADR 0028).

One test walks the presentation's demo from start to finish on a sim stack (stub model, no
real model, no spend):

1. a Knockout Candidate (``no_license_mx``) in the web chat;
2. a Qualified Candidate (``qualified_es``, Madrid);
3. the Madrid Recruiter Claims the Qualified Application;
4. takes over the chat, writes to the Candidate, and hands back;
5. Approves it from the review queue;
6. the agent offers slots, the Candidate taps one, the Interview is booked, and the booking
   confirmation carries the ``.ics`` invite, which downloads without a login;
7. the economics dashboard (``/api/metrics/economics``) shows these runs.

A second test replays a comparison dataset made on the same stack (`sim.compare`) and reads it
back from the dashboard's comparison view; it needs ``COMPOSE_PROJECT_NAME`` too.

The browser half of the demo is `src/web/tests/e2e/demo.spec.ts` (task 12). Collected only
when a stack is given (`conftest.py`, marker ``sim_stack``)::

    CRIBA_WEB_PORT=18106 docker compose -p criba-w3-3 \\
        -f docker-compose.yml -f docker-compose.sim.yml up -d --build --wait
    CRIBA_E2E_BASE_URL=http://localhost:18106 \\
    CRIBA_E2E_SEED_PASSWORD_FILE=secrets/criba_seed_password.txt \\
    COMPOSE_PROJECT_NAME=criba-w3-3 \\
        uv run --project src/backend pytest src/tests/e2e/test_demo.py -q
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
import pytest

from sim import asserts
from sim.compare.dataset import summarise
from sim.compare.import_dataset import import_compose
from sim.compare.runner import ComparisonRunner
from sim.driver import PersonaDriver, RunResult
from sim.personas.loader import load_personas
from sim.stack import (
    OPERATOR,
    is_sim_stack,
    login,
    new_phone,
    seed_password,
    settled_application,
)

pytestmark = [pytest.mark.e2e, pytest.mark.sim_stack]

BASE_URL = os.environ.get("CRIBA_E2E_BASE_URL", "")
RECRUITER = "recruiter.madrid@sazon.example"
PERSONAS = load_personas()


async def _client(email: str | None = None) -> httpx.AsyncClient:
    http = httpx.AsyncClient(base_url=BASE_URL, timeout=30)
    if email:
        await login(http, seed_password(), email)
    return http


async def _play(persona_id: str) -> tuple[RunResult, str]:
    persona = PERSONAS[persona_id]
    phone = new_phone(persona.identity.country)
    driver = PersonaDriver.for_base_url(BASE_URL, reply_timeout_s=60.0, poll_wait_s=5)
    try:
        run = await driver.run(persona, phone=phone)
    finally:
        await driver.aclose()
    return run, phone


async def _read_turn(
    http: httpx.AsyncClient, conversation_id: str, after: int
) -> list[dict[str, Any]]:
    """Messages after `after`, once the agent's turn is over."""
    url = f"/api/chat/conversations/{conversation_id}/messages"
    deadline = time.monotonic() + 60
    messages: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        page = (await http.get(url, params={"after": after, "wait": 5})).json()
        messages += page["messages"]
        after = page["cursor"]
        if messages and page["run_finished"]:
            break
    return messages


async def _approval_item(http: httpx.AsyncClient, application_id: str) -> dict[str, Any]:
    """The open Approval item for the Application, paging the queue (a reused stack has many)."""
    params: dict[str, Any] = {"kind": "approval", "limit": 200}
    while True:
        page = (await http.get("/api/queue", params=params)).json()
        for item in page["items"]:
            if item["application"]["id"] == application_id:
                return item
        if not page.get("next_cursor"):
            raise LookupError(f"no open Approval for {application_id}")
        params["cursor"] = page["next_cursor"]


def _report(run: RunResult, app: dict[str, Any]) -> str:
    lines = [f"ended={run.ended} stage={app.get('stage')} events={asserts.event_kinds(app)}"]
    lines += [f"  {e.sender}: {e.text[:100]!r}" for e in run.entries]
    return "\n".join(lines)


async def _demo() -> dict[str, Any]:
    chat = await _client()
    operator = await _client(OPERATOR)
    recruiter = await _client(RECRUITER)
    try:
        assert await is_sim_stack(chat), "the demo test runs on a sim stack only"
        await chat.post("/api/sim/clock/reset")

        # 1. A Knockout Candidate.
        ko_run, ko_phone = await _play("no_license_mx")
        ko_app = await settled_application(operator, ko_phone)
        failures = asserts.check_persona(PERSONAS["no_license_mx"], ko_app, ko_run)
        assert not failures, f"{failures}\n{_report(ko_run, ko_app)}"
        assert ko_app["outcome"] == "knockout"

        # 2. A Qualified Candidate.
        q_run, q_phone = await _play("qualified_es")
        q_app = await settled_application(operator, q_phone)
        failures = asserts.check_persona(PERSONAS["qualified_es"], q_app, q_run)
        assert not failures, f"{failures}\n{_report(q_run, q_app)}"
        app_id, conv = q_app["id"], q_run.conversation_id

        # The Recruiter sees it in the tracker.
        listing = (await recruiter.get("/api/applications", params={"q": q_phone})).json()
        assert [i["id"] for i in listing["items"]] == [app_id]

        # 3. Claim.
        claimed = await recruiter.post(f"/api/applications/{app_id}/claim")
        assert claimed.status_code == 200, claimed.text

        # 4. Takeover, a Team message, a Candidate reply that reaches the person, hand-back.
        started = await recruiter.post(f"/api/applications/{app_id}/takeover")
        assert started.status_code == 200, started.text
        text = "Hola Ana, soy del equipo de Grupo Sazón. Enseguida te confirmamos la entrevista."
        sent = await recruiter.post(
            f"/api/applications/{app_id}/takeover/messages", json={"text": text}
        )
        assert sent.status_code == 201, sent.text
        page = (
            await chat.get(f"/api/chat/conversations/{conv}/messages", params={"after": 0})
        ).json()
        team = [m for m in page["messages"] if m["sender"] == "human"]
        assert [m["text"] for m in team] == [text], page["messages"][-3:]
        cursor = page["cursor"]
        reply = await chat.post(
            f"/api/chat/conversations/{conv}/messages",
            json={
                "message_id": f"demo-{q_phone}-takeover",
                "text": "Gracias, quedo atento.",
            },
        )
        assert reply.status_code == 202, reply.text
        await asyncio.sleep(2)  # long enough for an agent run to have answered, had one started
        during = (
            await chat.get(f"/api/chat/conversations/{conv}/messages", params={"after": cursor})
        ).json()
        assert not [m for m in during["messages"] if m["sender"] == "agent"], during
        handed = await recruiter.post(
            f"/api/applications/{app_id}/takeover/handback",
            json={"summary": "Saludé a la Candidata; espera la Approval."},
        )
        assert handed.status_code == 200, handed.text

        # 5. Approval from the review queue.
        item = await _approval_item(recruiter, app_id)
        after = (
            await chat.get(f"/api/chat/conversations/{conv}/messages", params={"after": 0})
        ).json()["cursor"]
        decided = await recruiter.post(
            f"/api/queue/approvals/{item['id']}", json={"decision": "approved"}
        )
        assert decided.status_code == 200, decided.text

        # 6. Slots offered as buttons; a tap books the Interview; the confirmation has the .ics.
        offered = await _read_turn(chat, conv, after)
        offer = next((m for m in offered if m.get("interactive")), None)
        assert offer is not None, offered
        slot = next(o for o in offer["interactive"]["options"] if o["id"].startswith("so_"))
        tapped = await chat.post(
            f"/api/chat/conversations/{conv}/replies",
            json={
                "message_id": f"demo-{q_phone}-slot",
                "in_reply_to": offer["id"],
                "option_id": slot["id"],
            },
        )
        assert tapped.status_code == 202, tapped.text
        booked = await _read_turn(chat, conv, offer["id"])
        invites = [a for m in booked for a in m.get("attachments") or [] if a["type"] == "ics"]
        assert len(invites) == 1, booked
        ics = await chat.get(invites[0]["url"])
        assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
        assert "BEGIN:VCALENDAR" in ics.text and "BEGIN:VEVENT" in ics.text
        detail = (await recruiter.get(f"/api/applications/{app_id}")).json()

        # 7. The economics dashboard shows these runs (Operators only).
        economics = (
            await operator.get("/api/metrics/economics", params={"environment": "sim"})
        ).json()
        forbidden = await recruiter.get("/api/metrics/economics")
        ko_cost = (await operator.get(f"/api/applications/{ko_app['id']}")).json()["cost"]
        q_cost = (await operator.get(f"/api/applications/{app_id}")).json()["cost"]
        return {
            "detail": detail,
            "economics": economics,
            "economics_for_recruiter": forbidden.status_code,
            "costs": [ko_cost, q_cost],
        }
    finally:
        for http in (chat, operator, recruiter):
            await http.aclose()


def test_the_demo_from_knockout_to_booked_interview_and_economics() -> None:
    out = asyncio.run(_demo())
    detail = out["detail"]
    kinds = asserts.event_kinds(detail)
    assert detail["stage"] == "interview_booked", kinds
    for kind in (
        "takeover_started",
        "takeover_handed_back",
        "approved",
        "system_turn",
        "slots_offered",
        "interview_booked",
    ):
        assert kind in kinds, (kind, kinds)
    assert kinds.index("takeover_handed_back") < kinds.index("approved")
    assert detail["claim"] and detail["claim"]["user_name"].startswith("Recruiter Madrid"), detail[
        "claim"
    ]
    assert detail["interview"], detail
    assert "cost" not in detail  # Recruiters never see cost fields

    economics = out["economics"]
    assert out["economics_for_recruiter"] == 403
    assert economics["conversations"] >= 2, economics
    assert {"stub-model"} <= {m["model"] for m in economics["model_mix"]}, economics["model_mix"]
    assert economics["latency_ms"]["p50"] is not None
    for cost in out["costs"]:
        assert cost["runs"] > 0 and "stub-model" in cost["models"], cost


@pytest.mark.compose
def test_a_comparison_dataset_replays_on_the_economics_dashboard() -> None:
    """Two labels of the stub over two personas, imported as a replay, read back from the
    dashboard's comparison view (spec 004 tasks 9-10)."""
    dataset = f"demo-{int(time.time())}"

    async def scenario() -> tuple[list[Any], dict[str, Any]]:
        chat = await _client()
        operator = await _client(OPERATOR)
        try:

            async def enrich(phone: str) -> dict[str, Any]:
                return await settled_application(operator, phone)

            records = []
            for label in ("stub-as-haiku", "stub-as-sonnet"):
                runner = ComparisonRunner(chat, enrich, dataset=dataset, label=label)
                lines, stopped = await runner.run(
                    [PERSONAS["qualified_es"], PERSONAS["no_license_mx"]]
                )
                assert stopped is None
                records += lines
            import_compose(os.environ["COMPOSE_PROJECT_NAME"], records)
            response = await operator.get("/api/metrics/comparison", params={"dataset": dataset})
            assert response.status_code == 200, response.text
            return records, response.json()
        finally:
            await chat.aclose()
            await operator.aclose()

    records, replay = asyncio.run(scenario())
    assert all(r.correct for r in records), [(r.persona_id, r.failures) for r in records]
    assert replay["dataset"] == dataset and replay["personas"] == 4
    by_model = {m["model"]: m for m in replay["models"]}
    summary = {row["model"]: row for row in summarise(records)}
    assert set(by_model) == set(summary) == {"stub-as-haiku", "stub-as-sonnet"}
    for model, row in summary.items():
        assert by_model[model]["runs"] == row["turns"]
        assert by_model[model]["outcome_accuracy"] == row["outcome_accuracy"] == 1.0
        assert by_model[model]["cost_per_screening_usd"] == 0
