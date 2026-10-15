import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from criba.db import connect, migrate
from criba.db import migrator as migrate_module
from criba.db.migrator import Migration, available_migrations

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = REPO_ROOT / "contracts" / "schema.sql"
FIRST_MIGRATION = REPO_ROOT / "src/backend/criba/db/migrations/0001_schema.sql"


def schema_of(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'sqlite_%' AND name != 'schema_migrations'"
    ).fetchall()
    return {(r[0], r[1], r[2]) for r in rows}


# SHA-256 of contracts/schema.sql as frozen at wave 0, which 0001 copies byte for byte, taken
# again after the product's rename (it changed only the header comment). Applied migrations
# are never edited; contract amendments are new files.
FIRST_MIGRATION_SHA256 = "1397b7b4a049bb3de8b8af0f0ffea1f999ed7c999bc44ada34ad0cb4614f3fd8"


def test_first_migration_is_the_wave_0_contract_unchanged() -> None:
    assert hashlib.sha256(FIRST_MIGRATION.read_bytes()).hexdigest() == FIRST_MIGRATION_SHA256
    assert available_migrations()[0].version == 1


def test_all_migrations_build_exactly_the_contract_schema(conn: sqlite3.Connection) -> None:
    reference = sqlite3.connect(":memory:")
    reference.executescript(CONTRACT.read_text(encoding="utf-8"))
    assert schema_of(conn) == schema_of(reference)
    assert len(schema_of(conn)) > 20


def _insert_event(conn: sqlite3.Connection, kind: str, event_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO events (id, application_id, candidate_id, kind, actor, data, created_at)"
        " VALUES (?, NULL, 'cand_1', ?, 'system', '{\"n\": 1}', '2026-10-09T10:00:00Z')",
        (event_id, kind),
    )


def test_0002_keeps_existing_events_and_allows_system_turn(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = available_migrations()
    assert [m.version for m in real][:2] == [1, 2]
    db = connect(db_path)
    try:
        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:1])
        assert migrate(db) == [1]
        db.execute("INSERT INTO clients (id, name, created_at) VALUES ('c1', 'C', 'x')")
        db.execute(
            "INSERT INTO candidates (id, client_id, created_at, updated_at)"
            " VALUES ('cand_1', 'c1', 'x', 'x')"
        )
        _insert_event(db, "approved", 7)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_event(db, "system_turn")

        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real)
        assert 2 in migrate(db)
        rows = db.execute("SELECT id, kind, data FROM events").fetchall()
        assert [tuple(r) for r in rows] == [(7, "approved", '{"n": 1}')]
        _insert_event(db, "system_turn")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_event(db, "not_a_kind")
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
        assert {"events", "events_by_application", "events_by_kind"} <= names
        assert "events_before_0002" not in names
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()


def test_0003_adds_held_actions_to_a_version_2_database(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = available_migrations()
    assert [m.version for m in real][:3] == [1, 2, 3]
    db = connect(db_path)
    try:
        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:2])
        assert migrate(db) == [1, 2]
        before = schema_of(db)
        assert not any(name == "held_actions" for _, name, _ in before)
        db.execute("INSERT INTO clients (id, name, created_at) VALUES ('c1', 'C', 'x')")
        db.execute(
            "INSERT INTO candidates (id, client_id, created_at, updated_at)"
            " VALUES ('cand_1', 'c1', 'x', 'x')"
        )
        _insert_event(db, "approved", 7)

        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:3])
        assert migrate(db) == [3]
        # Only additions: every earlier object is untouched, existing rows are kept.
        assert before < schema_of(db)
        assert {n for t, n, _ in schema_of(db) - before} == {
            "held_actions",
            "held_actions_one_held",
            "held_actions_due",
        }
        assert db.execute("SELECT id FROM events").fetchall()[0][0] == 7
        assert db.execute("SELECT count(*) FROM held_actions").fetchone()[0] == 0
    finally:
        db.close()


