"""The voice driver offline, on `httpx.MockTransport` (voice-agent sim-personas SM5; R24.2).

A fake backend answers the four calls the driver makes: start a conversation, start a voice
session, one custom-LLM request per utterance (a `text/event-stream` ending `data: [DONE]`), and
the chat long poll, which holds what each turn stored. Each test scripts the agent's replies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
import yaml

from sim.personas.voice import VoicePersona, load_voice_personas
from sim.voice_driver import (
    StreamedTurn,
    VoiceDriver,
    VoiceDriverError,
    VoiceRun,
    VoiceUnavailableError,
    stream_turn,
)

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "voice.openapi.yaml"
COMPLETIONS = "/api/voice/v1/chat/completions"
# Low-entropy words joined at runtime: never a literal that looks like a credential.
SECRET = "-".join(("llm", "secret", "for", "tests"))
TOKEN = ".".join(("voice", "token", "for", "tests"))
CONV = "conv_test_1"

NAME_Q = "¡Hola! Soy un asistente de IA. ¿Cómo te llamas?"
ZONE_Q = "¿En qué zona vives?"
LICENCE_Q = "¿Tienes carnet de conducir?"
DONE_TEXT = "Gracias, lo dejamos aquí."  # asks nothing


def _personas() -> dict[str, VoicePersona]:
    return {p.id: p for p in load_voice_personas()}


def _with(persona_id: str, **update: Any) -> VoicePersona:
    data = _personas()[persona_id].model_dump(exclude_unset=True)
    data.update(update)
    return VoicePersona.model_validate(data)


def _frame(delta: dict[str, Any], finish: str | None = None) -> str:
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "criba-voice",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def _sse(*chunks: str, after_done: str = "") -> bytes:
    frames = [_frame({"role": "assistant", "content": ""})]
    frames += [_frame({"content": c}) for c in chunks]
    frames += [_frame({}, "stop"), "data: [DONE]\n\n"]
    return ("".join(frames) + after_done).encode("utf-8")


@dataclass
class Reply:
    text: str
    interactive: dict[str, Any] | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FakeBackend:
    """The backend's side of the calls, with the agent's replies in order."""

    script: list[Reply]
    requests: list[httpx.Request] = field(default_factory=list)
    bodies: list[dict[str, Any]] = field(default_factory=list)
    stored: list[dict[str, Any]] = field(default_factory=list)
    voice_status: int = 201
    completion_status: int = 200

    def _store(self, direction: str, sender: str, text: str, **extra: Any) -> None:
        message = {
            "id": len(self.stored) + 1,
            "direction": direction,
            "sender": sender,
            "text": text,
            "interactive": None,
            "links": [],
            "attachments": [],
        }
        message.update(extra)
        self.stored.append(message)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/api/chat/conversations":
            return httpx.Response(201, json={"conversation_id": CONV, "cursor": 0})
        if request.method == "POST" and path.endswith("/voice-session"):
            if self.voice_status != 201:
                body = {"error": "voice_unavailable", "message": "No disponible."}
                return httpx.Response(self.voice_status, json=body)
            return httpx.Response(
                201,
                json={
                    "conversation_token": "fake",
                    "voice_token": TOKEN,
                    "expires_at": "2026-10-11T12:00:00Z",
                    "country": "ES",
                },
            )
        if request.method == "POST" and path == COMPLETIONS:
            body = json.loads(request.content)
            self.bodies.append(body)
            if self.completion_status != 200:
                return httpx.Response(
                    self.completion_status,
                    json={"error": "unauthorized", "message": "Invalid credentials."},
                )
            said = [m for m in body["messages"] if m["role"] == "user"][-1]["content"]
            self._store("inbound", "candidate", said)
            reply = self.script.pop(0) if self.script else Reply(DONE_TEXT)
            self._store(
                "outbound",
                "agent",
                reply.text,
                interactive=reply.interactive,
                attachments=reply.attachments,
            )
            return httpx.Response(
                200, content=_sse(reply.text), headers={"content-type": "text/event-stream"}
            )
        if request.method == "GET" and path.endswith("/messages"):
            after = int(request.url.params.get("after", "0"))
            fresh = [m for m in self.stored if m["id"] > after]
            cursor = max([after, *[m["id"] for m in fresh]])
            return httpx.Response(
                200, json={"messages": fresh, "cursor": cursor, "run_finished": True}
            )
        return httpx.Response(404, json={"error": "not_found", "message": path})

    def client(self) -> httpx.Client:
        return httpx.Client(base_url="http://sim.test", transport=httpx.MockTransport(self.handler))

    def said(self) -> list[str]:
        users = [[m for m in b["messages"] if m["role"] == "user"] for b in self.bodies]
        return [said[-1]["content"] for said in users]


def _driver(backend: FakeBackend, **kwargs: Any) -> VoiceDriver:
    return VoiceDriver(backend.client(), llm_secret=SECRET, poll_wait_s=0, **kwargs)


def _yes_no(yes_label: str = "Sí, vivo ahí", yes_id: str = "zone_es_toledo") -> dict[str, Any]:
    return {
        "style": "buttons",
        "options": [{"id": yes_id, "label": yes_label}, {"id": "no", "label": "No"}],
    }


def _toledo_script() -> list[Reply]:
    """zone_outside_es: the opening, the name, Toledo's Sí/No, then the Knockout."""
    return [
        Reply(NAME_Q),
        Reply(ZONE_Q),
        Reply("¿Vives en Toledo?", _yes_no()),
        Reply("Lo siento, no cubrimos Toledo. Gracias por tu interés."),
    ]


