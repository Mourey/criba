"""The voice token, its verification, the session end and the ElevenLabs conversation id
(spec voice-agent session-start SE7, SE8, SE9, R7.4). Rows are planted through SQL; the
signing key is a low-entropy test word."""

from __future__ import annotations

import base64
import hashlib
import hmac
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from criba.channel.voice_session import (
    VOICE_TOKEN_TTL_S,
    VoiceSessionEnded,
    VoiceSessionRef,
    VoiceTokenError,
    end_voice_session,
    make_voice_token,
    set_elevenlabs_conversation_id,
    verify_voice_token,
)
from criba.db import repo
from criba.db.connection import iso

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
SECRET = b"voice-secret-for-tests"
CONV = "wc_" + "a" * 32
OTHER_CONV = "wc_" + "b" * 32
SESSION = "vs_" + "1" * 32
ARABIC_ONE = chr(0x0661)  # a Unicode digit int() would accept


def _candidate(conn: sqlite3.Connection, conversation_id: str) -> str:
    candidate, _ = repo.get_or_create_candidate(
        conn,
        client_id="grupo_sazon",
        channel="webchat",
        external_id=conversation_id,
        country="ES",
        language="es-ES",
        now=NOW,
    )
    return str(candidate["id"])


def _session(
    conn: sqlite3.Connection,
    session_id: str = SESSION,
    conversation_id: str = CONV,
    *,
    started_at: datetime = NOW,
) -> str:
    candidate_id = _candidate(conn, conversation_id)
    conn.execute(
        "INSERT INTO voice_sessions (id, conversation_id, candidate_id, country, started_at)"
        " VALUES (?, ?, ?, 'ES', ?)",
        (session_id, conversation_id, candidate_id, iso(started_at)),
    )
    return candidate_id


def _row(conn: sqlite3.Connection, session_id: str = SESSION) -> dict[str, Any]:
    return dict(conn.execute("SELECT * FROM voice_sessions WHERE id = ?", (session_id,)).fetchone())


def _token(
    conversation_id: str = CONV, session_id: str = SESSION, *, secret: bytes = SECRET
) -> str:
    return make_voice_token(
        conversation_id,
        session_id,
        NOW + timedelta(seconds=VOICE_TOKEN_TTL_S),
        secret=secret,
    )


def _signed(payload: str, secret: bytes = SECRET) -> str:
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{payload}.{base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')}"


def _take_over(conn: sqlite3.Connection, candidate_id: str) -> None:
    app = repo.start_application(conn, candidate_id, "webchat", now=NOW)
    conn.execute(
        "INSERT OR IGNORE INTO users (id, client_id, email, name, role, password_hash,"
        " created_at) VALUES ('u1', 'grupo_sazon', 'r@example.com', 'R', 'recruiter', 'x',"
        " '2026-10-09')"
    )
    conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'u1' WHERE id = ?",
        (app["id"],),
    )


def test_round_trip(conn: sqlite3.Connection) -> None:
    candidate_id = _session(conn)
    app = repo.start_application(conn, candidate_id, "webchat", now=NOW)
    token = _token()

    parts = token.split(".")
    assert len(parts) == 6
    assert parts[:4] == ["v1", "voice", CONV, SESSION]
    assert int(parts[4]) - NOW.timestamp() <= 1800
    assert int(parts[4]) - NOW.timestamp() == VOICE_TOKEN_TTL_S == 1800
    assert "=" not in parts[5]
    assert app["id"] not in token
    assert candidate_id not in token

    ref = verify_voice_token(conn, token, now=NOW, secret=SECRET)
    assert ref == VoiceSessionRef(
        voice_session_id=SESSION, conversation_id=CONV, candidate_id=candidate_id, country="ES"
    )
    assert verify_voice_token(conn, token, now=NOW, secret=SECRET, conversation_id=CONV) == ref
    assert not hasattr(ref, "application_id")