def _plant_version_3(db: sqlite3.Connection) -> None:
    """A version-3 database whose events carry Candidate free text (written before Q38)."""
    db.execute("INSERT INTO clients (id, name, created_at) VALUES ('c1', 'C', 'x')")
    db.execute(
        "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
        " VALUES ('usr_1', 'c1', 'r@example.com', 'R', 'recruiter', 'h', 'x')"
    )
    db.execute(
        "INSERT INTO candidates (id, client_id, created_at, updated_at)"
        " VALUES ('cand_1', 'c1', 'x', 'x')"
    )
    db.execute(
        "INSERT INTO applications (id, candidate_id, client_id, channel, status, outcome,"
        " knockout_criterion, closed_at, created_at, updated_at) VALUES ('app_1', 'cand_1', 'c1',"
        " 'webchat', 'closed', 'knockout', 'zone', 'x', 'x', 'x')"
    )
    db.execute(
        "INSERT INTO messages (id, application_id, candidate_id, channel, direction, sender,"
        " body, created_at) VALUES (31, 'app_1', 'cand_1', 'webchat', 'outbound', 'agent',"
        " 'Gracias, Ana. MARKERKNOCK', 'x')"
    )
    knockout = {
        "reason": "outside_service_areas",
        "template": "knockout_zone.es-MX",
        "message": "Gracias, Ana. MARKERKNOCK",
        "criterion": "zone",
        "stage": "zone",
    }
    handback = {"summary": "Candidate: MARKERHANDBACK", "generated": True, "held_by": "usr_1"}
    planted = [
        (1, "answer_recorded", None, {"field": "zone", "value": "z1", "raw_input": "MARKERZONE"}),
        (2, "answer_invalid", None, {"field": "zone", "raw_input": "MARKERMISS", "attempts": 1}),
        (3, "answer_ambiguous", None, {"field": "zone", "options": [], "raw_input": "MARKERAMB"}),
        (4, "review_requested", None, {"queue_item_id": "q1", "reason": None, "note": "MARKERRV"}),
        (5, "escalation_raised", None, {"queue_item_id": "q2", "reason": "x", "note": "MARKERES"}),
        (6, "approval_requested", None, {"queue_item_id": "q3", "reason": None, "note": None}),
        (7, "withdrawn", None, {"note": "MARKERWD", "stage": "zone"}),
        (8, "erasure_requested", None, {"note": "MARKERERASE"}),
        (9, "knockout", None, knockout),
        (10, "knockout", None, {"criterion": "zone", "stage": "zone", "message": "MARKERUNSENT"}),
        (11, "takeover_handed_back", "usr_1", handback),
        (12, "takeover_handed_back", "usr_1", {"reason": "application_closed", "held_by": "usr_1"}),
        (13, "rejection", "usr_1", {"reason": "other", "note": "Recruiter note", "stage": "zone"}),
    ]
    for event_id, kind, user_id, data in planted:
        db.execute(
            "INSERT INTO events (id, application_id, candidate_id, kind, actor, user_id, data,"
            " created_at) VALUES (?, 'app_1', 'cand_1', ?, ?, ?, ?, '2026-10-09T10:00:00Z')",
            (event_id, kind, "user" if user_id else "agent", user_id, json.dumps(data)),
        )


def test_0004_and_0005_move_free_text_out_of_a_version_3_database(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = available_migrations()
    assert [m.version for m in real][:5] == [1, 2, 3, 4, 5]
    db = connect(db_path)
    try:
        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:3])
        assert migrate(db) == [1, 2, 3]
        before = schema_of(db)
        _plant_version_3(db)

        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:5])
        assert migrate(db) == [4, 5]
        # 0004 only adds the handbacks table; 0005 changes data, no schema object.
        assert before < schema_of(db)
        assert {n for _, n, _ in schema_of(db) - before} == {
            "handbacks",
            "handbacks_by_application",
        }
        data = {
            r["id"]: json.loads(r["data"])
            for r in db.execute("SELECT id, data FROM events ORDER BY id")
        }
        assert "MARKER" not in json.dumps(data, ensure_ascii=False)
        assert data[1] == {"field": "zone", "value": "z1"}
        assert data[2] == {"field": "zone", "attempts": 1}
        assert data[3] == {"field": "zone", "options": []}
        assert data[4] == {"queue_item_id": "q1", "reason": None}
        assert data[5] == {"queue_item_id": "q2", "reason": "x"}
        assert data[6] == {"queue_item_id": "q3", "reason": None}
        assert data[7] == {"stage": "zone"}
        assert data[8] == {}
        # The Knockout text matched a sent agent message: the event now points at it.
        assert data[9] == {
            "reason": "outside_service_areas",
            "template": "knockout_zone.es-MX",
            "criterion": "zone",
            "stage": "zone",
            "message_id": 31,
        }
        assert data[10] == {"criterion": "zone", "stage": "zone"}  # no match: just dropped
        # The hand-back summary moved to handbacks, under the event's id.
        assert data[11] == {"generated": True, "held_by": "usr_1", "handback_id": 11}
        handback = db.execute("SELECT * FROM handbacks WHERE id = 11").fetchone()
        assert (handback["application_id"], handback["user_id"]) == ("app_1", "usr_1")
        assert handback["summary"] == "Candidate: MARKERHANDBACK"
        assert handback["generated"] == 1
        assert db.execute("SELECT count(*) FROM handbacks").fetchone()[0] == 1
        assert data[12] == {"reason": "application_closed", "held_by": "usr_1"}
        # A Recruiter's Rejection note is not Candidate free text and stays.
        assert data[13]["note"] == "Recruiter note"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()


