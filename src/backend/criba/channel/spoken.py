"""Deterministic checks on a spoken utterance (spec voice-agent spoken-checks R13-R15).

A voice turn's transcript is checked in code before ``accept_inbound``, through
``spoken-turn``'s :class:`~criba.channel.service.SpokenChecks` hook:

- :func:`is_optout_phrase`: one of R15's five opt-out phrases. The turn reaches the agent with
  ``[optout_request]``; nothing is opted out on a phrase (the agent asks for confirmation).
- :func:`yes_no_conflict`: a yes/no choice is pending and the transcript holds both a yes and
  a no. The turn is re-asked with fixed copy; nothing is recorded and no run starts.
- :func:`match_option`: the transcript names exactly one pending option. The id travels as
  ``reply_option_id``, so the turn takes the tap path (``_record_tap``).
- :func:`check_spoken`: the one entry point, in that order (R13.5).

The four functions are pure (no database, no clock, no network), never raise and never log
the transcript: an internal error is logged by exception type only and gives the safe
answer (no match, no conflict, not a phrase, ``plain``). The matcher never guesses: two
fitting options, a hedge, or a slot without a day and an hour is no match, and the turn goes
to the agent as normal text.

:class:`SpokenChecker` implements the hook. ``before_accept`` reads the pending choice and
runs :func:`check_spoken`; ``after_reply`` reads the turn with Jev after the reply is stored,
without a language decision (R9).

The tables below come from spike 4's scorer lists and
the spec's Matching table. Every entry is in normalised form (:func:`_normalise`).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from criba.channel import copy, optout
from criba.channel.service import SpokenDecision
from criba.db import repo
from criba.jev.turn import JevStep
from criba.tools.booking import OTHER_OPTION_ID
from criba.tools.choices import Interactive

log = logging.getLogger(__name__)

# R15.1: the voice opt-out phrases, nothing else (spec concern 7).
OPTOUT_PHRASES: tuple[str, ...] = (
    "dadme de baja",
    "no me llameis mas",
    "no me llamen mas",
    "ya no me llamen",
    "borrenme de su lista",
)

# R13.2: spoken synonyms of a non-slot option, keyed by option id (the label always counts
# too). Spike 4's scorer lists for availability, schedule and platforms.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "full_time": ("jornada completa", "tiempo completo", "full time"),
    "part_time": ("media jornada", "medio tiempo", "part time", "tiempo parcial"),
    "weekends": ("fines de semana", "fin de semana", "weekends", "weekend"),
    "morning": ("manana", "mananas", "morning"),
    "afternoon": ("tarde", "tardes", "afternoon"),
    "evening": ("noche", "noches", "evening"),
    "flexible": ("flexible", "me da igual"),
    "glovo": ("glovo",),
    "uber_eats": ("uber eats", "ubereats", "uber eat"),
    "rappi": ("rappi", "rapi"),
    "didi_food": ("didi", "didi food"),
    "just_eat": ("just eat", "justeat"),
    "none": ("ninguna app", "ninguna aplicacion", "ninguna"),
}

# R14.1: the polarity cues of a yes/no choice (spike 4's YES and NO lists). "si" and "como no"
# count only as the transcript writes them with their accent, "sí" and "cómo no" (affirmative
# only, the two words with nothing but a space between): unaccented, "si" is "if" and "como
# no" is "since ... not" (:func:`_clauses`). "no" counts only as a whole word, so "nomas" and
# "noche" do not. Spike 4's "nunca" is left out: "nunca me han quitado el carnet" is not a no.
# A yes/no answer is matched only when its first word, after FILLERS, is one of these cues
# (:func:`_answer_polarity`): "Tengo carnet, no te preocupes" answers nothing in code.
AFFIRMATIVE_CUES: tuple[str, ...] = (
    "si",
    "claro",
    "por supuesto",
    "vale",
    "simon",
    "aja",
    "como_no",  # "cómo no" with its accent, as _clauses writes it
    "orale",
    "andale",
    "okay",
    "okey",  # how realtime writes "okay" (spike 4, mx06)
    "ok",
    "yes",
    "correcto",
    "eso es",
    "exacto",
    "perfecto",
)
NEGATIVE_CUES: tuple[str, ...] = ("no", "que va", "para nada", "nel", "nop")

# R13.2: non-answers and hedges. With any of these, a yes/no choice matches nothing.
HEDGE_CUES: tuple[str, ...] = (
    "no se",
    "no estoy seguro",
    "pero",
    "aunque",
    "todavia",
    "caducado",
    "vencida",
    "suspendido",
    "sacando",
)
# Hedge forms R13.2's list does not spell out: inflections ("caducada", "se me vencio"),
# other non-answers ("no lo se", "no te oigo", "creo que") and a "no" that is not an answer
# ("no te preocupes"). They work exactly like HEDGE_CUES.
HEDGE_VARIANTS: tuple[str, ...] = (
    "no lo se",
    "no estoy segura",
    "no recuerdo",
    "no me acuerdo",
    "no sabria",
    "no entiendo",
    "no te oigo",
    "no te escucho",
    "no te he oido",
    "no te escuche",
    "no escuche",
    "no le escuche",
    "no te oi",
    "no oi",
    "no oigo",
    "no entendi",
    "no le entendi",
    "no se oye",
    "no se escucha",
    "me repites",
    "como dices",
    "que has dicho",
    "no te preocupes",
    "no pasa nada",
    "no hay problema",
    "no problem",
    "creo que",
    "caducada",
    "vencido",
    "vencio",
    "suspendida",
    "suspendieron",
    "tramitando",
    "tramite",
    "quitaron",
)
_HEDGES: tuple[str, ...] = HEDGE_CUES + HEDGE_VARIANTS
# A "no" answer is refused when the Candidate also says one of these without a "no" before
# it: "No, lo tengo" says they have it.
POSSESSION_WORDS: tuple[str, ...] = ("tengo", "tenemos", "tiene")
# The same for words that say the licence was got ("No, ya la saqué", "No, la renové"), as
# ``_normalise`` writes them (accents dropped).
ACQUIRED_WORDS: tuple[str, ...] = (
    "saque",
    "sacado",
    "obtuve",
    "obtenido",
    "consegui",
    "conseguido",
    "renove",
    "renovado",
)
# A "yes" answer is refused when the Candidate also negates in other words: "Claro, nunca lo
# he tenido", "Sí, sin licencia".
NEGATING_WORDS: tuple[str, ...] = ("tampoco", "nunca", "sin", "ni", "jamas")
# Words that may come before an answer's first cue ("Eh, pues sí").
FILLERS: tuple[str, ...] = (
    "eh",
    "ehh",
    "eeh",
    "eeeh",
    "ah",
    "mm",
    "mmm",
    "hmm",
    "em",
    "este",
    "pues",
    "bueno",
    "uy",
    "oye",
    "mira",
    "a ver",
    "o sea",
)

# R13.3: Spanish hour words, one to twenty-three, and the minute phrases after them.
HOUR_WORDS: dict[str, int] = {
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiuna": 21,
    "veintiuno": 21,
    "veintidos": 22,
    "veintitres": 23,
}
MINUTE_WORDS: dict[str, int] = {"y media": 30, "y cuarto": 15, "en punto": 0}
# Minute numbers above HOUR_WORDS ("cinco treinta"): the matcher does not read them.
_MINUTE_NUMBER_WORDS: tuple[str, ...] = ("veinticinco", "treinta", "cuarenta", "cincuenta")

# R13.3: the "other time" option of a slot choice.
OTHER_TIME_SYNONYMS: tuple[str, ...] = ("otro horario", "otra hora", "ninguno")

# Weekday names: a slot label's short form (``repo.local_label``) and the spoken full form,
# Monday first as in ``date.weekday()``.
_LABEL_WEEKDAYS: dict[str, int] = {
    **{d: i for i, d in enumerate(("lun", "mar", "mie", "jue", "vie", "sab", "dom"))},
    **{d: i for i, d in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))},
}
_SPOKEN_WEEKDAYS: dict[str, int] = {
    **{
        d: i
        for i, d in enumerate(
            ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
        )
    },
    **{
        d: i
        for i, d in enumerate(
            ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        )
    },
}
_AM_CUES: tuple[str, ...] = ("de la manana",)
_PM_CUES: tuple[str, ...] = ("de la tarde",)
# "de la noche" is 19:00-23:00 and midnight ("las doce de la noche"); before seven it is the
# small hours, which the matcher does not read.
_NIGHT_CUES: tuple[str, ...] = ("de la noche",)
# "a.m." / "p.m." (and "a. m.") as realtime writes them; read before punctuation is dropped.
_PERIOD_MARK = re.compile(r"(?<![^\W\d_])([ap])\.\s?m\.?(?![^\W\d_])")
_SLOT_LABEL = re.compile(r"^([^\W\d_]+)\.? (\d{1,2}) · (\d{1,2}):(\d{2})$")
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")
# Placeholders in :func:`_polarity_words`; "_" never survives :func:`_normalise`, so a
# transcript cannot produce them.
_AFFIRMATIVE_NO = "como_no"  # "cómo no", with its accent (in AFFIRMATIVE_CUES)
_CONDITIONAL_SI = "si_if"  # "si" without its accent: "if", not a cue


# --------------------------------------------------------------------------- text


def _normalise(text: str) -> str:
    """Case-fold, drop accents, turn every character that is not a letter, digit or ``:`` into
    a space, and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    kept = "".join(c if c.isalnum() or c == ":" else " " for c in bare)
    return " ".join(kept.split())


