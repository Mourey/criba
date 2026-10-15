"""What the router does with a :class:`~criba.jev.battery.Reading` (ADR 0051).

Pure functions and one frozen bundle of thresholds, so the same code runs in the router, in the
tests and in the calibration (``src/tests/jev_calibration``). The thresholds were fitted on
labelled messages against the real service (``docs/evidence/jev-calibration.md``); change
one only with a new run, and bump :attr:`Thresholds.version`.

Language is decided in code. An explicit request to continue in Spanish or English (the
``asks`` Choice, ADR 0061) wins over the language the message is written in, on the first
message and after it. Otherwise the first typed message of an Application sets it when Jev is
confident. After that it switches only when Jev says the message is in the other language with
confidence **and** says the message continues the chat in it as a full sentence. ``unclear``,
``other``, low confidence, a timeout or an error never switch: the stored language stands.

An unreadable message (ADR 0063: keyboard mashing, random letters, made-up words) is decided by
:func:`is_unreadable`: never a message without a letter (code decides those), otherwise the
``unreadable`` Noul at its threshold. The router counts it as a miss and, at
:data:`UNREADABLE_RUN` in a row, hands the conversation to the Recruiters.

Sentiment acts on the current turn only: a tone hint for the agent, and above a higher threshold
an Escalation. It is never stored as a property of the Candidate and never an input to a
Knockout or Rejection (ADR 0029).
"""

from __future__ import annotations

from dataclasses import dataclass

from criba.jev.battery import Reading

SPANISH_BY_COUNTRY = {"ES": "es-ES", "MX": "es-MX"}


@dataclass(frozen=True)
class Thresholds:
    version: str = "jev-1.13-v3"
    # Language: the Choice's confidence, and the `switch` Noul. `ask_confidence` gates the
    # `asks` Choice (an explicit request to change the language, ADR 0061).
    ask_confidence: float = 0.80
    first_confidence: float = 0.70
    switch_confidence: float = 0.60
    switch_noul: float = 0.85
    # Sentiment: a Noul at or above its threshold fires.
    frustrated: float = 0.80
    confused: float = 0.75
    wants_human_hint: float = 0.70
    wants_human_escalate: float = 0.85
    abusive_escalate: float = 0.85
    # Unreadable (ADR 0063): the `unreadable` Noul at or above `unreadable` is a miss; below
    # `readable` the message is readable and ends a run; in between it is uncertain and does
    # neither.
    unreadable: float = 0.50
    readable: float = 0.20


THRESHOLDS = Thresholds()

# Unreadable messages in a row that hand the conversation to the Recruiters (ADR 0063), the
# same count as the third miss of Phase 1 section 5.2.
UNREADABLE_RUN = 3

# Tone hint codes. The event holds the code; `get_application` renders the text for the agent,
# for this turn only. English because the agent's skill is; the agent replies in the Candidate's
# language (ADR 0035: no Candidate text, and no score, in an event).
TONE_HINT_TEXT = {
    "apologise": (
        "The Candidate seems frustrated. Acknowledge it in one short phrase, apologise for the "
        "trouble, then carry on simply."
    ),
    "simpler": (
        "The Candidate may be confused. Use simpler, shorter wording and offer to explain the "
        "question another way."
    ),
    "offer_person": (
        "The Candidate may want a person. Offer that someone from the team can help, then carry "
        "on with the pending question."
    ),
    "escalated": (
        "The Candidate asked for a person and the team has been alerted. Say that someone from "
        "the team will get in touch, kindly, and carry on with the pending question."
    ),
    "calm": (
        "The Candidate is being abusive and the team has been alerted. Stay calm and polite, "
        "redirect once to the pending question, and do not argue."
    ),
}

# The Escalation reasons these signals raise (the queue's `reason` codes).
ESCALATION_REASON = {"wants_human": "wants_human", "abusive": "abuse"}


@dataclass(frozen=True)
class LanguageDecision:
    target: str | None  # the language to store, or None to leave the stored one alone
    reason: str  # a code for the log and the tests


def spanish_for(country: str | None) -> str:
    """Spanish follows the Application's country (es-ES in Spain, es-MX in Mexico)."""
    return SPANISH_BY_COUNTRY.get(country or "", "es-ES")


