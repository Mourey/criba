"""Repository constants, the error types, and the small helpers every module shares."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Literal

from criba.db.connection import utc_now

SCREENING_STAGES: tuple[str, ...] = (
    "greeting",
    "name",
    "zone",
    "driver_license",
    "availability",
    "preferred_schedule",
    "experience",
    "start_date",
    "confirm",
)
STAGES: tuple[str, ...] = (*SCREENING_STAGES, "qualified", "approved", "interview_booked")
OUTCOMES: tuple[str, ...] = ("knockout", "rejection", "abandoned", "withdrawn")
REOPENABLE_OUTCOMES = frozenset({"knockout", "abandoned"})
REOPEN_WINDOW = timedelta(days=30)
CLAIM_IDLE_AFTER = timedelta(days=7)

# Answer fields: name -> (column, kind). ``zone`` is stored as ``zone_id``.
ANSWER_FIELDS: dict[str, str] = {
    "full_name": "text",
    "zone": "zone",
    "driver_license": "bool",
    "availability": "set",
    "preferred_schedule": "set",
    "experience_years": "number",
    "experience_platforms": "set",
    "start_date": "date",
}
_ANSWER_COLUMN = {"zone": "zone_id"}
# The Candidate's own words are kept for these fields (schema columns *_input).
_RAW_INPUT_COLUMN = {"zone": "zone_input", "driver_license": "driver_license_input"}
# The stage at which a failed Knock-out Criterion is asked again on Reopen.
_CRITERION_STAGE = {"zone": "zone", "driver_license": "driver_license"}

_JSON_COLUMNS = ("availability", "preferred_schedule", "experience_platforms")
_BOOL_COLUMNS = ("driver_license", "start_date_far", "qualified_waiting", "claimed_idle")

Actor = Literal["agent", "system", "candidate", "user"]


class RepoError(Exception):
    """Base error. ``code`` is a stable snake_case string for API and tool errors."""

    code = "repo_error"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class NotFound(RepoError):
    code = "not_found"


class Conflict(RepoError):
    code = "conflict"


class Forbidden(RepoError):
    code = "forbidden"


def _now(now: datetime | None) -> datetime:
    return now if now is not None else utc_now()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