def _words(text: str) -> list[str]:
    return _normalise(text).split()


def _find(words: Sequence[str], phrase: Sequence[str]) -> int:
    """The index of the first whole-word occurrence of ``phrase`` in ``words``, or -1."""
    n = len(phrase)
    if n == 0:
        return -1
    for i in range(len(words) - n + 1):
        if list(words[i : i + n]) == list(phrase):
            return i
    return -1


def _has(words: Sequence[str], phrase: str) -> bool:
    return _find(words, phrase.split()) >= 0


def _has_any(words: Sequence[str], phrases: Sequence[str]) -> bool:
    return any(_has(words, p) for p in phrases)


def _without(words: Sequence[str], phrase: str) -> list[str]:
    """``words`` with every occurrence of ``phrase`` removed."""
    target = phrase.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        if list(words[i : i + len(target)]) == target:
            i += len(target)
        else:
            out.append(words[i])
            i += 1
    return out


# --------------------------------------------------------------------------- R15.1


def is_optout_phrase(text: str) -> bool:
    """True when the transcript holds one of R15's opt-out phrases as whole words."""
    try:
        return _has_any(_words(text), OPTOUT_PHRASES)
    except Exception as exc:
        log.warning("spoken opt-out phrase check failed: %s", type(exc).__name__)
        return False


