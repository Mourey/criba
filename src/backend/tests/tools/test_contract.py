"""The vendored contract copy, the tool registry, the runtime args validator (checked against
jsonschema), and the Knockout templates (ADR 0016)."""

from __future__ import annotations

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from criba.tools import TOOLS, knockout_message
from criba.tools.knockout import REQUIREMENTS_URL, TEMPLATES
from criba.tools.schema import (
    answer_value_schema,
    check_value,
    contract,
    tool_names,
    validate_args,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_vendored_contract_matches_contracts_tools_json() -> None:
    vendored = REPO_ROOT / "src/backend/criba/tools/tools.json"
    assert vendored.read_bytes() == (REPO_ROOT / "contracts/tools.json").read_bytes(), (
        "copy contracts/tools.json to src/backend/criba/tools/tools.json"
    )


def test_every_contract_tool_is_implemented() -> None:
    assert set(TOOLS) == set(tool_names())
    assert set(tool_names()) == set(contract()["$defs"]["tool_name"]["enum"])


def _jsonschema_ok(tool: str, args) -> bool:
    ref = contract()["x-tools"][tool]["input"]
    validator = Draft202012Validator({"$defs": contract()["$defs"], "$ref": ref})
    return validator.is_valid(args)


CASES: list[tuple[str, object]] = [
    ("get_application", {}),
    ("get_application", {"x": 1}),
    ("get_application", []),
    ("get_application", None),
    ("record_answer", {"field": "zone", "value": "Centro"}),
    ("record_answer", {"field": "zone", "value": ["a", "b"]}),
    ("record_answer", {"field": "zone", "value": []}),
    ("record_answer", {"field": "zone", "value": [""]}),
    ("record_answer", {"field": "zone", "value": ["a"] * 11}),
    ("record_answer", {"field": "zone", "value": None}),
    ("record_answer", {"field": "zone", "value": 5}),
    ("record_answer", {"field": "zone"}),
    ("record_answer", {"field": "nope", "value": "x"}),
    ("record_answer", {"field": "zone", "value": "x", "application_id": "a"}),
    ("request_review", {}),
    ("request_review", {"note": "x" * 1000}),
    ("request_review", {"note": "x" * 1001}),
    ("raise_escalation", {"reason": "abuse"}),
    ("raise_escalation", {"reason": "abuse", "note": None}),
    ("raise_escalation", {}),
    ("finish_screening", {}),
    ("finish_screening", {"confirm": True}),
    ("record_backout", {"kind": "opted_out"}),
    ("record_backout", {"kind": "unsubscribe"}),
    ("offer_interview_slots", {}),
    ("offer_interview_slots", {"reschedule": True}),
    ("offer_interview_slots", {"reschedule": 1}),
    ("book_interview", {"slot_ref": "so_1"}),
    ("book_interview", {"slot_ref": ""}),
    ("book_interview", {"slot_ref": 7}),
    # ADR 0042: language on get_application, search_zones, structured values and null.
    ("get_application", {"language": "en"}),
    ("get_application", {"language": "es"}),
    ("search_zones", {"query": "Chamberí"}),
    ("search_zones", {"query": "Centro", "country": "MX"}),
    ("search_zones", {"query": ""}),
    ("search_zones", {"query": "x" * 101}),
    ("search_zones", {"query": "Centro", "country": "FR"}),
    ("search_zones", {}),
    ("record_answer", {"field": "zone", "value": ["zone_mx_puebla_centro"]}),
    ("record_answer", {"field": "zone", "value": ["zone_a", "zone_a"]}),
    ("record_answer", {"field": "driver_license", "value": "expired"}),
    ("record_answer", {"field": "experience_years", "value": 0.5}),
    ("record_answer", {"field": "experience_years", "value": -1}),
    ("record_answer", {"field": "experience_years", "value": True}),
    ("record_answer", {"field": "experience_platforms", "value": []}),
    ("record_answer", {"field": "start_date", "value": "2026-10-12"}),
    ("record_answer", {"field": "start_date", "value": None}),
    ("record_answer", {"field": "start_date", "value": {"date": "2026-10-12"}}),
    ("record_answer", {"field": "availability", "value": [1, 2]}),
]


@pytest.mark.parametrize(("tool", "args"), CASES)
def test_runtime_validator_agrees_with_jsonschema(tool: str, args) -> None:
    assert (validate_args(tool, args) is None) == _jsonschema_ok(tool, args)


# Every per-field value schema (``x-answer-values``), with values that should pass and fail.
VALUE_CASES: list[tuple[str, object]] = [
    ("full_name", "Ana García"),
    ("full_name", "Al"),
    ("full_name", "x" * 81),
    ("full_name", None),
    ("zone", ["zone_es_madrid_chamberi"]),
    ("zone", ["zone_a"] * 2),
    ("zone", [f"zone_{i}" for i in range(11)]),
    ("zone", []),
    ("zone", ["Chamberí"]),
    ("zone", "zone_es_madrid_chamberi"),
    ("driver_license", "yes"),
    ("driver_license", "pending"),
    ("driver_license", "sí"),
    ("driver_license", True),
    ("availability", ["full_time", "weekends"]),
    ("availability", []),
    ("availability", ["full_time", "full_time"]),
    ("availability", ["tiempo completo"]),
    ("availability", "full_time"),
    ("preferred_schedule", ["flexible"]),
    ("preferred_schedule", ["night"]),
    ("experience_years", 0),
    ("experience_years", 1.5),
    ("experience_years", 61),
    ("experience_years", -0.5),
    ("experience_years", "2"),
    ("experience_years", False),
    ("experience_platforms", []),
    ("experience_platforms", ["glovo", "other"]),
    ("experience_platforms", ["deliveroo"]),
    ("start_date", "2026-10-12"),
    ("start_date", "2026-02-30"),
    ("start_date", "20/10/2026"),
    ("start_date", "el lunes"),
    ("start_date", "ahora"),
    ("start_date", 20261012),
]


@pytest.mark.parametrize(("field", "value"), VALUE_CASES)
def test_value_schemas_agree_with_jsonschema(field: str, value) -> None:
    ref = contract()["x-answer-values"][field]
    validator = Draft202012Validator({"$defs": contract()["$defs"], "$ref": ref})
    assert (check_value(value, answer_value_schema(field)) is None) == validator.is_valid(value)


def test_every_field_has_a_value_schema_in_the_value_any_of() -> None:
    fields = contract()["$defs"]["field"]["enum"]
    assert list(contract()["x-answer-values"]) == fields
    forms = contract()["$defs"]["record_answer_input"]["properties"]["value"]["anyOf"]
    refs = [f.get("$ref") for f in forms]
    assert [contract()["x-answer-values"][f] for f in fields] == refs[: len(fields)]
    assert {"type": "null"} in forms


@pytest.mark.parametrize(
    ("schema", "value"),
    [
        ({"type": "number", "maximum": 60}, 60),
        ({"type": "number", "maximum": 60}, 60.5),
        ({"anyOf": [{"type": "integer"}, {"type": "null"}]}, None),
        ({"anyOf": [{"type": "integer"}, {"type": "null"}]}, "x"),
        ({"type": "string", "pattern": "^zone_"}, "zone_x"),
        ({"type": "string", "pattern": "^zone_"}, "a zone_x"),
        ({"type": "string", "pattern": "[0-9]"}, "abc1"),
    ],
)
def test_interpreter_keywords_agree_with_jsonschema(schema, value) -> None:
    assert (check_value(value, schema) is None) == Draft202012Validator(schema).is_valid(value)


def test_no_tool_input_has_identity_properties() -> None:
    for tool in tool_names():
        ref = contract()["x-tools"][tool]["input"]
        props = contract()["$defs"][ref.rsplit("/", 1)[1]].get("properties", {})
        assert not {"application_id", "candidate_id"} & set(props)


# --- Knockout templates -----------------------------------------------------


@pytest.mark.parametrize("language", ["es-ES", "es-MX", "en"])
@pytest.mark.parametrize(
    ("criterion", "reason"),
    [
        ("zone", "outside_service_areas"),
        ("driver_license", "no"),
        ("driver_license", "expired"),
        ("driver_license", "suspended"),
        ("driver_license", "pending"),
        ("driver_license", "something_new"),
    ],
)
def test_knockout_message_has_the_adr_0016_parts(criterion, reason, language) -> None:
    template, text = knockout_message(
        criterion, reason, language, first_name="Ana", zone="Milpa Alta"
    )
    assert template.endswith(f".{language}")
    assert template.startswith(f"knockout_{criterion}")
    assert REQUIREMENTS_URL in text  # link to the requirements
    review_word = '"review"' if language == "en" else '"revisión"'
    assert review_word in text  # how to ask for a person
    assert ("legal" in text) or criterion == "zone"  # why the requirement exists
    assert "{" not in text and "}" not in text
    assert "Ana" in text
    if criterion == "zone":
        assert "Milpa Alta" in text


def test_knockout_without_a_name_or_zone() -> None:
    _, text = knockout_message("zone", "outside_service_areas", "es-ES")
    assert text.startswith("Gracias. ")
    assert "tu zona" in text


def test_every_language_has_every_variant() -> None:
    variants = {frozenset(t) for t in TEMPLATES.values()}
    assert len(variants) == 1
