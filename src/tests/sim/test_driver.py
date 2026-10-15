"""Sim driver, clock helper and assertion helpers (spec 004 tasks 2 and 5; lane W2-E).

The driver runs against an in-process fake web chat server (`sim.fake_webchat`) that follows
`contracts/webchat.openapi.yaml`, so no Node, Prism, Compose or model is needed. The fake's
traffic is validated against the contract, which also checks that the driver's own requests
conform to it.
"""

from __future__ import annotations

import asyncio
import copy
import re
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator

from sim import asserts
from sim.clock import (
    SimClock,
    SimClockError,
    SimDisabledError,
    next_local_time,
)
from sim.detect import detect_prompt, guess_language, has_ai_disclosure, last_question
from sim.driver import (
    ChatError,
    ClockRequiredError,
    Entry,
    PersonaDriver,
    RunResult,
    Turn,
    WebChatClient,
    _Session,
)
from sim.fake_webchat import FakeWebChat
from sim.personas import load_personas

REPO = Path(__file__).resolve().parents[3]
PERSONAS = load_personas()
WEBCHAT = yaml.safe_load((REPO / "contracts" / "webchat.openapi.yaml").read_text("utf-8"))
API = yaml.safe_load((REPO / "contracts" / "api.openapi.yaml").read_text("utf-8"))
STUB_SCRIPT = yaml.safe_load(
    (Path(__file__).parent / "stub_llm" / "scripts" / "screener.yaml").read_text("utf-8")
)


def run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)  # type: ignore[arg-type]


def make_driver(fake: FakeWebChat, **kwargs: Any) -> tuple[PersonaDriver, httpx.AsyncClient]:
    http = fake.client()
    kwargs.setdefault("poll_wait_s", 1)
    kwargs.setdefault("settle_s", 0)
    return PersonaDriver(http, clock=SimClock(http), **kwargs), http


async def play(persona_id: str, fake: FakeWebChat | None = None, **kwargs: Any):
    fake = fake or FakeWebChat()
    driver, http = make_driver(fake, **kwargs)
    persona = PERSONAS[persona_id]
    async with http:
        result = await driver.run(persona)
    return fake, persona, result


def validator(spec: dict[str, Any], name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": spec["components"]}
    )


# ---- the two scenarios the gate names, then every seed persona -----------------------


def test_qualified_persona_runs_to_completion():
    fake, persona, result = run(play("qualified_es"))
    assert result.ended == "completed"
    detail = fake.application_detail(persona.identity.phone)
    assert detail["stage"] == "qualified" and detail["status"] == "open"
    assert detail["answers"]["full_name"] == "Ana García"
    assert asserts.recorded_order(detail) == list(persona.expect.field_order)
    asserts.assert_persona(persona, detail, run=result, tool_calls=fake.tool_calls("+34600000001"))


def test_no_license_knockout_persona_runs_to_completion():
    fake, persona, result = run(play("no_license_mx"))
    assert result.ended == "knockout"
    detail = fake.application_detail(persona.identity.phone)
    assert (detail["stage"], detail["status"], detail["outcome"]) == (
        "driver_license",
        "closed",
        "knockout",
    )
    assert detail["knockout_criterion"] == "driver_license"
    assert "review_requested" in asserts.event_kinds(detail)
    asserts.assert_persona(persona, detail, run=result)


@pytest.mark.parametrize("persona_id", sorted(PERSONAS))
def test_every_seed_persona_reaches_its_expected_end_state(persona_id):
    fake, persona, result = run(play(persona_id))
    detail = fake.application_detail(persona.identity.phone)
    failures = asserts.check_persona(
        persona, detail, run=result, tool_calls=fake.tool_calls(persona.identity.phone)
    )
    assert not failures, f"{result.ended}: {failures}"
    # Same checks with tool calls inferred from events only.
    assert not asserts.check_persona(persona, detail, run=result)


# ---- transcript, ids, options, media -------------------------------------------------------


