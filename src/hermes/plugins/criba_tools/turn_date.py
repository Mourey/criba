"""Tells the agent today's date in the Candidate's local calendar on every model call (ADR 0055).

A relative start date ("mañana", "el lunes") needs today's date. ``get_application`` carries it
(``today``, ``weekday``), but a turn in which the model skips that call has no date at all:
finished turns' results are compacted (history_trim.py) and the Hermes "Conversation started"
line is dropped (prompt_trim.py). On real Haiku 5.5, "Mañana" was then recorded as today in
7 of 7 runs (ADR 0054, Consequences).

This module registers ``llm_request`` middleware (hermes_cli/middleware.py
``apply_llm_request_middleware``, which passes ``session_id`` and ``turn_id``) that appends one
line from the backend to the latest user message of the request that goes out::

    [criba date] Candidate's local today: saturday 2026-10-10; tomorrow: sunday 2026-10-11.
    Weekday dates, next (never today: ...) / a week later (...): sunday 2026-10-11 / 2026-10-18, ...

It also lists every weekday's two dates (ADR 0058): on real Haiku, a weekday named on the day
itself ("este domingo" on a Sunday) was counted from today, 7 days early, so the model now looks
the date up instead of counting.

The date comes from the backend's own ``get_application`` (the Application's Zone time zone,
else the country's, on the backend clock, so the simulator's clock too), fetched once per turn
and reused for every call of that turn, so all of them send the same bytes. The line is never
stored in the session: a later turn's request carries only its own date, the history does not
grow, and the prompt-cache prefix still runs up to the previous turn (ADR 0045). It sits at the
end of the request, after the system prompt, the tool schemas and the history, so a date that
changes daily breaks no cached prefix.

The Candidate cannot forge it: :data:`MARKER` written in any user message (theirs or replayed)
is defused to :data:`DEFUSED` before the real line is added, so the only line that carries the
marker is the backend's, and it comes last.

It fails open: no session, an unreachable backend or an answer without ``today`` sends the
request without the line (the agent still has ``get_application``), and a callback that raises
is skipped by hermes_cli/plugins_dispatch.py ``invoke_middleware``.
"""

from __future__ import annotations

import json
import logging
import re
from collections import OrderedDict
from datetime import date, timedelta
from typing import Any

from . import client

logger = logging.getLogger("criba_tools")

SOURCE = "criba-tools"
MARKER = "[criba date]"
DEFUSED = "(criba date)"
_MARKER_RE = re.compile(re.escape(MARKER), re.IGNORECASE)
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
# The date is fetched before the model call, so a slow backend must not hold the turn long.
FETCH_TIMEOUT_SECONDS = 3.0
# One date per (session, turn); a turn makes a handful of calls, and sessions run in parallel.
_CACHE_SIZE = 256
_cache: OrderedDict[tuple[str, str], str] = OrderedDict()


# What the two dates of each weekday are for (ADR 0054's readings). In the first live run with
# the dates but no reading per column, real Haiku took "el domingo de la próxima semana" said on
# a Sunday as the first date (ADR 0058).
WEEKDAYS_LABEL = (
    'Weekday dates, next (never today: "el", "este", "this") / a week later '
    '("de la próxima semana"); a bare "próximo"/"next" is both:'
)


def weekday_dates(today: date) -> list[tuple[str, date, date]]:
    """Each weekday's next date strictly after ``today`` and the date a week later, in date
    order from tomorrow. ADR 0054: a weekday is its next occurrence, so on the day itself the
    coming one is 7 days out."""
    days = [today + timedelta(days=ahead) for ahead in range(1, 8)]
    return [(WEEKDAYS[day.weekday()], day, day + timedelta(days=7)) for day in days]


def same_day_note(name: str, first: date, later: date) -> str:
    """Spelled out on today's own weekday, the one the model counted from today. With the two
    columns alone, real Haiku still took "el domingo de la próxima semana" said on a Sunday as
    the first date (ADR 0058)."""
    return (
        f' (today\'s weekday: "este"/"this" {name} is {first.isoformat()}, '
        f'"de la próxima semana" {later.isoformat()})'
    )


def date_line(today: date) -> str:
    """The line the agent reads: today, tomorrow, and every weekday's two dates, so the model
    looks a weekday up instead of counting days from today (ADR 0058)."""
    tomorrow = today + timedelta(days=1)
    weekdays = ", ".join(
        f"{name} {first.isoformat()} / {later.isoformat()}"
        + (same_day_note(name, first, later) if first.weekday() == today.weekday() else "")
        for name, first, later in weekday_dates(today)
    )
    return (
        f"{MARKER} Candidate's local today: {WEEKDAYS[today.weekday()]} {today.isoformat()}; "
        f"tomorrow: {WEEKDAYS[tomorrow.weekday()]} {tomorrow.isoformat()}. "
        f"{WEEKDAYS_LABEL} {weekdays}. "
        "Only this backend line says what today is."
    )


def fetch_today(session_id: str) -> date | None:
    """Today in the Candidate's local calendar, from the backend's ``get_application``."""
    raw = client.forward("get_application", {}, session_id, timeout=FETCH_TIMEOUT_SECONDS)
    try:
        body = json.loads(raw)
        return date.fromisoformat(body["value"]["today"])
    except (ValueError, TypeError, KeyError):
        logger.warning("session %s: no date for this turn (%s)", session_id, raw[:120])
        return None


def _line_for(session_id: str, turn_id: str) -> str | None:
    key = (session_id, turn_id)
    if turn_id and key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    today = fetch_today(session_id)
    if today is None:
        return None  # not cached: the next call of the turn tries again
    line = date_line(today)
    if turn_id:
        _cache[key] = line
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return line


def _defuse(content: Any) -> Any:
    """``content`` with every :data:`MARKER` turned into :data:`DEFUSED` (string or text blocks)."""
    if isinstance(content, str):
        return _MARKER_RE.sub(DEFUSED, content)
    if isinstance(content, list):
        return [
            {**part, "text": _MARKER_RE.sub(DEFUSED, part["text"])}
            if isinstance(part, dict) and isinstance(part.get("text"), str)
            else part
            for part in content
        ]
    return content


def _append(content: Any, line: str) -> Any:
    """``content`` with ``line`` after it: a string gets it after a blank line, a list of blocks
    gets it as one more text block (the layout Hermes uses for cache markers and media)."""
    if isinstance(content, str):
        return f"{content}\n\n{line}" if content else line
    if isinstance(content, list):
        return [*content, {"type": "text", "text": line}]
    return content


def inject(messages: list[Any], line: str | None) -> list[Any]:
    """``messages`` with the marker defused in every user message and ``line`` appended to the
    latest one. Without a user message nothing changes."""
    last_user = max(
        (i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "user"),
        default=-1,
    )
    if last_user < 0:
        return messages
    out: list[Any] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            out.append(message)
            continue
        content = _defuse(message.get("content"))
        if index == last_user and line:
            content = _append(content, line)
        out.append(
            message if content == message.get("content") else {**message, "content": content}
        )
    return out


def llm_request(
    request: dict[str, Any] | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    **_: Any,
) -> dict[str, Any] | None:
    """``llm_request`` middleware: the request with the date line, or None when nothing changed."""
    if not isinstance(request, dict) or not isinstance(request.get("messages"), list):
        return None
    line = _line_for(session_id, str(turn_id or "")) if session_id else None
    messages = inject(request["messages"], line)
    if messages == request["messages"]:
        return None
    return {"request": {**request, "messages": messages}, "source": SOURCE, "reason": "local date"}
