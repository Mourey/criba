"""The live flows against canned conversations and the in-process fake API.

The conversation itself (the driver playing a persona) is replaced by a canned `RunResult`; the
Operator API, the chat reads, the Approval, the tap and the downloads go through the fake. Each
flow gets a passing record and the failures the live run is there to catch.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import httpx
import pytest

from criba.channel.copy import OPT_OUT_CONFIRMATION, UNREADABLE_HANDOVER
from live import flows, personas
from live.client import LiveStack
from live.fake_api import HAIKU, FakeApi, cost_block, make_application, outbound
from live.flows import FlowResult, FlowSpec, run_flow
from sim.driver import Entry, RunResult, Turn
from sim.personas.schema import Persona
from sim.spend import SpendGuard, SpendRefused

GREETING_EN = "Hi! I'm the virtual (AI) assistant of Grupo Sazón. What's your name?"
GREETING_ES = "¡Hola! Soy el asistente virtual (IA) de Grupo Sazón. ¿Cómo te llamas?"
KNOCKOUT_ES = (
    "Gracias, Luis. Por ahora necesitamos licencia de manejo vigente. "
    "Aquí están los requisitos: https://empleo.gruposazon.example/requisitos"
)
FIELD_ORDER = personas.FIELD_ORDER


class ScriptedStack(LiveStack):
    """A `LiveStack` whose conversations are canned: `play` hands out prepared runs."""

    def __init__(self, *args: Any, runs: list[tuple[RunResult, str]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.runs = list(runs)

    async def play(self, persona: Persona, **kwargs: Any) -> tuple[RunResult, str]:
        return self.runs.pop(0)


def entry(
    text: str,
    *,
    id: int,
    inbound: bool = False,
    purpose: str | None = None,
    **extra: Any,
) -> Entry:
    return Entry(
        direction="inbound" if inbound else "outbound",
        sender="candidate" if inbound else "agent",
        text=text,
        t=float(id),
        id=id,
        purpose=purpose,
        **extra,
    )


def run_of(
    entries: list[Entry], ended: str, phone: str, conversation: str = "conv_1"
) -> tuple[RunResult, str]:
    turns = [
        Turn(sent=e, replies=[], latency_first_s=1.5) for e in entries if e.direction == "inbound"
    ]
    return RunResult("p", conversation, phone, ended, entries, turns), phone


def play_flow(
    api: FakeApi,
    flow_id: str,
    runs: list[tuple[RunResult, str]],
    *,
    cap: float = 10.0,
) -> tuple[FlowResult, SpendGuard]:
    spec = next(f for f in flows.FLOWS if f.id == flow_id)
    return run_spec(api, spec, runs, cap=cap)


def run_spec(
    api: FakeApi,
    spec: FlowSpec,
    runs: list[tuple[RunResult, str]],
    *,
    cap: float = 10.0,
    stack_class: type[ScriptedStack] = ScriptedStack,
) -> tuple[FlowResult, SpendGuard]:
    guard = SpendGuard(cap_usd=cap, real=True)

    async def go() -> FlowResult:
        transport = httpx.ASGITransport(app=api.app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://fake") as chat,
            httpx.AsyncClient(transport=transport, base_url="http://fake") as operator,
        ):
            stack = stack_class(
                chat, operator, guard, runs=runs, reply_timeout_s=2.0, poll_wait_s=0.0
            )
            return await run_flow(stack, spec)

    return asyncio.run(go()), guard


def failed(res: FlowResult) -> list[str]:
    return [c.name for c in res.checks if not c.ok]


# ---- records the live run would see ------------------------------------------------------


def recorded(fields: tuple[str, ...] = FIELD_ORDER) -> list[tuple[str, dict[str, Any]]]:
    return [("answer_recorded", {"field": f}) for f in fields]


def qualified_app(
    app_id: str,
    phone: str,
    *,
    language: str = "en",
    stage: str = "qualified",
    prose: str | None = "Daniel Foster is qualified. He can start on Monday.",
    name: str = "Daniel Foster",
    cost: dict[str, Any] | None = None,
) -> dict[str, Any]:
    app = make_application(
        app_id,
        phone,
        stage=stage,
        prose=prose,
        events=[
            "application_started",
            *recorded(),
            ("screening_finished", {"result": "qualified"}),
            "approval_requested",
        ],
        answers={
            "full_name": name,
            "zone": {"id": "zone_es_madrid_chamberi", "name": "Chamberí", "city": "Madrid"},
            "driver_license": True,
            "availability": ["full_time"],
            "preferred_schedule": ["afternoon"],
            "experience_years": 2.5,
            "experience_platforms": ["glovo"],
            "start_date": "2026-10-12",
        },
        cost=cost,
    )
    app["candidate"]["language"] = language
    if prose:
        app["summary"]["structured"] = {"language": language}
    return app


def qualified_run(phone: str, greeting: str = GREETING_EN) -> tuple[RunResult, str]:
    return run_of(
        [
            entry("Hi, I'd like to apply", id=1, inbound=True, purpose="opening"),
            entry(greeting, id=2),
            entry("Yes", id=3, inbound=True, purpose="answer:confirm"),
            entry("All set, a recruiter will write to you.", id=4),
        ],
        "completed",
        phone,
    )


# ---- flow 1: Qualified -------------------------------------------------------------------


def test_qualified_passes_on_a_complete_record_with_a_finished_summary() -> None:
    api = FakeApi()
    api.applications["app_1"] = qualified_app("app_1", "+34600000001")
    res, guard = play_flow(api, "qualified", [qualified_run("+34600000001")])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    assert res.conversations == 1 and res.phones == ["+34600000001"]
    assert [lat.label for lat in res.latencies] == [
        "reply to opening",
        "reply to answer:confirm",
    ]
    assert res.charge.runs == 4 and res.charge.cost_usd > 0
    assert guard.spent_usd == pytest.approx(res.charge.guard_usd)
    assert any(line.startswith("Candidate: Hi") for line in res.transcript)
    assert any(n.startswith("Summary: ") for n in res.notes)
    assert any(c.name == f"turns served by {HAIKU}" and c.ok for c in res.checks)


def test_qualified_fails_when_screening_stalls_at_the_confirm_step() -> None:
    api = FakeApi()
    api.applications["app_1"] = qualified_app("app_1", "+34600000001", stage="confirm", prose=None)
    res, _ = play_flow(api, "qualified", [qualified_run("+34600000001")])
    assert res.status == "fail"
    names = failed(res)
    assert "stage qualified, open" in names
    assert "summary prose written" in names
    assert "summary prose complete (ends on a full stop)" in names


def test_qualified_fails_on_a_summary_cut_off_mid_sentence() -> None:
    api = FakeApi()
    api.applications["app_1"] = qualified_app(
        "app_1", "+34600000001", prose="Daniel Foster is qualified and can start on"
    )
    res, _ = play_flow(api, "qualified", [qualified_run("+34600000001")])
    assert failed(res) == ["summary prose complete (ends on a full stop)"]
    assert "can start on" in next(c.detail for c in res.checks if not c.ok)


def test_qualified_fails_on_a_summary_with_a_stage_code_or_the_notes() -> None:
    api = FakeApi()
    api.applications["app_1"] = qualified_app(
        "app_1", "+34600000001", prose="Daniel Foster is ready. Stage reached: qualified."
    )
    res, _ = play_flow(api, "qualified", [qualified_run("+34600000001")])
    assert failed(res) == ["summary prose is plain words (no stage, field or flag codes, no ids)"]
    api.applications["app_1"] = qualified_app(
        "app_1", "+34600000001", prose="Daniel Foster is ready. How to approach: be brief."
    )
    res, _ = play_flow(api, "qualified", [qualified_run("+34600000001")])
    assert failed(res) == ["summary prose carries no communication notes (ADR 0056)"]


def test_qualified_fails_without_the_ai_disclosure() -> None:
    api = FakeApi()
    api.applications["app_1"] = qualified_app("app_1", "+34600000001")
    res, _ = play_flow(
        api, "qualified", [qualified_run("+34600000001", greeting="Hi! What's your name?")]
    )
    assert failed(res) == ["AI disclosure in the first message"]


def test_a_turn_model_other_than_the_expected_one_fails_the_flow() -> None:
    api = FakeApi()
    stub_cost = cost_block(summary=False)
    stub_cost["models"] = ["stub-model"]
    stub_cost["by_model"][0]["model"] = "stub-model"
    api.applications["app_1"] = qualified_app("app_1", "+34600000001", cost=stub_cost)
    res, _ = play_flow(api, "qualified", [qualified_run("+34600000001")])
    assert f"turns served by {HAIKU}" in failed(res)


# ---- flow 2: Knockout then Review --------------------------------------------------------


def knockout_app(phone: str, *, with_review_event: bool = True) -> dict[str, Any]:
    events: list[str | tuple[str, dict[str, Any]]] = [
        "application_started",
        ("answer_recorded", {"field": "full_name"}),
        ("answer_recorded", {"field": "zone"}),
        ("answer_recorded", {"field": "driver_license"}),
        ("knockout", {"message_id": 3, "template": "knockout_driver_license.es-MX"}),
        ("screening_finished", {"result": "knockout"}),
    ]
    if with_review_event:
        events.append("review_requested")
    messages = [
        {"id": 1, "direction": "outbound", "sender": "agent", "body": GREETING_ES},
        {"id": 2, "direction": "inbound", "sender": "candidate", "body": "No tengo licencia"},
        {"id": 3, "direction": "outbound", "sender": "agent", "body": KNOCKOUT_ES},
        {"id": 4, "direction": "inbound", "sender": "candidate", "body": "revisión"},
        {"id": 5, "direction": "outbound", "sender": "agent", "body": "He pedido la revisión."},
    ]
    app = make_application(
        "app_2",
        phone,
        stage="driver_license",
        status="closed",
        outcome="knockout",
        knockout_criterion="driver_license",
        events=events,
        messages=messages,
        answers={
            "full_name": "Luis Hernández",
            "zone": {"id": "zone_mx_puebla_centro", "name": "Centro", "city": "Puebla"},
            "driver_license": False,
        },
        prose="Luis Hernández was knocked out on the licence.",
        cost=cost_block(summary=False),
    )
    app["candidate"]["language"] = "es-MX"
    return app


def knockout_run(phone: str) -> tuple[RunResult, str]:
    return run_of(
        [
            entry("Hola, quiero trabajar", id=1, inbound=True, purpose="opening"),
            entry(GREETING_ES, id=2),
            entry("No tengo licencia", id=3, inbound=True, purpose="answer:driver_license"),
            entry(KNOCKOUT_ES, id=4),
            entry("quisiera una revisión", id=5, inbound=True, purpose="interrupt:after_knockout"),
            entry("He pedido la revisión.", id=6),
        ],
        "knockout",
        phone,
    )


def review_item(application_id: str) -> dict[str, Any]:
    return {
        "id": "qi_rev",
        "kind": "review",
        "status": "open",
        "application": {"id": application_id},
    }


def test_knockout_review_passes_with_the_link_and_an_open_review_item() -> None:
    api = FakeApi()
    api.applications["app_2"] = knockout_app("+525500000001")
    api.queue_pages = [[review_item("app_2")]]
    res, _ = play_flow(api, "knockout_review", [knockout_run("+525500000001")])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    assert any("empleo.gruposazon.example/requisitos" in n for n in res.notes)
    assert any(n.startswith("Review item qi_rev") for n in res.notes)


def test_knockout_review_fails_without_a_review_item_on_the_queue() -> None:
    api = FakeApi()
    api.applications["app_2"] = knockout_app("+525500000001", with_review_event=False)
    api.queue_pages = [[]]
    res, _ = play_flow(api, "knockout_review", [knockout_run("+525500000001")])
    assert res.status == "fail"
    assert "one Review queue item open" in failed(res)
    assert "events on the record" in failed(res)


# ---- flow 3: CDMX Zone list --------------------------------------------------------------


ZONE_LIST = {
    "style": "list",
    "options": [
        {"id": "zone_mx_centro", "label": "Centro"},
        {"id": "zone_mx_polanco", "label": "Polanco"},
        {"id": "zone_mx_roma", "label": "Roma"},
    ],
}


def cdmx_app(phone: str, *, zone_id: str = "zone_mx_polanco") -> dict[str, Any]:
    app = make_application(
        "app_3",
        phone,
        stage="driver_license",
        events=[
            "application_started",
            ("answer_recorded", {"field": "full_name"}),
            "answer_ambiguous",
            ("answer_recorded", {"field": "zone"}),
        ],
        answers={
            "full_name": "Valeria Núñez",
            "zone": {"id": zone_id, "name": "Polanco", "city": "Ciudad de México"},
        },
        prose=None,
        cost=cost_block(summary=False),
    )
    app["candidate"]["language"] = "es-MX"
    return app


def cdmx_run(phone: str, *, offer: bool = True, tap: bool = True) -> tuple[RunResult, str]:
    entries = [
        entry("Hola", id=1, inbound=True, purpose="opening"),
        entry(GREETING_ES, id=2),
        entry("Ciudad de México, Cuajimalpa", id=3, inbound=True, purpose="answer:zone"),
        entry("Elige en la lista tu zona", id=4, interactive=ZONE_LIST if offer else None),
    ]
    if tap:
        entries.append(
            entry(
                "Polanco",
                id=5,
                inbound=True,
                purpose="answer:zone",
                reply_to={"message_id": 4, "option_id": "zone_mx_polanco"},
            )
        )
    return run_of(entries, "stopped", phone)


def test_cdmx_zone_passes_when_the_list_is_offered_and_the_tap_recorded() -> None:
    api = FakeApi()
    api.applications["app_3"] = cdmx_app("+525500000002")
    res, _ = play_flow(api, "cdmx_zone", [cdmx_run("+525500000002")])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    assert any(n.startswith("Zone list (3): Centro, Polanco, Roma") for n in res.notes)
    assert any("Recorded Zone: Polanco" in n for n in res.notes)
    assert any("(tapped)" in line for line in res.transcript)


def test_cdmx_zone_fails_when_the_tapped_zone_is_not_the_recorded_one() -> None:
    api = FakeApi()
    api.applications["app_3"] = cdmx_app("+525500000002", zone_id="zone_mx_roma")
    res, _ = play_flow(api, "cdmx_zone", [cdmx_run("+525500000002")])
    assert failed(res) == ["the tapped Zone is the recorded Zone"]


def test_cdmx_zone_fails_when_no_list_is_offered() -> None:
    api = FakeApi()
    api.applications["app_3"] = cdmx_app("+525500000002")
    res, _ = play_flow(api, "cdmx_zone", [cdmx_run("+525500000002", offer=False, tap=False)])
    assert "a list of Zones was offered" in failed(res)
    assert "a Zone was tapped" in failed(res)


# ---- flow 3b: a city plus a place that is not a Zone (demo chat, ADR 0062) ---------------

TEPITO_PHONE = "+525500000903"


def tepito_run(*, listed_first: bool = True) -> tuple[RunResult, str]:
    """The demo chat's Zone step: with ``listed_first`` the list comes with the first reply;
    otherwise as in the demo, a reply about "el listado" with none, then the list."""
    entries = [
        entry("Hola!", id=1, inbound=True, purpose="opening"),
        entry(GREETING_ES, id=2),
        entry("Ciudad de Mexico en Tepito", id=3, inbound=True, purpose="answer:zone"),
    ]
    if listed_first:
        entries.append(entry("Del listado, ¿cuál te queda más cerca?", id=4, interactive=ZONE_LIST))
    else:
        entries += [
            entry("Del listado de zonas, ¿cuál te queda más cerca de Tepito?", id=4),
            entry("Cual listado?", id=5, inbound=True, purpose="answer:zone"),
        ]
        entries.append(entry("Te muestro las zonas", id=6, interactive=ZONE_LIST))
    last = entries[-1].id
    entries.append(
        entry(
            "Polanco",
            id=last + 1,
            inbound=True,
            purpose="answer:zone",
            reply_to={"message_id": last, "option_id": "zone_mx_polanco"},
        )
    )
    return run_of(entries, "stopped", TEPITO_PHONE)


def test_cdmx_unknown_place_passes_when_the_first_reply_carries_the_list() -> None:
    api = FakeApi()
    api.applications["app_3"] = cdmx_app(TEPITO_PHONE)
    api.applications["app_3"]["answers"]["full_name"] = "Fernando Fernández"
    res, _ = play_flow(api, "cdmx_unknown_place", [tepito_run()])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    assert any("Recorded Zone: Polanco" in n for n in res.notes)


def test_cdmx_unknown_place_fails_when_the_list_comes_only_after_the_candidate_asks() -> None:
    api = FakeApi()
    api.applications["app_3"] = cdmx_app(TEPITO_PHONE)
    api.applications["app_3"]["answers"]["full_name"] = "Fernando Fernández"
    res, _ = play_flow(api, "cdmx_unknown_place", [tepito_run(listed_first=False)])
    assert failed(res) == ["the first reply to the Zone answer carries the list"]
    assert "Del listado de zonas" in next(
        c.detail
        for c in res.checks
        if c.name == "the first reply to the Zone answer carries the list"
    )


# ---- flows 3c and 3d: the Zone funnel (demo chat 2026-10-12, ADR 0068) --------------------

GREETING_EN_NOTICE = (
    "Hi Cody! I'm Grupo Sazón's virtual (AI) assistant. Where do you live?\n"
    "Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad"
)
CITIES_QUESTION = (
    "We hire delivery drivers in these cities: Ciudad de México, Guadalajara, Monterrey, "
    "Puebla. Can you work in one of them?"
)
CITIES_LIST = {
    "style": "list",
    "options": [
        {"id": "city_mx_ciudad_de_mexico", "label": "Ciudad de México"},
        {"id": "city_mx_guadalajara", "label": "Guadalajara"},
        {"id": "city_mx_monterrey", "label": "Monterrey"},
        {"id": "city_mx_puebla", "label": "Puebla"},
        {"id": "city_none", "label": "None of these"},
    ],
}
KNOCKOUT_EN = (
    "Thank you, Cody. Right now none of our locations deliver in your area. Here are the "
    "requirements: https://empleo.gruposazon.example/requisitos. If you think this is a "
    'mistake, reply "review" and a person will look at it.'
)
TEXAS_PHONE = "+525500000904"
DALLAS_PHONE = "+525500000905"


def texas_app(phone: str, *, zone_stored: bool = False) -> dict[str, Any]:
    app = make_application(
        "app_4",
        phone,
        stage="zone",
        status="closed",
        outcome="knockout",
        knockout_criterion="zone",
        events=[
            "application_started",
            ("answer_recorded", {"field": "full_name"}),
            ("answer_ambiguous", {"field": "zone", "funnel": True}),
            ("knockout", {"message_id": 6, "template": "knockout_zone.en"}),
            ("screening_finished", {"result": "knockout"}),
        ],
        messages=[
            {"id": 1, "direction": "outbound", "sender": "agent", "body": GREETING_EN_NOTICE},
            {"id": 2, "direction": "inbound", "sender": "candidate", "body": "I live in Texas!"},
            {"id": 3, "direction": "outbound", "sender": "agent", "body": CITIES_QUESTION},
            {"id": 5, "direction": "inbound", "sender": "candidate", "body": "None of these"},
            {"id": 6, "direction": "outbound", "sender": "agent", "body": KNOCKOUT_EN},
        ],
        answers={
            "full_name": "Cody Walker",
            "zone": {"id": "zone_mx_cdmx_polanco", "name": "Polanco", "city": "x"}
            if zone_stored
            else None,
        },
        prose=None,
        cost=cost_block(summary=False),
    )
    app["answer_status"] = [
        {"field": "zone", "status": "knockout", "raw_input": "I live in Texas!", "attempts": 1}
    ]
    app["candidate"]["language"] = "en"
    return app


def texas_run(
    phone: str, *, greeting: str = GREETING_EN_NOTICE, question: str = CITIES_QUESTION
) -> tuple[RunResult, str]:
    entries = [
        entry(
            "Hi, I'm Cody Walker, I saw your delivery job ad", id=1, inbound=True, purpose="opening"
        ),
        entry(greeting, id=2),
        entry("I live in Texas!", id=3, inbound=True, purpose="answer:zone"),
        entry(question, id=4, interactive=CITIES_LIST),
        entry(
            "None of these",
            id=5,
            inbound=True,
            purpose="answer:zone",
            reply_to={"message_id": 4, "option_id": "city_none"},
        ),
        entry(KNOCKOUT_EN, id=6),
    ]
    return run_of(entries, "knockout", phone)


def test_zone_funnel_none_passes_on_the_cities_question_and_the_knockout() -> None:
    api = FakeApi()
    api.applications["app_4"] = texas_app(TEXAS_PHONE)
    res, _ = play_flow(api, "zone_funnel_none", [texas_run(TEXAS_PHONE)])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    assert any(n.startswith("Cities question: We hire delivery drivers") for n in res.notes)


def test_zone_funnel_none_fails_when_the_agent_improvises_the_first_message_lacks_the_link() -> (
    None
):
    api = FakeApi()
    api.applications["app_4"] = texas_app(TEXAS_PHONE)
    run = texas_run(
        TEXAS_PHONE,
        greeting="Hi! I'm Grupo Sazón's virtual (AI) assistant. Where do you live?",
        question="We only hire in Spain and Mexico. Can you work in Ciudad de México, "
        "Guadalajara, Monterrey or Puebla?",
    )
    res, _ = play_flow(api, "zone_funnel_none", [run])
    assert "the first agent message links the privacy notice" in failed(res)
    assert "the agent named no other area" in failed(res)


def test_zone_funnel_none_fails_when_a_zone_was_stored_or_the_list_never_came() -> None:
    api = FakeApi()
    api.applications["app_4"] = texas_app(TEXAS_PHONE, zone_stored=True)
    run, phone = texas_run(TEXAS_PHONE)
    run.entries[3].interactive = None
    res, _ = play_flow(api, "zone_funnel_none", [(run, phone)])
    assert "the cities were offered once, as a list ending with none of these" in failed(res)
    assert "the Zone is the failed check, no Zone stored, the Candidate's words kept" in failed(res)


TYPED_PHONE = "+525500000906"


def typed_run(phone: str, *, tap: bool = False) -> tuple[RunResult, str]:
    """The Candidate types a second place instead of tapping: the Zone Knockout follows."""
    entries = [
        entry(
            "Hi, I'm Cody Walker, I saw your delivery job ad", id=1, inbound=True, purpose="opening"
        ),
        entry(GREETING_EN_NOTICE, id=2),
        entry("I live in Texas!", id=3, inbound=True, purpose="answer:zone"),
        entry(CITIES_QUESTION, id=4, interactive=CITIES_LIST),
        entry(
            "I live in the united states",
            id=5,
            inbound=True,
            purpose="answer:zone",
            reply_to={"message_id": 4, "option_id": "city_none"} if tap else None,
        ),
        entry(KNOCKOUT_EN, id=6),
    ]
    return run_of(entries, "knockout", phone)


def test_zone_funnel_typed_passes_when_the_second_typed_place_is_the_knockout() -> None:
    api = FakeApi()
    app = texas_app(TYPED_PHONE)
    app["answer_status"][0]["raw_input"] = "I live in the united states"
    api.applications["app_4"] = app
    res, _ = play_flow(api, "zone_funnel_typed", [typed_run(TYPED_PHONE)])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]


def test_zone_funnel_typed_fails_when_the_candidate_tapped_or_the_words_were_lost() -> None:
    api = FakeApi()
    api.applications["app_4"] = texas_app(TYPED_PHONE)  # keeps "Texas", not the second place
    res, _ = play_flow(api, "zone_funnel_typed", [typed_run(TYPED_PHONE, tap=True)])
    assert "nothing was tapped, the second place was typed" in failed(res)
    assert "the Zone is the failed check, no Zone stored, the second place kept" in failed(res)


def dallas_app(phone: str) -> dict[str, Any]:
    app = make_application(
        "app_5",
        phone,
        stage="driver_license",
        events=[
            "application_started",
            ("answer_recorded", {"field": "full_name"}),
            ("answer_ambiguous", {"field": "zone", "funnel": True}),
            ("answer_ambiguous", {"field": "zone"}),
            ("answer_recorded", {"field": "zone"}),
        ],
        answers={
            "full_name": "Dana Whitfield",
            "zone": {"id": "zone_mx_monterrey_cumbres", "name": "Cumbres", "city": "Monterrey"},
        },
        prose=None,
        cost=cost_block(summary=False),
    )
    app["candidate"]["language"] = "en"
    return app


def dallas_run(
    phone: str, *, tapped_zone: str = "zone_mx_monterrey_cumbres"
) -> tuple[RunResult, str]:
    zones = {
        "style": "list",
        "options": [
            {"id": "zone_mx_monterrey_cumbres", "label": "Cumbres"},
            {"id": "zone_mx_monterrey_guadalupe", "label": "Guadalupe"},
        ],
    }
    entries = [
        entry(
            "Hello, I'd like to apply as a delivery driver", id=1, inbound=True, purpose="opening"
        ),
        entry(GREETING_EN_NOTICE, id=2),
        entry("I live in Dallas", id=3, inbound=True, purpose="answer:zone"),
        entry(CITIES_QUESTION, id=4, interactive=CITIES_LIST),
        entry(
            "Monterrey",
            id=5,
            inbound=True,
            purpose="answer:zone",
            reply_to={"message_id": 4, "option_id": "city_mx_monterrey"},
        ),
        entry("Which one is your area? Pick it from the list.", id=6, interactive=zones),
        entry(
            "Cumbres",
            id=7,
            inbound=True,
            purpose="answer:zone",
            reply_to={"message_id": 6, "option_id": tapped_zone},
        ),
    ]
    return run_of(entries, "stopped", phone)


def test_zone_funnel_city_passes_when_a_city_leads_to_its_zones_and_one_is_recorded() -> None:
    api = FakeApi()
    api.applications["app_5"] = dallas_app(DALLAS_PHONE)
    res, _ = play_flow(api, "zone_funnel_city", [dallas_run(DALLAS_PHONE)])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    assert any("Recorded Zone: Cumbres (Monterrey)" in n for n in res.notes)


def test_zone_funnel_city_fails_when_the_tapped_zone_is_not_the_recorded_one() -> None:
    api = FakeApi()
    api.applications["app_5"] = dallas_app(DALLAS_PHONE)
    res, _ = play_flow(
        api,
        "zone_funnel_city",
        [dallas_run(DALLAS_PHONE, tapped_zone="zone_mx_monterrey_guadalupe")],
    )
    assert failed(res) == ["the tapped Zone is the recorded Zone"]


# ---- flow 4: Approval and booking --------------------------------------------------------

SLOT_OFFER = {
    "style": "list",
    "options": [
        {"id": "so_a", "label": "lun 12 · 10:00"},
        {"id": "so_b", "label": "lun 12 · 12:00"},
        {"id": "so_more", "label": "Otro horario"},
    ],
}
ICS = {"type": "ics", "url": "/files/entrevista.ics", "filename": "entrevista.ics"}


def booking_api(phone: str, *, ics: bool = True, slots: bool = True) -> FakeApi:
    api = FakeApi()
    app = qualified_app("app_4", phone, language="es-ES", name="Marta Ruiz Pérez")
    api.applications["app_4"] = app
    api.queue_pages = [
        [{"id": "qi_ap", "kind": "approval", "status": "open", "application": {"id": "app_4"}}]
    ]
    for number in range(4):  # the conversation the canned run left behind (ids 1 to 4)
        api.say("conv_1", f"earlier message {number}")
    api.files["entrevista.ics"] = ("text/calendar", "BEGIN:VCALENDAR\nBEGIN:VEVENT\nEND:VEVENT")

    def on_approve(a: FakeApi, item_id: str) -> None:
        app["events"].append({"id": 90, "kind": "approved", "actor": "user", "data": {}})
        if slots:
            a.say("conv_1", "Aprobada. Elige una entrevista", interactive=SLOT_OFFER)
        else:
            a.say("conv_1", "Aprobada.")

    def on_reply(a: FakeApi, cid: str, in_reply_to: int, option: str) -> None:
        for kind in ("slots_offered", "interview_booked"):
            app["events"].append({"id": 91, "kind": kind, "actor": "agent", "data": {}})
        app["stage"] = "interview_booked"
        app["interview"] = {"local_label": "lun 12 · 10:00", "location_name": "Sazón Madrid"}
        a.say("conv_1", "Entrevista confirmada", attachments=[ICS] if ics else [])

    api.on_approve = on_approve
    api.on_reply = on_reply
    return api


def booking_run(phone: str) -> tuple[RunResult, str]:
    run, _ = qualified_run(phone, greeting=GREETING_ES)
    return run, phone


def test_booking_passes_from_approval_to_the_ics() -> None:
    api = booking_api("+34600000003")
    res, _ = play_flow(api, "booking", [booking_run("+34600000003")])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    assert api.approvals == ["qi_ap"]
    assert {lat.label for lat in res.latencies} >= {
        "Approval to slot offer",
        "slot tap to confirmation",
    }
    assert any(n.startswith("Slots offered: lun 12") for n in res.notes)
    assert any("attachment ics" in line for line in res.transcript)


# ---- flow 4b: Otro horario ---------------------------------------------------------------

OTHER_OFFER = {
    "style": "buttons",
    "options": [
        {"id": "so_a", "label": "lun 12 · 10:00"},
        {"id": "so_b", "label": "lun 12 · 12:00"},
        {"id": "other", "label": "Otro horario"},
    ],
}
BOOKING_LINK = "https://empleo.example/book/tok.sig"


def other_time_api(
    phone: str, *, link: bool = True, buttons: bool = True, page: int = 200
) -> FakeApi:
    api = booking_api(phone)
    api.booking_page_status = page
    approve = api.on_approve
    booked = api.on_reply

    def on_approve(a: FakeApi, item_id: str) -> None:
        approve(a, item_id)
        a.messages["conv_1"][-1]["interactive"] = OTHER_OFFER

    def on_reply(a: FakeApi, cid: str, in_reply_to: int, option: str) -> None:
        if option != "other":
            booked(a, cid, in_reply_to, option)
            return
        text = f"Aquí tienes todos los horarios: {BOOKING_LINK}" if link else "¿Qué día te va bien?"
        a.say("conv_1", text, interactive=OTHER_OFFER if buttons else None)

    api.on_approve = on_approve
    api.on_reply = on_reply
    return api


def test_other_time_passes_when_every_tap_gets_a_working_link() -> None:
    api = other_time_api("+34600000003")
    res, _ = play_flow(api, "other_time", [booking_run("+34600000003")])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    taps = [c for c in res.checks if "the reply carries the booking link" in c.name]
    assert len(taps) == flows.OTHER_TIME_TAPS and all(c.ok for c in taps)
    assert any(c.name == "a slot tapped after the link is booked" and c.ok for c in res.checks)


def test_other_time_fails_when_the_reply_has_no_link() -> None:
    api = other_time_api("+34600000003", link=False)
    res, _ = play_flow(api, "other_time", [booking_run("+34600000003")])
    assert failed(res) == ["tap 1: the reply carries the booking link"]


def test_other_time_fails_when_the_page_refuses_the_link() -> None:
    api = other_time_api("+34600000003", page=410)
    res, _ = play_flow(api, "other_time", [booking_run("+34600000003")])
    assert "tap 1: the booking page accepts the link and lists slots" in failed(res)


def test_other_time_fails_when_the_slot_buttons_are_gone_after_the_link() -> None:
    api = other_time_api("+34600000003", buttons=False)
    res, _ = play_flow(api, "other_time", [booking_run("+34600000003")])
    assert failed(res) == ["tap 1: the slot buttons are still offered"]


def test_other_time_stops_when_the_slot_offer_has_no_other_option() -> None:
    api = booking_api("+34600000003")  # its offer ends with "so_more", not "other"
    res, _ = play_flow(api, "other_time", [booking_run("+34600000003")])
    assert failed(res) == ["tap 1: the buttons include the other-time option"]


def test_booking_fails_when_the_confirmation_has_no_ics() -> None:
    api = booking_api("+34600000003", ics=False)
    res, _ = play_flow(api, "booking", [booking_run("+34600000003")])
    assert failed(res) == ["booking confirmation carries the .ics"]


def test_booking_fails_when_no_slots_arrive_after_the_approval() -> None:
    api = booking_api("+34600000003", slots=False)
    res, _ = play_flow(api, "booking", [booking_run("+34600000003")])
    assert "slot offer arrives with buttons" in failed(res)
    assert all(lat.label != "slot tap to confirmation" for lat in res.latencies)


def test_booking_stops_when_the_approval_is_refused() -> None:
    api = booking_api("+34600000003")
    api.approve_status = 409
    res, _ = play_flow(api, "booking", [booking_run("+34600000003")])
    assert failed(res) == ["Approval accepted by the Operator API"]


def test_booking_stops_when_there_is_no_approval_to_resolve() -> None:
    api = booking_api("+34600000003")
    api.queue_pages = [[]]
    res, _ = play_flow(api, "booking", [booking_run("+34600000003")])
    assert failed(res) == ["one Approval queue item open"]
    assert api.approvals == []


# ---- flow 5: STOP ------------------------------------------------------------------------


def stop_api(*, confirm: bool = True, opt_out: bool = True, cold_app: bool = False) -> FakeApi:
    api = FakeApi()
    seen: dict[str, int] = {}

    def on_message(a: FakeApi, cid: str, text: str) -> None:
        phone = a.phones[cid]
        seen[cid] = seen.get(cid, 0) + 1
        if text == "STOP":
            if cid in a.applications_by_conv:
                app = a.applications_by_conv[cid]
                app["candidate"]["opted_out"] = opt_out
                if opt_out:
                    app["events"].append({"id": 50, "kind": "opted_out", "actor": "system"})
            elif cold_app:
                a.applications_by_conv[cid] = make_application(f"app_{cid}", phone)
                a.applications[f"app_{cid}"] = a.applications_by_conv[cid]
            if confirm:
                a.say(cid, OPT_OUT_CONFIRMATION["es-ES"])
            return
        app = make_application(
            f"app_{cid}", phone, stage="name", events=["application_started"], prose=None
        )
        a.applications[app["id"]] = app
        a.applications_by_conv[cid] = app
        a.say(cid, GREETING_ES)

    api.on_message = on_message
    return api


def test_stop_passes_mid_conversation_and_as_a_first_message() -> None:
    api = stop_api()
    res, guard = play_flow(api, "stop", [])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    assert res.conversations == 2 and len(res.phones) == 2
    assert [lat.model for lat in res.latencies] == [True, False, False]
    assert res.charge.runs == 4  # only the greeted conversation has an Application with usage
    assert guard.spent_usd > 0


def test_stop_fails_when_it_is_not_confirmed_or_not_recorded() -> None:
    res, _ = play_flow(stop_api(confirm=False, opt_out=False), "stop", [])
    names = failed(res)
    assert "STOP confirmed once, in the fixed copy" in names
    assert "Candidate marked Opted out" in names
    assert "a first-message STOP is confirmed" in names


def test_stop_fails_when_a_first_message_stop_creates_an_application() -> None:
    res, _ = play_flow(stop_api(cold_app=True), "stop", [])
    assert failed(res) == ["a first-message STOP created no Application"]


# ---- run_flow ----------------------------------------------------------------------------


async def _raises(stack: LiveStack, res: FlowResult) -> None:
    res.phones.append("+34600000005")
    res.conversations += 1
    raise RuntimeError("the chat went away")


def test_a_flow_that_raises_is_an_error_with_its_spend_still_counted() -> None:
    api = FakeApi()
    api.applications["app_5"] = qualified_app("app_5", "+34600000005")
    res, guard = run_spec(api, FlowSpec("boom", "Boom", _raises), [])
    assert res.status == "error" and "RuntimeError: the chat went away" in (res.error or "")
    assert not res.passed
    assert res.charge.runs == 4 and guard.spent_usd > 0


def test_a_flow_with_no_application_costs_nothing() -> None:
    res, guard = run_spec(FakeApi(), FlowSpec("boom", "Boom", _raises), [])
    assert res.status == "error" and res.charge.runs == 0 and guard.spent_usd == 0
    assert not res.cost_unknown


class UnreadableStack(ScriptedStack):
    """The Operator API times out when the flow's cost is read."""

    async def application(self, phone: str, **kwargs: Any) -> dict[str, Any]:
        raise httpx.ReadTimeout("slow")