def test_transcript_captures_both_sides_with_timings():
    _, _, result = run(play("qualified_es"))
    inbound = [e for e in result.entries if e.direction == "inbound"]
    outbound = result.agent_entries
    assert inbound[0].text.startswith("Hola, soy Ana")
    assert inbound[0].purpose == "opening" and outbound[0].sender == "agent"
    assert [t.sent for t in result.turns] == inbound
    assert all(t.latency_first_s is not None and t.latency_first_s >= 0 for t in result.turns)
    assert all(a.t <= b.t for a, b in zip(result.entries, result.entries[1:], strict=False))
    assert result.to_dict()["persona_id"] == "qualified_es"


def test_every_message_id_is_unique_and_the_multi_answer_opening_skips_ahead():
    fake, _, result = run(play("qualified_es"))
    ids = [e.message_id for e in result.entries if e.direction == "inbound"]
    assert len(ids) == len(set(ids))
    # The opening covers name, Zone and license: 1 opening + 5 answers + 1 confirm.
    assert len(ids) == 7
    posted = [x for x in fake.log if x["method"] == "POST" and x["path"].endswith("/messages")]
    assert len(posted) == 7


def test_ambiguous_zone_option_is_tapped_through_replies():
    fake, persona, result = run(play("zone_ambiguous_mx"))
    taps = [x for x in fake.log if x["path"].endswith("/replies")]
    assert len(taps) == 1 and taps[0]["status"] == 202
    assert taps[0]["request"]["option_id"] == "zone_mx_puebla_centro"
    tapped = next(e for e in result.entries if e.reply_to)
    assert tapped.text == "Centro (Puebla)"
    detail = fake.application_detail(persona.identity.phone)
    assert detail["answers"]["zone"]["city"] == "Puebla"


def test_a_zone_corrected_at_confirm_is_picked_from_the_correction_not_the_first_answer():
    fake, persona, _ = run(play("confirm_correction_pick_mx"))
    taps = [x for x in fake.log if x["path"].endswith("/replies")]
    assert [t["request"]["option_id"] for t in taps] == ["zone_mx_puebla_la_paz"]
    detail = fake.application_detail(persona.identity.phone)
    assert detail["stage"] == "qualified"
    assert detail["answers"]["zone"]["name"] == "La Paz"


def test_after_a_correction_the_field_is_answered_with_it_every_time():
    """Real Haiku re-asked the corrected Zone ("¿Cuál es tu zona?"); the driver used to send the
    persona's first Zone and tap it, undoing the correction it had just made."""
    fake = FakeWebChat()
    driver, _ = make_driver(fake)
    persona = PERSONAS["confirm_correction_mx"]
    session = _Session(driver, persona, persona.identity.phone)
    options = [
        {"id": "zone_mx_puebla_angelopolis", "label": "Angelópolis"},
        {"id": "zone_mx_puebla_centro", "label": "Centro"},
        {"id": "zone_mx_puebla_la_paz", "label": "La Paz"},
    ]
    assert session._next_reply("zone").text == "Centro, Puebla"
    assert session._zone_pick(options) == 1
    correction = session._next_reply("confirm")
    assert correction.field == "zone"
    assert session._next_reply("zone") is correction
    assert session._next_reply("zone") is correction  # however often it is asked
    assert session._zone_pick(options) == 2  # La Paz
    assert session._next_reply("full_name").text == "Diego Martínez"  # others unchanged
    assert session._next_reply("confirm").text == "sí, ahora sí"


def test_a_corrected_zone_asked_again_in_text_is_typed_as_the_zone_it_would_tap():
    """confirm_correction_pick_mx names only the city; when the agent asks for the colonia in
    text instead of offering the list, the persona names La Paz instead of looping."""
    driver, _ = make_driver(FakeWebChat())
    persona = PERSONAS["confirm_correction_pick_mx"]
    session = _Session(driver, persona, persona.identity.phone)
    correction = session._next_reply("confirm")
    asked = Entry(direction="outbound", sender="agent", text="¿En qué colonia vives?", t=0.0)
    typed = session._with_zone_choice(session._next_reply("zone"), asked)
    assert typed.text == "La Paz" and typed.pick_option is None
    listed = Entry(
        direction="outbound",
        sender="agent",
        text="¿Cuál es la tuya?",
        t=0.0,
        interactive={
            "style": "buttons",
            "options": [{"id": "zone_mx_puebla_la_paz", "label": "La Paz"}],
        },
    )
    assert session._with_zone_choice(correction, listed) is correction  # taps instead


