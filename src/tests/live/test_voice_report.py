"""The voice acceptance report: sections, template, `--verify` and secret hygiene (VA7, VA11).

No file under `reports/` is read or written here; every test works on strings or on
`tmp_path`.
"""

from __future__ import annotations

import builtins
import pathlib
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from live import voice_report as vr

STAMP = vr.RunStamp(date_utc="2026-10-11 20:00", commit="abc1234", dirty=False)


BASE_HEADER = vr.HeaderMeta(
    stamp=STAMP,
    stack="http://localhost:18141",
    turn_model="anthropic/claude-haiku-5.5",
    voice_ids={"ES": "voice-es", "MX": "voice-mx"},
    check_result="exit 0",
    check_time="2026-10-11 19:58 UTC",
    max_concurrent="20",
    daily_minutes="600",
    spend_line="real-model spend: $0.0100 of $2.00",
    tool_calls="read from the Hermes run events",
)


def header(**over: Any) -> vr.HeaderMeta:
    return replace(BASE_HEADER, **over)


def blocks_of(text: str) -> dict[str, str]:
    """Each marked block of `text`, markers included."""
    found: dict[str, str] = {}
    for section_id in vr.SECTION_IDS:
        start = text.find(vr.start_marker(section_id))
        end = text.find(vr.end_marker(section_id))
        if start != -1 and end != -1:
            found[section_id] = text[start : end + len(vr.end_marker(section_id))]
    return found


def full_report() -> str:
    bodies = {
        "header": vr.render_header(header()),
        "smoke": vr.section_body("Smoke", STAMP, ["smoke body line"]),
        "speech": vr.section_body("Speech recognition", STAMP, ["speech body line"]),
    }
    bodies.update(vr.live_template())
    return vr.update_report(None, bodies)


# ---- VA7: one report, sections replaced one at a time ---------------------------------------


@pytest.mark.parametrize("section_id", vr.SECTION_IDS)
def test_rendering_one_section_keeps_the_others(section_id: str) -> None:
    original = full_report()
    before = blocks_of(original)
    assert list(before) == list(vr.SECTION_IDS)
    # Owner notes outside every block, and between blocks, must survive byte for byte.
    noted = original.replace(
        vr.end_marker("smoke"), vr.end_marker("smoke") + "\n\nOwner note: keep me.\n"
    )
    new_body = vr.section_body("Changed", STAMP, ["a changed line"])
    updated = vr.update_report(noted, {section_id: new_body})

    after = blocks_of(updated)
    for other in vr.SECTION_IDS:
        if other != section_id:
            assert after[other] == before[other]
    assert "a changed line" in after[section_id]
    assert "Owner note: keep me." in updated
    # Replacing the section is the only difference.
    assert updated.replace(after[section_id], before[section_id]) == noted


def test_a_missing_section_is_appended_in_order() -> None:
    base = vr.update_report(None, {"smoke": vr.section_body("Smoke", STAMP, ["s"])})
    assert list(blocks_of(base)) == ["smoke"]
    with_clip = vr.update_report(base, {"clip": vr.section_body("Clip", STAMP, ["c"])})
    with_header = vr.update_report(with_clip, {"header": vr.render_header(header())})
    with_speech = vr.update_report(with_header, {"speech": vr.section_body("S", STAMP, ["x"])})
    order = [with_speech.index(vr.start_marker(i)) for i in ("header", "smoke", "speech", "clip")]
    assert order == sorted(order)
    # The smoke block is the one the first call wrote, untouched.
    assert blocks_of(with_speech)["smoke"] == blocks_of(base)["smoke"]
    # A section inserted before an existing later one leaves the later bytes alone.
    assert blocks_of(with_speech)["clip"] == blocks_of(with_clip)["clip"]


def test_a_new_report_has_a_title_and_ends_with_a_newline() -> None:
    text = vr.update_report("", {"smoke": vr.section_body("Smoke", STAMP, ["s"])})
    assert text.startswith("# Voice acceptance")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_updating_twice_is_stable() -> None:
    body = vr.section_body("Smoke", STAMP, ["same"])
    once = vr.update_report(None, {"smoke": body})
    assert vr.update_report(once, {"smoke": body}) == once


def test_a_start_marker_without_an_end_is_refused() -> None:
    broken = f"# Voice acceptance\n\n{vr.start_marker('smoke')}\nno end\n"
    with pytest.raises(vr.ReportError):
        vr.update_report(broken, {"smoke": "x"})


def test_an_unknown_section_is_refused() -> None:
    with pytest.raises(vr.ReportError):
        vr.update_report(None, {"nope": "x"})


