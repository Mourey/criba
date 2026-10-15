"""The stub model's interpreter (spec 006b): persona replies indexed as (field, text)."""

from datetime import date
from pathlib import Path

import pytest

from sim.personas import PERSONAS_DIR, load_personas
from sim.personas.schema import Interpretation
from sim.stub_llm.interpret import Interpreter, default_interpreter, resolve_date

FRIDAY = date(2026, 10, 9)
PERSONAS = load_personas()


def table() -> Interpreter:
    return Interpreter(
        [
            Interpretation("full_name", "me llamo ana garcía", "es", value="Ana García"),
            Interpretation("driver_license", "sí, tengo carnet", "es", value="yes"),
            Interpretation("availability", "full time", "en", value=["full_time"]),
            Interpretation("start_date", "el lunes", "es", value="next:mon"),
            Interpretation("zone", "near Chamberí, Madrid", "en", query="Chamberí"),
            Interpretation("zone", "por la salida a Toluca", "es", query=None),
        ]
    )


def test_a_scripted_message_interprets_to_its_canonical_value():
    interp = table()
    assert interp.value("full_name", "me llamo ana garcía") == "Ana García"
    assert interp.value("driver_license", "sí, tengo carnet") == "yes"
    assert interp.value("availability", "full time") == ["full_time"]
    assert interp.value("full_name", "  me llamo ana garcía  ") == "Ana García"


def test_the_same_words_mean_nothing_for_another_field():
    assert table().value("availability", "sí, tengo carnet") is None


def test_text_no_persona_says_is_null():
    interp = table()
    for field in ("full_name", "driver_license", "availability", "start_date", "zone"):
        assert interp.value(field, "algo que nadie dice") is None


def test_a_tapped_option_id_passes_through():
    interp = table()
    assert interp.value("driver_license", "expired") == "expired"
    assert interp.value("availability", "weekends") == ["weekends"]
    assert interp.value("experience_platforms", "didi_food") == ["didi_food"]
    assert interp.value("zone", "zone_es_madrid_chamberi") == ["zone_es_madrid_chamberi"]


def test_a_code_of_another_field_is_not_a_value_here():
    assert table().value("availability", "morning") is None
    assert table().value("driver_license", "full_time") is None


def test_no_for_a_zone_confirmation_is_null():
    assert table().value("zone", "no") is None


def test_a_zone_message_has_a_query_never_a_value():
    interp = table()
    assert interp.zone_query("near Chamberí, Madrid") == "Chamberí"
    assert interp.value("zone", "near Chamberí, Madrid") is None
    assert interp.interprets("zone", "near Chamberí, Madrid")


def test_a_query_of_null_means_nothing_to_search():
    interp = table()
    assert interp.zone_query("por la salida a Toluca") is None
    assert not interp.interprets("zone", "por la salida a Toluca")
    assert interp.entry("zone", "por la salida a Toluca") is not None


def test_interprets_is_false_for_unknown_text_and_true_for_other_fields():
    interp = table()
    assert not interp.interprets("zone", "algo que nadie dice")
    assert interp.interprets("full_name", "me llamo ana garcía")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-01", "2026-11-01"),
        ("+2d", "2026-10-11"),
        ("+0d", "2026-10-09"),
        ("-1d", "2026-10-08"),
        ("next:mon", "2026-10-12"),
        (
            "next:fri",
            "2026-10-16",
        ),  # strictly after today, as the backend reads "el viernes"
        ("next:sat", "2026-10-10"),
        ("next:sun", "2026-10-11"),
    ],
)
def test_relative_dates_resolve_against_today(value, expected):
    assert resolve_date(value, FRIDAY) == expected


def test_a_relative_date_needs_today_and_an_iso_date_does_not():
    assert resolve_date("next:mon", None) is None
    assert resolve_date("2026-11-01", None) == "2026-11-01"
    assert resolve_date(None, FRIDAY) is None


def test_start_date_values_resolve_through_the_interpreter():
    interp = table()
    assert interp.value("start_date", "el lunes", FRIDAY) == "2026-10-12"
    assert interp.value("start_date", "el lunes") is None


def test_taps_and_typed_text_are_told_apart():
    interp = table()
    for tap in (
        "yes",
        "no",
        "expired",
        "full_time",
        "other",
        "zone_mx_puebla_centro",
        "so_ab12-_",
    ):
        assert interp.is_tap(tap), tap
    for typed in ("Hola", "sí, tengo carnet", "[system] approved", "full time"):
        assert not interp.is_tap(typed), typed