def test_a_cost_that_cannot_be_read_is_marked_unknown() -> None:
    async def body(stack: LiveStack, res: FlowResult) -> None:
        res.phones.append("+34600000005")

    res, _ = run_spec(FakeApi(), FlowSpec("slow", "Slow", body), [], stack_class=UnreadableStack)
    assert res.cost_unknown and any("unknown" in n for n in res.notes)


def test_a_spend_refusal_is_not_swallowed() -> None:
    async def refuses(stack: LiveStack, res: FlowResult) -> None:
        raise SpendRefused("over the cap")

    with pytest.raises(SpendRefused):
        run_spec(FakeApi(), FlowSpec("cap", "Cap", refuses), [])


# ---- helpers -----------------------------------------------------------------------------


def test_select_returns_all_flows_or_the_named_ones_in_order() -> None:
    assert [f.id for f in flows.select(None)] == [
        "qualified",
        "knockout_review",
        "cdmx_zone",
        "cdmx_unknown_place",
        "zone_funnel_none",
        "zone_funnel_typed",
        "zone_funnel_city",
        "booking",
        "other_time",
        "stop",
        "language",
        "frustrated",
        "no_app",
        "start_date_choice",
        "start_date_plain",
        "zone_country",
        "language_request",
        "unreadable",
        "zone_wording_es",
        "zone_wording_mx",
    ]
    assert [f.id for f in flows.select(["stop", "qualified"])] == ["stop", "qualified"]
    with pytest.raises(ValueError, match="unknown flow nope"):
        flows.select(["nope"])