def test_each_section_names_its_runs_date_and_commit_first() -> None:
    body = vr.section_body("Smoke", STAMP, ["line"])
    first = body.splitlines()[0]
    assert "2026-10-11 20:00" in first
    assert "UTC" in first
    assert "abc1234" in first
    dirty = vr.section_body("Smoke", vr.RunStamp("2026-10-11 20:00", "abc1234", True), ["line"])
    assert "uncommitted" in dirty.splitlines()[0]


def test_header_carries_every_field() -> None:
    text = vr.render_header(header())
    for needle in (
        "2026-10-11 20:00",
        "abc1234",
        "http://localhost:18141",
        "anthropic/claude-haiku-5.5",
        "voice-es",
        "voice-mx",
        "exit 0",
        "2026-10-11 19:58 UTC",
        "CRIBA_VOICE_MAX_CONCURRENT",
        "CRIBA_VOICE_DAILY_MINUTES",
        "600",
        "real-model spend: $0.0100 of $2.00",
        "read from the Hermes run events",
    ):
        assert needle in text, needle


def test_latency_table_has_no_verdict() -> None:
    table = vr.latency_table(first_byte_s=[0.4, 0.9, 1.7], last_byte_s=[2.0, 4.0, 9.0])
    assert "p50" in table
    assert "p95" in table
    assert "Slowest" in table
    assert "1.5" in table  # the first-sound target, beside the figures
    assert "5" in table  # the answer target
    assert not re.search(r"pass|fail|miss|\bmet\b|\bwithin\b", table.lower())
    # The figures themselves: p50 of three values is the middle one, the slowest the largest.
    assert "0.9" in table
    assert "9.0" in table


def test_latency_table_with_no_turns_says_so() -> None:
    assert "no turn" in vr.latency_table([], []).lower()


# ---- VA7: the live smoke report is not this report's business -------------------------------


def test_live_smoke_report_is_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    real_open = builtins.open

    def spy_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    for name in ("read_text", "write_text", "read_bytes", "write_bytes", "open"):
        original = getattr(pathlib.Path, name)

        def spy(self: Path, *args: Any, _original: Any = original, **kwargs: Any) -> Any:
            opened.append(str(self))
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, name, spy)

    out = tmp_path / "voice-acceptance.md"
    vr.write_report(out, {"smoke": vr.section_body("Smoke", STAMP, ["s"])})
    vr.write_report(out, {"header": vr.render_header(header())})
    assert not [p for p in opened if p.endswith("live-smoke.md")]
    assert vr.REPORT_PATH.name == "voice-acceptance.md"
    assert Path("reports/live-smoke.md") != vr.REPORT_PATH
    with pytest.raises(vr.ReportError):
        vr.write_report(tmp_path / "live-smoke.md", {"smoke": "x"})
    assert not (tmp_path / "live-smoke.md").exists()


def test_write_report_keeps_what_the_file_already_holds(tmp_path: Path) -> None:
    out = tmp_path / "voice-acceptance.md"
    vr.write_report(out, {"smoke": vr.section_body("Smoke", STAMP, ["first run"])})
    first = out.read_text(encoding="utf-8")
    vr.write_report(out, {"speech": vr.section_body("Speech", STAMP, ["second run"])})
    second = out.read_text(encoding="utf-8")
    assert blocks_of(second)["smoke"] == blocks_of(first)["smoke"]
    assert "second run" in second


# ---- VA7, sharp-edges: no secret reaches the report ------------------------------------------

BEARER = "sentinelbearerword"
VOICE_TOKEN = "sentinelvoicetokenword"
CONVERSATION_TOKEN = "sentinelconversationword"
AUTH_VALUE = "sentinelauthvalue"
TUNNEL_HOST = "quiet-sentinel-tunnel.trycloudflare.com"


def test_no_secret_reaches_the_report() -> None:
    scrubber = vr.Scrubber([BEARER, VOICE_TOKEN, CONVERSATION_TOKEN, AUTH_VALUE, TUNNEL_HOST])
    dirty_lines = [
        f"turn failed: Authorization: Bearer {BEARER}",
        f"voice_token={VOICE_TOKEN} conversation_token={CONVERSATION_TOKEN}",
        f"Authorization: {AUTH_VALUE}",
        f"error calling https://{TUNNEL_HOST}/api/voice/v1/chat/completions",
        "v1.voice.wv_abc.12.1760000000.signaturepart",
    ]
    meta = header(stack=f"https://{TUNNEL_HOST}", spend_line=f"bearer {BEARER}")
    text = vr.update_report(
        None,
        {
            "header": vr.render_header(meta),
            "smoke": vr.section_body("Smoke", STAMP, dirty_lines),
        },
        scrubber=scrubber,
    )
    for secret in (BEARER, VOICE_TOKEN, CONVERSATION_TOKEN, AUTH_VALUE, TUNNEL_HOST):
        assert secret not in text
    assert "trycloudflare" not in text
    assert "v1.voice." not in text
    assert "[redacted]" in text