def test_0006_removes_names_from_answer_recorded_on_a_version_5_database(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = available_migrations()
    assert [m.version for m in real][:6] == [1, 2, 3, 4, 5, 6]
    db = connect(db_path)
    try:
        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:5])
        assert migrate(db) == [1, 2, 3, 4, 5]
        before = schema_of(db)
        db.execute("INSERT INTO clients (id, name, created_at) VALUES ('c1', 'C', 'x')")
        db.execute(
            "INSERT INTO candidates (id, client_id, full_name, created_at, updated_at)"
            " VALUES ('cand_1', 'c1', 'Ana Markername', 'x', 'x')"
        )
        db.execute(
            "INSERT INTO applications (id, candidate_id, client_id, channel, full_name,"
            " created_at, updated_at) VALUES ('app_1', 'cand_1', 'c1', 'webchat',"
            " 'Ana Markername', 'x', 'x')"
        )
        planted = [
            (1, "answer_recorded", {"field": "full_name", "value": "Ana Markername"}),
            (
                2,
                "answer_recorded",
                {"field": "full_name", "value": "Ana Markernew", "message_id": 4},
            ),
            (3, "answer_recorded", {"field": "full_name"}),  # already clean
            (4, "answer_recorded", {"field": "zone", "value": "z1", "locations": ["l1"]}),
            (5, "answer_recorded", {"field": "driver_license", "value": True}),
            (6, "answer_invalid", {"field": "full_name", "attempts": 1}),
            (7, "approved", {"value": "kept: not an answer_recorded event"}),
        ]
        for event_id, kind, data in planted:
            db.execute(
                "INSERT INTO events (id, application_id, candidate_id, kind, actor, data,"
                " created_at) VALUES (?, 'app_1', 'cand_1', ?, 'agent', ?,"
                " '2026-10-09T10:00:00Z')",
                (event_id, kind, json.dumps(data)),
            )

        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:6])
        assert migrate(db) == [6]
        assert schema_of(db) == before  # data only
        data = {
            r["id"]: json.loads(r["data"])
            for r in db.execute("SELECT id, data FROM events ORDER BY id")
        }
        assert "Markern" not in json.dumps(data)
        assert data[1] == {"field": "full_name"}
        assert data[2] == {"field": "full_name", "message_id": 4}
        assert data[3] == {"field": "full_name"}
        assert data[4] == {"field": "zone", "value": "z1", "locations": ["l1"]}
        assert data[5] == {"field": "driver_license", "value": True}
        assert data[6] == {"field": "full_name", "attempts": 1}
        assert data[7] == {"value": "kept: not an answer_recorded event"}
        # The name is still on the record.
        app = db.execute("SELECT full_name FROM applications WHERE id = 'app_1'").fetchone()
        assert app["full_name"] == "Ana Markername"
    finally:
        db.close()


