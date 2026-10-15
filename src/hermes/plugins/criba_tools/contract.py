"""Loads the tool schemas from ``contracts/tools.json`` (the single source of truth).

The contract's per-tool input schemas use ``$ref`` into ``$defs`` (for example the ``field``
enum), which a model provider cannot resolve. This module inlines every ``$ref`` so each tool's
``parameters`` is a self-contained JSON Schema, and drops the keys that only cost tokens
(``title``, ``examples``).

Standard library only: the plugin must import in the Hermes image (Python 3.14) and in the
host test environment (Python 3.12) alike.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTRACT_ENV = "CRIBA_TOOLS_CONTRACT"
CONTRACT_FILENAME = "tools.json"

# Keys removed from the model-facing schema. They document the contract for humans and cost
# input tokens on every turn (spikes 1 and 2: the toolset budget is about 7K tokens).
_STRIPPED_KEYS = frozenset(
    {
        "title",
        "examples",
        "$comment",
        # Size and format limits. The backend checks every one again with its own message
        # (criba.tools.schema, ADR 0042: a value of the wrong shape is invalid_args and counts
        # as no miss), and the Screener skill names the shapes. About 350 characters on every
        # call (docs/evidence/turn-size.md). Types, enums, `pattern` and
        # `additionalProperties` stay: they say what a value is.
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minimum",
        "format",
        "default",
    }
)

# Identity never comes from the model. Used by tests and by a defensive check at load time.
FORBIDDEN_PROPERTY_NAMES = frozenset({"application_id", "candidate_id"})


class ContractError(RuntimeError):
    """The contract file is missing or does not have the expected shape."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]

    def schema(self) -> dict[str, Any]:
        """The Hermes tool schema: ``{name, description, parameters}``."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": copy.deepcopy(self.parameters),
        }


def find_contract(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Locate ``tools.json``.

    Order: the explicit argument, ``$CRIBA_TOOLS_CONTRACT``, a copy next to this module (the
    Docker image places one there at build time), then ``contracts/tools.json`` in a parent
    directory (a repository checkout, used by the host tests).
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get(CONTRACT_ENV)
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve().parent
    candidates.append(here / CONTRACT_FILENAME)
    candidates.extend(parent / "contracts" / CONTRACT_FILENAME for parent in here.parents)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(c) for c in candidates[:4])
    raise ContractError(f"tools.json not found (looked in {searched}, ...)")


def load_contract(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    with open(find_contract(path), encoding="utf-8") as handle:
        return json.load(handle)


def _resolve_ref(ref: str, defs: dict[str, Any]) -> Any:
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        raise ContractError(f"unsupported $ref {ref!r}: only {prefix}<name> is allowed")
    name = ref[len(prefix) :]
    if name not in defs:
        raise ContractError(f"$ref {ref!r} does not exist in $defs")
    return defs[name]


def inline_refs(node: Any, defs: dict[str, Any], _seen: tuple[str, ...] = ()) -> Any:
    """Return ``node`` with every ``$ref`` replaced by the definition it points at."""
    if isinstance(node, list):
        return [inline_refs(item, defs, _seen) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        ref = node["$ref"]
        if ref in _seen:
            raise ContractError(f"recursive $ref {ref!r} cannot be inlined")
        target = inline_refs(copy.deepcopy(_resolve_ref(ref, defs)), defs, (*_seen, ref))
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        if siblings and isinstance(target, dict):
            target = {**target, **inline_refs(siblings, defs, _seen)}
        return target
    return {key: inline_refs(value, defs, _seen) for key, value in node.items()}


def _strip(node: Any) -> Any:
    if isinstance(node, list):
        return [_strip(item) for item in node]
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in _STRIPPED_KEYS}
    return node


def property_names(node: Any) -> set[str]:
    """Every object property name that appears anywhere in a schema."""
    names: set[str] = set()
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            names.update(props)
        for value in node.values():
            names |= property_names(value)
    elif isinstance(node, list):
        for item in node:
            names |= property_names(item)
    return names


def tool_specs(contract: dict[str, Any] | None = None) -> list[ToolSpec]:
    """One ``ToolSpec`` per entry in the contract's ``x-tools`` map, in contract order."""
    contract = contract if contract is not None else load_contract()
    defs = contract.get("$defs") or {}
    x_tools = contract.get("x-tools")
    if not isinstance(x_tools, dict) or not x_tools:
        raise ContractError("tools.json has no x-tools map")
    specs: list[ToolSpec] = []
    for name, entry in x_tools.items():
        input_schema = inline_refs({"$ref": entry["input"]}, defs)
        description = str(input_schema.get("description") or "").strip()
        if not description:
            raise ContractError(f"tool {name!r} has no description in its input schema")
        parameters = _strip({k: v for k, v in input_schema.items() if k != "description"})
        parameters.setdefault("type", "object")
        parameters.setdefault("properties", {})
        bad = property_names(parameters) & FORBIDDEN_PROPERTY_NAMES
        if bad:
            raise ContractError(f"tool {name!r} exposes identity properties {sorted(bad)}")
        specs.append(ToolSpec(name=name, description=description, parameters=parameters))
    return specs