def family(language: str | None) -> str:
    """``en`` or ``es``: the stored language without its Spanish variant. Unset is Spanish."""
    return "en" if language == "en" else "es"


def decide_language(
    stored: str | None,
    country: str | None,
    reading: Reading,
    *,
    first: bool,
    thresholds: Thresholds = THRESHOLDS,
) -> LanguageDecision:
    """Whether this message changes the stored language, and to what."""
    if reading.asks in ("es", "en") and reading.asks_confidence >= thresholds.ask_confidence:
        # An explicit request about the language of the chat wins over the written language.
        wanted = "en" if reading.asks == "en" else spanish_for(country)
        if family(wanted) == family(stored):
            # Already in that language: only the Spanish variant could differ (the country's).
            return LanguageDecision(None if stored in (None, wanted) else wanted, "request_same")
        return LanguageDecision(wanted, "request")
    if reading.lang not in ("es", "en"):
        return LanguageDecision(None, reading.lang)
    target = "en" if reading.lang == "en" else spanish_for(country)
    if first:
        if reading.lang_confidence < thresholds.first_confidence:
            return LanguageDecision(None, "low_confidence")
        return LanguageDecision(None if target == stored else target, "first_message")
    if family(target) == family(stored):
        # Same language: only the Spanish variant could differ, and that follows the country.
        return LanguageDecision(None if stored in (None, target) else target, "same_language")
    if reading.lang_confidence < thresholds.switch_confidence:
        return LanguageDecision(None, "low_confidence")
    if reading.switch < thresholds.switch_noul:
        return LanguageDecision(None, "not_a_switch")
    return LanguageDecision(target, "switch")


@dataclass(frozen=True)
class ToneDecision:
    hint: str | None  # a key of TONE_HINT_TEXT
    signals: tuple[str, ...]  # which signals fired, as codes
    escalation: str | None  # the queue reason to escalate with, if any


def decide_tone(reading: Reading, thresholds: Thresholds = THRESHOLDS) -> ToneDecision:
    """The tone hint and Escalation for this message, from the sentiment Nouls."""
    s = reading.signals
    abusive = s.get("abusive", 0.0) >= thresholds.abusive_escalate
    wants = s.get("wants_human", 0.0) >= thresholds.wants_human_hint
    escalate_human = s.get("wants_human", 0.0) >= thresholds.wants_human_escalate
    frustrated = s.get("frustrated", 0.0) >= thresholds.frustrated
    confused = s.get("confused", 0.0) >= thresholds.confused
    fired = tuple(
        name
        for name, on in (
            ("abusive", abusive),
            ("wants_human", wants),
            ("frustrated", frustrated),
            ("confused", confused),
        )
        if on
    )
    escalation = None
    if abusive:
        hint, escalation = "calm", ESCALATION_REASON["abusive"]
    elif escalate_human:
        hint, escalation = "escalated", ESCALATION_REASON["wants_human"]
    elif wants:
        hint = "offer_person"
    elif frustrated:
        hint = "apologise"
    elif confused:
        hint = "simpler"
    else:
        hint = None
    return ToneDecision(hint, fired, escalation)


def unreadable_state(text: str, value: float | None, thresholds: Thresholds = THRESHOLDS) -> str:
    """``unreadable``, ``uncertain`` or ``readable`` for a typed message (ADR 0063), from the
    ``unreadable`` Noul's ``value``. A message with no letter at all (a number, a date, an
    emoji) is readable: code decides that, not Jev. No value (a failed call) is uncertain:
    a failure never counts and never resets."""
    if not any(ch.isalpha() for ch in text):
        return "readable"
    if value is None:
        return "uncertain"
    if value >= thresholds.unreadable:
        return "unreadable"
    if value < thresholds.readable:
        return "readable"
    return "uncertain"


def is_unreadable(text: str, reading: Reading, thresholds: Thresholds = THRESHOLDS) -> bool:
    """Whether a typed message counts as unreadable (a miss)."""
    return unreadable_state(text, reading.unreadable, thresholds) == "unreadable"