def test_expired_option_falls_back_to_typing_the_text():
    fake = FakeWebChat()
    driver, http = make_driver(fake)

    async def options_expired(cid, in_reply_to, option_id, *, message_id=None):
        raise ChatError("expired", status=409, code="option_expired")

    driver.chat.reply = options_expired  # type: ignore[method-assign]

    async def go():
        async with http:
            return await driver.run(PERSONAS["zone_ambiguous_mx"])

    result = run(go())
    assert result.ended == "completed"
    texts = [e.text for e in result.entries if e.direction == "inbound"]
    assert "Centro, Puebla" in texts
    assert not any(e.reply_to for e in result.entries)


def test_voice_note_is_sent_as_a_media_stand_in():
    persona = PERSONAS["qualified_es"].model_copy(deep=True)
    persona.opening.media = "voice_note"
    persona.opening.text = "Hola, soy Ana García, vivo en Madrid, Chamberí y tengo carnet"
    fake = FakeWebChat()
    driver, http = make_driver(fake)

    async def go():
        async with http:
            return await driver.run(persona)

    run(go())
    first = next(x for x in fake.log if x["path"].endswith("/messages") and x["method"] == "POST")
    assert first["request"]["media"] == {"type": "audio", "filename": "nota-de-voz.ogg"}


def test_resending_a_message_id_is_a_duplicate_and_a_changed_body_conflicts():
    fake = FakeWebChat()

    async def go():
        async with fake.client() as http:
            chat = WebChatClient(http)
            conv = await chat.start("ES", phone="+34600000099", locale="es-ES")
            cid = conv["conversation_id"]
            first = await chat.send(cid, "Hola", message_id="m-1")
            again = await chat.send(cid, "Hola", message_id="m-1")
            with pytest.raises(ChatError) as err:
                await chat.send(cid, "Otra cosa", message_id="m-1")
            return first, again, err.value

    first, again, err = run(go())
    assert first["duplicate"] is False and again == {**first, "duplicate": True}
    assert err.status == 409 and err.code == "message_id_reused"


# ---- timing: long poll, typing, timeouts ----------------------------------------------------


def test_slow_agent_is_waited_for_and_its_latency_measured():
    fake = FakeWebChat(reply_delay_s=0.15)
    _, _, result = run(play("no_license_mx", fake=fake, reply_timeout_s=5))
    assert result.ended == "knockout"
    assert all(0.12 <= t.latency_first_s < 2 for t in result.turns)
    assert not asserts.check_latency(result, 2.0)
    assert asserts.check_latency(result, 0.05)  # a tighter limit fails


def test_no_reply_ends_the_run_as_a_timeout_not_a_hang():
    fake = FakeWebChat(reply_delay_s=60)
    _, _, result = run(play("qualified_es", fake=fake, reply_timeout_s=0.3))
    assert result.ended == "timeout"
    assert result.turns[0].timed_out
    assert asserts.check_latency(result)


def _scripted_pages(pages: list[dict[str, Any]]) -> httpx.AsyncClient:
    """A web chat whose reads return `pages` in order (the last one repeats)."""
    served = iter(pages)
    last: dict[str, Any] = pages[-1]

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal last
        last = next(served, last)
        return httpx.Response(200, json=last)

    return httpx.AsyncClient(base_url="http://scripted", transport=httpx.MockTransport(handler))


def _agent(mid: int, text: str) -> dict[str, Any]:
    return {
        "id": mid,
        "message_id": None,
        "direction": "outbound",
        "sender": "agent",
        "sender_label": "Sazón Empleo",
        "text": text,
        "interactive": None,
        "reply_to": None,
        "links": [],
        "attachments": [],
        "created_at": "2026-10-09T10:00:00Z",
    }


def _await(pages: list[dict[str, Any]], **kwargs: Any) -> list[str]:
    from sim.driver import Entry, Turn, _Session

    async def go() -> list[str]:
        async with _scripted_pages(pages) as http:
            kwargs.setdefault("settle_s", 0)
            driver = PersonaDriver(http, clock=None, poll_wait_s=0, **kwargs)
            session = _Session(driver, PERSONAS["qualified_es"], "+34600000000")
            session.conversation_id = "wc_" + "0" * 32
            turn = Turn(sent=Entry(direction="inbound", sender="candidate", text="hola", t=0))
            await session._await_reply(turn, session._now())
            return [e.text for e in turn.replies]

    return run(go())


