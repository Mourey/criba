"""Verify functions for structured answer values (ADR 0042, spec 006a task 6).

Boundary tests per field, and the reachable Knockouts: exactly an uncovered Zone (tapped,
or after a Knockout) and the four non-yes licence codes.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from criba.screening import Accepted, Invalid, Knockout, NeedsChoice, ZoneRef
from criba.screening.results import (
    EXPERIENCE_PLATFORMS_SKIPPED,
    LIST_ROW_MAX,
    REPLY_BUTTON_MAX,
    START_DATE_FAR,
)
from criba.screening.verify import (
    UNRESOLVED_HINTS,
    codes,
    date_label,
    field_options,
    unresolved,
    verify_codes,
    verify_driver_license,
    verify_experience_years,
    verify_full_name,
    verify_start_date,
    verify_value,
    verify_zone,
)

from .conftest import ZONES

TODAY = date(2026, 10, 9)
FIELDS = list(UNRESOLVED_HINTS)


# --- full_name ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Ana García", "Ana García"),
        ("ana garcía", "Ana García"),
        ("JOHN O'BRIEN", "John O'Brien"),
        ("maría de la o", "María de la O"),
        ("Jean-Luc Picard", "Jean-Luc Picard"),
        ("ana garcía-lópez", "Ana García-López"),
        ("  Íñigo   Ortúzar  ", "Íñigo Ortúzar"),  # whitespace collapsed
        ("luis hernández de la cruz", "Luis Hernández de la Cruz"),  # particles stay lower
        ("María de los Ángeles Núñez", "María de los Ángeles Núñez"),
        ("JUAN PÉREZ", "Juan Pérez"),
        ("mary-jane watson", "Mary-Jane Watson"),
        ("Sean O\u2019Brien", "Sean O\u2019Brien"),  # typographic apostrophe
        ("Ana van der Berg", "Ana van der Berg"),  # mixed case is kept as typed
        ("McDonald Ruiz", "McDonald Ruiz"),
        ("Guadalupe Ramírez.", "Guadalupe Ramírez"),  # trailing punctuation dropped
        # The agent removes "me llamo" (ADR 0042); code no longer strips it with a word list.
        ("Me llamo Ana García", "Me llamo Ana García"),
        ("a b c d e f g h", "A B C D E F G H"),  # 8 words
        ("Al Bo", "Al Bo"),  # exactly 4 letters
        ("Ab " + "c" * 76, "Ab " + "c" * 76),  # 79 characters
        ("Ab " + "c" * 77, "Ab " + "c" * 77),  # 80 characters
    ],
)
def test_names_that_pass(value: str, expected: str) -> None:
    assert verify_full_name(value) == Accepted(value=expected)


@pytest.mark.parametrize(
    "value",
    [
        "Ana",  # one word
        "a b c d e f g h i",  # 9 words
        "Al B",  # 3 letters
        "Ab " + "c" * 78,  # 81 characters
        "Ana García 2",  # digits
        "Ana -García",  # a joiner at the edge
        "Ana_García López",
        "ana@garcia.com",
        "Ana 😊 García",  # emoji
        "- García",  # a joiner alone
        "A B",  # too few letters
        "",
        "   ",
        "uno dos tres cuatro cinco seis siete ocho nueve",  # a sentence, not a name
    ],
)
def test_names_that_fail(value: str) -> None:
    result = verify_full_name(value)
    assert isinstance(result, Invalid) and result.reason == "invalid_name" and result.counted


# --- zone ---------------------------------------------------------------------------

BY_ID = {z.id: z for z in ZONES}
MADRID_4 = [
    "zone_es_madrid_chamberi",
    "zone_es_madrid_centro",
    "zone_es_madrid_salamanca",
    "zone_es_madrid_vallecas",
]
CENTROS = ["zone_mx_puebla_centro", "zone_mx_cdmx_centro", "zone_mx_qro_centro", MADRID_4[1]]


def test_one_covered_zone_is_accepted() -> None:
    assert verify_zone(["zone_es_madrid_chamberi"], ZONES) == Accepted(BY_ID[MADRID_4[0]])


@pytest.mark.parametrize(("language", "yes"), [("es-ES", "Sí"), ("es-MX", "Sí"), ("en", "Yes")])
def test_one_uncovered_zone_is_confirmed_first(language: str, yes: str) -> None:
    result = verify_zone(["zone_mx_cancun_hotelera"], ZONES, language=language)
    assert isinstance(result, NeedsChoice)
    assert result.confirm == "zone_mx_cancun_hotelera"
    assert [o.as_dict() for o in result.options] == [
        {"id": "zone_mx_cancun_hotelera", "label": yes},
        {"id": "no", "label": "No"},
    ]


def test_one_uncovered_zone_tapped_from_our_options_is_the_knockout() -> None:
    result = verify_zone(
        ["zone_mx_cancun_hotelera"], ZONES, offered={"zone_mx_cancun_hotelera", "no"}
    )
    assert result == Knockout("zone", "outside_service_areas", BY_ID["zone_mx_cancun_hotelera"])


def test_one_covered_zone_tapped_is_accepted() -> None:
    result = verify_zone(["zone_es_madrid_centro"], ZONES, offered=set(MADRID_4))
    assert result == Accepted(BY_ID["zone_es_madrid_centro"])


def test_after_a_knockout_an_uncovered_zone_is_a_knockout_result_directly() -> None:
    result = verify_zone(["zone_mx_cancun_hotelera"], ZONES, confirm_uncovered=False)
    assert isinstance(result, Knockout)


@pytest.mark.parametrize(
    "ids",
    [
        ["zone_es_sevilla_centro", "zone_es_sevilla_triana"],  # 2, none covered
        ["zone_mx_puebla_centro", "zone_mx_cdmx_centro", "zone_mx_qro_centro"],  # 3 cities
        ["zone_es_madrid_chamberi", "zone_es_sevilla_triana"],
    ],
)
def test_two_or_three_zones_are_a_choice_covered_or_not(ids: list[str]) -> None:
    result = verify_zone(ids, ZONES)
    assert isinstance(result, NeedsChoice) and result.confirm is None
    assert sorted(o.id for o in result.options) == sorted(ids)
    covered = [BY_ID[o.id].covered for o in result.options]
    assert covered == sorted(covered, reverse=True)  # covered first


def test_four_zones_of_one_city_are_the_q45_list_without_a_miss() -> None:
    result = verify_zone(MADRID_4, ZONES)
    assert isinstance(result, Invalid) and result.narrowing
    assert result.reason == "ambiguous_zone"
    assert sorted(o.id for o in result.options) == sorted(MADRID_4)


def test_four_zones_across_cities_are_an_ambiguous_zone_miss() -> None:
    result = verify_zone(CENTROS, ZONES)
    assert isinstance(result, Invalid)
    assert result.reason == "ambiguous_zone" and result.counted and not result.narrowing
    assert not result.options


@pytest.mark.parametrize("ids", [["zone_es_atlantis"], ["zone_es_madrid_chamberi", "zone_x"], []])
def test_unknown_ids_are_not_counted(ids: list[str]) -> None:
    result = verify_zone(ids, ZONES)
    assert isinstance(result, Invalid)
    assert result.reason == "unknown_zone_id" and not result.counted
    assert "search_zones" in result.hint


def test_ten_zones_of_one_city_are_still_a_list() -> None:
    city = tuple(
        ZoneRef(f"zone_t_{i}", "ES", "Testville", f"B{i}", "Europe/Madrid", covered=i % 2 == 0)
        for i in range(10)
    )
    result = verify_zone([z.id for z in city], city)
    assert isinstance(result, Invalid) and result.narrowing and len(result.options) == 10


# --- driver_license -----------------------------------------------------------------


def test_yes_is_accepted_as_true() -> None:
    assert verify_driver_license("yes") == Accepted(value=True)


@pytest.mark.parametrize("code", ["no", "expired", "suspended", "pending"])
def test_each_other_code_is_a_knockout_with_its_reason(code: str) -> None:
    assert verify_driver_license(code) == Knockout("driver_license", code, False)


def test_an_unknown_licence_code_is_not_counted() -> None:
    result = verify_driver_license("maybe")
    assert isinstance(result, Invalid) and result.reason == "invalid_option" and not result.counted


# --- option lists -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "values", "expected"),
    [
        ("availability", ["weekends", "full_time"], ["full_time", "weekends"]),
        ("availability", ["part_time"], ["part_time"]),
        ("preferred_schedule", ["flexible", "morning"], ["morning", "flexible"]),
        ("experience_platforms", ["other", "glovo"], ["glovo", "other"]),
        ("experience_platforms", [], []),
    ],
)
def test_codes_are_kept_in_canonical_order(field: str, values, expected) -> None:
    assert verify_codes(field, values) == Accepted(value=expected)


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("availability", []),
        ("availability", ["night"]),
        ("preferred_schedule", []),
        ("experience_platforms", ["deliveroo"]),
    ],
)
def test_codes_outside_the_enum_or_empty_lists_are_not_counted(field: str, values) -> None:
    result = verify_codes(field, values)
    assert isinstance(result, Invalid) and result.reason == "invalid_option" and not result.counted


def test_canonical_order_is_the_contract_enum_order() -> None:
    assert codes("availability") == ("full_time", "part_time", "weekends")
    assert codes("preferred_schedule") == ("morning", "afternoon", "evening", "flexible")
    assert codes("experience_platforms") == (
        "glovo",
        "uber_eats",
        "just_eat",
        "rappi",
        "didi_food",
        "other",
    )


# --- experience_years ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("years", "value", "flags"),
    [
        (0, 0.0, (EXPERIENCE_PLATFORMS_SKIPPED,)),
        (0.5, 0.5, ()),
        (1.333333, 1.33, ()),
        (60, 60.0, ()),
    ],
)
def test_years_in_range(years: float, value: float, flags: tuple) -> None:
    assert verify_experience_years(years) == Accepted(value=value, flags=flags)


@pytest.mark.parametrize("years", [60.01, 61, 1000, -0.01])
def test_years_out_of_range_are_a_miss(years: float) -> None:
    result = verify_experience_years(years)
    assert isinstance(result, Invalid) and result.reason == "out_of_range" and result.counted


# --- start_date ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "flags"),
    [
        ("2026-10-09", ()),  # today
        ("2026-10-12", ()),
        ("2026-12-08", ()),  # 60 days out
        ("2026-12-09", (START_DATE_FAR,)),  # 61 days out
        ("2027-02-28", (START_DATE_FAR,)),
    ],
)
def test_start_dates_today_or_later(value: str, flags: tuple) -> None:
    assert verify_start_date(value, today=TODAY) == Accepted(value=value, flags=flags)


def test_yesterday_is_a_past_start_date_miss() -> None:
    result = verify_start_date("2026-10-08", today=TODAY)
    assert isinstance(result, Invalid) and result.reason == "past_start_date" and result.counted


@pytest.mark.parametrize("value", ["2026-02-30", "2026-13-01", "20261012", "2026-W41-5", "ahora"])
def test_a_date_that_does_not_exist_is_not_counted(value: str) -> None:
    result = verify_start_date(value, today=TODAY)
    assert isinstance(result, Invalid) and result.reason == "invalid_date" and not result.counted


# --- start_date: two readings (ADR 0054) --------------------------------------------


@pytest.mark.parametrize(
    ("language", "labels"),
    [
        ("es-MX", ["Domingo 11 oct", "Domingo 18 oct"]),
        ("es-ES", ["Domingo 11 oct", "Domingo 18 oct"]),
        ("en", ["Sunday 11 Oct", "Sunday 18 Oct"]),
    ],
)
def test_two_future_dates_are_a_choice_of_both(language: str, labels: list[str]) -> None:
    """ "el próximo domingo" on Friday 9 October fits Sunday 11 and Sunday 18: the backend
    asks with two buttons whose ids are the dates, in date order, whatever order they came."""
    for value in (["2026-10-11", "2026-10-18"], ["2026-10-18", "2026-10-11"]):
        result = verify_start_date(value, today=TODAY, language=language)
        assert isinstance(result, NeedsChoice) and result.confirm is None
        assert [o.id for o in result.options] == ["2026-10-11", "2026-10-18"]
        assert [o.label for o in result.options] == labels


def test_a_past_reading_is_dropped_and_the_other_accepted() -> None:
    result = verify_start_date(["2026-10-08", "2026-10-15"], today=TODAY)
    assert result == Accepted(value="2026-10-15")
    far = verify_start_date(["2026-10-08", "2027-01-15"], today=TODAY)
    assert far == Accepted(value="2027-01-15", flags=(START_DATE_FAR,))


def test_two_past_readings_are_one_past_start_date_miss() -> None:
    result = verify_start_date(["2026-10-01", "2026-10-08"], today=TODAY)
    assert isinstance(result, Invalid) and result.reason == "past_start_date" and result.counted


def test_a_reading_that_is_not_a_date_is_not_counted() -> None:
    result = verify_start_date(["2026-02-30", "2026-10-11"], today=TODAY)
    assert isinstance(result, Invalid) and result.reason == "invalid_date" and not result.counted


# One week from Monday 2026-10-12: every weekday is today once (ADR 0058).
SAME_DAY_CASES = [date(2026, 10, 12) + timedelta(days=n) for n in range(7)]


@pytest.mark.parametrize("today", SAME_DAY_CASES, ids=lambda d: d.strftime("%A"))
def test_a_choice_holding_today_is_sent_back_without_a_miss(today: date) -> None:
    """The live run of Sunday 2026-10-11: "el próximo domingo" came as [today, +7]. A weekday
    is its next occurrence, never today (ADR 0054), so a list holding today was counted from
    today: the agent is told to take both dates from the line, and no miss is counted."""
    week = today + timedelta(days=7)
    for value in (
        [today.isoformat(), week.isoformat()],
        [week.isoformat(), today.isoformat()],
        [(today - timedelta(days=7)).isoformat(), today.isoformat()],
    ):
        result = verify_start_date(value, today=today, language="es-MX")
        assert isinstance(result, Invalid) and not result.counted, value
        assert result.reason == "today_in_date_choice"
        assert "[criba date]" in result.hint and "never today" in result.hint
    # The two readings ADR 0054 means on that day: 7 and 14 days out, offered as buttons.
    right = verify_start_date(
        [week.isoformat(), (week + timedelta(days=7)).isoformat()], today=today, language="es-MX"
    )
    assert isinstance(right, NeedsChoice)
    assert [o.id for o in right.options] == [
        week.isoformat(),
        (week + timedelta(days=7)).isoformat(),
    ]


def test_today_alone_is_still_a_start_date() -> None:
    """Only a list is two readings of a weekday: one date of today ("hoy") is accepted."""
    assert verify_start_date(TODAY.isoformat(), today=TODAY) == Accepted(value=TODAY.isoformat())


def test_every_start_date_label_fits_a_reply_button() -> None:
    days = [date(2026, 1, 1) + timedelta(days=n) for n in range(366)]
    for language in ("es-ES", "es-MX", "en"):
        longest = max((date_label(d.isoformat(), language) for d in days), key=len)
        assert len(longest) <= REPLY_BUTTON_MAX, longest
    assert date_label("2026-09-30", "es-ES") == "Miércoles 30 sept"


def test_verify_value_passes_the_language_to_a_start_date_choice() -> None:
    result = verify_value("start_date", ["2026-10-11", "2026-10-18"], today=TODAY, language="en")
    assert isinstance(result, NeedsChoice) and result.options[0].label == "Sunday 11 Oct"


# --- null and the dispatcher --------------------------------------------------------


@pytest.mark.parametrize("field", FIELDS)
def test_null_is_an_unresolved_miss_with_the_fields_hint(field: str) -> None:
    result = verify_value(field, None, today=TODAY)
    assert result == unresolved(field)
    assert isinstance(result, Invalid) and result.reason == "unresolved" and result.counted
    # The Zone hint names the reply language's word: Spain's barrio, the default language here.
    base = UNRESOLVED_HINTS[field]
    assert result.hint == (base.replace("neighbourhood", "barrio") if field == "zone" else base)


@pytest.mark.parametrize(
    ("language", "word", "other"),
    [
        ("es-ES", "barrio", "colonia"),
        ("es-MX", "colonia", "barrio"),
        ("en", "neighbourhood", "colonia"),
    ],
)
def test_the_zone_hints_name_the_reply_languages_word(language: str, word: str, other: str) -> None:
    """Spain asks for the barrio, Mexico for the colonia, English for the neighbourhood: the
    hint the agent words its re-ask from never carries the other country's word."""
    hint = unresolved("zone", language).hint
    assert word in hint and other not in hint
    assert verify_value("zone", None, today=TODAY, language=language).hint == hint
    ambiguous = verify_zone(CENTROS, ZONES, language=language)
    assert isinstance(ambiguous, Invalid) and ambiguous.reason == "ambiguous_zone"
    assert word in ambiguous.hint and other not in ambiguous.hint


