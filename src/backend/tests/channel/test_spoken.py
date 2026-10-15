"""The deterministic spoken checks (spec voice-agent spoken-checks R13, R14.1, R15.1).

Spike 4's realtime transcripts are the fixtures, read by ``call``, ``turn`` and ``condition``
from ``src/tests/fixtures/spikes/004-spanish-voice/results/transcripts_realtime.json``,
never retyped. Choices
are built from the labels the backend attaches (``screening/vocab.py``, ``repo.local_label``).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pytest

from criba.channel import spoken
from criba.channel.service import SpokenDecision
from criba.channel.spoken import (
    HEDGE_CUES,
    OPTOUT_PHRASES,
    check_spoken,
    is_optout_phrase,
    match_option,
    yes_no_conflict,
)
from criba.db import iso, repo
from criba.screening.vocab import LABELS, NO_PLATFORM, NO_PLATFORM_LABELS, WEEKDAY_NAMES
from criba.tools.booking import OTHER_LABEL, OTHER_OPTION_ID
from criba.tools.choices import Interactive

TRANSCRIPTS = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "tests"
    / "fixtures"
    / "spikes"
    / "004-spanish-voice"
    / "results"
    / "transcripts_realtime.json"
)
CONDITIONS = ("clean", "moderate", "loud")


@cache
def _fixtures() -> dict[tuple[str, int, str], str]:
    rows = json.loads(TRANSCRIPTS.read_text(encoding="utf-8"))
    return {(r["call"], r["turn"], r["condition"]): r["transcript"] for r in rows}


def said(call: str, turn: int, condition: str = "clean") -> str:
    return _fixtures()[(call, turn, condition)]


def choice(*options: tuple[str, str], style: Literal["buttons", "list"] = "buttons") -> Interactive:
    return {"style": style, "options": [{"id": i, "label": label} for i, label in options]}


def field(name: str, language: str) -> Interactive:
    """A field's options as ``pending_choice`` attaches them (the platforms end with no app)."""
    options = list(LABELS[name][language].items())
    if name == "experience_platforms":
        options.append((NO_PLATFORM, NO_PLATFORM_LABELS[language]))
    return choice(*options, style="list" if len(options) > 3 else "buttons")


LICENCE_ES = field("driver_license", "es-ES")
LICENCE_MX = field("driver_license", "es-MX")
AVAILABILITY_ES = field("availability", "es-ES")
AVAILABILITY_MX = field("availability", "es-MX")
SCHEDULE_ES = field("preferred_schedule", "es-ES")
SCHEDULE_MX = field("preferred_schedule", "es-MX")
PLATFORMS_ES = field("experience_platforms", "es-ES")
PLATFORMS_MX = field("experience_platforms", "es-MX")
COYOACAN = "zone_mx_cdmx_coyoacan"
ZONE_CONFIRMATION = choice((COYOACAN, "Sí"), ("no", "No"))
# A Zone ambiguity in zone_labels' two forms: across cities "Name (City)", in one city "Name".
ZONES_ACROSS = choice(
    ("zone_es_valencia_ruzafa", "Ruzafa (Valencia)"), ("zone_es_madrid_getafe", "Getafe (Madrid)")
)
ZONES_SEVILLA = choice(("zone_es_sevilla_triana", "Triana"), ("zone_es_sevilla_nervion", "Nervión"))
ZONES_CDMX = choice(("zone_mx_cdmx_juarez", "Juárez"), ("zone_mx_cdmx_iztapalapa", "Iztapalapa"))
# A start-date ambiguity in the ADR 0054 form ("Lunes 12 oct").
_ES = WEEKDAY_NAMES["es"]
START_DATES = choice(("2026-10-12", f"{_ES[0]} 12 oct"), ("2026-10-19", f"{_ES[0]} 19 oct"))
START_NOVEMBER = choice(("2026-11-01", f"{_ES[6]} 1 nov"), ("2026-11-02", f"{_ES[0]} 2 nov"))


# --------------------------------------------------------------------------- R13.1


