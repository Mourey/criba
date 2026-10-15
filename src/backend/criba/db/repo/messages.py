"""Messages: stored verbatim, with the inbound idempotency check."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Literal

from criba.db.connection import iso, transaction
from criba.db.repo.base import _dumps, _now


def add_message(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    application_id: str | None,
    channel: str,
    direction: Literal["inbound", "outbound"],
    sender: Literal["candidate", "agent", "human", "system"],
    body: str,
    sender_user_id: str | None = None,
    client_message_id: str | None = None,
    content_type: str = "text",
    interactive: dict[str, Any] | None = None,
    reply_option_id: str | None = None,
    template_name: str | None = None,
    media_type: str | None = None,
    run_id: str | None = None,
    modality: Literal["typed", "spoken"] = "typed",
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Store a message verbatim. Returns ``(message, created)``.

    ``modality`` is ``spoken`` for a voice call's turns and the replies to them.

    An inbound message whose ``(channel, client_message_id)`` was already stored is
    not stored again: the original is returned with ``created=False``, so a
    repeated ``message_id`` never triggers a second run.
    """
    ts = iso(_now(now))
    with transaction(conn):
        if client_message_id is not None:
            existing = conn.execute(
                "SELECT * FROM messages WHERE channel = ? AND client_message_id = ?",
                (channel, client_message_id),
            ).fetchone()
            if existing:
                return _message(existing), False
        cur = conn.execute(
            "INSERT INTO messages (application_id, candidate_id, channel, direction, sender,"
            " sender_user_id, client_message_id, content_type, body, interactive,"
            " reply_option_id, template_name, media_type, run_id, modality, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                application_id,
                candidate_id,
                channel,
                direction,
                sender,
                sender_user_id,
                client_message_id,
                content_type,
                body,
                _dumps(interactive) if interactive is not None else None,
                reply_option_id,
                template_name,
                media_type,
                run_id,
                modality,
                ts,
            ),
        )
        if application_id is not None:
            column = "last_inbound_at" if direction == "inbound" else "last_outbound_at"
            conn.execute(
                f"UPDATE applications SET {column} = ?, updated_at = ? WHERE id = ?",
                (ts, ts, application_id),
            )
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _message(row), True


def last_agent_message_at(conn: sqlite3.Connection, application_id: str) -> str | None:
    """When the agent last wrote on the Application (ISO), or None."""
    row = conn.execute(
        "SELECT max(created_at) FROM messages WHERE application_id = ? AND sender = 'agent'",
        (application_id,),
    ).fetchone()
    created_at: str | None = row[0]
    return created_at


def agent_wrote_after(conn: sqlite3.Connection, application_id: str, message_id: int) -> bool:
    """Whether the agent has written on the Application since message ``message_id``."""
    answered = conn.execute(
        "SELECT 1 FROM messages WHERE application_id = ? AND sender = 'agent' AND id > ? LIMIT 1",
        (application_id, message_id),
    ).fetchone()
    return answered is not None


def max_message_id(conn: sqlite3.Connection, application_id: str) -> int:
    """The newest message id of the Application (0 when there is none)."""
    last: int = conn.execute(
        "SELECT coalesce(max(id), 0) FROM messages WHERE application_id = ?", (application_id,)
    ).fetchone()[0]
    return last


def first_agent_message_after(
    conn: sqlite3.Connection, application_id: str, after_id: int
) -> int | None:
    """The id of the first outbound agent message after ``after_id``, or None."""
    first: int | None = conn.execute(
        "SELECT min(id) FROM messages WHERE application_id = ? AND direction = 'outbound'"
        " AND sender = 'agent' AND id > ?",
        (application_id, after_id),
    ).fetchone()[0]
    return first


def takeover_messages(
    conn: sqlite3.Connection, application_id: str, since: str, until: str, limit: int
) -> list[sqlite3.Row]:
    """The Candidate's and the team's messages between ``since`` and ``until`` (inclusive),
    newest first, at most ``limit`` (``sender`` and ``body``)."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT sender, body FROM messages WHERE application_id = ? AND created_at >= ?"
        " AND created_at <= ? AND sender IN ('candidate', 'human') ORDER BY id DESC LIMIT ?",
        (application_id, since, until, limit),
    ).fetchall()
    return rows


def max_candidate_message_id(conn: sqlite3.Connection, candidate_id: str, channel: str) -> int:
    """The newest message id of the Candidate on ``channel`` (0 when there is none)."""
    row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM messages WHERE candidate_id = ? AND channel = ?",
        (candidate_id, channel),
    ).fetchone()
    return int(row[0])


def recent_interactive_messages(
    conn: sqlite3.Connection, candidate_id: str, before_id: int
) -> list[sqlite3.Row]:
    """The Candidate's last five outbound messages with buttons before ``before_id``, newest
    first (``id`` and the raw ``interactive`` JSON)."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT id, interactive FROM messages WHERE candidate_id = ? AND direction = 'outbound'"
        " AND interactive IS NOT NULL AND id < ? ORDER BY id DESC LIMIT 5",
        (candidate_id, before_id),
    ).fetchall()
    return rows


