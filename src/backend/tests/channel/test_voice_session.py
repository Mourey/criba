"""Voice settings, secret reader and availability gate (spec voice-agent session-config:
SE1, SE2, SE13). No endpoint here: the HTTP halves belong to session-start and
session-webhook. Secret test values are low-entropy words written to ``tmp_path``."""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient
from httpx import Response

from criba.channel.config import usage_environment
from criba.channel.copy import spoken_disclosure, with_ai_disclosure
from criba.channel.elevenlabs import ConversationToken, ElevenLabsError, FakeElevenLabsClient
from criba.channel.voice_config import VoiceSettings, voice_available
from criba.channel.voice_session import (
    VOICE_TOKEN_TTL_S,
    AgentDriftCache,
    VoiceRuntime,
    VoiceSessionRefused,
    VoiceSessionStart,
    link_spoken_disclosure,
    start_voice_session,
    store_spoken_disclosure,
    verify_voice_token,
)
from criba.db import connect, repo
from criba.db.connection import iso
from criba.screening.clock import FakeClock

from .conftest import candidate_for, start

LOGGER = "criba.channel.voice_config"

SecretName = Literal[
    "elevenlabs_api_key",
    "criba_voice_secret",
    "criba_voice_llm_secret",
    "elevenlabs_webhook_secret",
]
SECRET_NAMES: tuple[SecretName, ...] = (
    "elevenlabs_api_key",
    "criba_voice_secret",
    "criba_voice_llm_secret",
    "elevenlabs_webhook_secret",
)
FILE_VARS: dict[SecretName, str] = {
    "elevenlabs_api_key": "CRIBA_ELEVENLABS_API_KEY_FILE",
    "criba_voice_secret": "CRIBA_VOICE_SECRET_FILE",
    "criba_voice_llm_secret": "CRIBA_VOICE_LLM_SECRET_FILE",
    "elevenlabs_webhook_secret": "CRIBA_ELEVENLABS_WEBHOOK_SECRET_FILE",
}
ALL_VARS = (
    "CRIBA_VOICE",
    "CRIBA_VOICE_AGENT_ES",
    "CRIBA_VOICE_AGENT_MX",
    "CRIBA_VOICE_PUBLIC_BASE_URL",
    "CRIBA_VOICE_MAX_CONCURRENT",
    "CRIBA_VOICE_DAILY_MINUTES",
    "CRIBA_VOICE_COST_PER_MIN",
    "CRIBA_ENV",
    *FILE_VARS.values(),
)
SECRET_VALUE = "voice-secret-for-tests"


def _secret_files(tmp_path: Path, names: tuple[SecretName, ...] = SECRET_NAMES) -> dict[str, str]:
    env: dict[str, str] = {}
    for name in names:
        path = tmp_path / f"{name}.txt"
        path.write_text(f"{SECRET_VALUE}-{name}\n", encoding="utf-8")
        env[FILE_VARS[name]] = str(path)
    return env


def _complete_env(tmp_path: Path) -> dict[str, str]:
    return {
        "CRIBA_VOICE": "on",
        "CRIBA_VOICE_AGENT_ES": "agent-es",
        "CRIBA_VOICE_AGENT_MX": "agent-mx",
        "CRIBA_VOICE_PUBLIC_BASE_URL": "https://voice.example",
        **_secret_files(tmp_path),
    }


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]


@pytest.mark.parametrize(
    ("value", "enabled", "warned"),
    [
        ("on", True, False),
        ("ON", True, False),
        (" on ", True, False),
        ("true", False, True),
        ("1", False, True),
        ("", False, False),
        (None, False, False),
        ("off", False, False),
    ],
)
def test_voice_flag(
    value: str | None, enabled: bool, warned: bool, caplog: pytest.LogCaptureFixture
) -> None:
    env = {} if value is None else {"CRIBA_VOICE": value}
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        settings = VoiceSettings.from_env(env)
    assert settings.enabled is enabled
    assert settings.invalid == ()
    records = _warnings(caplog)
    if not warned:
        assert records == []
        return
    assert len(records) == 1
    message = records[0].getMessage()
    assert "CRIBA_VOICE" in message
    others = [v for v in ALL_VARS if v != "CRIBA_VOICE"]
    assert not any(other in message for other in others)
    assert value not in message.replace("CRIBA_VOICE", "")