@pytest.mark.parametrize("text", ["", "   ", "¿¡...!?", "—, ;"])
def test_degenerate_input_matches_nothing(
    text: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for pending in (LICENCE_ES, AVAILABILITY_ES, PLATFORMS_MX, ZONE_CONFIRMATION):
        assert match_option(text, pending) is None
    assert match_option("Jornada completa.", choice()) is None
    assert match_option("Jornada completa.", {"style": "buttons", "options": []}) is None
    # An internal error matches nothing and logs no transcript text.
    text = said("es01", 4)

    def broken(value: str) -> list[str]:
        raise RuntimeError(f"cannot read {value}")

    monkeypatch.setattr(spoken, "_words", broken)
    with caplog.at_level(logging.DEBUG):
        assert match_option(text, AVAILABILITY_ES) is None
        assert yes_no_conflict(text, LICENCE_ES) is False
        assert is_optout_phrase(text) is False
    assert "RuntimeError" in caplog.text
    assert text not in caplog.text and "Jornada" not in caplog.text


ALL_CHOICES = (
    LICENCE_ES,
    LICENCE_MX,
    AVAILABILITY_ES,
    AVAILABILITY_MX,
    SCHEDULE_ES,
    SCHEDULE_MX,
    PLATFORMS_ES,
    PLATFORMS_MX,
    ZONE_CONFIRMATION,
    ZONES_ACROSS,
    ZONES_SEVILLA,
    ZONES_CDMX,
    START_DATES,
    START_NOVEMBER,
)


def test_returned_ids_are_in_the_choice() -> None:
    matched = 0
    for text in _fixtures().values():
        for pending in ALL_CHOICES:
            found = match_option(text, pending)
            if found is not None:
                matched += 1
                assert found in {o["id"] for o in pending["options"]}
    assert matched > 0


# --------------------------------------------------------------------------- R15.1


@pytest.mark.parametrize("condition", CONDITIONS)
@pytest.mark.parametrize("call,turn", [("es05", 2), ("es10", 2), ("mx05", 1), ("mx10", 2)])
def test_optout_phrases_are_classified(call: str, turn: int, condition: str) -> None:
    assert is_optout_phrase(said(call, turn, condition))


def test_the_phrase_list_is_r15s() -> None:
    assert OPTOUT_PHRASES == (
        "dadme de baja",
        "no me llameis mas",
        "no me llamen mas",
        "ya no me llamen",
        "borrenme de su lista",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Baja.",
        "Vivo en Baja California",
        "Quiero darme de baja?",
        said("es05", 1),
        said("es05", 3),
        said("mx05", 2),
        "No me llamas más tarde, ¿vale?",
    ],
)
def test_other_text_is_not_an_optout_phrase(text: str) -> None:
    assert not is_optout_phrase(text)


# --------------------------------------------------------------------------- R14.1


