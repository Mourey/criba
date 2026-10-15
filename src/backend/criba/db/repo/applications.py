"""Applications: reading, covering Locations, and the shared column update."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from criba.db.repo.base import _BOOL_COLUMNS, _JSON_COLUMNS, Conflict, NotFound


def _application(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for col in _JSON_COLUMNS:
        if out[col] is not None:
            out[col] = json.loads(out[col])
    if out["summary_structured"] is not None:
        out["summary_structured"] = json.loads(out["summary_structured"])
    for col in _BOOL_COLUMNS:
        if out[col] is not None:
            out[col] = bool(out[col])
    return out


def get_application(conn: sqlite3.Connection, application_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
    return _application(row) if row else None


def require_application(conn: sqlite3.Connection, application_id: str) -> dict[str, Any]:
    app = get_application(conn, application_id)
    if app is None:
        raise NotFound(f"no Application {application_id}")
    return app


def _require_open(conn: sqlite3.Connection, application_id: str) -> dict[str, Any]:
    app = require_application(conn, application_id)
    if app["status"] != "open":
        raise Conflict(f"Application {application_id} is closed", "application_closed")
    return app


def get_open_application(conn: sqlite3.Connection, candidate_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM applications WHERE candidate_id = ? AND status = 'open'", (candidate_id,)
    ).fetchone()
    return _application(row) if row else None


def list_candidate_applications(
    conn: sqlite3.Connection, candidate_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM applications WHERE candidate_id = ? ORDER BY created_at, id",
        (candidate_id,),
    ).fetchall()
    return [_application(r) for r in rows]


def candidate_application_ids(conn: sqlite3.Connection, candidate_id: str) -> list[str]:
    """The ids of every Application of the Candidate, open or closed (no order)."""
    return [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM applications WHERE candidate_id = ?", (candidate_id,)
        ).fetchall()
    ]


def open_application_under_takeover(conn: sqlite3.Connection, candidate_id: str) -> bool:
    """Whether the Candidate's open Application is under a Takeover (``control = 'human'``)."""
    found = conn.execute(
        "SELECT 1 FROM applications WHERE candidate_id = ? AND status = 'open'"
        " AND control = 'human'",
        (candidate_id,),
    ).fetchone()
    return found is not None


def previous_application_outcome(
    conn: sqlite3.Connection, candidate_id: str, application_id: str
) -> str | None:
    """The Outcome of the Candidate's Application created just before ``application_id`` (None
    when there is none, or it is open)."""
    row = conn.execute(
        "SELECT outcome FROM applications WHERE candidate_id = ?"
        " AND rowid < (SELECT rowid FROM applications WHERE id = ?)"
        " ORDER BY rowid DESC LIMIT 1",
        (candidate_id, application_id),
    ).fetchone()
    return None if row is None else row["outcome"]


def application_location_ids(conn: sqlite3.Connection, application_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT location_id FROM application_locations WHERE application_id = ? ORDER BY 1",
        (application_id,),
    ).fetchall()
    return [r[0] for r in rows]


def covering_location_ids(conn: sqlite3.Connection, zone_id: str) -> list[str]:
    """Every active Location whose Service Area contains the Zone (ADR 0021)."""
    rows = conn.execute(
        "SELECT l.id FROM locations l"
        " JOIN service_areas sa ON sa.location_id = l.id"
        " JOIN service_area_zones saz ON saz.service_area_id = sa.id"
        " WHERE saz.zone_id = ? AND l.active = 1 ORDER BY l.id",
        (zone_id,),
    ).fetchall()
    return [r[0] for r in rows]


def clear_control(conn: sqlite3.Connection, application_id: str) -> None:
    """Give the chat back to the agent with no Takeover record (``control_user_id`` and
    ``control_since`` cleared); the caller writes the event."""
    conn.execute(
        "UPDATE applications SET control = 'agent', control_user_id = NULL,"
        " control_since = NULL WHERE id = ?",
        (application_id,),
    )


def _update(conn: sqlite3.Connection, application_id: str, sets: dict[str, Any]) -> None:
    cols = ", ".join(f"{c} = ?" for c in sets)
    conn.execute(f"UPDATE applications SET {cols} WHERE id = ?", (*sets.values(), application_id))


def take_control(conn: sqlite3.Connection, application_id: str, user_id: str, ts: str) -> bool:
    """Give the chat to ``user_id`` (compare-and-set on ``control = 'agent'``); whether it won."""
    cur = conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = ?, control_since = ?,"
        " updated_at = ? WHERE id = ? AND control = 'agent'",
        (user_id, ts, ts, application_id),
    )
    return cur.rowcount == 1


def release_control(conn: sqlite3.Connection, application_id: str, ts: str) -> bool:
    """Hand the chat back to the agent (compare-and-set on ``control = 'human'``); True if won."""
    cur = conn.execute(
        "UPDATE applications SET control = 'agent', control_user_id = NULL,"
        " control_since = NULL, updated_at = ? WHERE id = ? AND control = 'human'",
        (ts, application_id),
    )
    return cur.rowcount == 1


def application_location_rows(conn: sqlite3.Connection, application_id: str) -> list[sqlite3.Row]:
    """The Locations covering the Application (``id``, ``name``, ``country``, ``city``),
    ordered by country, city and name."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT l.id, l.name, l.country, l.city FROM application_locations al"
        " JOIN locations l ON l.id = al.location_id WHERE al.application_id = ?"
        " ORDER BY l.country, l.city, l.name",
        (application_id,),
    ).fetchall()
    return rows


def other_application_rows(
    conn: sqlite3.Connection, candidate_id: str, application_id: str
) -> list[sqlite3.Row]:
    """The Candidate's other Applications (``id``, ``status``, ``stage``, ``outcome``,
    ``created_at``), newest first."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT id, status, stage, outcome, created_at FROM applications"
        " WHERE candidate_id = ? AND id != ? ORDER BY created_at DESC, id DESC",
        (candidate_id, application_id),
    ).fetchall()
    return rows


def application_control(conn: sqlite3.Connection, application_id: str) -> str | None:
    """``control`` as stored now (``agent`` or ``human``), or None for no such Application."""
    row = conn.execute(
        "SELECT control FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    control: str | None = row["control"] if row else None
    return control


def count_applications(conn: sqlite3.Connection, base_sql: str, params: list[Any]) -> int:
    """How many Applications ``base_sql`` selects. ``base_sql`` is the caller's
    ``FROM applications a JOIN candidates c ... WHERE ...`` with its ``params``."""
    total: int = conn.execute(f"SELECT count(*) {base_sql}", params).fetchone()[0]
    return total


def application_ids_page(
    conn: sqlite3.Connection,
    base_sql: str,
    params: list[Any],
    order_sql: str,
    limit: int,
    offset: int,
) -> list[sqlite3.Row]:
    """One page of Application ids (``id``) from the caller's ``base_sql`` (see
    :func:`count_applications`), in the caller's ``order_sql``."""
    rows: list[sqlite3.Row] = conn.execute(
        f"SELECT a.id {base_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return rows


def first_location_id(conn: sqlite3.Connection, application_id: str) -> str | None:
    """The id of the Application's first covering Location (by id), or None."""
    row = conn.execute(
        "SELECT location_id FROM application_locations WHERE application_id = ?"
        " ORDER BY location_id LIMIT 1",
        (application_id,),
    ).fetchone()
    location_id: str | None = row[0] if row else None
    return location_id
