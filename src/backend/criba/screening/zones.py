"""Zones: reference rows, the place-name ranker behind ``search_zones``, and time zones.

The Screening Agent looks the Candidate's place up with ``search_zones``, which ranks the
seeded Zones with :func:`rank_zones`, and passes ``record_answer`` the Zone ids it picked;
``criba.screening.verify.verify_zone`` checks those ids and decides (ADR 0042). Reference
data is passed in (``criba.tools.zones_lookup`` loads it from ``zones`` and
``service_area_zones``); this module never reads the database.

Ranking: whole words against each Zone's name, aliases and city, accent- and
case-insensitively, with a typo-tolerant fallback. The most specific match wins (name and
city, then name, then city alone). :func:`city_zones` turns a known city without a single
Zone into Q45's list of its Zones, up to ten (a narrowing step, not a miss), or a text
re-ask with example neighbourhoods for a bigger city.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from criba.screening.results import Invalid, Option, truncate_label
from criba.screening.text import contains_phrase, normalize

COUNTRY_TIMEZONES: dict[str, str] = {"ES": "Europe/Madrid", "MX": "America/Mexico_City"}
PHONE_PREFIX_COUNTRIES: dict[str, str] = {"+34": "ES", "+52": "MX"}

# The word for the part of a place below the city, per reply language: Spain says barrio,
# Mexico colonia. The hints name it so the agent does not borrow the other country's word.
PLACE_WORDS: dict[str, str] = {"es-ES": "barrio", "es-MX": "colonia"}


def place_word(language: str) -> str:
    """The reply language's word for a neighbourhood (English: ``neighbourhood``)."""
    return PLACE_WORDS.get(language, "neighbourhood")


_FUZZY_CUTOFF = 0.85
_FUZZY_MIN_LEN = 5
_MAX_LIST = 10  # WhatsApp list rows; tools.json allows 10 options on tool_error
_EXAMPLES = 3

# Short city names for list labels that must show the city (a list spanning cities). The
# seed has no such data: its Zone aliases are for matching a Candidate's words, not labels.
CITY_ALIASES: dict[str, str] = {"Ciudad de México": "CDMX"}

# Match tiers: higher wins. CITY_TIER: the city matched, but no Zone name in it.
_TIER_NAME_AND_CITY = 3
_TIER_NAME = 2
CITY_TIER = 1


@dataclass(frozen=True, slots=True)
class ZoneRef:
    """One row of `zones`, plus whether any Service Area contains it."""

    id: str
    country: str
    city: str
    name: str
    timezone: str
    covered: bool
    aliases: tuple[str, ...] = field(default=())

    def as_answer(self) -> dict[str, str]:
        """The `answers.zone` shape from tools.json."""
        return {"name": self.name, "city": self.city, "country": self.country}

    @property
    def label(self) -> str:
        """``Name (City)`` with the city's short alias, for a Zone shown on its own."""
        if normalize(self.name) == normalize(self.city):
            return truncate_label(self.name)
        return truncate_label(f"{self.name} ({CITY_ALIASES.get(self.city, self.city)})")


def zone_labels(zones: Sequence[ZoneRef]) -> list[str]:
    """Labels for one list of Zones, in the same order.

    A list whose Zones all share a city names just the Zone: the city is the question's
    context and would only eat the WhatsApp label limit. A list across cities keeps
    ``Name (City)``, with the city's short alias where it has one. A lone Zone keeps its
    city too. Every label is cut to the limit in ``criba.screening.results``.
    """
    if len(zones) > 1 and len({(z.country, z.city) for z in zones}) == 1:
        return [truncate_label(z.name) for z in zones]
    return [z.label for z in zones]


def country_from_phone(phone: str | None) -> str | None:
    """'ES' for +34, 'MX' for +52, otherwise None. Accepts E.164 with or without '+'."""
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    if not digits.startswith("+"):
        digits = "+" + digits.lstrip("0")
    for prefix, country in PHONE_PREFIX_COUNTRIES.items():
        if digits.startswith(prefix):
            return country
    return None


