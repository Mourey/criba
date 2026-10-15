"""The criba-tools plugin registers exactly the tools in contracts/tools.json, with schemas a
model can use and none that lets the model name an Application (ADR 0026, spike 2).
"""

from __future__ import annotations

import inspect
import json
from types import ModuleType
from typing import Any

import jsonschema
import pytest

IDENTITY_NAMES = ("application_id", "candidate_id")


class FakeContext:
    """Records what a plugin registers, like the ``ctx`` Hermes passes to ``register``."""

    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = []

    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)


@pytest.fixture
def registered(plugin: ModuleType) -> list[dict[str, Any]]:
    ctx = FakeContext()
    plugin.register(ctx)
    return ctx.tools


def test_registers_every_contract_tool_in_contract_order(
    registered: list[dict[str, Any]], contract: dict[str, Any]
) -> None:
    assert [t["name"] for t in registered] == list(contract["x-tools"])
    assert set(contract["x-tools"]) == set(contract["$defs"]["tool_name"]["enum"])
    assert len(registered) == 9
    assert "search_zones" in [t["name"] for t in registered]


def test_all_tools_share_the_criba_tools_toolset(registered: list[dict[str, Any]]) -> None:
    assert {t["toolset"] for t in registered} == {"criba-tools"}


def test_no_tool_schema_contains_application_id(registered: list[dict[str, Any]]) -> None:
    """The model obeys an injected id (spike 2, finding 7), so no schema may offer one: not as
    a property, not in a description, not anywhere in the serialised schema."""
    for tool in registered:
        text = json.dumps(tool["schema"], ensure_ascii=False)
        for name in IDENTITY_NAMES:
            assert name not in text, f"{tool['name']} schema mentions {name}"
        assert "application id" not in text.lower()


def test_identity_check_actually_catches_a_bad_contract(contract_module: ModuleType) -> None:
    """The guard above is meaningless if the loader would silently accept an id property."""
    bad = {
        "$defs": {
            "evil_input": {
                "description": "Takes an id.",
                "type": "object",
                "properties": {"application_id": {"type": "string"}},
            }
        },
        "x-tools": {"evil": {"input": "#/$defs/evil_input"}},
    }
    with pytest.raises(contract_module.ContractError, match="application_id"):
        contract_module.tool_specs(bad)


def test_schemas_are_self_contained(registered: list[dict[str, Any]]) -> None:
    for tool in registered:
        text = json.dumps(tool["schema"])
        assert "$ref" not in text, f"{tool['name']} still has a $ref"
        assert "$defs" not in text
        assert "examples" not in text, "examples cost tokens on every turn"


def test_schema_shape_is_what_hermes_expects(registered: list[dict[str, Any]]) -> None:
    for tool in registered:
        schema = tool["schema"]
        assert set(schema) == {"name", "description", "parameters"}
        assert schema["name"] == tool["name"]
        assert len(schema["description"]) > 20
        params = schema["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)
        assert params.get("additionalProperties") is False
        jsonschema.Draft202012Validator.check_schema(params)


def test_description_and_properties_come_from_the_contract(
    registered: list[dict[str, Any]], contract: dict[str, Any]
) -> None:
    for tool in registered:
        source = contract["$defs"][f"{tool['name']}_input"]
        assert tool["schema"]["description"] == source["description"].strip()
        assert set(tool["schema"]["parameters"]["properties"]) == set(source["properties"])
        assert tool["schema"]["parameters"].get("required", []) == source.get("required", [])


def test_inlined_field_enum_matches_the_contract(
    registered: list[dict[str, Any]], contract: dict[str, Any]
) -> None:
    record = next(t for t in registered if t["name"] == "record_answer")
    field = record["schema"]["parameters"]["properties"]["field"]
    assert field["enum"] == contract["$defs"]["field"]["enum"]


@pytest.mark.parametrize(
    "tool_name", ["record_answer", "book_interview", "raise_escalation", "search_zones"]
)
def test_contract_examples_validate_against_the_inlined_schema(
    registered: list[dict[str, Any]], contract: dict[str, Any], tool_name: str
) -> None:
    """An example the contract blesses must be accepted by the schema the model is given."""
    tool = next(t for t in registered if t["name"] == tool_name)
    examples = contract["$defs"][f"{tool_name}_input"]["examples"]
    assert examples
    for example in examples:
        jsonschema.validate(example, tool["schema"]["parameters"])


def test_a_model_supplied_application_id_is_rejected_by_the_schema(
    registered: list[dict[str, Any]],
) -> None:
    record = next(t for t in registered if t["name"] == "record_answer")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"field": "zone", "value": "Centro", "application_id": "app_VICTIM"},
            record["schema"]["parameters"],
        )