def test_flow_result_status_reads_checks_error_and_not_run() -> None:
    res = FlowResult("x", "X")
    assert res.status == "fail"  # no checks is not a pass
    res.check("a", True)
    assert res.status == "pass" and res.passed
    res.check("b", False, "why")
    assert res.status == "fail"
    res.error = "boom"
    assert res.status == "error"
    res.not_run = "cap"
    assert res.status == "not run"


def test_transcript_lines_show_taps_buttons_links_and_attachments() -> None:
    lines = flows.transcript_of(
        [
            entry("Elige", id=1, interactive=ZONE_LIST, links=[{"url": "https://x.example"}]),
            entry("Polanco", id=2, inbound=True, reply_to={"message_id": 1, "option_id": "z"}),
            entry("Listo", id=3, attachments=[ICS]),
        ]
    )
    assert lines[0].startswith("Agent: Elige [list: Centro | Polanco | Roma; link https://x")
    assert lines[1] == "Candidate (tapped): Polanco"
    assert lines[2] == "Agent: Listo [attachment ics entrevista.ics]"
    assert flows.transcript_of_messages([outbound("hola")]) == ["Agent: hola"]


def test_clip_collapses_whitespace_and_truncates() -> None:
    assert flows.clip("a \n  b") == "a b"
    clipped = flows.clip("x" * 400)
    assert len(clipped) == flows.TEXT_LIMIT and clipped.endswith("...")


