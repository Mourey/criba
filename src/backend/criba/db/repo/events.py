"""Events: the Application timeline, its read cursors, and Knockout message links."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from criba.db.connection import iso, transaction
from criba.db.repo.base import Actor, _dumps, _now


def add_event(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    application_id: str | None,
    kind: str,
    actor: Actor,
    user_id: str | None = None,
    data: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> int:
    """Append an event to the timeline and return its id (a read cursor)."""
    cur = conn.execute(
        "INSERT INTO events (application_id, candidate_id, kind, actor, user_id, data, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (application_id, candidate_id, kind, actor, user_id, _dumps(data or {}), iso(_now(now))),
    )
    return int(cur.lastrowid or 0)


def list_events(
    conn: sqlite3.Connection, application_id: str, *, after_id: int = 0
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM events WHERE application_id = ? AND id > ? ORDER BY id",
        (application_id, after_id),
    ).fetchall()
    return [{**dict(r), "data": json.loads(r["data"])} for r in rows]


def last_event_id(conn: sqlite3.Connection, application_id: str) -> int:
    """The Application's newest event id, or 0: a cursor for "events written since"."""
    row = conn.execute(
        "SELECT max(id) FROM events WHERE application_id = ?", (application_id,)
    ).fetchone()
    return int(row[0] or 0)


def last_inbound_message_id(conn: sqlite3.Connection, application_id: str) -> int | None:
    """The Candidate's latest message on the Application: what an answer event references."""
    row = conn.execute(
        "SELECT max(id) FROM messages WHERE application_id = ? AND direction = 'inbound'",
        (application_id,),
    ).fetchone()
    return row[0]


def last_inbound_message(conn: sqlite3.Connection, application_id: str) -> dict[str, Any] | None:
    """The Candidate's latest message on the Application (``id`` and ``body``), or None.

    Its ``body`` is the Candidate's own words for ``zone_input`` and
    ``driver_license_input``: never the agent's structured value (ADR 0042).
    """
    row = conn.execute(
        "SELECT id, body FROM messages WHERE application_id = ? AND direction = 'inbound'"
        " ORDER BY id DESC LIMIT 1",
        (application_id,),
    ).fetchone()
    return None if row is None else {"id": row["id"], "body": row["body"]}


def last_typed_inbound_text(conn: sqlite3.Connection, application_id: str) -> str | None:
    """The Candidate's latest typed message on the Application (not a button tap), or None.

    A tap's stored text is the option's label, so the Candidate's own words for a place they
    named are in the message before it (ADR 0068).
    """
    row = conn.execute(
        "SELECT body FROM messages WHERE application_id = ? AND direction = 'inbound'"
        " AND content_type = 'text' AND reply_option_id IS NULL ORDER BY id DESC LIMIT 1",
        (application_id,),
    ).fetchone()
    return None if row is None else row["body"]


def last_funnel_offer(
    conn: sqlite3.Connection, application_id: str, *, after_event_id: int = 0
) -> dict[str, Any] | None:
    """The data of the Application's newest Zone funnel event (``answer_ambiguous`` with
    ``funnel: true``, ADR 0068) written after ``after_event_id``, or None."""
    row = conn.execute(
        "SELECT data FROM events WHERE application_id = ? AND id > ?"
        " AND kind = 'answer_ambiguous' AND json_extract(data, '$.funnel') = 1"
        " ORDER BY id DESC LIMIT 1",
        (application_id, after_event_id),
    ).fetchone()
    return None if row is None else json.loads(row["data"])


_ANSWER_KINDS = "('answer_recorded', 'answer_invalid', 'answer_ambiguous')"


def answered_message(conn: sqlite3.Connection, application_id: str, message_id: int) -> bool:
    """Whether an answer event (recorded, a miss, or options offered) references the Candidate
    message ``message_id``: something was done with it at the pending question (ADR 0076)."""
    row = conn.execute(
        f"SELECT 1 FROM events WHERE application_id = ? AND kind IN {_ANSWER_KINDS}"
        " AND json_extract(data, '$.message_id') = ? LIMIT 1",
        (application_id, message_id),
    ).fetchone()
    return row is not None


