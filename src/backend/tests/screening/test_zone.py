"""The Zone ranker behind ``search_zones`` and Q45's city list (ADR 0042).

``rank_zones`` is the only free-text reader kept: it suggests Zones, it never decides. What a
set of ids means (accepted, a choice, a confirmation, a Knockout) is ``verify_zone``'s job,
tested in ``test_verify.py`` and through ``record_answer``.
"""

from zoneinfo import ZoneInfo

import pytest

from criba.screening import (
    Invalid,
    ZoneRef,
    candidate_timezone,
    country_from_phone,
    rank_zones,
)
from criba.screening.results import REPLY_BUTTON_MAX
from criba.screening.text import contains_phrase, normalize
from criba.screening.zones import city_zones, place_word, zone_labels

Zones = tuple[ZoneRef, ...]

NAME_AND_CITY, NAME, CITY = 3, 2, 1


def ids(zones: Zones, raw: str, country: str | None = None) -> list[str]:
    _, matched = rank_zones(raw, zones)
    home = [z for z in matched if z.country == country]
    return sorted(z.id for z in (home or matched))


# --- one Zone ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "country", "expected"),
    [
        # es-ES
        ("Chamberí", "ES", "zone_es_madrid_chamberi"),
        ("chamberi", "ES", "zone_es_madrid_chamberi"),
        ("Vivo en Chamberí, Madrid", "ES", "zone_es_madrid_chamberi"),
        ("Madrid, Centro", "ES", "zone_es_madrid_centro"),
        ("por Sol", "ES", "zone_es_madrid_centro"),
        ("en Puente de Vallecas", "ES", "zone_es_madrid_vallecas"),
        ("Getafe", "ES", "zone_es_getafe"),
        ("Vegueta, Las Palmas", "ES", "zone_es_lpgc_vegueta"),
        ("Triana", "ES", "zone_es_sevilla_triana"),  # known, outside every Service Area
        # es-MX
        ("Vivo en la Roma", "MX", "zone_mx_cdmx_roma"),
        ("colonia Roma, CDMX", "MX", "zone_mx_cdmx_roma"),
        ("Centro de Puebla", "MX", "zone_mx_puebla_centro"),
        ("en el Centro Histórico", "MX", "zone_mx_cdmx_centro"),
        ("Zona Rio, Tijuana", "MX", "zone_mx_tijuana_rio"),
        ("Zona Hotelera, Cancún", "MX", "zone_mx_cancun_hotelera"),
        # en
        ("I live in Chamberí, Madrid", "ES", "zone_es_madrid_chamberi"),
        ("Downtown Querétaro, the Centro", "MX", "zone_mx_qro_centro"),
        ("I live in Cancun", "MX", "zone_mx_cancun_hotelera"),
        # unknown country: whole list
        ("Roma Norte", None, "zone_mx_cdmx_roma"),
    ],
)
def test_a_place_ranks_one_zone(zones: Zones, raw: str, country: str | None, expected: str) -> None:
    assert ids(zones, raw, country) == [expected]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Chamberri", "zone_es_madrid_chamberi"),  # typo
        ("Valleca", "zone_es_madrid_vallecas"),
    ],
)
def test_typos_are_tolerated(zones: Zones, raw: str, expected: str) -> None:
    assert ids(zones, raw, "ES") == [expected]


def test_name_and_city_beats_name_beats_city(zones: Zones) -> None:
    assert rank_zones("Centro, Puebla", zones)[0] == NAME_AND_CITY
    assert rank_zones("Chamberí", zones)[0] == NAME
    assert rank_zones("Madrid", zones)[0] == CITY
    tier, matched = rank_zones("Centro de Puebla", zones)
    assert tier == NAME_AND_CITY and [z.id for z in matched] == ["zone_mx_puebla_centro"]


def test_the_longer_name_wins_within_a_tier(zones: Zones) -> None:
    # "Centro Histórico" (an alias of CDMX Centro) beats every plain "Centro".
    assert ids(zones, "en el Centro Histórico") == ["zone_mx_cdmx_centro"]