def test_event_summary_counts_repeats_in_order_of_first_appearance() -> None:
    app = make_application(events=["application_started", "answer_recorded", "answer_recorded"])
    assert flows.event_summary(app) == "application_started, answer_recorded x2"


# ---- flows 6 and 7: the Jev router (ADR 0051) ---------------------------------------------

JEV = "typesafe/jev-1.13-20260917"


def jev_cost() -> dict[str, Any]:
    block = cost_block()
    block["models"] = [*block["models"], JEV]
    block["by_model"] = [
        *block["by_model"],
        {
            "model": JEV,
            "runs": 0,
            "input_tokens": 800,
            "output_tokens": 130,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "est_cost_usd": 0.0,
            "reconciled_cost_usd": 0.00006,
        },
    ]
    return block


def language_api(*, reply_language: str = "en", stored: str = "en", jev: bool = True) -> FakeApi:
    api = FakeApi()
    greeting = {"en": GREETING_EN, "es": GREETING_ES}[reply_language]

    def on_message(a: FakeApi, cid: str, text: str) -> None:
        phone = a.phones[cid]
        if cid not in a.applications_by_conv:
            app = make_application(
                f"app_{cid}",
                phone,
                stage="name",
                events=["application_started"],
                prose=None,
                cost=jev_cost() if jev else cost_block(),
            )
            app["candidate"]["language"] = stored
            a.applications[app["id"]] = app
            a.applications_by_conv[cid] = app
            a.say(cid, greeting)
            return
        a.say(cid, "Great. What is your full name?" if reply_language == "en" else GREETING_ES)

    api.on_message = on_message
    return api