# --------------------------------------------------------------------------- R14.1


def _yes_no_ids(choice: Interactive | None) -> tuple[str, str] | None:
    """``(yes id, no id)`` when ``choice`` is a yes/no choice: exactly two options, one whose
    normalised label starts with "si" or "yes" and one whose label is "no"."""
    if not choice:
        return None
    options = choice.get("options") or []
    if len(options) != 2:
        return None
    yes = [o["id"] for o in options if (_words(o["label"]) or [""])[0] in ("si", "yes")]
    no = [o["id"] for o in options if _normalise(o["label"]) == "no"]
    if len(yes) != 1 or len(no) != 1 or yes[0] == no[0]:
        return None
    return yes[0], no[0]


@dataclass(frozen=True)
class _Clause:
    words: list[str]
    question: bool  # the clause ends with "?"


def _raw_tokens(text: str) -> tuple[list[tuple[str, str]], str]:
    """The transcript's words before accents are dropped (case-folded, NFC), each with the
    punctuation between it and the word before; and the punctuation after the last word."""
    raw = unicodedata.normalize("NFC", text.casefold())
    tokens: list[tuple[str, str]] = []
    token = ""
    marks = token_marks = ""
    for char in raw + " ":
        if char.isalnum() or char == ":":
            if not token:
                token_marks, marks = marks, ""
            token += char
            continue
        if token:
            tokens.append((token, token_marks))
            token = ""
        if not char.isspace():
            marks += char
    return tokens, marks


def _clauses(text: str) -> list[_Clause]:
    """The transcript's words for the polarity cues, split into clauses at punctuation. As
    :func:`_words`, except that "cómo no" (accented, only a space between) becomes one
    affirmative-only word and "si" without its accent becomes a word that is no cue."""
    tokens, trailing = _raw_tokens(text)
    clauses: list[_Clause] = []
    current: list[str] = []
    i = 0
    while i < len(tokens):
        word, marks = tokens[i]
        if marks and current:
            clauses.append(_Clause(current, "?" in marks))
            current = []
        if word == "cómo" and tokens[i + 1 : i + 2] == [("no", "")]:
            current.append(_AFFIRMATIVE_NO)
            i += 2
            continue
        current.extend([_CONDITIONAL_SI] if word == "si" else _words(word))
        i += 1
    if current:
        clauses.append(_Clause(current, "?" in trailing))
    return clauses


