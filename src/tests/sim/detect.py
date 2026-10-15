"""Text heuristics shared by the driver and the assertion helpers.

The driver has to know which question the agent just asked ("the n-th time the agent asks a
field"), and the assertions have to recognise an AI disclosure and a reply's language, all
without asserting on prose. These helpers are deliberately crude and bilingual (Spanish and
English). They only ever steer the simulated Candidate or flag an obvious miss; the case record
stays the source of truth for pass or fail.

`detect_prompt` is pluggable: `PersonaDriver(detector=...)` takes any callable with the same
signature, for example one that reads the Application's stage from the REST API.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

PromptKind = Literal[
    "full_name",
    "zone",
    "driver_license",
    "availability",
    "preferred_schedule",
    "experience_years",
    "experience_platforms",
    "start_date",
    "confirm",
]

_AVAILABILITY_IDS = {"full_time", "part_time", "weekends"}
_SCHEDULE_IDS = {"morning", "afternoon", "evening", "flexible"}
# The platforms list ends with the "no app" row (ADR 0053).
_PLATFORM_IDS = {"glovo", "uber_eats", "just_eat", "rappi", "didi_food", "other", "none"}
# A start-date choice offers the dates themselves (ADR 0054).
_DATE_ID = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

# Checked in this order against the last question of the message, accents stripped.
# More specific topics come first ("platforms" before "experience", "confirm" before all).
_PROMPT_PATTERNS: tuple[tuple[PromptKind, re.Pattern[str]], ...] = (
    (
        "confirm",
        # "right now" is a time, not a check ("a licence that's valid right now?").
        re.compile(
            r"correct|\bright\b(?! now)|resum|summary|todo bien|all good|looks good|bien asi"
        ),
    ),
    (
        "experience_platforms",
        re.compile(
            r"plataforma|platform|\bapps?\b|aplicacion|aplicativ|glovo|rappi|uber|didi"
            r"|just eat"
        ),
    ),
    (
        "availability",
        re.compile(r"disponibilidad|availab|tiempo completo|full.?time|jornada"),
    ),
    ("preferred_schedule", re.compile(r"horario|turno|schedule|shift")),
    (
        "driver_license",
        re.compile(
            r"licencia|licen[cs]e|carnet|carne\b|permiso de (conducir|manejo)|conducir|manejo"
        ),
    ),
    (
        "experience_years",
        re.compile(r"experiencia|experience|cuanto tiempo|how long|how many years|cuantos anos"),
    ),
    (
        "start_date",
        re.compile(r"empezar|empiezas|comenzar|incorporar|\bstart\b|cuando|\bwhen\b|fecha"),
    ),
    (
        "zone",
        re.compile(
            r"\bzona|ciudad|barrio|colonia|municipio|alcaldia|delegacion|vives|\bvive\b"
            r"|\bcity\b|\blive\b|neighbo|district|donde"
        ),
    ),
    ("full_name", re.compile(r"nombre|apellido|llamas|\bname\b|call you")),
)

# A question about how long the Candidate has done the work may name the apps in passing ("how many
# years on delivery apps?") and is still the years question; one that asks *which* apps is not.
_DURATION_RE = re.compile(
    r"cuantos anos|cuanto tiempo|anos de experiencia|how many years|how long|years of"
)
_WHICH_PLATFORM_RE = re.compile(
    r"\b(que|cual|cuales|which|what)\s+(?:\w+\s+){0,2}"
    r"(apps?|aplicaci\w+|aplicativ\w+|plataforma\w*|platform\w*)\b"
)

# A field asked for as a statement or an imperative ("Necesito tu nombre y apellido.", "Tell me
# your city."). Real models sometimes re-ask with no question mark. The sentence must carry both a
# request cue addressed to the Candidate (the agent's "I need", "tell me", "please send") and the
# field's own topic words, so a Knockout that merely mentions the license ("Necesitamos licencia
# de manejo vigente. Escribenos cuando la tengas.") still asks nothing. Accents are stripped.
_REQUEST_RE = re.compile(
    r"\bnecesito (?:tu|tus|su|sus|el|la|los|las|que|saber|conocer)\b"
    r"|\bme (?:falta|faltan)\b"
    r"|\b(?:me gustaria|quisiera|quiero) (?:saber|conocer)\b"
    r"|\b(?:dime|dimelo|digame|cuentame|indicame|dame|damelo|pasame|comparteme|enviame|mandame"
    r"|escribeme|confirmame|aclarame)\b"
    r"|\b(?:indica|comparte|envia|manda|escribe|confirma|aclara)\s+(?:tu|tus|su|sus|el|la)\b"
    r"|\bpor favor,? (?:indica|dime|cuenta|comparte|envia|manda|escribe|confirma|aclara)"
    r"|\bi need (?:your|to know)\b"
    r"|\b(?:tell|give|send|show) me\b|\blet me know\b"
    r"|\bplease,? (?:tell|send|share|give|provide|type|write|confirm|enter|let me know)\b"
    r"|\b(?:send|share|give|provide|type|enter) (?:me )?(?:your|the)\b"
    r"|\bi(?:'d| would) like to (?:know|get)\b"
)

# Abbreviations and ellipses whose periods are not sentence ends: "(Glovo, Uber Eats, etc.)?".
_NOT_A_STOP_RE = re.compile(
    r"\b(?:etc|e\.g|i\.e|p\.\s?ej|ej|vs|aprox|approx|tel)\.|\.{2,}", re.IGNORECASE
)
_DOT = "\u2024"  # one dot leader, stands in for a period that does not end a sentence

_ES_WORDS = frozenset(
    [
        "de",
        "la",
        "el",
        "que",
        "y",
        "en",
        "un",
        "una",
        "tu",
        "tus",
        "para",
        "con",
        "por",
        "es",
        "soy",
        "tengo",
        "hola",
        "gracias",
        "te",
        "me",
        "lo",
        "los",
        "las",
        "si",
        "sí",
        "cuál",
        "cuando",
        "cómo",
        "qué",
        "eres",
        "estás",
        "vives",
        "puedes",
        "podrías",
        "quieres",
        "gustaría",
        "vuestro",
        "solicitud",
        "nombre",
        "zona",
        "licencia",
        "carnet",
        "equipo",
        "ayudo",
        "escríbenos",
        "escribenos",
        "mejor",
        "ya",
        "yo",
        "mi",
        "mis",
        "del",
        "al",
        "más",
        "pero",
        "también",
        "muy",
        "bien",
        "claro",
        "vale",
        "aquí",
        "tienes",
        "tienes",
        "cómo",
        "está",
    ]
)
_EN_WORDS = frozenset(
    [
        "the",
        "and",
        "to",
        "is",
        "you",
        "your",
        "do",
        "have",
        "what",
        "i",
        "am",
        "my",
        "can",
        "we",
        "in",
        "of",
        "for",
        "with",
        "please",
        "yes",
        "hi",
        "thanks",
        "will",
        "it",
        "that",
        "an",
        "are",
        "this",
        "be",
        "not",
        "our",
        "would",
        "could",
        "how",
        "when",
        "where",
        "which",
        "who",
        "at",
        "on",
        "or",
        "if",
        "application",
        "name",
        "zone",
        "license",
        "continue",
        "english",
        "sorry",
        "stop",
        "messaging",
        "done",
        "good",
        "luck",
        "right",
        "summary",
    ]
)

_AI_PATTERNS = (
    re.compile(r"\b(ia|ai)\b"),
    re.compile(r"inteligencia artificial|artificial intelligence"),
    re.compile(r"asistente (virtual|automatico|digital)|virtual assistant|automated assistant"),
    re.compile(r"\bchat ?bot\b|\bbot\b"),
)

URL_RE = re.compile(r"https?://[^\s)>\]]+")
# Emoji and pictographs. Phase 1 section 5: none in Knockout or Rejection messages.
EMOJI_RE = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff\U0000fe0f]")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def normalize(text: str) -> str:
    """Lower-case, accent-free text for keyword matching."""
    return strip_accents(text).lower()


def guess_language(text: str) -> Literal["es", "en"] | None:
    """Spanish or English by stopword count; None when it cannot tell (short or mixed)."""
    words = re.findall(r"[a-záéíóúñü']+", text.lower())
    es = sum(1 for w in words if w in _ES_WORDS)
    en = sum(1 for w in words if w in _EN_WORDS)
    if "¿" in text or "¡" in text:
        es += 2
    if es == en:
        return None
    return "es" if es > en else "en"


def has_ai_disclosure(text: str) -> bool:
    """True when the text says it is an AI, a bot or a virtual assistant (EU AI Act)."""
    norm = normalize(text)
    return any(p.search(norm) for p in _AI_PATTERNS)


def last_question(text: str) -> str | None:
    """The last sentence of `text` that ends in a question mark, or None."""
    shielded = _NOT_A_STOP_RE.sub(lambda m: m.group(0).replace(".", _DOT), text)
    found = re.findall(r"[^.!?¿\n]*\?", shielded)
    found = [q.strip().replace(_DOT, ".") for q in found if q.strip("? \t")]
    return found[-1] if found else None


def last_request(text: str) -> str | None:
    """The last sentence that asks the Candidate for something without a question mark, or None.

    Only sentences with a request cue count (`_REQUEST_RE`); a plain statement does not.
    """
    shielded = _NOT_A_STOP_RE.sub(lambda m: m.group(0).replace(".", _DOT), text)
    sentences = [part.replace(_DOT, ".").strip() for part in re.split(r"[.!?¿¡\n]+", shielded)]
    found = [x for x in sentences if x and _REQUEST_RE.search(normalize(x))]
    return found[-1] if found else None


def asks_something(text: str, interactive: dict[str, Any] | None = None) -> bool:
    return bool(interactive) or "?" in text or "¿" in text


def zone_options(interactive: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The options of an interactive message when every one answers the Zone question: a Zone
    id, or a city of the Zone funnel with its "none of these" (ADR 0068); else []."""
    options = (interactive or {}).get("options") or []
    if options and all(str(o.get("id", "")).startswith(("zone_", "city_")) for o in options):
        return list(options)
    return []


