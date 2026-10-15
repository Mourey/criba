"""Applications: start, resolve, Reopen."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Literal

from criba.db.connection import iso, new_id, parse_iso, transaction
from criba.db.repo.applications import (
    _application,
    _update,
    get_open_application,
    require_application,
)
from criba.db.repo.base import (
    _CRITERION_STAGE,
    REOPEN_WINDOW,
    REOPENABLE_OUTCOMES,
    Actor,
    Conflict,
    NotFound,
    _now,
)
from criba.db.repo.candidates import get_candidate
from criba.db.repo.events import add_event


def start_application(
    conn: sqlite3.Connection,
    candidate_id: str,
    channel: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Start a new Application at ``greeting``. Conflict if one is already open."""
    ts = iso(_now(now))
    with transaction(conn):
        candidate = get_candidate(conn, candidate_id)
        if candidate is None:
            raise NotFound(f"no Candidate {candidate_id}")
        if get_open_application(conn, candidate_id):
            raise Conflict("the Candidate already has an open Application", "application_open")
        application_id = new_id("app", now)
        conn.execute(
            "INSERT INTO applications"
            " (id, candidate_id, client_id, channel, country, language, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                application_id,
                candidate_id,
                candidate["client_id"],
                channel,
                candidate["country"],
                candidate["language"],
                ts,
                ts,
            ),
        )
        add_event(
            conn,
            candidate_id=candidate_id,
            application_id=application_id,
            kind="application_started",
            actor="candidate",
            data={"channel": channel},
            now=now,
        )
    return require_application(conn, application_id)


def reopenable_application(
    conn: sqlite3.Connection, candidate_id: str, *, now: datetime | None = None
) -> dict[str, Any] | None:
    """The Candidate's latest Application if it ended in Knockout or Abandoned within 30 days."""
    row = conn.execute(
        "SELECT * FROM applications WHERE candidate_id = ? AND status = 'closed'"
        " ORDER BY closed_at DESC, id DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    if row is None or row["outcome"] not in REOPENABLE_OUTCOMES:
        return None
    app = _application(row)
    return app if within_reopen_window(app, now=now) else None


def within_reopen_window(app: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Closed at most 30 days ago (the Candidate's Reopen window, ADR 0021)."""
    closed_at = app.get("closed_at")
    return closed_at is not None and _now(now) - parse_iso(closed_at) <= REOPEN_WINDOW


def reopen_application(
    conn: sqlite3.Connection,
    application_id: str,
    *,
    actor: Actor = "candidate",
    user_id: str | None = None,
    via: Literal["candidate", "review_overturned"] = "candidate",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reopen a Knockout or Abandoned Application.

    ``via="candidate"`` is the Candidate writing again: allowed within 30 days of
    closing. ``via="review_overturned"`` is a Recruiter overturning a Knockout on
    Review, which has no time limit and applies to Knockouts only.

    A Knockout resumes at the failed check: the stage goes back to that criterion
    and its answer is cleared so it is asked again (the old value is kept in the
    event). An Abandoned Application resumes at the stage it reached.
    """
    current = _now(now)
    with transaction(conn):
        app = require_application(conn, application_id)
        if app["status"] != "closed" or app["outcome"] not in REOPENABLE_OUTCOMES:
            raise Conflict("only a Knockout or Abandoned Application can reopen", "not_reopenable")
        if via == "review_overturned" and app["outcome"] != "knockout":
            raise Conflict("a Review applies only to a Knockout", "not_reopenable")
        if via == "candidate" and not within_reopen_window(app, now=current):
            raise Conflict("the 30-day Reopen window has passed", "reopen_window_passed")
        if get_open_application(conn, app["candidate_id"]):
            raise Conflict("the Candidate already has an open Application", "application_open")

        sets = {
            "status": "open",
            "outcome": None,
            "knockout_criterion": None,
            "closed_at": None,
            "invalid_attempts": 0,
            "stalled_since": None,
            "nudges_sent": 0,
            "reopened_count": app["reopened_count"] + 1,
            "last_reopened_at": iso(current),
            "updated_at": iso(current),
        }
        data: dict[str, Any] = {"previous_outcome": app["outcome"], "via": via}
        criterion = app["knockout_criterion"]
        if criterion:
            data["criterion"] = criterion
            sets["stage"] = _CRITERION_STAGE[criterion]
            if criterion == "zone":
                data["previous_value"] = app["zone_id"]
                sets["zone_id"] = None
                conn.execute(
                    "DELETE FROM application_locations WHERE application_id = ?",
                    (application_id,),
                )
            else:
                data["previous_value"] = app["driver_license"]
                sets["driver_license"] = None
        _update(conn, application_id, sets)
        add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            kind="reopened",
            actor=actor,
            user_id=user_id,
            data=data,
            now=now,
        )
    return require_application(conn, application_id)


def resolve_application(
    conn: sqlite3.Connection,
    candidate_id: str,
    channel: str,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], Literal["existing", "reopened", "knockout", "started"]]:
    """The Application an inbound message belongs to (ADRs 0021, 0033).

    - The open Application if there is one (``existing``).
    - Else, if the last one ended within 30 days:
      - Abandoned: it reopens on any message (``reopened``);
      - Knockout: it is returned **still closed** (``knockout``). The message goes to
        the agent on that Application, which can request a Review or, on a new valid
        answer to the failed criterion, reopen it through ``record_answer`` (ADR 0033).
    - Else a new Application (``started``).
    """
    with transaction(conn):
        app = get_open_application(conn, candidate_id)
        if app:
            return app, "existing"
        reopenable = reopenable_application(conn, candidate_id, now=now)
        if reopenable and reopenable["outcome"] == "knockout":
            return reopenable, "knockout"
        if reopenable:
            return reopen_application(conn, reopenable["id"], now=now), "reopened"
        return start_application(conn, candidate_id, channel, now=now), "started"
