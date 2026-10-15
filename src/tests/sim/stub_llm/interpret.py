"""What the stub model does with a Candidate's words (spec 006b, ADR 0042).

The agent interprets and the backend verifies, so `record_answer` takes a canonical value, not
the Candidate's text. The stub cannot understand words; it looks them up. Every persona reply
carries the value a competent agent would produce (`Reply.value`, and `Reply.query` for the
Zone), and `Interpreter` indexes them as `(field, text)`:

- a message a persona sends interprets to its canonical value (or its Zone search query);
- a tapped option id passes through as it is (a list for list fields and for the Zone);
- any other text interprets to `null`, which is today's "could not be understood" miss.

Pure functions and one immutable index, no I/O after construction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from sim.personas.loader import PERSONAS_DIR, load_personas
from sim.personas.schema import (
    FIELD_CODES,
    LIST_FIELDS,
    WEEKDAYS,
    Interpretation,
    Persona,
)

# Ids that reach the agent when the Candidate taps a button or a list row.
_ZONE_ID = re.compile(r"^zone_[A-Za-z0-9_]+$")
_SLOT_REF = re.compile(r"^so_[A-Za-z0-9_-]{1,61}$")
_ALL_CODES = frozenset(code for codes in FIELD_CODES.values() for code in codes)
_OPTION_IDS = frozenset({*_ALL_CODES, "other", "no"})
_STUB_LANGUAGE = {
    "es": "es-ES",
    "en": "en",
}  # the backend picks the Spanish country variant


class Interpreter:
    """The `(field, text)` index of every scripted Candidate message."""

    def __init__(
        self,
        entries: Iterable[Interpretation] = (),
        languages: Mapping[str, str] | None = None,
    ) -> None:
        self._entries: dict[tuple[str, str], Interpretation] = {}
        self._languages: dict[str, str] = {}
        for entry in entries:
            self._add(entry)
        for text, lang in (languages or {}).items():
            self._languages.setdefault(_key(text), lang)

    def _add(self, entry: Interpretation) -> None:
        key = (entry.field, _key(entry.text))
        known = self._entries.get(key)
        if known is not None and (known.value, known.query) != (
            entry.value,
            entry.query,
        ):
            raise ValueError(
                f"{entry.field}: {entry.text!r} would interpret two ways: "
                f"{known.value!r}/{known.query!r} and {entry.value!r}/{entry.query!r}"
            )
        self._entries[key] = entry
        self._languages.setdefault(key[1], entry.lang)

    @classmethod
    def from_personas(cls, personas: Iterable[Persona]) -> Interpreter:
        entries: list[Interpretation] = []
        languages: dict[str, str] = {}
        for persona in personas:
            entries.extend(persona.interpretations())
            for _, reply in persona.replies():
                languages.setdefault(_key(reply.text), persona.reply_lang(reply))
        return cls(entries, languages)

    @classmethod
    def from_directory(cls, directory: str | Path = PERSONAS_DIR) -> Interpreter:
        """Index every persona under `directory`; empty when there are none."""
        path = Path(directory)
        if not path.is_dir():
            return cls()
        return cls.from_personas(load_personas(path).values())

    def __len__(self) -> int:
        return len(self._entries)

    # ---- the questions a rule asks ----------------------------------------------------

    def entry(self, field: str, text: str) -> Interpretation | None:
        return self._entries.get((field, _key(text)))

    def interprets(self, field: str, text: str) -> bool:
        """Whether a scripted message exists for this field (for the Zone: with a query)."""
        found = self.entry(field, text)
        if found is None:
            return False
        return found.query is not None if field == "zone" else True

    def zone_query(self, text: str) -> str | None:
        """What the agent would pass to `search_zones`, or None when there is nothing to search."""
        found = self.entry("zone", text)
        return None if found is None else found.query

    def value(self, field: str, text: str, today: date | None = None) -> Any:
        """The canonical value for `record_answer(field, ...)` (the Zone: its tapped id list)."""
        found = self.entry(field, text)
        if found is not None and field != "zone":
            return resolve_date(found.value, today) if field == "start_date" else found.value
        if field == "zone":
            return [text.strip()] if _ZONE_ID.match(text.strip()) else None
        codes = FIELD_CODES.get(field, ())
        if text.strip() in codes:
            return [text.strip()] if field in LIST_FIELDS else text.strip()
        return None

    def is_tap(self, text: str) -> bool:
        """Whether the message is a tapped option id rather than typed text."""
        if _key(text) in self._languages:
            return False
        stripped = text.strip()
        return bool(
            _ZONE_ID.match(stripped) or _SLOT_REF.match(stripped) or stripped in _OPTION_IDS
        )

    def language(self, text: str) -> str | None:
        """`es` or `en` for a scripted message, else None."""
        return self._languages.get(_key(text))

    def api_language(self, text: str, guess: str | None) -> str | None:
        """The `language` for `get_application`: None after a tap or when nothing shows one."""
        known = self.language(text)
        if known is None:
            if self.is_tap(text):
                return None
            known = guess
        return _STUB_LANGUAGE.get(known or "")


@cache
def default_interpreter() -> Interpreter:
    """The seed personas next to this package (the Docker image ships them with the stub)."""
    return Interpreter.from_directory()


def resolve_date(value: Any, today: date | None) -> Any:
    """A persona's start date (ISO, `+2d`, `-1d`, `next:mon`, `next:sun+7d`) as an ISO date,
    or None; a list of two (the two days the words fit, ADR 0054) as a list of two."""
    if isinstance(value, list):
        resolved = [resolve_date(v, today) for v in value]
        return None if None in resolved else resolved
    if not isinstance(value, str) or not value:
        return None
    if re.match(r"^[0-9]{4}-", value):
        return value
    if today is None:
        return None
    if value.startswith("next:"):
        name, _, extra = value[len("next:") :].partition("+")
        ahead = (WEEKDAYS.index(name) - today.weekday()) % 7 or 7
        ahead += int(extra[:-1]) if extra else 0
        return (today + timedelta(days=ahead)).isoformat()
    return (today + timedelta(days=int(value[:-1]))).isoformat()


def _key(text: str) -> str:
    return text.strip()
