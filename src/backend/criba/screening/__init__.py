"""Screening rules in code: the clock, Zones and the answer verifiers (ADR 0042).

``record_answer`` passes each structured value to ``verify.verify_value`` and maps the
result onto ``contracts/tools.json``; see ``results.py`` for the mapping. Nothing here
parses the Candidate's words, except ``zones.rank_zones`` behind ``search_zones``.
"""

from criba.screening.clock import Clock, FakeClock, SystemClock, local_today
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
from criba.screening.verify import field_options, verify_value
from criba.screening.zones import (
    ZoneRef,
    candidate_timezone,
    country_from_phone,
    rank_zones,
)

__all__ = [
    "EXPERIENCE_PLATFORMS_SKIPPED",
    "START_DATE_FAR",
    "Accepted",
    "Clock",
    "FakeClock",
    "Invalid",
    "Knockout",
    "NeedsChoice",
    "Option",
    "SystemClock",
    "ValidationResult",
    "ZoneRef",
    "candidate_timezone",
    "country_from_phone",
    "field_options",
    "local_today",
    "rank_zones",
    "verify_value",
]