def _polarity(text: str) -> tuple[bool, bool]:
    """``(affirmative, negative)`` cues anywhere in the transcript (:func:`_clauses`). The "no"
    of a hedge ("no se", "no lo se", "no te preocupes") is not a negative cue."""
    words = [w for clause in _clauses(text) for w in clause.words]
    for hedge in _HEDGES:
        if hedge.startswith("no "):
            words = _without(words, hedge)
    return _has_any(words, AFFIRMATIVE_CUES), _has_any(words, NEGATIVE_CUES)


def _starts_with(words: Sequence[str], phrases: Sequence[str]) -> bool:
    return any(list(words[: len(p.split())]) == p.split() for p in phrases)


def _answer_polarity(text: str) -> str | None:
    """ "yes" or "no" when the answer opens with a cue: the first clause that is not only
    FILLERS, after its leading FILLERS. None otherwise."""
    for clause in _clauses(text):
        words = list(clause.words)
        while words and _starts_with(words, FILLERS):
            words = words[len(next(f for f in FILLERS if _starts_with(words, [f])).split()) :]
        if not words:
            continue
        if _starts_with(words, AFFIRMATIVE_CUES):
            return "yes"
        if _starts_with(words, NEGATIVE_CUES):
            return "no"
        return None
    return None


def _has_unnegated(text: str, verbs: Sequence[str]) -> bool:
    """True when one of ``verbs`` appears without a "no" before it in its clause ("tengo",
    "No, lo tengo"; not "no tengo", "no lo tengo")."""
    for clause in _clauses(text):
        words = clause.words
        for i, word in enumerate(words):
            if word not in verbs:
                continue
            before = words[max(0, i - 2) : i]
            negated = before[-1:] == ["no"] or (
                before[-1:] in (["lo"], ["la"]) and before[:1] == ["no"]
            )
            if not negated:
                return True
    return False


def _has_unnegated_ya(text: str) -> bool:
    """True when "ya" ("already") appears and the next word is not "no" ("No, ya la saqué";
    not "No, ya no la tengo")."""
    return any(
        word == "ya" and clause.words[i + 1 : i + 2] != ["no"]
        for clause in _clauses(text)
        for i, word in enumerate(clause.words)
    )


def yes_no_conflict(text: str, choice: Interactive | None) -> bool:
    """True when ``choice`` is yes/no and the transcript holds both a yes and a no cue."""
    try:
        if _yes_no_ids(choice) is None:
            return False
        affirmative, negative = _polarity(text)
        return affirmative and negative
    except Exception as exc:
        log.warning("spoken yes/no check failed: %s", type(exc).__name__)
        return False


# --------------------------------------------------------------------------- R13.1-R13.3


@dataclass(frozen=True)
class _Slot:
    id: str
    weekday: int
    day: int
    hour: int
    minute: int


@dataclass(frozen=True)
class _Said:
    """The day and time a transcript names; None where it names none."""

    weekday: int | None
    day: int | None
    hour: int
    minute: int
    period: str | None  # "am", "pm", "night" or None


def match_option(text: str, choice: Interactive) -> str | None:
    """The one option of ``choice`` the transcript names, or None. Never raises."""
    try:
        return _match(text, choice)
    except Exception as exc:
        log.warning("spoken option match failed: %s", type(exc).__name__)
        return None


def _match(text: str, choice: Interactive) -> str | None:
    options = list(choice.get("options") or [])
    words = _words(text)
    if not options or not words:
        return None
    yes_no = _yes_no_ids(choice)
    if yes_no is not None:
        found = _yes_no_match(text, words, yes_no)
    elif any(_slot(o["id"], o["label"]) for o in options):
        found = _slot_match(text, words, options)
    else:
        cities = {_city(o["label"]) for o in options} - {""}
        said_cities = frozenset(c for c in cities if _has(words, c))
        found = [o["id"] for o in options if _names(words, o["id"], o["label"], said_cities)]
    ids = {o["id"] for o in options}
    return found[0] if len(found) == 1 and found[0] in ids else None