def test_run_finished_waits_for_a_trailing_message_with_typing_off():
    """The race the heuristic misses: the first reply is stored, typing reads false (between
    two runs, or before the next starts), and a second message follows."""
    pages = [
        {
            "cursor": 2,
            "agent_typing": False,
            "run_finished": False,
            "messages": [_agent(2, "Uno")],
        },
        {"cursor": 2, "agent_typing": False, "run_finished": False, "messages": []},
        {
            "cursor": 3,
            "agent_typing": False,
            "run_finished": True,
            "messages": [_agent(3, "Dos")],
        },
    ]
    assert _await(pages, reply_timeout_s=5) == ["Uno", "Dos"]


def test_without_run_finished_the_driver_falls_back_to_typing_and_settle():
    pages = [
        {"cursor": 2, "agent_typing": True, "messages": [_agent(2, "Uno")]},
        {"cursor": 3, "agent_typing": False, "messages": [_agent(3, "Dos")]},
        {"cursor": 3, "agent_typing": False, "messages": []},
    ]
    assert _await(pages, reply_timeout_s=5, settle_s=0.01) == ["Uno", "Dos"]


def test_a_backend_without_run_finished_still_plays_a_persona():
    fake = FakeWebChat(run_finished=False)
    _, _, result = run(play("no_license_mx", fake=fake))
    assert result.ended == "knockout"


def test_persona_that_moves_time_needs_a_clock():
    fake = FakeWebChat()
    http = fake.client()
    driver = PersonaDriver(http, clock=None, poll_wait_s=1, settle_s=0)

    async def go():
        async with http:
            return await driver.run(PERSONAS["stalled_abandoned_es"])

    with pytest.raises(ClockRequiredError):
        run(go())


def test_stalled_path_completes_in_seconds_and_drains_nudges_into_the_transcript():
    _, _, result = run(play("stalled_abandoned_es"))
    assert result.ended == "stopped"
    steps = [s["step"] for s in result.clock_steps]
    assert steps == ["align:14:00", "1h", "19h", "28h", "5d", "1h"]
    nudges = [e for e in result.agent_entries if "interesado" in e.text]
    assert len(nudges) == 3
    assert result.elapsed_s < 5
    assert sum(s["due_jobs_run"] for s in result.clock_steps) >= 5  # stalled, 3 nudges, abandoned


def test_clock_starts_at_the_personas_local_time():
    _, _, result = run(play("stalled_abandoned_es"))
    aligned = datetime.fromisoformat(result.clock_steps[0]["now"])
    local = aligned.astimezone(ZoneInfo("Europe/Madrid"))
    assert (local.hour, local.minute) == (14, 0)


def test_shared_clock_driver_for_base_url_helper_closes_cleanly():
    fake = FakeWebChat()

    async def go():
        driver = PersonaDriver.for_base_url("http://unused")
        await driver.aclose()
        return driver

    assert run(go()).clock is not None
    assert fake is not None


# ---- clock helper --------------------------------------------------------------------------


def test_clock_state_advance_steps_reset_and_job_counts():
    fake = FakeWebChat()

    async def go():
        async with fake.client() as http:
            clock = SimClock(http)
            start = await clock.state()
            moved = await clock.advance("2h")
            steps = await clock.advance_steps(["1h", 30])
            reset = await clock.reset()
            return start, moved, steps, reset

    start, moved, steps, reset = run(go())
    assert moved.offset_seconds - start.offset_seconds == 7200
    assert [s.offset_seconds for s in steps] == [10800, 10830]
    assert reset.offset_seconds == 0


def test_clock_advance_splits_spans_over_the_contract_maximum():
    fake = FakeWebChat()

    async def go():
        async with fake.client() as http:
            clock = SimClock(http)
            state = await clock.advance(200 * 86400)
            return state, [x for x in fake.log if x["path"].endswith("/advance")]

    state, calls = run(go())
    assert state.offset_seconds == 200 * 86400
    assert len(calls) == 3 and all(c["request"]["seconds"] <= 7_776_000 for c in calls)