def test_0008_adds_modality_and_voice_sessions_to_a_version_7_database(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = available_migrations()
    assert [m.version for m in real][:8] == [1, 2, 3, 4, 5, 6, 7, 8]
    db = connect(db_path)
    try:
        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:7])
        assert migrate(db) == [1, 2, 3, 4, 5, 6, 7]
        before = {(t, n): sql for t, n, sql in schema_of(db)}
        db.execute("INSERT INTO clients (id, name, created_at) VALUES ('c1', 'C', 'x')")
        db.execute(
            "INSERT INTO candidates (id, client_id, created_at, updated_at)"
            " VALUES ('cand_1', 'c1', 'x', 'x')"
        )
        db.execute(
            "INSERT INTO applications (id, candidate_id, client_id, channel, created_at,"
            " updated_at) VALUES ('app_1', 'cand_1', 'c1', 'webchat', 'x', 'x')"
        )
        db.execute(
            "INSERT INTO messages (id, application_id, candidate_id, channel, direction, sender,"
            " client_message_id, body, created_at) VALUES (1, 'app_1', 'cand_1', 'webchat',"
            " 'inbound', 'candidate', 'm1', 'Hola', 'x')"
        )
        db.execute(
            "INSERT INTO messages (id, application_id, candidate_id, channel, direction, sender,"
            " body, created_at) VALUES (2, 'app_1', 'cand_1', 'webchat', 'outbound', 'agent',"
            " 'Hola, soy el asistente', 'x')"
        )
        db.execute(
            "INSERT INTO message_signals (message_id, application_id, status, created_at)"
            " VALUES (1, 'app_1', 'ok', 'x')"
        )

        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:8])
        assert migrate(db) == [8]
        after = {(t, n): sql for t, n, sql in schema_of(db)}
        assert {n for t, n in after.keys() - before.keys()} == {
            "voice_sessions",
            "voice_sessions_by_conversation",
            "voice_sessions_live",
            "voice_sessions_by_elevenlabs",
        }
        assert not before.keys() - after.keys()
        assert {key[1] for key, sql in before.items() if after[key] != sql} == {"messages"}
        assert after[("index", "messages_idempotency")] == before[("index", "messages_idempotency")]

        rows = db.execute("SELECT id, modality FROM messages ORDER BY id").fetchall()
        assert [tuple(r) for r in rows] == [(1, "typed"), (2, "typed")]
        assert db.execute("SELECT message_id FROM message_signals").fetchall()[0][0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

        columns = [r["name"] for r in db.execute("PRAGMA table_info(voice_sessions)")]
        assert columns == [
            "id",
            "conversation_id",
            "candidate_id",
            "country",
            "elevenlabs_conversation_id",
            "started_at",
            "ended_at",
            "seconds",
        ]

        insert = (
            "INSERT INTO messages (application_id, candidate_id, channel, direction, sender,"
            " body, modality, created_at) VALUES ('app_1', 'cand_1', 'voice', 'outbound',"
            " 'agent', 'Vale', ?, 'x')"
        )
        db.execute(insert, ("spoken",))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(insert, ("sung",))
    finally:
        db.close()


def test_0010_adds_the_language_request_columns_and_keeps_the_signals(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = available_migrations()
    assert [m.version for m in real][:10] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    db = connect(db_path)
    try:
        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:9])
        assert migrate(db) == [1, 2, 3, 4, 5, 6, 7, 8, 9]
        db.execute("INSERT INTO clients (id, name, created_at) VALUES ('c1', 'C', 'x')")
        db.execute(
            "INSERT INTO candidates (id, client_id, created_at, updated_at)"
            " VALUES ('cand_1', 'c1', 'x', 'x')"
        )
        db.execute(
            "INSERT INTO applications (id, candidate_id, client_id, channel, created_at,"
            " updated_at) VALUES ('app_1', 'cand_1', 'c1', 'webchat', 'x', 'x')"
        )
        db.execute(
            "INSERT INTO messages (id, application_id, candidate_id, channel, direction, sender,"
            " client_message_id, body, created_at) VALUES (1, 'app_1', 'cand_1', 'webchat',"
            " 'inbound', 'candidate', 'm1', 'Hola', 'x')"
        )
        db.execute(
            "INSERT INTO message_signals (message_id, application_id, status, lang, created_at)"
            " VALUES (1, 'app_1', 'ok', 'es', 'x')"
        )

        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:10])
        assert migrate(db) == [10]
        old = db.execute("SELECT lang, asks, asks_confidence, language_reason FROM message_signals")
        assert tuple(old.fetchone()) == ("es", None, None, None)

        insert = (
            "INSERT INTO message_signals (message_id, application_id, status, asks,"
            " asks_confidence, language_reason, created_at)"
            " VALUES (?, 'app_1', 'ok', ?, ?, ?, 'x')"
        )

        def message(message_id: int) -> int:
            db.execute(
                "INSERT INTO messages (id, application_id, candidate_id, channel, direction,"
                " sender, client_message_id, body, created_at) VALUES (?, 'app_1', 'cand_1',"
                " 'webchat', 'inbound', 'candidate', ?, 'x', 'x')",
                (message_id, f"m{message_id}"),
            )
            return message_id

        good = [("en", 0.95, "request"), ("none", 1.0, "variant"), (None, None, None)]
        for message_id, values in enumerate(good, start=2):
            db.execute(insert, (message(message_id), *values))
        bad = [("fr", 0.5, None), ("es", 1.5, None), ("es", 0.5, "guess")]
        for message_id, values in enumerate(bad, start=10):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(insert, (message(message_id), *values))
    finally:
        db.close()