def test_the_ranked_zone_maps_to_the_answers_shape(zones: Zones) -> None:
    _, matched = rank_zones("la Roma", zones)
    assert [z.as_answer() for z in matched] == [
        {"name": "Roma Norte", "city": "CDMX", "country": "MX"}
    ]


# --- several Zones ----------------------------------------------------------


def test_an_ambiguous_place_ranks_every_reading(zones: Zones) -> None:
    # The tools.json example: "vivo en el centro" in Mexico.
    assert ids(zones, "vivo en el centro", "MX") == [
        "zone_mx_cdmx_centro",
        "zone_mx_puebla_centro",
        "zone_mx_qro_centro",
    ]
    assert ids(zones, "I'm in the centro", "ES") == [
        "zone_es_madrid_centro",
        "zone_es_sevilla_centro",
    ]


def test_a_city_ranks_all_its_zones(zones: Zones) -> None:
    madrid = [
        "zone_es_madrid_centro",
        "zone_es_madrid_chamberi",
        "zone_es_madrid_salamanca",
        "zone_es_madrid_vallecas",
    ]
    for text in ("Madrid", "vivo en Madrid", "Madrid, Moratalaz"):  # last: unknown barrio
        tier, matched = rank_zones(text, zones)
        assert tier == CITY, text
        assert sorted(z.id for z in matched) == madrid
    assert ids(zones, "Sevilla") == ["zone_es_sevilla_centro", "zone_es_sevilla_triana"]


# --- nothing ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["Narnia", "no sé", "asdfgh", "I'd rather not say", "", "   ", "🏠", "zone_xx_unknown"],
)
def test_an_unknown_place_ranks_nothing(zones: Zones, raw: str) -> None:
    assert rank_zones(raw, zones) == (0, [])


def test_short_keys_do_not_fuzzy_match(zones: Zones) -> None:
    # "So" is close to the alias "Sol" but too short to guess.
    assert rank_zones("So", zones) == (0, [])


# --- Q45: a city's Zones as a list ------------------------------------------


def _big_city(n: int) -> Zones:
    return tuple(
        ZoneRef(
            id=f"zone_mx_big_{i:02d}",
            country="MX",
            city="Bigtown",
            name=f"Colonia {chr(65 + i)}",
            timezone="America/Mexico_City",
            covered=True,
        )
        for i in range(n)
    )


def test_a_city_with_four_to_ten_zones_is_offered_as_a_list(zones: Zones) -> None:
    result = city_zones([z for z in zones if z.city == "Madrid"])
    assert isinstance(result, Invalid)
    assert result.reason == "ambiguous_zone"
    assert result.narrowing
    assert [o.as_dict() for o in result.options] == [
        {"id": "zone_es_madrid_salamanca", "label": "Barrio de Salamanca"},
        {"id": "zone_es_madrid_centro", "label": "Centro"},
        {"id": "zone_es_madrid_chamberi", "label": "Chamberí"},
        {"id": "zone_es_madrid_vallecas", "label": "Vallecas"},
    ]
    assert all(len(o.label) <= 20 for o in result.options)
    assert result.hint.startswith("City recognised (Madrid); neighbourhood not recognised")
    assert "offer the list" in result.hint


@pytest.mark.parametrize(
    ("language", "word"), [("es-ES", "barrio"), ("es-MX", "colonia"), ("en", "neighbourhood")]
)
def test_the_city_reask_names_the_reply_languages_word(language: str, word: str) -> None:
    assert place_word(language) == word
    result = city_zones(list(_big_city(11)), language)
    assert result.hint.startswith(f"City recognised (Bigtown); {word} not recognised")
    assert f"ask which {word}, for example" in result.hint
    assert "neighbourhood" not in result.hint or word == "neighbourhood"


def test_a_city_with_ten_zones_still_gets_the_list() -> None:
    result = city_zones(list(_big_city(10)))
    assert isinstance(result, Invalid) and result.narrowing
    assert len(result.options) == 10