def _yes_no_match(text: str, words: Sequence[str], yes_no: tuple[str, str]) -> list[str]:
    """The yes or the no option: one polarity in the whole transcript, opening the answer,
    no hedge and no question. A "no" is refused when the Candidate also says they have or got
    the licence without a "no" before it (POSSESSION_WORDS, ACQUIRED_WORDS, or "ya" not
    followed by "no"): "No, ya la saqué" means they have it, so the agent reads it."""
    if _has_any(words, _HEDGES) or any(c.question for c in _clauses(text)):
        return []  # a hedge, or a question anywhere ("¿Sí?", "Sí, ¿me lo repites?")
    affirmative, negative = _polarity(text)
    answer = _answer_polarity(text)
    if affirmative == negative or answer != ("yes" if affirmative else "no"):
        return []
    if answer == "no" and (
        _has_unnegated(text, POSSESSION_WORDS)
        or _has_unnegated(text, ACQUIRED_WORDS)
        or _has_unnegated_ya(text)
    ):
        return []
    if answer == "yes" and _has_any(words, NEGATING_WORDS):
        return []
    return [yes_no[0] if affirmative else yes_no[1]]


def _city(label: str) -> str:
    """The normalised city of a "Name (City)" label, or ''."""
    if "(" not in label:
        return ""
    return _normalise(label.split("(", 1)[1])


def _names(words: Sequence[str], option_id: str, label: str, said_cities: frozenset[str]) -> bool:
    """The label, or a synonym, is in the transcript. A "Name (City)" label also matches on
    its Name alone, unless the transcript names only other cities of the choice."""
    phrases = [_normalise(label), *SYNONYMS.get(option_id, ())]
    head = _normalise(label.split("(", 1)[0]) if "(" in label else ""
    if head and (not said_cities or _city(label) in said_cities):
        phrases.append(head)
    return _has_any(words, [p for p in phrases if p])


def _slot(option_id: str, label: str) -> _Slot | None:
    """The day and time of a slot option's label (``repo.local_label``'s form), or None."""
    found = _SLOT_LABEL.match(label.strip())
    if found is None:
        return None
    weekday = _LABEL_WEEKDAYS.get(_normalise(found.group(1)))
    if weekday is None:
        return None
    return _Slot(option_id, weekday, int(found.group(2)), int(found.group(3)), int(found.group(4)))


def _slot_match(text: str, words: Sequence[str], options: Sequence[Any]) -> list[str]:
    found: list[str] = []
    slots = [s for s in (_slot(o["id"], o["label"]) for o in options) if s is not None]
    said = _said(text, words)
    if said is not None:
        found.extend(s.id for s in _fitting(slots, said))
    for option in options:
        if option["id"] == OTHER_OPTION_ID and _has_any(
            words, [_normalise(option["label"]), *OTHER_TIME_SYNONYMS]
        ):
            found.append(option["id"])
    return found


def _fitting(slots: Sequence[_Slot], said: _Said) -> list[_Slot]:
    """The slots that fit a day and an hour. A 12-hour reading counts only when it leaves one
    slot; a period cue fixes the half of the day."""
    same_day = [
        s
        for s in slots
        if (said.weekday is None or s.weekday == said.weekday)
        and (said.day is None or s.day == said.day)
    ]
    timed = [s for s in same_day if s.minute == said.minute]
    if said.period is not None:
        if said.hour > 12 or (said.period == "night" and said.hour < 7):
            return []
        if said.period == "night" and said.hour == 12:
            return [s for s in timed if s.hour == 0]
        hour = said.hour % 12 + (0 if said.period == "am" else 12)
        return [s for s in timed if s.hour == hour]
    if said.hour > 12:
        return [s for s in timed if s.hour == said.hour]
    twelve = [s for s in timed if s.hour % 12 == said.hour % 12]
    return twelve if len(twelve) == 1 else []


def _period(text: str, words: Sequence[str]) -> str | None:
    marks = {m.group(1) for m in _PERIOD_MARK.finditer(text.casefold())}
    if _has_any(words, _AM_CUES):
        marks.add("a")
    if _has_any(words, _PM_CUES):
        marks.add("p")
    if _has_any(words, _NIGHT_CUES):
        return "night" if marks <= {"p"} else None
    if len(marks) != 1:
        return None
    return "am" if marks == {"a"} else "pm"


def _number(word: str) -> int | None:
    if word.isdigit() and len(word) <= 2:
        return int(word)
    return HOUR_WORDS.get(word)


