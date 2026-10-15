"""Script loading and validation against contracts/tools.json."""

from pathlib import Path

import pytest

from sim.stub_llm.app import DEFAULT_SCRIPT
from sim.stub_llm.script import ScriptError, ToolsContract, load_script, parse_script

CONTRACT = ToolsContract.discover()


def test_contract_is_found_in_the_repo():
    assert CONTRACT is not None, "contracts/tools.json should be discoverable from the repo"
    assert {"record_answer", "get_application", "book_interview"} <= set(CONTRACT.tools)
    assert CONTRACT.tools["record_answer"]["required"] == {"field", "value"}


def test_packaged_screener_script_is_valid_against_the_contract():
    script = load_script(DEFAULT_SCRIPT, CONTRACT)
    assert script.name == "screener"
    assert len(script.rules) > 20
    names = [r.name for r in script.rules]
    assert len(names) == len(set(names)), "rule names should be unique"


def test_screener_uses_only_contract_tools():
    script = load_script(DEFAULT_SCRIPT)
    used = {c.name for r in script.rules for c in r.respond.tool_calls}
    assert used  # at least the basics
    assert used <= set(CONTRACT.tools)
    assert {
        "get_application",
        "record_answer",
        "finish_screening",
        "record_backout",
    } <= used


def test_unknown_tool_is_rejected():
    raw = {"rules": [{"respond": {"tool_calls": [{"name": "delete_everything"}]}}]}
    with pytest.raises(ScriptError, match="unknown tool 'delete_everything'"):
        parse_script(raw, CONTRACT)


def test_missing_required_argument_is_rejected():
    raw = {
        "rules": [
            {"respond": {"tool_calls": [{"name": "record_answer", "arguments": {"field": "zone"}}]}}
        ]
    }
    with pytest.raises(ScriptError, match="needs argument"):
        parse_script(raw, CONTRACT)


def test_identity_arguments_are_rejected():
    """Tool inputs never carry application_id or candidate_id (tools.json)."""
    call = {"name": "get_application", "arguments": {"application_id": "app_1"}}
    with pytest.raises(ScriptError, match="no argument"):
        parse_script({"rules": [{"respond": {"tool_calls": [call]}}]}, CONTRACT)


def test_fallback_tool_calls_are_checked_too():
    with pytest.raises(ScriptError, match="fallback"):
        parse_script({"fallback": {"tool_calls": [{"name": "nope"}]}}, CONTRACT)


def test_without_a_contract_tool_names_are_not_checked():
    script = parse_script({"rules": [{"respond": {"tool_calls": [{"name": "anything"}]}}]})
    assert script.rules[0].respond.tool_calls[0].name == "anything"


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"rules": [{"respond": {}}]}, "content, tool_calls or error"),
        (
            {"rules": [{"respond": {"content": "x", "error": {"status": 500}}}]},
            "cannot be combined",
        ),
        ({"rules": [{"match": {"bogus": 1}, "respond": {"content": "x"}}]}, "bogus"),
        (
            {"rules": [{"match": {"user_regex": "("}, "respond": {"content": "x"}}]},
            "not a valid regex",
        ),
        ({"rules": [{"respond": {"error": {"status": 200}}}]}, "greater than or equal"),
    ],
)
def test_bad_scripts_are_rejected(raw, message):
    with pytest.raises(ScriptError, match=message):
        parse_script(raw)


def test_invalid_yaml_names_the_file(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("rules: [unclosed", encoding="utf-8")
    with pytest.raises(ScriptError, match=r"bad\.yaml"):
        load_script(bad)


def test_the_marker_key_parses():
    raw = {"rules": [{"match": {"marker": "optout_request"}, "respond": {"content": "x"}}]}
    assert parse_script(raw, CONTRACT).rules[0].match.marker == "optout_request"


def test_an_unknown_marker_is_refused():
    raw = {"rules": [{"match": {"marker": "voice"}, "respond": {"content": "x"}}]}
    with pytest.raises(ScriptError, match="marker"):
        parse_script(raw, CONTRACT)