def test_the_unreadable_migration_keeps_queue_items_and_adds_the_reason_and_the_column(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = available_migrations()
    index = next(i for i, m in enumerate(real) if m.name == "unreadable")
    db = connect(db_path)
    try:
        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:index])
        migrate(db)
        before = {(t, n): sql for t, n, sql in schema_of(db)}
        db.execute("INSERT INTO clients (id, name, created_at) VALUES ('c1', 'C', 'x')")
        db.execute(
            "INSERT INTO candidates (id, client_id, created_at, updated_at)"
            " VALUES ('cand_1', 'c1', 'x', 'x')"
        )
        db.execute(
            "INSERT INTO applications (id, candidate_id, client_id, channel, created_at,"
            " updated_at) VALUES ('app_1', 'cand_1', 'c1', 'webchat', 'x', 'x')"
        )
        db.execute(
            "INSERT INTO queue_items (id, application_id, kind, status, reason, note, resolution,"
            " resolved_at, created_at) VALUES ('qi_1', 'app_1', 'escalation', 'resolved',"
            " 'wants_human', 'n', 'dismissed', 'y', 'x')"
        )
        db.execute(
            "INSERT INTO queue_items (id, application_id, kind, reason, created_at)"
            " VALUES ('qi_2', 'app_1', 'escalation', 'abuse', 'x')"
        )
        db.execute(
            "INSERT INTO messages (id, application_id, candidate_id, channel, direction, sender,"
            " client_message_id, body, created_at) VALUES (1, 'app_1', 'cand_1', 'webchat',"
            " 'inbound', 'candidate', 'm1', 'Hola', 'x')"
        )
        db.execute(
            "INSERT INTO message_signals (message_id, application_id, status, created_at)"
            " VALUES (1, 'app_1', 'ok', 'x')"
        )

        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[: index + 1])
        assert migrate(db) == [real[index].version]
        after = {(t, n): sql for t, n, sql in schema_of(db)}
        assert after.keys() == before.keys()
        assert {key[1] for key, sql in before.items() if after[key] != sql} == {
            "queue_items",
            "message_signals",
        }
        rows = db.execute("SELECT id, status, reason, note FROM queue_items ORDER BY id").fetchall()
        assert [tuple(r) for r in rows] == [
            ("qi_1", "resolved", "wants_human", "n"),
            ("qi_2", "open", "abuse", None),
        ]
        with pytest.raises(sqlite3.IntegrityError):  # still one open item of each kind
            db.execute(
                "INSERT INTO queue_items (id, application_id, kind, reason, created_at)"
                " VALUES ('qi_3', 'app_1', 'escalation', 'unreadable', 'x')"
            )
        db.execute("UPDATE queue_items SET reason = 'unreadable' WHERE id = 'qi_2'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE queue_items SET reason = 'gibberish' WHERE id = 'qi_2'")
        assert db.execute("SELECT unreadable FROM message_signals").fetchone()[0] is None
        db.execute("UPDATE message_signals SET unreadable = 0.92")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE message_signals SET unreadable = 1.5")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()


def test_rerunning_migrations_is_idempotent(db_path: Path, conn: sqlite3.Connection) -> None:
    before = schema_of(conn)
    assert migrate(conn) == []
    again = connect(db_path)
    try:
        assert migrate(again) == []
        assert schema_of(again) == before
        versions = [r[0] for r in again.execute("SELECT version FROM schema_migrations")]
        assert versions == [m.version for m in available_migrations()]
    finally:
        again.close()


def test_every_connection_enforces_foreign_keys_in_wal_mode(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO locations (id, client_id, name, country, city, timezone, created_at)"
            " VALUES ('loc_x', 'no_such_client', 'X', 'ES', 'Madrid', 'Europe/Madrid', 'now')"
        )


