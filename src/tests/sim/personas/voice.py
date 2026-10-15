"""Voice personas: transcript-level scripts played over the custom-LLM endpoint (voice-agent
sim-personas SM3, SM4, SM6; R24.1).

A voice persona names its typed counterpart (`typed`) and says what that persona says, with
three differences: it may say a free-text answer in other words the stub model can read
(`utterances`), it plays spoken moments instead of the typed `interrupts` (`moments`), and a
Qualified persona may book by voice (`booking`). It has a phone of its own. The outcome it must
reach is its typed counterpart's (`outcome_expect`); only transport keys may be overridden.

The stub model reads Candidate words only through the typed persona files
(`sim.stub_llm.interpret.default_interpreter`), so every utterance a run sends as text must be a
text the stub knows for its field with the same value or query. A tap is said as the option's
label, which the backend's spoken checks match; any wording is allowed there. Voice persona
files live in `personas/voice/`, which the typed loader (`*.yaml` in `personas/`) never reads.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, ValidationError, model_validator

from sim.personas.loader import load_personas
from sim.personas.schema import _PHONE_PREFIX, Expect, FieldName, Persona, Reply, _Strict
from sim.stub_llm.interpret import Interpreter, default_interpreter

VOICE_DIR = Path(__file__).resolve().parent / "voice"

# SM6: the keys of `Expect` a voice persona must reach as its typed counterpart does, and the
# keys about the transport (tools, events, timing, templates, prose) it may override.
OUTCOME_KEYS = (
    "stage",
    "status",
    "outcome",
    "knockout_criterion",
    "fields",
    "fields_unanswered",
    "candidate_opted_out",
)
TRANSPORT_KEYS = (
    "tools",
    "events",
    "event_counts",
    "nudges",
    "max_reply_latency_s",
    "knockout_template",
    "ai_disclosure",
    "forbidden_agent_text",
)

_PHONE_PATTERN = r"^\+(34|52)[0-9]{8,11}$"  # Identity.phone


class VoicePersonaError(ValueError):
    """A voice persona file is invalid. The message names the file."""


class VoiceUtterance(_Strict):
    """What the Candidate says for one typed reply. A `value` or `query` not given is the typed
    reply's at the same index."""

    text: str = Field(min_length=1, max_length=4000)
    value: Any = None
    query: str | None = None


class Correction(_Strict):
    """Said at the read-back in place of the typed persona's first `confirm` reply."""

    field: FieldName
    text: str = Field(min_length=1, max_length=4000)
    query: str | None = None
    value: Any = None


class YesNoConflict(_Strict):
    """Said at the first prompt for `at` that offers a yes/no choice, then `then` after the
    re-ask, in place of the typed reply at that index."""

    at: FieldName
    text: str = Field(min_length=1, max_length=4000)
    then: str = Field(min_length=1, max_length=4000)


class OptOut(_Strict):
    """`phrase` said when `at` is asked, then `then` at the confirmation question."""

    at: FieldName
    phrase: str = Field(min_length=1, max_length=4000)
    then: Literal["Baja."] = "Baja."


class Moments(_Strict):
    correction: Correction | None = None
    yes_no_conflict: YesNoConflict | None = None
    optout: OptOut | None = None


class Booking(_Strict):
    """The slot said after Approval; None picks from the offer (the first offered slot, or the
    first at `hour`)."""

    hour: Literal[10, 12, 16] | None = None  # the seed's SLOT_TIMES
    weekday: Literal["lunes", "martes", "miércoles", "jueves", "viernes", "sábado"] | None = None