def test_yes_no_conflict() -> None:
    for condition in ("moderate", "loud"):
        conflict = said("mx01", 8, condition)
        assert yes_no_conflict(conflict, ZONE_CONFIRMATION)
        assert yes_no_conflict(conflict, LICENCE_MX)
        assert not yes_no_conflict(conflict, AVAILABILITY_MX)
        assert not yes_no_conflict(conflict, None)
    assert not yes_no_conflict(said("mx01", 8, "clean"), ZONE_CONFIRMATION)
    for text in (said("mx09", 2), said("mx09", 0), said("mx02", 2)):
        assert not yes_no_conflict(text, LICENCE_MX), text
    assert not yes_no_conflict("Sí, de noche, nomás", LICENCE_MX)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Sí, cómo no.", "yes"),
        ("¡Cómo no!", "yes"),
        ("Eh, pues sí, tengo", "yes"),
        ("Bueno... no, no tengo", "no"),
        ("Eh... mmm, a ver", None),
        # "como no" (since ... not) is not the affirmative "cómo no", nor a clear no.
        ("Como no tengo licencia, ¿puedo ir en bici?", None),
        # "¿Cómo?" (what?) then "no": a question, not "cómo no".
        ("¿Cómo? No te he oído", None),
        # "si" without its accent is "if", not a yes, and its "no" is no answer.
        ("Si me lo piden, lo saco", None),
        ("Si no me equivoco, tengo carnet", None),
        ("Y si no, voy en bici", None),
        # A "no" that is not the answer's first word does not answer.
        ("Tengo carnet, no te preocupes", None),
        # "nunca" is not a polarity cue: "never lost it" is not a no.
        ("Nunca me han quitado el carnet", None),
        # Hedges and their variants are non-answers, never a conflict.
        ("Sí, no sé", None),
        ("No lo sé", None),
        ("No estoy segura", None),
        ("Sí, la tengo caducada", None),
        ("Sí, está vencido", None),
        ("Sí, se me venció", None),
        ("Sí, me la suspendieron", None),
        ("Sí, la estoy tramitando", None),
        ("Sí, en trámite", None),
        ("Sí, me la quitaron", None),
        ("No recuerdo", None),
        ("No sabría decirte", None),
        ("Creo que no", None),
        # A "no" that opens the answer but is not one: the Candidate has the licence.
        ("No te preocupes, tengo carnet B", None),
        ("No pasa nada, tengo licencia", None),
        ("No hay problema, tengo carnet", None),
        ("No, tengo licencia", None),
        ("No, lo tengo", None),
        ("No, no, tengo carnet", None),
        # Non-answers that open with "no", and questions.
        ("No me acuerdo", None),
        ("No entiendo la pregunta", None),
        ("No te oigo", None),
        ("No te escucho bien", None),
        ("No te he oído", None),
        ("No, perdona, ¿qué has dicho?", None),
        ("¿Sí?", None),
        ("¿No?", None),
        # Mexican past-tense non-answers, and any question in the utterance.
        ("No te escuché.", None),
        ("No te oí.", None),
        ("No entendí.", None),
        ("No le entendí.", None),
        ("No oigo nada.", None),
        ("No, no te entendí, ¿me repites?", None),
        ("No, ¿cómo?", None),
        ("No, espera, ¿de qué licencia?", None),
        ("Sí, ¿me lo repites?", None),
        ("Sí, perdona, ¿qué me decías?", None),
        ("Sí, sí. ¿Y ahora qué?", None),
        # A yes that also negates is no clear yes.
        ("Okay, tampoco tengo carnet", None),
        ("Claro, nunca lo he tenido", None),
        ("Vale, ni de broma", None),
        ("Sí, sin licencia", None),
        # Clear answers still match.
        ("No, no lo tengo", "no"),
        ("No. No tengo licencia.", "no"),
        ("Sí, sí, tengo licencia.", "yes"),
    ],
)
def test_polarity_reads_the_accent_of_si_and_como(text: str, expected: str | None) -> None:
    assert match_option(text, LICENCE_MX) == expected
    assert not yes_no_conflict(text, LICENCE_MX)


@pytest.mark.parametrize(
    "text",
    [
        "No, ya la saqué",
        "No ya la saqué",
        "No, ya lo tengo hace años",
        "No, la saqué el año pasado",
        "No, ya la renové",
        "No, la obtuve en 2020",
        "No, ya la conseguí",
    ],
)
def test_a_no_that_says_the_licence_was_got_goes_to_the_agent(text: str) -> None:
    """ "No, ya la saqué" is "no, (I did) get it": a "no" with an unnegated acquisition word or
    "ya" is never recorded, so it is never a Knockout."""
    for pending in (LICENCE_ES, LICENCE_MX):
        assert match_option(text, pending) is None
        assert check_spoken(text, pending) == SpokenDecision("plain")


@pytest.mark.parametrize(
    "text",
    [
        "No.",
        "No, no tengo.",
        "No, no la tengo, lo siento",
        "Eh, no.",
        "No, ya no la tengo",
        "No, no la saqué",
        "No, no tengo licencia. Nomás manejo una bici.",
    ],
)
def test_a_clean_no_is_still_recorded(text: str) -> None:
    for pending in (LICENCE_ES, LICENCE_MX):
        assert match_option(text, pending) == "no"
        assert check_spoken(text, pending) == SpokenDecision("option", option_id="no")


def test_a_clean_yes_is_unchanged() -> None:
    for pending in (LICENCE_ES, LICENCE_MX):
        assert match_option("Sí, la tengo.", pending) == "yes"
        assert check_spoken("Sí, la tengo.", pending) == SpokenDecision("option", option_id="yes")


# --------------------------------------------------------------------------- R13.2

