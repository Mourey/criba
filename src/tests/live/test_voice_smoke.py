"""The voice smoke (voice-agent acceptance-live VA5, VA6), against an in-process fake.

No test here reaches the network: the stack is `FakeVoiceApi` (an ASGI app, read through a
`TestClient` and an `ASGITransport`), the secrets are low-entropy words, and the Hermes and
database reads go through an injected runner.

Voice is its own flow (ADR 0059): a call starts on a voice conversation (`channel: voice`, `wv_`
id), and `voice-session` works on that kind only.
"""

from __future__ import annotations

import asyncio
import copy
import io
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient

from live import voice_report as vr
from live import voice_smoke as vs
from live.fake_api import FakeApi, FakeVoiceApi, cost_block, make_application
from sim.personas.voice import VoicePersona, load_voice_personas
from sim.spend import PRICES_ENV

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"
VOICE_CONTRACT = yaml.safe_load((CONTRACTS / "voice.openapi.yaml").read_text(encoding="utf-8"))
MEXICO = ZoneInfo("America/Mexico_City")
COMPLETIONS = "/api/voice/v1/chat/completions"
# Low-entropy words joined at runtime: never a literal that looks like a credential.
LLM_SECRET = "-".join(("llm", "secret", "for", "tests"))


def contract_schema(name: str) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{name}", "components": VOICE_CONTRACT["components"]}


