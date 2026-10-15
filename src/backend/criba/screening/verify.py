"""Verify structured answer values (ADR 0042, spec 006a task 6).

The Screening Agent reads the Candidate's words and passes ``record_answer`` a structured
value per field (``x-answer-values`` in ``contracts/tools.json``). The functions here check
that value against the rules and decide Knockouts. They read no free text: a value of the wrong
shape never gets here (``record_answer`` answers it with ``invalid_args``).

Each returns a ``ValidationResult`` (see ``results.py``):

- full_name: ``Accepted(str)``, the name the agent cleaned; 2 to 8 words of letters and
  joiners, at least 4 letters, at most 80 characters; all-lower or all-upper input is
  title-cased. Else ``invalid_name``.
- zone: ids must exist (else ``unknown_zone_id``, not counted). One covered: ``Accepted``.
  One uncovered: a Sí/No confirmation (``NeedsChoice`` with ``confirm``), unless the
  Candidate tapped it from options we offered, which is the Knockout
  ``outside_service_areas``. 2-3: ``NeedsChoice``. 4-10 in one city: Q45's list
  (``city_zones``). 4 or more across cities: ``ambiguous_zone`` (counted).
- driver_license: ``yes`` is ``Accepted(True)``; ``no``, ``expired``, ``suspended`` and
  ``pending`` are Knockouts with that reason (and template variant), value False.
- availability, preferred_schedule, experience_platforms: codes in canonical order; only the
  platforms may be empty (no app: they delivered on their own).
- experience_years: at most 60 (else ``out_of_range``); 0 flags
  ``experience_platforms_skipped``.
- start_date: a real date (else ``invalid_date``, not counted), today or later in the
  Candidate's calendar (else ``past_start_date``); over 60 days out flags ``start_date_far``.
  Two dates (the two days the words fit, ADR 0054): a past one is dropped; two left are a
  ``NeedsChoice`` with the dates as option ids. A list holding today is
  ``today_in_date_choice``, not counted: a weekday is never today (ADR 0058).
- ``null`` (:func:`unresolved`): the Candidate answered, the agent could not map it:
  ``unresolved``, counted, with the field's hint.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Sequence
from datetime import date, timedelta
from typing import Any

from criba.screening import vocab
from criba.screening.results import (
    EXPERIENCE_PLATFORMS_SKIPPED,
    START_DATE_FAR,
    Accepted,
    Invalid,
    Knockout,
    NeedsChoice,
    Option,
    ValidationResult,
)
from criba.screening.zones import ZoneRef, city_zones, place_word, zone_labels

START_DATE_FAR_DAYS = 60
MAX_EXPERIENCE_YEARS = 60
MAX_CHOICES = 3
NOT_CONFIRMED = "no"  # the No button of a Zone confirmation; it reaches record_answer as null

LICENSE_KNOCKOUTS = ("no", "expired", "suspended", "pending")
_CONFIRM_LABELS = {"en": ("Yes", "No")}
_CONFIRM_LABELS_ES = ("Sí", "No")

# The hint for a null value (reason ``unresolved``), per field.
UNRESOLVED_HINTS: dict[str, str] = {
    "full_name": (
        "The name was not clear. Ask for their first name and surname, with an example "
        "such as 'Ana García'."
    ),
    "zone": (
        "The place was not clear. Ask for the neighbourhood and the city they would work "
        "from, with an example such as 'Madrid, Chamberí'."
    ),
    "driver_license": (
        "Could not tell whether they hold a driving licence. Ask for a clear yes or no "
        "(an expired licence, or one they are still getting, counts as no)."
    ),
    "availability": "Ask whether they want full-time, part-time or weekends (one or more).",
    "preferred_schedule": "Ask which shifts suit them: morning, afternoon, evening or flexible.",
    "experience_years": (
        "Ask how long they have worked as a delivery driver, in years or months "
        "(for example '2 años' or '6 meses'); 'none' is fine."
    ),
    "experience_platforms": (
        "Ask which delivery apps they have worked with (Glovo, Uber Eats, Rappi...). "
        "Delivering on their own, or with no app, is an empty list []."
    ),
    "start_date": (
        "Ask when they could start, as a day or a date "
        "(for example 'mañana', 'el lunes' or '20 de octubre')."
    ),
}


def codes(field: str) -> tuple[str, ...]:
    """The option codes of a list field, in canonical order (the contract's enum order)."""
    return tuple(vocab.LABELS[field]["en"])


def field_options(field: str, language: str) -> tuple[Option, ...]:
    """The fixed options for a field in the Candidate's language (empty if none).

    The platforms end with the "no app" row (``vocab.NO_PLATFORM``), whose tap is recorded
    as the empty list (ADR 0053).
    """
    per_lang = vocab.LABELS.get(field)
    if per_lang is None:
        return ()
    labels = per_lang.get(language, per_lang["es-ES"])
    options = tuple(Option(id=k, label=v) for k, v in labels.items())
    if field == "experience_platforms":
        label = vocab.NO_PLATFORM_LABELS.get(language, vocab.NO_PLATFORM_LABELS["es-ES"])
        options += (Option(id=vocab.NO_PLATFORM, label=label),)
    return options


# ---------------------------------------------------------------------------
# null
# ---------------------------------------------------------------------------


def unresolved(field: str, language: str = "es-ES") -> Invalid:
    """The agent could not map the Candidate's answer: a miss, with the field's hint."""
    hint = UNRESOLVED_HINTS[field]
    if field == "zone":  # Spain says barrio, Mexico colonia (zones.place_word)
        hint = hint.replace("neighbourhood", place_word(language))
    return Invalid(
        reason="unresolved",
        hint=hint,
        options=field_options(field, language),
    )


# ---------------------------------------------------------------------------
# Full name
# ---------------------------------------------------------------------------

_NAME_PARTICLES = {"de", "del", "la", "las", "los", "y", "da", "das", "do", "dos", "van", "von"}
_NAME_JOINERS = {"-", "'", "\u2019"}
_NAME_MIN_WORDS = 2
_NAME_MAX_WORDS = 8
_NAME_MAX_LEN = 80
_NAME_MIN_LETTERS = 4


def verify_full_name(value: str) -> ValidationResult:
    """At least two words of letters (accents, ñ, hyphens and apostrophes allowed).

    The agent removes greetings and "me llamo" (ADR 0042); a name that still carries them
    fails here or is stored as given, never stripped by a word list.
    """
    text = unicodedata.normalize("NFC", value).strip().strip(".,;:!¡?¿ ")
    words = text.split()
    if (
        len(words) < _NAME_MIN_WORDS
        or len(words) > _NAME_MAX_WORDS
        or len(text) > _NAME_MAX_LEN
        or not all(_is_name_word(w) for w in words)
        or sum(ch.isalpha() for ch in text) < _NAME_MIN_LETTERS
    ):
        return Invalid(
            reason="invalid_name",
            hint=(
                "The name needs a first name and a surname, in letters only. "
                "Ask again, with an example such as 'Ana García'."
            ),
        )
    if text == text.lower() or text == text.upper():
        words = [_cap_word(w, first=i == 0) for i, w in enumerate(words)]
    return Accepted(value=" ".join(words))


def _is_name_word(word: str) -> bool:
    if word[0] in _NAME_JOINERS or word[-1] in _NAME_JOINERS:
        return False
    return all(ch.isalpha() or ch in _NAME_JOINERS for ch in word)


def _cap_word(word: str, *, first: bool) -> str:
    lower = word.lower()
    if not first and lower in _NAME_PARTICLES:
        return lower
    return re.sub(r"(^|[-'\u2019])(\w)", lambda m: m[1] + m[2].upper(), lower)


# ---------------------------------------------------------------------------
# Zone
# ---------------------------------------------------------------------------


def verify_zone(
    ids: Sequence[str],
    zones: Sequence[ZoneRef],
    *,
    offered: Collection[str] = (),
    language: str = "es-ES",
    confirm_uncovered: bool = True,
) -> ValidationResult:
    """Zone ids the agent found with ``search_zones`` (Accepted/Knockout value: the ZoneRef).

    ``offered``: the ids in the options we last offered for the Zone (buttons, a list or a
    confirmation). One uncovered id among them is the Candidate's own choice, so it is the
    Knockout; otherwise it is confirmed first. ``confirm_uncovered=False`` (after a
    Knockout, ADR 0033) makes one uncovered id a Knockout result directly.
    """
    by_id = {z.id: z for z in zones}
    unknown = [i for i in ids if i not in by_id]
    if unknown or not ids:
        return Invalid(
            reason="unknown_zone_id",
            hint=(
                f"Unknown Zone id {', '.join(unknown) or '(none)'}. Call search_zones with the "
                "place the Candidate named and pass only ids it returned."
            ),
            counted=False,
        )
    matched = [by_id[i] for i in ids]
    if len(matched) == 1:
        return _one_zone(matched[0], offered=offered, language=language, confirm=confirm_uncovered)
    if len(matched) <= MAX_CHOICES:
        ordered = sorted(matched, key=lambda z: (not z.covered, z.city, z.name, z.id))
        return NeedsChoice(
            options=tuple(
                Option(id=z.id, label=label)
                for z, label in zip(ordered, zone_labels(ordered), strict=True)
            )
        )
    if len({(z.country, z.city) for z in matched}) == 1:
        return city_zones(matched, language)
    return Invalid(
        reason="ambiguous_zone",
        hint=(
            f"Those areas are in several cities. Ask which city and {place_word(language)}, for "
            f"example '{matched[0].name}, {matched[0].city}'."
        ),
    )


def _one_zone(
    zone: ZoneRef, *, offered: Collection[str], language: str, confirm: bool
) -> ValidationResult:
    if zone.covered:
        return Accepted(value=zone)
    if zone.id in offered or not confirm:
        return Knockout(criterion="zone", reason="outside_service_areas", value=zone)
    yes, no = _CONFIRM_LABELS.get(language, _CONFIRM_LABELS_ES)
    return NeedsChoice(
        options=(Option(id=zone.id, label=yes), Option(id=NOT_CONFIRMED, label=no)),
        confirm=zone.id,
    )


# ---------------------------------------------------------------------------
# Driver's licence (Knock-out Criterion)
# ---------------------------------------------------------------------------


def verify_driver_license(code: str) -> ValidationResult:
    """``yes`` accepts; each other code is a Knockout whose reason picks the template."""
    if code == "yes":
        return Accepted(value=True)
    if code in LICENSE_KNOCKOUTS:
        return Knockout(criterion="driver_license", reason=code, value=False)
    return _invalid_option("driver_license", [code])


# ---------------------------------------------------------------------------
# Option lists
# ---------------------------------------------------------------------------


def verify_codes(field: str, values: Sequence[str]) -> ValidationResult:
    """Codes from the field's enum, in canonical order. Only platforms may be empty."""
    allowed = codes(field)
    unknown = [v for v in values if v not in allowed]
    if unknown or (not values and field != "experience_platforms"):
        return _invalid_option(field, unknown)
    return Accepted(value=[c for c in allowed if c in values])


def _invalid_option(field: str, unknown: Sequence[str]) -> Invalid:
    return Invalid(
        reason="invalid_option",
        hint=(
            f"Not a valid {field} value: {', '.join(unknown) or '(empty)'}. "
            f"Use only these codes: {', '.join(_field_codes(field))}."
        ),
        counted=False,
    )


def _field_codes(field: str) -> tuple[str, ...]:
    if field == "driver_license":
        return ("yes", *LICENSE_KNOCKOUTS)
    return codes(field)


# ---------------------------------------------------------------------------
# Experience
# ---------------------------------------------------------------------------


def verify_experience_years(years: float) -> ValidationResult:
    """0 to 60 years; 0 skips the platform question."""
    if not 0 <= years <= MAX_EXPERIENCE_YEARS:
        return Invalid(
            reason="out_of_range",
            hint=(
                f"Years of experience must be between 0 and {MAX_EXPERIENCE_YEARS}. Ask again "
                "how long they have worked as a delivery driver."
            ),
        )
    value = round(float(years), 2)
    flags = (EXPERIENCE_PLATFORMS_SKIPPED,) if value == 0 else ()
    return Accepted(value=value, flags=flags)


# ---------------------------------------------------------------------------
# Start date
# ---------------------------------------------------------------------------

_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def verify_start_date(
    value: str | Sequence[str], *, today: date, language: str = "es-ES"
) -> ValidationResult:
    """A real ISO date, today or later in the Candidate's local calendar.

    Two dates are the two days the Candidate's words fit ("el próximo domingo" said within
    the week, ADR 0054): a past one is dropped, and two that remain are a ``NeedsChoice``
    whose option ids are the dates, labelled in the Candidate's language.
    """
    if not isinstance(value, str):
        return _start_date_choice(value, today=today, language=language)
    try:
        if not _ISO_DATE.fullmatch(value):
            raise ValueError(value)
        start = date.fromisoformat(value)
    except ValueError:
        return Invalid(
            reason="invalid_date",
            hint=(
                f"{value!r} is not a calendar date. Resolve the day against today from "
                "get_application and pass it as YYYY-MM-DD."
            ),
            counted=False,
        )
    if start < today:
        return Invalid(
            reason="past_start_date",
            hint=(
                "That date has already passed. Ask from which day, today or later, they can start."
            ),
        )
    flags = (START_DATE_FAR,) if start > today + timedelta(days=START_DATE_FAR_DAYS) else ()
    return Accepted(value=start.isoformat(), flags=flags)


def _start_date_choice(values: Sequence[str], *, today: date, language: str) -> ValidationResult:
    results = [verify_start_date(v, today=today) for v in values]
    for result in results:
        if isinstance(result, Invalid) and not result.counted:
            return result  # not a calendar date: the agent's error, as for one date
    if today.isoformat() in values:
        # Two dates are the two days one weekday fits, and a weekday is its next occurrence,
        # never today (ADR 0054): a list holding today was counted from today (ADR 0058).
        return Invalid(
            reason="today_in_date_choice",
            hint=(
                "Two dates are the two days a weekday fits, and a weekday is never today: its "
                "next date is 7 days out. Take both dates from the [criba date] line and call "
                "again without asking them."
            ),
            counted=False,
        )
    accepted = sorted(
        {r.value: r for r in results if isinstance(r, Accepted)}.values(), key=lambda r: r.value
    )
    if not accepted:
        return results[0]  # every reading has passed: one past_start_date miss
    if len(accepted) == 1:
        return accepted[0]
    return NeedsChoice(
        options=tuple(Option(id=r.value, label=date_label(r.value, language)) for r in accepted)
    )


def date_label(iso: str, language: str) -> str:
    """A start date as a button label: "Domingo 11 oct", "Sunday 11 Oct"."""
    day = date.fromisoformat(iso)
    lang = "en" if language == "en" else "es"
    weekday = vocab.WEEKDAY_NAMES[lang][day.weekday()]
    return f"{weekday} {day.day} {vocab.MONTH_NAMES[lang][day.month - 1]}"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def verify_value(
    field: str,
    value: Any,
    *,
    zones: Sequence[ZoneRef] = (),
    today: date | None = None,
    language: str = "es-ES",
    offered: Collection[str] = (),
    confirm_uncovered: bool = True,
) -> ValidationResult:
    """Verify one structured value (already shaped by the field's schema), or null."""
    if value is None:
        return unresolved(field, language)
    if field == "full_name":
        return verify_full_name(value)
    if field == "zone":
        return verify_zone(
            value,
            zones,
            offered=offered,
            language=language,
            confirm_uncovered=confirm_uncovered,
        )
    if field == "driver_license":
        return verify_driver_license(value)
    if field == "experience_years":
        return verify_experience_years(value)
    if field == "start_date":
        if today is None:
            raise ValueError("start_date needs today")
        return verify_start_date(value, today=today, language=language)
    if field in ("availability", "preferred_schedule", "experience_platforms"):
        return verify_codes(field, value)
    raise KeyError(field)