def typed_messages_since_answer(conn: sqlite3.Connection, application_id: str) -> int:
    """How many typed Candidate messages (not taps) the Application received after the newest
    message an answer event references: the messages in a row that got nothing done at the
    pending question, the one being read included (ADR 0076)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE application_id = ? AND direction = 'inbound'"
        " AND sender = 'candidate' AND content_type = 'text' AND reply_option_id IS NULL"
        " AND id > coalesce((SELECT max(json_extract(data, '$.message_id')) FROM events"
        f"   WHERE application_id = ? AND kind IN {_ANSWER_KINDS}), 0)",
        (application_id, application_id),
    ).fetchone()
    return int(row[0])


def last_inbound_is_spoken(conn: sqlite3.Connection, application_id: str) -> bool:
    """Whether the Candidate's latest message on the Application was spoken (a voice call's
    turn): the run a tool call belongs to is then a spoken turn."""
    row = conn.execute(
        "SELECT modality FROM messages WHERE application_id = ? AND direction = 'inbound'"
        " ORDER BY id DESC LIMIT 1",
        (application_id,),
    ).fetchone()
    return row is not None and row["modality"] == "spoken"


def link_knockout_message(
    conn: sqlite3.Connection, application_id: str, *, after_event_id: int, message_id: int
) -> int:
    """Point the Knockout written since ``after_event_id`` at the agent message carrying it.

    The ``knockout`` event is written by the tool during the run, before the agent's reply
    exists; the reply's id is added once it is sent. The text itself never enters the
    event (Q38). Returns the number of events linked.
    """
    with transaction(conn):
        cur = conn.execute(
            "UPDATE events SET data = json_set(data, '$.message_id', ?)"
            " WHERE application_id = ? AND kind = 'knockout' AND id > ?"
            " AND json_type(data, '$.message_id') IS NULL",
            (message_id, application_id, after_event_id),
        )
    return cur.rowcount


def max_event_id(conn: sqlite3.Connection) -> int:
    """The newest event id across all Applications (0 when there is none)."""
    newest: int = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    return newest


# ---------------------------------------------------------------------------
# Reads behind the screening tools (``criba.tools.application``). Rows come back raw: the
# caller decodes ``data``.
# ---------------------------------------------------------------------------


def latest_event(conn: sqlite3.Connection, application_id: str, kind: str) -> sqlite3.Row | None:
    """The Application's newest ``kind`` event (``id``, ``data``, ``created_at``), or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, data, created_at FROM events WHERE application_id = ? AND kind = ?"
        " ORDER BY id DESC LIMIT 1",
        (application_id, kind),
    ).fetchone()
    return row


def has_event_after(
    conn: sqlite3.Connection, application_id: str, kind: str, after_id: int
) -> bool:
    """Whether the Application has a ``kind`` event newer than ``after_id``."""
    row = conn.execute(
        "SELECT 1 FROM events WHERE application_id = ? AND kind = ? AND id > ? LIMIT 1",
        (application_id, kind, after_id),
    ).fetchone()
    return row is not None


def latest_tone_hint(conn: sqlite3.Connection, application_id: str) -> sqlite3.Row | None:
    """The Application's newest ``tone_hint`` event (``data`` only), or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT data FROM events WHERE application_id = ? AND kind = 'tone_hint'"
        " ORDER BY id DESC LIMIT 1",
        (application_id,),
    ).fetchone()
    return row


def takeover_started_before(
    conn: sqlite3.Connection, application_id: str, event_id: int
) -> sqlite3.Row | None:
    """The newest ``takeover_started`` event before ``event_id`` (``created_at``), or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT created_at FROM events WHERE application_id = ? AND kind = 'takeover_started'"
        " AND id < ? ORDER BY id DESC LIMIT 1",
        (application_id, event_id),
    ).fetchone()
    return row