def _tamper_payload(token: str) -> str:
    payload, sig = token.rsplit(".", 1)
    return f"{payload[:-1]}{'0' if payload[-1] != '0' else '1'}.{sig}"


def _tamper_signature(token: str) -> str:
    payload, sig = token.rsplit(".", 1)
    return f"{payload}.{'A' if sig[0] != 'A' else 'B'}{sig[1:]}"


def _exp(offset: int) -> str:
    return _signed(f"v1.voice.{CONV}.{SESSION}.{int(NOW.timestamp()) + offset}")


INVALID = {
    "tampered-payload": lambda conn: _tamper_payload(_token()),
    "tampered-signature": lambda conn: _tamper_signature(_token()),
    "expired": lambda conn: _exp(-1),
    "exp-equals-now": lambda conn: _exp(0),
    "wrong-secret": lambda conn: _token(secret=b"another-secret-word"),
    "unknown-session": lambda conn: _token(session_id="vs_" + "9" * 32),
    "other-conversations-session": lambda conn: _signed(
        f"v1.voice.{OTHER_CONV}.{SESSION}.{int(NOW.timestamp()) + 60}"
    ),
    "v2-prefix": lambda conn: _signed(f"v2.voice.{CONV}.{SESSION}.{int(NOW.timestamp()) + 60}"),
    "not-voice": lambda conn: _signed(f"v1.book.{CONV}.{SESSION}.{int(NOW.timestamp()) + 60}"),
    "five-parts": lambda conn: _signed(f"v1.voice.{CONV}.{int(NOW.timestamp()) + 60}"),
    "seven-parts": lambda conn: _signed(f"v1.voice.{CONV}.{SESSION}.x.{int(NOW.timestamp()) + 60}"),
    "signed-exp": lambda conn: _signed(f"v1.voice.{CONV}.{SESSION}.+{int(NOW.timestamp()) + 60}"),
    "spaced-exp": lambda conn: _signed(f"v1.voice.{CONV}.{SESSION}. {int(NOW.timestamp()) + 60}"),
    "unicode-exp": lambda conn: _signed(
        f"v1.voice.{CONV}.{SESSION}.{str(int(NOW.timestamp()) + 60).replace('1', ARABIC_ONE)}"
    ),
    "empty": lambda conn: "",
    "garbage": lambda conn: "v1.voice.a.b.c.ñ",
}


@pytest.mark.parametrize("case", [*INVALID, "wrong-conversation", "ended-session"])
def test_invalid_tokens_are_refused(case: str, conn: sqlite3.Connection) -> None:
    _session(conn)
    _session(conn, "vs_" + "2" * 32, OTHER_CONV)
    kwargs: dict[str, Any] = {}
    if case == "wrong-conversation":
        token = _token()
        kwargs["conversation_id"] = OTHER_CONV
    elif case == "ended-session":
        token = _token()
        end_voice_session(conn, SESSION, now=NOW)
    else:
        token = INVALID[case](conn)
    with pytest.raises(VoiceTokenError) as info:
        verify_voice_token(conn, token, now=NOW, secret=SECRET, **kwargs)
    assert type(info.value) is VoiceTokenError
    assert str(info.value) == str(VoiceTokenError("invalid voice token"))


def test_an_empty_secret_refuses_before_parsing(conn: sqlite3.Connection) -> None:
    _session(conn)
    with pytest.raises(VoiceTokenError):
        verify_voice_token(conn, _token(), now=NOW, secret=b"")
    # Not even a token signed with the empty key verifies under it.
    empty_signed = _signed(f"v1.voice.{CONV}.{SESSION}.{int(NOW.timestamp()) + 60}", b"")
    with pytest.raises(VoiceTokenError):
        verify_voice_token(conn, empty_signed, now=NOW, secret=b"")
    with pytest.raises(ValueError):
        make_voice_token(CONV, SESSION, NOW, secret=b"")


