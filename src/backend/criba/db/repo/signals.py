"""Jev router storage (ADR 0051): the usage row of each Jev call and each message's signals.

Only the statements live here, behind ``criba.jev.store`` (which keeps the retention rule and
the shapes it returns). None of these opens a transaction. The aggregate's Location scope
arrives as a SQL fragment on ``applications a`` with its parameters, so ``db/`` does not import
from ``api/``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any


def insert_jev_usage(
    conn: sqlite3.Connection,
    *,
    ts: str,
    environment: str,
    client_id: str,
    candidate_id: str,
    application_id: str,
    stage: str,
    provider: str,
    requested_model: str,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    reconciled_cost_usd: float | None,
    latency_ms: int | None,
    status: str,
    error: str | None,
) -> None:
    """One ledger row for a Jev call (``kind = 'jev'``)."""
    conn.execute(
        "INSERT INTO usage (ts, environment, client_id, candidate_id, application_id, stage,"
        " kind, provider, requested_model, model, input_tokens, output_tokens,"
        " reconciled_cost_usd, latency_ms, status, error)"
        " VALUES (?, ?, ?, ?, ?, ?, 'jev', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ts,
            environment,
            client_id,
            candidate_id,
            application_id,
            stage,
            provider,
            requested_model,
            model,
            input_tokens,
            output_tokens,
            reconciled_cost_usd,
            latency_ms,
            status,
            error,
        ),
    )


def upsert_signal_row(conn: sqlite3.Connection, values: Sequence[Any]) -> None:
    """Store (or replace) one ``message_signals`` row. ``values`` are the 26 columns in the
    order ``message_id, application_id, stage, status, model, policy_version, lang,
    lang_confidence, switch, frustrated, confused, wants_human, abusive, first_message,
    language_set, fired, hint, escalation, created_at, asks, asks_confidence, language_reason,
    unreadable, intent, intent_confidence, malice``."""
    conn.execute(
        "INSERT OR REPLACE INTO message_signals (message_id, application_id, stage, status,"
        " model, policy_version, lang, lang_confidence, switch, frustrated, confused,"
        " wants_human, abusive, first_message, language_set, fired, hint, escalation,"
        " created_at, asks, asks_confidence, language_reason, unreadable, intent,"
        " intent_confidence, malice)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(values),
    )


def latest_event_time_of_kinds(
    conn: sqlite3.Connection, application_id: str, kinds: Sequence[str]
) -> str:
    """The time of the Application's newest event of any of ``kinds`` ('' when there is none)."""
    since: str = conn.execute(
        "SELECT COALESCE(MAX(created_at), '') FROM events WHERE application_id = ?"
        f" AND kind IN ({', '.join('?' for _ in kinds)})",
        (application_id, *kinds),
    ).fetchone()[0]
    return since


def inbound_with_signals_before(
    conn: sqlite3.Connection,
    application_id: str,
    before_message_id: int,
    since: str,
    limit: int,
) -> list[sqlite3.Row]:
    """The Candidate's inbound messages before ``before_message_id`` and after ``since``,
    newest first, at most ``limit``, each with its signals (``body``, ``status``,
    ``unreadable``, ``signals_id``; the last three None for a message with no reading)."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT m.body, s.status, s.unreadable, s.id AS signals_id FROM messages m"
        " LEFT JOIN message_signals s ON s.message_id = m.id"
        " WHERE m.application_id = ? AND m.direction = 'inbound' AND m.id < ?"
        " AND m.created_at > ? ORDER BY m.id DESC LIMIT ?",
        (application_id, before_message_id, since, limit),
    ).fetchall()
    return rows


def signals_with_message_time(conn: sqlite3.Connection, application_id: str) -> list[sqlite3.Row]:
    """The Application's ``message_signals`` rows, oldest first, each with ``message_at``."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT s.*, m.created_at AS message_at FROM message_signals s"
        " JOIN messages m ON m.id = s.message_id WHERE s.application_id = ? ORDER BY s.id",
        (application_id,),
    ).fetchall()
    return rows


def delete_signals_before(conn: sqlite3.Connection, cutoff: str) -> int:
    """Delete the signals created before ``cutoff``; how many rows went. Does not commit."""
    return conn.execute("DELETE FROM message_signals WHERE created_at < ?", (cutoff,)).rowcount


def signal_counts_by_stage(
    conn: sqlite3.Connection, scope_sql: str, params: list[Any], start: str, end: str
) -> list[sqlite3.Row]:
    """Per screening stage over ``[start, end)``: ``messages``, ``unread``, the four
    ``fired`` counters and ``language_switches``. ``scope_sql`` is the caller's Location scope
    on ``applications a``, with ``params``."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT s.stage AS stage,"
        " sum(s.status = 'ok') AS messages,"
        " sum(s.status != 'ok') AS unread,"
        " sum(s.fired LIKE '%\"frustrated\"%') AS frustrated,"
        " sum(s.fired LIKE '%\"confused\"%') AS confused,"
        " sum(s.fired LIKE '%\"wants_human\"%') AS wants_human,"
        " sum(s.fired LIKE '%\"abusive\"%') AS abusive,"
        " sum(s.language_set IS NOT NULL AND s.first_message = 0) AS language_switches"
        " FROM message_signals s JOIN applications a ON a.id = s.application_id"
        f" WHERE {scope_sql} AND s.created_at >= ? AND s.created_at < ?"
        " GROUP BY s.stage ORDER BY s.stage",
        [*params, start, end],
    ).fetchall()
    return rows


# `body` is only tested for emptiness here, in SQL, and never selected. SQLite's `trim` strips
# the listed characters only, as plan Risk 10 accepts: a body of other whitespace (a no-break
# space) still gets an entry, which shows as `not_read`.
READINGS_QUERY = """
SELECT m.id AS message_id, m.created_at AS at,
       s.id AS signal_id, s.stage, s.status, s.model, s.policy_version, s.lang,
       s.lang_confidence, s.switch, s.frustrated, s.confused, s.wants_human, s.abusive,
       s.first_message, s.language_set, s.fired, s.hint, s.escalation,
       s.intent, s.intent_confidence, s.malice
  FROM messages m
  LEFT JOIN message_signals s ON s.message_id = m.id
 WHERE m.application_id = ?
   AND m.direction = 'inbound'
   AND m.sender = 'candidate'
   AND m.content_type = 'text'
   AND m.modality = 'typed'
   AND length(trim(m.body, char(32, 9, 10, 13))) > 0
 ORDER BY m.id
"""


def typed_messages_with_signals(conn: sqlite3.Connection, application_id: str) -> list[sqlite3.Row]:
    """The Candidate's typed text messages with the signals stored for each (the signal
    columns are None for a message with no reading), oldest first. See :data:`READINGS_QUERY`."""
    rows: list[sqlite3.Row] = conn.execute(READINGS_QUERY, (application_id,)).fetchall()
    return rows
