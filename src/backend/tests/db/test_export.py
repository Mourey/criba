"""The Application JSON export matches the Phase 2 field list
(``ApplicationExport`` in contracts/api.openapi.yaml)."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from criba.db import repo
from criba.db.repo import NotFound
from criba.db.seed import CLIENT_ID

T0 = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 9, 10, 4, tzinfo=UTC)
OPENAPI = Path(__file__).resolve().parents[4] / "contracts" / "api.openapi.yaml"


@pytest.fixture(scope="module")
def schemas() -> dict[str, Any]:
    return yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))["components"]["schemas"]


def qualified_application(conn: sqlite3.Connection) -> str:
    candidate, _ = repo.get_or_create_candidate(
        conn,
        client_id=CLIENT_ID,
        channel="webchat",
        external_id="conv-luis",
        phone="+525512345678",
        language="es-MX",
        now=T0,
    )
    app, _ = repo.resolve_application(conn, candidate["id"], "webchat", now=T0)
    answers = [
        ("full_name", "Luis Hernández"),
        ("zone", "zone_mx_puebla_centro"),
        ("driver_license", True),
        ("availability", ["full_time"]),
        ("preferred_schedule", ["morning", "afternoon"]),
        ("experience_years", 2),
        ("experience_platforms", ["rappi", "didi_food"]),
        ("start_date", "2026-10-12"),
    ]
    for field, value in answers:
        repo.record_answer(conn, app["id"], field, value, now=T0)
    repo.set_stage(conn, app["id"], "confirm", now=T0)
    repo.mark_qualified(conn, app["id"], now=T1)
    return app["id"]


def assert_matches(value: Any, schema: dict[str, Any], schemas: dict[str, Any]) -> None:
    """A small structural check: required keys, no extra keys, and leaf types."""
    if "$ref" in schema:
        schema = schemas[schema["$ref"].rsplit("/", 1)[1]]
    if "oneOf" in schema:
        errors = []
        for option in schema["oneOf"]:
            try:
                assert_matches(value, option, schemas)
                return
            except AssertionError as exc:
                errors.append(str(exc))
        raise AssertionError(f"{value!r} matches no option: {errors}")
    if "const" in schema:
        assert value == schema["const"]
        return
    types = schema.get("type")
    types = types if isinstance(types, list) else [types] if types else []
    if value is None:
        assert "null" in types, f"null not allowed by {schema}"
        return
    if "enum" in schema:
        assert value in schema["enum"], f"{value!r} not in {schema['enum']}"
    if "object" in types:
        assert isinstance(value, dict)
        props = schema.get("properties", {})
        assert set(schema.get("required", [])) <= set(value), set(schema["required"]) - set(value)
        if props:
            assert set(value) <= set(props), set(value) - set(props)
        for key, sub in props.items():
            if key in value:
                assert_matches(value[key], sub, schemas)
    elif "array" in types:
        assert isinstance(value, list)
        for item in value:
            assert_matches(item, schema.get("items", {}), schemas)
    elif "string" in types:
        assert isinstance(value, str)
    elif "boolean" in types:
        assert isinstance(value, bool)
    elif "number" in types:
        assert isinstance(value, int | float) and not isinstance(value, bool)
    elif "integer" in types:
        assert isinstance(value, int) and not isinstance(value, bool)


def test_export_of_a_qualified_application_matches_the_contract(
    seeded: sqlite3.Connection, schemas: dict[str, Any]
) -> None:
    app_id = qualified_application(seeded)
    exported = repo.export_application(seeded, app_id)
    assert_matches(exported, schemas["ApplicationExport"], schemas)
    assert set(exported) == set(schemas["ApplicationExport"]["properties"])
    assert json.loads(json.dumps(exported)) == exported

    assert exported["application_id"] == app_id
    assert exported["status"] == "open" and exported["stage"] == "qualified"
    assert exported["qualified_at"] == "2026-10-09T10:04:00Z"
    assert exported["answers"] == {
        "full_name": "Luis Hernández",
        "zone": {
            "id": "zone_mx_puebla_centro",
            "name": "Centro",
            "city": "Puebla",
            "country": "MX",
        },
        "driver_license": True,
        "availability": ["full_time"],
        "preferred_schedule": ["morning", "afternoon"],
        "experience_years": 2,
        "experience_platforms": ["rappi", "didi_food"],
        "start_date": "2026-10-12",
    }
    assert "loc_mx_puebla_centro" in exported["locations"]
    assert exported["start_date_far"] is False
    assert exported["interview"] is None and exported["summary"] is None
    assert "cost" not in json.dumps(exported)


def test_export_of_a_fresh_application_has_every_field_null(
    seeded: sqlite3.Connection, schemas: dict[str, Any]
) -> None:
    candidate, _ = repo.get_or_create_candidate(
        seeded, client_id=CLIENT_ID, channel="webchat", external_id="conv-new", now=T0
    )
    app, _ = repo.resolve_application(seeded, candidate["id"], "webchat", now=T0)
    exported = repo.export_application(seeded, app["id"])
    assert_matches(exported, schemas["ApplicationExport"], schemas)
    assert set(exported["answers"].values()) == {None}
    assert exported["locations"] == []


def test_export_includes_interview_and_summary(
    seeded: sqlite3.Connection, schemas: dict[str, Any]
) -> None:
    app_id = qualified_application(seeded)
    seeded.execute(
        "INSERT INTO interview_slots (id, location_id, starts_at, ends_at, created_at)"
        " VALUES ('slot_t', 'loc_mx_puebla_centro', '2026-10-14T16:00:00Z',"
        " '2026-10-14T16:30:00Z', 'x')"
    )
    seeded.execute(
        "INSERT INTO bookings (id, application_id, slot_id, booked_via, ics_uid, created_at)"
        " VALUES ('bk_t', ?, 'slot_t', 'chat', 'uid', 'x')",
        (app_id,),
    )
    repo.set_summary(seeded, app_id, {"result": "qualified", "nudges": 0}, "Luis is ready.", now=T1)
    exported = repo.export_application(seeded, app_id)
    assert_matches(exported, schemas["ApplicationExport"], schemas)
    assert exported["interview"] == {
        "starts_at": "2026-10-14T16:00:00Z",
        "ends_at": "2026-10-14T16:30:00Z",
        "local_label": "mié 14 · 10:00",
        "location_name": "Sazón Puebla Centro",
        "address": exported["interview"]["address"],
    }
    assert exported["summary"] == {
        "structured": {"result": "qualified", "nudges": 0},
        "prose": "Luis is ready.",
        "written_at": "2026-10-09T10:04:00Z",
    }


def test_export_of_a_knockout(seeded: sqlite3.Connection, schemas: dict[str, Any]) -> None:
    candidate, _ = repo.get_or_create_candidate(
        seeded, client_id=CLIENT_ID, channel="webchat", external_id="conv-ko", now=T0
    )
    app, _ = repo.resolve_application(seeded, candidate["id"], "webchat", now=T0)
    repo.record_answer(seeded, app["id"], "driver_license", False, raw_input="no", now=T0)
    repo.close_application(
        seeded, app["id"], "knockout", knockout_criterion="driver_license", now=T1
    )
    exported = repo.export_application(seeded, app["id"])
    assert_matches(exported, schemas["ApplicationExport"], schemas)
    assert exported["outcome"] == "knockout"
    assert exported["knockout_criterion"] == "driver_license"
    assert exported["answers"]["driver_license"] is False
    assert exported["closed_at"] == "2026-10-09T10:04:00Z"


def test_local_label_follows_language() -> None:
    assert repo.local_label("2026-10-14T16:00:00Z", "America/Mexico_City", "en") == (
        "Wed 14 · 10:00"
    )
    assert repo.local_label("2026-10-14T08:00:00Z", "Europe/Madrid", "es-ES") == "mié 14 · 10:00"


def test_export_of_unknown_application_is_not_found(seeded: sqlite3.Connection) -> None:
    with pytest.raises(NotFound):
        repo.export_application(seeded, "app_missing")
