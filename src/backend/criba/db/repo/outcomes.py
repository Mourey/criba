"""Applications: stages, answers, Outcomes, and the summary."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from criba.db.connection import iso, transaction
from criba.db.repo.applications import (
    _require_open,
    _update,
    covering_location_ids,
    require_application,
)
from criba.db.repo.base import (
    _ANSWER_COLUMN,
    _RAW_INPUT_COLUMN,
    ANSWER_FIELDS,
    OUTCOMES,
    SCREENING_STAGES,
    STAGES,
    Actor,
    Conflict,
    _dumps,
    _now,
)
from criba.db.repo.events import add_event
from criba.db.repo.queue import _resolve_open_item, open_queue_item


def set_stage(
    conn: sqlite3.Connection,
    application_id: str,
    stage: str,
    *,
    actor: Actor = "agent",
    user_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Move an open Application to ``stage``, with a ``stage_changed`` event. No-op if unchanged."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    with transaction(conn):
        app = _require_open(conn, application_id)
        if app["stage"] != stage:
            _update(conn, application_id, {"stage": stage, "updated_at": iso(_now(now))})
            add_event(
                conn,
                candidate_id=app["candidate_id"],
                application_id=application_id,
                kind="stage_changed",
                actor=actor,
                user_id=user_id,
                data={"from": app["stage"], "to": stage},
                now=now,
            )
    return require_application(conn, application_id)


def record_answer(
    conn: sqlite3.Connection,
    application_id: str,
    field: str,
    value: Any,
    *,
    raw_input: str | None = None,
    message_id: int | None = None,
    start_date_far: bool | None = None,
    actor: Actor = "agent",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Store a validated, canonical answer and reset the miss counter.

    Validation happens before this call (``criba.screening``). ``zone`` takes a
    ``zone_id`` and recomputes the covering Locations; whether that means a
    Knockout is the caller's decision (see :func:`close_application`).
    ``full_name`` also updates the Candidate's display name. A Zone in another country moves
    the Application to that country, with its Spanish variant (ADR 0057).

    ``raw_input`` (the Candidate's words) goes to the ``*_input`` column for zone and
    driver_license only; the event references the inbound ``message_id`` instead (Q38).
    The event carries ``value`` only for canonical kinds: for a ``text`` field
    (``full_name``) it says the field was recorded, and the value is on the record.
    """
    kind = ANSWER_FIELDS.get(field)
    if kind is None:
        raise ValueError(f"unknown answer field {field!r}")
    column = _ANSWER_COLUMN.get(field, field)
    stored: Any = value
    if kind == "bool" and value is not None:
        stored = 1 if value else 0
    elif kind == "set" and value is not None:
        stored = _dumps(list(value))
    ts = iso(_now(now))
    with transaction(conn):
        app = _require_open(conn, application_id)
        sets: dict[str, Any] = {column: stored, "invalid_attempts": 0, "updated_at": ts}
        if field in _RAW_INPUT_COLUMN and raw_input is not None:
            sets[_RAW_INPUT_COLUMN[field]] = raw_input
        if field == "start_date" and start_date_far is not None:
            sets["start_date_far"] = 1 if start_date_far else 0
        _update(conn, application_id, sets)
        data: dict[str, Any] = {"field": field}
        if kind != "text":
            # A free-text value (the Candidate's name) stays on the record only.
            data["value"] = value
        if message_id is not None:
            data["message_id"] = message_id
        if field == "zone":
            locations = covering_location_ids(conn, value) if value else []
            conn.execute(
                "DELETE FROM application_locations WHERE application_id = ?", (application_id,)
            )
            conn.executemany(
                "INSERT INTO application_locations (application_id, location_id) VALUES (?, ?)",
                [(application_id, loc) for loc in locations],
            )
            data["locations"] = locations
            data.update(_follow_zone_country(conn, app, value, ts))
        if field == "full_name":
            conn.execute(
                "UPDATE candidates SET full_name = ?, updated_at = ? WHERE id = ?",
                (value, ts, app["candidate_id"]),
            )
        add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            kind="answer_recorded",
            actor=actor,
            data=data,
            now=now,
        )
    return require_application(conn, application_id)