def candidate_timezone(zone: ZoneRef | None = None, country: str | None = None) -> ZoneInfo:
    """The Candidate's local time zone: the Zone's, else the country's, else UTC."""
    if zone is not None:
        return ZoneInfo(zone.timezone)
    if country in COUNTRY_TIMEZONES:
        return ZoneInfo(COUNTRY_TIMEZONES[country])
    return ZoneInfo("UTC")


def rank_zones(query: str, zones: Sequence[ZoneRef]) -> tuple[int, list[ZoneRef]]:
    """The best-matching Zones for a place name, and their match tier (0 for none).

    Whole words against each Zone's name, aliases and city, accent- and case-insensitive,
    with a typo-tolerant fallback; only the top tier is returned (name and city, then name,
    then city alone). The only free-text reader kept by ADR 0042, behind ``search_zones``.
    """
    text = normalize(query)
    if not text:
        return 0, []
    return _best_matches(text, list(zones))


def _keys(zone: ZoneRef) -> list[str]:
    return [k for k in (normalize(n) for n in (zone.name, *zone.aliases)) if k]


def _best_matches(text: str, zones: list[ZoneRef]) -> tuple[int, list[ZoneRef]]:
    scored = _score(text, zones, _phrase_hit) or _score(text, zones, _fuzzy_hit)
    if not scored:
        return 0, []
    top = max(scored.values())
    return top[0], [z for z in zones if scored.get(z.id) == top]


def _score(
    text: str, zones: list[ZoneRef], hit: Callable[[str, str], bool]
) -> dict[str, tuple[int, int]]:
    """(tier, length of the longest matching name) per matching Zone.

    The length breaks ties within a tier, so "Centro Histórico" beats plain "Centro".
    """
    scores: dict[str, tuple[int, int]] = {}
    for zone in zones:
        name_len = max((len(k) for k in _keys(zone) if hit(text, k)), default=0)
        city = normalize(zone.city)
        city_hit = hit(text, city)
        if name_len and city_hit and city not in _keys(zone):
            scores[zone.id] = (_TIER_NAME_AND_CITY, name_len)
        elif name_len:
            scores[zone.id] = (_TIER_NAME, name_len)
        elif city_hit:
            scores[zone.id] = (CITY_TIER, 0)
    return scores


def _phrase_hit(text: str, key: str) -> bool:
    return contains_phrase(text, key)


def _fuzzy_hit(text: str, key: str) -> bool:
    if len(key) < _FUZZY_MIN_LEN:
        return False
    words = text.split()
    width = len(key.split())
    grams = [" ".join(words[i : i + width]) for i in range(len(words) - width + 1)]
    return bool(difflib.get_close_matches(key, grams, n=1, cutoff=_FUZZY_CUTOFF))


def city_zones(matched: list[ZoneRef], language: str = "en") -> Invalid:
    """The Candidate named a known city, but no single Zone in it (Q45).

    Up to ten Zones: offer them all as a list (a narrowing step, not a miss). More:
    ask for the neighbourhood in text, with a few examples.
    """
    city = matched[0].city
    ordered = sorted(matched, key=lambda z: (normalize(z.name), z.id))
    word = place_word(language)
    recognised = f"City recognised ({city}); {word} not recognised or not given"
    if len(ordered) <= _MAX_LIST:
        return Invalid(
            reason="ambiguous_zone",
            hint=f"{recognised}; offer the list of its areas.",
            options=tuple(
                Option(id=z.id, label=label)
                for z, label in zip(ordered, zone_labels(ordered), strict=True)
            ),
            narrowing=True,
        )
    examples = ", ".join(f"'{z.name}, {city}'" for z in ordered[:_EXAMPLES])
    return Invalid(
        reason="ambiguous_zone",
        hint=f"{recognised}; ask which {word}, for example {examples}.",
    )
