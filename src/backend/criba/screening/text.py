"""Text normalisation for the Zone ranker (``zones.rank_zones``, behind ``search_zones``)."""

from __future__ import annotations

import re
import unicodedata

_NON_WORD = re.compile(r"[^\w]+|_+")


def fold(text: str) -> str:
    """Casefold and strip accents ("Chamberí" -> "chamberi", "Ñ" -> "n")."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """Fold, turn punctuation and underscores into spaces, collapse whitespace."""
    return " ".join(_NON_WORD.sub(" ", fold(text)).split())


def contains_phrase(haystack: str, phrase: str) -> bool:
    """Whole-word phrase match on already-normalised text."""
    if not phrase:
        return False
    return f" {phrase} " in f" {haystack} "
