"""Persona schema (spec 004 task 1, ADR 0017).

A persona is data: who the simulated Candidate is, what they reply to each question, how
they misbehave, and the end state the case record must reach. The rules-based persona engine
(lane W2-E) answers each agent message from `answers`, `opening`, `interrupts` and `confirm`;
nothing here asserts on the agent's prose. Field names, stage names, Outcomes and event kinds
come from `contracts/tools.json` and `contracts/schema.sql`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

FieldName = Literal[
    "full_name",
    "zone",
    "driver_license",
    "availability",
    "preferred_schedule",
    "experience_years",
    "experience_platforms",
    "start_date",
]
# Order in which the Screening Agent collects fields (contracts/tools.json `field`).
FIELD_ORDER: tuple[str, ...] = (
    "full_name",
    "zone",
    "driver_license",
    "availability",
    "preferred_schedule",
    "experience_years",
    "experience_platforms",
    "start_date",
)

# The option codes `record_answer` takes per field (contracts/tools.json `answer_value_*`);
# test_personas.py compares them with the contract.
FIELD_CODES: dict[str, tuple[str, ...]] = {
    "driver_license": ("yes", "no", "expired", "suspended", "pending"),
    "availability": ("full_time", "part_time", "weekends"),
    "preferred_schedule": ("morning", "afternoon", "evening", "flexible"),
    "experience_platforms": (
        "glovo",
        "uber_eats",
        "just_eat",
        "rappi",
        "didi_food",
        "other",
    ),
}
# Fields whose structured value is a list (of option codes, or of Zone ids for `zone`).
LIST_FIELDS: frozenset[str] = frozenset(
    {"availability", "preferred_schedule", "experience_platforms", "zone"}
)
WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# A start date a persona may state relative to the sim's today: +2d, -1d, next:mon, and
# next:sun+7d (the same weekday a week later; ADR 0054's second reading).
_RELATIVE_DATE = re.compile(
    r"^(?:[+-][0-9]{1,3}d|next:(?:" + "|".join(WEEKDAYS) + r")(?:\+[0-9]{1,2}d)?)$"
)
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DATE_SHAPES = (
    "start_date must be YYYY-MM-DD, +Nd, -Nd, next:<weekday>[+Nd], or a list of two of them"
)


def value_problem(field: str, value: Any) -> str | None:
    """Why `value` is not a canonical value of `field` (contracts/tools.json), or None.

    `null` is always fine (the agent could not map the answer). A start date may also be
    relative to the sim's today (`+2d`, `-1d`, `next:mon`); the stub resolves it.
    """
    if value is None:
        return None
    if field == "zone":
        return "a zone is searched (`query`), it has no `value`"
    if field == "full_name":
        ok = isinstance(value, str) and 3 <= len(value) <= 80
        return None if ok else "full_name must be a string of 3 to 80 characters"
    if field == "driver_license":
        ok = value in FIELD_CODES[field]
        return None if ok else f"driver_license must be one of {FIELD_CODES[field]}"
    if field in LIST_FIELDS:
        codes = FIELD_CODES[field]
        bad = (
            not isinstance(value, list)
            or len(set(map(str, value))) != len(value)
            or any(v not in codes for v in value)
            or (field != "experience_platforms" and not value)
        )
        return f"{field} must be a list of unique codes from {codes}" if bad else None
    if field == "experience_years":
        number = isinstance(value, int | float) and not isinstance(value, bool)
        ok = number and value >= 0
        return None if ok else "experience_years must be a number of 0 or more"
    if field == "start_date":
        if isinstance(value, list):
            # The two days the words fit ("el próximo domingo", ADR 0054): two unique dates.
            if len(value) != 2 or len(set(map(str, value))) != 2:
                return "two start dates must be a list of two different dates"
            return next((p for v in value if (p := value_problem(field, v))), None)
        if not isinstance(value, str):
            return _DATE_SHAPES
        if _RELATIVE_DATE.match(value):
            return None
        try:
            if _ISO_DATE.match(value):
                date.fromisoformat(value)
                return None
        except ValueError:
            pass
        return _DATE_SHAPES
    return f"unknown field {field!r}"


Stage = Literal[
    "greeting",
    "name",
    "zone",
    "driver_license",
    "availability",
    "preferred_schedule",
    "experience",
    "start_date",
    "confirm",
    "qualified",
    "approved",
    "interview_booked",
]
Outcome = Literal["knockout", "rejection", "abandoned", "withdrawn"]
Locale = Literal["es-ES", "es-MX", "en"]
Country = Literal["ES", "MX"]
Lang = Literal["es", "en"]

# Every `events.kind` in contracts/schema.sql, in its order. The persona package also runs in the
# stub-llm image, which carries `tools.json` but not `schema.sql`, so the list is written out
# here and `test_personas.py` compares it with the schema (a persona can only name a kind the
# database accepts, and an `events.kind` added to the schema fails that test until it is here).
EventKind = Literal[
    # lifecycle
    "application_started",
    "stage_changed",
    "reopened",
    "answer_recorded",
    "answer_invalid",
    "answer_ambiguous",
    "screening_finished",
    "summary_written",
    # Outcomes
    "knockout",
    "rejection",
    "abandoned",
    "withdrawn",
    # Review, Escalation, Approval
    "review_requested",
    "review_upheld",
    "review_overturned",
    "escalation_raised",
    "escalation_dismissed",
    "approval_requested",
    "approved",
    # Claim
    "claim_taken",
    "claim_released",
    "claim_taken_over",
    # Takeover
    "takeover_started",
    "takeover_handed_back",
    # Stalled and follow-ups
    "stalled",
    "nudge_sent",
    "flag_qualified_waiting",
    "flag_claimed_idle",
    # Interview
    "slots_offered",
    "booking_link_sent",
    "interview_booked",
    "interview_rescheduled",
    "interview_slot_released",
    # Candidate-level
    "outreach_sent",
    "import_unresponsive",
    "opted_out",
    "opt_out_lifted",
    "erasure_requested",
    # Runtime
    "tone_hint",
    "run_error",
    "template_sent",
    "system_turn",
]
# Tool names the agent should call because of the persona's behaviour (contracts/tools.json).
ToolName = Literal[
    "get_application",
    "search_zones",
    "record_answer",
    "request_review",
    "raise_escalation",
    "finish_screening",
    "record_backout",
    "offer_interview_slots",
    "book_interview",
]

Quirk = Literal[
    "multi_answer",
    "out_of_order",
    "invalid_answers",
    "corrections",
    "ambiguous_zone",
    "silence",
    "code_switching",
    "foreign_word",
    "abuse",
    "injection",
    "voice_note",
    "withdraws",
    "opts_out",
    "disputes_knockout",
    "reopens",
    "unreadable",
]

_DURATION_PATTERN = r"^[0-9]+(s|m|h|d)$"
_PHONE_PREFIX = {"ES": "+34", "MX": "+52"}
_LOCALE_COUNTRY = {"es-ES": "ES", "es-MX": "MX"}

Duration = Annotated[str, StringConstraints(pattern=_DURATION_PATTERN)]


def duration_seconds(value: str) -> int:
    """Parse '90s', '15m', '20h' or '7d' into seconds."""
    if not re.match(_DURATION_PATTERN, value):
        raise ValueError(f"bad duration {value!r}; use <n>s, <n>m, <n>h or <n>d")
    return int(value[:-1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[value[-1]]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reply(_Strict):
    """One Candidate message."""

    text: str = Field(min_length=1, max_length=4000)
    lang: Lang | None = Field(
        default=None,
        description="Language of this message. Defaults to the persona locale's language. "
        "The agent must answer in the language of the latest message (Phase 1 section 4.3).",
    )
    pick_option: int | str | None = Field(
        default=None,
        description="When the agent offers options (ambiguous Zone, Availability): the 0-based "
        "index to tap, or a label substring. `text` is sent when no options are shown.",
    )
    media: Literal["voice_note", "image"] | None = Field(
        default=None,
        description="Stand-in for a non-text message; `text` is its caption.",
    )
    covers: list[FieldName] = Field(
        default_factory=list,
        description="Fields this one message answers at once; the agent skips ahead past them.",
    )
    value: Any = Field(
        default=None,
        description="The canonical value a competent agent records for what this message "
        "answers (contracts/tools.json `answer_value_*`); `null` means it maps to nothing. "
        "The stub model sends it instead of the text. Needed on every typed answer. With "
        "`covers`, a mapping from each covered field (but `zone`) to its value. A start date "
        "may be relative to the sim's today: `+2d`, `-1d`, `next:mon`.",
    )
    query: str | None = Field(
        default=None,
        description="Zone only: what the agent searches with `search_zones`. Defaults to "
        "`text`; `null` means there is nothing to search yet (the stub records `null` and "
        "does not model the free follow-up). Needed when `covers` holds `zone`.",
    )
    field: FieldName | None = Field(
        default=None,
        description="The field a reply outside `answers` (an interrupt or a confirm "
        "correction) answers, when it carries a `value` or `query`.",
    )


@dataclass(frozen=True)
class Interpretation:
    """What a competent agent does with one scripted Candidate message, for one field.

    `query` is for the Zone (what `search_zones` gets, None for nothing to search); `value`
    is for every other field. `lang` is the message's language (`es` or `en`).
    """

    field: str
    text: str
    lang: str
    value: Any = None
    query: str | None = None


class Identity(_Strict):
    name: str = Field(description="Display name used in the sim.")
    phone: str = Field(pattern=r"^\+(34|52)[0-9]{8,11}$", description="Simulated, unique.")
    country: Country
    locale: Locale = Field(description="The Candidate's variant of the language at the start.")
    entry: Literal["candidate_writes_first", "outreach_reply"] = "candidate_writes_first"


class Timing(_Strict):
    start_local_time: str = Field(
        default="14:00",
        pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$",
        description="Local wall-clock time (Candidate's time zone) at which the sim clock "
        "starts. Chosen so that nudges at +1h, +20h and +48h fall outside quiet hours "
        "(22:00 to 09:00).",
    )
    reply_delay_s: tuple[float, float] = Field(
        default=(2.0, 8.0),
        description="Simulated typing time per reply: min and max seconds.",
    )


class Interrupt(_Strict):
    """Something the persona does besides answering the pending question."""

    at: FieldName | Literal["start", "confirm", "after_knockout", "after_backout_prompt"] = Field(
        description="Fires when the agent is about to ask this field, or at the named moment "
        "(start: before the opening; confirm: at the summary; "
        "after_knockout / after_backout_prompt: once the Knockout, or the question confirming "
        "a withdrawal or opt-out, has been sent), before the persona's normal answer."
    )
    advance: list[Duration] = Field(
        default_factory=list,
        description="Advance the sim clock by each duration in turn, letting the scheduler run "
        "between steps (for example ['1h', '19h', '28h', '5d']).",
    )
    say: Reply | None = None
    then: Literal["continue", "stop"] = Field(
        default="continue",
        description="stop: the persona sends nothing more (silence, or the conversation is over).",
    )


class AnswerCheck(_Strict):
    """Expected canonical value for a field (contracts/tools.json `answers`).

    Set `value` (compared for equality) or `non_null` (just present), not both.
    """

    value: str | bool | int | float | list[str] | dict[str, str] | None = None
    non_null: bool = False

    @model_validator(mode="after")
    def _one_kind(self) -> AnswerCheck:
        if self.non_null and self.value is not None:
            raise ValueError("set either value or non_null, not both")
        if not self.non_null and self.value is None:
            raise ValueError("set value or non_null")
        return self


class EventCount(_Strict):
    kind: EventKind
    count: int = Field(ge=0)


class Expect(_Strict):
    """The end state the case record must reach. Assertions never compare prose."""

    stage: Stage
    status: Literal["open", "closed"]
    outcome: Outcome | None = None
    knockout_criterion: Literal["driver_license", "zone"] | None = None
    final_language: Locale | None = Field(
        default=None, description="Application language at the end, if it matters."
    )
    final_country: Country | None = Field(
        default=None,
        description="Application country at the end, if it matters: a Zone in another country "
        "moves the Application there (ADR 0057), and every covering Location is in it.",
    )
    fields: dict[FieldName, AnswerCheck] = Field(default_factory=dict)
    fields_unanswered: list[FieldName] = Field(
        default_factory=list, description="Fields that must still be empty."
    )
    field_order: list[FieldName] = Field(
        default_factory=list,
        description="Fields in the order they were first recorded (the required collection "
        "order, Phase 1 section 1). Knockout checks run before the details.",
    )
    events: list[EventKind] = Field(
        default_factory=list,
        description="Event kinds that must appear, in this relative order; others may interleave.",
    )
    event_counts: list[EventCount] = Field(default_factory=list)
    forbidden_events: list[EventKind] = Field(default_factory=list)
    tools: list[ToolName] = Field(
        default_factory=list,
        description="Tools the run must have called at least once.",
    )
    forbidden_tools: list[ToolName] = Field(default_factory=list)
    ai_disclosure: bool = Field(
        default=True,
        description="The first agent message discloses it is an AI (EU AI Act).",
    )
    knockout_template: Locale | None = Field(
        default=None, description="Language of the Knockout template sent, when one is."
    )
    reply_languages_follow_latest: bool = Field(
        default=False,
        description="Each agent reply is in the language of the Candidate's latest message.",
    )
    reply_language: Lang | None = Field(
        default=None,
        description="Every agent reply is in this language, whatever the Candidate's latest "
        "message was: a one-word answer in another language is not a switch (Phase 1 section 4.3).",
    )
    candidate_opted_out: bool = False
    nudges: int | None = Field(default=None, ge=0, le=3, description="Exact nudges sent.")
    max_reply_latency_s: float = Field(
        default=10.0,
        description="Phase 1 section 6: later replies within 10 s (first within 5).",
    )
    forbidden_agent_text: list[str] = Field(
        default_factory=list,
        description="Case-insensitive substrings no agent message may contain: never name "
        "Criba, never leak tool names or instructions.",
    )


class Persona(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str
    quirks: list[Quirk] = Field(default_factory=list)
    policy: Literal["rules", "model"] = Field(
        default="rules",
        description="rules: scripted replies (CI default). model: a small model plays the "
        "persona from `style`, used only in the model comparison run (spec 004).",
    )
    style: str | None = Field(default=None, description="Brief for the model policy.")
    identity: Identity
    timing: Timing = Field(default_factory=Timing)
    opening: Reply
    answers: dict[FieldName, list[Reply]] = Field(
        default_factory=dict,
        description="Replies per field. The n-th time the agent asks the field the persona "
        "sends the n-th reply; when they run out it repeats the last one.",
    )
    zone_choice: int | str | None = Field(
        default=None,
        description="Which Zone to tap when the agent offers Zone options (Phase 1 section 3: "
        "a place name that matches 2 or 3 Zones): a 0-based index or a label substring. "
        "Without it the driver taps the first option whose label contains the words of the "
        "persona's zone answers (its intended neighbourhood or city), else the first option. "
        "A `pick_option` on the scripted reply wins over this.",
    )
    interrupts: list[Interrupt] = Field(default_factory=list)
    confirm: list[Reply] = Field(
        default_factory=list,
        description="Replies to the confirmation summary, in order; empty means a plain yes. "
        "Used for corrections, which update the field and re-run its check.",
    )
    expect: Expect
    seed_assumptions: list[str] = Field(
        default_factory=list,
        description="Facts about the seeded Locations and Service Areas the persona relies on.",
    )

    def replies(self) -> Iterator[tuple[str | None, Reply]]:
        """Every scripted Candidate message with the field it answers (None: no field known)."""
        yield None, self.opening
        for field, replies in self.answers.items():
            for reply in replies:
                yield field, reply
        for interrupt in self.interrupts:
            if interrupt.say is not None:
                yield None, interrupt.say
        for reply in self.confirm:
            yield None, reply

    def reply_lang(self, reply: Reply) -> str:
        return reply.lang or ("en" if self.identity.locale == "en" else "es")

    def interpretations(self) -> list[Interpretation]:
        """One entry per (field, message) the stub model interprets; raises ValueError when a
        reply's `value`, `query`, `field` or `covers` do not fit together (spec 006b)."""
        found: list[Interpretation] = []
        for answering, reply in self.replies():
            where = f"{self.id}: reply {reply.text!r}"
            given = reply.model_fields_set
            if answering is not None and reply.field is not None:
                raise ValueError(f"{where}: `field` is only for replies outside `answers`")
            field = answering or reply.field
            if reply.covers:
                targets = list(reply.covers)
            elif field is not None:
                targets = [field]
            else:
                targets = []
            if not targets:
                if reply.value is not None or reply.query is not None:
                    raise ValueError(f"{where}: `value` or `query` needs `field` or `covers`")
                continue
            if reply.covers:
                values = reply.value if isinstance(reply.value, dict) else None
                wanted = {f for f in reply.covers if f != "zone"}
                if wanted and (values is None or set(values) != wanted):
                    raise ValueError(
                        f"{where}: with `covers`, `value` maps exactly {sorted(wanted)}"
                    )
                if "zone" in reply.covers and "query" not in given:
                    raise ValueError(f"{where}: `covers` holds zone, so it needs a `query`")
                if "zone" not in reply.covers and reply.query is not None:
                    raise ValueError(f"{where}: `query` is for the zone only")
            elif "zone" in targets:
                if reply.value is not None:
                    raise ValueError(f"{where}: a zone answer takes `query`, not `value`")
            else:
                if reply.query is not None:
                    raise ValueError(f"{where}: `query` is for the zone only")
                if "value" not in given and reply.pick_option is None:
                    raise ValueError(
                        f"{where}: a typed {targets[0]} answer needs a canonical `value`"
                    )
            lang = self.reply_lang(reply)
            for target in targets:
                if target == "zone":
                    query = reply.query if "query" in given else reply.text
                    found.append(Interpretation(target, reply.text, lang, query=query))
                    continue
                if reply.covers:
                    value = (reply.value or {})[target]
                elif "value" in given:
                    value = reply.value
                else:
                    continue  # a tap: the option id travels as it is
                problem = value_problem(target, value)
                if problem:
                    raise ValueError(f"{where}: {problem}")
                found.append(Interpretation(target, reply.text, lang, value=value))
        return found

    @model_validator(mode="after")
    def _consistent(self) -> Persona:
        self.interpretations()
        ident = self.identity
        if not ident.phone.startswith(_PHONE_PREFIX[ident.country]):
            raise ValueError(f"{self.id}: phone {ident.phone} does not match {ident.country}")
        expected_country = _LOCALE_COUNTRY.get(ident.locale)
        if expected_country and expected_country != ident.country:
            raise ValueError(f"{self.id}: locale {ident.locale} does not match {ident.country}")
        if isinstance(self.zone_choice, int) and self.zone_choice < 0:
            raise ValueError(f"{self.id}: zone_choice index must be 0 or more")
        if isinstance(self.zone_choice, str) and not self.zone_choice.strip():
            raise ValueError(f"{self.id}: zone_choice label must not be empty")
        if self.policy == "model" and not self.style:
            raise ValueError(f"{self.id}: policy 'model' needs a style")
        exp = self.expect
        if exp.status == "open" and exp.outcome is not None:
            raise ValueError(f"{self.id}: an open Application has no Outcome")
        if exp.status == "closed" and exp.outcome is None:
            raise ValueError(f"{self.id}: a closed Application needs an Outcome")
        if (exp.outcome == "knockout") != (exp.knockout_criterion is not None):
            raise ValueError(f"{self.id}: knockout_criterion is set exactly for knockouts")
        ranks = [FIELD_ORDER.index(f) for f in exp.field_order]
        if ranks != sorted(ranks):
            raise ValueError(f"{self.id}: expect.field_order must follow {FIELD_ORDER}")
        both = set(exp.fields) & set(exp.fields_unanswered)
        if both:
            raise ValueError(f"{self.id}: {sorted(both)} both answered and unanswered")
        if exp.stage in ("qualified", "approved", "interview_booked"):
            answerable = set(self.answers) | set(self.opening.covers)
            missing = [f for f in FIELD_ORDER if f not in answerable]
            years = exp.fields.get("experience_years")
            if years is not None and years.value == 0:
                # Platforms are skipped when years is 0 (tools.json experience_platforms_skipped).
                missing = [f for f in missing if f != "experience_platforms"]
            if missing:
                raise ValueError(f"{self.id}: no replies for {missing}")
        return self
