"""Validator result types.

Every validator returns one of four results. `record_answer` (lane W2-A) maps
them onto `contracts/tools.json`:

- `Accepted`    -> `record_answer_output` "accepted" (store `value`, pass `flags`)
- `NeedsChoice` -> "needs_choice" (2-3 `options` as buttons; with `confirm`, the Sí/No
  confirmation of one uncovered Zone before a Knockout, ADR 0042)
- `Knockout`    -> "knockout" (criterion; the tool adds the approved template)
- `Invalid`     -> `{ok: false, error: "invalid_value", hint, attempt, options}`.
  `options` is always filled for fixed-option fields; the tool decides whether
  to send them (Phase 1 section 4.2: from the second miss on). A `narrowing`
  Invalid (a known city without a single Zone, Q45) sends its options at once
  and counts no attempt. An Invalid with `counted=False` (the agent's error, not the
  Candidate's: an unknown Zone id, a date that does not exist) counts none either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# Flags carried by `Accepted.flags` (enum in tools.json record_answer_output.flags).
START_DATE_FAR = "start_date_far"
EXPERIENCE_PLATFORMS_SKIPPED = "experience_platforms_skipped"

KnockoutCriterion = Literal["zone", "driver_license"]

REPLY_BUTTON_MAX = 20  # WhatsApp reply-button title limit
LIST_ROW_MAX = 24  # WhatsApp list-row title limit


@dataclass(frozen=True, slots=True)
class Option:
    """A tap-to-answer choice. `id` is passed back as the answer verbatim."""

    id: str
    label: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label}


@dataclass(frozen=True, slots=True)
class Accepted:
    """A valid answer. `value` is the canonical value to store (see each validator)."""

    value: Any
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NeedsChoice:
    """The answer matched several values; offer `options` (2 to 3) as buttons.

    `confirm` is set when one Zone outside every Service Area was found: the options are
    that Zone's id (yes) and `no`, and only the Candidate's tap on the id is the Knockout.
    """

    options: tuple[Option, ...]
    confirm: str | None = None

    def __post_init__(self) -> None:
        if not 2 <= len(self.options) <= 3:
            raise ValueError("NeedsChoice carries 2 to 3 options")


@dataclass(frozen=True, slots=True)
class Knockout:
    """A Knock-out Criterion failed.

    `value` is what to store for the field (the out-of-area Zone, or False for the
    licence). `reason` is a machine code for the event log and the Review queue.
    """

    criterion: KnockoutCriterion
    reason: str
    value: Any


@dataclass(frozen=True, slots=True)
class Invalid:
    """The answer could not be accepted.

    `reason` is a machine code; `hint` is an English instruction for the Screening
    Agent (never shown verbatim). `options` are the fixed choices to offer as
    buttons or a list, when the field has them.

    `narrowing` marks an answer that is not wrong but not enough: a known city without
    a single Zone (Q45). Its `options` (up to 10) go out at once as a list, and it does
    not count as a miss toward the 3-miss Escalation, like `NeedsChoice`.

    `counted` is False for the agent's errors rather than the Candidate's (ADR 0042):
    they are reported with their `reason` but count no miss.
    """

    reason: str
    hint: str
    options: tuple[Option, ...] = ()
    narrowing: bool = False
    counted: bool = True

    def __post_init__(self) -> None:
        if self.narrowing and not 1 <= len(self.options) <= 10:
            raise ValueError("a narrowing Invalid carries 1 to 10 options")


ValidationResult = Accepted | NeedsChoice | Knockout | Invalid


def truncate_label(label: str, limit: int = REPLY_BUTTON_MAX) -> str:
    """Fit a label into a WhatsApp button or list row, ending with an ellipsis if cut."""
    label = " ".join(label.split())
    if len(label) <= limit:
        return label
    return label[: limit - 1].rstrip() + "…"