# ---- the request ------------------------------------------------------------------------


def test_request_bodies_validate_against_the_contract() -> None:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    schema = {
        "$ref": "#/components/schemas/VoiceChatCompletionRequest",
        "components": contract["components"],
    }
    backend = FakeBackend(_toledo_script())
    _driver(backend).play(_personas()["zone_outside_es"])
    assert len(backend.bodies) == 4
    for body in backend.bodies:
        jsonschema.Draft202012Validator(schema).validate(body)


def test_bearer_and_token_travel_on_every_request() -> None:
    backend = FakeBackend(_toledo_script())
    _driver(backend).play(_personas()["zone_outside_es"])
    calls = [r for r in backend.requests if r.url.path == COMPLETIONS]
    assert calls
    for request in calls:
        assert request.headers["authorization"] == f"Bearer {SECRET}"
    for body in backend.bodies:
        assert body["elevenlabs_extra_body"] == {"criba_voice_token": TOKEN}


def test_the_call_starts_on_a_voice_conversation() -> None:
    """Voice is its own flow (ADR 0059): the driver opens a voice conversation, and its body
    validates against the web chat contract's start request."""
    webchat = yaml.safe_load((CONTRACT.parent / "webchat.openapi.yaml").read_text(encoding="utf-8"))
    schema = {
        "$ref": "#/components/schemas/StartConversationRequest",
        "components": webchat["components"],
    }
    backend = FakeBackend(_toledo_script())
    _driver(backend).play(_personas()["zone_outside_es"])
    (start,) = [r for r in backend.requests if r.url.path == "/api/chat/conversations"]
    body = json.loads(start.content)
    assert body["channel"] == "voice"
    jsonschema.Draft202012Validator(schema).validate(body)


def test_history_grows_by_one_user_message_per_utterance() -> None:
    backend = FakeBackend(_toledo_script())
    run = _driver(backend).play(_personas()["zone_outside_es"])
    for n, body in enumerate(backend.bodies, start=1):
        roles = [m["role"] for m in body["messages"]]
        assert roles.count("user") == n
        assert roles[-1] == "user"
        # Each earlier utterance is followed by the reply streamed to it.
        assert roles[:-1] == ["user", "assistant"] * (n - 1)
        assistant = [m["content"] for m in body["messages"] if m["role"] == "assistant"]
        assert assistant == [t.text for t in run.turns[: n - 1]]
    assert backend.said() == run.said


