"""Dependencies the channel takes from other lanes, as small injectable functions.

Lane W2-A builds both in ``criba.tools`` in the same wave, so the channel never imports
them at module load. The integrator passes the real functions to
:class:`criba.channel.service.Channel`; the defaults below look them up lazily.

- ``PendingChoiceFn(conn, application_id, *, now) -> Interactive | None`` (Q40): the buttons
  or list to attach to the agent's last outbound message. ``now`` is the channel's clock, so
  slot offers expire by the same time the rest of the channel uses (the sim clock in sim).
- ``RecordTapFn(conn, application_id, option_id, *, now) -> str | None`` (ADR 0043): records a
  tap on a pending answer option through ``record_answer`` and returns the text the agent
  receives for it, or None when the tap is not an answer option (a slot, say).
- ``InterviewOfferFn(conn, application_id, now) -> offer``: offers Interview slots from
  code, for the Interview-offer template outside WhatsApp's 24-hour window (Q41). Returns
  the ``offer_interview_slots`` output from ``contracts/tools.json``:
  ``{"slots": [{"slot_ref", "label", ...}], "booking_url", "other_label"}``.
"""

from __future__ import annotations

import importlib
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

log = logging.getLogger(__name__)

Interactive = dict[str, Any]


class PendingChoiceFn(Protocol):
    def __call__(
        self, conn: sqlite3.Connection, application_id: str, *, now: datetime
    ) -> Interactive | None: ...


class RecordTapFn(Protocol):
    def __call__(
        self, conn: sqlite3.Connection, application_id: str, option_id: str, *, now: datetime
    ) -> str | None: ...


InterviewOfferFn = Callable[[sqlite3.Connection, str, datetime], dict[str, Any]]


class NotWired(RuntimeError):
    """A dependency from another lane is not available in this build."""


def _lookup(module: str, name: str) -> Callable[..., Any] | None:
    try:
        return getattr(importlib.import_module(module), name, None)
    except ImportError:
        return None


def default_pending_choice(
    conn: sqlite3.Connection, application_id: str, *, now: datetime
) -> Interactive | None:
    """``criba.tools.pending_choice`` if W2-A's code is present, else no buttons."""
    fn = _lookup("criba.tools", "pending_choice")
    if fn is None:
        return None
    return fn(conn, application_id, now=now)


def default_record_tap(
    conn: sqlite3.Connection, application_id: str, option_id: str, *, now: datetime
) -> str | None:
    """``criba.tools.tap_answer`` if present, else the tap reaches the agent as its id."""
    fn = _lookup("criba.tools", "tap_answer")
    if fn is None:
        return None
    return fn(conn, application_id, option_id, now=now)


def default_interview_offer(
    conn: sqlite3.Connection, application_id: str, now: datetime
) -> dict[str, Any]:
    """No default: the integrator wires W2-A's slot offer (see the module docstring)."""
    raise NotWired("interview offer is not wired; pass interview_offer= to Channel")


def valid_interactive(value: Any) -> Interactive | None:
    """Keep ``value`` only if it matches the web chat ``Interactive`` schema."""
    if not isinstance(value, dict):
        return None
    style, options = value.get("style"), value.get("options")
    if style not in ("buttons", "list") or not isinstance(options, list) or not options:
        log.warning("dropping malformed interactive: %r", value)
        return None
    limit = 3 if style == "buttons" else 10
    clean = [
        {"id": str(o["id"]), "label": str(o["label"])[:24]}
        for o in options
        if isinstance(o, dict) and o.get("id") and o.get("label")
    ]
    if not clean or len(clean) > limit:
        log.warning("dropping interactive with %d options for style %s", len(clean), style)
        return None
    return {"style": style, "options": clean}