def _from_options(interactive: dict[str, Any] | None) -> PromptKind | None:
    if not interactive:
        return None
    if zone_options(interactive):
        return "zone"
    ids = {str(o.get("id")) for o in interactive.get("options", [])}
    if ids and ids <= _AVAILABILITY_IDS:
        return "availability"
    if ids and ids <= _SCHEDULE_IDS:
        return "preferred_schedule"
    if ids and ids <= _PLATFORM_IDS:
        return "experience_platforms"
    if ids and all(_DATE_ID.match(i) for i in ids):
        return "start_date"
    return None


def detect_prompt(text: str, interactive: dict[str, Any] | None = None) -> PromptKind | None:
    """Which question does this agent message ask? None when it asks no recognisable one.

    Looks at the option ids first (they are canonical), then at the last question in the text,
    then at the last sentence that asks in a statement or imperative ("Necesito tu nombre y
    apellido."). A plain statement is never a prompt: a Knockout template mentions the license
    without asking for it.
    """
    by_options = _from_options(interactive)
    if by_options:
        return by_options
    question = last_question(text)
    kind = _kind_of(question) if question is not None else None
    if kind is None:
        # No question mark, or a question with no topic: the agent may have asked in a statement.
        request = last_request(text)
        kind = _kind_of(request) if request is not None else None
    return kind


def _kind_of(sentence: str) -> PromptKind | None:
    norm = normalize(sentence)
    for kind, pattern in _PROMPT_PATTERNS:
        if not pattern.search(norm):
            continue
        if (
            kind == "experience_platforms"
            and _DURATION_RE.search(norm)
            and not _WHICH_PLATFORM_RE.search(norm)
        ):
            return "experience_years"
        return kind
    return None
