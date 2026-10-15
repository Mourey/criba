"""Review queue (minimal: what the lifecycle itself needs) and the Recruiters' hold."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from criba.db.connection import iso, new_id, transaction
from criba.db.repo.applications import require_application
from criba.db.repo.base import _now
from criba.db.repo.events import add_event


def open_queue_item(
    conn: sqlite3.Connection,
    application_id: str,
    kind: Literal["approval", "review", "escalation"],
    *,
    reason: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Open a queue item, or return the one already open of that kind (one per kind)."""
    with transaction(conn):
        existing = conn.execute(
            "SELECT * FROM queue_items WHERE application_id = ? AND kind = ? AND status = 'open'",
            (application_id, kind),
        ).fetchone()
        if existing:
            return dict(existing)
        app = require_application(conn, application_id)
        item_id = new_id("qi", now)
        conn.execute(
            "INSERT INTO queue_items (id, application_id, kind, reason, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, application_id, kind, reason, note, iso(_now(now))),
        )
        event_kind = {
            "approval": "approval_requested",
            "review": "review_requested",
            "escalation": "escalation_raised",
        }[kind]
        add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            kind=event_kind,
            actor="agent" if kind != "review" else "candidate",
            # The note (the agent's or the Candidate's words) stays on the queue item (Q38).
            data={"queue_item_id": item_id, "reason": reason},
            now=now,
        )
        row = conn.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
    return dict(row)


# The Escalation reason that hands the conversation to the Recruiters (ADR 0063): while one is
# open the agent does not run, as under a Takeover. Resolving it (dismissed or taken over)
# hands the conversation back.
HOLD_REASON = "unreadable"


def agent_on_hold(conn: sqlite3.Connection, application_id: str) -> bool:
    """True while an ``unreadable`` Escalation is open on the Application (ADR 0063): the
    Recruiters have the conversation and the agent does not run."""
    row = conn.execute(
        "SELECT 1 FROM queue_items WHERE application_id = ? AND kind = 'escalation'"
        " AND status = 'open' AND reason = ?",
        (application_id, HOLD_REASON),
    ).fetchone()
    return row is not None


def hold_for_recruiters(
    conn: sqlite3.Connection,
    application_id: str,
    *,
    note: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Raise the ``unreadable`` Escalation that hands the conversation to the Recruiters (ADR
    0063). One Escalation is open per Application: if another one is already open it becomes
    this one (its reason changes to ``unreadable``, its note stays) and an ``escalation_raised``
    event records the change, so one queue item carries the hold."""
    with transaction(conn):
        existing = conn.execute(
            "SELECT * FROM queue_items WHERE application_id = ? AND kind = 'escalation'"
            " AND status = 'open'",
            (application_id,),
        ).fetchone()
        if existing is None:
            return open_queue_item(
                conn, application_id, "escalation", reason=HOLD_REASON, note=note, now=now
            )
        if existing["reason"] != HOLD_REASON:
            conn.execute(
                "UPDATE queue_items SET reason = ? WHERE id = ?", (HOLD_REASON, existing["id"])
            )
            app = require_application(conn, application_id)
            add_event(
                conn,
                candidate_id=app["candidate_id"],
                application_id=application_id,
                kind="escalation_raised",
                actor="system",
                data={"queue_item_id": existing["id"], "reason": HOLD_REASON},
                now=now,
            )
        row = conn.execute("SELECT * FROM queue_items WHERE id = ?", (existing["id"],)).fetchone()
    return dict(row)


def resolve_hold(
    conn: sqlite3.Connection,
    application_id: str,
    *,
    user_id: str,
    now: datetime | None = None,
) -> bool:
    """Resolve an open ``unreadable`` Escalation as taken over (ADR 0063). True if one was."""
    cur = conn.execute(
        "UPDATE queue_items SET status = 'resolved', resolution = 'takeover', resolved_by = ?,"
        " resolved_at = ? WHERE application_id = ? AND kind = 'escalation' AND status = 'open'"
        " AND reason = ?",
        (user_id, iso(_now(now)), application_id, HOLD_REASON),
    )
    return cur.rowcount == 1


def _resolve_open_item(
    conn: sqlite3.Connection,
    application_id: str,
    kind: str,
    resolution: str,
    user_id: str | None,
    note: str | None,
    now: datetime | None,
) -> None:
    conn.execute(
        "UPDATE queue_items SET status = 'resolved', resolution = ?, resolution_note = ?,"
        " resolved_by = ?, resolved_at = ?"
        " WHERE application_id = ? AND kind = ? AND status = 'open'",
        (resolution, note, user_id, iso(_now(now)), application_id, kind),
    )


def has_open_queue_item(conn: sqlite3.Connection, application_id: str, kind: str) -> bool:
    """True while an item of ``kind`` is open on the Application."""
    row = conn.execute(
        "SELECT 1 FROM queue_items WHERE application_id = ? AND kind = ? AND status = 'open'",
        (application_id, kind),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# The Recruiters' queue (``criba.api.queue``). Scope is the caller's: ``scope`` is an SQL
# fragment over ``applications a`` and ``scope_params`` its parameters (``api.present.scope_sql``).
# None of these opens a transaction; they run in the caller's.
# ---------------------------------------------------------------------------


def find_scoped_queue_item(
    conn: sqlite3.Connection, item_id: str, kind: str, scope: str, scope_params: list[Any]
) -> sqlite3.Row | None:
    """The queue item of ``kind`` with ``item_id`` if its Application is in scope, else None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT qi.* FROM queue_items qi JOIN applications a ON a.id = qi.application_id"
        f" WHERE qi.id = ? AND qi.kind = ? AND {scope}",
        [item_id, kind, *scope_params],
    ).fetchone()
    return row