def test_language_passes_when_the_opener_gets_english_and_one_word_does_not_switch() -> None:
    res, _ = play_flow(language_api(), "language", [])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    assert res.conversations == 1 and len(res.phones) == 1


def test_language_fails_on_a_spanish_reply_a_wrong_stored_language_or_no_jev_row() -> None:
    res, _ = play_flow(language_api(reply_language="es", stored="es-MX", jev=False), "language", [])
    assert set(failed(res)) >= {
        "the reply to an English opener is in English",
        "the Application language is en",
        "the Jev router served the call (a jev row in the ledger)",
        "the Application language is still en",
        "one Spanish word does not switch the reply language",
    }


ES_BUTTONS = {
    "style": "buttons",
    "options": [
        {"id": "full_time", "label": "Jornada completa"},
        {"id": "part_time", "label": "Media jornada"},
        {"id": "weekends", "label": "Fines de semana"},
    ],
}
EN_BUTTONS = {
    "style": "buttons",
    "options": [
        {"id": "full_time", "label": "Full-time"},
        {"id": "part_time", "label": "Part-time"},
        {"id": "weekends", "label": "Weekends"},
    ],
}


def language_request_api(
    *,
    stored_after: str = "en",
    reply_language: str = "en",
    buttons: dict[str, Any] = EN_BUTTONS,
    jev: bool = True,
    next_language: str = "en",
) -> FakeApi:
    """A Spain chat that reaches the availability question in Spanish, then gets
    "me preguntas en inglés porfi". The knobs are what the backend did with the request."""
    api = FakeApi()
    scripted = {
        "Hola, busco trabajo de repartidor": ("¡Hola! ¿Cómo te llamas?", None),
        "Me llamo Ramiro Salcedo": ("Gracias, Ramiro. ¿En qué zona vives?", None),
        "Vivo en Chamberí, Madrid": ("Perfecto. ¿Tienes carnet de conducir vigente?", None),
        "Sí, tengo carnet": ("Gracias. ¿Qué disponibilidad tienes?", ES_BUTTONS),
    }
    english = {"en": "Sure, I'll continue in English. What availability do you have?"}
    spanish = "Claro. ¿Qué disponibilidad tienes?"

    def on_message(a: FakeApi, cid: str, text: str) -> None:
        if cid not in a.applications_by_conv:
            app = make_application(
                f"app_{cid}",
                a.phones[cid],
                stage="name",
                events=["application_started"],
                prose=None,
                cost=jev_cost() if jev else cost_block(),
            )
            app["candidate"]["language"] = "es-ES"
            a.applications[app["id"]] = app
            a.applications_by_conv[cid] = app
        app = a.applications_by_conv[cid]
        if text in scripted:
            reply, interactive = scripted[text]
            a.say(cid, reply, interactive=interactive)
            return
        assert text == flows.LANGUAGE_REQUEST, text
        app["candidate"]["language"] = stored_after
        a.say(
            cid,
            english["en"] if reply_language == "en" else spanish,
            interactive=buttons,
        )

    def on_reply(a: FakeApi, cid: str, offer_id: int, option_id: str) -> None:
        app = a.applications_by_conv[cid]
        app["answers"] = {"availability": [option_id]}
        a.say(
            cid,
            "Great. Which schedule do you prefer?"
            if next_language == "en"
            else "Genial. ¿Qué horario prefieres?",
        )

    api.on_message = on_message
    api.on_reply = on_reply
    return api