def test_clock_advance_to_and_local_time_alignment():
    fake = FakeWebChat()

    async def go():
        async with fake.client() as http:
            clock = SimClock(http)
            now = await clock.now()
            target = now + timedelta(hours=5)
            state = await clock.advance_to(target)
            with pytest.raises(SimClockError):
                await clock.advance_to(now)
            mx = await clock.advance_to_local_time("09:30", "MX")
            return state, target, mx

    state, target, mx = run(go())
    assert abs((state.now - target).total_seconds()) < 2
    local = mx.now.astimezone(ZoneInfo("America/Mexico_City"))
    assert (local.hour, local.minute) == (9, 30)


def test_next_local_time_rolls_to_tomorrow_when_today_has_passed():
    now = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)  # 17:00 in Madrid
    nxt = next_local_time(now, "14:00", "Europe/Madrid")
    assert nxt == datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    assert next_local_time(now, "18:00", "Europe/Madrid") == datetime(
        2026, 10, 9, 16, 0, tzinfo=UTC
    )


def test_clock_reports_sim_disabled_and_rejects_bad_input():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    disabled = FastAPI()

    @disabled.api_route("/api/sim/clock{rest:path}", methods=["GET", "POST"])
    async def nope(rest: str):
        return JSONResponse({"error": "sim_disabled", "message": "off"}, 404)

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=disabled), base_url="http://x"
        ) as http:
            clock = SimClock(http)
            with pytest.raises(SimDisabledError):
                await clock.state()
            with pytest.raises(ValueError):
                await clock.advance(0)

    run(go())


# ---- contract conformance of everything the driver and the fake exchanged --------------------

_ROUTES: list[tuple[str, re.Pattern[str], str | None, str | None]] = [
    (
        "POST",
        re.compile(r"^/api/chat/conversations$"),
        "StartConversationRequest",
        "Conversation",
    ),
    (
        "POST",
        re.compile(r"^/api/chat/conversations/[^/]+/messages$"),
        "SendMessageRequest",
        "MessageAccepted",
    ),
    (
        "GET",
        re.compile(r"^/api/chat/conversations/[^/]+/messages$"),
        None,
        "MessagePage",
    ),
    (
        "POST",
        re.compile(r"^/api/chat/conversations/[^/]+/replies$"),
        "ButtonReplyRequest",
        "MessageAccepted",
    ),
    ("GET", re.compile(r"^/api/sim/clock$"), None, "ClockState"),
    (
        "POST",
        re.compile(r"^/api/sim/clock/advance$"),
        "AdvanceClockRequest",
        "ClockState",
    ),
    ("POST", re.compile(r"^/api/sim/clock/reset$"), None, "ClockState"),
]


def check_exchanges(log: list[dict[str, Any]]) -> int:
    checked = 0
    for x in log:
        for method, pattern, req_schema, resp_schema in _ROUTES:
            if x["method"] != method or not pattern.match(x["path"]):
                continue
            if req_schema:
                validator(WEBCHAT, req_schema).validate(x["request"])
            schema = resp_schema if x["status"] < 300 else "Error"
            validator(WEBCHAT, schema).validate(x["response"])
            checked += 1
    return checked


@pytest.mark.parametrize(
    "persona_id",
    ["qualified_es", "zone_ambiguous_mx", "stalled_abandoned_es", "no_license_mx"],
)
def test_traffic_conforms_to_the_web_chat_contract(persona_id):
    fake, _, _ = run(play(persona_id))
    assert check_exchanges(fake.log) >= 6
    assert any(x["status"] == 200 and x["path"].endswith("/messages") for x in fake.log)


def test_fake_application_detail_conforms_to_the_api_contract():
    fake, persona, _ = run(play("qualified_es"))
    detail = fake.application_detail(persona.identity.phone)
    validator(API, "ApplicationDetail").validate(detail)


# ---- detection heuristics, checked against the stub model's real prompts -----------------------


def _stub_questions() -> list[tuple[str, str]]:
    out = []
    for rule in STUB_SCRIPT["rules"]:
        name = rule["name"]
        content = rule.get("respond", {}).get("content")
        if name.startswith("ra-next-") and isinstance(content, dict):
            out += [
                (name.removeprefix("ra-next-").replace("-", "_"), lang) for lang in content.values()
            ]
    return out


@pytest.mark.parametrize(("field", "text"), _stub_questions())
def test_detector_places_every_question_the_stub_model_asks(field, text):
    assert detect_prompt(text) == field


