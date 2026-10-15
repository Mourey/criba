"""Plain words for the Application Summary (ADR 0056).

The Summary is prose for a Recruiter. Nothing in it is a system code: no stage key
(`qualified`), no field key (`full_name`), no flag key (`review_requested`), no id. Two
pieces live here:

- `render_facts` turns the structured part into the fact sheet the summary model reads, in
  words, so the model is never shown a labelled raw code to copy. The structured part
  itself is unchanged: it is the contract shape and the Recruiter API still serves it.
- `leaked_codes` finds our own internal codes in a text. It checks the output against our
  own closed lists of codes, never against what the Candidate wrote (ADR 0042).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# Active flag -> (English, Spanish). `FLAG_ORDER` in `criba.summary` lists the codes.
FLAG_LABELS: dict[str, tuple[str, str]] = {
    "review_requested": ("a review was requested", "se pidió una revisión"),
    "escalation": ("an Escalation is open", "hay una Escalación abierta"),
    "stalled": ("the Candidate stopped replying", "el candidato dejó de responder"),
    "takeover": ("a person has taken over the chat", "una persona tomó el chat"),
    "qualified_waiting": (
        "qualified and waiting for approval for a while",
        "calificada y a la espera de aprobación desde hace tiempo",
    ),
    "claimed_idle": (
        "claimed by a Recruiter but without activity",
        "asignada a un reclutador pero sin actividad",
    ),
    "start_date_far": (
        "the start date is more than 60 days away",
        "la fecha de inicio está a más de 60 días",
    ),
    "opted_out": (
        "the Candidate opted out of messages",
        "el candidato pidió no recibir más mensajes",
    ),
}

_SCREENING_STAGE_WORDS = {
    "greeting": "at the greeting",
    "name": "while asking for the name",
    "zone": "while asking for the zone",
    "driver_license": "while asking about the driving licence",
    "availability": "while asking about availability",
    "preferred_schedule": "while asking for the preferred schedule",
    "experience": "while asking about experience",
    "start_date": "while asking for the start date",
    "confirm": "while confirming the answers",
}
_OPEN_STATE = {
    "qualified": "passed Screening and is waiting for a Recruiter's approval",
    "approved": "was approved by a Recruiter; the Candidate can book an Interview",
    "interview_booked": "was approved and an Interview is booked",
}
_OUTCOME_WORDS = {
    "knockout": "ended in a Knockout",
    "rejection": "was rejected by a Recruiter",
    "abandoned": "was abandoned after the Candidate stopped replying",
    "withdrawn": "was withdrawn by the Candidate",
}
_CRITERION_WORDS = {"driver_license": "the driving licence", "zone": "the zone"}
_ANSWER_WORDS = {
    "full_time": "full time",
    "part_time": "part time",
    "weekends": "weekends",
    "morning": "mornings",
    "afternoon": "afternoons",
    "evening": "evenings",
    "flexible": "flexible",
    "glovo": "Glovo",
    "uber_eats": "Uber Eats",
    "just_eat": "Just Eat",
    "rappi": "Rappi",
    "didi_food": "DiDi Food",
    "other": "other",
}

# Every internal code of ours, from the contract enums. A code with an underscore cannot be a
# word of prose; a single-word code (`qualified`, `zone`) can, so it counts only when it is
# written as a labelled value (`Stage reached: qualified`, `` `qualified` ``).
_STAGE_CODES = (
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
)
_OTHER_CODES = (
    "knockout",
    "rejection",
    "abandoned",
    "withdrawn",
    "full_name",
    "experience_years",
    "experience_platforms",
    *FLAG_LABELS,
    *_ANSWER_WORDS,
    "no_show",
    "failed_call",
    "role_filled",
)
_ALL_CODES = tuple(dict.fromkeys((*_STAGE_CODES, *_OTHER_CODES)))
_COMPOUND = re.compile(
    r"\b(" + "|".join(sorted((c for c in _ALL_CODES if "_" in c), key=len, reverse=True)) + r")\b"
)
_LABELLED = re.compile(
    r"(?i:\b(?:stage|etapa|status|estado|outcome|resultado|criterion|criterio|field|campo|"
    r"flags?|avisos?)\b)[^.:=\n]{0,25}[:=]\s*[`'\"]?("
    + "|".join(sorted((c for c in _ALL_CODES if "_" not in c), key=len, reverse=True))
    + r")\b"
)
_BACKTICKED = re.compile(r"`(" + "|".join(sorted(_ALL_CODES, key=len, reverse=True)) + r")`")
_ID_LIKE = re.compile(r"\b(?:app|cand|bk|qi|slot|zone|loc|user)_[A-Za-z0-9_]+")


def leaked_codes(text: str, *, ids: Iterable[str] = ()) -> list[str]:
    """Our own internal codes (stage, field, flag, outcome keys and ids) found in `text`,
    in the order they appear, without repeats. Empty when the text is clean."""
    found: list[tuple[int, str]] = []
    for pattern in (_COMPOUND, _LABELLED, _BACKTICKED, _ID_LIKE):
        for match in pattern.finditer(text):
            found.append((match.start(), match.group(1) if pattern.groups else match.group(0)))
    for known in ids:
        if known and (at := text.find(known)) >= 0:
            found.append((at, known))
    return list(dict.fromkeys(code for _, code in sorted(found)))


def flag_text(flag: str, language: str) -> str:
    """A flag in words, in the language of the summary (`es...` is Spanish, else English)."""
    english, spanish = FLAG_LABELS.get(flag, (flag.replace("_", " "),) * 2)
    return spanish if language.startswith("es") else english


def _words(values: Sequence[str] | None) -> str:
    return ", ".join(_ANSWER_WORDS.get(v, v) for v in values or [])


def _state_line(structured: Mapping[str, Any]) -> str:
    stage = structured.get("stage")
    outcome = structured.get("outcome")
    if outcome:
        line = f"The application {_OUTCOME_WORDS.get(outcome, outcome)}"
        criterion = structured.get("knockout_criterion")
        if outcome == "knockout" and criterion:
            line += f" on {_CRITERION_WORDS.get(criterion, 'a requirement')}"
        where = _SCREENING_STAGE_WORDS.get(str(stage))
        return line + (f", {where}." if where and outcome != "knockout" else ".")
    if stage in _OPEN_STATE:
        return f"The application {_OPEN_STATE[stage]}."
    where = _SCREENING_STAGE_WORDS.get(str(stage))
    return "Screening is still in progress" + (f", {where}." if where else ".")


def _answer_lines(answers: Mapping[str, Any]) -> list[str]:
    def put(label: str, value: Any) -> str:
        return f"- {label}: {'not given' if value in (None, '', []) else value}"

    zone = answers.get("zone")
    zone_name = zone.get("name") if isinstance(zone, Mapping) else None
    licence = answers.get("driver_license")
    years = answers.get("experience_years")
    platforms = _words(answers.get("experience_platforms"))
    experience = None
    if years is not None:
        experience = f"{years} years" + (f" ({platforms})" if platforms else "")
    elif platforms:
        experience = platforms
    return [
        put("Name", answers.get("full_name")),
        put("Zone", zone_name),
        put("Driving licence", None if licence is None else ("yes" if licence else "no")),
        put("Availability", _words(answers.get("availability"))),
        put("Preferred schedule", _words(answers.get("preferred_schedule"))),
        put("Experience", experience),
        put("Start date", answers.get("start_date")),
    ]


def render_facts(structured: Mapping[str, Any]) -> str:
    """The structured summary as a short fact sheet in plain words, for the summary model."""
    lines = [_state_line(structured)]
    answers = structured.get("answers")
    if isinstance(answers, Mapping):
        lines += ["Answers the Candidate gave:", *_answer_lines(answers)]
    seconds = (structured.get("durations_seconds") or {}).get("total")
    if seconds is not None:
        lines.append(f"Time taken: {seconds} seconds in total.")
    if "nudges" in structured:
        lines.append(f"Reminders sent: {structured['nudges']}.")
    if "escalations" in structured:
        lines.append(f"Escalations raised: {structured['escalations']}.")
    if structured.get("reopened_count"):
        lines.append(f"Reopened {structured['reopened_count']} time(s): the Candidate came back.")
    flags = [flag_text(f, "en") for f in structured.get("flags") or []]
    lines.append("Notices for the Recruiter: " + ("; ".join(flags) if flags else "none") + ".")
    return "\n".join(lines)