def test_language_request_passes_when_the_reply_the_buttons_and_the_record_are_english() -> None:
    res, _ = play_flow(language_request_api(), "language_request", [])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    assert res.conversations == 1 and len(res.phones) == 1
    assert any("me preguntas en inglés porfi" in line for line in res.transcript)


def test_language_request_fails_on_the_demo_chat_where_reply_buttons_and_record_disagree() -> None:
    """The bug of 2026-10-11: English reply, Spanish buttons, record stuck on es-ES."""
    api = language_request_api(stored_after="es-ES", buttons=ES_BUTTONS)
    res, _ = play_flow(api, "language_request", [])
    assert res.status == "fail"
    assert set(failed(res)) == {
        "the Application language is en (the request, not the language it is written in)",
        "the buttons on that turn are English, as the reply and the record",
        "the Application language is still en",
    }


def test_language_request_fails_on_a_spanish_reply_or_next_question_and_no_jev_row() -> None:
    api = language_request_api(reply_language="es", next_language="es", jev=False)
    res, _ = play_flow(api, "language_request", [])
    assert set(failed(res)) >= {
        "the reply to the request is in English",
        "the Jev router served the calls (jev rows in the ledger)",
        "the next question is in English",
    }


def test_language_request_stops_when_the_availability_buttons_never_come() -> None:
    api = language_request_api()
    api.on_message = lambda a, cid, text: a.say(cid, "¿Cómo te llamas?")
    res, _ = play_flow(api, "language_request", [])
    assert failed(res) == ["the availability question comes with Spanish buttons first"]