def test_a_failing_migration_is_rolled_back_whole(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = Migration(9999, "broken", "CREATE TABLE half_done (x INTEGER);\nNOT SQL;")
    real = available_migrations()
    monkeypatch.setattr(migrate_module, "available_migrations", lambda: [*real, broken])
    with pytest.raises(sqlite3.Error):
        migrate(conn)
    assert not conn.in_transaction
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "half_done" not in tables
    assert conn.execute("SELECT 1 FROM schema_migrations WHERE version = 9999").fetchone() is None


def test_cli_migrates_a_new_database(tmp_path: Path) -> None:
    path = tmp_path / "cli.db"
    assert migrate_module.main([str(path)]) == 0
    assert migrate_module.main([str(path)]) == 0
    check = connect(path)
    try:
        assert check.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == len(
            available_migrations()
        )
    finally:
        check.close()


def test_0009_adds_client_and_persona_id_to_a_version_8_database(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = available_migrations()
    assert [m.version for m in real][:9] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    db = connect(db_path)
    try:
        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:8])
        assert migrate(db) == [1, 2, 3, 4, 5, 6, 7, 8]
        before = {(t, n): sql for t, n, sql in schema_of(db)}
        db.execute("INSERT INTO clients (id, name, created_at) VALUES ('c1', 'C', 'x')")
        db.execute(
            "INSERT INTO candidates (id, client_id, created_at, updated_at)"
            " VALUES ('cand_1', 'c1', 'x', 'x')"
        )
        db.execute(
            "INSERT INTO candidate_identities (candidate_id, channel, external_id, created_at)"
            " VALUES ('cand_1', 'webchat', 'wc_old', 'x')"
        )

        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real[:9])
        assert migrate(db) == [9]
        after = {(t, n): sql for t, n, sql in schema_of(db)}
        assert after.keys() == before.keys()
        assert {key[1] for key, sql in before.items() if after[key] != sql} == {
            "candidate_identities"
        }

        columns = [r["name"] for r in db.execute("PRAGMA table_info(candidate_identities)")]
        assert columns == [
            "candidate_id",
            "channel",
            "external_id",
            "created_at",
            "client",
            "persona_id",
        ]
        row = db.execute(
            "SELECT client, persona_id FROM candidate_identities WHERE external_id = 'wc_old'"
        ).fetchone()
        assert tuple(row) == (None, None)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()


def test_0009_checks_hold_codes_only(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO clients (id, name, created_at) VALUES ('c1', 'C', 'x')")
    conn.execute(
        "INSERT INTO candidates (id, client_id, created_at, updated_at)"
        " VALUES ('cand_1', 'c1', 'x', 'x')"
    )
    insert = (
        "INSERT INTO candidate_identities (candidate_id, channel, external_id, created_at,"
        " client, persona_id) VALUES ('cand_1', 'webchat', ?, 'x', ?, ?)"
    )
    for n, bad in enumerate(["Bad Id!", "a" * 65, "", "con-guion", "Mayus"]):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (f"wc_bad{n}", "demo", bad))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("wc_other", "other", None))

    conn.execute(insert, ("wc_ok", "demo", "diego_es"))
    conn.execute(insert, ("wc_max", "test_chat", "a" * 64))
    conn.execute(insert, ("wc_null", None, None))
    stored = conn.execute(
        "SELECT external_id, client, persona_id FROM candidate_identities ORDER BY external_id"
    ).fetchall()
    assert [tuple(r) for r in stored] == [
        ("wc_max", "test_chat", "a" * 64),
        ("wc_null", None, None),
        ("wc_ok", "demo", "diego_es"),
    ]


def test_0012_adds_tool_calls_and_keeps_the_applications(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = available_migrations()
    before = [m for m in real if m.version < 12]
    assert 12 in [m.version for m in real]
    db = connect(db_path)
    try:
        monkeypatch.setattr(migrate_module, "available_migrations", lambda: before)
        assert migrate(db) == [m.version for m in before]
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "tool_calls" not in tables
        db.execute("INSERT INTO clients (id, name, created_at) VALUES ('c1', 'C', 'x')")
        db.execute(
            "INSERT INTO candidates (id, client_id, created_at, updated_at)"
            " VALUES ('cand_1', 'c1', 'x', 'x')"
        )
        db.execute(
            "INSERT INTO applications (id, candidate_id, client_id, channel, created_at,"
            " updated_at) VALUES ('app_1', 'cand_1', 'c1', 'webchat', 'x', 'x')"
        )

        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real)
        assert 12 in migrate(db)
        assert db.execute("SELECT id FROM applications").fetchall()[0][0] == "app_1"
        assert db.execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"tool_calls_by_application", "tool_calls_by_run", "tool_calls_by_ts"} <= names
    finally:
        db.close()


