"""Tool input validation against ``contracts/tools.json``.

The backend image is built from ``src/backend`` only, so the contract is vendored as
``criba/tools/tools.json``; a test keeps it byte-identical to ``contracts/tools.json``.

Validation uses a small interpreter for the JSON Schema keywords the tool inputs use
(``type``, ``enum``, ``const``, ``properties``, ``required``, ``additionalProperties``,
``minLength``/``maxLength``, ``pattern``, ``items``/``minItems``/``maxItems``/``uniqueItems``,
``minimum``/``maximum``, ``oneOf``/``anyOf`` (a ``null`` form included) and local ``$ref``),
so the runtime needs no ``jsonschema`` dependency. ``format`` is an annotation here, as in
``jsonschema`` by default. Tests check it agrees with ``jsonschema`` on the contract.
"""

from __future__ import annotations

import json
import re
from functools import cache
from importlib import resources
from typing import Any, Literal

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "boolean": (bool,),
    "null": (type(None),),
}


# The keywords `_check` acts on, and the ones it ignores on purpose (annotations and the
# contract's own bookkeeping). Any other keyword in `tools.json` would be silently skipped,
# so `tests/tools/test_schema_keywords.py` fails on one, and checks this set against the
# keywords `_check` reads.
SUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        "$ref",
        "oneOf",
        "anyOf",
        "type",
        "const",
        "enum",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "uniqueItems",
        "items",
        "properties",
        "required",
        "additionalProperties",
    }
)
IGNORED_KEYWORDS: frozenset[str] = frozenset(
    {"$schema", "$id", "$comment", "$defs", "title", "description", "default", "examples", "format"}
)


@cache
def contract() -> dict[str, Any]:
    text = resources.files("criba.tools").joinpath("tools.json").read_text(encoding="utf-8")
    return json.loads(text)


def tool_names() -> tuple[str, ...]:
    return tuple(contract()["x-tools"])


def input_schema(tool: str) -> dict[str, Any]:
    return _resolve(contract()["x-tools"][tool]["input"])


def output_schema(tool: str) -> dict[str, Any]:
    return _resolve(contract()["x-tools"][tool]["output"])


def _resolve(ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"only local $ref is supported: {ref}")
    node: Any = contract()
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def validate_args(tool: str, args: Any) -> str | None:
    """None if ``args`` matches the tool's input schema, else a short English reason."""
    return _check(args, input_schema(tool), "args")


def answer_value_schema(field: str) -> dict[str, Any]:
    """The structured value schema for one answer field (``x-answer-values``, ADR 0042)."""
    return _resolve(contract()["x-answer-values"][field])


def check_value(value: Any, schema: dict[str, Any], path: str = "value") -> str | None:
    """None if ``value`` matches ``schema``, else a short English reason."""
    return _check(value, schema, path)


# What each field's structured value looks like, for the invalid_args hint (ADR 0042).
ANSWER_SHAPES: dict[str, str] = {
    "full_name": "the cleaned full name as a string",
    "zone": "a list of 1 to 10 Zone ids from search_zones",
    "driver_license": "one of yes, no, expired, suspended, pending",
    "availability": "a list of codes from full_time, part_time, weekends",
    "preferred_schedule": "a list of codes from morning, afternoon, evening, flexible",
    "experience_years": "a number of years from 0 (months divided by 12)",
    "experience_platforms": (
        "a list of codes from glovo, uber_eats, just_eat, rappi, didi_food, other "
        "(the empty list [] for no app)"
    ),
    "start_date": (
        "an ISO date, YYYY-MM-DD, today or later in the Candidate's calendar, or a list of the "
        "two dates their words fit"
    ),
}

AnswerForm = Literal["structured", "null"]


def answer_form(field: str, value: Any) -> AnswerForm | None:
    """Which form a ``record_answer`` value takes for its field, or None for a wrong shape.

    ``structured``: it matches the field's ``x-answer-values`` schema. ``null``: the
    Candidate answered but the agent could not map it. Anything else, the Candidate's own
    words included, is the agent's error (``invalid_args``, ADR 0042).
    """
    if value is None:
        return "null"
    if check_value(value, answer_value_schema(field)) is None:
        return "structured"
    return None


def validate_answer_value(field: str, value: Any) -> str | None:
    """None if ``value`` has a valid form for ``field``, else a reason naming the shape.

    The contract's ``value`` is one ``anyOf`` for every field (one schema keeps the tool
    small); this is the per-field half of the input check, run right after it.
    """
    if answer_form(field, value) is not None:
        return None
    return f"value for {field} must be {ANSWER_SHAPES[field]}, or null"


def _is_type(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def _check(value: Any, schema: dict[str, Any], path: str) -> str | None:
    if "$ref" in schema:
        return _check(value, _resolve(schema["$ref"]), path)
    if "oneOf" in schema:
        matches = sum(_check(value, s, path) is None for s in schema["oneOf"])
        if matches != 1:
            return f"{path} does not match exactly one allowed form"
    if "anyOf" in schema and not any(_check(value, s, path) is None for s in schema["anyOf"]):
        return f"{path} does not match any allowed form"
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, n) for n in names):
            return f"{path} must be of type {' or '.join(names)}"
    if "const" in schema and value != schema["const"]:
        return f"{path} must be {schema['const']!r}"
    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(v) for v in schema["enum"])
        return f"{path} must be one of {allowed}"
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            return f"{path} is too short"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return f"{path} is longer than {schema['maxLength']} characters"
        if "pattern" in schema and not re.search(schema["pattern"], value):
            return f"{path} does not match the pattern {schema['pattern']}"
    if _is_type(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path} must be at least {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path} must be at most {schema['maximum']}"
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            return f"{path} needs at least {schema['minItems']} item(s)"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return f"{path} has more than {schema['maxItems']} items"
        if schema.get("uniqueItems"):
            seen = [json.dumps(v, sort_keys=True) for v in value]
            if len(set(seen)) != len(seen):
                return f"{path} has duplicate items"
        if "items" in schema:
            for i, item in enumerate(value):
                if problem := _check(item, schema["items"], f"{path}[{i}]"):
                    return problem
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                return f"{path}.{key} is required"
        if schema.get("additionalProperties") is False:
            extra = sorted(k for k in value if k not in props)
            if extra:
                return f"{path} has unknown propert{'ies' if len(extra) > 1 else 'y'} " + (
                    ", ".join(extra)
                )
        for key, sub in props.items():
            if key in value and (problem := _check(value[key], sub, f"{path}.{key}")):
                return problem
    return None