# The spec's Matching table, the rows that are not slots, read from the fixtures by id.
NON_SLOT_TABLE = [
    (("es01", 2), LICENCE_ES, ("option", "yes")),
    (("es02", 2), LICENCE_ES, ("plain", None)),  # a hedge: the agent maps it
    (("mx02", 2), LICENCE_MX, ("option", "no")),
    (("mx01", 8), ZONE_CONFIRMATION, ("reask", None)),  # moderate and loud only
    (("es01", 4), AVAILABILITY_ES, ("option", "full_time")),
    (("es04", 4), AVAILABILITY_ES, ("option", "part_time")),
    (("es01", 6), PLATFORMS_ES, ("plain", None)),  # two platforms
    (("es05", 2), LICENCE_ES, ("optout_request", None)),
]


@pytest.mark.parametrize("fixture,pending,expected", NON_SLOT_TABLE)
def test_non_slot_matching_table(
    fixture: tuple[str, int], pending: Interactive, expected: tuple[str, str | None]
) -> None:
    conditions = ("moderate", "loud") if expected[0] == "reask" else CONDITIONS
    for condition in conditions:
        decision = check_spoken(said(*fixture, condition), pending)
        assert (decision.kind, decision.option_id) == expected, (fixture, condition)


# Every R13.2 option kind against named fixtures: (call, turn, conditions), choice, expected.
SPIKE4_TABLE: list[tuple[tuple[str, int, tuple[str, ...]], Interactive, str | None]] = [
    # licence yes/no
    (("es01", 2, CONDITIONS), LICENCE_ES, "yes"),
    (("es02", 2, CONDITIONS), LICENCE_ES, None),
    (("es04", 3, CONDITIONS), LICENCE_ES, "yes"),
    (("es06", 2, CONDITIONS), LICENCE_ES, "yes"),
    (("es09", 0, CONDITIONS), LICENCE_ES, "yes"),
    (("mx01", 2, CONDITIONS), LICENCE_MX, "yes"),
    (("mx02", 2, CONDITIONS), LICENCE_MX, "no"),
    (("mx04", 3, CONDITIONS), LICENCE_MX, "yes"),
    (("mx06", 1, CONDITIONS), LICENCE_MX, "yes"),
    (("mx09", 0, CONDITIONS), LICENCE_MX, "yes"),
    # Zone confirmation
    (("mx01", 8, ("clean",)), ZONE_CONFIRMATION, COYOACAN),
    (("mx01", 8, ("moderate", "loud")), ZONE_CONFIRMATION, None),
    (("mx09", 2, CONDITIONS), ZONE_CONFIRMATION, COYOACAN),
    # availability
    (("es01", 4, CONDITIONS), AVAILABILITY_ES, "full_time"),
    (("es04", 4, CONDITIONS), AVAILABILITY_ES, "part_time"),
    (("es06", 3, CONDITIONS), AVAILABILITY_ES, "part_time"),
    (("es06", 4, CONDITIONS), AVAILABILITY_ES, "weekends"),
    (("mx01", 4, CONDITIONS), AVAILABILITY_MX, "full_time"),
    (("mx06", 2, CONDITIONS), AVAILABILITY_MX, "part_time"),
    (("mx09", 4, CONDITIONS), AVAILABILITY_MX, "weekends"),
    # preferred schedule
    (("es01", 5, CONDITIONS), SCHEDULE_ES, "afternoon"),
    (("es04", 4, CONDITIONS), SCHEDULE_ES, "morning"),
    (("es08", 4, CONDITIONS), SCHEDULE_ES, "flexible"),
    (("mx01", 5, CONDITIONS), SCHEDULE_MX, "morning"),
    (("mx06", 4, CONDITIONS), SCHEDULE_MX, "evening"),
    # platforms, with "Ninguna app"
    (("es01", 6, CONDITIONS), PLATFORMS_ES, None),
    (("es08", 1, CONDITIONS), PLATFORMS_ES, "just_eat"),
    (("mx01", 6, CONDITIONS), PLATFORMS_MX, None),
    (("mx06", 3, CONDITIONS), PLATFORMS_MX, "uber_eats"),
    (("mx08", 1, CONDITIONS), PLATFORMS_MX, "didi_food"),
    # Zone ambiguity
    (("es07", 2, ("clean", "moderate")), ZONES_ACROSS, "zone_es_valencia_ruzafa"),
    (("es07", 2, ("loud",)), ZONES_ACROSS, None),
    (("es07", 3, CONDITIONS), ZONES_ACROSS, "zone_es_madrid_getafe"),
    (("es03", 1, CONDITIONS), ZONES_SEVILLA, "zone_es_sevilla_triana"),
    (("mx04", 4, CONDITIONS), ZONES_CDMX, "zone_mx_cdmx_juarez"),
    # start-date ambiguity
    (("es01", 7, CONDITIONS), START_DATES, None),
    (("mx01", 7, CONDITIONS), START_DATES, None),
    (("es08", 2, CONDITIONS), START_NOVEMBER, None),
]