def _said(text: str, words: Sequence[str]) -> _Said | None:
    """The day and the time the transcript names, or None. None too when it names no day, no
    hour, two different weekdays, days or times, or a time form the matcher does not read
    ("y veinte", "menos cuarto")."""
    weekdays = {_SPOKEN_WEEKDAYS[w] for w in words if w in _SPOKEN_WEEKDAYS}
    days: set[int] = set()
    times: set[tuple[int, int]] = set()
    for i, word in enumerate(words):
        before = words[i - 1] if i else ""
        clock = _CLOCK.match(word)
        if clock is not None:
            times.add((int(clock.group(1)), int(clock.group(2))))
            continue
        value = _number(word)
        if value is None:
            continue
        if before in ("la", "las") and words[i - 2 : i - 1] == ["a"]:  # "a las cinco"
            minute = _minutes(words[i + 1 : i + 3])
            if minute is None:
                return None
            times.add((value, minute))
        elif before in _SPOKEN_WEEKDAYS or before in ("el", "dia"):
            days.add(value)
    if len(weekdays) > 1 or len(days) > 1 or len(times) != 1 or not (weekdays or days):
        return None
    [(hour, minute)] = times
    day = next(iter(days), None)
    if hour > 23 or minute > 59 or (day is not None and not 1 <= day <= 31):
        return None
    return _Said(next(iter(weekdays), None), day, hour, minute, _period(text, words))


def _minutes(rest: Sequence[str]) -> int | None:
    """Minutes after an hour word: "y media", "y cuarto", "en punto", or none (0). None when
    the hour is followed by a minute form the matcher does not read: "y veinte", "menos
    cuarto", or a number ("5 30" from "5.30", "cinco treinta")."""
    pair = " ".join(rest[:2])
    if pair in MINUTE_WORDS:
        return MINUTE_WORDS[pair]
    after = rest[0] if rest else ""
    if after in ("y", "menos") or after.isdigit() or after in HOUR_WORDS:
        return None
    if after in _MINUTE_NUMBER_WORDS:
        return None
    return 0


# --------------------------------------------------------------------------- R13.5


def check_spoken(text: str, choice: Interactive | None) -> SpokenDecision:
    """The first of: an opt-out phrase, a yes/no conflict, a matched option; else ``plain``."""
    try:
        if is_optout_phrase(text):
            return SpokenDecision("optout_request")
        if choice is None:
            return SpokenDecision("plain")
        if yes_no_conflict(text, choice):
            return SpokenDecision("reask")
        option_id = match_option(text, choice)
        if option_id is not None:
            return SpokenDecision("option", option_id=option_id)
        return SpokenDecision("plain")
    except Exception as exc:
        log.warning("spoken check failed: %s", type(exc).__name__)
        return SpokenDecision("plain")


# --------------------------------------------------------------------------- the hook


class SpokenChecker:
    """``spoken-turn``'s :class:`~criba.channel.service.SpokenChecks` hook (R9.2, R13.4, R14.2,
    R15.3). ``pending`` reads an Application's pending choice (``Router.pending_interactive``);
    ``jev`` is the router's Jev step, or None when Jev is off."""

    def __init__(
        self,
        *,
        pending: Callable[[sqlite3.Connection, str], Interactive | None],
        jev: JevStep | None,
    ) -> None:
        self.pending = pending
        self.jev = jev

    def before_accept(
        self, conn: sqlite3.Connection, candidate: dict[str, Any], *, text: str, now: datetime
    ) -> SpokenDecision:
        """The spoken checks for one new utterance. Never raises: ``plain`` on error."""
        try:
            if optout.is_opt_out_request(text):
                return SpokenDecision("plain")  # R15.3: the existing opt-out rule
            app = repo.get_open_application(conn, candidate["id"])
            if app is None:
                return check_spoken(text, None)
            decision = check_spoken(text, self.pending(conn, app["id"]))
            if decision.kind == "reask":
                reply = copy.spoken_reask(app.get("language"), app.get("country"))
                return SpokenDecision("reask", reply=reply)
            return decision
        except Exception as exc:
            log.warning("spoken before_accept failed: %s", type(exc).__name__)
            return SpokenDecision("plain")

    def after_reply(
        self,
        conn: sqlite3.Connection,
        app: dict[str, Any],
        *,
        message_pk: int,
        reply_pk: int,
        text: str,
        now: datetime,
    ) -> None:
        """Read the turn with Jev after its reply is stored, without a language decision."""
        if self.jev is None:
            return
        try:
            self.jev.read(
                conn,
                app,
                message_id=message_pk,
                text=text,
                now=now,
                decide_language=False,
                reply_message_id=reply_pk,
            )
        except Exception as exc:
            log.warning("spoken after_reply failed: %s", type(exc).__name__)
