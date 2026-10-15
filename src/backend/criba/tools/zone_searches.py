"""What the agent's ``search_zones`` calls found for a Candidate message (ADR 0076).

The router's Zone backstop asks one thing after a run: did a search for the message being read
find nothing, in any country? A search's result is not in the record (the lookup is read-only,
and the tool-call trace keeps no result, ADR 0069), so the backend keeps that one bit here, in
process memory: per Application and Candidate message, whether a search matched a Zone anywhere.
Ids and a boolean only, never the query (ADR 0035).

The backend is one process (one uvicorn worker, one SQLite writer), and the tool calls and the
run that made them are served by it. A restart forgets the bits: the backstop then waits for the
second Candidate message that got no Zone, which offers the funnel all the same.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

MAX_KEPT = 1024  # recent messages; one run reads its own message's bit right after it ends

_lock = threading.Lock()
_found: OrderedDict[tuple[str, int], bool] = OrderedDict()


def note(application_id: str, message_id: int | None, *, matched: bool) -> None:
    """Record one search for ``message_id``: ``matched`` when its query matched a Zone in any
    country. One match among several searches for the same message is enough."""
    if message_id is None:
        return
    key = (application_id, message_id)
    with _lock:
        _found[key] = _found.get(key, False) or matched
        _found.move_to_end(key)
        while len(_found) > MAX_KEPT:
            _found.popitem(last=False)


def matched_nothing(application_id: str, message_id: int | None) -> bool:
    """Whether the agent searched for ``message_id`` and no search matched a Zone anywhere."""
    if message_id is None:
        return False
    with _lock:
        return _found.get((application_id, message_id)) is False


def clear() -> None:
    """Forget every search (tests)."""
    with _lock:
        _found.clear()


__all__ = ["MAX_KEPT", "clear", "matched_nothing", "note"]