def test_null_carries_the_fixed_options_in_the_candidates_language() -> None:
    result = unresolved("driver_license", "en")
    assert [o.id for o in result.options] == ["yes", "no"]
    assert result.options[0].label == "Yes, I have one"
    assert unresolved("zone").options == ()


@pytest.mark.parametrize(
    ("language", "label"), [("es-ES", "Ninguna app"), ("es-MX", "Ninguna app"), ("en", "No app")]
)
def test_the_platform_options_end_with_no_app(language: str, label: str) -> None:
    """ADR 0053: a Candidate who delivered on their own has a row to tap from the first
    question; it is not a platform code (its tap is recorded as the empty list)."""
    options = field_options("experience_platforms", language)
    assert [o.id for o in options] == [*codes("experience_platforms"), "none"]
    assert options[-1].label == label
    assert "none" not in codes("experience_platforms")
    assert all(len(o.label) <= LIST_ROW_MAX for o in options)


def test_the_platforms_hint_says_no_app_is_the_empty_list() -> None:
    """The demo chat of 2026-10-10: "ninguna de ellos" reached record_answer as null."""
    assert "empty list []" in unresolved("experience_platforms").hint
    assert verify_codes("experience_platforms", []) == Accepted([])


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("full_name", "ana garcía", Accepted("Ana García")),
        ("zone", ["zone_es_madrid_chamberi"], Accepted(BY_ID["zone_es_madrid_chamberi"])),
        ("driver_license", "yes", Accepted(True)),
        ("availability", ["full_time"], Accepted(["full_time"])),
        ("preferred_schedule", ["evening"], Accepted(["evening"])),
        ("experience_years", 2, Accepted(2.0)),
        ("experience_platforms", ["rappi"], Accepted(["rappi"])),
        ("start_date", "2026-10-12", Accepted("2026-10-12")),
    ],
)
def test_verify_value_dispatches_per_field(field: str, value, expected) -> None:
    assert verify_value(field, value, zones=ZONES, today=TODAY) == expected


def test_verify_value_needs_today_for_a_start_date() -> None:
    with pytest.raises(ValueError, match="today"):
        verify_value("start_date", "2026-10-12")


def test_verify_value_rejects_an_unknown_field() -> None:
    with pytest.raises(KeyError):
        verify_value("salary", 1000)


def test_the_only_reachable_knockouts() -> None:
    """Zone (uncovered, tapped or after a Knockout) and the four non-yes licence codes."""
    knockouts = set()
    for code in ("yes", "no", "expired", "suspended", "pending"):
        if isinstance(r := verify_driver_license(code), Knockout):
            knockouts.add((r.criterion, r.reason))
    for offered, confirm in ((set(), True), ({"zone_mx_cancun_hotelera"}, True), (set(), False)):
        r = verify_zone(
            ["zone_mx_cancun_hotelera"], ZONES, offered=offered, confirm_uncovered=confirm
        )
        if isinstance(r, Knockout):
            knockouts.add((r.criterion, r.reason))
    assert knockouts == {
        ("driver_license", "no"),
        ("driver_license", "expired"),
        ("driver_license", "suspended"),
        ("driver_license", "pending"),
        ("zone", "outside_service_areas"),
    }