_SPANISH_BY_COUNTRY = {"ES": "es-ES", "MX": "es-MX"}


def _follow_zone_country(
    conn: sqlite3.Connection, app: dict[str, Any], zone_id: str | None, ts: str
) -> dict[str, Any]:
    """The Application's country follows its recorded Zone's (ADR 0057).

    A web chat opened as MX by someone who lives in Chamberí would otherwise keep Mexico's
    Spanish, templates and time zone fallback while its Locations are in Madrid. A Spanish
    language follows the new country (es-ES, es-MX); ``en`` and no language yet stay as they
    are. Returns the codes for the ``answer_recorded`` event, empty when nothing changed.
    """
    row = (
        conn.execute("SELECT country FROM zones WHERE id = ?", (zone_id,)).fetchone()
        if zone_id
        else None
    )
    if row is None or row["country"] == app["country"]:
        return {}
    country = row["country"]
    sets: dict[str, Any] = {"country": country}
    changes: dict[str, Any] = {"country_changed": {"from": app["country"], "to": country}}
    language = app.get("language")
    if language in _SPANISH_BY_COUNTRY.values() and language != _SPANISH_BY_COUNTRY[country]:
        sets["language"] = _SPANISH_BY_COUNTRY[country]
        changes["language_changed"] = {"from": language, "to": sets["language"]}
        # The Candidate's language is that of their latest exchange (set_candidate_language).
        conn.execute(
            "UPDATE candidates SET language = ?, updated_at = ? WHERE id = ?",
            (sets["language"], ts, app["candidate_id"]),
        )
    _update(conn, app["id"], sets)
    return changes


def record_invalid_answer(
    conn: sqlite3.Connection,
    application_id: str,
    field: str,
    *,
    message_id: int | None = None,
    reason: str | None = None,
    ambiguous: bool = False,
    options: list[Any] | None = None,
    now: datetime | None = None,
) -> int:
    """Count a miss on the pending question; return the miss count (Phase 1 §4.2).

    The event references the Candidate's inbound ``message_id``, never their words (Q38),
    and carries the rule's ``reason`` code (ADR 0042), when given.
    """
    if field not in ANSWER_FIELDS:
        raise ValueError(f"unknown answer field {field!r}")
    with transaction(conn):
        app = _require_open(conn, application_id)
        attempts = app["invalid_attempts"] + 1
        _update(conn, application_id, {"invalid_attempts": attempts, "updated_at": iso(_now(now))})
        data: dict[str, Any] = {"field": field, "attempts": attempts}
        if reason is not None:
            data["reason"] = reason
        if message_id is not None:
            data["message_id"] = message_id
        if options is not None:
            data["options"] = options
        add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            kind="answer_ambiguous" if ambiguous else "answer_invalid",
            actor="agent",
            data=data,
            now=now,
        )
    return attempts


def mark_qualified(
    conn: sqlite3.Connection, application_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Screening passed: stage ``qualified``, and an Approval item in the review queue."""
    ts = iso(_now(now))
    with transaction(conn):
        app = _require_open(conn, application_id)
        if app["stage"] not in SCREENING_STAGES:
            raise Conflict("Screening has already ended", "not_in_screening")
        _update(
            conn,
            application_id,
            {"stage": "qualified", "qualified_at": ts, "updated_at": ts},
        )
        common = {"candidate_id": app["candidate_id"], "application_id": application_id}
        add_event(
            conn,
            **common,
            kind="stage_changed",
            actor="agent",
            data={"from": app["stage"], "to": "qualified"},
            now=now,
        )
        add_event(
            conn,
            **common,
            kind="screening_finished",
            actor="agent",
            data={"result": "qualified"},
            now=now,
        )
        open_queue_item(conn, application_id, "approval", now=now)
    return require_application(conn, application_id)


def mark_approved(
    conn: sqlite3.Connection,
    application_id: str,
    user_id: str,
    *,
    note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Approval ended Approved: stage ``approved``; resolves the open Approval item."""
    ts = iso(_now(now))
    with transaction(conn):
        app = _require_open(conn, application_id)
        if app["stage"] != "qualified":
            raise Conflict("only a Qualified Application can be Approved", "not_qualified")
        _update(
            conn,
            application_id,
            {"stage": "approved", "approved_at": ts, "approved_by": user_id, "updated_at": ts},
        )
        _resolve_open_item(conn, application_id, "approval", "approved", user_id, note, now)
        add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            kind="approved",
            actor="user",
            user_id=user_id,
            data={"note": note} if note else {},
            now=now,
        )
    return require_application(conn, application_id)


