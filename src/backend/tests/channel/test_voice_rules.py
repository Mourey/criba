"""The voice rules written into the `[voice]` block of each spoken turn (parent R11, spec AG8, AG9).

The rules override three typed lines of the Screener skill on spoken turns. Each override quotes a
phrase of the line it overrides, and that phrase is checked against the live `SKILL.md`, so a skill
edit that removes it fails here first. Voice and text are different flows (owner decision 64): the
rules and the spoken copy never point the Candidate to a chat, a button or a link.
"""

from __future__ import annotations

import re
import typing
from pathlib import Path
from typing import Final

from criba.channel import voice_rules
from criba.channel.voice_rules import VOICE_TURN_RULES
from criba.tools.schema import tool_names

REPO_ROOT = Path(__file__).resolve().parents[4]
SKILL = REPO_ROOT / "src/skills/criba/screener/SKILL.md"

ANCHORS = ("do not retype them", "booking_url", "Voice notes")
OPTOUT_QUESTION = "¿quieres que dejemos de contactarte? Di 'baja'"
PRIVACY_LINE = "Tus datos se usan solo para tu solicitud de empleo."
NEXT_STEP = "un reclutador de Grupo Sazón revisará tu solicitud y se pondrá en contacto contigo"
# Words that belong to the text flow (owner decision 64): none may reach a spoken line.
FORBIDDEN = re.compile(
    r"chat|enlace|bot[oó]n|\bescrib\w*|por aquí|\bhttps?\b|www\.|\.com|revisión|responde"
    r"|🛵|[\U0001F300-\U0001FAFF\u2600-\u27BF]",
    re.IGNORECASE,
)
ID_TOKEN = re.compile(r"\bids?\b|_ids?\b", re.IGNORECASE)


def _lines() -> list[str]:
    return VOICE_TURN_RULES.splitlines()


def _one_line_with(*parts: str) -> str:
    """The single rule line holding every part (compared lowercased)."""
    wanted = [p.lower() for p in parts]
    found = [line for line in _lines() if all(p in line.lower() for p in wanted)]
    assert len(found) == 1, f"expected one rule line holding {parts!r}, found {found!r}"
    return found[0]


def test_rules_name_the_markers_and_shape() -> None:
    assert "[voice]" in VOICE_TURN_RULES
    assert "[optout_request]" in VOICE_TURN_RULES
    assert VOICE_TURN_RULES.strip() == VOICE_TURN_RULES
    assert not VOICE_TURN_RULES.startswith("[voice]")
    lines = _lines()
    assert len(lines) >= 6
    assert len(VOICE_TURN_RULES) <= 2600  # sent every spoken turn: keep it compact
    for line in lines:
        assert line.startswith("- "), line
        assert not line.startswith("["), line


def test_rules_carry_each_r11_rule() -> None:
    _one_line_with("short plain sentences", "one idea each")
    _one_line_with("aloud", "options")

    url_line = _one_line_with("no urls", "no emoji", "no markdown", "no lists or bullets")
    assert "booking_url" not in url_line
    _one_line_with("already spoken", "never repeat", "disclosure", "unless the candidate asks")
    _one_line_with("only the options the latest tool result gives", "never a zone")
    assert "[application]" not in VOICE_TURN_RULES  # the block carries no options
    zone_line = _one_line_with("colonia", "ciudad", "first", "nearest zones only when")
    assert "barrio (Spain) or colonia (Mexico)" in zone_line  # each country's own word
    _one_line_with("never say where", "tools", "records", "corrections")
    _one_line_with("one or two natural sentences", "never as a list")
    assert PRIVACY_LINE in _one_line_with("privacy notice", "once")
    assert NEXT_STEP in _one_line_with("what happens next")
    _one_line_with(
        "cannot answer",
        "someone from the team will contact them",
        "still call the tool the skill names",
    )

    _one_line_with(
        "digit by digit",
        "postcode",
        "zone",
        "name",
        "slot",
        "de la mañana",
        "de la tarde",
        "confirm",
    )

    optout_line = _one_line_with("[optout_request]")
    assert OPTOUT_QUESTION in optout_line

    _one_line_with("Voice notes", "not a voice note", "candidate speaking")


def test_rules_override_the_typed_lines_by_quoting_them() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    for anchor in ANCHORS:
        assert anchor in VOICE_TURN_RULES, anchor
        assert anchor in skill, f"{anchor!r} no longer in SKILL.md"
    retype_line = _one_line_with("do not retype them")
    assert "aloud" in retype_line.lower()
    assert "buttons" not in retype_line.lower()  # voice and text are different flows
    _one_line_with("booking_url", "never read it out")


def test_rules_name_no_tool_id_or_url() -> None:
    text = VOICE_TURN_RULES.lower()
    names = tool_names()
    assert len(names) == 9
    for name in names:
        assert name.lower() not in text, name
    assert ID_TOKEN.search(VOICE_TURN_RULES) is None
    assert "http" not in text


def test_rules_never_point_to_the_text_flow() -> None:
    """Every word the owner heard on the two demo calls is absent from the rules."""
    assert FORBIDDEN.search(VOICE_TURN_RULES) is None, FORBIDDEN.search(VOICE_TURN_RULES)
    lowered = VOICE_TURN_RULES.lower()
    for word in ("chat", "enlace", "botones", "escribir", "por aquí", "el sistema", "buttons"):
        assert word not in lowered, word


def test_rules_are_one_final_string() -> None:
    assert isinstance(VOICE_TURN_RULES, str)
    assert voice_rules.__all__ == ["VOICE_TURN_RULES"]
    public = {n for n in vars(voice_rules) if not n.startswith("_")} - {"Final"}
    assert public == {"VOICE_TURN_RULES"}
    hints = typing.get_type_hints(voice_rules, include_extras=True)
    assert hints["VOICE_TURN_RULES"] == Final[str]