def test_detector_handles_the_greeting_summary_and_non_prompts():
    greeting = next(r for r in STUB_SCRIPT["rules"] if r["name"] == "greeting")["respond"][
        "content"
    ]
    assert detect_prompt(greeting["es"]) == "full_name"
    assert detect_prompt(greeting["en"]) == "full_name"
    assert detect_prompt("Ya tengo todo, Ana. ¿Es correcto?") == "confirm"
    assert detect_prompt("I have everything, Ana. Is that right?") == "confirm"
    # A Knockout template mentions the license but asks nothing.
    assert (
        detect_prompt("Necesitamos licencia de manejo vigente. Escríbenos cuando la tengas.")
        is None
    )
    assert detect_prompt("Hecho. Gracias por tu tiempo y mucho éxito.") is None
    assert (
        detect_prompt("No me ha quedado claro. ¿Me lo dices de otra forma, con un ejemplo?") is None
    )
    assert (
        detect_prompt(
            "Elige una opción",
            {
                "style": "buttons",
                "options": [
                    {"id": "full_time", "label": "Completo"},
                    {"id": "weekends", "label": "Finde"},
                ],
            },
        )
        == "availability"
    )
    assert (
        detect_prompt(
            "¿Qué horario?",
            {"style": "buttons", "options": [{"id": "morning", "label": "Mañana"}]},
        )
        == "preferred_schedule"
    )
    assert (
        last_question("Gracias. ¿En qué zona vives? Por ejemplo: Madrid.") == "En qué zona vives?"
    )


def test_language_and_disclosure_heuristics():
    assert guess_language("Hecho. Gracias por tu tiempo y mucho éxito.") == "es"
    assert guess_language("Done. Thanks for your time, and good luck.") == "en"
    assert guess_language("OK") is None
    assert has_ai_disclosure("¡Hola! Soy el asistente virtual de Grupo Sazón")
    assert has_ai_disclosure("Hi! I am an AI assistant")
    assert not has_ai_disclosure("Hola, soy Marta del equipo de Grupo Sazón")


# ---- assertion helpers: each check catches what it should ----------------------------------


@pytest.fixture(scope="module")
def good():
    fake, persona, result = run(play("qualified_es"))
    return persona, fake.application_detail(persona.identity.phone), result


@pytest.fixture(scope="module")
def knocked():
    fake, persona, result = run(play("no_license_mx"))
    return persona, fake.application_detail(persona.identity.phone), result


def test_wrong_stage_outcome_and_language_are_reported(good):
    persona, detail, _ = good
    bad = {**detail, "stage": "zone", "status": "closed", "outcome": "knockout"}
    failures = asserts.check_stage(bad, persona.expect)
    assert len(failures) == 3 and "stage" in failures[0]


def test_wrong_field_value_and_unexpected_answer_are_reported(good):
    persona, detail, _ = good
    bad = copy.deepcopy(detail)
    bad["answers"]["driver_license"] = False
    bad["answers"]["experience_years"] = 5
    failures = asserts.check_fields(bad, persona.expect)
    assert len(failures) == 2
    knock_persona = PERSONAS["no_license_mx"]
    leaked = copy.deepcopy(detail)
    assert asserts.check_fields(leaked, knock_persona.expect)  # availability must be empty


def test_field_order_catches_swapped_fields(good):
    persona, detail, _ = good
    bad = copy.deepcopy(detail)
    events = [e for e in bad["events"] if e["kind"] == "answer_recorded"]
    events[0]["data"]["field"], events[1]["data"]["field"] = "zone", "full_name"
    failures = asserts.check_field_order(bad, persona.expect)
    assert failures and "field order" in failures[0]


def test_field_order_falls_back_to_answer_status_when_events_name_no_field(good):
    persona, detail, _ = good
    bare = copy.deepcopy(detail)
    for event in bare["events"]:
        event["data"] = {}
    assert asserts.recorded_order(bare) == list(persona.expect.field_order)