def test_scrubber_patterns_catch_what_it_was_not_told() -> None:
    scrubber = vr.Scrubber([])
    cleaned = scrubber.clean(
        "Authorization: Bearer abcdef0123 and https://x-y.trycloudflare.com/p and v1.voice.a.b.c.d"
    )
    assert "abcdef0123" not in cleaned
    assert "trycloudflare" not in cleaned
    assert "v1.voice." not in cleaned


def test_scrubber_ignores_empty_secrets() -> None:
    scrubber = vr.Scrubber(["", "  "])
    assert scrubber.clean("plain text") == "plain text"


# ---- VA11: the template and --verify ---------------------------------------------------------


def data_rows(text: str) -> list[list[str]]:
    """The cells of every table body row in `text` (not header or separator rows)."""
    lines = text.splitlines()
    rows: list[list[str]] = []
    for index, line in enumerate(lines):
        if not line.startswith("|") or set(line) <= set("|- :"):
            continue
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if following.startswith("|") and set(following) <= set("|- :"):
            continue  # a header row
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def test_template_has_every_field_blank() -> None:
    template = vr.live_template()
    assert set(template) == {"live-calls", "clip"}
    live = template["live-calls"]
    clip = template["clip"]
    for needle in (
        "Spain (ES)",
        "Mexico (MX)",
        "Date and time (Mexico City)",
        "Browser and device",
        "Timing source",
        "End of speech to first sound",
        "End of speech to spoken answer",
        "AI disclosure",
        "transcript",
        "Public exposure up",
        "Public exposure down",
        "iOS Safari",
        "Android Chrome",
        "Hablar",
        "microphone",
        "Toca para reanudar",
        "/voice",
    ):
        assert needle in live, needle
    assert "/test-chat" not in live
    for needle in ("Recording file name", "Clip start", "Clip length", "Country", "sign-off"):
        assert needle in clip, needle
    report = vr.update_report(None, template)
    rows = data_rows(template["live-calls"]) + data_rows(template["clip"])
    assert rows
    assert all(all(cell == "" for cell in row[1:]) for row in rows)  # no pre-filled value
    blanks = vr.blank_fields(report)
    assert len(blanks) == len(rows)
    assert {b.section for b in blanks} == {"live-calls", "clip"}


def filled(report: str) -> str:
    """`report` with every empty table cell given a value, as the owner would."""
    out: list[str] = []
    lines = report.splitlines()
    for index, line in enumerate(lines):
        is_row = line.startswith("|") and not set(line) <= set("|- :")
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        next_is_separator = nxt.startswith("|") and set(nxt) <= set("|- :")
        if is_row and not next_is_separator:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            line = "| " + " | ".join(c or "observed" for c in cells) + " |"
        out.append(line)
    return "\n".join(out) + "\n"


def test_verify_fails_on_the_template_and_passes_a_filled_fixture() -> None:
    bodies = {
        "header": vr.render_header(header()),
        "smoke": vr.section_body("Smoke", STAMP, ["s"]),
        "speech": vr.section_body("Speech", STAMP, ["s"]),
        **vr.live_template(),
    }
    template_report = vr.update_report(None, bodies)
    problems = vr.verify(template_report)
    assert problems
    assert any("Date and time (Mexico City)" in p for p in problems)
    assert vr.verify(filled(template_report)) == []


def test_verify_names_each_blank_field_with_its_section() -> None:
    report = vr.update_report(None, {"live-calls": vr.live_template()["live-calls"]})
    blanks = vr.blank_fields(report)
    assert all(b.section == "live-calls" for b in blanks)
    assert any(b.label == "Browser and device" and "ES" in b.context for b in blanks)
    assert any(b.label == "Browser and device" and "MX" in b.context for b in blanks)


def test_verify_requires_every_section() -> None:
    report = filled(vr.update_report(None, vr.live_template()))
    problems = vr.verify(report)
    assert any("header" in p for p in problems)
    assert any("smoke" in p for p in problems)
    assert any("speech" in p for p in problems)


def test_verify_counts_a_row_with_only_whitespace_as_blank() -> None:
    template_report = vr.update_report(None, vr.live_template())
    spaced = template_report.replace("| Browser and device |  |", "| Browser and device |    |")
    assert spaced != template_report
    assert any("Browser and device" in p for p in vr.verify(spaced))
    answered = template_report.replace(
        "| Browser and device |  |", "| Browser and device | Chrome |"
    )
    assert not any("Browser and device" in p for p in vr.verify(answered))