def start(client: TestClient, channel: str | None = "voice", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "country": "ES",
        "phone": "+34600001001",
        "locale": "es-ES",
        "client": "simulator",
        **extra,
    }
    if channel is not None:
        body["channel"] = channel
    response = client.post("/api/chat/conversations", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def fake() -> tuple[FakeVoiceApi, TestClient]:
    api = FakeVoiceApi(llm_secret=LLM_SECRET)
    return api, TestClient(api.app, base_url="http://fake")


def completion_body(token: str, *utterances: str) -> dict[str, Any]:
    messages: list[dict[str, str]] = [{"role": "system", "content": "Eres un asistente."}]
    messages += [{"role": "user", "content": u} for u in utterances]
    return {
        "model": "criba-voice",
        "stream": True,
        "messages": messages,
        "elevenlabs_extra_body": {"criba_voice_token": token},
    }


def frames(text: str) -> list[str]:
    return [line[len("data:") :].strip() for line in text.splitlines() if line.startswith("data:")]


# ---- the fake's own behaviour ----------------------------------------------------------------


def test_fake_voice_conversation_has_a_wv_id() -> None:
    api, client = fake()
    voice = start(client, "voice")
    chat = start(client, "webchat")
    plain = start(client, None)
    assert voice["conversation_id"].startswith("wv_")
    assert voice["channel"] == "voice"
    assert chat["conversation_id"].startswith("conv_")
    assert plain["conversation_id"].startswith("conv_")
    assert api.messages[voice["conversation_id"]] == []


def test_fake_voice_session_only_on_a_voice_conversation() -> None:
    api, client = fake()
    voice = start(client, "voice")["conversation_id"]
    chat = start(client, "webchat")["conversation_id"]

    ok = client.post(f"/api/chat/conversations/{voice}/voice-session")
    assert ok.status_code == 201
    body = ok.json()
    assert set(body) == {"conversation_token", "voice_token", "expires_at", "country"}
    assert body["country"] == "ES"

    refused = client.post(f"/api/chat/conversations/{chat}/voice-session")
    assert refused.status_code == 409
    assert refused.json()["error"] == "voice_refused"

    missing = client.post("/api/chat/conversations/wv_unknown/voice-session")
    assert missing.status_code == 404
    assert api.sessions == [voice]


def test_fake_voice_conversation_takes_no_typed_message() -> None:
    api, client = fake()
    voice = start(client, "voice")["conversation_id"]
    chat = start(client, "webchat")["conversation_id"]
    typed = client.post(f"/api/chat/conversations/{voice}/messages", json={"text": "hola"})
    tapped = client.post(
        f"/api/chat/conversations/{voice}/replies", json={"in_reply_to": 1, "option_id": "x"}
    )
    assert typed.status_code == 404
    assert tapped.status_code == 404
    assert api.messages[voice] == []
    # A web chat conversation still takes both: the base fake is unchanged.
    assert (
        client.post(f"/api/chat/conversations/{chat}/messages", json={"text": "hola"}).status_code
        == 202
    )


def test_fake_start_validates_the_request_against_the_contract() -> None:
    _, client = fake()
    bad = client.post("/api/chat/conversations", json={"country": "FR", "channel": "voice"})
    assert bad.status_code == 422


def test_fake_completions_check_bearer_and_token() -> None:
    api, client = fake()
    cid = start(client)["conversation_id"]
    token = client.post(f"/api/chat/conversations/{cid}/voice-session").json()["voice_token"]
    body = completion_body(token, "hola")

    assert client.post(COMPLETIONS, json=body).status_code == 401
    wrong = client.post(COMPLETIONS, json=body, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    unminted = client.post(
        COMPLETIONS,
        json=completion_body("a-token-no-session-minted", "hola"),
        headers={"Authorization": f"Bearer {LLM_SECRET}"},
    )
    assert unminted.status_code == 401
    assert api.completion_requests == 0

    ok = client.post(COMPLETIONS, json=body, headers={"Authorization": f"Bearer {LLM_SECRET}"})
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("text/event-stream")
    assert api.completion_requests == 1


def test_fake_completions_stream_ends_with_done_and_frames_match_the_contract() -> None:
    api, client = fake()
    api.replies.append("Gracias. ¿Cómo te llamas?")
    cid = start(client)["conversation_id"]
    token = client.post(f"/api/chat/conversations/{cid}/voice-session").json()["voice_token"]
    response = client.post(
        COMPLETIONS,
        json=completion_body(token, "hola"),
        headers={"Authorization": f"Bearer {LLM_SECRET}"},
    )
    data = frames(response.text)
    assert data[-1] == "[DONE]"
    chunks = [json.loads(d) for d in data[:-1]]
    for chunk in chunks:
        jsonschema.Draft202012Validator(contract_schema("ChatCompletionChunk")).validate(chunk)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    spoken = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert spoken == "Gracias. ¿Cómo te llamas?"
    # The spoken turn and the reply are on the voice conversation's transcript.
    thread = api.messages[cid]
    assert [m["direction"] for m in thread] == ["inbound", "outbound"]
    assert thread[0]["text"] == "hola"
    assert thread[1]["text"] == "Gracias. ¿Cómo te llamas?"


def test_fake_completions_can_split_a_reply_across_deltas() -> None:
    api, client = fake()
    api.replies.append(["Entra en https://exa", "mple.com/book ", "para reservar."])
    cid = start(client)["conversation_id"]
    token = client.post(f"/api/chat/conversations/{cid}/voice-session").json()["voice_token"]
    response = client.post(
        COMPLETIONS,
        json=completion_body(token, "hola"),
        headers={"Authorization": f"Bearer {LLM_SECRET}"},
    )
    contents = [
        json.loads(d)["choices"][0]["delta"].get("content")
        for d in frames(response.text)[:-1]
        if json.loads(d)["choices"][0]["delta"].get("content")
    ]
    assert contents == ["Entra en https://exa", "mple.com/book ", "para reservar."]


def test_fake_completions_body_is_validated_against_the_contract() -> None:
    api, client = fake()
    cid = start(client)["conversation_id"]
    token = client.post(f"/api/chat/conversations/{cid}/voice-session").json()["voice_token"]
    headers = {"Authorization": f"Bearer {LLM_SECRET}"}

    no_messages = completion_body(token, "hola")
    del no_messages["messages"]
    empty_token = completion_body("", "hola")
    no_extra = completion_body(token, "hola")
    del no_extra["elevenlabs_extra_body"]
    for body in (no_messages, empty_token, no_extra):
        response = client.post(COMPLETIONS, json=body, headers=headers)
        assert response.status_code in (401, 422), response.text
    assert api.completion_requests == 0
    assert client.post(COMPLETIONS, json=no_messages, headers=headers).status_code == 422

    # A body the contract accepts is recorded as it came.
    good = completion_body(token, "hola")
    assert client.post(COMPLETIONS, json=good, headers=headers).status_code == 200
    assert api.completion_bodies == [good]
    jsonschema.Draft202012Validator(contract_schema("VoiceChatCompletionRequest")).validate(good)


@pytest.mark.parametrize("path", ["/api/chat/conversations/wv_unknown/voice-session"])
def test_fake_voice_session_unknown_is_a_not_found_error_body(path: str) -> None:
    _, client = fake()
    response = client.post(path)
    assert response.json()["error"] == "not_found"


# ======================================================================================
# The smoke itself (VA5, VA6)
# ======================================================================================

NAME_Q = "Gracias, Marta. ¿En qué zona vives?"
ASK_NAME = "¡Hola! Soy un asistente de IA. ¿Cómo te llamas?"
ASK_TOLEDO = "¿Vives en Toledo?"
KNOCKOUT = "Lo siento, no cubrimos Toledo. Gracias por tu interés."
YES_NO = {
    "style": "buttons",
    "options": [{"id": "zone_es_toledo", "label": "Sí, vivo ahí"}, {"id": "no", "label": "No"}],
}
TOLEDO_SCRIPT: list[Any] = [
    ASK_NAME,
    NAME_Q,
    {"text": ASK_TOLEDO, "interactive": YES_NO},
    KNOCKOUT,
]

T0 = datetime(2026, 10, 11, 20, 0, 0, tzinfo=UTC)  # 14:00 in Mexico City
IN_WINDOW = T0
SEED = "seed-password-for-tests"


class StepClock:
    """A clock that moves ten seconds each time it is read."""

    def __init__(self, start: datetime = T0, step_s: float = 10.0) -> None:
        self.at = start - timedelta(seconds=step_s)
        self.step = timedelta(seconds=step_s)
        self.reads = 0

    def __call__(self) -> datetime:
        self.at += self.step
        self.reads += 1
        return self.at


def voice_persona(persona_id: str = "zone_outside_es", **over: Any) -> VoicePersona:
    data = next(p for p in load_voice_personas() if p.id == "zone_outside_es").model_dump(
        exclude_unset=True
    )
    data.update(over)
    data["id"] = persona_id
    return VoicePersona.model_validate(data)


def two_personas() -> list[VoicePersona]:
    return [
        voice_persona("zone_outside_es"),
        voice_persona("zone_outside_es_b", phone="+34600001998"),
    ]


class Harness:
    """A fake stack, the secret files and the injected dependencies for one smoke run."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        sim: bool = False,
        cap: str | None = "3",
        now: datetime = IN_WINDOW,
    ) -> None:
        self.api = FakeVoiceApi(sim=sim, password=SEED, llm_secret=LLM_SECRET)
        self.api.replies.extend(copy.deepcopy(TOLEDO_SCRIPT) + copy.deepcopy(TOLEDO_SCRIPT))
        self.tmp = tmp_path
        self.llm_file = tmp_path / "llm_secret.txt"
        self.llm_file.write_text(LLM_SECRET + "\n", encoding="utf-8")
        self.password_file = tmp_path / "seed_password.txt"
        self.password_file.write_text(SEED + "\n", encoding="utf-8")
        self.ledger = tmp_path / "ledger.json"
        for name in ("CRIBA_REAL_SPEND_CAP_USD", "CRIBA_REAL_SPEND_LEDGER", PRICES_ENV):
            monkeypatch.delenv(name, raising=False)
        self.env: dict[str, str] = {"CRIBA_REAL_SPEND_LEDGER": str(self.ledger)}
        if cap is not None:
            self.env["CRIBA_REAL_SPEND_CAP_USD"] = cap
        self.checks: list[vs.AgentsCheck] = []
        self.agents_exit = 0
        self.now = now
        self.personas = two_personas()
        self.reader: vs.RunsReader | None = None
        self.turn_clock = StepClock()
        self.git = ("abc1234", False)

    def seed_apps(self, **cost: Any) -> None:
        for index, persona in enumerate(self.personas, start=1):
            self.api.applications[f"app_{index}"] = make_application(
                f"app_{index}",
                persona.phone,
                stage="knockout",
                cost=cost_block(**cost) if cost else None,
            )

    def deps(self) -> vs.Deps:
        def check() -> vs.AgentsCheck:
            result = vs.AgentsCheck(exit_code=self.agents_exit, at=T0)
            self.checks.append(result)
            return result

        api = self.api
        return vs.Deps(
            env=self.env,
            now=lambda: self.now,
            turn_clock=self.turn_clock,
            check_agents=check,
            runs_reader=self.reader,
            sync_client=lambda base_url: TestClient(api.app, base_url=base_url),
            async_client=lambda base_url: httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api.app), base_url=base_url
            ),
            phone_for=lambda persona: persona.phone,
            load_personas=lambda: list(self.personas),
            git_state=lambda: self.git,
        )

    def argv(self, *extra: str) -> list[str]:
        return [
            "--base-url",
            "http://fake",
            "--out",
            str(self.tmp / "voice-acceptance.md"),
            "--llm-secret-file",
            str(self.llm_file),
            "--seed-password-file",
            str(self.password_file),
            *extra,
        ]

    @property
    def report(self) -> Path:
        return self.tmp / "voice-acceptance.md"

    def run(self, *extra: str) -> int:
        return vs.main(self.argv(*extra), self.deps())

    def endpoint_untouched(self) -> bool:
        return (
            self.api.completion_requests == 0
            and self.api.sessions == []
            and not [c for c in self.api.messages if c.startswith(("wv_", "conv_"))]
        )


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(tmp_path, monkeypatch)


# ---- VA5: opt in twice, the refusals ----------------------------------------------------------


def refuse_no_cap(h: Harness) -> None:
    h.env.pop("CRIBA_REAL_SPEND_CAP_USD")


def refuse_zero_cap(h: Harness) -> None:
    h.env["CRIBA_REAL_SPEND_CAP_USD"] = "0"


def refuse_sim_stack(h: Harness) -> None:
    h.api.sim = True


def refuse_before_window(h: Harness) -> None:
    h.now = datetime(2026, 10, 11, 8, 59, tzinfo=MEXICO)


def refuse_at_window_close(h: Harness) -> None:
    h.now = datetime(2026, 10, 11, 22, 0, tzinfo=MEXICO)


def refuse_check_drift(h: Harness) -> None:
    h.agents_exit = 1


def refuse_check_cannot_decide(h: Harness) -> None:
    h.agents_exit = 2


@pytest.mark.parametrize(
    "refusal",
    [
        refuse_no_cap,
        refuse_zero_cap,
        refuse_sim_stack,
        refuse_before_window,
        refuse_at_window_close,
        refuse_check_drift,
        refuse_check_cannot_decide,
    ],
)
def test_each_refusal_exits_2_with_no_endpoint_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: Any
) -> None:
    h = Harness(tmp_path, monkeypatch)
    refusal(h)
    assert h.run() == 2
    assert h.endpoint_untouched()
    assert not h.report.exists()


def test_the_window_is_09_to_22_mexico_city() -> None:
    for hour, minute, allowed in ((9, 0, True), (21, 59, True), (8, 59, False), (22, 0, False)):
        assert vs.in_window(datetime(2026, 10, 11, hour, minute, tzinfo=MEXICO)) is allowed
    # Judged in Mexico City: 03:30 UTC is 21:30 there, 04:30 is 22:30, 14:59 is 08:59.
    assert vs.in_window(datetime(2026, 10, 12, 3, 30, tzinfo=UTC)) is True
    assert vs.in_window(datetime(2026, 10, 12, 4, 30, tzinfo=UTC)) is False
    assert vs.in_window(datetime(2026, 10, 11, 14, 59, tzinfo=UTC)) is False


def test_refusals_are_checked_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    h = Harness(tmp_path, monkeypatch, sim=True)
    refuse_no_cap(h)
    refuse_before_window(h)
    refuse_check_drift(h)
    assert h.run() == 2
    assert "CRIBA_REAL_SPEND_CAP_USD" in capsys.readouterr().err
    assert h.checks == []  # the later checks did not run

    h = Harness(tmp_path, monkeypatch, sim=True)
    refuse_before_window(h)
    refuse_check_drift(h)
    assert h.run() == 2
    assert "sim stack" in capsys.readouterr().err

    h = Harness(tmp_path, monkeypatch)
    refuse_before_window(h)
    refuse_check_drift(h)
    assert h.run() == 2
    assert "09:00" in capsys.readouterr().err
    assert h.checks == []

    h = Harness(tmp_path, monkeypatch)
    refuse_check_drift(h)
    assert h.run() == 2
    assert "voice_agents.py --check" in capsys.readouterr().err
    assert len(h.checks) == 1


def test_a_refusal_for_an_unknown_persona_and_a_forbidden_out_file(harness: Harness) -> None:
    assert harness.run("--personas", "nobody") == 2
    assert harness.endpoint_untouched()
    argv = harness.argv()
    argv[argv.index("--out") + 1] = str(harness.tmp / "live-smoke.md")
    assert vs.main(argv, harness.deps()) == 2
    assert harness.endpoint_untouched()


def test_a_bad_login_is_a_refusal(harness: Harness) -> None:
    harness.password_file.write_text("not-the-password\n", encoding="utf-8")
    assert harness.run() == 2
    assert harness.endpoint_untouched()


def test_the_agents_check_gets_only_the_variables_it_reads(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def runner(argv: list[str], env: dict[str, str]) -> int:
        seen["argv"] = argv
        seen["env"] = env
        return 0

    env = {
        "CRIBA_VOICE_AGENT_ES": "agent-es",
        "CRIBA_VOICE_AGENT_MX": "agent-mx",
        "CRIBA_VOICE_PUBLIC_BASE_URL": "https://example.test",
        "CRIBA_REAL_SPEND_CAP_USD": "3",
        "CRIBA_E2E_SEED_PASSWORD": SEED,
        "PATH": "/usr/bin",
        "HOME": "/home/test",
        "TELEGRAM_BOT_TOKEN": "must-not-travel",
    }
    result = vs.run_agents_check(env, runner=runner, now=lambda: T0)
    assert result == vs.AgentsCheck(exit_code=0, at=T0)
    assert seen["argv"][-1] == "--check"
    assert seen["argv"][-2].endswith("voice_agents.py")
    passed = seen["env"]
    assert passed["CRIBA_VOICE_AGENT_ES"] == "agent-es"
    assert passed["CRIBA_VOICE_AGENT_MX"] == "agent-mx"
    assert passed["CRIBA_VOICE_PUBLIC_BASE_URL"] == "https://example.test"
    # The key files default to this checkout's secrets; nothing else travels.
    assert passed["CRIBA_ELEVENLABS_API_KEY_FILE"].endswith("secrets/elevenlabs_api_key.txt")
    assert passed["CRIBA_VOICE_LLM_SECRET_FILE"].endswith("secrets/criba_voice_llm_secret.txt")
    for leaked in ("CRIBA_REAL_SPEND_CAP_USD", "CRIBA_E2E_SEED_PASSWORD", "TELEGRAM_BOT_TOKEN"):
        assert leaked not in passed


# ---- VA5: each persona in its own voice conversation, in order --------------------------------


def test_personas_run_in_their_own_session_in_order(harness: Harness) -> None:
    harness.seed_apps()
    assert harness.run() == 0
    api = harness.api
    assert len(api.sessions) == 2
    assert len(set(api.sessions)) == 2
    assert all(cid.startswith("wv_") for cid in api.sessions)
    assert api.sessions == sorted(api.sessions)  # started in order
    first, second = api.sessions
    # Each conversation holds exactly its own persona's spoken turns and the replies to them.
    for cid in (first, second):
        thread = api.messages[cid]
        assert [m["direction"] for m in thread] == ["inbound", "outbound"] * 4
    assert api.completion_requests == 8
    # The first persona finished before the second one started.
    assert [
        body["elevenlabs_extra_body"]["criba_voice_token"] for body in api.completion_bodies
    ] == (
        [api.voice_tokens_by_conversation(first)] * 4
        + [api.voice_tokens_by_conversation(second)] * 4
    )
    text = harness.report.read_text(encoding="utf-8")
    assert text.index("zone_outside_es ") < text.index("zone_outside_es_b")


def test_a_persona_starts_a_voice_conversation_not_a_chat(harness: Harness) -> None:
    harness.seed_apps()
    assert harness.run("--personas", "zone_outside_es") == 0
    assert [cid for cid in harness.api.messages if cid.startswith("wv_")]
    assert not [cid for cid in harness.api.messages if not cid.startswith("wv_")]
    assert harness.api.sessions == [next(iter(harness.api.messages))]


def test_the_personas_option_picks_and_orders(harness: Harness) -> None:
    harness.seed_apps()
    assert harness.run("--personas", "zone_outside_es_b") == 0
    assert len(harness.api.sessions) == 1
    text = harness.report.read_text(encoding="utf-8")
    assert "zone_outside_es_b" in text
    assert "| zone_outside_es |" not in text


# ---- VA5: the spend rule ----------------------------------------------------------------------


def test_spend_cap_stops_before_a_persona(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(tmp_path, monkeypatch, cap="0.08")
    h.seed_apps(haiku_in=400_000)  # the first persona costs about $0.048
    assert h.run() == 0
    assert len(h.api.sessions) == 1  # the second persona was projected over the cap
    text = h.report.read_text(encoding="utf-8")
    assert "not run" in text.lower()
    assert "spend cap" in text
    assert "zone_outside_es_b" in text
    assert h.ledger.exists()  # the spend is saved for the next command


def test_a_persona_the_first_projection_cannot_take_is_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path, monkeypatch, cap="0.01")  # under the first projection
    h.seed_apps()
    assert h.run() == 0
    assert h.api.sessions == []
    assert "not run" in h.report.read_text(encoding="utf-8").lower()


# ---- VA6: the per-turn record -----------------------------------------------------------------


def runs_for(starts: list[datetime]) -> vs.RunsReading:
    """One run in each of turns 1 to 3 (not turn 4: the Knockout is written by code)."""
    return vs.RunsReading(
        runs=[
            vs.RunInfo("run_1", starts[0] + timedelta(seconds=4), 1200, ["get_application"]),
            vs.RunInfo("run_2", starts[1] + timedelta(seconds=4), 1500, ["record_answer"]),
            vs.RunInfo(
                "run_3", starts[2] + timedelta(seconds=4), 1800, ["search_zones", "record_answer"]
            ),
        ]
    )


def play(h: Harness) -> vs.SmokeResult:
    """The whole smoke, as `main` runs it, with its results."""
    args = vs.build_parser().parse_args(h.argv())
    return asyncio.run(vs.smoke(args, h.deps()))


def play_one(h: Harness) -> list[vs.PersonaResult]:
    h.seed_apps()
    h.personas = h.personas[:1]
    outcome = play(h)
    assert outcome.exit_code == 0
    return outcome.results


def test_turn_record(harness: Harness) -> None:
    starts = [T0 + timedelta(seconds=20 * k) for k in range(4)]
    seen_apps: list[str] = []

    def reader(application_id: str) -> vs.RunsReading:
        seen_apps.append(application_id)
        return runs_for(starts)

    harness.reader = reader
    (result,) = play_one(harness)
    assert seen_apps == ["app_1"]
    assert result.ended == "knockout"
    assert [t.number for t in result.turns] == [1, 2, 3, 4]
    for turn in result.turns:
        assert 0 < turn.first_byte_s <= turn.last_byte_s
    assert [t.tools for t in result.turns] == [
        ["get_application"],
        ["record_answer"],
        ["search_zones", "record_answer"],
        [],  # no run: the Knockout text is written by code
    ]
    assert [t.get_application for t in result.turns] == [True, False, False, False]
    assert [t.input_tokens for t in result.turns] == [1200, 1500, 1800, None]
    assert [t.runs for t in result.turns] == [1, 1, 1, 0]
    assert result.turns[1].reply == NAME_Q
    assert result.stage == "knockout"
    assert result.charge is not None

    text = harness.report.read_text(encoding="utf-8")
    assert "get_application" in text
    assert "no run" in text
    assert "1800" in text


def test_tool_calls_unavailable_when_events_are_gone(harness: Harness) -> None:
    def reader(application_id: str) -> vs.RunsReading:
        runs = runs_for([T0 + timedelta(seconds=20 * k) for k in range(4)]).runs
        return vs.RunsReading(
            runs=[vs.RunInfo(r.run_id, r.ts, r.input_tokens, None) for r in runs],
            note="Hermes no longer holds the run events",
        )

    harness.reader = reader
    (result,) = play_one(harness)
    assert [t.tools for t in result.turns][:3] == [None, None, None]
    assert [t.get_application for t in result.turns][:3] == [None, None, None]
    assert [t.input_tokens for t in result.turns][:3] == [1200, 1500, 1800]
    text = harness.report.read_text(encoding="utf-8")
    assert "tool calls: unavailable" in text
    assert "Hermes no longer holds the run events" in text


def test_tool_calls_unavailable_without_a_reader(harness: Harness) -> None:
    (result,) = play_one(harness)
    assert all(t.tools is None and t.input_tokens is None for t in result.turns)
    assert "tool calls: unavailable" in harness.report.read_text(encoding="utf-8")


def test_a_failing_reader_does_not_stop_the_run_or_leak_its_message(harness: Harness) -> None:
    def reader(application_id: str) -> vs.RunsReading:
        raise RuntimeError(f"docker said {LLM_SECRET} went wrong")

    harness.reader = reader
    (result,) = play_one(harness)
    assert len(result.turns) == 4
    text = harness.report.read_text(encoding="utf-8")
    assert "tool calls: unavailable" in text
    assert LLM_SECRET not in text


def test_a_reader_that_could_not_read_leaves_every_turn_unknown_not_empty(
    harness: Harness,
) -> None:
    """`docker compose exec` failing (a wrong project) is not "no run": no turn may say so."""

    def reader(application_id: str) -> vs.RunsReading:
        return vs.RunsReading(note="could not read the stack's usage rows: exit 1", failed=True)

    harness.reader = reader
    (result,) = play_one(harness)
    assert [t.tools for t in result.turns] == [None] * 4
    assert [t.get_application for t in result.turns] == [None] * 4
    assert [t.input_tokens for t in result.turns] == [None] * 4
    text = harness.report.read_text(encoding="utf-8")
    assert "no run" not in text
    assert "read from the voice profile's usage rows and the Hermes run events" not in text
    assert "tool calls: unavailable" in text


def test_runs_are_assigned_to_the_turn_whose_window_holds_them() -> None:
    def at(s: float) -> datetime:
        return T0 + timedelta(seconds=s)

    windows = [(at(0), at(10)), (at(10.3), at(20)), (at(20.4), at(30))]
    runs = [
        vs.RunInfo("a", at(3), 1, ["x"]),
        # The usage row lands a moment before the next utterance starts, not after it.
        vs.RunInfo("b", at(10.1), 2, ["y"]),
        vs.RunInfo("c", at(13), 3, ["z"]),
    ]
    assigned = vs.assign_runs(windows, runs)
    assert [[r.run_id for r in rs] for rs in assigned] == [["a", "b"], ["c"], []]
    # A run stamped a little before the first turn (clock drift) still belongs to it.
    early = vs.assign_runs(windows, [vs.RunInfo("e", at(-1), 1, [])])
    assert [r.run_id for r in early[0]] == ["e"]
    # One far from every turn is left out rather than guessed.
    far = vs.assign_runs(windows, [vs.RunInfo("f", at(500), 1, [])])
    assert far == [[], [], []]


def test_a_split_url_is_flagged(harness: Harness) -> None:
    harness.api.replies.clear()
    harness.api.replies.extend(
        [
            ASK_NAME,
            [
                "Gracias, Marta. Entra en https://exa",
                "mple.com/book para reservar. ¿En qué zona vives?",
            ],
            {"text": ASK_TOLEDO, "interactive": YES_NO},
            KNOCKOUT,
        ]
    )
    (result,) = play_one(harness)
    assert [t.url for t in result.turns] == [False, True, False, False]
    assert "https://example.com" not in "".join(t.reply for t in result.turns[:1])
    assert "URL" in harness.report.read_text(encoding="utf-8")


def test_leak_flags() -> None:
    assert vs.mentions_url("mira https://criba.work/book/abc")
    assert vs.mentions_url("entra en criba.work/book/x")
    assert vs.mentions_url("www.criba.work")
    assert vs.mentions_url("[este enlace](https://criba.work/book/abc)")
    assert vs.mentions_url("la invitación invite.ics")
    assert vs.mentions_url("ve a ejemplo.com ahora")
    assert not vs.mentions_url("Te espero a las 5 p. m. en el local.")
    assert not vs.mentions_url("Pagan 1.200 € al mes.")
    assert not vs.mentions_url("Moto, bici, etc. lo que tengas.")
    token = ".".join(("a" * 20, "b" * 20))
    assert vs.mentions_booking_token(f"el código {token}")
    assert not vs.mentions_booking_token("el código de la reserva")


def test_postcode_read_back() -> None:
    persona = voice_persona()
    expected = vs.expected_for(persona, "veintiocho cero diez")
    assert expected.postcode == "28010"
    assert vs.read_back(expected, "Perfecto, el código postal veintiocho cero diez, ¿verdad?") == {
        "postcode": True
    }
    assert vs.read_back(expected, "Perfecto, el 28010.") == {"postcode": True}
    assert vs.read_back(expected, "Perfecto, ¿en qué zona vives?") == {"postcode": False}
    assert vs.read_back(vs.Expected(), "cualquier cosa") == {}


def test_name_and_zone_read_back_follow_the_persona() -> None:
    persona = voice_persona()
    name = vs.expected_for(persona, "Marta Ruiz")
    assert name.name == "Marta"
    assert vs.read_back(name, "Gracias, Marta.") == {"name": True}
    assert vs.read_back(name, "Gracias.") == {"name": False}
    zone = vs.expected_for(persona, "Vivo en Toledo")
    assert zone.zone == "Toledo"
    assert vs.read_back(zone, "¿Vives en Toledo?") == {"zone": True}
    assert vs.read_back(zone, "¿Y en qué barrio?") == {"zone": False}
    nothing = vs.expected_for(persona, "Sí, vivo ahí")
    assert nothing == vs.Expected()


def test_slot_read_back_needs_day_and_hour_only() -> None:
    persona = voice_persona()
    expected = vs.expected_for(persona, "El martes a las diez de la mañana")
    assert expected.slot == ("martes", 10)
    assert vs.read_back(expected, "Te espero el martes a las diez en el local.") == {"slot": True}
    assert vs.read_back(expected, "Quedamos el martes a las 10:00.") == {"slot": True}
    assert vs.read_back(expected, "Quedamos el martes, sin hora.") == {"slot": False}
    assert vs.read_back(expected, "Quedamos a las diez.") == {"slot": False}
    afternoon = vs.expected_for(persona, "El jueves a las cuatro de la tarde")
    assert afternoon.slot == ("jueves", 16)
    assert vs.read_back(afternoon, "El jueves a las cuatro, perfecto.") == {"slot": True}
    assert vs.read_back(afternoon, "El jueves a las 16:00.") == {"slot": True}


def test_booking_after_the_approval_reads_the_slot_back(harness: Harness) -> None:
    persona = voice_persona(booking={})
    harness.personas = [persona]
    api = harness.api
    api.applications["app_1"] = make_application("app_1", persona.phone, stage="qualified")
    api.queue_pages = [[{"id": "q_1", "kind": "approval", "application": {"id": "app_1"}}]]
    slots = {
        "text": "Elige un horario",
        "interactive": {
            "style": "list",
            "options": [{"id": "so_1", "label": "mar 14 · 10:00"}],
        },
    }
    ics = {"text": "Listo", "attachments": [{"type": "ics", "url": "/files/a.ics"}]}

    def on_approve(a: FakeApi, item_id: str) -> None:
        assert isinstance(a, FakeVoiceApi)
        a.say(a.sessions[0], slots["text"], interactive=slots["interactive"])

    api.on_approve = on_approve

    def reply_for(a: FakeVoiceApi, cid: str, messages: list[dict[str, Any]]) -> Any:
        said = [m for m in messages if m["role"] == "user"][-1]["content"]
        if said.startswith("El martes"):
            a.say(cid, ics["text"], attachments=ics["attachments"])
            return "Quedamos el martes a las diez en el local."
        return a.replies.pop(0)

    api.reply_for = reply_for
    outcome = play(harness)
    assert outcome.exit_code == 0
    (result,) = outcome.results
    assert result.approved
    assert result.ended == "booked"
    slot_turn = result.turns[-1]
    assert slot_turn.utterance == "El martes a las diez de la mañana"
    assert slot_turn.read_back == {"slot": True}
    assert api.approvals == ["q_1"]


def test_booking_without_an_open_approval_is_noted_not_fatal(harness: Harness) -> None:
    persona = voice_persona(booking={})
    harness.personas = [persona]
    harness.api.applications["app_1"] = make_application("app_1", persona.phone, stage="qualified")
    outcome = play(harness)
    assert outcome.exit_code == 0
    (result,) = outcome.results
    assert not result.approved
    assert result.notes[0].startswith("booking: no Approval item")
    assert "booking: no Approval item" in harness.report.read_text(encoding="utf-8")


# ---- VA7 and sharp-edges: the report and no secret ---------------------------------------------


def test_the_report_holds_the_header_and_the_smoke_and_keeps_other_sections(
    harness: Harness,
) -> None:
    harness.seed_apps()
    stamp = vr.RunStamp("2026-10-01 10:00", "old0000", False)
    harness.report.write_text(
        vr.update_report(
            None, {"speech": vr.section_body("Speech", stamp, ["owner-held speech block"])}
        ),
        encoding="utf-8",
    )
    before = harness.report.read_text(encoding="utf-8")
    harness.env["CRIBA_VOICE_MAX_CONCURRENT"] = "20"
    harness.env["CRIBA_VOICE_DAILY_MINUTES"] = "600"
    assert harness.run("--note", "canary run") == 0
    text = harness.report.read_text(encoding="utf-8")
    speech_block = before[before.index(vr.start_marker("speech")) :]
    assert speech_block.strip() in text
    for section_id in ("header", "smoke"):
        assert vr.start_marker(section_id) in text
        assert vr.end_marker(section_id) in text
    assert text.index(vr.start_marker("header")) < text.index(vr.start_marker("smoke"))
    assert text.index(vr.start_marker("smoke")) < text.index(vr.start_marker("speech"))
    for needle in ("abc1234", "http://fake", "exit 0", "600", "canary run"):
        assert needle in text, needle
    assert "CRIBA_VOICE_MAX_CONCURRENT" in text
    assert "real-model spend" in text
    assert "p50" in text
    assert "live-smoke.md" not in text


def test_no_secret_in_report_or_logs(
    harness: Harness, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.seed_apps()

    def reader(application_id: str) -> vs.RunsReading:
        raise RuntimeError(f"Authorization: Bearer {LLM_SECRET}")

    harness.reader = reader
    caplog.set_level(logging.DEBUG)
    assert harness.run() == 0
    out = capsys.readouterr()
    everything = "\n".join(
        [
            harness.report.read_text(encoding="utf-8"),
            caplog.text,
            out.out,
            out.err,
        ]
    )
    tokens = list(harness.api.voice_tokens)
    assert tokens
    for secret in (LLM_SECRET, SEED, *tokens):
        assert secret not in everything
    assert "Authorization" not in harness.report.read_text(encoding="utf-8")


def test_an_error_stops_the_run_and_is_reported(harness: Harness) -> None:
    harness.seed_apps()
    harness.api.session_status = 503
    assert harness.run() == 1
    text = harness.report.read_text(encoding="utf-8")
    assert "voice_unavailable" in text
    assert "not run" in text.lower()  # the second persona never started
    assert harness.api.completion_requests == 0


# ---- the read-only reader ----------------------------------------------------------------------


def test_runs_script_reads_the_usage_rows_and_tool_names(tmp_path: Path) -> None:
    schema = (CONTRACTS / "schema.sql").read_text(encoding="utf-8")
    db = tmp_path / "criba.db"
    conn = sqlite3.connect(db)
    conn.executescript(schema)
    conn.execute("PRAGMA foreign_keys = OFF")
    insert = (
        "INSERT INTO usage (ts, environment, application_id, profile, run_id, kind, provider,"
        " model, input_tokens) VALUES (?, 'prod', ?, ?, ?, ?, 'openrouter', 'm', ?)"
    )
    rows = [
        ("2026-10-11T20:00:04Z", "app_1", "criba-recruiter-voice", "run_a", "run", 1200),
        ("2026-10-11T20:00:14Z", "app_1", "criba-recruiter-voice", "run_b", "run", 1500),
        ("2026-10-11T20:00:15Z", "app_1", "criba-recruiter-voice", "run_b", "auxiliary", 99),
        ("2026-10-11T20:00:16Z", "app_1", "criba-recruiter", "run_text", "run", 7),
        ("2026-10-11T20:00:17Z", "app_2", "criba-recruiter-voice", "run_other", "run", 8),
    ]
    for row in rows:
        conn.execute(insert, row)
    conn.commit()
    conn.close()

    namespace: dict[str, Any] = {"__name__": "probe"}
    exec(compile(vs.RUNS_SCRIPT, "runs_script", "exec"), namespace)
    found = namespace["read_runs"](str(db), "app_1", "criba-recruiter-voice")
    assert found == [
        {"run_id": "run_a", "ts": "2026-10-11T20:00:04Z", "input_tokens": 1200},
        {"run_id": "run_b", "ts": "2026-10-11T20:00:14Z", "input_tokens": 1500},
    ]
    # The database is opened read-only: a write through the script's connection fails.
    opened = namespace["open_read_only"](str(db))
    with pytest.raises(sqlite3.OperationalError):
        opened.execute("DELETE FROM usage")
    opened.close()


def test_runs_script_tool_names_come_from_the_events_stream() -> None:
    namespace: dict[str, Any] = {"__name__": "probe"}
    exec(compile(vs.RUNS_SCRIPT, "runs_script", "exec"), namespace)
    frames = [
        {"event": "run.started"},
        {"event": "tool.started", "tool": "get_application"},
        {"event": "tool.completed", "tool": "get_application"},
        {"event": "message.delta", "delta": "hola"},
        {"event": "tool.started", "tool": "record_answer"},
        {"event": "run.completed"},
        {"event": "tool.started", "tool": "after_the_end"},
    ]
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames).encode()
    seen: dict[str, Any] = {}

    class Response(io.BytesIO):
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def urlopen(request: Any, timeout: float = 0) -> Response:
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        return Response(body)

    names = namespace["tool_names"](
        "http://hermes:8642", "key-word", "criba-recruiter-voice", "run_a", urlopen
    )
    assert names == ["get_application", "record_answer"]
    assert seen["url"] == "http://hermes:8642/p/criba-recruiter-voice/v1/runs/run_a/events"
    assert seen["auth"] == "Bearer key-word"

    def gone(request: Any, timeout: float = 0) -> Response:
        raise OSError("404")

    assert namespace["tool_names"]("http://hermes:8642", "k", "p", "run_a", gone) is None

    class Open(Response):
        """The stream stays open after the events: the next read times out."""

        def __next__(self) -> bytes:
            line = self.readline()
            if not line:
                raise TimeoutError("read timed out")
            return line

    partial = (
        b'data: {"event": "tool.started", "tool": "record_answer"}\n\n'
        b'data: {"event": "message.delta", "delta": "hola"}\n\n'
    )
    names_so_far = namespace["tool_names"](
        "http://hermes:8642", "k", "p", "run_a", lambda r, timeout=0: Open(partial)
    )
    assert names_so_far == ["record_answer"]
    empty = namespace["tool_names"](
        "http://hermes:8642", "k", "p", "run_a", lambda r, timeout=0: Response(b"")
    )
    assert empty is None


def test_the_compose_reader_runs_one_read_only_exec_and_parses_its_output() -> None:
    calls: list[list[str]] = []
    payload = {
        "runs": [
            {"run_id": "run_a", "ts": "2026-10-11T20:00:04Z", "input_tokens": 1200, "tools": ["x"]},
            {"run_id": "run_b", "ts": "2026-10-11T20:00:14Z", "input_tokens": 1500, "tools": None},
        ],
        "note": "events gone for run_b",
    }

    def execute(argv: list[str]) -> str:
        calls.append(argv)
        return json.dumps(payload)

    reader = vs.compose_runs_reader("criba-voicelive-stack", execute=execute)
    reading = reader("app_1")
    assert calls[0][:6] == ["docker", "compose", "-p", "criba-voicelive-stack", "exec", "-T"]
    assert "backend" in calls[0]
    assert calls[0][-2:] == ["app_1", "criba-recruiter-voice"]
    assert reading.runs[0] == vs.RunInfo(
        "run_a", datetime(2026, 10, 11, 20, 0, 4, tzinfo=UTC), 1200, ["x"]
    )
    assert reading.runs[1].tools is None
    assert reading.note == "events gone for run_b"

    def broken(argv: list[str]) -> str:
        raise RuntimeError("docker is not running")

    unavailable = vs.compose_runs_reader("p", execute=broken)("app_1")
    assert unavailable.runs == []
    assert "docker is not running" in unavailable.note
    assert vs.compose_runs_reader("p", execute=lambda argv: "not json")("app_1").runs == []


def test_owner_command_is_in_the_docstring() -> None:
    doc = vs.__doc__ or ""
    for needle in (
        "python -m live.voice_smoke",
        "CRIBA_REAL_SPEND_CAP_USD",
        "--compose-project",
        "voice_agents.py --check",
        "/voice",
        "criba-voicelive-run",
    ):
        assert needle in doc, needle