def test_spike4_transcript_table() -> None:
    failures: list[str] = []
    decided = total = 0
    for (call, turn, conditions), pending, expected in SPIKE4_TABLE:
        for condition in conditions:
            total += 1
            text = said(call, turn, condition)
            found = match_option(text, pending)
            if found != expected:
                failures.append(f"{call} t{turn} {condition}: {found!r} != {expected!r}")
            if check_spoken(text, pending).kind in ("option", "reask"):
                decided += 1
    share = f"mapped to option or reask: {decided}/{total} ({decided / total:.0%})"
    print(share)  # the spec's Measurement, shown under -s
    assert not failures, "\n".join([share, *failures])


@pytest.mark.parametrize("hedge", ["No sé.", "No sé, la verdad", *HEDGE_CUES])
def test_hedges_and_non_answers_never_match(hedge: str) -> None:
    assert match_option(hedge, LICENCE_ES) is None
    assert match_option(f"Sí, {hedge}", LICENCE_ES) is None
    assert match_option(f"No, {hedge}", LICENCE_MX) is None


def test_hedge_cues_are_r13_2s() -> None:
    assert HEDGE_CUES == (
        "no se",
        "no estoy seguro",
        "pero",
        "aunque",
        "todavia",
        "caducado",
        "vencida",
        "suspendido",
        "sacando",
    )


def test_a_zone_name_alone_needs_no_other_city() -> None:
    centres = choice(
        ("zone_es_madrid_centro", "Centro (Madrid)"),
        ("zone_es_valencia_ruzafa", "Ruzafa (Valencia)"),
        ("zone_es_valencia_benimaclet", "Benimaclet (Valencia)"),
    )
    assert match_option("Vivo en el centro de Valencia", centres) is None
    assert match_option("Vivo en el centro de Madrid", centres) == "zone_es_madrid_centro"
    assert match_option("Vivo en el centro", centres) == "zone_es_madrid_centro"
    assert match_option("En Ruzafa, en Valencia", centres) == "zone_es_valencia_ruzafa"


def test_matching_is_on_whole_words() -> None:
    afternoon_only = choice(("pm_shift", "Tarde"), ("am_shift", "Temprano"))
    assert match_option("Por las tardes", afternoon_only) is None
    assert match_option("Por la tarde", afternoon_only) == "pm_shift"
    assert match_option("Nomás la bici", LICENCE_MX) is None
    assert match_option("De noche", LICENCE_MX) is None
    assert match_option("Nomás de noche", LICENCE_MX) is None
    assert match_option("No", LICENCE_MX) == "no"
    assert match_option("Glovos", PLATFORMS_ES) is None


# --------------------------------------------------------------------------- R13.3

MADRID = "Europe/Madrid"
MONTERREY = "America/Monterrey"


def slot(
    option_id: str, local: datetime, timezone: str, language: str = "es-ES"
) -> tuple[str, str]:
    """A slot option as ``pending_choice`` builds it: the label is ``repo.local_label``'s."""
    starts_at = iso(local.replace(tzinfo=ZoneInfo(timezone)).astimezone(UTC))
    return option_id, repo.local_label(starts_at, timezone, language)


def offers(*slots: tuple[str, str], language: str = "es-ES") -> Interactive:
    return choice(*slots, (OTHER_OPTION_ID, OTHER_LABEL[language]))


THU_15_1000 = slot("so_thu", datetime(2026, 10, 15, 10, 0), MADRID)
FRI_16_1730 = slot("so_fri", datetime(2026, 10, 16, 17, 30), MADRID)
FRI_16_0530 = slot("so_fri_early", datetime(2026, 10, 16, 5, 30), MADRID)
TUE_13_1100 = slot("so_tue", datetime(2026, 10, 13, 11, 0), MONTERREY, "es-MX")
WED_14_1600 = slot("so_wed", datetime(2026, 10, 14, 16, 0), MONTERREY, "es-MX")
SEVILLA_OFFERS = offers(THU_15_1000, FRI_16_1730)
MONTERREY_OFFERS = offers(TUE_13_1100, WED_14_1600, language="es-MX")