def close_application(
    conn: sqlite3.Connection,
    application_id: str,
    outcome: str,
    *,
    knockout_criterion: str | None = None,
    rejection_reason: str | None = None,
    rejection_note: str | None = None,
    actor: Actor = "agent",
    user_id: str | None = None,
    data: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """End an open Application with exactly one Outcome.

    - ``knockout`` needs ``knockout_criterion`` (``zone`` or ``driver_license``).
    - ``rejection`` needs a Recruiter (``actor="user"``, ``user_id``) and a
      ``rejection_reason``; ``other`` needs a note. It resolves an open Approval
      item as rejected.
    - ``abandoned`` is the scheduler's; ``withdrawn`` is the Candidate's.

    Closing ends the Claim, and releases an active Interview booking so the slot
    frees up. The stage stays where it was.
    """
    _check_close_args(outcome, knockout_criterion, rejection_reason, rejection_note, actor, user_id)
    ts = iso(_now(now))
    with transaction(conn):
        app = _require_open(conn, application_id)
        common = {"candidate_id": app["candidate_id"], "application_id": application_id}
        _update(
            conn,
            application_id,
            _closed_fields(outcome, knockout_criterion, rejection_reason, rejection_note, ts),
        )
        _emit_claim_released(conn, app, common, now)
        if outcome == "rejection":
            _resolve_open_item(
                conn, application_id, "approval", "rejected", user_id, rejection_note, now
            )
        _release_booked_slots(conn, application_id, common, ts, now)
        event_data = _outcome_event_data(
            data, knockout_criterion, rejection_reason, rejection_note, app["stage"]
        )
        _emit_outcome(conn, app, common, outcome, actor, user_id, event_data, now)
    return require_application(conn, application_id)


def _emit_claim_released(
    conn: sqlite3.Connection, app: dict[str, Any], common: dict[str, Any], now: datetime | None
) -> None:
    if app["claim_user_id"]:
        add_event(
            conn,
            **common,
            kind="claim_released",
            actor="system",
            data={"user_id": app["claim_user_id"], "reason": "application_closed"},
            now=now,
        )


def _emit_outcome(
    conn: sqlite3.Connection,
    app: dict[str, Any],
    common: dict[str, Any],
    outcome: str,
    actor: Actor,
    user_id: str | None,
    event_data: dict[str, Any],
    now: datetime | None,
) -> None:
    add_event(conn, **common, kind=outcome, actor=actor, user_id=user_id, data=event_data, now=now)
    if app["stage"] in SCREENING_STAGES and outcome != "rejection":
        add_event(
            conn,
            **common,
            kind="screening_finished",
            actor=actor,
            user_id=user_id,
            data={"result": outcome},
            now=now,
        )


def _check_close_args(
    outcome: str,
    knockout_criterion: str | None,
    rejection_reason: str | None,
    rejection_note: str | None,
    actor: Actor,
    user_id: str | None,
) -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    if (outcome == "knockout") != (knockout_criterion is not None):
        raise ValueError("knockout_criterion is required for, and only for, a Knockout")
    if (outcome == "rejection") != (rejection_reason is not None):
        raise ValueError("rejection_reason is required for, and only for, a Rejection")
    if outcome == "rejection" and (actor != "user" or not user_id):
        raise ValueError("a Rejection is a Recruiter's decision: actor='user' and user_id")
    if rejection_reason == "other" and not rejection_note:
        raise ValueError("a Rejection for 'other' needs a note")


def _closed_fields(
    outcome: str,
    knockout_criterion: str | None,
    rejection_reason: str | None,
    rejection_note: str | None,
    ts: str,
) -> dict[str, Any]:
    return {
        "status": "closed",
        "outcome": outcome,
        "knockout_criterion": knockout_criterion,
        "rejection_reason": rejection_reason,
        "rejection_note": rejection_note,
        "closed_at": ts,
        "claim_user_id": None,
        "claimed_at": None,
        "claim_last_action_at": None,
        "claimed_idle": 0,
        "qualified_waiting": 0,
        "updated_at": ts,
    }


def _release_booked_slots(
    conn: sqlite3.Connection,
    application_id: str,
    common: dict[str, Any],
    ts: str,
    now: datetime | None,
) -> None:
    for booking in conn.execute(
        "SELECT id, slot_id FROM bookings WHERE application_id = ? AND status = 'booked'",
        (application_id,),
    ).fetchall():
        conn.execute(
            "UPDATE bookings SET status = 'released', released_at = ? WHERE id = ?",
            (ts, booking["id"]),
        )
        add_event(
            conn,
            **common,
            kind="interview_slot_released",
            actor="system",
            data={
                "booking_id": booking["id"],
                "slot_id": booking["slot_id"],
                "reason": "application_closed",
            },
            now=now,
        )


def _outcome_event_data(
    data: dict[str, Any] | None,
    knockout_criterion: str | None,
    rejection_reason: str | None,
    rejection_note: str | None,
    stage: str,
) -> dict[str, Any]:
    event_data = dict(data or {})
    if knockout_criterion:
        event_data["criterion"] = knockout_criterion
    if rejection_reason:
        event_data["reason"] = rejection_reason
        if rejection_note:
            event_data["note"] = rejection_note
    event_data["stage"] = stage
    return event_data


def set_summary(
    conn: sqlite3.Connection,
    application_id: str,
    structured: dict[str, Any],
    prose: str | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Store (or overwrite, after a Reopen) the Application's summary."""
    ts = iso(_now(now))
    with transaction(conn):
        app = require_application(conn, application_id)
        _update(
            conn,
            application_id,
            {
                "summary_structured": _dumps(structured),
                "summary_prose": prose,
                "summary_at": ts,
                "updated_at": ts,
            },
        )
        add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            kind="summary_written",
            actor="system",
            now=now,
        )
    return require_application(conn, application_id)


def set_summary_structured(
    conn: sqlite3.Connection,
    application_id: str,
    structured: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    """Overwrite only the stored structured part, with no event: the summary prose retry
    records its next try there (ADR 0072). The prose and ``summary_at`` stay as they are."""
    _update(
        conn,
        application_id,
        {"summary_structured": _dumps(structured), "updated_at": iso(_now(now))},
    )


def prose_retries_due(conn: sqlite3.Connection, status: str, now: datetime) -> list[str]:
    """The Applications whose stored summary has ``prose_status`` = ``status`` and a next prose
    try due at ``now`` (ADR 0072), the longest-waiting first."""
    rows = conn.execute(
        "SELECT id FROM applications"
        " WHERE json_extract(summary_structured, '$.prose_status') = ?"
        " AND json_extract(summary_structured, '$.prose_retry.next_at') <= ?"
        " ORDER BY json_extract(summary_structured, '$.prose_retry.next_at'), id",
        (status, iso(now)),
    ).fetchall()
    return [row["id"] for row in rows]


def set_raw_input(
    conn: sqlite3.Connection,
    application_id: str,
    field: str,
    text: str | None,
    *,
    now: datetime | None = None,
) -> None:
    """Keep the Candidate's own words for ``zone`` or ``driver_license`` with no answer stored.

    A Knockout for a place that matches no Zone (ADR 0068) has no Zone id; the words that missed
    are all a Review has to read. Never in events (Q38).
    """
    column = _RAW_INPUT_COLUMN[field]
    _update(conn, application_id, {column: text, "updated_at": iso(_now(now))})