def candidate_messages_after(
    conn: sqlite3.Connection, candidate_id: str, channel: str, after_id: int, limit: int
) -> list[sqlite3.Row]:
    """The Candidate's messages on ``channel`` after ``after_id``, in order, at most ``limit``
    (raw rows: ``interactive`` is still JSON text)."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT * FROM messages WHERE candidate_id = ? AND channel = ? AND id > ?"
        " ORDER BY id LIMIT ?",
        (candidate_id, channel, after_id, limit),
    ).fetchall()
    return rows


def candidate_message_count_after(
    conn: sqlite3.Connection,
    application_id: str,
    *,
    after_id: int | None = None,
    after_ts: str | None = None,
) -> int:
    """How many Candidate messages the Application received after the message ``after_id``,
    else after the time ``after_ts`` (an ISO timestamp)."""
    where = "id > ?" if after_id is not None else "created_at > ?"
    value: int | str = after_id if after_id is not None else (after_ts or "")
    row = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE application_id = ?"
        f" AND direction = 'inbound' AND sender = 'candidate' AND {where}",
        (application_id, value),
    ).fetchone()
    return int(row[0])


def has_outbound_message(conn: sqlite3.Connection, application_id: str) -> bool:
    """Whether any message has gone out on the Application."""
    row = conn.execute(
        "SELECT 1 FROM messages WHERE application_id = ? AND direction = 'outbound' LIMIT 1",
        (application_id,),
    ).fetchone()
    return row is not None


def client_message_seen(conn: sqlite3.Connection, channel: str, client_message_id: str) -> bool:
    """Whether a message with this ``(channel, client_message_id)`` is stored."""
    seen = conn.execute(
        "SELECT 1 FROM messages WHERE channel = ? AND client_message_id = ?",
        (channel, client_message_id),
    ).fetchone()
    return seen is not None


def stored_message_body(conn: sqlite3.Connection, channel: str, client_message_id: str) -> str:
    """The body of the stored message ``(channel, client_message_id)``; the caller knows it is
    stored (a missing one raises ``TypeError``, as indexing None does)."""
    row = conn.execute(
        "SELECT body FROM messages WHERE channel = ? AND client_message_id = ?",
        (channel, client_message_id),
    ).fetchone()
    body: str = row["body"]
    return body


def _message(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    if out["interactive"] is not None:
        out["interactive"] = json.loads(out["interactive"])
    return out


def list_messages(
    conn: sqlite3.Connection,
    application_id: str,
    *,
    after_id: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Messages in order; ``after_id`` is the read cursor."""
    sql = "SELECT * FROM messages WHERE application_id = ? AND id > ? ORDER BY id"
    params: tuple[Any, ...] = (application_id, after_id)
    if limit is not None:
        sql += " LIMIT ?"
        params = (*params, limit)
    return [_message(r) for r in conn.execute(sql, params).fetchall()]


def link_message_to_application(
    conn: sqlite3.Connection,
    channel: str,
    client_message_id: str,
    candidate_id: str,
    application_id: str,
) -> bool:
    """Put the Candidate's stored message with this ``client_message_id`` on ``application_id``
    when it has no Application yet (the spoken disclosure of a call that started before the
    Application existed). Only while ``application_id IS NULL`` and only for this Candidate, so
    a message that already belongs to an Application never moves and another Candidate's never
    links. True when it was linked."""
    cur = conn.execute(
        "UPDATE messages SET application_id = ? WHERE channel = ? AND client_message_id = ?"
        " AND candidate_id = ? AND application_id IS NULL",
        (application_id, channel, client_message_id, candidate_id),
    )
    return cur.rowcount == 1


def transcript_since(
    conn: sqlite3.Connection, application_id: str, since: str
) -> list[sqlite3.Row]:
    """The Candidate's and the team's messages from ``since`` on, oldest first
    (``sender`` and ``body``)."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT sender, body FROM messages WHERE application_id = ?"
        " AND sender IN ('candidate', 'human') AND created_at >= ? ORDER BY id",
        (application_id, since),
    ).fetchall()
    return rows


def message_created_at(
    conn: sqlite3.Connection, application_id: str, message_id: int
) -> str | None:
    """When the Application's message ``message_id`` was stored, or None for no such message."""
    row = conn.execute(
        "SELECT created_at FROM messages WHERE id = ? AND application_id = ?",
        (message_id, application_id),
    ).fetchone()
    return None if row is None else str(row["created_at"])


def max_message_id_before(conn: sqlite3.Connection, application_id: str, created_at: str) -> int:
    """The newest message id of the Application stored before ``created_at`` (0 for none)."""
    before: int = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM messages WHERE application_id = ? AND created_at < ?",
        (application_id, created_at),
    ).fetchone()[0]
    return before