def test_a_city_with_more_than_ten_zones_keeps_the_text_reask_with_examples() -> None:
    result = city_zones(list(_big_city(11)))
    assert isinstance(result, Invalid)
    assert result.reason == "ambiguous_zone"
    assert not result.narrowing
    assert result.options == ()
    assert "City recognised (Bigtown)" in result.hint
    assert "'Colonia A, Bigtown', 'Colonia B, Bigtown', 'Colonia C, Bigtown'" in result.hint


def test_a_narrowing_invalid_needs_options() -> None:
    with pytest.raises(ValueError):
        Invalid(reason="ambiguous_zone", hint="x", narrowing=True)


def _cdmx(*names: str) -> list[ZoneRef]:
    return [
        ZoneRef(
            id=f"zone_mx_cdmx_{i}",
            country="MX",
            city="Ciudad de México",
            name=name,
            timezone="America/Mexico_City",
            covered=True,
        )
        for i, name in enumerate(names)
    ]


def test_a_cdmx_city_list_has_bare_zone_names() -> None:
    result = city_zones(_cdmx("Centro", "Polanco", "Condesa", "Santa Fe"))
    assert isinstance(result, Invalid) and result.narrowing
    assert [o.label for o in result.options] == ["Centro", "Condesa", "Polanco", "Santa Fe"]


def test_a_mixed_city_list_keeps_the_city_with_its_alias() -> None:
    puebla = ZoneRef(
        id="zone_mx_puebla_centro",
        country="MX",
        city="Puebla",
        name="Centro",
        timezone="America/Mexico_City",
        covered=True,
    )
    mixed = [*_cdmx("Centro"), puebla]
    assert zone_labels(mixed) == ["Centro (CDMX)", "Centro (Puebla)"]
    assert mixed[0].label == "Centro (CDMX)"


def test_a_lone_zone_keeps_its_city() -> None:
    assert zone_labels(_cdmx("Polanco")) == ["Polanco (CDMX)"]


def test_every_zone_label_is_within_the_limit(zones: Zones) -> None:
    long_names = _cdmx("Colonia Hipódromo Condesa Norte", "Ex Hacienda de San Juan de Dios")
    other_city = [z for z in zones if z.city != "Ciudad de México"]
    for batch in (long_names, list(zones), other_city, [*long_names, *other_city]):
        labels = zone_labels(batch)
        assert len(labels) == len(batch)
        assert all(0 < len(label) <= REPLY_BUTTON_MAX for label in labels), labels
    assert zone_labels(long_names)[0].endswith("…")


def test_labels_fit_whatsapp_reply_buttons() -> None:
    long = ZoneRef(
        id="z1",
        country="ES",
        city="Las Palmas de Gran Canaria",
        name="Vegueta",
        timezone="Atlantic/Canary",
        covered=True,
    )
    assert len(long.label) <= 20
    assert long.label.endswith("…")


# --- country and time zone --------------------------------------------------


@pytest.mark.parametrize(
    ("phone", "country"),
    [
        ("+34612345678", "ES"),
        ("34 612 345 678", "ES"),
        ("+52 1 222 123 4567", "MX"),
        ("+15551234567", None),
        ("", None),
        (None, None),
    ],
)
def test_country_from_phone(phone: str | None, country: str | None) -> None:
    assert country_from_phone(phone) == country


def test_candidate_timezone_prefers_the_zone(zones: Zones) -> None:
    tijuana = next(z for z in zones if z.id == "zone_mx_tijuana_rio")
    assert candidate_timezone(tijuana, "MX") == ZoneInfo("America/Tijuana")
    assert candidate_timezone(None, "MX") == ZoneInfo("America/Mexico_City")
    assert candidate_timezone(None, "ES") == ZoneInfo("Europe/Madrid")
    assert candidate_timezone(None, None) == ZoneInfo("UTC")


def test_phrases_match_whole_words_only() -> None:
    assert contains_phrase(normalize("Vivo en Chamberí, Madrid"), "chamberi")
    assert not contains_phrase("chamberilla", "chamberi")
    assert not contains_phrase("madrid", "")  # an empty key never matches
