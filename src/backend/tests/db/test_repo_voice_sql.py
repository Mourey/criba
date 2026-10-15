"""Direct tests for the repo functions that took over the SQL of ``channel/voice_session.py``
and ``channel/webchat.py``: the session insert, the guarded ends, the write-once ElevenLabs
conversation id (the partial unique index raising out of the repo untouched, and the caller
still answering ``VoiceTokenError``), the rate and cap reads, and the identity insert. The
channel tests cover the plain selects through their callers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from criba.channel.voice_session import VoiceTokenError, set_elevenlabs_conversation_id
from criba.db import repo
from criba.db.connection import iso
from criba.db.seed import CLIENT_ID

T0 = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)


def candidate(conn: sqlite3.Connection, external_id: str, phone: str | None = None) -> str:
    found, _ = repo.get_or_create_candidate(
        conn,
        client_id=CLIENT_ID,
        channel="voice",
        external_id=external_id,
        phone=phone,
        country="ES",
        now=T0,
    )
    candidate_id: str = found["id"]
    return candidate_id


def session(conn: sqlite3.Connection, session_id: str, conversation: str, at: datetime) -> str:
    candidate_id = candidate(conn, conversation)
    repo.insert_voice_session(conn, session_id, conversation, candidate_id, "ES", iso(at))
    return candidate_id


def test_insert_then_read_a_session(seeded: sqlite3.Connection) -> None:
    candidate_id = session(seeded, "vs_a", "wv_a", T0)
    row = repo.voice_session_row(seeded, "vs_a")
    assert row is not None
    assert dict(row) == {
        "id": "vs_a",
        "conversation_id": "wv_a",
        "candidate_id": candidate_id,
        "country": "ES",
        "ended_at": None,
    }
    speaker = repo.voice_session_speaker(seeded, "vs_a")
    assert speaker is not None
    assert (speaker["candidate_id"], speaker["country"]) == (candidate_id, "ES")
    assert repo.voice_session_exists(seeded, "vs_a")
    assert not repo.voice_session_exists(seeded, "vs_missing")
    assert repo.voice_session_row(seeded, "vs_missing") is None
    assert repo.voice_session_speaker(seeded, "vs_missing") is None


def test_an_end_writes_only_while_ended_at_is_null(seeded: sqlite3.Connection) -> None:
    session(seeded, "vs_a", "wv_a", T0)
    session(seeded, "vs_b", "wv_b", T0)
    first, later = iso(T0 + timedelta(minutes=1)), iso(T0 + timedelta(minutes=5))

    repo.mark_voice_session_ended(seeded, "vs_a", first)
    repo.mark_voice_session_ended(seeded, "vs_a", later, 42.0)
    row = seeded.execute(
        "SELECT ended_at, seconds FROM voice_sessions WHERE id = 'vs_a'"
    ).fetchone()
    assert (row["ended_at"], row["seconds"]) == (first, None)

    repo.mark_voice_session_ended(seeded, "vs_b", first, 0)
    repo.mark_voice_session_ended(seeded, "vs_b", later)
    row = seeded.execute(
        "SELECT ended_at, seconds FROM voice_sessions WHERE id = 'vs_b'"
    ).fetchone()
    assert (row["ended_at"], row["seconds"]) == (first, 0.0)

    repo.mark_voice_session_ended(seeded, "vs_missing", first)  # an unknown id changes nothing


def test_the_conversation_id_is_written_once(seeded: sqlite3.Connection) -> None:
    session(seeded, "vs_a", "wv_a", T0)
    repo.set_voice_session_conversation_id(seeded, "vs_a", "el_1")
    repo.set_voice_session_conversation_id(seeded, "vs_a", "el_2")
    stored = seeded.execute(
        "SELECT elevenlabs_conversation_id FROM voice_sessions WHERE id = 'vs_a'"
    ).fetchone()[0]
    assert stored == "el_1"


def test_a_conversation_id_on_another_session_raises_integrity_error(
    seeded: sqlite3.Connection,
) -> None:
    session(seeded, "vs_a", "wv_a", T0)
    session(seeded, "vs_b", "wv_b", T0)
    repo.set_voice_session_conversation_id(seeded, "vs_a", "el_1")

    # db/ does not translate the unique-index clash.
    with pytest.raises(sqlite3.IntegrityError):
        repo.set_voice_session_conversation_id(seeded, "vs_b", "el_1")
    # The caller still turns it into its one refusal; both rows stay as they were.
    with pytest.raises(VoiceTokenError):
        set_elevenlabs_conversation_id(seeded, "vs_b", "el_1")
    rows = seeded.execute(
        "SELECT id, elevenlabs_conversation_id FROM voice_sessions ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("vs_a", "el_1"), ("vs_b", None)]


def test_rate_and_cap_reads(seeded: sqlite3.Connection) -> None:
    session(seeded, "vs_old", "wv_a", T0 - timedelta(hours=1))
    session(seeded, "vs_1", "wv_a", T0)
    session(seeded, "vs_2", "wv_a", T0 + timedelta(minutes=1))
    session(seeded, "vs_failed", "wv_a", T0 + timedelta(minutes=2))
    session(seeded, "vs_other", "wv_b", T0 + timedelta(minutes=3))
    # A failed mint: ended at once with seconds = 0, so not a started session for the rate.
    started = iso(T0 + timedelta(minutes=2))
    repo.mark_voice_session_ended(seeded, "vs_failed", started, 0)
    repo.mark_voice_session_ended(seeded, "vs_old", iso(T0 - timedelta(minutes=30)), 90.0)

    since = iso(T0 - timedelta(minutes=10))
    starts = repo.recent_voice_session_starts(seeded, "wv_a", since)
    assert [r["started_at"] for r in starts] == [iso(T0), iso(T0 + timedelta(minutes=1))]

    assert repo.live_voice_session_count(seeded, since) == 3  # vs_1, vs_2, vs_other
    assert repo.live_voice_session_count(seeded, iso(T0 + timedelta(minutes=1))) == 1

    day = repo.voice_sessions_started_since(seeded, iso(T0 - timedelta(hours=2)))
    assert len(day) == 5
    assert {r["seconds"] for r in day} == {None, 0.0, 90.0}
    assert len(repo.voice_sessions_started_since(seeded, iso(T0))) == 4


def test_add_candidate_identity_and_the_lookups(seeded: sqlite3.Connection) -> None:
    candidate_id = candidate(seeded, "wv_first", phone="+34600111222")
    found = repo.first_candidate_by_phone(seeded, "+34600111222", CLIENT_ID)
    assert found is not None and found["id"] == candidate_id
    assert repo.first_candidate_by_phone(seeded, "+34600111222", "client_other") is None

    repo.add_candidate_identity(
        seeded, candidate_id, "webchat", "wc_second", iso(T0), "simulator", "persona_1"
    )
    row = seeded.execute(
        "SELECT candidate_id, channel, created_at, client, persona_id FROM candidate_identities"
        " WHERE external_id = 'wc_second'"
    ).fetchone()
    assert tuple(row) == (candidate_id, "webchat", iso(T0), "simulator", "persona_1")

    identity = repo.identity_candidate_row(seeded, "webchat", "wc_second")
    assert identity is not None and identity["candidate_id"] == candidate_id
    assert repo.identity_candidate_row(seeded, "voice", "wc_second") is None
    assert repo.identity_exists(seeded, "webchat", "wc_second")
    assert not repo.identity_exists(seeded, "voice", "wc_second")

    # The web chat identity first, whatever order the channels come in.
    repo.add_candidate_identity(seeded, candidate_id, "voice", "wc_second", iso(T0), None, None)
    both = repo.conversation_identity(seeded, ("voice", "webchat"), "wc_second", "webchat")
    assert both is not None and both["channel"] == "webchat"
    voice = repo.conversation_identity(seeded, ("webchat", "voice"), "wc_second", "voice")
    assert voice is not None and voice["channel"] == "voice"
    assert repo.conversation_identity(seeded, ("webchat", "voice"), "nope", "webchat") is None

    assert repo.candidate_country(seeded, candidate_id) == "ES"
    assert repo.candidate_country(seeded, "cand_missing") is None
    assert repo.candidate_language(seeded, "cand_missing") is None


def test_link_message_to_application_only_fills_a_missing_application(
    seeded: sqlite3.Connection,
) -> None:
    candidate_id = candidate(seeded, "wv_link")
    stranger_id = candidate(seeded, "wv_stranger")
    first = repo.start_application(seeded, candidate_id, "voice", now=T0)
    stored, _ = repo.add_message(
        seeded,
        candidate_id=candidate_id,
        application_id=None,
        channel="voice",
        direction="outbound",
        sender="agent",
        body="Hola.",
        client_message_id="vd_vs_link",
        modality="spoken",
        now=T0,
    )
    assert stored["application_id"] is None

    def link(candidate: str, application_id: str, channel: str = "voice") -> bool:
        return repo.link_message_to_application(
            seeded, channel, "vd_vs_link", candidate, application_id
        )

    def linked_to() -> str | None:
        found: str | None = seeded.execute(
            "SELECT application_id FROM messages WHERE id = ?", (stored["id"],)
        ).fetchone()[0]
        return found

    # Another Candidate's id, another channel or an unknown message id link nothing.
    assert link(stranger_id, first["id"]) is False
    assert link(candidate_id, first["id"], channel="webchat") is False
    assert (
        repo.link_message_to_application(seeded, "voice", "vd_missing", candidate_id, first["id"])
        is False
    )
    assert linked_to() is None

    assert link(candidate_id, first["id"]) is True
    assert linked_to() == first["id"]

    # A message that already belongs to an Application never moves.
    assert link(candidate_id, "app_other") is False
    assert linked_to() == first["id"]
