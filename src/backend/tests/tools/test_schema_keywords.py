"""Guard: the schema interpreter ignores keywords it does not implement, so every keyword in
``contracts/tools.json`` must be one it implements or ignores on purpose."""

from __future__ import annotations

import ast
import inspect
from typing import Any

from criba.tools import schema
from criba.tools.schema import IGNORED_KEYWORDS, SUPPORTED_KEYWORDS, contract


def _unknown_keywords(node: Any, path: str) -> list[str]:
    """Keywords outside the known sets, found by walking a schema (not its data values)."""
    if not isinstance(node, dict):
        return []
    found = [
        f"{path}.{key}"
        for key in node
        if key not in SUPPORTED_KEYWORDS | IGNORED_KEYWORDS and not key.startswith("x-")
    ]
    for key in ("properties", "$defs"):
        for name, sub in (node.get(key) or {}).items():
            found += _unknown_keywords(sub, f"{path}.{key}.{name}")
    for key in ("items", "additionalProperties"):
        found += _unknown_keywords(node.get(key), f"{path}.{key}")
    for key in ("oneOf", "anyOf"):
        for i, sub in enumerate(node.get(key) or []):
            found += _unknown_keywords(sub, f"{path}.{key}[{i}]")
    return found


def test_contract_uses_only_keywords_the_interpreter_knows() -> None:
    assert _unknown_keywords(contract(), "tools.json") == []


def test_walker_flags_an_unsupported_keyword() -> None:
    bad = {"properties": {"a": {"type": "string", "allOf": [{"minLength": 1}]}}}
    assert _unknown_keywords(bad, "s") == ["s.properties.a.allOf"]


def test_supported_set_matches_the_keywords_check_reads() -> None:
    read: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(schema._check))):
        match node:
            case ast.Compare(
                left=ast.Constant(value=str() as key), comparators=[ast.Name(id="schema")]
            ):
                read.add(key)
            case ast.Call(
                func=ast.Attribute(value=ast.Name(id="schema"), attr="get"),
                args=[ast.Constant(value=str() as key), *_],
            ):
                read.add(key)
            case ast.Subscript(value=ast.Name(id="schema"), slice=ast.Constant(value=str() as key)):
                read.add(key)
    assert read == SUPPORTED_KEYWORDS