@pytest.mark.parametrize(
    ("variable", "value", "field"),
    [
        ("CRIBA_VOICE_MAX_CONCURRENT", "0", "max_concurrent"),
        ("CRIBA_VOICE_MAX_CONCURRENT", "-1", "max_concurrent"),
        ("CRIBA_VOICE_MAX_CONCURRENT", "abc", "max_concurrent"),
        ("CRIBA_VOICE_MAX_CONCURRENT", "2.5", "max_concurrent"),
        ("CRIBA_VOICE_DAILY_MINUTES", "0", "daily_minutes"),
        ("CRIBA_VOICE_DAILY_MINUTES", "-1", "daily_minutes"),
        ("CRIBA_VOICE_DAILY_MINUTES", "abc", "daily_minutes"),
        ("CRIBA_VOICE_DAILY_MINUTES", "2.5", "daily_minutes"),
        ("CRIBA_VOICE_COST_PER_MIN", "-0.1", "cost_per_min"),
        ("CRIBA_VOICE_COST_PER_MIN", "abc", "cost_per_min"),
        ("CRIBA_VOICE_COST_PER_MIN", "nan", "cost_per_min"),
        ("CRIBA_VOICE_COST_PER_MIN", "inf", "cost_per_min"),
        ("CRIBA_VOICE_COST_PER_MIN", "0", None),
        ("CRIBA_VOICE_MAX_CONCURRENT", " 7 ", None),
        ("CRIBA_VOICE_DAILY_MINUTES", "30", None),
        ("CRIBA_VOICE_COST_PER_MIN", "0.15", None),
    ],
)
def test_invalid_numbers_make_voice_unavailable(
    variable: str,
    value: str,
    field: str | None,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    env = {**_complete_env(tmp_path), variable: value}
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        settings = VoiceSettings.from_env(env)
    if field is None:
        accepted = {
            "CRIBA_VOICE_MAX_CONCURRENT": ("max_concurrent", 7),
            "CRIBA_VOICE_DAILY_MINUTES": ("daily_minutes", 30),
            "CRIBA_VOICE_COST_PER_MIN": ("cost_per_min", float(value)),
        }
        name, expected = accepted[variable]
        assert settings.invalid == ()
        assert getattr(settings, name) == expected
        assert voice_available(settings) is True
        assert _warnings(caplog) == []
        return
    assert settings.invalid == (variable,)
    assert getattr(settings, field) == 0
    assert voice_available(settings) is False
    records = _warnings(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert variable in message
    assert value not in message.replace(variable, "")


@pytest.mark.parametrize("criba_env", [None, "prod", "sim", "test", "staging"])
def test_defaults(criba_env: str | None) -> None:
    env = {"CRIBA_VOICE": "on"}
    if criba_env is not None:
        env["CRIBA_ENV"] = criba_env
    settings = VoiceSettings.from_env(env)
    assert settings.enabled is True
    assert settings.agent_es == ""
    assert settings.agent_mx == ""
    assert settings.public_base_url == ""
    assert settings.max_concurrent == 5
    assert settings.daily_minutes == 120
    assert settings.cost_per_min == 0.08
    assert settings.environment == usage_environment(criba_env)
    assert settings.elevenlabs_api_key_file == ""
    assert settings.voice_secret_file == ""
    assert settings.voice_llm_secret_file == ""
    assert settings.elevenlabs_webhook_secret_file == ""
    assert settings.invalid == ()
    for name in SECRET_NAMES:
        assert settings.read_secret(name) == ""


@pytest.mark.parametrize("name", SECRET_NAMES)
def test_read_secret(name: SecretName, tmp_path: Path) -> None:
    variable = FILE_VARS[name]
    path = tmp_path / "secret.txt"

    # Unset variable.
    assert VoiceSettings.from_env({}).read_secret(name) == ""

    settings = VoiceSettings.from_env({variable: str(path)})
    # Missing file.
    assert settings.read_secret(name) == ""
    # Empty file.
    path.write_text("", encoding="utf-8")
    assert settings.read_secret(name) == ""
    # Whitespace-only file.
    path.write_text("  \n\t\n", encoding="utf-8")
    assert settings.read_secret(name) == ""
    # Trailing newline is stripped.
    path.write_text("first-voice-word\n", encoding="utf-8")
    assert settings.read_secret(name) == "first-voice-word"
    # Rewritten between calls: the new value, never a cached one.
    path.write_text("second-voice-word\n", encoding="utf-8")
    assert settings.read_secret(name) == "second-voice-word"
    # A directory (unreadable as a file) is ''.
    assert VoiceSettings.from_env({variable: str(tmp_path)}).read_secret(name) == ""
    # No fallback: the other three names stay '' with only this file set.
    for other in SECRET_NAMES:
        if other != name:
            assert settings.read_secret(other) == ""


def _without(env: dict[str, str], tmp_path: Path, case: str) -> dict[str, str]:
    env = dict(env)
    if case == "enabled_off":
        env["CRIBA_VOICE"] = "off"
    elif case == "agent_es_empty":
        env["CRIBA_VOICE_AGENT_ES"] = ""
    elif case == "agent_mx_empty":
        env["CRIBA_VOICE_AGENT_MX"] = ""
    elif case == "api_key_missing":
        env[FILE_VARS["elevenlabs_api_key"]] = str(tmp_path / "absent-api-key.txt")
    elif case == "api_key_empty":
        Path(env[FILE_VARS["elevenlabs_api_key"]]).write_text("", encoding="utf-8")
    elif case == "voice_secret_missing":
        env[FILE_VARS["criba_voice_secret"]] = str(tmp_path / "absent-voice-secret.txt")
    elif case == "voice_secret_empty":
        Path(env[FILE_VARS["criba_voice_secret"]]).write_text("", encoding="utf-8")
    return env


@pytest.mark.parametrize(
    "case",
    [
        "enabled_off",
        "agent_es_empty",
        "agent_mx_empty",
        "api_key_missing",
        "api_key_empty",
        "voice_secret_missing",
        "voice_secret_empty",
    ],
)
def test_voice_available(case: str, tmp_path: Path) -> None:
    complete = _complete_env(tmp_path)
    assert voice_available(VoiceSettings.from_env(complete)) is True

    # The custom-LLM and webhook secrets are not required for availability.
    required_only = {
        **complete,
        FILE_VARS["criba_voice_llm_secret"]: str(tmp_path / "absent-llm.txt"),
        FILE_VARS["elevenlabs_webhook_secret"]: str(tmp_path / "absent-webhook.txt"),
    }
    assert voice_available(VoiceSettings.from_env(required_only)) is True

    # One voice secret never stands in for another: with only the optional two
    # present, the required two are still missing.
    optional_only = {
        k: v
        for k, v in complete.items()
        if k not in (FILE_VARS["elevenlabs_api_key"], FILE_VARS["criba_voice_secret"])
    }
    assert voice_available(VoiceSettings.from_env(optional_only)) is False

    assert voice_available(VoiceSettings.from_env(_without(complete, tmp_path, case))) is False


def test_no_secret_in_repr_or_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    env = {
        **_complete_env(tmp_path),
        "CRIBA_VOICE_MAX_CONCURRENT": "9999.5",
        "CRIBA_VOICE_COST_PER_MIN": "-7777",
    }
    with caplog.at_level(logging.DEBUG):
        settings = VoiceSettings.from_env(env)
        for name in SECRET_NAMES:
            assert settings.read_secret(name) == f"{SECRET_VALUE}-{name}"
        assert voice_available(settings) is False

    assert SECRET_VALUE not in repr(settings)
    assert SECRET_VALUE not in str(dataclasses.asdict(settings))
    for record in caplog.records:
        assert SECRET_VALUE not in record.getMessage()
        assert SECRET_VALUE not in str(record.args)
        assert "9999" not in record.getMessage()
        assert "7777" not in record.getMessage()
    messages = [r.getMessage() for r in _warnings(caplog)]
    assert any("CRIBA_VOICE_MAX_CONCURRENT" in m for m in messages)
    assert any("CRIBA_VOICE_COST_PER_MIN" in m for m in messages)


# ---------------------------------------------------------------------------
# start_voice_session (spec voice-agent session-start SE4-SE6, SE10, SE16)
# ---------------------------------------------------------------------------

START = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
AGENTS = {"ES": "agent-es", "MX": "agent-mx"}
# A voice conversation has no chat to point to (ADR 0059).
REFUSED_409 = "No podemos empezar la llamada ahora. Inténtalo más tarde."
REFUSED_503 = "La llamada de voz no está disponible ahora. Inténtalo más tarde."


def _voice_settings(tmp_path: Path, **overrides: str) -> VoiceSettings:
    env = {**_complete_env(tmp_path), "CRIBA_ENV": "test", **overrides}
    return VoiceSettings.from_env(env)


def _conversation(
    conn: sqlite3.Connection, name: str, *, country: str | None = "ES", language: str = "es-ES"
) -> tuple[str, str]:
    conversation_id = f"wc_{name}"
    candidate, _ = repo.get_or_create_candidate(
        conn,
        client_id="grupo_sazon",
        channel="voice",  # a call starts on a voice conversation only (ADR 0059)
        external_id=conversation_id,
        country=country,
        language=language,
        now=START,
    )
    if country is None:
        conn.execute("UPDATE candidates SET country = NULL WHERE id = ?", (candidate["id"],))
    return conversation_id, str(candidate["id"])


def _start(
    conn: sqlite3.Connection,
    conversation_id: str,
    settings: VoiceSettings,
    *,
    now: datetime = START,
    client: FakeElevenLabsClient | None = None,
    drift: AgentDriftCache | None = None,
) -> VoiceSessionStart:
    return start_voice_session(
        conn,
        conversation_id=conversation_id,
        settings=settings,
        now=now,
        client=client if client is not None else FakeElevenLabsClient(),
        drift=drift,
    )


def _refusal(
    conn: sqlite3.Connection, conversation_id: str, settings: VoiceSettings, **kwargs: object
) -> VoiceSessionRefused:
    with pytest.raises(VoiceSessionRefused) as info:
        _start(conn, conversation_id, settings, **kwargs)
    return info.value


def _sessions(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return [dict(r) for r in conn.execute("SELECT * FROM voice_sessions ORDER BY started_at, id")]


def _messages(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return [dict(r) for r in conn.execute("SELECT * FROM messages ORDER BY id")]


def _disclosure(
    session: dict[str, object], message_id: int, application_id: str | None = None
) -> dict[str, object]:
    """The ``messages`` row the spoken disclosure of ``session`` is stored as, at mint."""
    return {
        "id": message_id,
        "application_id": application_id,
        "candidate_id": session["candidate_id"],
        "channel": "voice",
        "direction": "outbound",
        "sender": "agent",
        "sender_user_id": None,
        "client_message_id": f"vd_{session['id']}",
        "content_type": "text",
        "body": spoken_disclosure("es", str(session["country"])),
        "interactive": None,
        "reply_option_id": None,
        "template_name": None,
        "media_type": None,
        "run_id": None,
        "modality": "spoken",
        "created_at": iso(START),
    }


def _plant(
    conn: sqlite3.Connection,
    conversation_id: str,
    candidate_id: str,
    started_at: datetime,
    *,
    ended_at: datetime | None = None,
    seconds: float | None = None,
) -> None:
    conn.execute(
        "INSERT INTO voice_sessions (id, conversation_id, candidate_id, country, started_at,"
        " ended_at, seconds) VALUES (?, ?, ?, 'ES', ?, ?, ?)",
        (
            f"vs_planted{len(_sessions(conn)):022d}",
            conversation_id,
            candidate_id,
            iso(started_at),
            iso(ended_at) if ended_at else None,
            seconds,
        ),
    )


def _take_over(conn: sqlite3.Connection, candidate_id: str) -> dict[str, object]:
    app = repo.start_application(conn, candidate_id, "voice", now=START)
    conn.execute(
        "INSERT OR IGNORE INTO users (id, client_id, email, name, role, password_hash,"
        " created_at) VALUES ('u1', 'grupo_sazon', 'r@example.com', 'R', 'recruiter', 'x',"
        " '2026-10-09')"
    )
    conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'u1' WHERE id = ?",
        (app["id"],),
    )
    return app


def test_start_records_a_session_and_signs_a_token(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = _voice_settings(tmp_path)
    fake = FakeElevenLabsClient()
    es, es_candidate = _conversation(conn, "es")
    mx, _ = _conversation(conn, "mx", country="MX", language="es-MX")
    _, opted_candidate = _conversation(conn, "opted")
    repo.mark_opted_out(conn, opted_candidate, now=START)

    first = _start(conn, es, settings, client=fake)
    second = _start(conn, mx, settings, client=fake)
    third = _start(conn, "wc_opted", settings, client=fake)

    assert fake.calls == ["agent-es", "agent-mx", "agent-es"]
    assert (first.country, second.country, third.country) == ("ES", "MX", "ES")
    assert first.conversation_token == "fake-conversation-token"
    assert first.expires_at == START + timedelta(seconds=VOICE_TOKEN_TTL_S)
    assert set(dataclasses.asdict(first)) == {
        "conversation_token",
        "voice_token",
        "expires_at",
        "country",
    }
    secret = settings.read_secret("criba_voice_secret").encode("utf-8")
    ref = verify_voice_token(conn, first.voice_token, now=START, secret=secret, conversation_id=es)
    assert ref.candidate_id == es_candidate

    rows = _sessions(conn)
    assert len(rows) == 3
    row = next(r for r in rows if r["id"] == ref.voice_session_id)
    assert row["id"].startswith("vs_") and len(row["id"]) == 35
    assert row["conversation_id"] == es
    assert row["candidate_id"] == es_candidate
    assert row["country"] == "ES"
    assert row["started_at"] == iso(START)
    assert row["ended_at"] is None and row["seconds"] is None
    assert row["elevenlabs_conversation_id"] == fake.conversation_ids[0]

    # No Application created, each call's disclosure stored for its Candidate alone, and the
    # opt-out stands.
    assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0
    by_conversation = {r["conversation_id"]: r for r in rows}
    assert _messages(conn) == [
        _disclosure(by_conversation[conversation_id], number)
        for number, conversation_id in enumerate((es, mx, "wc_opted"), start=1)
    ]
    opted = repo.get_candidate(conn, opted_candidate)
    assert opted is not None and opted["opted_out"]


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("off", 503, "voice_unavailable"),
        ("no-voice-secret", 503, "voice_unavailable"),
        ("unknown", 404, "not_found"),
        ("dotted-id", 404, "not_found"),
        ("no-country", 503, "voice_unavailable"),
        ("takeover", 409, "voice_refused"),
        ("rate", 429, "rate_limited"),
        ("concurrent", 503, "voice_unavailable"),
        ("daily", 503, "voice_unavailable"),
        ("drift", 503, "voice_unavailable"),
        # Pairs that prove the order.
        ("off+unknown", 503, "voice_unavailable"),
        ("unknown+caps", 404, "not_found"),
        ("takeover+rate", 409, "voice_refused"),
        ("rate+caps", 429, "rate_limited"),
        ("caps+drift", 503, "voice_unavailable"),
    ],
)
def test_refusals_in_order(
    case: str, status: int, code: str, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    overrides: dict[str, str] = {}
    if "off" in case:
        overrides["CRIBA_VOICE"] = "off"
    if case in ("concurrent", "unknown+caps", "rate+caps", "caps+drift"):
        overrides["CRIBA_VOICE_MAX_CONCURRENT"] = "1"
    if case == "daily":
        overrides["CRIBA_VOICE_DAILY_MINUTES"] = "1"
    settings = _voice_settings(tmp_path, **overrides)
    if case == "no-voice-secret":
        Path(settings.voice_secret_file).write_text("", encoding="utf-8")
    conversation_id, candidate_id = _conversation(conn, "main")
    other, other_candidate = _conversation(conn, "other")
    if "unknown" in case:
        conversation_id = "wc_unknown"
    if case == "dotted-id":
        conversation_id = "wc_main.x"
    if case == "no-country":
        conversation_id, _ = _conversation(conn, "nocountry", country=None)
    if "takeover" in case:
        _take_over(conn, candidate_id)
    if "rate" in case:
        for minutes in (9, 8, 7):
            _plant(conn, conversation_id, candidate_id, START - timedelta(minutes=minutes))
    if case in ("concurrent", "unknown+caps", "caps+drift"):
        _plant(conn, other, other_candidate, START - timedelta(minutes=1))
    if case == "daily":
        _plant(
            conn,
            other,
            other_candidate,
            START - timedelta(minutes=5),
            ended_at=START - timedelta(minutes=4),
            seconds=60,
        )
    drift = None
    if "drift" in case:
        drift = AgentDriftCache(lambda: ["agent-es: first_message differs"])
        drift.refresh()

    sessions, messages = _sessions(conn), _messages(conn)
    fake = FakeElevenLabsClient()
    refused = _refusal(conn, conversation_id, settings, client=fake, drift=drift)

    assert (refused.status, refused.code) == (status, code)
    assert fake.calls == []
    assert _sessions(conn) == sessions
    assert _messages(conn) == messages
    if status == 409:
        assert refused.message == REFUSED_409
    if status == 503:
        assert refused.message == REFUSED_503
        assert refused.retry_after is None
    if status == 429:
        assert refused.retry_after == 60
        assert refused.message == "Vas muy rápido. Espera un momento y vuelve a escribir."


def test_the_rate_limit_counts_started_sessions(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _voice_settings(tmp_path, CRIBA_VOICE_MAX_CONCURRENT="10")
    clock = FakeClock(START)
    conversation_id, _ = _conversation(conn, "rate")
    for _ in range(3):
        _start(conn, conversation_id, settings, now=clock.now())
        clock.advance(minutes=1)

    refused = _refusal(conn, conversation_id, settings, now=clock.now())
    assert refused.status == 429
    assert refused.retry_after == 420  # the oldest, at minute 0, leaves the window at minute 10
    assert len(_sessions(conn)) == 3  # a refusal adds no row
    _refusal(conn, conversation_id, settings, now=clock.now())
    assert len(_sessions(conn)) == 3

    clock.set(START + timedelta(minutes=11))
    _start(conn, conversation_id, settings, now=clock.now())
    assert len(_sessions(conn)) == 4

    # Three failed mints do not block a fourth start.
    failing, _ = _conversation(conn, "failing")
    fake = FakeElevenLabsClient()
    fake.fail = ElevenLabsError("ElevenLabs answered 500")
    for _ in range(3):
        assert _refusal(conn, failing, settings, now=clock.now(), client=fake).status == 503
    fake.fail = None
    _start(conn, failing, settings, now=clock.now(), client=fake)


def test_the_concurrent_cap_is_atomic(db_path: Path, tmp_path: Path) -> None:
    settings = _voice_settings(tmp_path, CRIBA_VOICE_MAX_CONCURRENT="1")
    setup = connect(db_path)
    try:
        first, _ = _conversation(setup, "one")
        second, _ = _conversation(setup, "two")
    finally:
        setup.close()
    barrier = threading.Barrier(2)
    outcomes: list[object] = []
    lock = threading.Lock()

    def attempt(conversation_id: str) -> None:
        own = connect(db_path)
        try:
            barrier.wait(timeout=5)
            try:
                result: object = _start(own, conversation_id, settings)
            except VoiceSessionRefused as refused:
                result = refused.status
            with lock:
                outcomes.append(result)
        finally:
            own.close()

    threads = [threading.Thread(target=attempt, args=(c,)) for c in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert len(outcomes) == 2
    assert sum(isinstance(o, VoiceSessionStart) for o in outcomes) == 1
    assert 503 in outcomes
    check = connect(db_path)
    try:
        assert len(_sessions(check)) == 1
    finally:
        check.close()


def test_an_expired_unended_session_is_not_live(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _voice_settings(tmp_path, CRIBA_VOICE_MAX_CONCURRENT="1")
    conversation_id, candidate_id = _conversation(conn, "live")
    _plant(conn, conversation_id, candidate_id, START - timedelta(minutes=31))
    _start(conn, conversation_id, settings)
    # The 30-minute boundary itself is no longer live either.
    other, other_candidate = _conversation(conn, "edge")
    conn.execute("DELETE FROM voice_sessions WHERE started_at = ?", (iso(START),))
    _plant(conn, other, other_candidate, START - timedelta(seconds=VOICE_TOKEN_TTL_S))
    _start(conn, conversation_id, settings)
    assert _refusal(conn, other, settings).status == 503


def test_the_daily_minutes_cap(conn: sqlite3.Connection, tmp_path: Path) -> None:
    conversation_id, _ = _conversation(conn, "daily")
    other, other_candidate = _conversation(conn, "spent")
    settings = _voice_settings(tmp_path, CRIBA_VOICE_MAX_CONCURRENT="20")
    # Yesterday's calls do not count.
    _plant(
        conn,
        other,
        other_candidate,
        START - timedelta(days=1),
        ended_at=START - timedelta(days=1) + timedelta(hours=3),
        seconds=3 * 3600,
    )
    # Ended sessions totalling 119 minutes: one more starts.
    for hour, seconds in ((1, 3600.0), (2, 3540.0)):
        _plant(
            conn,
            other,
            other_candidate,
            START - timedelta(hours=hour + 2),
            ended_at=START - timedelta(hours=hour + 1),
            seconds=seconds,
        )
    started = _start(conn, conversation_id, settings)
    secret = settings.read_secret("criba_voice_secret").encode("utf-8")
    ref = verify_voice_token(conn, started.voice_token, now=START, secret=secret)
    conn.execute(
        "UPDATE voice_sessions SET ended_at = ?, seconds = 60 WHERE id = ?",
        (iso(START), ref.voice_session_id),
    )
    # Now 120 minutes are used.
    assert _refusal(conn, conversation_id, settings).status == 503

    # An ended row without seconds (a Takeover end) counts ended_at - started_at.
    conn.execute("DELETE FROM voice_sessions")
    _plant(
        conn,
        other,
        other_candidate,
        START - timedelta(minutes=40),
        ended_at=START - timedelta(minutes=10),
    )
    thirty = _voice_settings(tmp_path, CRIBA_VOICE_DAILY_MINUTES="30")
    assert _refusal(conn, conversation_id, thirty).status == 503
    thirty_one = _voice_settings(tmp_path, CRIBA_VOICE_DAILY_MINUTES="31")
    _start(conn, conversation_id, thirty_one)

    # An expired unended session counts 1800 s, not its whole age.
    conn.execute("DELETE FROM voice_sessions")
    _plant(conn, other, other_candidate, START - timedelta(minutes=40))
    assert _refusal(conn, conversation_id, thirty).status == 503
    _start(conn, conversation_id, thirty_one)


def test_a_failed_mint_closes_the_row(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _voice_settings(tmp_path)
    conversation_id, candidate_id = _conversation(conn, "fail")
    repo.start_application(conn, candidate_id, "voice", now=START)
    fake = FakeElevenLabsClient()
    fake.fail = ElevenLabsError("ElevenLabs answered 500")

    refused = _refusal(conn, conversation_id, settings, client=fake)

    assert (refused.status, refused.code) == (503, "voice_unavailable")
    (row,) = _sessions(conn)
    assert row["ended_at"] == iso(START)
    assert row["seconds"] == 0
    assert row["elevenlabs_conversation_id"] is None
    assert _messages(conn) == []
    assert refused.__cause__ is None and refused.__suppress_context__ is True

    # Any exception from the client, not only ElevenLabsError.
    fake.fail = RuntimeError("boom")
    assert _refusal(conn, conversation_id, settings, client=fake).status == 503


def test_a_conversation_id_clash_ends_the_new_session(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = _voice_settings(tmp_path)
    first, _ = _conversation(conn, "first")
    second, second_candidate = _conversation(conn, "second")
    repo.start_application(conn, second_candidate, "voice", now=START)
    fake = FakeElevenLabsClient(conversation_id="conv-fixed")

    _start(conn, first, settings, client=fake)
    refused = _refusal(conn, second, settings, client=fake)

    assert (refused.status, refused.code) == (503, "voice_unavailable")
    rows = {r["conversation_id"]: r for r in _sessions(conn)}
    assert rows[first]["elevenlabs_conversation_id"] == "conv-fixed"
    assert rows[first]["ended_at"] is None
    assert rows[second]["elevenlabs_conversation_id"] is None
    assert rows[second]["ended_at"] == iso(START)
    assert rows[second]["seconds"] == 0
    # Only the session that started stored its disclosure; the failed one stored none.
    assert _messages(conn) == [_disclosure(rows[first], 1)]


def test_the_mint_runs_outside_the_write_lock(db_path: Path, tmp_path: Path) -> None:
    settings = _voice_settings(tmp_path)
    conn = connect(db_path)
    probes: list[str] = []

    class LockProbe(FakeElevenLabsClient):
        def conversation_token(self, agent_id: str) -> ConversationToken:
            other = sqlite3.connect(str(db_path), timeout=0, isolation_level=None)
            try:
                other.execute("BEGIN IMMEDIATE")
                other.execute("ROLLBACK")
                probes.append("write lock free")
            finally:
                other.close()
            assert not conn.in_transaction
            return super().conversation_token(agent_id)

    try:
        conversation_id, candidate_id = _conversation(conn, "lock")
        repo.start_application(conn, candidate_id, "voice", now=START)
        _start(conn, conversation_id, settings, client=LockProbe())
    finally:
        conn.close()
    assert probes == ["write lock free"]


def test_an_open_transaction_is_refused(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _voice_settings(tmp_path)
    conversation_id, _ = _conversation(conn, "txn")
    fake = FakeElevenLabsClient()
    conn.execute("BEGIN")
    try:
        with pytest.raises(ValueError):
            _start(conn, conversation_id, settings, client=fake)
    finally:
        conn.execute("ROLLBACK")
    assert fake.calls == []
    assert _sessions(conn) == []


def _raise_drift() -> Sequence[str]:
    raise RuntimeError("drift check down")


@pytest.mark.parametrize(
    ("case", "environment", "started"),
    [
        ("drift", "test", False),
        ("raising", "test", False),
        ("clean", "prod", True),
        ("none", "prod", False),
        ("none", "test", True),
        ("none", "sim", True),
    ],
)
def test_the_drift_step(
    case: str, environment: str, started: bool, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = _voice_settings(tmp_path, CRIBA_ENV=environment)
    conversation_id, _ = _conversation(conn, "drift")
    drift: AgentDriftCache | None = None
    if case == "drift":
        drift = AgentDriftCache(lambda: ["agent-es: voice differs"])
    elif case == "raising":
        drift = AgentDriftCache(_raise_drift)
    elif case == "clean":
        drift = AgentDriftCache(lambda: [])
    if drift is not None:
        drift.refresh()
    if started:
        assert _start(conn, conversation_id, settings, drift=drift).country == "ES"
    else:
        refused = _refusal(conn, conversation_id, settings, drift=drift)
        assert (refused.status, refused.code) == (503, "voice_unavailable")
        assert _sessions(conn) == []


def test_the_drift_cache_states(caplog: pytest.LogCaptureFixture) -> None:
    results: list[object] = []
    called = threading.Event()

    def check() -> Sequence[str]:
        called.set()
        value = results.pop(0) if results else []
        if isinstance(value, Exception):
            raise value
        return value

    with caplog.at_level(logging.DEBUG):
        cache = AgentDriftCache(check, interval_s=600)
        assert cache.ok() is False  # before the first check
        results.append(["agent-es: llm differs"])
        cache.refresh()
        assert cache.ok() is False  # drift
        results.append([])
        cache.refresh()
        assert cache.ok() is True
        results.append(RuntimeError("voice-secret-for-tests in a message"))
        cache.refresh()
        assert cache.ok() is False  # after an exception
        results.append([])
        cache.refresh()
        assert cache.ok() is True

        results.append(["agent-mx: first_message differs"])
        cache.start()
        assert called.wait(timeout=5)
        threads = [t for t in threading.enumerate() if t.name == "voice-agent-drift"]
        assert threads and all(t.daemon for t in threads)
        cache.stop()
        assert not any(t.is_alive() for t in threads)
        assert cache.ok() is False

    for record in caplog.records:
        text = record.getMessage() + str(record.args) + str(record.exc_info)
        assert "voice-secret-for-tests" not in text
        assert "agent-es" not in text and "agent-mx" not in text


@pytest.mark.parametrize(("country", "language"), [("ES", "es-ES"), ("MX", "es-MX")])
def test_the_disclosure_is_stored_at_mint(
    country: str, language: str, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = _voice_settings(tmp_path)
    conversation_id, candidate_id = _conversation(
        conn, country.lower(), country=country, language=language
    )
    app = repo.start_application(conn, candidate_id, "voice", now=START)

    _start(conn, conversation_id, settings)

    (stored,) = _messages(conn)
    (session,) = _sessions(conn)
    assert stored["body"] == spoken_disclosure("es", country)
    assert stored["modality"] == "spoken"
    assert stored["application_id"] == app["id"]
    assert stored["candidate_id"] == candidate_id
    assert (stored["channel"], stored["direction"], stored["sender"]) == (
        "voice",
        "outbound",
        "agent",
    )
    assert stored["client_message_id"] == f"vd_{session['id']}"
    reply = "¿Cómo te llamas?"
    assert with_ai_disclosure(reply, [stored["body"]]) == reply


def test_store_spoken_disclosure_stores_once(conn: sqlite3.Connection, tmp_path: Path) -> None:
    settings = _voice_settings(tmp_path)
    conversation_id, candidate_id = _conversation(conn, "later")

    _start(conn, conversation_id, settings)
    # No Application at mint: the disclosure is stored for the Candidate alone (the schema's
    # NULL application_id), so the captions have it from the start of the call.
    (session,) = _sessions(conn)
    assert _messages(conn) == [_disclosure(session, 1)]
    (before,) = _messages(conn)
    assert before["candidate_id"] == candidate_id

    app = repo.start_application(conn, candidate_id, "voice", now=START)
    first = store_spoken_disclosure(conn, session["id"], app, now=START + timedelta(seconds=5))
    second = store_spoken_disclosure(conn, session["id"], app, now=START + timedelta(seconds=9))

    (stored,) = _messages(conn)
    assert first is not None and second is not None
    assert first["id"] == second["id"] == stored["id"] == before["id"]
    assert stored["application_id"] == app["id"]  # linked once the Application exists
    assert stored["body"] == spoken_disclosure("es", "ES")
    assert stored["modality"] == "spoken"
    assert store_spoken_disclosure(conn, "vs_" + "0" * 32, app, now=START) is None
    _, stranger = _conversation(conn, "stranger")
    other_app = repo.start_application(conn, stranger, "voice", now=START)
    assert store_spoken_disclosure(conn, session["id"], other_app, now=START) is None
    assert len(_messages(conn)) == 1


def test_link_spoken_disclosure_refuses_another_candidates_application(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    settings = _voice_settings(tmp_path)
    conversation_id, candidate_id = _conversation(conn, "owner")
    _, stranger = _conversation(conn, "stranger")
    _start(conn, conversation_id, settings)
    (session,) = _sessions(conn)
    mine = repo.start_application(conn, candidate_id, "voice", now=START)
    theirs = repo.start_application(conn, stranger, "voice", now=START)

    assert link_spoken_disclosure(conn, session["id"], theirs) is False
    assert link_spoken_disclosure(conn, "vs_" + "0" * 32, mine) is False
    assert _messages(conn) == [_disclosure(session, 1)]  # still on the Candidate alone

    assert link_spoken_disclosure(conn, session["id"], mine) is True
    assert _messages(conn) == [_disclosure(session, 1, mine["id"])]
    assert link_spoken_disclosure(conn, session["id"], mine) is False  # already linked


def test_a_first_time_callers_captions_start_with_the_disclosure(
    client: TestClient, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """The call starts before an Application exists; the captions read the conversation's
    messages, so the disclosure must already be among them when the session is minted."""
    client.app.state.criba_voice = _voice(tmp_path)
    conversation_id = start(client, country="ES", locale="es-ES", channel="voice")

    assert _post(client, conversation_id, json={}).status_code == 201

    page = client.get(f"/api/chat/conversations/{conversation_id}/messages").json()
    (caption,) = page["messages"]
    assert caption["text"] == spoken_disclosure("es", "ES")
    assert (caption["direction"], caption["sender"], caption["modality"]) == (
        "outbound",
        "agent",
        "spoken",
    )
    assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0
    (stored,) = _messages(conn)
    assert stored["application_id"] is None


# ---------------------------------------------------------------------------
# The endpoint (SE2's endpoint half, SE4, SE5)
# ---------------------------------------------------------------------------

CONTRACT = Path(__file__).resolve().parents[4] / "contracts" / "webchat.openapi.yaml"


@pytest.fixture(scope="module")
def components() -> dict[str, object]:
    loaded = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    return dict(loaded["components"])


def _check(components: dict[str, object], name: str, value: object) -> None:
    schema = {"$ref": f"#/components/schemas/{name}", "components": components}
    jsonschema.Draft202012Validator(schema).validate(value)


def _voice(tmp_path: Path, **overrides: str) -> VoiceRuntime:
    settings = _voice_settings(tmp_path, **overrides)
    return VoiceRuntime(settings=settings, client=FakeElevenLabsClient())


def _post(client: TestClient, conversation_id: str, **kwargs: object) -> Response:
    return client.post(f"/api/chat/conversations/{conversation_id}/voice-session", **kwargs)


def test_voice_off_answers_503(
    client: TestClient, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    client.app.state.criba_voice = _voice(tmp_path, CRIBA_VOICE="off")
    conversation_id = start(client, channel="voice")
    chat = start(client)

    for target in (conversation_id, "wc_unknown"):
        resp = _post(client, target, json={})
        assert resp.status_code == 503
        assert resp.json() == {"error": "voice_unavailable", "message": REFUSED_503}
    assert _sessions(conn) == []

    sent = client.post(
        f"/api/chat/conversations/{chat}/messages",
        json={"message_id": "c1", "text": "hola"},
    )
    assert sent.status_code == 202


def test_the_endpoint_returns_201(
    client: TestClient, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    voice = _voice(tmp_path)
    client.app.state.criba_voice = voice
    conversation_id = start(client, country="MX", locale="es-MX", channel="voice")

    for kwargs in ({"json": {}}, {}):
        resp = _post(client, conversation_id, **kwargs)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert set(body) == {"conversation_token", "voice_token", "expires_at", "country"}
        assert body["country"] == "MX"
        assert body["conversation_token"] == "fake-conversation-token"
        assert body["expires_at"] == iso(START + timedelta(seconds=VOICE_TOKEN_TTL_S))
        secret = voice.settings.read_secret("criba_voice_secret").encode("utf-8")
        ref = verify_voice_token(
            conn,
            body["voice_token"],
            now=START,
            secret=secret,
            conversation_id=conversation_id,
        )
        assert ref.country == "MX"
    assert len(_sessions(conn)) == 2
    assert isinstance(voice.client, FakeElevenLabsClient)
    assert voice.client.calls == ["agent-mx", "agent-mx"]
    assert conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"json": {"application_id": "app_1"}},
        {"json": {"candidate_id": "cand_1"}},
        {"json": {"agent_id": "agent-es"}},
        {"json": []},
        {"json": "x"},
        {"content": b"null", "headers": {"Content-Type": "application/json"}},
        {"content": b"not json", "headers": {"Content-Type": "application/json"}},
    ],
    ids=["application_id", "candidate_id", "agent_id", "list", "string", "null", "text"],
)
def test_extra_body_fields_are_422(
    kwargs: dict[str, object], client: TestClient, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    voice = _voice(tmp_path)
    client.app.state.criba_voice = voice
    conversation_id = start(client, channel="voice")
    resp = _post(client, conversation_id, **kwargs)
    assert resp.status_code == 422
    assert resp.json()["error"] == "validation_error"
    assert _sessions(conn) == []
    assert isinstance(voice.client, FakeElevenLabsClient) and voice.client.calls == []


def _refusing_cases(
    client: TestClient, conn: sqlite3.Connection, tmp_path: Path
) -> list[tuple[str, Response]]:
    """One response per contract status, on the same app."""
    out: list[tuple[str, Response]] = []
    client.app.state.criba_voice = _voice(tmp_path, CRIBA_VOICE_MAX_CONCURRENT="20")
    conversation_id = start(client, channel="voice")
    out.append(("201", _post(client, conversation_id, json={})))
    out.append(("404", _post(client, "wc_unknown", json={})))
    out.append(("422", _post(client, conversation_id, json={"application_id": "app_1"})))
    taken = start(client, channel="voice")
    _take_over(conn, candidate_for(conn, taken, "voice")["id"])
    out.append(("409", _post(client, taken, json={})))
    for _ in range(2):
        _post(client, conversation_id, json={})
    out.append(("429", _post(client, conversation_id, json={})))
    client.app.state.criba_voice = _voice(tmp_path, CRIBA_VOICE="off")
    out.append(("503", _post(client, conversation_id, json={})))
    return out


def test_every_response_matches_the_contract(
    client: TestClient,
    conn: sqlite3.Connection,
    tmp_path: Path,
    components: dict[str, object],
) -> None:
    path = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["paths"][
        "/api/chat/conversations/{conversation_id}/voice-session"
    ]["post"]["responses"]
    responses = components["responses"]
    assert isinstance(responses, dict)
    for status, resp in _refusing_cases(client, conn, tmp_path):
        assert resp.status_code == int(status), (status, resp.text)
        declared = path[status]
        if "$ref" in declared:
            declared = responses[declared["$ref"].rsplit("/", 1)[1]]
        ref = declared["content"]["application/json"]["schema"]["$ref"]
        _check(components, ref.rsplit("/", 1)[1], resp.json())
        if status == "429":
            assert int(resp.headers["Retry-After"]) >= 1
    assert path.keys() >= {"201", "404", "409", "422", "429", "503"}


def test_the_409_names_no_reason(
    client: TestClient, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    client.app.state.criba_voice = _voice(tmp_path)
    conversation_id = start(client, channel="voice")
    candidate_id = candidate_for(conn, conversation_id, "voice")["id"]
    _take_over(conn, candidate_id)
    repo.mark_opted_out(conn, candidate_id, now=START)

    resp = _post(client, conversation_id, json={})

    assert resp.status_code == 409
    assert resp.json() == {"error": "voice_refused", "message": REFUSED_409}
    text = resp.text + str(dict(resp.headers))
    for word in ("Takeover", "takeover", "control", "human", "opt"):
        assert word not in text


def test_get_voice_resolution(
    client: TestClient, conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = client.app
    conversation_id = start(client, channel="voice")

    # 1. app.state.criba_runtime.voice, when no criba_voice is set.
    from_runtime = _voice(tmp_path, CRIBA_VOICE_MAX_CONCURRENT="20")
    app.state.criba_runtime = SimpleNamespace(voice=from_runtime)
    assert _post(client, conversation_id, json={}).status_code == 201
    assert isinstance(from_runtime.client, FakeElevenLabsClient)
    assert len(from_runtime.client.calls) == 1

    # 2. app.state.criba_voice comes first.
    preferred = _voice(tmp_path, CRIBA_VOICE_MAX_CONCURRENT="20")
    app.state.criba_voice = preferred
    assert _post(client, conversation_id, json={}).status_code == 201
    assert isinstance(preferred.client, FakeElevenLabsClient)
    assert len(preferred.client.calls) == 1
    assert len(from_runtime.client.calls) == 1

    # 3. Neither: one runtime built from the environment, kept and reused.
    del app.state.criba_voice
    del app.state.criba_runtime
    for name, value in {**_complete_env(tmp_path), "CRIBA_ENV": "test"}.items():
        monkeypatch.setenv(name, value)
    later = start(client, channel="voice")
    assert _post(client, later, json={}).status_code == 201
    built = app.state.criba_voice
    assert isinstance(built, VoiceRuntime)
    assert isinstance(built.client, FakeElevenLabsClient)
    assert built.drift is None
    other = start(client, channel="voice")
    assert _post(client, other, json={}).status_code == 201
    assert app.state.criba_voice is built
    assert len(built.client.calls) == 2


def test_no_secret_in_any_response(
    client: TestClient, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    responses = _refusing_cases(client, conn, tmp_path)
    secrets_and_ids = [f"{SECRET_VALUE}-{name}" for name in SECRET_NAMES]
    secrets_and_ids += ["agent-es", "agent-mx", SECRET_VALUE]
    assert len(responses) == 6
    for _, resp in responses:
        text = resp.text + str(dict(resp.headers))
        for value in secrets_and_ids:
            assert value not in text


# ---------------------------------------------------------------------------
# The runtime wiring (SE14's wiring.py lines, R1's sim fake)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["test", "sim"])
def test_the_runtime_wires_the_voice_client(environment: str, tmp_path: Path) -> None:
    from criba.summary import DeterministicSummaryClient
    from criba.wiring import build_runtime

    env = {
        **_complete_env(tmp_path),
        "CRIBA_ENV": environment,
        "CRIBA_JEV": "off",
        "CRIBA_DB_PATH": str(tmp_path / "criba.db"),
        "CRIBA_VOICE_MAX_CONCURRENT": "20",
    }
    runtime = build_runtime(
        env,
        run_client=SimpleNamespace(),  # never called while building
        summary_client=DeterministicSummaryClient(),
    )

    assert isinstance(runtime.voice, VoiceRuntime)
    assert isinstance(runtime.voice.client, FakeElevenLabsClient)
    assert runtime.voice.settings == VoiceSettings.from_env(env)
    assert runtime.voice.settings.environment == environment
    assert runtime.voice.settings.max_concurrent == 20
    assert runtime.voice.drift is None