def live_zone_choice(conn: sqlite3.Connection, application_id: str) -> sqlite3.Row | None:
    """The newest Zone ``answer_ambiguous`` event (``data``) written after the last Reopen and
    the last recorded Zone, or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT data FROM events WHERE application_id = ? AND kind = 'answer_ambiguous'"
        " AND json_extract(data, '$.field') = 'zone'"
        " AND id > coalesce((SELECT max(id) FROM events WHERE application_id = ?"
        "   AND (kind = 'reopened' OR (kind = 'answer_recorded'"
        "        AND json_extract(data, '$.field') = 'zone'))), 0)"
        " ORDER BY id DESC LIMIT 1",
        (application_id, application_id),
    ).fetchone()
    return row


def counted_unreadable(conn: sqlite3.Connection, application_id: str, message_id: int) -> bool:
    """Whether an ``answer_invalid`` event counted ``message_id`` as unreadable (ADR 0063)."""
    row = conn.execute(
        "SELECT 1 FROM events WHERE application_id = ? AND kind = 'answer_invalid'"
        " AND json_extract(data, '$.message_id') = ?"
        " AND json_extract(data, '$.reason') = 'unreadable' LIMIT 1",
        (application_id, message_id),
    ).fetchone()
    return row is not None


def event_created_at(conn: sqlite3.Connection, application_id: str, event_id: int) -> str | None:
    """When the Application's event ``event_id`` was stored, or None for no such event."""
    row = conn.execute(
        "SELECT created_at FROM events WHERE id = ? AND application_id = ?",
        (event_id, application_id),
    ).fetchone()
    return None if row is None else str(row["created_at"])


def max_event_id_before(conn: sqlite3.Connection, application_id: str, created_at: str) -> int:
    """The newest event id of the Application stored before ``created_at`` (0 for none)."""
    before: int = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM events WHERE application_id = ? AND created_at < ?",
        (application_id, created_at),
    ).fetchone()[0]
    return before


def max_application_event_id(conn: sqlite3.Connection, application_id: str) -> int:
    """The newest event id of the Application (0 when there is none)."""
    newest: int = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM events WHERE application_id = ?", (application_id,)
    ).fetchone()[0]
    return newest


def slots_offered_since_booking(conn: sqlite3.Connection, application_id: str) -> list[sqlite3.Row]:
    """The ``slots_offered`` events (``data``) after the Application's latest booking or
    reschedule event (all of them when there is none), oldest first."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT data FROM events WHERE application_id = ? AND kind = 'slots_offered'"
        " AND id > coalesce((SELECT max(id) FROM events WHERE application_id = ?"
        "                    AND kind IN ('interview_booked', 'interview_rescheduled')), 0)",
        (application_id, application_id),
    ).fetchall()
    return rows


def ambiguity_events_since_reopen(
    conn: sqlite3.Connection, application_id: str
) -> list[sqlite3.Row]:
    """The ``answer_ambiguous`` events (``id``, ``data``) after the Application's latest
    ``reopened`` event, newest first."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT id, data FROM events WHERE application_id = ? AND kind = 'answer_ambiguous'"
        " AND id > coalesce((SELECT max(id) FROM events WHERE application_id = ?"
        "                    AND kind = 'reopened'), 0)"
        " ORDER BY id DESC",
        (application_id, application_id),
    ).fetchall()
    return rows


def answer_recorded_after(
    conn: sqlite3.Connection, application_id: str, field: str, event_id: int
) -> bool:
    """Whether an ``answer_recorded`` event for ``field`` came after event ``event_id``."""
    row = conn.execute(
        "SELECT 1 FROM events WHERE application_id = ? AND kind = 'answer_recorded'"
        " AND json_extract(data, '$.field') = ? AND id > ? LIMIT 1",
        (application_id, field, event_id),
    ).fetchone()
    return row is not None


def last_answer_or_reopen_events(
    conn: sqlite3.Connection, application_id: str
) -> list[sqlite3.Row]:
    """The Application's last two ``reopened`` or ``answer_recorded`` events (``kind``,
    ``data``), newest first."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT kind, data FROM events WHERE application_id = ?"
        " AND kind IN ('reopened', 'answer_recorded') ORDER BY id DESC LIMIT 2",
        (application_id,),
    ).fetchall()
    return rows


def count_invalid_answers(
    conn: sqlite3.Connection, application_id: str, fields: Sequence[str]
) -> int:
    """How many ``answer_invalid`` events the Application has for any of ``fields``."""
    marks = ",".join("?" for _ in fields)
    row = conn.execute(
        "SELECT count(*) FROM events WHERE application_id = ? AND kind = 'answer_invalid'"
        f" AND json_extract(data, '$.field') IN ({marks})",
        (application_id, *fields),
    ).fetchone()
    return int(row[0])