def person_api(*, escalate: bool = True, flagged: bool = True, decided: bool = False) -> FakeApi:
    api = FakeApi()

    def on_message(a: FakeApi, cid: str, text: str) -> None:
        phone = a.phones[cid]
        if cid not in a.applications_by_conv:
            app = make_application(
                f"app_{cid}", phone, stage="name", events=["application_started"], prose=None
            )
            a.applications[app["id"]] = app
            a.applications_by_conv[cid] = app
            a.say(cid, GREETING_ES)
            return
        app = a.applications_by_conv[cid]
        if escalate:
            app["open_queue_items"] = [{"kind": "escalation", "reason": "wants_human"}]
        app["events"] += [
            {
                "id": 90,
                "kind": "tone_hint",
                "actor": "system",
                "data": {"code": "escalated", "signals": ["wants_human"], "message_id": 4},
            }
        ]
        if decided:
            app["events"].append({"id": 91, "kind": "knockout", "actor": "agent", "data": {}})
        if flagged:
            app["message_signals"] = [{"message_id": 4, "fired": ["wants_human", "frustrated"]}]
            app["communication_notes"] = [{"kind": "asked_for_person", "text": "Pidió hablar."}]
        a.say(cid, "Perdona el lío. Alguien del equipo te escribe pronto.")

    api.on_message = on_message
    return api


def test_frustrated_passes_with_the_reply_the_escalation_and_the_codes_only_event() -> None:
    res, _ = play_flow(person_api(), "frustrated", [])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    assert any(n.startswith("Note: ") for n in res.notes)


def test_frustrated_fails_without_an_escalation_flags_or_with_a_decision() -> None:
    res, _ = play_flow(person_api(escalate=False, flagged=False, decided=True), "frustrated", [])
    assert set(failed(res)) == {
        "an Escalation is open for a person (reason wants_human)",
        "the message is flagged for the Recruiter, as a flag with no score",
        "the communication notes say a person was asked for",
        "nothing about the Candidate was decided (still open, no Knockout or Rejection)",
    }


def test_frustrated_fails_when_the_event_carries_more_than_codes() -> None:
    api = person_api()
    original = api.on_message

    def with_text(a: FakeApi, cid: str, text: str) -> None:
        original(a, cid, text)
        for event in a.applications_by_conv[cid]["events"]:
            if event["kind"] == "tone_hint":
                event["data"]["text"] = "ya te dije"

    api.on_message = with_text
    res, _ = play_flow(api, "frustrated", [])
    assert failed(res) == ["a tone hint is on the record with codes only"]


# ---- flow 12: unreadable messages (ADR 0063) ---------------------------------------------


def unreadable_api(
    *, hold: bool = True, misses: int = 3, reply_in_hold: bool = False, recorded: bool = False
) -> FakeApi:
    """Three nonsense messages: the third gets the fixed message and opens the hold."""
    api = FakeApi()
    seen: dict[str, int] = {}

    def on_message(a: FakeApi, cid: str, text: str) -> None:
        phone = a.phones[cid]
        if cid not in a.applications_by_conv:
            app = make_application(
                f"app_{cid}", phone, stage="greeting", events=["application_started"], prose=None
            )
            app["control"] = "agent"
            a.applications[app["id"]] = app
            a.applications_by_conv[cid] = app
            a.say(cid, GREETING_ES)
            return
        app = a.applications_by_conv[cid]
        seen[cid] = seen.get(cid, 0) + 1
        n = seen[cid]
        if n <= min(3, misses):
            data = {"field": "full_name", "attempts": n, "reason": "unreadable"}
            app["events"].append({"id": 10 + n, "kind": "answer_invalid", "data": data})
        if n < 3:
            a.say(cid, "No logré leer tu nombre. ¿Me lo escribes?")
        elif n == 3:
            if hold:
                app["open_queue_items"] = [{"kind": "escalation", "reason": "unreadable"}]
                a.say(cid, UNREADABLE_HANDOVER["es-MX"], sender="system")
            else:
                a.say(cid, "¿Me escribes tu nombre?")
            if recorded:
                app["events"].append({"id": 30, "kind": "answer_recorded", "data": {}})
        elif reply_in_hold:
            a.say(cid, "¿Me escribes tu nombre?")

    api.on_message = on_message
    return api


@pytest.fixture
def short_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flows, "HOLD_WAIT_S", 0.2)


def test_unreadable_passes_with_the_fixed_message_the_hold_and_three_misses(short_hold) -> None:
    res, _ = play_flow(unreadable_api(), "unreadable", [])
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]


def test_unreadable_fails_without_the_hold_with_double_misses_or_a_reply_in_the_hold(
    short_hold,
) -> None:
    res, _ = play_flow(
        unreadable_api(hold=False, misses=2, reply_in_hold=True, recorded=True), "unreadable", []
    )
    assert set(failed(res)) == {
        "the third unreadable message gets the fixed message, and nothing from the agent",
        "an unreadable Escalation is open",
        "each unreadable message counted once as a miss",
        "a message while the Recruiters have the chat gets no reply",
        "the Application stays open with nothing decided or recorded",
    }


# ---- flow 8: no app (ADR 0053) -----------------------------------------------------------

PLATFORM_LIST = {
    "style": "list",
    "options": [{"id": i, "label": i} for i in ("glovo", "other", "none")],
}


def no_app_app(app_id: str, phone: str, *, platforms: Any, misses: int = 0) -> dict[str, Any]:
    return make_application(
        app_id,
        phone,
        stage="start_date",
        prose=None,
        events=[
            "application_started",
            *recorded(FIELD_ORDER[:6]),
            *[("answer_invalid", {"field": "experience_platforms"})] * misses,
            ("answer_recorded", {"field": "experience_platforms"}),
        ],
        answers={"experience_platforms": platforms},
        cost=cost_block(summary=False),
    )


def no_app_run(phone: str, *, offer: bool = True) -> tuple[RunResult, str]:
    entries = [
        entry("Hola, soy Ramiro", id=1, inbound=True, purpose="opening"),
        entry(GREETING_ES, id=2),
        entry("Por mi cuenta", id=3, inbound=True, purpose="answer:experience_platforms"),
        entry("¿Desde qué día?", id=4),
    ]
    if offer:
        entries.insert(2, entry("¿Con qué apps?", id=5, interactive=PLATFORM_LIST))
    return run_of(entries, "stopped", phone)


def no_app_play(*, first: Any = (), misses: int = 0, offer_last: bool = True) -> FlowResult:
    """The three languages; the first records `first` after `misses` misses."""
    api = FakeApi()
    runs = []
    for i in range(len(personas.NO_APP_ANSWERS)):
        phone = f"+3460000080{i}"
        platforms, missed = (list(first), misses) if i == 0 else ([], 0)
        api.applications[f"app_na_{i}"] = no_app_app(
            f"app_na_{i}", phone, platforms=platforms, misses=missed
        )
        runs.append(no_app_run(phone, offer=offer_last or i < 2))
    res, _ = play_flow(api, "no_app", runs)
    return res


def test_no_app_passes_when_each_language_records_an_empty_list_at_once() -> None:
    res = no_app_play()
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]
    assert res.conversations == 3


def test_no_app_fails_on_a_miss_or_a_value_other_than_the_empty_list() -> None:
    res = no_app_play(first=["other"], misses=1, offer_last=False)
    assert "es-MX: no app is recorded as []" in failed(res)
    assert "es-MX: no miss on the platforms" in failed(res)
    assert "en: the platforms question offered the no-app row" in failed(res)


# ---- flows 9 and 10: start dates (ADR 0054) ----------------------------------------------

SATURDAY = date(2026, 10, 10)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("proximo_domingo_mx", "2026-10-11"),
        ("este_domingo_es", "2026-10-11"),
        ("domingo_semana_mx", "2026-10-18"),
        ("manana_es", "2026-10-11"),
        ("tomorrow_en", "2026-10-11"),
        ("el_lunes_mx", "2026-10-12"),
        ("veinte_octubre_es", "2026-10-20"),
    ],
)
def test_the_expected_start_date_of_each_phrase_on_saturday_10_october(key, expected) -> None:
    reading = next(r for r in personas.DATE_READINGS if r.key == key)
    assert flows.expected_start(reading, SATURDAY).isoformat() == expected


