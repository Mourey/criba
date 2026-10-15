"""A stub for Jev's decisions endpoint, for the sim stack (ADR 0036, 0051).

``POST /api/alpha/decisions`` takes the request OpenRouter's endpoint takes
(``{model, state, questions}``) and answers in the same shape, deterministically and with no
model: the language by the stub model's stopword vote, the switch by word count, the tone
signals by keyword lists that cover what the seed personas write, and ``unreadable`` (ADR 0063)
by keyboard-row fragments and tripled letters. In sim mode the backend's
``CRIBA_JEV_URL`` points here (``docker-compose.sim.yml``) and ``check_sim_settings`` refuses
any public URL, so a test rig never reaches the real service.

An explicit request to change the language of the chat ("me preguntas en inglés porfi", "can we
continue in Spanish?") is found by a language name after "en"/"in" plus a request cue; a mention
("trabajé en inglés", "hablo inglés y español") has no cue, so the stub answers ``none`` for it,
as the real service does (ADR 0061).

``intent`` and ``malice`` (ADR 0071, display only) are answered from cue lists, in the order of
precedence given at :func:`intent_of`: an injection cue gives ``steering`` and malice 0.97, else
a stop cue ``wants_to_stop``, else a question mark or a job-question cue ``asking``, else a
message of only greetings and thanks ``other``, else ``answering``; malice is 0.02 without an
injection cue.

The answers are made up to drive the flows, not to measure Jev: calibration runs against the real
service (``jev_calibration``). The model name starts with ``stub`` so the spend guard counts it as
free.
"""

from __future__ import annotations

import itertools
import re
from typing import Any

from sim.stub_llm.engine import guess_lang

MODEL = "stub-jev"
_ids = itertools.count(1)

SIGNAL_PHRASES: dict[str, tuple[str, ...]] = {
    "frustrated": (
        "ya te dije",
        "otra vez",
        "harto",
        "third time",
        "same thing",
        "tired of",
        "no sirve",
        "ugh",
    ),
    "confused": ("no entiendo", "don't understand", "what do you mean", "qué es", "no sé qué"),
    "wants_human": (
        "una persona",
        "con alguien",
        "humano",
        "reclutador",
        "real person",
        "a human",
        "recruiter",
        "someone from the team",
        "talk to someone",
    ),
    "abusive": ("imbécil", "idiot", "mierda", "fuck", "estúpid", "stupid"),
}
HIGH, LOW = 0.97, 0.02

# `intent` and `malice` (ADR 0071). The cues are matched on the lowercased message, in this order
# of precedence: an injection cue gives `steering` (and malice HIGH, else LOW), then a stop cue
# `wants_to_stop`, then a question mark or a job-question cue `asking`, then a message made only
# of greetings and thanks `other`, else `answering`. The stub never answers `off_topic`.
INJECTION_CUES = (
    "ignora tus instrucciones",
    "system prompt",
    "system:",
    "ignore your instructions",
    "ya está aprobada",
)
STOP_CUES = ("ya no me interesa", "no quiero seguir", "not interested anymore")
JOB_QUESTION_CUES = (
    "cuánto pagan",
    "cuanto pagan",
    "sueldo",
    "salario",
    "horario",
    "do i need",
    "how much",
    "qué documentos",
    "what documents",
)
GREETING_WORDS = frozenset(
    {
        "hola", "buenas", "buenos", "días", "dias", "tardes", "noches", "gracias", "muchas",
        "hi", "hello", "hey", "thanks", "thank", "you",
    }
)  # fmt: skip

# Keyboard mashing: runs along a keyboard row, or one letter three times. A message whose words
# mostly carry one, and that the stopword vote cannot place in a language, is unreadable.
MASH_FRAGMENTS = (
    "asd", "sdf", "dfg", "fgh", "ghj", "hjk", "jkl", "qwe", "wer", "ert", "rty",
    "zxc", "xcv", "cvb", "vbn", "bnm", "ksk", "jqw",
)  # fmt: skip
_TRIPLED = re.compile(r"([a-zñ])\1\1")
_ALPHA_WORD = re.compile(r"[^\W\d_]+")


def unreadable(message: str) -> bool:
    """The stub's keyboard-mash rule (ADR 0063)."""
    if guess_lang(message) is not None:
        return False
    words = [w.lower() for w in _ALPHA_WORD.findall(message)]
    mashed = [w for w in words if _TRIPLED.search(w) or any(f in w for f in MASH_FRAGMENTS)]
    return bool(words) and 2 * len(mashed) >= len(words)