def inbound_by_client_message_id(
    conn: sqlite3.Connection, channel: str, client_message_id: str
) -> sqlite3.Row | None:
    """The stored inbound message on ``channel`` with this client message id
    (``id``, ``candidate_id``, ``application_id``, ``body``), or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, candidate_id, application_id, body FROM messages"
        " WHERE channel = ? AND client_message_id = ? AND direction = 'inbound'",
        (channel, client_message_id),
    ).fetchone()
    return row


def stored_voice_reply(
    conn: sqlite3.Connection, candidate_id: str, channel: str, inbound_id: int
) -> sqlite3.Row | None:
    """The reply to an inbound row (R5.3, risk 7): the Candidate's first outbound ``agent`` or
    ``system`` message on ``channel`` after it and before their next inbound row, never the
    stored spoken disclosure (``client_message_id`` starting ``vd_``). ``id`` and ``body``."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, body FROM messages"
        " WHERE candidate_id = ? AND channel = ? AND direction = 'outbound'"
        " AND sender IN ('agent', 'system') AND id > ?"
        " AND (client_message_id IS NULL OR substr(client_message_id, 1, 3) != 'vd_')"
        " AND NOT EXISTS (SELECT 1 FROM messages later WHERE later.candidate_id = ?"
        " AND later.channel = ? AND later.direction = 'inbound' AND later.id > ?"
        " AND later.id < messages.id)"
        " ORDER BY id LIMIT 1",
        (candidate_id, channel, inbound_id, candidate_id, channel, inbound_id),
    ).fetchone()
    return row


def application_of_latest_message_with_prefix(
    conn: sqlite3.Connection, candidate_id: str, prefix: str
) -> str | None:
    """The Application of the Candidate's newest message whose client message id starts with
    ``prefix`` and that belongs to an Application, or None."""
    row = conn.execute(
        "SELECT application_id FROM messages WHERE candidate_id = ?"
        " AND substr(client_message_id, 1, ?) = ? AND application_id IS NOT NULL"
        " ORDER BY id DESC LIMIT 1",
        (candidate_id, len(prefix), prefix),
    ).fetchone()
    return None if row is None else str(row["application_id"])


def candidate_channel_messages(
    conn: sqlite3.Connection, candidate_id: str, channel: str
) -> list[sqlite3.Row]:
    """Every message of the Candidate on ``channel`` (all columns), oldest first."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT * FROM messages WHERE candidate_id = ? AND channel = ? ORDER BY id",
        (candidate_id, channel),
    ).fetchall()
    return rows


def has_earlier_inbound_text(
    conn: sqlite3.Connection, application_id: str, message_id: int
) -> bool:
    """Whether the Application has an inbound text message before ``message_id``."""
    row = conn.execute(
        "SELECT 1 FROM messages WHERE application_id = ? AND direction = 'inbound'"
        " AND content_type = 'text' AND id < ? LIMIT 1",
        (application_id, message_id),
    ).fetchone()
    return row is not None


def recent_nonempty_messages(
    conn: sqlite3.Connection, application_id: str, before_id: int, limit: int
) -> list[sqlite3.Row]:
    """The Application's last ``limit`` messages with a body before ``before_id`` (``sender``,
    ``body``), newest first."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT sender, body FROM messages WHERE application_id = ? AND id < ?"
        " AND body != '' ORDER BY id DESC LIMIT ?",
        (application_id, before_id, limit),
    ).fetchall()
    return rows


def message_by_client_id(
    conn: sqlite3.Connection, channel: str, client_message_id: str
) -> sqlite3.Row | None:
    """The stored message ``(channel, client_message_id)`` (all columns), or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM messages WHERE channel = ? AND client_message_id = ?",
        (channel, client_message_id),
    ).fetchone()
    return row


def outbound_message_bodies(conn: sqlite3.Connection, application_id: str) -> list[str]:
    """The bodies of the Application's outbound messages, in no promised order."""
    rows = conn.execute(
        "SELECT body FROM messages WHERE application_id = ? AND direction = 'outbound'",
        (application_id,),
    ).fetchall()
    return [row["body"] for row in rows]


def interactive_outbound_message(
    conn: sqlite3.Connection, message_id: int, candidate_id: str
) -> sqlite3.Row | None:
    """The Candidate's outbound message ``message_id`` if it carries ``interactive`` options
    (all columns), or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM messages WHERE id = ? AND candidate_id = ? AND direction = 'outbound'"
        " AND interactive IS NOT NULL",
        (message_id, candidate_id),
    ).fetchone()
    return row


def agent_message_exists_after(conn: sqlite3.Connection, candidate_id: str, after_id: int) -> bool:
    """Whether the Candidate has an outbound ``agent`` message after ``after_id``."""
    row = conn.execute(
        "SELECT 1 FROM messages WHERE candidate_id = ? AND direction = 'outbound'"
        " AND sender = 'agent' AND id > ? LIMIT 1",
        (candidate_id, after_id),
    ).fetchone()
    return row is not None