# ---- the stream -------------------------------------------------------------------------


def _one_stream(content: bytes, seen: list[httpx.Request] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})

    return httpx.Client(base_url="http://sim.test", transport=httpx.MockTransport(handler))


def test_stream_parser_stops_at_done() -> None:
    late = _frame({"content": "never spoken"})
    client = _one_stream(_sse("... ", "Hola, ", "¿cómo te llamas?", after_done=late))
    turn = stream_turn(
        client, llm_secret=SECRET, voice_token=TOKEN, messages=[{"role": "user", "content": "Hola"}]
    )
    assert isinstance(turn, StreamedTurn)
    assert turn.text == "Hola, ¿cómo te llamas?"
    contents = [f["choices"][0]["delta"].get("content") for f in turn.frames]
    assert "... " in contents  # the first chunk is kept in the frames
    assert "never spoken" not in contents  # nothing after [DONE] is read
    assert turn.frames[-1]["choices"][0]["finish_reason"] == "stop"


def test_first_and_last_byte_times_are_recorded() -> None:
    client = _one_stream(_sse("Hola."))
    turn = stream_turn(
        client, llm_secret=SECRET, voice_token=TOKEN, messages=[{"role": "user", "content": "Hola"}]
    )
    assert 0 <= turn.first_byte_s <= turn.last_byte_s


def test_an_empty_secret_is_refused_before_any_request() -> None:
    seen: list[httpx.Request] = []
    client = _one_stream(_sse("Hola."), seen)
    with pytest.raises(ValueError, match="secret"):
        VoiceDriver(client, llm_secret="")
    with pytest.raises(ValueError, match="secret"):
        VoiceDriver(client, llm_secret="   ")
    with pytest.raises(ValueError, match="secret"):
        stream_turn(
            client, llm_secret="", voice_token=TOKEN, messages=[{"role": "user", "content": "x"}]
        )
    assert seen == []


def test_an_unavailable_voice_session_says_so() -> None:
    backend = FakeBackend([], voice_status=503)
    with pytest.raises(VoiceUnavailableError) as caught:
        _driver(backend).play(_personas()["qualified_es"])
    assert caught.value.status == 503 and caught.value.code == "voice_unavailable"
    assert not [r for r in backend.requests if r.url.path == COMPLETIONS]


# ---- what is said -----------------------------------------------------------------------


def test_options_are_said_as_their_label() -> None:
    backend = FakeBackend(_toledo_script())
    run = _driver(backend).play(_personas()["zone_outside_es"])
    # The typed persona taps option 0 at the Toledo confirmation; the voice persona says it.
    assert run.said == [
        "Buenas, me gustaría trabajar de repartidora",
        "Marta Ruiz",
        "Vivo en Toledo",
        "Sí, vivo ahí",
    ]
    posted = [r.url.path for r in backend.requests if r.method == "POST"]
    assert not [p for p in posted if p.endswith(("/messages", "/replies"))], posted


def test_a_zone_list_is_said_as_the_chosen_label() -> None:
    zones = {
        "style": "list",
        "options": [
            {"id": "zone_mx_puebla_angelopolis", "label": "Angelópolis, Puebla"},
            {"id": "zone_mx_puebla_centro", "label": "Centro, Puebla"},
            {"id": "zone_mx_puebla_la_paz", "label": "La Paz, Puebla"},
        ],
    }
    backend = FakeBackend(
        [
            Reply(NAME_Q),
            Reply(ZONE_Q),
            Reply("¿En cuál de estas zonas vives?", zones),
            Reply(LICENCE_Q),
            Reply("Lo siento, sin licencia no podemos seguir. Gracias."),
        ]
    )
    run = _driver(backend).play(_personas()["no_license_mx"])
    assert run.said[2:] == [
        "Puebla",
        "Centro, Puebla",  # zone_choice "Centro"
        "No, todavía no tengo licencia de manejo",
    ]
    assert run.ended == "knockout"


