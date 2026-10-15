"""The Operator-only Jev readings of one Application (``GET /api/applications/{id}/jev-readings``).

ADR 0066 lets Operators, and only Operators, see the probabilities Jev stored for each typed
Candidate message (ADR 0051), for calibration and incident review. Display only: no Knockout,
Rejection or Approval reads this module (``tests/jev/test_not_a_decision_input.py`` keeps it
that way). Ids, codes and numbers go out; the message ``body`` is never selected. ``intent``
and ``malice`` (ADR 0071) go out as stored, ``null`` when the row holds none.

One entry per typed Candidate message, the same predicate the router uses for ``typed_text``
(``channel/router.py``) plus ``modality = 'typed'``. A message with no stored row is ``expired``
when it is older than the retention period, else ``not_read``: the stored data cannot tell Jev
off, a Takeover, an opt-out or a failed read apart.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from criba.api import present
from criba.api.auth import Ctx, Operator
from criba.api.routing import new_router
from criba.db import repo
from criba.db.connection import iso
from criba.jev.policy import THRESHOLDS
from criba.jev.store import retention_days

router = new_router(tags=["applications"])


def _intent(row: Any) -> dict[str, Any] | None:
    """The stored intent reading (ADR 0071), or None when the row holds none. The contract
    requires the confidence with the choice, so a choice without one is sent as None too."""
    if row["intent"] is None or row["intent_confidence"] is None:
        return None
    return {"choice": row["intent"], "confidence": row["intent_confidence"]}


def _reading(row: Any) -> dict[str, Any]:
    return {
        "lang": row["lang"],
        "lang_confidence": row["lang_confidence"],
        "switch": row["switch"],
        "frustrated": row["frustrated"],
        "confused": row["confused"],
        "wants_human": row["wants_human"],
        "abusive": row["abusive"],
        "fired": json.loads(row["fired"]),
        "hint": row["hint"],
        "escalation": row["escalation"],
        "model": row["model"],
        "policy_version": row["policy_version"],
        "first_message": bool(row["first_message"]),
        "language_set": row["language_set"],
        "intent": _intent(row),
        "malice": row["malice"],
    }


def _entry(row: Any, cutoff: str) -> dict[str, Any]:
    if row["signal_id"] is None:
        status = "expired" if row["at"] < cutoff else "not_read"
        return {
            "message_id": row["message_id"],
            "at": row["at"],
            "stage": None,
            "status": status,
            "reading": None,
        }
    status = row["status"]
    return {
        "message_id": row["message_id"],
        "at": row["at"],
        "stage": row["stage"],
        "status": status,
        "reading": _reading(row) if status == "ok" else None,
    }


@router.get("/applications/{application_id}/jev-readings")
def get_jev_readings(application_id: str, user: Operator, ctx: Ctx) -> dict[str, Any]:
    cutoff = iso(ctx.clock.now() - timedelta(days=retention_days()))
    with ctx.db() as conn:
        present.visible_application(conn, user, application_id)
        rows = repo.typed_messages_with_signals(conn, application_id)
    return {
        "application_id": application_id,
        "thresholds": asdict(THRESHOLDS),
        "readings": [_entry(row, cutoff) for row in rows],
    }