def test_slot_labels_follow_local_label() -> None:
    assert [THU_15_1000[1], FRI_16_1730[1]] == ["jue 15 · 10:00", "vie 16 · 17:30"]
    assert [TUE_13_1100[1], WED_14_1600[1]] == ["mar 13 · 11:00", "mié 14 · 16:00"]
    english = offers(slot("so_en", datetime(2026, 10, 16, 17, 30), MADRID, "en"), language="en")
    assert english["options"][0]["label"] == "Fri 16 · 17:30"
    assert match_option("Friday 16 at 17:30", english) == "so_en"


SLOT_TABLE = [
    (("es03", 2), SEVILLA_OFFERS, "so_thu"),
    (("es03", 3), SEVILLA_OFFERS, "so_fri"),
    (("mx03", 2), MONTERREY_OFFERS, "so_tue"),
    (("mx03", 3), MONTERREY_OFFERS, "so_wed"),
]


@pytest.mark.parametrize("condition", CONDITIONS)
@pytest.mark.parametrize("fixture,pending,expected", SLOT_TABLE)
def test_slot_matching_table(
    fixture: tuple[str, int], pending: Interactive, expected: str, condition: str
) -> None:
    text = said(*fixture, condition)
    assert match_option(text, pending) == expected
    assert check_spoken(text, pending) == SpokenDecision("option", option_id=expected)


def test_slot_matching_reads_spoken_number_words() -> None:
    clean_words = "Mejor el viernes dieciséis a las cinco y media de la tarde."
    assert match_option(clean_words, SEVILLA_OFFERS) == "so_fri"
    assert match_option("El jueves a las diez de la mañana", SEVILLA_OFFERS) == "so_thu"
    assert match_option("El jueves a las diez de la noche", SEVILLA_OFFERS) is None
    assert match_option("El viernes a las cinco y veinte", SEVILLA_OFFERS) is None
    assert match_option("El viernes a las seis menos cuarto", SEVILLA_OFFERS) is None


def test_an_ambiguous_twelve_hour_time_matches_nothing() -> None:
    both = offers(FRI_16_0530, FRI_16_1730)
    assert match_option("el viernes 16 a las 5:30", both) is None
    assert match_option("el viernes 16 a las 5:30", offers(FRI_16_1730)) == "so_fri"
    assert match_option("el viernes 16 a las 5:30", offers(FRI_16_0530)) == "so_fri_early"
    assert match_option("a las 5:30", both) is None
    assert match_option("a las 5:30", offers(FRI_16_1730)) is None  # no day
    # A period cue or a 24-hour time picks one.
    assert match_option("el viernes 16 a las 5:30 p.m.", both) == "so_fri"
    assert match_option("el viernes 16 a las 5:30 a. m.", both) == "so_fri_early"
    assert match_option("el viernes 16 a las 17:30", both) == "so_fri"


def test_a_day_and_date_that_disagree_match_nothing() -> None:
    assert match_option("El jueves 16 a las 5:30 p.m.", SEVILLA_OFFERS) is None
    assert match_option("El viernes 15 a las 10:00", SEVILLA_OFFERS) is None
    assert match_option("El jueves o el viernes a las 10:00", SEVILLA_OFFERS) is None
    assert match_option("El jueves a las 10:00 o a las 11:00", SEVILLA_OFFERS) is None


@pytest.mark.parametrize(
    "text",
    [
        "el viernes a las 5.30 de la tarde",
        "el viernes a las cinco treinta de la tarde",
        "el viernes a las 5 30 de la tarde",
        "el viernes a las 17.30",
    ],
)
def test_minutes_the_matcher_cannot_read_match_nothing(text: str) -> None:
    both = offers(
        slot("so_1700", datetime(2026, 10, 16, 17, 0), MADRID),
        slot("so_1730", datetime(2026, 10, 16, 17, 30), MADRID),
    )
    assert match_option(text, both) is None