def test_tool_calls_checks_defaults_and_cascade(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO clients (id, name, created_at) VALUES ('c1', 'C', 'x')")
    conn.execute(
        "INSERT INTO candidates (id, client_id, created_at, updated_at)"
        " VALUES ('cand_1', 'c1', 'x', 'x')"
    )
    conn.execute(
        "INSERT INTO applications (id, candidate_id, client_id, channel, created_at,"
        " updated_at) VALUES ('app_1', 'cand_1', 'c1', 'webchat', 'x', 'x')"
    )
    insert = (
        "INSERT INTO tool_calls (application_id, run_closed, tool, started_at, duration_ms,"
        " outcome, created_at) VALUES (?, ?, 'record_answer', 'x', ?, ?, 'x')"
    )
    for application_id, closed, duration, outcome in [
        ("app_1", 0, 5, "other"),
        ("app_1", 0, -1, "ok"),
        ("app_1", 2, 5, "ok"),
        ("app_missing", 0, 5, "ok"),
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (application_id, closed, duration, outcome))

    for outcome in ("ok", "invalid", "knockout", "error"):
        conn.execute(insert, ("app_1", 0, 0, outcome))
    row = conn.execute("SELECT args, run_closed, run_id, error FROM tool_calls LIMIT 1").fetchone()
    assert tuple(row) == ("{}", 0, None, None)
    assert conn.execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 4

    conn.execute("DELETE FROM applications WHERE id = 'app_1'")
    assert conn.execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 0


def test_0015_adds_owed_turns_and_keeps_the_messages(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = available_migrations()
    before = [m for m in real if m.version < 15]
    assert 15 in [m.version for m in real]
    db = connect(db_path)
    try:
        monkeypatch.setattr(migrate_module, "available_migrations", lambda: before)
        migrate(db)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "owed_turns" not in tables
        monkeypatch.setattr(migrate_module, "available_migrations", lambda: real)
        assert 15 in migrate(db)
        assert db.execute("SELECT count(*) FROM owed_turns").fetchone()[0] == 0
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"owed_turns_by_application", "owed_turns_due"} <= names
    finally:
        db.close()


def test_owed_turns_checks_and_cascade_with_its_message(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO clients (id, name, created_at) VALUES ('c1', 'C', 'x')")
    conn.execute(
        "INSERT INTO candidates (id, client_id, created_at, updated_at)"
        " VALUES ('cand_1', 'c1', 'x', 'x')"
    )
    conn.execute(
        "INSERT INTO applications (id, candidate_id, client_id, channel, created_at,"
        " updated_at) VALUES ('app_1', 'cand_1', 'c1', 'webchat', 'x', 'x')"
    )
    conn.execute(
        "INSERT INTO messages (id, application_id, candidate_id, channel, direction, sender,"
        " body, created_at) VALUES (7, 'app_1', 'cand_1', 'webchat', 'inbound', 'candidate',"
        " 'hola', 'x')"
    )
    insert = (
        "INSERT INTO owed_turns (message_id, application_id, channel, turn_input,"
        " idempotency_key, status, next_attempt_at, created_at, updated_at)"
        " VALUES (?, 'app_1', ?, 'hola', 'm1', ?, 'x', 'x', 'x')"
    )
    for message_id, channel, status in [
        (7, "voice", "owed"),
        (7, "webchat", "done"),
        (8, "webchat", "owed"),
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (message_id, channel, status))
    conn.execute(insert, (7, "webchat", "owed"))
    row = conn.execute("SELECT attempts, holding_sent, run_input, typed FROM owed_turns").fetchone()
    assert tuple(row) == (0, 0, None, 0)

    conn.execute("DELETE FROM messages WHERE id = 7")
    assert conn.execute("SELECT count(*) FROM owed_turns").fetchone()[0] == 0
