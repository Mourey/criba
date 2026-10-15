"""The voice acceptance report: one file, five marked blocks, written by two scripts and the owner.

`reports/voice-acceptance.md` holds a header block and one block per check (voice-agent
acceptance-live VA7, VA11): `header`, `smoke` (R25, `python -m live.voice_smoke`), `speech`
(R26(a), `python -m live.voice_acceptance`), `live-calls` and `clip` (R26(b), (c): the owner's,
from `--live-template`). Each block sits between::

    <!-- voice-acceptance:<id>:start -->
    <!-- voice-acceptance:<id>:end -->

A script replaces the `header` block and its own block only. Every other byte of the file is
kept, and a block the file does not have yet is inserted in the order above. Each block's first
line names its own run's date (UTC) and commit.

The report is separate from `live-smoke.md`, which `python -m live.smoke` rewrites on each run:
nothing here opens that file, and :func:`write_report` refuses it.

Latency is shown beside the Measurement targets and never decides anything. No secret reaches the
report: every block passes through a :class:`Scrubber` before it is written.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from live.report import _table
from sim.asserts import percentile

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = REPO_ROOT / "reports" / "voice-acceptance.md"
SECTION_IDS = ("header", "smoke", "speech", "live-calls", "clip")
OWNER_SECTIONS = ("live-calls", "clip")
FIRST_SOUND_TARGET_S = 1.5
ANSWER_TARGET_S = 5.0
TITLE = "# Voice acceptance"
INTRO = (
    "The voice flow on the real-model stack (ADR 0059): the live endpoint smoke (R25), the speech "
    "recognition run (R26(a)), the owner's live calls and device checks on `/voice` (R26(b)) and "
    "the recording clip (R26(c)). Each block between its `voice-acceptance` markers is written by "
    "its own command or by the owner; a run replaces the header and its own block only."
)
REDACTED = "[redacted]"


class ReportError(ValueError):
    """The report cannot be written or parsed as asked."""


# ---- secrets never reach the report ----------------------------------------------------------

_PATTERNS = (
    re.compile(r"(?i)\bauthorization\b\s*[:=]\s*(?:bearer\s+)?\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(r"\bv1\.voice\.\S+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"),
    re.compile(r"(?i)[a-z0-9.-]*trycloudflare\.com\S*"),
)


class Scrubber:
    """Replaces known secret values, and anything shaped like a token or a tunnel host."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        values = {s.strip() for s in secrets if s and s.strip()}
        self._secrets = sorted(values, key=len, reverse=True)

    def add(self, *secrets: str) -> None:
        values = {*self._secrets, *(s.strip() for s in secrets if s and s.strip())}
        self._secrets = sorted(values, key=len, reverse=True)

    def clean(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        for pattern in _PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text


# ---- block bodies -----------------------------------------------------------------------------


@dataclass(frozen=True)
class RunStamp:
    """When and at what commit a block was written."""

    date_utc: str
    commit: str
    dirty: bool = False

    def line(self) -> str:
        tree = " (uncommitted changes in the tree)" if self.dirty else ""
        return f"Run {self.date_utc} UTC, commit {self.commit}{tree}."


@dataclass
class HeaderMeta:
    """What the header block says about the run (VA7)."""

    stamp: RunStamp
    stack: str
    turn_model: str
    voice_ids: Mapping[str, str]
    check_result: str
    check_time: str
    max_concurrent: str
    daily_minutes: str
    spend_line: str
    tool_calls: str = "not read"
    note: str = ""
    extra: Sequence[tuple[str, str]] = field(default_factory=tuple)


def section_body(title: str, stamp: RunStamp, lines: Sequence[str]) -> str:
    """A block's text: the run's stamp first, then the heading and the lines."""
    return "\n".join([stamp.line(), "", f"## {title}", "", *lines]).rstrip("\n") + "\n"


def render_header(meta: HeaderMeta) -> str:
    ids = ", ".join(f"{country} {voice}" for country, voice in sorted(meta.voice_ids.items()))
    rows: list[list[object]] = [
        ["Date (UTC)", meta.stamp.date_utc],
        ["Commit", meta.stamp.commit],
        ["Uncommitted changes in the tree", "yes" if meta.stamp.dirty else "no"],
        ["Stack", meta.stack],
        ["Turn model", meta.turn_model],
        ["Voice ids in use", ids or "unknown"],
        ["`voice_agents.py --check`", f"{meta.check_result}, at {meta.check_time}"],
        ["`CRIBA_VOICE_MAX_CONCURRENT`", meta.max_concurrent],
        ["`CRIBA_VOICE_DAILY_MINUTES`", meta.daily_minutes],
        ["Tool calls and input tokens", meta.tool_calls],
        ["Spend", meta.spend_line],
    ]
    if meta.note:
        rows.append(["Note", meta.note])
    rows.extend([key, value] for key, value in meta.extra)
    return section_body("Run facts", meta.stamp, _table(["Fact", "Value"], rows))


def _stats(values: Sequence[float]) -> tuple[str, str, str, str]:
    if not values:
        return ("0", "n/a", "n/a", "n/a")
    return (
        str(len(values)),
        f"{percentile(values, 0.5):.1f}",
        f"{percentile(values, 0.95):.1f}",
        f"{max(values):.1f}",
    )


def latency_table(first_byte_s: Sequence[float], last_byte_s: Sequence[float]) -> str:
    """p50, p95 and slowest beside the Measurement targets. Never a verdict."""
    if not first_byte_s and not last_byte_s:
        return "No turn was timed.\n"
    rows = [
        [
            "First byte of the reply, from the request",
            *_stats(first_byte_s),
            f"first sound under {FIRST_SOUND_TARGET_S:g} s",
        ],
        [
            "Last byte of the reply, from the request",
            *_stats(last_byte_s),
            f"answer under about {ANSWER_TARGET_S:g} s",
        ],
    ]
    lines = [
        "The backend's share only: the custom-LLM endpoint, from the request to the stream's "
        "bytes, before speech recognition and synthesis. The targets are the Measurement "
        "targets, shown for reference; the figures decide nothing.",
        "",
        *_table(["Measure", "Turns", "p50 (s)", "p95 (s)", "Slowest (s)", "Target"], rows),
    ]
    return "\n".join(lines)


# ---- the owner's blocks: template, blanks and --verify ---------------------------------------

_FIELD_TABLE = ["Field", "Value"]


def _field_rows(labels: Sequence[str]) -> list[str]:
    return _table(_FIELD_TABLE, [[label, ""] for label in labels])


def _call_section(heading: str) -> list[str]:
    lines = [f"### Call: {heading}", ""]
    lines += _field_rows(
        [
            "Date and time (Mexico City)",
            "Browser and device",
            "Timing source (ElevenLabs conversation timestamps or screen recording)",
            "First spoken line was the AI disclosure (yes or no)",
            "Transcript appeared on `/voice` (yes or no)",
            "Public exposure up (time, Mexico City)",
            "Public exposure down (time, Mexico City)",
        ]
    )
    lines += _table(
        [
            "Timed turn",
            "End of speech to first sound (s)",
            "End of speech to spoken answer (s)",
        ],
        [[f"Turn {n}", "", ""] for n in (1, 2, 3)],
    )
    return lines


def _device_section(name: str) -> list[str]:
    lines = [f"### Device check: {name}", ""]
    lines += _field_rows(
        [
            "Device and system version",
            'Call starts from the tap on "Hablar"',
            "Permission prompt for the microphone handled",
            "Agent audio plays",
            'Backgrounding the page gives "Toca para reanudar"',
            'After the call the transcript stays and "Empezar de nuevo" is offered',
        ]
    )
    return lines


def live_template(stamp: RunStamp | None = None) -> dict[str, str]:
    """The `live-calls` and `clip` blocks with every field present and blank (VA11)."""
    intro = [
        "Owner-run (R26(b)): one call per country on `/voice`, timed from the end of speech to "
        "the first sound and to the spoken answer, from the ElevenLabs conversation timestamps "
        "(transcripts are kept one day) or a screen recording. Fill every cell with what you "
        "observed. If you did not run a check, write `not run` and why; do not write that it "
        "worked. `python -m live.voice_acceptance --verify` exits 1 while any cell is blank.",
        "",
    ]
    live = [
        *intro,
        *_call_section("Spain (ES)"),
        *_call_section("Mexico (MX)"),
        *_device_section("iOS Safari"),
        *_device_section("Android Chrome"),
    ]
    clip = [
        "Owner-run (R26(c)): a short voice clip in the five-minute recording. The recording "
        "stays outside the repository. Fill every cell.",
        "",
        "### Recording clip",
        "",
        *_field_rows(
            [
                "Recording file name (kept outside the repository)",
                "Clip start (mm:ss in the recording)",
                "Clip length (s)",
                "Country",
                "Owner's sign-off",
            ]
        ),
    ]
    if stamp is None:
        return {
            "live-calls": "\n".join(
                ["Template, not yet filled in.", "", "## Live calls", "", *live]
            ),
            "clip": "\n".join(["Template, not yet filled in.", "", "## Recording clip", "", *clip]),
        }
    return {
        "live-calls": section_body("Live calls and device checks", stamp, live),
        "clip": section_body("Recording clip", stamp, clip),
    }


@dataclass(frozen=True)
class BlankField:
    section: str
    context: str
    label: str

    def __str__(self) -> str:
        where = f"{self.section} / {self.context}" if self.context else self.section
        return f"{where}: {self.label} is blank"


def start_marker(section_id: str) -> str:
    return f"<!-- voice-acceptance:{section_id}:start -->"


def end_marker(section_id: str) -> str:
    return f"<!-- voice-acceptance:{section_id}:end -->"


def _is_separator(line: str) -> bool:
    return line.startswith("|") and set(line.strip()) <= set("|-: ")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _block_text(text: str, section_id: str) -> str | None:
    spans = _spans(text)
    if section_id not in spans:
        return None
    start, end = spans[section_id]
    return text[start:end]


def blank_fields(text: str) -> list[BlankField]:
    """Every table cell left empty in the owner's blocks, with the heading it sits under."""
    found: list[BlankField] = []
    for section_id in OWNER_SECTIONS:
        block = _block_text(text, section_id)
        if block is None:
            continue
        lines = block.splitlines()
        context = ""
        for index, line in enumerate(lines):
            if line.startswith("### "):
                context = line[4:].strip()
                continue
            if not line.startswith("|") or _is_separator(line):
                continue
            if index + 1 < len(lines) and _is_separator(lines[index + 1]):
                continue  # a header row
            cells = _cells(line)
            if any(not cell for cell in cells):
                found.append(BlankField(section_id, context, cells[0] or "(unnamed row)"))
    return found


def verify(text: str) -> list[str]:
    """What stops the report being committed: a missing block or a blank field. Empty is fine."""
    problems = [f"block {i} is missing" for i in SECTION_IDS if _block_text(text, i) is None]
    problems += [str(blank) for blank in blank_fields(text)]
    return problems


# ---- writing the file -------------------------------------------------------------------------


def _spans(text: str) -> dict[str, tuple[int, int]]:
    """`{id: (start of its start marker, end of its end marker)}`; ReportError on a broken pair."""
    spans: dict[str, tuple[int, int]] = {}
    for section_id in SECTION_IDS:
        start_at = text.find(start_marker(section_id))
        end_at = text.find(end_marker(section_id))
        if start_at == -1 and end_at == -1:
            continue
        if start_at == -1 or end_at == -1 or end_at < start_at:
            raise ReportError(f"block {section_id}: start and end markers do not pair")
        if text.count(start_marker(section_id)) != 1 or text.count(end_marker(section_id)) != 1:
            raise ReportError(f"block {section_id}: markers appear more than once")
        spans[section_id] = (start_at, end_at + len(end_marker(section_id)))
    return spans


def wrap(section_id: str, body: str) -> str:
    return f"{start_marker(section_id)}\n{body.rstrip(chr(10))}\n{end_marker(section_id)}"


def update_report(
    existing: str | None,
    blocks: Mapping[str, str],
    *,
    scrubber: Scrubber | None = None,
) -> str:
    """`existing` with each of `blocks` replaced (or inserted in order); the rest is untouched."""
    unknown = [i for i in blocks if i not in SECTION_IDS]
    if unknown:
        raise ReportError(f"unknown block {unknown[0]!r}; the ids are {', '.join(SECTION_IDS)}")
    clean = scrubber.clean if scrubber is not None else Scrubber().clean
    text = existing or ""
    if not text.strip():
        text = f"{TITLE}\n\n{INTRO}\n"
    for index, section_id in enumerate(SECTION_IDS):
        if section_id not in blocks:
            continue
        block = wrap(section_id, clean(blocks[section_id]))
        spans = _spans(text)
        if section_id in spans:
            start, end = spans[section_id]
            text = text[:start] + block + text[end:]
            continue
        later = next((spans[i][0] for i in SECTION_IDS[index + 1 :] if i in spans), None)
        if later is not None:
            text = text[:later] + block + "\n\n" + text[later:]
        else:
            if not text.endswith("\n"):
                text += "\n"
            text += "\n" + block + "\n"
    return text


def write_report(
    path: Path,
    blocks: Mapping[str, str],
    *,
    scrubber: Scrubber | None = None,
) -> str:
    """Update the report at `path` in place and return its new text. Never `live-smoke.md`."""
    if path.name == "live-smoke.md":
        raise ReportError(
            "live-smoke.md is the live smoke's report; this one is voice-acceptance.md"
        )
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    text = update_report(existing, blocks, scrubber=scrubber)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


__all__ = [
    "ANSWER_TARGET_S",
    "FIRST_SOUND_TARGET_S",
    "OWNER_SECTIONS",
    "REPORT_PATH",
    "SECTION_IDS",
    "BlankField",
    "HeaderMeta",
    "ReportError",
    "RunStamp",
    "Scrubber",
    "blank_fields",
    "end_marker",
    "latency_table",
    "live_template",
    "render_header",
    "section_body",
    "start_marker",
    "update_report",
    "verify",
    "wrap",
    "write_report",
]