def test_the_conflict_fires_at_the_yes_no_prompt_and_consumes_the_typed_reply() -> None:
    reask = "Perdona, no te he oído bien. ¿Es sí o no?"
    cdmx = {
        "style": "list",
        "options": [
            {"id": "zone_mx_cdmx_condesa", "label": "Condesa, Ciudad de México"},
            {"id": "zone_mx_cdmx_polanco", "label": "Polanco, Ciudad de México"},
        ],
    }
    backend = FakeBackend(
        [
            Reply(NAME_Q),
            Reply(ZONE_Q),  # zone prompt 1
            Reply("¿Vives en Toluca?", _yes_no("Sí, en Toluca", "zone_mx_toluca")),  # 2: Sí/No
            Reply(reask, _yes_no("Sí, en Toluca", "zone_mx_toluca")),
            Reply("Entonces, ¿en qué zona vives?"),  # zone prompt 3
            Reply("¿En cuál de estas zonas vives?", cdmx),
            Reply(DONE_TEXT),
        ]
    )
    persona = _personas()["zone_freeform_mx"]
    run = _driver(backend).play(persona)
    conflict = persona.moments.yes_no_conflict
    assert conflict is not None
    assert run.said[2:] == [
        "por la salida a Toluca",
        conflict.text,
        "No",
        "Ciudad de México, Cuajimalpa",  # the typed reply after the consumed "No"
        "Polanco, Ciudad de México",  # zone_choice "Polanco"
    ]
    assert run.said.count("No") == 1
    assert [t.text for t in run.turns][3] == reask


def test_the_conflict_waits_for_a_yes_no_choice() -> None:
    # A Zone prompt without a Sí/No choice is answered as typed; the moment waits.
    backend = FakeBackend([Reply(NAME_Q), Reply(ZONE_Q), Reply(ZONE_Q), Reply(DONE_TEXT)])
    persona = _personas()["zone_freeform_mx"]
    run = _driver(backend).play(persona)
    assert run.said[2:] == ["por la salida a Toluca", "No"]
    conflict = persona.moments.yes_no_conflict
    assert conflict is not None and conflict.text not in run.said


def test_the_optout_phrase_then_baja_at_its_field() -> None:
    backend = FakeBackend(
        [
            Reply(NAME_Q),
            Reply(ZONE_Q),
            Reply(LICENCE_Q),
            Reply("¿Quieres que dejemos de escribirte? Di «baja» para confirmarlo."),
            Reply("Hecho, no te escribiremos más."),
        ]
    )
    seen: list[str] = []
    run = _driver(backend, after_turn=lambda r, t: seen.append(r.said[-1])).play(
        _personas()["opted_out_code_switch_es"]
    )
    assert run.said[-2:] == ["Que no me llaméis más, por favor.", "Baja."]
    assert run.ended == "opted_out"
    assert seen == run.said  # the hook runs after every turn


def test_the_correction_replaces_the_first_confirm_reply() -> None:
    summary = "Resumen: Diego, Centro (Puebla), licencia, tiempo completo. ¿Es correcto?"
    backend = FakeBackend(
        [
            Reply(NAME_Q),
            Reply(ZONE_Q),
            Reply(LICENCE_Q),
            Reply("¿Qué disponibilidad tienes?"),
            Reply("¿Qué horario prefieres?"),
            Reply("¿Cuántos años de experiencia tienes como repartidor?"),
            Reply("¿En qué plataformas has trabajado?"),
            Reply("¿Cuándo podrías empezar?"),
            Reply(summary),
            Reply(summary),
            Reply("¡Listo! Ya terminamos."),
        ]
    )
    run = _driver(backend).play(_personas()["confirm_correction_mx"])
    assert run.said[-2:] == ["no, mi zona es La Paz", "sí, ahora sí"]
    assert run.ended == "completed"


# ---- booking ----------------------------------------------------------------------------