def test_event_checks_catch_missing_forbidden_and_miscounted_events(good):
    persona, detail, _ = good
    assert not asserts.check_events(detail, persona.expect)
    missing = {
        **detail,
        "events": [e for e in detail["events"] if e["kind"] != "screening_finished"],
    }
    assert asserts.check_events(missing, persona.expect)
    extra = copy.deepcopy(detail)
    extra["events"].append(
        {
            "id": 999,
            "kind": "knockout",
            "actor": "agent",
            "user_name": None,
            "data": {},
            "created_at": "2026-10-09T10:00:00Z",
        }
    )
    assert any("forbidden knockout" in f for f in asserts.check_events(extra, persona.expect))
    nudged = copy.deepcopy(detail)
    nudged["events"].append(
        {
            "id": 1000,
            "kind": "nudge_sent",
            "actor": "system",
            "user_name": None,
            "data": {},
            "created_at": "2026-10-09T10:00:00Z",
        }
    )
    assert any("nudges" in f for f in asserts.check_events(nudged, persona.expect))


def test_tool_checks_use_explicit_calls_or_infer_from_events(good):
    persona, detail, _ = good
    assert not asserts.check_tools(detail, persona.expect)
    assert asserts.check_tools(detail, persona.expect, ["get_application"])
    forbidden = asserts.check_tools(
        detail,
        persona.expect,
        ["record_answer", "finish_screening", "raise_escalation"],
    )
    assert any("forbidden raise_escalation" in f for f in forbidden)
    assert "record_answer" in asserts.inferred_tools(detail)


def test_ai_disclosure_must_be_in_the_first_agent_message(good):
    _, detail, run_result = good
    assert not asserts.check_ai_disclosure(detail, run_result)
    bad = copy.deepcopy(detail)
    bad["messages"] = [m for m in bad["messages"] if m["direction"] == "outbound"][1:]
    assert asserts.check_ai_disclosure(bad)
    assert asserts.check_ai_disclosure(None, RunResult("p", "c", None, "completed"))


def test_knockout_template_needs_one_message_with_a_link_the_right_language_and_no_emoji(
    knocked,
):
    persona, detail, _ = knocked
    assert not asserts.check_knockout_template(detail, persona.expect)

    def knockout_event(app: dict[str, Any]) -> dict[str, Any]:
        return next(e for e in app["events"] if e["kind"] == "knockout")

    def with_body(body: str) -> dict[str, Any]:
        bad = copy.deepcopy(detail)
        reply_id = knockout_event(bad)["data"]["message_id"]
        for m in bad["messages"]:
            if m["id"] == reply_id:
                m["body"] = body
        return bad

    assert any(
        "no link" in f
        for f in asserts.check_knockout_template(with_body("Necesitamos licencia."), persona.expect)
    )
    assert any(
        "emoji" in f
        for f in asserts.check_knockout_template(
            with_body("Necesitamos licencia 🛵 https://x.example/r"), persona.expect
        )
    )
    wrong = copy.deepcopy(detail)
    knockout_event(wrong)["data"]["template"] = "knockout_driver_license.es-ES"
    assert any(
        "expected es-MX" in f for f in asserts.check_knockout_template(wrong, persona.expect)
    )
    none = copy.deepcopy(detail)
    reply_id = knockout_event(none)["data"]["message_id"]
    none["messages"] = [m for m in none["messages"] if m["id"] != reply_id]
    assert asserts.check_knockout_template(none, persona.expect)


def test_forbidden_agent_text_is_found_case_insensitively(good):
    persona, detail, run_result = good
    assert not asserts.check_forbidden_text(persona.expect, detail, run_result)
    bad = copy.deepcopy(detail)
    bad["messages"][-1]["body"] = "Esto lo hace CRIBA"
    assert asserts.check_forbidden_text(persona.expect, bad)


def test_reply_language_check_flags_a_reply_in_the_other_language():
    persona = PERSONAS["language_one_word_en"]
    _, _, result = run(play("language_one_word_en"))
    assert not asserts.check_reply_language(result, persona.expect)
    broken = copy.deepcopy(result)
    english = next(e for e in broken.agent_entries if guess_language(e.text) == "en")
    english.text = "Perfecto, gracias por la información que me has dado."
    assert asserts.check_reply_language(broken, persona.expect)
    assert not asserts.check_reply_language(broken, PERSONAS["qualified_es"].expect)