def test_a_day_and_month_already_past_rolls_to_next_year() -> None:
    reading = next(r for r in personas.DATE_READINGS if r.key == "veinte_octubre_es")
    assert flows.expected_start(reading, date(2026, 11, 2)) == date(2027, 10, 20)


DATE_BUTTONS = {
    "style": "buttons",
    "options": [
        {"id": "2026-10-11", "label": "Domingo 11 oct"},
        {"id": "2026-10-18", "label": "Domingo 18 oct"},
    ],
}


def date_run(*, offer: bool) -> RunResult:
    entries = [
        entry("El próximo domingo", id=1, inbound=True, purpose="answer:start_date"),
        entry(
            "¿Este o el de la semana siguiente?", id=2, interactive=DATE_BUTTONS if offer else None
        ),
    ]
    return run_of(entries, "stopped", "+525500000001")[0]


def date_app(start: str | None, *, misses: int = 0) -> dict[str, Any]:
    events: list[Any] = [("answer_invalid", {"field": "start_date"})] * misses
    return make_application(answers={"start_date": start}, events=events)


def date_result(key: str, app: dict[str, Any], run: RunResult) -> FlowResult:
    res = FlowResult("x", "X")
    reading = next(r for r in personas.DATE_READINGS if r.key == key)
    flows.date_checks(res, reading, app, run, SATURDAY)
    return res


def test_an_ambiguous_phrase_passes_with_both_dates_offered_and_the_tap_recorded() -> None:
    res = date_result("proximo_domingo_mx", date_app("2026-10-18"), date_run(offer=True))
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]


def test_an_ambiguous_phrase_fails_when_nothing_is_asked_and_tomorrow_is_recorded() -> None:
    """The demo chat of 2026-10-10."""
    res = date_result("proximo_domingo_mx", date_app("2026-10-11"), date_run(offer=False))
    assert failed(res) == [
        "proximo_domingo_mx: both dates offered as buttons",
        "proximo_domingo_mx: the tapped date is recorded",
    ]


def test_a_plain_phrase_passes_with_no_question_and_fails_when_asked() -> None:
    passed = date_result("este_domingo_es", date_app("2026-10-11"), date_run(offer=False))
    assert passed.status == "pass"
    asked = date_result("este_domingo_es", date_app("2026-10-11", misses=1), date_run(offer=True))
    assert failed(asked) == [
        "este_domingo_es: no miss on the start date",
        "este_domingo_es: no date choice asked",
    ]


def test_the_start_date_flows_play_their_readings_only() -> None:
    choice = [r.key for r in personas.DATE_READINGS if r.ambiguous]
    plain = [r.key for r in personas.DATE_READINGS if not r.ambiguous]
    assert choice == ["proximo_domingo_mx", "next_sunday_en"]
    assert len(plain) == 6


# ---- flow 11: the demo chat, the Zone's country and "tomorrow" (ADR 0057) ------------------


def zone_country_app(phone: str, *, moved: bool, start: str | None) -> dict[str, Any]:
    zone: dict[str, Any] = {"field": "zone", "value": "zone_es_madrid_chamberi"}
    if moved:
        zone["country_changed"] = {"from": "MX", "to": "ES"}
    app = make_application(
        "app_zc",
        phone,
        stage="confirm",
        prose=None,
        events=[
            "application_started",
            ("answer_recorded", {"field": "full_name"}),
            ("answer_recorded", zone),
            *recorded(FIELD_ORDER[2:]),
        ],
        answers={"zone": {"name": "Chamberí", "city": "Madrid"}, "start_date": start},
        cost=cost_block(summary=False),
    )
    app["candidate"]["country"] = "MX"
    app["locations"] = [{"id": "loc_es_madrid_chamberi", "country": "ES"}]
    return app


def zone_country_run(phone: str, *, asked_again: bool = False) -> tuple[RunResult, str]:
    entries = [
        entry("Hey!", id=1, inbound=True, purpose="opening"),
        entry(GREETING_EN, id=2),
        entry("tomorrow works for me", id=3, inbound=True, purpose="answer:start_date"),
        entry("Here is a summary. Is it right?", id=4),
    ]
    if asked_again:
        again = entry("tomorrow works for me", id=6, inbound=True, purpose="answer:start_date")
        entries[3:3] = [entry("Could you give me the exact date?", id=5), again]
    return run_of(entries, "stopped", phone)


def zone_country_play(*, moved: bool, start: str | None, asked_again: bool = False) -> FlowResult:
    api = FakeApi()
    phone = "+525500000990"
    api.applications["app_zc"] = zone_country_app(phone, moved=moved, start=start)
    res, _ = play_flow(api, "zone_country", [zone_country_run(phone, asked_again=asked_again)])
    return res


def tomorrow_in_madrid() -> str:
    return (flows.local_today("ES") + timedelta(days=1)).isoformat()


def test_zone_country_passes_when_the_country_moves_and_tomorrow_is_recorded_at_once() -> None:
    res = zone_country_play(moved=True, start=tomorrow_in_madrid())
    assert res.status == "pass", [(c.name, c.detail) for c in res.checks if not c.ok]


def test_zone_country_fails_on_the_demo_chat() -> None:
    """The demo chat of 2026-10-10: country left at MX, "the exact date" asked."""
    res = zone_country_play(moved=False, start=tomorrow_in_madrid(), asked_again=True)
    assert failed(res) == ["stage confirm, open", "the start date was given once"]
    today = zone_country_play(moved=True, start=flows.local_today("ES").isoformat())
    assert failed(today) == [f"tomorrow recorded ({tomorrow_in_madrid()})"]


# ---- flow 12b: the Zone question's own word per country (ES barrio, MX colonia) ----------


def zone_wording_api(question: str) -> FakeApi:
    """A chat that greets, then asks ``question`` once it has the name."""
    api = FakeApi()
    seen: dict[str, int] = {}

    def on_message(a: FakeApi, cid: str, text: str) -> None:
        seen[cid] = seen.get(cid, 0) + 1
        if cid not in a.applications_by_conv:
            app = make_application(
                f"app_{cid}",
                a.phones[cid],
                stage="name",
                events=["application_started"],
                prose=None,
            )
            a.applications[app["id"]] = app
            a.applications_by_conv[cid] = app
        a.say(cid, GREETING_ES if seen[cid] == 1 else question)

    api.on_message = on_message
    return api


def test_zone_wording_passes_with_barrio_in_spain_and_colonia_in_mexico() -> None:
    es, _ = play_flow(
        zone_wording_api("Gracias, Lucía. ¿En qué barrio y ciudad vives?"), "zone_wording_es", []
    )
    assert es.status == "pass", [(c.name, c.detail) for c in es.checks if not c.ok]
    mx, _ = play_flow(
        zone_wording_api("Gracias, Ramiro. ¿En qué colonia y ciudad vives?"), "zone_wording_mx", []
    )
    assert mx.status == "pass", [(c.name, c.detail) for c in mx.checks if not c.ok]
    assert es.notes == ["ES Zone question: Gracias, Lucía. ¿En qué barrio y ciudad vives?"]


def test_zone_wording_fails_on_the_other_countrys_word() -> None:
    """The demo chat of 2026-10-11: a Spain Candidate asked for the colonia."""
    es, _ = play_flow(
        zone_wording_api("Gracias, Gonzalo. ¿En qué ciudad y colonia vives?"), "zone_wording_es", []
    )
    assert failed(es) == [
        "the ES Zone question says barrio",
        "the Spain Zone question never says colonia",
    ]
    mx, _ = play_flow(
        zone_wording_api("Gracias. ¿En qué barrio y ciudad vives?"), "zone_wording_mx", []
    )
    assert failed(mx) == ["the MX Zone question says colonia"]