class VoicePersona(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str
    typed: str
    phone: str = Field(pattern=_PHONE_PATTERN)
    utterances: dict[FieldName, list[VoiceUtterance]] = Field(default_factory=dict)
    moments: Moments = Field(default_factory=Moments)
    booking: Booking | None = None
    expect_overrides: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _overrides_are_transport_keys(self) -> VoicePersona:
        for key in self.expect_overrides:
            if key in OUTCOME_KEYS:
                raise ValueError(f"expect_overrides: {key!r} is an outcome key, never overridden")
            if key not in TRANSPORT_KEYS:
                raise ValueError(
                    f"expect_overrides: {key!r} is not a transport key {TRANSPORT_KEYS}"
                )
        return self


# ---- the typed counterpart -------------------------------------------------------------


@cache
def _typed_personas() -> dict[str, Persona]:
    return load_personas()


def typed_of(persona: VoicePersona) -> Persona:
    """The typed persona `persona` plays."""
    return _typed_personas()[persona.typed]


def outcome_expect(persona: VoicePersona) -> Expect:
    """The typed `expect` with every field but `OUTCOME_KEYS` at its default, then the
    persona's transport overrides."""
    typed = typed_of(persona).expect
    kept = {key: getattr(typed, key) for key in OUTCOME_KEYS}
    base = Expect(**kept)
    if not persona.expect_overrides:
        return base
    return Expect.model_validate({**base.model_dump(), **persona.expect_overrides})


# ---- what is said ----------------------------------------------------------------------


def typed_reply(typed: Persona, field: str, index: int) -> Reply | None:
    """The typed persona's reply the `index`-th time `field` is asked (the last one repeats)."""
    replies = {str(name): said for name, said in typed.answers.items()}.get(field)
    if not replies:
        return None
    return replies[min(index, len(replies) - 1)]


def is_tap(reply: Reply) -> bool:
    """A typed reply that taps an option: said as the option's label."""
    return reply.pick_option is not None


def _readable(
    interpreter: Interpreter, field: str, text: str, value: Any, query: str | None
) -> str | None:
    """Why the stub cannot read `text` for `field` as `value` (`query` for the Zone), or None."""
    entry = interpreter.entry(field, text)
    if entry is None:
        return f"{field}: the stub model knows no typed text {text!r} for this field"
    if field == "zone":
        if entry.query != query:
            return f"zone: the stub reads {text!r} as query {entry.query!r}, not {query!r}"
    elif entry.value != value:
        return f"{field}: the stub reads {text!r} as {entry.value!r}, not {value!r}"
    return None


def _check_utterances(persona: VoicePersona, typed: Persona, interpreter: Interpreter) -> None:
    for field, said in persona.utterances.items():
        replies = typed.answers.get(field) or []
        for index, utterance in enumerate(said):
            if index >= len(replies):
                raise ValueError(
                    f"utterances.{field}[{index}]: the typed persona {typed.id} has no reply "
                    f"at this index ({len(replies)} replies)"
                )
            reply = replies[index]
            if is_tap(reply):
                continue  # said as an option label; the spoken checks match it
            given = utterance.model_fields_set
            value = utterance.value if "value" in given else reply.value
            if "query" in given:
                query = utterance.query
            else:
                query = reply.query if "query" in reply.model_fields_set else reply.text
            problem = _readable(interpreter, field, utterance.text, value, query)
            if problem:
                raise ValueError(f"utterances.{field}[{index}]: {problem}")


def _check_correction(persona: VoicePersona, typed: Persona, interpreter: Interpreter) -> None:
    correction = persona.moments.correction
    if correction is None:
        return
    first = typed.confirm[0] if typed.confirm else None
    inherited = first if first is not None and first.field == correction.field else None
    given = correction.model_fields_set
    value = correction.value if "value" in given or inherited is None else inherited.value
    query = correction.query if "query" in given or inherited is None else inherited.query
    problem = _readable(interpreter, correction.field, correction.text, value, query)
    if problem:
        raise ValueError(f"moments.correction: {problem}")


def check_voice_persona(
    persona: VoicePersona,
    *,
    typed: dict[str, Persona] | None = None,
    interpreter: Interpreter | None = None,
) -> None:
    """The checks that need the typed personas and the stub's interpreter; ValueError."""
    typed = _typed_personas() if typed is None else typed
    counterpart = typed.get(persona.typed)
    if counterpart is None:
        raise ValueError(f"typed persona {persona.typed!r} does not exist")
    country = counterpart.identity.country
    if not persona.phone.startswith(_PHONE_PREFIX[country]):
        raise ValueError(f"phone {persona.phone} does not match the typed country {country}")
    for other in typed.values():
        if other.identity.phone == persona.phone:
            raise ValueError(f"phone {persona.phone} is shared with typed persona {other.id}")
    reader = default_interpreter() if interpreter is None else interpreter
    _check_utterances(persona, counterpart, reader)
    _check_correction(persona, counterpart, reader)
    if persona.expect_overrides:
        outcome_expect(persona)  # the overrides must still make a valid Expect


# ---- loading ---------------------------------------------------------------------------


def load_voice_persona(path: str | Path) -> VoicePersona:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise VoicePersonaError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise VoicePersonaError(f"{path.name}: expected a mapping at the top level")
    try:
        persona = VoicePersona.model_validate(raw)
    except ValidationError as exc:
        raise VoicePersonaError(f"{path.name}: {exc}") from exc
    if persona.id != path.stem:
        raise VoicePersonaError(f"{path.name}: id {persona.id!r} must equal the file name")
    try:
        check_voice_persona(persona)
    except ValueError as exc:
        raise VoicePersonaError(f"{path.name}: {exc}") from exc
    return persona


def load_voice_personas(directory: Path | None = None) -> list[VoicePersona]:
    """Every `*.yaml` in `directory` (default `VOICE_DIR`), sorted by id. Raises
    VoicePersonaError on the first invalid file or a phone two voice personas share."""
    personas: list[VoicePersona] = []
    phones: dict[str, str] = {}
    for path in sorted(Path(directory or VOICE_DIR).glob("*.yaml")):
        persona = load_voice_persona(path)
        other = phones.get(persona.phone)
        if other:
            raise VoicePersonaError(f"{path.name}: phone shared with voice persona {other}")
        phones[persona.phone] = persona.id
        personas.append(persona)
    return sorted(personas, key=lambda p: p.id)


__all__ = [
    "OUTCOME_KEYS",
    "TRANSPORT_KEYS",
    "VOICE_DIR",
    "Booking",
    "Correction",
    "Moments",
    "OptOut",
    "VoicePersona",
    "VoicePersonaError",
    "VoiceUtterance",
    "YesNoConflict",
    "check_voice_persona",
    "is_tap",
    "load_voice_persona",
    "load_voice_personas",
    "outcome_expect",
    "typed_of",
    "typed_reply",
]