def resolve_queue_item(
    conn: sqlite3.Connection,
    item_id: str,
    resolution: str,
    user_id: str,
    note: str | None,
    now: datetime,
) -> bool:
    """Resolve the item if it is still open (compare-and-set). False when it was not."""
    cur = conn.execute(
        "UPDATE queue_items SET status = 'resolved', resolution = ?, resolution_note = ?,"
        " resolved_by = ?, resolved_at = ? WHERE id = ? AND status = 'open'",
        (resolution, note, user_id, iso(now), item_id),
    )
    return cur.rowcount == 1


def get_queue_item_row(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row:
    """The queue item row; the caller knows it exists (it has just read or resolved it)."""
    row: sqlite3.Row = conn.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
    return row


def queue_item_status(conn: sqlite3.Connection, item_id: str) -> str:
    """The item's status; the caller knows it exists (it has just read it)."""
    row = conn.execute("SELECT status FROM queue_items WHERE id = ?", (item_id,)).fetchone()
    status: str = row["status"]
    return status


def list_queue_items(
    conn: sqlite3.Connection,
    scope: str,
    scope_params: list[Any],
    *,
    status: str,
    kind: Sequence[str] | None,
    location_id: str | None,
    limit: int,
    offset: int,
) -> list[sqlite3.Row]:
    """One page of queue items in scope: open ones oldest first, resolved ones newest first.

    ``kind`` filters by any of the given kinds. Returns up to ``limit + 1`` rows, so the
    caller can tell whether there is a next page.
    """
    params = scope_params
    where = [scope, "qi.status = ?"]
    params.append(status)
    if kind:
        where.append(f"qi.kind IN ({','.join('?' * len(kind))})")
        params += kind
    if location_id:
        where.append(
            "EXISTS (SELECT 1 FROM application_locations f WHERE f.application_id = a.id"
            " AND f.location_id = ?)"
        )
        params.append(location_id)
    order = (
        "qi.created_at ASC, qi.id ASC" if status == "open" else "qi.resolved_at DESC, qi.id DESC"
    )
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT qi.* FROM queue_items qi JOIN applications a ON a.id = qi.application_id"
        f" WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, limit + 1, offset],
    ).fetchall()
    return rows


def max_queue_item_rowid(conn: sqlite3.Connection) -> int:
    """The queue's high-water mark (0 when empty)."""
    newest: int = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM queue_items").fetchone()[0]
    return newest


_STREAM_BASE = "FROM queue_items qi JOIN applications a ON a.id = qi.application_id"


def queue_items_created_after(
    conn: sqlite3.Connection, rowid: int, scope: str, scope_params: list[Any]
) -> list[sqlite3.Row]:
    """Items in scope inserted after ``rowid``, in insertion order, with their rowid as ``rid``."""
    base = _STREAM_BASE
    rows: list[sqlite3.Row] = conn.execute(
        f"SELECT qi.rowid AS rid, qi.* {base} WHERE qi.rowid > ? AND {scope} ORDER BY qi.rowid",
        [rowid, *scope_params],
    ).fetchall()
    return rows


def queue_items_resolved_since(
    conn: sqlite3.Connection, since: str, scope: str, scope_params: list[Any]
) -> list[sqlite3.Row]:
    """Items in scope resolved at or after ``since`` (an ISO instant), oldest first."""
    base = _STREAM_BASE
    rows: list[sqlite3.Row] = conn.execute(
        f"SELECT qi.* {base} WHERE qi.status = 'resolved' AND qi.resolved_at >= ?"
        f" AND {scope} ORDER BY qi.resolved_at, qi.rowid",
        [since, *scope_params],
    ).fetchall()
    return rows


def direct_rejections_after(
    conn: sqlite3.Connection, event_id: int, scope: str, scope_params: list[Any]
) -> list[sqlite3.Row]:
    """``rejection`` events in scope after ``event_id`` that resolved no Approval item.

    A Rejection made outside the queue (the Application's own page) opens and resolves no
    item. One made from an Approval resolves it, and the resolved items cover that.
    """
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT e.id, e.application_id, e.created_at FROM events e"
        " JOIN applications a ON a.id = e.application_id"
        f" WHERE e.id > ? AND e.kind = 'rejection' AND {scope}"
        " AND NOT EXISTS (SELECT 1 FROM queue_items qi WHERE qi.application_id ="
        " e.application_id AND qi.kind = 'approval' AND qi.resolution = 'rejected'"
        " AND qi.resolved_at = e.created_at) ORDER BY e.id",
        [event_id, *scope_params],
    ).fetchall()
    return rows


def open_queue_kinds(conn: sqlite3.Connection, application_id: str) -> set[str]:
    """The kinds of the Application's open queue items."""
    rows = conn.execute(
        "SELECT kind FROM queue_items WHERE application_id = ? AND status = 'open'",
        (application_id,),
    ).fetchall()
    return {r[0] for r in rows}


def open_queue_item_rows(conn: sqlite3.Connection, application_id: str) -> list[sqlite3.Row]:
    """The Application's open queue items (all columns), oldest first."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT * FROM queue_items WHERE application_id = ? AND status = 'open'"
        " ORDER BY created_at, id",
        (application_id,),
    ).fetchall()
    return rows
