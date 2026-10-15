"""Voice sessions (ADR 0065): the reads and writes behind ``criba.channel.voice_session``.

None of these opens a transaction; ``start_voice_session`` runs its checks and
:func:`insert_voice_session` in one ``BEGIN IMMEDIATE`` of its own. The ends keep their
``ended_at IS NULL`` guard and the conversation-id write its
``elevenlabs_conversation_id IS NULL`` guard in the SQL. :func:`set_voice_session_conversation_id`
lets ``sqlite3.IntegrityError`` from the partial unique index propagate untouched: the caller
turns it into ``VoiceTokenError``. Time values come in as ISO strings computed by the caller.
"""

from __future__ import annotations

import sqlite3


def voice_session_row(conn: sqlite3.Connection, voice_session_id: str) -> sqlite3.Row | None:
    """The session's ``id``, ``conversation_id``, ``candidate_id``, ``country`` and
    ``ended_at``, or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, conversation_id, candidate_id, country, ended_at FROM voice_sessions"
        " WHERE id = ?",
        (voice_session_id,),
    ).fetchone()
    return row


def voice_session_exists(conn: sqlite3.Connection, voice_session_id: str) -> bool:
    """Whether a session with this id exists."""
    known = conn.execute(
        "SELECT 1 FROM voice_sessions WHERE id = ?", (voice_session_id,)
    ).fetchone()
    return known is not None


def voice_session_speaker(conn: sqlite3.Connection, voice_session_id: str) -> sqlite3.Row | None:
    """The session's ``candidate_id`` and ``country``, or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT candidate_id, country FROM voice_sessions WHERE id = ?", (voice_session_id,)
    ).fetchone()
    return row


def mark_voice_session_ended(
    conn: sqlite3.Connection, voice_session_id: str, ended_at: str, seconds: float | None = None
) -> None:
    """Set ``ended_at`` (and ``seconds`` when given) only while ``ended_at`` is NULL."""
    if seconds is None:
        conn.execute(
            "UPDATE voice_sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (ended_at, voice_session_id),
        )
        return
    conn.execute(
        "UPDATE voice_sessions SET ended_at = ?, seconds = ? WHERE id = ? AND ended_at IS NULL",
        (ended_at, seconds, voice_session_id),
    )


def set_voice_session_conversation_id(
    conn: sqlite3.Connection, voice_session_id: str, elevenlabs_conversation_id: str
) -> None:
    """Write the ElevenLabs conversation id only while it is NULL; ``sqlite3.IntegrityError``
    when another session holds it propagates untouched."""
    conn.execute(
        "UPDATE voice_sessions SET elevenlabs_conversation_id = ?"
        " WHERE id = ? AND elevenlabs_conversation_id IS NULL",
        (elevenlabs_conversation_id, voice_session_id),
    )


def insert_voice_session(
    conn: sqlite3.Connection,
    voice_session_id: str,
    conversation_id: str,
    candidate_id: str,
    country: str,
    started_at: str,
) -> None:
    """Insert a new, unended session started at ``started_at``."""
    conn.execute(
        "INSERT INTO voice_sessions (id, conversation_id, candidate_id, country, started_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (voice_session_id, conversation_id, candidate_id, country, started_at),
    )


def recent_voice_session_starts(
    conn: sqlite3.Connection, conversation_id: str, started_after: str
) -> list[sqlite3.Row]:
    """``started_at`` of the conversation's sessions started after ``started_after``, oldest
    first, failed mints (``seconds = 0`` and ended at once) left out."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT started_at FROM voice_sessions WHERE conversation_id = ? AND started_at > ?"
        " AND NOT (seconds IS 0 AND ended_at IS started_at) ORDER BY started_at",
        (conversation_id, started_after),
    ).fetchall()
    return rows


def live_voice_session_count(conn: sqlite3.Connection, started_after: str) -> int:
    """Sessions not ended that started after ``started_after``."""
    row = conn.execute(
        "SELECT COUNT(*) FROM voice_sessions WHERE ended_at IS NULL AND started_at > ?",
        (started_after,),
    ).fetchone()
    return int(row[0])


def voice_sessions_started_since(conn: sqlite3.Connection, since: str) -> list[sqlite3.Row]:
    """``started_at``, ``ended_at`` and ``seconds`` of sessions started at or after ``since``."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT started_at, ended_at, seconds FROM voice_sessions WHERE started_at >= ?",
        (since,),
    ).fetchall()
    return rows


def voice_session_by_conversation(
    conn: sqlite3.Connection, conversation_id: str
) -> sqlite3.Row | None:
    """The voice session (``id``, ``candidate_id``) with this ElevenLabs conversation id."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, candidate_id FROM voice_sessions WHERE elevenlabs_conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return row


def speech_usage_exists(conn: sqlite3.Connection, session_id: str) -> bool:
    """Whether a speech usage row is already on the voice session."""
    row = conn.execute(
        "SELECT 1 FROM usage WHERE kind = 'speech' AND session_id = ?", (session_id,)
    ).fetchone()
    return row is not None


def insert_speech_usage(
    conn: sqlite3.Connection,
    *,
    ts: str,
    environment: str,
    model: str,
    session_id: str,
    candidate_id: str,
    application_id: str | None,
    audio_seconds: float,
    est_cost_usd: float,
) -> None:
    """One live speech (ElevenLabs) usage row for the voice session."""
    conn.execute(
        "INSERT INTO usage (ts, environment, source, kind, provider, model, session_id,"
        " candidate_id, application_id, audio_seconds, est_cost_usd)"
        " VALUES (?, ?, 'live', 'speech', 'elevenlabs', ?, ?, ?, ?, ?, ?)",
        (
            ts,
            environment,
            model,
            session_id,
            candidate_id,
            application_id,
            audio_seconds,
            est_cost_usd,
        ),
    )