def test_las_dos_as_both_is_no_hour() -> None:
    two = offers(
        slot("so_10", datetime(2026, 10, 16, 10, 0), MADRID),
        slot("so_14", datetime(2026, 10, 16, 14, 0), MADRID),
    )
    assert match_option("El viernes, cualquiera de las dos", two) is None
    assert match_option("El viernes, me valen las dos", two) is None
    assert match_option("El viernes a las dos de la tarde", two) == "so_14"


def test_twelve_at_night_is_midnight_not_noon() -> None:
    noon = offers(slot("so_noon", datetime(2026, 10, 16, 12, 0), MADRID))
    midnight = offers(slot("so_midnight", datetime(2026, 10, 16, 0, 0), MADRID))
    assert match_option("El viernes 16 a las doce de la noche", noon) is None
    assert match_option("El viernes 16 a las doce de la noche", midnight) == "so_midnight"
    assert match_option("El viernes 16 a las 12 p.m.", noon) == "so_noon"


def test_a_slot_needs_a_day_and_an_hour() -> None:
    assert match_option("El jueves me viene genial", SEVILLA_OFFERS) is None
    assert match_option("El 15 me viene genial", SEVILLA_OFFERS) is None
    assert match_option("A las 10:00 a.m.", SEVILLA_OFFERS) is None
    assert match_option("El 15 a las 10:00", SEVILLA_OFFERS) == "so_thu"
    assert match_option("Un a.m. cualquiera", SEVILLA_OFFERS) is None


def test_impossible_times_and_unknown_labels_match_nothing() -> None:
    assert match_option("El viernes 16 a las 17:30 p.m.", SEVILLA_OFFERS) is None
    assert match_option("El viernes 16 a las 25:00", SEVILLA_OFFERS) is None
    assert match_option("El viernes 40 a las 10:00", SEVILLA_OFFERS) is None
    unknown = offers(("so_x", "xyz 16 · 10:00"))
    assert match_option("El viernes 16 a las 10:00", unknown) is None
    assert spoken._has(["no"], "") is False


@pytest.mark.parametrize("text", ["Otro horario.", "¿Tienes otra hora?", "Ninguno me va bien"])
def test_the_other_time_option_matches_its_synonyms(text: str) -> None:
    assert match_option(text, SEVILLA_OFFERS) == OTHER_OPTION_ID
    assert match_option(text, MONTERREY_OFFERS) == OTHER_OPTION_ID
    assert match_option(f"{text} El jueves a las 10:00", SEVILLA_OFFERS) is None


# --------------------------------------------------------------------------- R13.5


@pytest.mark.parametrize(
    "text,pending,expected",
    [
        ("No, no me llaméis más", LICENCE_ES, SpokenDecision("optout_request")),
        (said("es10", 2), LICENCE_ES, SpokenDecision("optout_request")),
        (said("mx01", 8, "loud"), LICENCE_MX, SpokenDecision("reask")),
        (said("mx01", 8, "moderate"), ZONE_CONFIRMATION, SpokenDecision("reask")),
        ("Jornada completa", AVAILABILITY_ES, SpokenDecision("option", option_id="full_time")),
        (said("es01", 6), PLATFORMS_ES, SpokenDecision("plain")),
        ("Sí, está todo bien. ¡No, no!", AVAILABILITY_MX, SpokenDecision("plain")),
        ("Que no me llaméis más", None, SpokenDecision("optout_request")),
        ("Jornada completa", None, SpokenDecision("plain")),
        ("Sí, está todo bien. ¡No, no!", None, SpokenDecision("plain")),
    ],
)
def test_check_spoken_order(
    text: str, pending: Interactive | None, expected: SpokenDecision
) -> None:
    assert check_spoken(text, pending) == expected


def test_check_spoken_never_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    text = said("es01", 4)
    malformed: Any = {"style": "buttons", "options": [{"label": "Jornada completa"}]}
    assert check_spoken(text, malformed) == SpokenDecision("plain")

    def broken(value: str, *rest: Any) -> bool:
        raise ValueError(f"cannot read {value}")

    monkeypatch.setattr(spoken, "is_optout_phrase", broken)
    with caplog.at_level(logging.DEBUG):
        assert check_spoken(text, AVAILABILITY_ES) == SpokenDecision("plain")
    assert "ValueError" in caplog.text and text not in caplog.text