_LANGUAGE_NAMES = {
    "inglés": "en",
    "ingles": "en",
    "english": "en",
    "español": "es",
    "espanol": "es",
    "castellano": "es",
    "spanish": "es",
}
_ASKED = re.compile(r"\b(?:en|in)\s+(" + "|".join(_LANGUAGE_NAMES) + r")\b")
_ASKED_PLEASE = re.compile(
    r"\b(" + "|".join(_LANGUAGE_NAMES) + r")\s*,?\s*(?:please|pls|porfa|porfi|por favor)\b"
)
_REQUEST_CUES = (
    "porfi",
    "porfa",
    "por favor",
    "please",
    "pls",
    "can we",
    "could we",
    "can you",
    "could you",
    "podemos",
    "puedes",
    "podrías",
    "me preguntas",
    "me hablas",
    "háblame",
    "hablame",
    "escríbeme",
    "escribeme",
    "mejor",
    "prefiero",
    "i'd rather",
    "seguimos",
    "sigamos",
    "continue",
)


def asked_language(lowered: str) -> str:
    """``en``, ``es`` or ``none``: the language a message asks the chat to continue in."""
    found = _ASKED_PLEASE.search(lowered)
    if found is None:
        found = _ASKED.search(lowered)
        if found is None or not any(cue in lowered for cue in _REQUEST_CUES):
            return "none"
    return _LANGUAGE_NAMES[found.group(1)]


def has_injection(lowered: str) -> bool:
    """Whether the lowercased message carries one of the stub's injection cues."""
    return any(cue in lowered for cue in INJECTION_CUES)


def intent_of(lowered: str) -> str:
    """The stub's `intent` for a lowercased message, by the order of precedence above."""
    if has_injection(lowered):
        return "steering"
    if any(cue in lowered for cue in STOP_CUES):
        return "wants_to_stop"
    if "?" in lowered or any(cue in lowered for cue in JOB_QUESTION_CUES):
        return "asking"
    words = _ALPHA_WORD.findall(lowered)
    if words and all(word in GREETING_WORDS for word in words):
        return "other"
    return "answering"


def _family(language: str | None) -> str:
    return "en" if language == "en" else "es"


def read(state: dict[str, Any]) -> dict[str, Any]:
    """The stub's reading of one state, as plain values."""
    conversation = state.get("conversation") or {}
    message = str(state.get("latest_candidate_message") or "")
    lowered = message.lower()
    guessed = guess_lang(message)
    lang = guessed or "unclear"
    words = len([w for w in message.split() if any(c.isalpha() for c in w)])
    switch = (
        0.95
        if guessed is not None
        and words >= 3
        and guessed != _family(str(conversation.get("current_language") or "es"))
        else 0.03
    )
    signals = {
        name: HIGH if any(p in lowered for p in phrases) else LOW
        for name, phrases in SIGNAL_PHRASES.items()
    }
    return {
        "lang": lang,
        "lang_confidence": 0.99 if guessed else 0.9,
        "switch": switch,
        "asks": asked_language(lowered),
        "asks_confidence": 0.97,
        **signals,
        "unreadable": HIGH if unreadable(message) else LOW,
        "intent": intent_of(lowered),
        "intent_confidence": 0.97,
        "malice": HIGH if has_injection(lowered) else LOW,
    }


def decisions(body: dict[str, Any]) -> dict[str, Any]:
    """The response for a decisions request: one answer per question id."""
    reading = read(body.get("state") or {})
    answers: dict[str, Any] = {}
    for question_id, question in (body.get("questions") or {}).items():
        if question_id == "intent" and question.get("type") == "choice":  # ADR 0071
            options = list((question.get("criteria") or {}).keys())
            intent = reading["intent"] if reading["intent"] in options else (options or [""])[0]
            answers[question_id] = {
                "type": "choice",
                "choice": intent,
                "probabilities": {intent: reading["intent_confidence"]},
                "confidence": reading["intent_confidence"],
            }
            continue
        if question.get("type") == "choice":
            options = list((question.get("criteria") or {}).keys())
            asked = question_id == "asks"
            wanted = reading["asks"] if asked else reading["lang"]
            confidence = reading["asks_confidence" if asked else "lang_confidence"]
            choice = wanted if wanted in options else (options or [""])[0]
            answers[question_id] = {
                "type": "choice",
                "choice": choice,
                "probabilities": {choice: confidence},
                "confidence": confidence,
            }
        else:
            answers[question_id] = {"type": "noul", "noul": float(reading.get(question_id, 0.0))}
    return {
        "model": MODEL,
        "answers": answers,
        "usage": {"input_tokens": 0, "output_tokens": 0, "cost": 0.0},
        "id": f"gen-dec-stub-{next(_ids):06d}",
        "provider": "stub",
    }