def test_size_limits_stay_in_the_contract_and_out_of_the_prompt(
    registered: list[dict[str, Any]], contract: dict[str, Any]
) -> None:
    """The backend checks every limit again (ADR 0042), so the model is not sent them; what a
    value is (types, enums, the zone id pattern, additionalProperties) still is."""
    sent = json.dumps([t["schema"] for t in registered])
    for keyword in ("minLength", "maxLength", "minItems", "maxItems", "uniqueItems", "minimum"):
        assert f'"{keyword}"' not in sent, keyword
        assert f'"{keyword}"' in json.dumps(contract), f"the contract lost {keyword}"
    record = next(t for t in registered if t["name"] == "record_answer")
    assert '"^zone_"' in json.dumps(record["schema"])
    assert record["schema"]["parameters"]["additionalProperties"] is False
    # Looser than the contract, never wrong about a type: a long note still reaches the backend.
    jsonschema.validate(
        {"kind": "withdrawn", "note": "x" * 5000}, _params(registered, "record_backout")
    )


def _params(registered: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(t for t in registered if t["name"] == name)["schema"]["parameters"]


def test_tool_schemas_stay_inside_the_token_budget(registered: list[dict[str, Any]]) -> None:
    """Spikes 1 and 2: the whole turn must stay near 10K input tokens, and the tool schemas are
    the part this lane controls. At 4 characters a token, 8 tools measured about 1.1K tokens."""
    chars = len(json.dumps([t["schema"] for t in registered], ensure_ascii=False))
    assert chars / 4 < 2000, f"tool schemas are about {chars // 4} tokens"
    # Turn size (docs/evidence/turn-size.md): the real model counts about 1.5 tokens for
    # every 4 characters of this JSON, and the schemas ride on every call. Long descriptions
    # belong in the contract's `$comment`, which the plugin strips.
    assert chars <= 5_000, f"tool schemas are {chars} characters"
    assert "$comment" not in json.dumps([t["schema"] for t in registered])


# The 8 tool schemas before spec 006, in serialised characters (4,474, about 1,119 tokens).
SCHEMA_CHARS_BEFORE_006 = 4474
ADDED_TOKEN_BUDGET_006 = 350


def test_structured_answers_add_at_most_350_tokens(registered: list[dict[str, Any]]) -> None:
    """Spec 006a: search_zones, the per-field value schemas and get_application's language
    cost at most 350 tokens on every turn, at 4 characters a token."""
    chars = len(json.dumps([t["schema"] for t in registered], ensure_ascii=False))
    added = (chars - SCHEMA_CHARS_BEFORE_006) / 4
    assert added <= ADDED_TOKEN_BUDGET_006, f"spec 006 added about {added:.0f} tokens"


def test_search_zones_and_language_take_no_identity(registered: list[dict[str, Any]]) -> None:
    by_name = {t["name"]: t["schema"]["parameters"] for t in registered}
    assert set(by_name["search_zones"]["properties"]) == {"query", "country"}
    assert by_name["search_zones"]["required"] == ["query"]
    assert set(by_name["get_application"]["properties"]) == {"language"}
    assert by_name["get_application"]["properties"]["language"]["enum"] == ["es-ES", "es-MX", "en"]
    assert "required" not in by_name["get_application"]


def test_record_answer_value_accepts_structured_values_and_null(
    registered: list[dict[str, Any]],
) -> None:
    params = next(t for t in registered if t["name"] == "record_answer")["schema"]["parameters"]
    for value in (["zone_es_madrid_chamberi"], "expired", ["full_time"], 1.5, "2026-10-12", None):
        jsonschema.validate({"field": "zone", "value": value}, params)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"field": "zone", "value": {"ok": True}}, params)


def test_handlers_declare_the_dispatcher_context(registered: list[dict[str, Any]]) -> None:
    """``ToolRegistry.dispatch`` passes ``task_id`` and ``session_id`` only to handlers whose
    signature accepts them (tools/registry.py, ``_kwargs_accepted_by``)."""
    for tool in registered:
        params = inspect.signature(tool["handler"]).parameters
        assert "session_id" in params, f"{tool['name']} handler cannot receive session_id"
        assert "task_id" in params
        assert params["session_id"].default is None


def test_handlers_are_distinct_per_tool(registered: list[dict[str, Any]]) -> None:
    assert len({id(t["handler"]) for t in registered}) == len(registered)