@pytest.mark.parametrize(
    ("conversation_id", "session_id"),
    [("wc_a.b", SESSION), (CONV, "vs_1.2"), ("", SESSION), (CONV, "")],
)
def test_ids_with_a_dot_cannot_be_signed(conversation_id: str, session_id: str) -> None:
    with pytest.raises(ValueError):
        make_voice_token(conversation_id, session_id, NOW, secret=SECRET)


def test_a_session_ended_by_a_takeover_raises_voice_session_ended(
    conn: sqlite3.Connection,
) -> None:
    candidate_id = _session(conn)
    _take_over(conn, candidate_id)
    token = _token()
    # Not yet ended: a Takeover alone does not refuse the token here (spoken-turn decides).
    verify_voice_token(conn, token, now=NOW, secret=SECRET)
    end_voice_session(conn, SESSION, now=NOW)

    with pytest.raises(VoiceSessionEnded) as info:
        verify_voice_token(conn, token, now=NOW, secret=SECRET)
    assert info.value.ref == VoiceSessionRef(SESSION, CONV, candidate_id, "ES")
    assert isinstance(info.value, VoiceTokenError)

    # The same session, expired or tampered: plain VoiceTokenError.
    later = NOW + timedelta(seconds=VOICE_TOKEN_TTL_S)
    for bad, now in ((token, later), (_tamper_signature(token), NOW)):
        with pytest.raises(VoiceTokenError) as plain:
            verify_voice_token(conn, bad, now=now, secret=SECRET)
        assert type(plain.value) is VoiceTokenError


def test_end_voice_session_writes_once(conn: sqlite3.Connection) -> None:
    _session(conn)
    token = _token()
    verify_voice_token(conn, token, now=NOW, secret=SECRET)

    first = NOW + timedelta(minutes=3)
    end_voice_session(conn, SESSION, now=first, seconds=170.5)
    with pytest.raises(VoiceTokenError):
        verify_voice_token(conn, token, now=first, secret=SECRET)

    end_voice_session(conn, SESSION, now=first + timedelta(minutes=5), seconds=999)
    end_voice_session(conn, SESSION, now=first + timedelta(minutes=9))
    row = _row(conn)
    assert row["ended_at"] == iso(first)
    assert row["seconds"] == 170.5

    # Without seconds (a Takeover end), seconds stays NULL.
    _session(conn, "vs_" + "3" * 32, OTHER_CONV)
    end_voice_session(conn, "vs_" + "3" * 32, now=first)
    assert _row(conn, "vs_" + "3" * 32)["seconds"] is None
    # An unknown session is a no-op.
    end_voice_session(conn, "vs_" + "8" * 32, now=first)


def test_the_elevenlabs_conversation_id_is_written_once(conn: sqlite3.Connection) -> None:
    _session(conn)
    set_elevenlabs_conversation_id(conn, SESSION, "conv_el_1")
    set_elevenlabs_conversation_id(conn, SESSION, "conv_el_1")
    assert _row(conn)["elevenlabs_conversation_id"] == "conv_el_1"
    set_elevenlabs_conversation_id(conn, SESSION, "conv_el_2")
    assert _row(conn)["elevenlabs_conversation_id"] == "conv_el_1"


def test_an_elevenlabs_conversation_id_on_another_session_is_refused(
    conn: sqlite3.Connection,
) -> None:
    other = "vs_" + "2" * 32
    _session(conn)
    _session(conn, other, OTHER_CONV)
    set_elevenlabs_conversation_id(conn, SESSION, "conv_el_1")
    before = (_row(conn), _row(conn, other))

    with pytest.raises(VoiceTokenError) as info:
        set_elevenlabs_conversation_id(conn, other, "conv_el_1")
    assert type(info.value) is VoiceTokenError
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__ is True
    assert (_row(conn), _row(conn, other)) == before

    with pytest.raises(VoiceTokenError):
        set_elevenlabs_conversation_id(conn, "vs_" + "7" * 32, "conv_el_9")
    with pytest.raises(VoiceTokenError):
        set_elevenlabs_conversation_id(conn, other, "")
    assert (_row(conn), _row(conn, other)) == before