def test_country_check_follows_the_zone_and_flags_a_country_left_behind():
    """ADR 0057: the demo chat kept country MX after recording Chamberí."""
    persona = PERSONAS["zone_country_tomorrow_en"]
    fake, _, _ = run(play("zone_country_tomorrow_en"))
    app = fake.application_detail(persona.identity.phone)
    assert asserts.app_country(app) == "ES"
    assert not asserts.check_stage(app, persona.expect)
    stale = copy.deepcopy(app)
    for event in stale["events"]:
        (event.get("data") or {}).pop("country_changed", None)
    assert asserts.check_stage(stale, persona.expect) == ["country: expected 'ES', got 'MX'"]
    madrid = {"id": "loc_es_madrid_chamberi", "country": "ES"}
    puebla = {"id": "loc_mx_puebla_centro", "country": "MX"}
    assert not asserts.check_stage({**app, "locations": [madrid]}, persona.expect)
    assert asserts.check_stage({**app, "locations": [madrid, puebla]}, persona.expect) == [
        "locations outside ES: ['loc_mx_puebla_centro']"
    ]


def test_language_follow_check_flags_a_reply_in_the_wrong_language():
    persona = PERSONAS["language_switch_mx"]
    fake, _, result = run(play("language_switch_mx"))
    assert not asserts.check_language_follows(result, persona.expect)
    broken = copy.deepcopy(result)
    english = next(e for e in broken.agent_entries if guess_language(e.text) == "en")
    english.text = "Perfecto, gracias por la información que me has dado."
    assert asserts.check_language_follows(broken, persona.expect)
    assert fake is not None


def test_latency_limits_first_reply_five_seconds_later_ten():
    persona = PERSONAS["qualified_es"]

    def result_with(*latencies: float) -> RunResult:
        r = RunResult("qualified_es", "c", None, "completed")
        for i, latency in enumerate(latencies):
            from sim.driver import Entry

            sent = Entry(
                direction="inbound",
                sender="candidate",
                text="x",
                t=0.0,
                purpose=f"answer:{i}",
            )
            r.turns.append(Turn(sent=sent, latency_first_s=latency, latency_last_s=latency))
        return r

    assert not asserts.check_latency(result_with(4.9, 9.9, 1.0), persona.expect)
    assert asserts.check_latency(result_with(5.1, 1.0), persona.expect)
    assert asserts.check_latency(result_with(1.0, 10.1), persona.expect)
    assert asserts.reply_latencies(result_with(1.0, 2.0)) == [1.0, 2.0]


def test_usage_check_flags_errors_wrong_environment_and_slow_p95():
    ok = [{"status": "ok", "environment": "sim", "latency_ms": 900 + i} for i in range(20)]
    assert not asserts.check_usage(ok, max_latency_ms=10_000)
    assert asserts.check_usage([])
    assert not asserts.check_usage([], require_rows=False)
    assert asserts.check_usage([*ok, {"status": "error", "environment": "sim"}])
    assert asserts.check_usage([{"status": "ok", "environment": "prod", "latency_ms": 1}])
    assert asserts.check_usage(ok, max_latency_ms=500)
    assert asserts.percentile([1, 2, 3, 4, 100], 0.95) == 100


def test_assert_persona_raises_one_error_naming_every_failure(good):
    persona, detail, run_result = good
    bad = {**detail, "stage": "zone"}
    bad["candidate"] = {**detail["candidate"], "opted_out": True}
    with pytest.raises(AssertionError) as err:
        asserts.assert_persona(persona, bad, run_result)
    message = str(err.value)
    assert "qualified_es" in message and "stage" in message and "opted_out" in message


def test_find_application_looks_up_by_phone_and_drops_cost():
    detail = {
        "id": "app_1",
        "cost": {"est_cost_usd": 1},
        "candidate": {"phone": "+34600000001"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/applications":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": "app_0", "candidate": {"phone": "+34699999999"}},
                        {"id": "app_1", "candidate": {"phone": "+34600000001"}},
                    ],
                    "next_cursor": None,
                    "total": 2,
                },
            )
        assert request.url.path == "/api/applications/app_1"
        return httpx.Response(200, json=detail)

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://x"
        ) as http:
            found = await asserts.find_application(http, "+34600000001")
            with pytest.raises(LookupError):
                await asserts.find_application(http, "+34611111111")
            return found

    assert "cost" not in run(go())
