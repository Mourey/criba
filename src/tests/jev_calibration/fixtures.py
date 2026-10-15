"""The labelled messages the Jev questions and thresholds are fitted on (ADR 0051).

``messages.json`` is synthetic text only (no real Candidate wrote any of it), written to cover
the cases the router must get right: English openers, one-word answers in each language, real
switches in full sentences both ways, explicit requests to change the language of the chat
(``asks``) against mentions of a language that are not requests, es-ES and es-MX markers, mixed
messages, and calm versus frustrated, confused, asking-for-a-person and abusive messages in both
languages. Each carries
its labels, kept here and never sent to Jev. The ``dev`` split is for choosing wording and
thresholds, the ``holdout`` split is reported once.

``unreadable.json`` (ADR 0063) adds nonsense messages (keyboard mashing, random letters, made-up
words) against the readable cases closest to them: typos, slang, unusual names, numbers, short
answers, emoji and sentences in other languages. Every message in ``messages.json`` is readable.

``intent.json`` (ADR 0071) labels the two display-only questions: ``intent`` (one of
:data:`~criba.jev.battery.INTENTS`) and ``manipulation`` (the ``malice`` Noul). It holds every
intent in both splits, prompt-injection lines (the ``prompt_injection_es`` persona's two among
them), lines aimed at Jev itself (``m_jev_*``) and hard negatives that are not manipulation
(``hn_*``: a question about the job, asking whether it is a bot, a sharp complaint, a polite
request for a person). Its labels were written by hand from the battery's option definitions
before any harness code, never from a Jev answer. It loads last. The messages of the two other
files have no intent label and are all Manipulation negatives.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from criba.jev.battery import ASKS, INTENTS, SIGNALS, JevInput, Turn

MESSAGES = Path(__file__).with_name("messages.json")
UNREADABLE = Path(__file__).with_name("unreadable.json")
INTENT = Path(__file__).with_name("intent.json")
SPLITS = ("dev", "holdout")
COUNTRY_OF = {"es-MX": "MX", "es-ES": "ES"}


@dataclass(frozen=True)
class Fixture:
    id: str
    split: str
    text: str
    current_language: str
    country: str
    first: bool
    recent: tuple[Turn, ...]
    lang: str  # es, en, other or unclear
    switch: bool | None  # None: not scored (a third language, or a request for a language)
    signals: frozenset[str]  # the sentiment signals that are true
    asks: str = "none"  # es or en: the message asks the chat to continue in it (ADR 0061)
    unreadable: bool = False  # nonsense, not a word, answer or sentence in any language
    intent: str | None = None  # one of INTENTS; None: not labelled (ADR 0071)
    manipulation: bool = False  # tries to manipulate the assistant (the malice Noul)

    def jev_input(self) -> JevInput:
        return JevInput(
            message=self.text,
            current_language=self.current_language,
            country=self.country,
            recent_turns=self.recent,
        )


def _fixture(raw: dict[str, Any]) -> Fixture:
    current = raw["cur"]
    signals = frozenset(raw.get("yes", []))
    if not signals <= set(SIGNALS):
        raise ValueError(f"{raw['id']}: unknown signal in {sorted(signals)}")
    switch = raw.get("switch", False)
    asks = raw.get("asks", "none")
    if asks not in ASKS:
        raise ValueError(f"{raw['id']}: unknown asks {asks!r}")
    intent = raw.get("intent")
    if intent is not None and intent not in INTENTS:
        raise ValueError(f"{raw['id']}: unknown intent {intent!r}")
    return Fixture(
        id=raw["id"],
        split=raw["split"],
        text=raw["text"],
        current_language=current,
        country=raw.get("country") or COUNTRY_OF[current],
        first=bool(raw.get("first", False)),
        recent=tuple(Turn(sender, text) for sender, text in raw.get("recent", [])),
        lang=raw["lang"],
        switch=switch,
        signals=signals,
        asks=asks,
        unreadable=bool(raw.get("unreadable", False)),
        intent=intent,
        manipulation=bool(raw.get("manipulation", False)),
    )


def load_fixtures(*paths: Path) -> list[Fixture]:
    fixtures = [
        _fixture(raw)
        for path in paths or (MESSAGES, UNREADABLE, INTENT)
        for raw in json.loads(path.read_text(encoding="utf-8"))
    ]
    ids = [f.id for f in fixtures]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate fixture ids")
    bad = [f.id for f in fixtures if f.split not in SPLITS]
    if bad:
        raise ValueError(f"unknown split in {bad}")
    return fixtures
