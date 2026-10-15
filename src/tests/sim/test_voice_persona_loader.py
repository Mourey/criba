"""Voice persona format and loader (voice-agent sim-personas SM3, SM4; R24.1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from sim.personas import PERSONAS_DIR, load_personas
from sim.personas.schema import Expect
from sim.personas.voice import (
    OUTCOME_KEYS,
    TRANSPORT_KEYS,
    VOICE_DIR,
    VoicePersona,
    VoicePersonaError,
    load_voice_personas,
    outcome_expect,
    typed_of,
)


def _write(directory: Path, data: dict[str, Any], name: str | None = None) -> Path:
    path = directory / f"{name or data['id']}.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _persona(**extra: Any) -> dict[str, Any]:
    """A minimal voice persona over the typed qualified_es, with a phone of its own."""
    data: dict[str, Any] = {
        "id": "voice_test_es",
        "description": "A test persona.",
        "typed": "qualified_es",
        "phone": "+34600009901",
    }
    data.update(extra)
    return data


def _refused(directory: Path, *needles: str) -> str:
    with pytest.raises(VoicePersonaError) as caught:
        load_voice_personas(directory)
    message = str(caught.value)
    for needle in needles:
        assert needle in message, message
    return message


# ---- format and loader (SM3) ----------------------------------------------------------


def test_every_voice_persona_loads(tmp_path: Path) -> None:
    _write(tmp_path, _persona())
    _write(
        tmp_path,
        _persona(id="voice_other_mx", typed="no_license_mx", phone="+525500009902"),
    )
    loaded = load_voice_personas(tmp_path)
    assert [p.id for p in loaded] == ["voice_other_mx", "voice_test_es"]  # sorted by id
    assert all(isinstance(p, VoicePersona) for p in loaded)
    assert typed_of(loaded[1]).id == "qualified_es"


def test_id_must_equal_the_file_name(tmp_path: Path) -> None:
    _write(tmp_path, _persona(), name="another_name")
    _refused(tmp_path, "another_name.yaml", "file name")


def test_unknown_typed_id_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, _persona(typed="no_such_persona"))
    _refused(tmp_path, "voice_test_es.yaml", "no_such_persona")


def test_a_phone_shared_with_a_typed_persona_is_refused(tmp_path: Path) -> None:
    typed_phone = load_personas()["qualified_es"].identity.phone
    _write(tmp_path, _persona(phone=typed_phone))
    _refused(tmp_path, "voice_test_es.yaml", "phone", "qualified_es")


def test_a_phone_shared_with_a_voice_persona_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, _persona())
    _write(tmp_path, _persona(id="voice_twin_es"))
    _refused(tmp_path, "voice_twin_es.yaml", "phone", "voice_test_es")


def test_a_phone_of_another_country_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, _persona(phone="+525500009903"))
    _refused(tmp_path, "voice_test_es.yaml", "phone", "ES")


@pytest.mark.parametrize("key", ["media", "audio", "audio_url", "url"])
def test_media_or_audio_fields_are_refused(tmp_path: Path, key: str) -> None:
    _write(tmp_path, _persona(**{key: "nota.ogg"}))
    _refused(tmp_path, "voice_test_es.yaml", key)
    utterance = {"text": "Ana García", key: "nota.ogg"}
    _write(tmp_path, _persona(utterances={"full_name": [utterance]}))
    _refused(tmp_path, "voice_test_es.yaml", key)


@pytest.mark.parametrize("key", OUTCOME_KEYS)
def test_an_outcome_override_is_refused(tmp_path: Path, key: str) -> None:
    _write(tmp_path, _persona(expect_overrides={key: None}))
    _refused(tmp_path, "voice_test_es.yaml", key)


def test_an_unknown_override_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, _persona(expect_overrides={"loudness": 3}))
    _refused(tmp_path, "voice_test_es.yaml", "loudness")


def test_a_transport_override_is_accepted(tmp_path: Path) -> None:
    _write(tmp_path, _persona(expect_overrides={"nudges": None, "tools": []}))
    [persona] = load_voice_personas(tmp_path)
    assert outcome_expect(persona).nudges is None
    assert set(TRANSPORT_KEYS).isdisjoint(OUTCOME_KEYS)


def test_an_utterance_the_stub_cannot_read_is_refused(tmp_path: Path) -> None:
    # No typed persona sends "Me llamo Ana" for full_name, so the stub would record null.
    _write(tmp_path, _persona(utterances={"full_name": [{"text": "Me llamo Ana"}]}))
    _refused(tmp_path, "voice_test_es.yaml", "full_name", "Me llamo Ana")


def test_an_utterance_the_stub_reads_with_another_value_is_refused(tmp_path: Path) -> None:
    # "2 años" is a typed experience_years text, but for 2, not 3.
    _write(tmp_path, _persona(utterances={"experience_years": [{"text": "2 años", "value": 3}]}))
    _refused(tmp_path, "voice_test_es.yaml", "experience_years")


def test_a_typed_text_from_another_persona_is_readable(tmp_path: Path) -> None:
    # qualified_es types "Sí, tengo carnet"; "Sí, tengo licencia de manejo" is another
    # persona's typed text for the same value, which the stub knows.
    said = {"driver_license": [{"text": "Sí, tengo licencia de manejo"}]}
    _write(tmp_path, _persona(utterances=said))
    [persona] = load_voice_personas(tmp_path)
    assert persona.utterances["driver_license"][0].text == "Sí, tengo licencia de manejo"


def test_a_tap_reply_may_be_said_as_any_label(tmp_path: Path) -> None:
    # zone_outside_es taps option 0 at its second Zone prompt (the Toledo Sí/No).
    zone = [{"text": "Vivo en Toledo"}, {"text": "Sí, en Toledo"}]
    _write(
        tmp_path,
        _persona(typed="zone_outside_es", phone="+34600009904", utterances={"zone": zone}),
    )
    [persona] = load_voice_personas(tmp_path)
    assert persona.utterances["zone"][1].text == "Sí, en Toledo"


def test_an_utterance_past_the_typed_replies_is_refused(tmp_path: Path) -> None:
    said = {"full_name": [{"text": "Ana García"}, {"text": "Ana García"}]}
    _write(tmp_path, _persona(utterances=said))
    _refused(tmp_path, "voice_test_es.yaml", "full_name")


def test_a_correction_the_stub_cannot_read_is_refused(tmp_path: Path) -> None:
    correction = {"field": "zone", "text": "no, vivo en otro sitio"}
    _write(
        tmp_path,
        _persona(
            typed="confirm_correction_mx",
            phone="+525500009905",
            moments={"correction": correction},
        ),
    )
    _refused(tmp_path, "voice_test_es.yaml", "correction", "zone")


def test_moments_at_most_one_of_each(tmp_path: Path) -> None:
    optout = {"at": "driver_license", "phrase": "Que no me llaméis más, por favor."}
    _write(tmp_path, _persona(moments={"optout": [optout, optout]}))
    _refused(tmp_path, "voice_test_es.yaml", "optout")
    _write(tmp_path, _persona(moments={"interruption": optout}))
    _refused(tmp_path, "voice_test_es.yaml", "interruption")
    _write(tmp_path, _persona(moments={"optout": {**optout, "then": "Sí"}}))
    _refused(tmp_path, "voice_test_es.yaml", "then")


def test_booking_takes_a_seeded_hour_and_a_weekday(tmp_path: Path) -> None:
    _write(tmp_path, _persona(booking={"hour": 16, "weekday": "martes"}))
    [persona] = load_voice_personas(tmp_path)
    assert persona.booking is not None and persona.booking.hour == 16
    _write(tmp_path, _persona(booking={"hour": 11}))
    _refused(tmp_path, "voice_test_es.yaml", "hour")


def test_outcome_expect_keeps_only_outcome_keys(tmp_path: Path) -> None:
    _write(tmp_path, _persona(typed="opted_out_code_switch_es"))
    [persona] = load_voice_personas(tmp_path)
    typed = typed_of(persona).expect
    got = outcome_expect(persona)
    defaults = Expect(stage=typed.stage, status=typed.status)
    for name in ("final_language", "reply_languages_follow_latest", "tools", "events", "nudges"):
        assert getattr(got, name) == getattr(defaults, name), name
    assert typed.final_language == "en" and typed.tools  # the typed values were not defaults
    assert got.candidate_opted_out is True
    for key in OUTCOME_KEYS:
        assert getattr(got, key) == getattr(typed, key), key


def test_typed_personas_are_unchanged() -> None:
    typed = load_personas()
    assert len(typed) == 22
    assert set(typed) == {p.stem for p in PERSONAS_DIR.glob("*.yaml")}
    assert VOICE_DIR.parent == PERSONAS_DIR


# ---- the persona set (SM4) -------------------------------------------------------------

SET_IDS = [
    "confirm_correction_mx",
    "no_license_mx",
    "opted_out_code_switch_es",
    "qualified_es",
    "zone_freeform_mx",
    "zone_outside_es",
]
SPIKE_TRANSCRIPTS = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "tests"
    / "fixtures"
    / "spikes"
    / "004-spanish-voice"
    / "results"
    / "transcripts_realtime.json"
)


def _set() -> dict[str, VoicePersona]:
    return {p.id: p for p in load_voice_personas()}


def _transcript(call: str, turn: int, condition: str) -> str:
    rows = json.loads(SPIKE_TRANSCRIPTS.read_text(encoding="utf-8"))
    [row] = [r for r in rows if (r["call"], r["turn"], r["condition"]) == (call, turn, condition)]
    return str(row["transcript"])


def test_the_set_holds_each_kind_in_each_country() -> None:
    personas = _set()
    assert sorted(personas) == SET_IDS
    assert load_voice_personas() == load_voice_personas(VOICE_DIR)
    typed = {pid: typed_of(p) for pid, p in personas.items()}
    assert all(typed[pid].id == pid for pid in SET_IDS)  # each plays its namesake

    def country(pid: str) -> str:
        return typed[pid].identity.country

    qualified = [pid for pid in SET_IDS if typed[pid].expect.stage == "qualified"]
    assert {country(pid) for pid in qualified} == {"ES", "MX"}
    # Knockouts: ES on the Zone (Toledo is outside every Service Area), MX on the licence.
    assert country("zone_outside_es") == "ES"
    assert typed["zone_outside_es"].answers["zone"][0].query == "Toledo"
    assert typed["no_license_mx"].expect.knockout_criterion == "driver_license"
    assert country("no_license_mx") == "MX"
    # Booking, ES and MX: the two Qualified personas without another moment's end.
    booking = sorted(pid for pid, p in personas.items() if p.booking is not None)
    assert booking == ["confirm_correction_mx", "qualified_es"]
    assert {country(pid) for pid in booking} == {"ES", "MX"}
    # One moment each, where SM4 puts it.
    moments = {
        pid: [name for name, value in p.moments if value is not None] for pid, p in personas.items()
    }
    assert moments == {
        "confirm_correction_mx": ["correction"],
        "no_license_mx": [],
        "opted_out_code_switch_es": ["optout"],
        "qualified_es": [],
        "zone_freeform_mx": ["yes_no_conflict"],
        "zone_outside_es": [],
    }
    optout = personas["opted_out_code_switch_es"].moments.optout
    assert optout is not None and (optout.at, optout.then) == ("driver_license", "Baja.")
    conflict = personas["zone_freeform_mx"].moments.yes_no_conflict
    assert conflict is not None and (conflict.at, conflict.then) == ("zone", "No")
    correction = personas["confirm_correction_mx"].moments.correction
    assert correction is not None and correction.field == "zone"


def test_moment_texts_are_spike_four_transcripts() -> None:
    personas = _set()
    conflict = personas["zone_freeform_mx"].moments.yes_no_conflict
    optout = personas["opted_out_code_switch_es"].moments.optout
    assert conflict is not None and optout is not None
    assert conflict.text == _transcript("mx01", 8, "loud") == "Sí, está todo bien. ¡No, no!"
    assert optout.phrase == _transcript("es05", 2, "clean")
    assert optout.phrase == "Que no me llaméis más, por favor."


def test_phones_are_synthetic() -> None:
    typed_phones = {p.identity.phone for p in load_personas().values()}
    personas = _set().values()
    for persona in personas:
        country = typed_of(persona).identity.country
        prefix = {"ES": "+34", "MX": "+52"}[country]
        assert re.fullmatch(r"\+(34|52)[0-9]{8,11}", persona.phone), persona.id
        assert persona.phone.startswith(prefix), persona.id
        assert persona.phone not in typed_phones, persona.id
    assert len({p.phone for p in personas}) == len(SET_IDS)


def test_voice_personas_are_spanish_only() -> None:
    for persona in _set().values():
        assert typed_of(persona).identity.locale in ("es-ES", "es-MX"), persona.id