def test_a_message_a_persona_says_is_typed_even_if_it_looks_like_an_id():
    interp = Interpreter([Interpretation("driver_license", "yes", "en", value="yes")])
    assert not interp.is_tap("yes")
    assert interp.api_language("yes", None) == "en"


def test_the_language_is_the_personas_then_a_guess_and_none_after_a_tap():
    interp = table()
    assert interp.api_language("full time", "es") == "en"  # the persona's lang wins
    assert interp.api_language("sí, tengo carnet", None) == "es-ES"
    assert interp.api_language("hola buenas", "es") == "es-ES"
    assert interp.api_language("hello there", "en") == "en"
    assert interp.api_language("hello there", None) is None
    assert interp.api_language("expired", "en") is None  # a tap says nothing
    assert interp.api_language("zone_mx_puebla_centro", None) is None


def test_two_meanings_for_the_same_words_are_refused():
    with pytest.raises(ValueError, match="two ways"):
        Interpreter(
            [
                Interpretation("availability", "tiempo", "es", value=["full_time"]),
                Interpretation("availability", "tiempo", "es", value=["part_time"]),
            ]
        )
    Interpreter(  # the same meaning twice is fine
        [
            Interpretation("availability", "tiempo", "es", value=["full_time"]),
            Interpretation("availability", "tiempo", "es", value=["full_time"]),
        ]
    )


# ---- the seed personas ---------------------------------------------------------------------


def test_the_seed_personas_index_without_a_conflict():
    interp = Interpreter.from_personas(PERSONAS.values())
    assert len(interp) > 50


def test_every_typed_answer_of_every_persona_has_an_interpretation():
    interp = default_interpreter()
    missing = []
    for persona in PERSONAS.values():
        for field, replies in persona.answers.items():
            for reply in replies:
                if reply.covers or reply.pick_option is not None:
                    continue
                if interp.entry(field, reply.text) is None:
                    missing.append((persona.id, field, reply.text))
    assert not missing


def test_no_text_means_two_things_across_personas():
    seen: dict[tuple[str, str], tuple[object, object]] = {}
    for persona in PERSONAS.values():
        for entry in persona.interpretations():
            key = (entry.field, entry.text)
            meaning = (entry.value, entry.query)
            assert seen.setdefault(key, meaning) == meaning, key


def test_the_persona_texts_are_never_taken_for_taps():
    interp = default_interpreter()
    for persona in PERSONAS.values():
        for _, reply in persona.replies():
            assert not interp.is_tap(reply.text), (persona.id, reply.text)


def test_the_stub_reads_the_personas_next_to_its_package():
    assert PERSONAS_DIR.is_dir()
    assert len(default_interpreter()) == len(Interpreter.from_directory(PERSONAS_DIR))


def test_a_missing_personas_directory_gives_an_empty_interpreter(tmp_path: Path):
    assert len(Interpreter.from_directory(tmp_path / "nope")) == 0


def test_a_start_date_in_every_persona_resolves_to_a_valid_date():
    interp = default_interpreter()
    for persona in PERSONAS.values():
        for entry in persona.interpretations():
            if entry.field == "start_date" and entry.value is not None:
                resolved = interp.value("start_date", entry.text, FRIDAY)
                if isinstance(resolved, list):  # the two readings of ADR 0054
                    assert len(resolved) == 2 and all(map(date.fromisoformat, resolved))
                    continue
                assert date.fromisoformat(resolved), (persona.id, entry.text)


def test_next_weekday_and_a_week_later_resolve_against_today():
    """ADR 0054: on Friday 9 October, "el próximo domingo" is the 11th or the 18th."""
    assert resolve_date(["next:sun", "next:sun+7d"], FRIDAY) == ["2026-10-11", "2026-10-18"]
    assert resolve_date("next:fri+7d", FRIDAY) == "2026-10-23"
    assert resolve_date(["next:sun", "next:sun+7d"], None) is None  # no today yet


def test_the_seed_values_match_what_the_old_parsers_read():
    """The values were chosen to give the results the personas expected before 006."""
    interp = default_interpreter()
    assert interp.value("experience_years", "6 meses") == 0.5
    assert interp.value("availability", "medio tiempo y fines de semana") == [
        "part_time",
        "weekends",
    ]
    assert interp.value("experience_platforms", "Rappi y DiDi Food") == [
        "rappi",
        "didi_food",
    ]
    assert interp.value("start_date", "pasado mañana", FRIDAY) == "2026-10-11"
    assert interp.value("start_date", "ayer", FRIDAY) == "2026-10-08"
    assert interp.value("driver_license", "No, todavía no tengo licencia de manejo") == "no"
