"""The opt-out keyword (Phase 1 detailed spec section 4.2, CONTEXT.md "Opted out").

The nudge copy tells the Candidate to reply STOP. Code, not the agent, recognises it:
an inbound text message that is *exactly* one of :data:`OPT_OUT_KEYWORDS`, after trimming,
case-folding and dropping accents and surrounding punctuation, is an opt-out request.
Whole-message only: "no paro", "baja california" and "quiero darme de baja?" are
ordinary messages and reach the agent. Button taps never match (the router passes only
``content_type == "text"``).
"""

from __future__ import annotations

import unicodedata
from typing import Final

# Normalised form (lower case, no accents). The one place the keyword set lives.
OPT_OUT_KEYWORDS: Final[frozenset[str]] = frozenset({"stop", "baja"})


def _strippable(char: str) -> bool:
    return char.isspace() or unicodedata.category(char).startswith("P")


def normalise(text: str) -> str:
    """Case-fold, drop accents, and trim whitespace and punctuation from both ends."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    start, end = 0, len(bare)
    while start < end and _strippable(bare[start]):
        start += 1
    while end > start and _strippable(bare[end - 1]):
        end -= 1
    return bare[start:end]


def is_opt_out_request(text: str) -> bool:
    """True when the whole message is an opt-out keyword."""
    return normalise(text) in OPT_OUT_KEYWORDS