def _offer() -> Reply:
    options = [
        {"id": "so_aaaa", "label": "mar 14 · 10:00"},
        {"id": "so_bbbb", "label": "mar 14 · 16:00"},
        {"id": "so_cccc", "label": "mié 15 · 12:00"},
        {"id": "other", "label": "Otro horario"},
    ]
    return Reply("Tengo estos huecos para la entrevista.", {"style": "buttons", "options": options})


def _book(backend: FakeBackend, persona: VoicePersona) -> tuple[VoiceRun, str]:
    # The post-Approval reply is stored by the router's system turn, not by a spoken turn.
    offer = _offer()
    backend._store("outbound", "agent", offer.text, interactive=offer.interactive)
    confirm = [{"type": "ics", "url": "/api/chat/ics/x.ics", "filename": "entrevista.ics"}]
    backend.script = [Reply("Reservado: martes a las 10:00.", attachments=confirm)]
    run = VoiceRun(conversation_id=CONV, voice_token=TOKEN, ended="completed")
    run = _driver(backend).book(run, persona)
    return run, run.said[-1]


def test_booking_says_day_and_hour() -> None:
    run, said = _book(FakeBackend([]), _with("qualified_es", booking={}))
    assert said == "El martes a las diez de la mañana"
    assert run.ended == "booked"
    assert any(a["type"] == "ics" for m in run.messages for a in m.get("attachments") or [])

    _, said = _book(FakeBackend([]), _with("qualified_es", booking={"hour": 16}))
    assert said == "El martes a las cuatro de la tarde"

    _, said = _book(FakeBackend([]), _with("qualified_es", booking={"weekday": "miércoles"}))
    assert said == "El miércoles a las doce del mediodía"

    with pytest.raises(VoiceDriverError) as caught:
        _book(FakeBackend([]), _with("qualified_es", booking={"hour": 12, "weekday": "lunes"}))
    message = str(caught.value)
    for label in ("mar 14 · 10:00", "mar 14 · 16:00", "mié 15 · 12:00"):
        assert label in message, message


# ---- secrets and the end ------------------------------------------------------------------


def _all_text(run: VoiceRun) -> str:
    fields = {k: v for k, v in vars(run).items() if k != "voice_token"}
    return json.dumps(fields, default=lambda o: vars(o), ensure_ascii=False)


def test_secrets_never_reach_the_run_or_errors() -> None:
    backend = FakeBackend(_toledo_script())
    run = _driver(backend).play(_personas()["zone_outside_es"])
    assert run.voice_token == TOKEN
    for text in (_all_text(run), repr(run)):
        assert SECRET not in text
        assert TOKEN not in text

    refused = FakeBackend([], completion_status=401)
    with pytest.raises(VoiceDriverError) as caught:
        _driver(refused).play(_personas()["zone_outside_es"])
    for text in (str(caught.value), repr(caught.value)):
        assert SECRET not in text and TOKEN not in text
        assert "401" in text


def test_a_knockout_ends_the_run() -> None:
    # zone_outside_es: the typed expect has no Outcome (it ends after the Reopen), but the
    # typed persona has an after_knockout interrupt, so the voice run ends at the Knockout.
    backend = FakeBackend([*_toledo_script(), Reply(ZONE_Q)])
    run = _driver(backend).play(_personas()["zone_outside_es"])
    assert run.ended == "knockout"
    assert len(run.turns) == 4  # nothing said after the Knockout
    assert all(isinstance(t, StreamedTurn) for t in run.turns)


def test_the_after_turn_hook_sees_every_turn() -> None:
    calls: list[tuple[int, str]] = []

    def hook(run: VoiceRun, turn: StreamedTurn) -> None:
        calls.append((len(run.turns), turn.text))

    backend = FakeBackend(_toledo_script())
    run = _driver(backend, after_turn=hook).play(_personas()["zone_outside_es"])
    assert calls == [(n, t.text) for n, t in enumerate(run.turns, start=1)]
