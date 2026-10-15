"""Owner-run live endpoint smoke for the voice flow (voice-agent acceptance-live VA5, VA6; R25).

The sim children's voice personas, played as text over the custom-LLM endpoint of a real-model
stack. Voice is its own flow (ADR 0059): each persona opens a **voice conversation**
(`POST /api/chat/conversations` with `channel: voice`), mints a **voice session** on it
(`POST .../voice-session`), sends each utterance as one `POST /api/voice/v1/chat/completions`
with the bearer and the voice token (`sim.voice_driver.VoiceDriver`, `stream_turn`), and reads the
transcript back with `GET .../messages`. Nothing here is audio, and the Candidate's page, `/voice`,
is not used: the owner's calls on it are R26(b).

Per turn the report records: the time to the first and the last streamed byte, the tool names the
turn called and whether `get_application` was among them, the input tokens, whether the reply
read back the postcode, Zone, name or slot the Candidate just said (decided in code), and whether
the streamed text held a URL or a booking token. Latency is the backend's share and is shown beside
the Measurement targets, never as a verdict.

Opt in twice, like `python -m live.smoke`: a positive ``CRIBA_REAL_SPEND_CAP_USD`` (the ledger
``CRIBA_REAL_SPEND_LEDGER`` carries the total across commands) and the command itself. The
preflight refuses with exit code 2, before any request to the endpoint, in this order:

1. no positive ``CRIBA_REAL_SPEND_CAP_USD`` (`sim.spend.SpendGuard.from_env`);
2. a sim stack (`sim.stack.is_sim_stack`: stub model, sim clock);
3. outside 09:00-22:00 America/Mexico_City;
4. ``voice_agents.py --check`` exiting other than 0 (1 drift, 2 cannot decide). It is run with the
   variables it reads (``CRIBA_VOICE_AGENT_ES``, ``CRIBA_VOICE_AGENT_MX``,
   ``CRIBA_VOICE_PUBLIC_BASE_URL``, ``CRIBA_ELEVENLABS_API_KEY_FILE``,
   ``CRIBA_VOICE_LLM_SECRET_FILE``) taken from this process; the two key files default to this
   checkout's ``secrets/``. Its result and time go in the report header.

Then the Operator login (the seed password). Exit codes: 0 done, 1 a persona errored (the run
stops there, the rest are "not run"), 2 refused to start, 3 the spend guard refused mid-run.
The spend is checked before every persona: the first at `live.smoke.first_projection`, each later
one at 1.5 times the dearest so far; a persona the cap cannot take is "not run".

Tool names and input tokens come from the stack's own records, read-only, after each persona:
``docker compose -p <--compose-project> exec -T backend python -c ...`` opens the SQLite database
with ``mode=ro`` for the Application's ``kind = 'run'`` usage rows under the voice profile
(`criba-recruiter-voice`) and asks Hermes for each run's events
(``GET /p/criba-recruiter-voice/v1/runs/{run_id}/events``). A run belongs to the turn that started
last before the run's time. Without ``--compose-project``, or if Hermes no longer holds a run's
events, the turn records ``tool calls: unavailable`` and the header says so.

Secrets are read only from this checkout (``secrets/criba_voice_llm_secret.txt`` and the seed
password file). No secret, voice token or tunnel host reaches the report or a log line
(`live.voice_report.Scrubber`).

Owner-run sequence (R25), from the main checkout at the merge commit, state kept in
``$TMPDIR/criba-voicelive-run/`` (mode 700). The plan's Live run lists the gates, the canary and
the teardown; in short:

1. The spend caps from the voice log's Spend table; inside 09:00-21:00 Mexico City time.
2. A real stack (plain ``docker-compose.yml``, no sim overlay; spoken turns already point at
   ``criba-recruiter-voice``) on ``CRIBA_WEB_PORT=18141`` with ``CRIBA_VOICE=on``,
   ``CRIBA_VOICE_MAX_CONCURRENT=20``, ``CRIBA_VOICE_DAILY_MINUTES=600``, the two agent ids and the
   public base URL, with ``voice_agents.py --check`` exiting 0 (apply the agents first if not).
3. The canary, one persona::

       PYTHONPATH=src/tests CRIBA_E2E_SEED_PASSWORD_FILE=secrets/criba_seed_password.txt \\
       CRIBA_REAL_SPEND_CAP_USD=<n> \\
       CRIBA_REAL_SPEND_LEDGER=$TMPDIR/criba-voicelive-run/spend.json \\
       CRIBA_VOICE_MAX_CONCURRENT=20 CRIBA_VOICE_DAILY_MINUTES=600 \\
       uv run --project src/backend python -m live.voice_smoke \\
         --base-url http://localhost:18141 --compose-project <the stack's project> \\
         --personas qualified_es --out reports/voice-acceptance.md

4. Then every persona, the same command without ``--personas``; commit the report (the header
   and the `smoke` section); tear the stack down.

Docker Desktop's clock can drift from the host's by a second or two; a run is matched to the
turn whose window it falls nearest to, which absorbs that. The calls come from the host, so the
public exposure (the tunnel) is not needed for this smoke;
only the ElevenLabs agents' stored URL must be the one ``--check`` compares.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from criba.channel.voice_agents_config import VOICE_IDS
from live import voice_report as vr
from live.client import DEFAULT_MODEL, Charge, LiveStack
from live.report import _table
from live.smoke import first_projection, git_state, password
from live.voice_audio import digit_runs, norm_tokens, spoken_digits, strip_accents, words
from sim.personas.voice import VoicePersona, load_voice_personas, outcome_expect, typed_of
from sim.spend import SpendGuard, SpendRefused
from sim.stack import is_sim_stack, login, new_phone
from sim.voice_driver import (
    HOUR_WORDS,
    WEEKDAY_NAMES,
    StreamedTurn,
    VoiceDriver,
    VoiceDriverError,
    VoiceRun,
)

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
VOICE_AGENTS = REPO_ROOT / "src" / "scripts" / "voice_agents.py"
DEFAULT_LLM_SECRET_FILE = REPO_ROOT / "secrets" / "criba_voice_llm_secret.txt"
DEFAULT_KEY_FILE = REPO_ROOT / "secrets" / "elevenlabs_api_key.txt"
MEXICO = ZoneInfo("America/Mexico_City")
WINDOW_OPEN = clock_time(9, 0)
WINDOW_CLOSE = clock_time(22, 0)
VOICE_PROFILE = "criba-recruiter-voice"
RUN_MAX_DISTANCE = timedelta(seconds=30)  # a run further than this from every turn is left out
SUMMARY_STAGES = ("qualified", "approved", "interview_booked")
EXIT_DONE, EXIT_ERROR, EXIT_REFUSED, EXIT_SPEND = 0, 1, 2, 3

# ---- the preflight ----------------------------------------------------------------------------


def in_window(now: datetime) -> bool:
    """09:00 <= t < 22:00 in America/Mexico_City."""
    return WINDOW_OPEN <= now.astimezone(MEXICO).time() < WINDOW_CLOSE


@dataclass(frozen=True)
class AgentsCheck:
    """The outcome of ``voice_agents.py --check`` and when it ran."""

    exit_code: int
    at: datetime


def _run_subprocess(argv: list[str], env: dict[str, str]) -> int:
    try:
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return 2
    if done.returncode != 0:
        for line in (done.stdout + done.stderr).splitlines():
            print(f"  voice_agents.py: {line}", file=sys.stderr)  # drift lines are redacted there
    return done.returncode


def run_agents_check(
    env: Mapping[str, str],
    *,
    runner: Callable[[list[str], dict[str, str]], int] | None = None,
    now: Callable[[], datetime] | None = None,
) -> AgentsCheck:
    """``voice_agents.py --check`` with the variables it reads taken from `env`, nothing else."""
    passed = {k: env[k] for k in ("PATH", "HOME") if k in env}
    for name in ("CRIBA_VOICE_AGENT_ES", "CRIBA_VOICE_AGENT_MX", "CRIBA_VOICE_PUBLIC_BASE_URL"):
        if name in env:
            passed[name] = env[name]
    passed["CRIBA_ELEVENLABS_API_KEY_FILE"] = env.get(
        "CRIBA_ELEVENLABS_API_KEY_FILE", str(DEFAULT_KEY_FILE)
    )
    passed["CRIBA_VOICE_LLM_SECRET_FILE"] = env.get(
        "CRIBA_VOICE_LLM_SECRET_FILE", str(DEFAULT_LLM_SECRET_FILE)
    )
    code = (runner or _run_subprocess)([sys.executable, str(VOICE_AGENTS), "--check"], passed)
    return AgentsCheck(exit_code=code, at=(now or (lambda: datetime.now(UTC)))())


# ---- tool names and tokens, read-only ---------------------------------------------------------

# Runs inside the backend container (`docker compose exec -T backend python -c`): the standard
# library only. It reads the usage rows with the database opened read-only, and the tool names
# from the Hermes run events.
RUNS_SCRIPT = r"""
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path


def open_read_only(db):
    return sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)


def read_runs(db, application_id, profile):
    conn = open_read_only(db)
    try:
        rows = conn.execute(
            "SELECT run_id, ts, input_tokens FROM usage WHERE application_id = ?"
            " AND kind = 'run' AND profile = ? AND run_id IS NOT NULL ORDER BY id",
            (application_id, profile),
        ).fetchall()
    finally:
        conn.close()
    return [{"run_id": r[0], "ts": r[1], "input_tokens": r[2]} for r in rows]


def tool_names(base, key, profile, run_id, urlopen=urllib.request.urlopen, timeout=10):
    url = base.rstrip("/") + "/p/" + profile + "/v1/runs/" + run_id + "/events"
    request = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + key, "Accept": "text/event-stream"}
    )
    names = []
    seen = False
    try:
        with urlopen(request, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:])
                except ValueError:
                    continue
                seen = True
                kind = event.get("event") or ""
                if kind == "tool.started":
                    names.append(event.get("tool"))
                if kind.startswith("run.") and kind != "run.started":
                    break
            return names if seen else None
    except Exception:
        # A stream that stays open after the run ends times out; what was read still counts.
        return names if seen else None


def main():
    application_id, profile = sys.argv[1], sys.argv[2]
    key_file = os.environ.get("CRIBA_HERMES_API_KEY_FILE")
    key = Path(key_file).read_text().strip() if key_file else ""
    base = os.environ.get("CRIBA_HERMES_URL", "http://hermes:8642")
    runs = read_runs(os.environ.get("CRIBA_DB_PATH", "/data/criba.db"), application_id, profile)
    out = []
    gone = 0
    for run in runs:
        tools = tool_names(base, key, profile, run["run_id"])
        gone += tools is None
        out.append(dict(run, tools=tools))
    note = "Hermes returned no events for %d of %d run(s)" % (gone, len(runs)) if gone else ""
    print(json.dumps({"runs": out, "note": note}))


if __name__ == "__main__":
    main()
"""


@dataclass(frozen=True)
class RunInfo:
    """One Hermes run of the Application: its usage row and, when Hermes still has them, the
    tools it called (None: its events are gone)."""

    run_id: str
    ts: datetime
    input_tokens: int
    tools: list[str] | None


@dataclass
class RunsReading:
    """What the stack's records said. `failed`: the read itself did not work, so nothing about
    any turn is known (an empty `runs` then does not mean "no run")."""

    runs: list[RunInfo] = field(default_factory=list)
    note: str = ""
    failed: bool = False


RunsReader = Callable[[str], RunsReading]
Executor = Callable[[list[str]], str]


def _docker_exec(argv: list[str]) -> str:
    done = subprocess.run(
        argv, cwd=REPO_ROOT, capture_output=True, text=True, timeout=120, check=False
    )
    if done.returncode != 0:
        raise RuntimeError(f"docker compose exec exited {done.returncode}")
    return done.stdout


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def compose_runs_reader(
    project: str, *, execute: Executor | None = None, profile: str = VOICE_PROFILE
) -> RunsReader:
    """A reader that runs the read-only script in the stack's backend container."""
    run = execute or _docker_exec

    def read(application_id: str) -> RunsReading:
        argv = [
            "docker",
            "compose",
            "-p",
            project,
            "exec",
            "-T",
            "backend",
            "python",
            "-c",
            RUNS_SCRIPT,
            application_id,
            profile,
        ]
        try:
            payload = json.loads(run(argv))
            runs = [
                RunInfo(
                    run_id=str(item["run_id"]),
                    ts=_parse_ts(str(item["ts"])),
                    input_tokens=int(item["input_tokens"]),
                    tools=None if item.get("tools") is None else [str(t) for t in item["tools"]],
                )
                for item in payload["runs"]
            ]
            return RunsReading(runs=runs, note=str(payload.get("note") or ""))
        except Exception as exc:
            return RunsReading(note=f"could not read the stack's usage rows: {exc}", failed=True)

    return read


def assign_runs(
    windows: Sequence[tuple[datetime, datetime]], runs: Sequence[RunInfo]
) -> list[list[RunInfo]]:
    """The runs of each turn, given each turn's `(start, end)`.

    The usage row is written when the run ends, a moment before the turn's reply is stored and
    well before the next utterance starts, so a run belongs to the turn whose window holds its
    time or, if none does (clock drift between the host and the stack), the nearest window.
    """
    assigned: list[list[RunInfo]] = [[] for _ in windows]
    for run in sorted(runs, key=lambda r: r.ts):
        best: tuple[timedelta, int] | None = None
        for index, (start, end) in enumerate(windows):
            if run.ts < start:
                distance = start - run.ts
            elif run.ts > end:
                distance = run.ts - end
            else:
                distance = timedelta(0)
            if best is None or distance < best[0]:
                best = (distance, index)
        if best is not None and best[0] <= RUN_MAX_DISTANCE:
            assigned[best[1]].append(run)
    return assigned


# ---- what a turn is expected to read back, and what a reply must not contain -----------------

_URL_PATTERNS = (
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"\bwww\.", re.IGNORECASE),
    re.compile(r"[a-z0-9-]+\.[a-z]{2,}/", re.IGNORECASE),
    re.compile(r"\b[a-z0-9-]+\.(?:com|es|mx|work|net|org|io|app|dev)\b", re.IGNORECASE),
    re.compile(r"\.ics\b", re.IGNORECASE),
    re.compile(r"/book/", re.IGNORECASE),
    re.compile(r"\]\("),
    re.compile(r"localhost", re.IGNORECASE),
)
_BOOKING_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")
_SLOT_UTTERANCE = re.compile(r"^el (\w+) a las ")
_WEEKDAYS = {strip_accents(name): name for name in WEEKDAY_NAMES.values()}


def mentions_url(text: str) -> bool:
    """A URL, link, host name or invitation file in `text` (the patterns the voice scrubber's
    test lists: scheme, www, host/path, a common TLD, .ics, /book/, a Markdown link)."""
    return any(pattern.search(text) for pattern in _URL_PATTERNS)


def mentions_booking_token(text: str) -> bool:
    return _BOOKING_TOKEN.search(text) is not None


@dataclass(frozen=True)
class Expected:
    """What the Candidate just said that the reply should read back."""

    postcode: str | None = None
    zone: str | None = None
    name: str | None = None
    slot: tuple[str, int] | None = None


def _clean(text: str) -> str:
    return " ".join(words(text))


def _answered_fields(persona: VoicePersona, utterance: str) -> list[tuple[str, str | None]]:
    """`(field, value-or-query)` for the typed replies and moments that say `utterance`."""
    typed = typed_of(persona)
    wanted = _clean(utterance)
    found: list[tuple[str, str | None]] = []
    opening = typed.opening
    if _clean(opening.text) == wanted:
        value = opening.value if isinstance(opening.value, dict) else {}
        for covered in opening.covers:
            if covered == "full_name":
                found.append((covered, str(value.get("full_name") or "") or None))
            elif covered == "zone":
                found.append((covered, opening.query or None))
    for field_name, replies in typed.answers.items():
        for reply in replies:
            if _clean(reply.text) != wanted:
                continue
            if field_name == "full_name":
                found.append((field_name, str(reply.value) if reply.value else reply.text))
            elif field_name == "zone":
                found.append((field_name, reply.query or reply.text))
    for field_name, said in persona.utterances.items():
        for utterance_spec in said:
            if _clean(utterance_spec.text) != wanted:
                continue
            if field_name == "full_name":
                found.append((field_name, str(utterance_spec.value or utterance_spec.text)))
            elif field_name == "zone":
                found.append((field_name, utterance_spec.query or utterance_spec.text))
    correction = persona.moments.correction
    if correction is not None and _clean(correction.text) == wanted:
        if correction.field == "full_name":
            found.append((correction.field, str(correction.value or correction.text)))
        elif correction.field == "zone":
            found.append((correction.field, correction.query or correction.text))
    return found


def expected_for(persona: VoicePersona, utterance: str) -> Expected:
    """The postcode, Zone, name or slot in what the Candidate said, from the persona's script."""
    digits = spoken_digits(utterance)
    postcode = digits if len(digits) == 5 else None
    name = zone = None
    for field_name, value in _answered_fields(persona, utterance):
        if not value:
            continue
        if field_name == "full_name":
            name = value.split()[0]
        elif field_name == "zone":
            zone = value.split(",")[-1].strip()
    slot = None
    match = _SLOT_UTTERANCE.match(_clean(utterance))
    if match and match.group(1) in _WEEKDAYS:
        for hour, said in HOUR_WORDS.items():
            if _clean(said) in _clean(utterance):
                slot = (_WEEKDAYS[match.group(1)], hour)
    return Expected(postcode=postcode, zone=zone, name=name, slot=slot)


def read_back(expected: Expected, reply: str) -> dict[str, bool]:
    """For each expected value, whether `reply` says it. A slot needs its day and hour only."""
    tokens = norm_tokens(reply)
    clean = f" {_clean(reply)} "
    found: dict[str, bool] = {}
    if expected.postcode:
        found["postcode"] = any(expected.postcode in run for run in digit_runs(tokens))
    if expected.zone:
        found["zone"] = f" {_clean(expected.zone)} " in clean
    if expected.name:
        found["name"] = strip_accents(expected.name) in tokens
    if expected.slot:
        day, hour = expected.slot
        hours = {str(hour), str(hour - 12)} if hour > 12 else {str(hour)}
        found["slot"] = strip_accents(day) in tokens and bool(hours & set(tokens))
    return found


# ---- the records ------------------------------------------------------------------------------


@dataclass
class TurnRecord:
    number: int
    started: datetime
    ended: datetime
    utterance: str
    reply: str
    first_byte_s: float
    last_byte_s: float
    read_back: dict[str, bool]
    url: bool
    booking_token: bool
    runs: int = 0
    tools: list[str] | None = None  # None: not known (events gone, or no reader)
    get_application: bool | None = None
    input_tokens: int | None = None


@dataclass
class PersonaResult:
    id: str
    country: str
    expected_stage: str | None = None
    ended: str = ""
    stage: str | None = None
    turns: list[TurnRecord] = field(default_factory=list)
    charge: Charge | None = None
    elapsed_s: float = 0.0
    approved: bool = False
    error: str | None = None
    not_run: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class SmokeResult:
    exit_code: int
    results: list[PersonaResult] = field(default_factory=list)


class TimedVoiceDriver(VoiceDriver):
    """The sim child's driver, with each utterance's start time and reply kept for the record."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        llm_secret: str,
        clock: Callable[[], datetime],
        **kwargs: Any,
    ) -> None:
        super().__init__(client, llm_secret=llm_secret, **kwargs)
        self._clock = clock
        self.log: list[tuple[datetime, datetime, str, StreamedTurn]] = []

    def say(self, run: VoiceRun, utterance: str) -> tuple[StreamedTurn, list[dict[str, Any]]]:
        started = self._clock()
        turn, stored = super().say(run, utterance)
        self.log.append((started, self._clock(), utterance, turn))
        return turn, stored


def turn_records(
    persona: VoicePersona, log_rows: Sequence[tuple[datetime, datetime, str, StreamedTurn]]
) -> list[TurnRecord]:
    records: list[TurnRecord] = []
    for number, (started, ended, utterance, turn) in enumerate(log_rows, start=1):
        records.append(
            TurnRecord(
                number=number,
                started=started,
                ended=ended,
                utterance=utterance,
                reply=turn.text,
                first_byte_s=turn.first_byte_s,
                last_byte_s=turn.last_byte_s,
                read_back=read_back(expected_for(persona, utterance), turn.text),
                url=mentions_url(turn.text),
                booking_token=mentions_booking_token(turn.text),
            )
        )
    return records


def apply_runs(turns: list[TurnRecord], reading: RunsReading) -> None:
    """Fill each turn's tool names, `get_application` flag and input tokens from its runs."""
    if reading.failed:
        for turn in turns:  # nothing is known about any turn
            turn.tools, turn.get_application, turn.input_tokens = None, None, None
        return
    windows = [(t.started, t.ended) for t in turns]
    for turn, runs in zip(turns, assign_runs(windows, reading.runs), strict=True):
        turn.runs = len(runs)
        if not runs:
            turn.tools, turn.get_application, turn.input_tokens = [], False, None
            continue
        turn.input_tokens = sum(r.input_tokens for r in runs)
        if any(r.tools is None for r in runs):
            turn.tools, turn.get_application = None, None
        else:
            names = [name for r in runs for name in r.tools or []]
            turn.tools, turn.get_application = names, "get_application" in names


# ---- dependencies and the run -----------------------------------------------------------------


@dataclass
class Deps:
    """Everything outside the process, injectable so the tests make no network call."""

    env: Mapping[str, str]
    now: Callable[[], datetime]
    turn_clock: Callable[[], datetime]
    check_agents: Callable[[], AgentsCheck]
    runs_reader: RunsReader | None
    sync_client: Callable[[str], httpx.Client]
    async_client: Callable[[str], httpx.AsyncClient]
    phone_for: Callable[[VoicePersona], str]
    load_personas: Callable[[], list[VoicePersona]]
    git_state: Callable[[], tuple[str, bool]]


def default_deps(env: Mapping[str, str] | None = None) -> Deps:
    environment = dict(os.environ) if env is None else env
    return Deps(
        env=environment,
        now=lambda: datetime.now(UTC),
        turn_clock=lambda: datetime.now(UTC),
        check_agents=lambda: run_agents_check(environment),
        runs_reader=None,
        sync_client=lambda base_url: httpx.Client(base_url=base_url, timeout=30),
        async_client=lambda base_url: httpx.AsyncClient(base_url=base_url, timeout=30),
        phone_for=lambda persona: new_phone(typed_of(persona).identity.country),
        load_personas=load_voice_personas,
        git_state=git_state,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m live.voice_smoke", description=(__doc__ or "").split("\n")[0]
    )
    p.add_argument(
        "--base-url", required=True, help="the real-model stack, e.g. http://localhost:18141"
    )
    p.add_argument("--out", type=Path, required=True, help="the report (voice-acceptance.md)")
    p.add_argument("--personas", help="comma list of voice persona ids (default: all, in order)")
    p.add_argument("--note", default="", help="one line for the report header")
    p.add_argument(
        "--compose-project",
        help="the stack's Compose project, to read tool calls and input tokens read-only",
    )
    p.add_argument("--model", default=DEFAULT_MODEL, help="the turn model the stack should serve")
    p.add_argument("--reply-timeout", type=float, default=90.0)
    p.add_argument(
        "--llm-secret-file",
        type=Path,
        default=DEFAULT_LLM_SECRET_FILE,
        help="the custom-LLM bearer (default: secrets/criba_voice_llm_secret.txt)",
    )
    p.add_argument(
        "--seed-password-file",
        type=Path,
        help="the stack's seed password (default: CRIBA_E2E_SEED_PASSWORD_FILE, then "
        "secrets/criba_seed_password.txt)",
    )
    return p


def select_personas(available: Sequence[VoicePersona], ids: str | None) -> list[VoicePersona]:
    if not ids:
        return list(available)
    by_id = {p.id: p for p in available}
    wanted = [i.strip() for i in ids.split(",") if i.strip()]
    unknown = [i for i in wanted if i not in by_id]
    if unknown:
        raise ValueError(f"unknown voice persona {unknown[0]!r}; choose from {', '.join(by_id)}")
    return [by_id[i] for i in wanted]


def read_secret(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(
            f"cannot read the custom-LLM secret file {path.name}: {exc.strerror}"
        ) from exc
    if not value:
        raise ValueError(f"the custom-LLM secret file {path.name} is empty")
    return value


def say(scrub: vr.Scrubber, message: str) -> None:
    clean = scrub.clean(message)
    log.info("%s", clean)
    print(clean, file=sys.stderr, flush=True)


async def _play(
    persona: VoicePersona,
    *,
    driver: TimedVoiceDriver,
    stack: LiveStack,
    deps: Deps,
    scrub: vr.Scrubber,
) -> PersonaResult:
    result = PersonaResult(
        id=persona.id,
        country=typed_of(persona).identity.country,
        expected_stage=outcome_expect(persona).stage,
    )
    phone = deps.phone_for(persona)
    started = time.monotonic()
    try:
        driver.log.clear()
        run = driver.play(persona, phone=phone)
        scrub.add(run.voice_token)
        app = await stack.application(phone)
        if persona.booking is not None and app.get("stage") == "qualified":
            items = await stack.queue_items(app["id"], "approval")
            if items:
                approved = await stack.approve(items[0]["id"])
                result.approved = approved.status_code == 200
            else:
                result.notes.append("booking: no Approval item was open for the Application")
            if result.approved:
                try:
                    driver.book(run, persona)
                except VoiceDriverError as exc:
                    result.notes.append(f"booking: {scrub.clean(str(exc))}")
        result.ended = run.ended
        qualified = app.get("stage") in SUMMARY_STAGES
        app = await stack.application(phone, with_summary=qualified, timeout_s=60.0)
        result.stage = app.get("stage")
        result.turns = turn_records(persona, driver.log)
        if deps.runs_reader is not None:
            try:
                reading = deps.runs_reader(str(app["id"]))
            except Exception as exc:
                reading = RunsReading(note=f"the runs reader failed: {exc}", failed=True)
            apply_runs(result.turns, reading)
            if reading.note:
                result.notes.append(f"tool calls: unavailable for some runs ({reading.note})")
        else:
            result.notes.append("tool calls: unavailable (no --compose-project given)")
        result.charge = stack.charge(app)
    except (VoiceDriverError, LookupError, httpx.HTTPError) as exc:
        result.error = scrub.clean(str(exc) or type(exc).__name__)
        result.turns = result.turns or turn_records(persona, driver.log)
    result.elapsed_s = time.monotonic() - started
    result.notes = [scrub.clean(note) for note in result.notes]
    return result


def _not_run(persona: VoicePersona, reason: str) -> PersonaResult:
    return PersonaResult(id=persona.id, country=typed_of(persona).identity.country, not_run=reason)


async def _run_all(
    personas: Sequence[VoicePersona],
    *,
    args: argparse.Namespace,
    deps: Deps,
    guard: SpendGuard,
    stack: LiveStack,
    secret: str,
    scrub: vr.Scrubber,
    results: list[PersonaResult],
) -> None:
    """Every persona in order, appended to `results` as it finishes (so a mid-run refusal keeps
    what already ran)."""
    dearest = 0.0
    stopped: str | None = None
    with deps.sync_client(args.base_url) as client:
        driver = TimedVoiceDriver(
            client,
            llm_secret=secret,
            clock=deps.turn_clock,
            reply_timeout_s=args.reply_timeout,
        )
        for persona in personas:
            projected = dearest * 1.5 if dearest > 0 else first_projection(guard, args.model)
            if stopped is None and not guard.allow(projected):
                stopped = (
                    f"spend cap: ${guard.total_usd:.4f} counted, the next persona projected "
                    f"${projected:.4f}, cap ${guard.cap_usd:.2f}"
                )
            if stopped is not None:
                results.append(_not_run(persona, stopped))
                continue
            result = await _play(persona, driver=driver, stack=stack, deps=deps, scrub=scrub)
            if result.charge is not None:
                dearest = max(dearest, result.charge.guard_usd)
            cost = result.charge.cost_usd if result.charge else 0.0
            say(
                scrub,
                f"  {persona.id}: {result.error or result.ended}, {len(result.turns)} turns, "
                f"{result.elapsed_s:.1f}s, ${cost:.4f} (total ${guard.total_usd:.4f})",
            )
            results.append(result)
            if result.error:
                stopped = f"stopped after an error in {persona.id}"


# ---- the report -------------------------------------------------------------------------------


def _s(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _cell_tools(turn: TurnRecord) -> str:
    if turn.tools is None:
        return "unavailable"
    if not turn.tools:
        return "no run" if turn.runs == 0 else "none"
    return ", ".join(turn.tools)


def _cell_yes(value: bool | None) -> str:
    return "n/a" if value is None else ("yes" if value else "no")


def _cell_read_back(turn: TurnRecord) -> str:
    return ", ".join(f"{k} {'yes' if v else 'no'}" for k, v in turn.read_back.items()) or "-"


def tool_summary(results: Sequence[PersonaResult], have_reader: bool) -> str:
    turns = [t for r in results for t in r.turns]
    if not have_reader:
        return "tool calls: unavailable (no --compose-project); input tokens not read"
    if any(t.tools is None for t in turns):
        return (
            "tool calls: unavailable for some or all turns (the stack's records could not be "
            "read, or Hermes no longer holds the run events); input tokens from the usage rows "
            "where they were read"
        )
    return "read from the voice profile's usage rows and the Hermes run events"


def smoke_lines(results: Sequence[PersonaResult], args: argparse.Namespace) -> list[str]:
    lines = [
        "Voice flow on the real-model stack (ADR 0059): each persona opens a voice conversation, "
        "mints a voice session on it and plays its turns as text to the custom-LLM endpoint from "
        "the host. Times run from the request to the stream's first and last byte (the backend's "
        "share: no speech recognition, no synthesis). Outcomes are read on the record.",
        "",
        "### Personas",
        "",
    ]
    rows: list[list[object]] = []
    for r in results:
        if r.not_run:
            stage = r.expected_stage or "-"
            rows.append([r.id, r.country, "-", "-", stage, 0, "-", "-", f"not run: {r.not_run}"])
            continue
        rows.append(
            [
                r.id,
                r.country,
                r.error or r.ended,
                r.stage or "-",
                r.expected_stage or "-",
                len(r.turns),
                f"{r.charge.cost_usd:.4f}" if r.charge else "-",
                _s(r.elapsed_s),
                "; ".join(r.notes) or ("approved and booked" if r.approved else ""),
            ]
        )
    lines += _table(
        [
            "Persona",
            "Country",
            "Ended",
            "Stage reached",
            "Expected stage",
            "Turns",
            "Cost (USD)",
            "Time (s)",
            "Note",
        ],
        rows,
    )
    turns = [t for r in results for t in r.turns]
    lines += [
        "### Latency",
        "",
        vr.latency_table([t.first_byte_s for t in turns], [t.last_byte_s for t in turns]),
    ]
    lines += ["### Per-turn record", ""]
    for r in results:
        if r.not_run or not r.turns:
            continue
        lines += [f"#### {r.id}", ""]
        lines += _table(
            [
                "Turn",
                "First byte (s)",
                "Last byte (s)",
                "Tool calls",
                "get_application",
                "Input tokens",
                "Read back",
                "URL spoken",
                "Booking token spoken",
            ],
            [
                [
                    t.number,
                    f"{t.first_byte_s:.2f}",
                    f"{t.last_byte_s:.2f}",
                    _cell_tools(t),
                    _cell_yes(t.get_application),
                    "n/a" if t.input_tokens is None else t.input_tokens,
                    _cell_read_back(t),
                    _cell_yes(t.url),
                    _cell_yes(t.booking_token),
                ]
                for t in r.turns
            ],
        )
    if not any(t.tools is not None for t in turns):
        lines += ["- tool calls: unavailable for every turn of this run.", ""]
    return lines


def run_stamp(deps: Deps) -> vr.RunStamp:
    """The date (UTC) and commit a block is stamped with."""
    commit, dirty = deps.git_state()
    return vr.RunStamp(deps.now().astimezone(UTC).strftime("%Y-%m-%d %H:%M"), commit, dirty)


def header_meta(
    args: argparse.Namespace,
    deps: Deps,
    guard: SpendGuard,
    check: AgentsCheck,
    stamp: vr.RunStamp,
    tool_calls: str,
) -> vr.HeaderMeta:
    """The header block's facts, shared by the smoke and the speech recognition run."""
    return vr.HeaderMeta(
        stamp=stamp,
        stack=args.base_url,
        turn_model=args.model,
        voice_ids=VOICE_IDS,
        check_result=f"exit {check.exit_code}",
        check_time=check.at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        max_concurrent=deps.env.get("CRIBA_VOICE_MAX_CONCURRENT", "not given to this command"),
        daily_minutes=deps.env.get("CRIBA_VOICE_DAILY_MINUTES", "not given to this command"),
        spend_line=guard.summary(),
        tool_calls=tool_calls,
        note=args.note,
    )


def write_outputs(
    args: argparse.Namespace,
    deps: Deps,
    guard: SpendGuard,
    check: AgentsCheck,
    results: Sequence[PersonaResult],
    scrub: vr.Scrubber,
) -> None:
    stamp = run_stamp(deps)
    header = header_meta(
        args, deps, guard, check, stamp, tool_summary(results, deps.runs_reader is not None)
    )
    section = vr.section_body("Live endpoint smoke (R25)", stamp, smoke_lines(results, args))
    vr.write_report(
        args.out,
        {"header": vr.render_header(header), "smoke": section},
        scrubber=scrub,
    )


def _refuse(scrub: vr.Scrubber, message: str, *, before_spend: bool = False) -> SmokeResult:
    say(scrub, f"refused: {message}")
    if before_spend:
        say(scrub, "real-model spend: $0.0000 (nothing was called)")
    return SmokeResult(EXIT_REFUSED)


async def smoke(args: argparse.Namespace, deps: Deps) -> SmokeResult:
    """The whole run: preflight, the personas in order, the report. Returns the exit code."""
    scrub = vr.Scrubber()
    try:
        if args.out.name == "live-smoke.md":
            raise ValueError("--out is the live smoke's report; use voice-acceptance.md")
        personas = select_personas(deps.load_personas(), args.personas)
        secret = read_secret(args.llm_secret_file)
        scrub.add(secret)
        scrub.add(deps.env.get("CRIBA_VOICE_PUBLIC_BASE_URL", ""))
        guard = SpendGuard.from_env(real=True, label="voice-smoke", env=deps.env)
    except (ValueError, SpendRefused) as exc:
        return _refuse(scrub, str(exc), before_spend=True)
    code = EXIT_DONE
    results: list[PersonaResult] = []
    check: AgentsCheck | None = None
    try:
        async with (
            deps.async_client(args.base_url) as chat,
            deps.async_client(args.base_url) as operator,
        ):
            if await is_sim_stack(chat):
                return _refuse(
                    scrub,
                    "this is a sim stack (stub model, sim clock); the smoke needs the real stack",
                )
            if not in_window(deps.now()):
                return _refuse(scrub, "outside 09:00-22:00 America/Mexico_City")
            check = deps.check_agents()
            if check.exit_code != 0:
                return _refuse(
                    scrub,
                    f"voice_agents.py --check exited {check.exit_code} (1 drift, 2 cannot decide)",
                )
            try:
                seed = password(args)
                scrub.add(seed)
                await login(operator, seed)
            except (RuntimeError, OSError, httpx.HTTPError) as exc:
                return _refuse(scrub, f"cannot log in as the Operator: {exc}")
            stack = LiveStack(
                chat, operator, guard, model=args.model, reply_timeout_s=args.reply_timeout
            )
            try:
                await _run_all(
                    personas,
                    args=args,
                    deps=deps,
                    guard=guard,
                    stack=stack,
                    secret=secret,
                    scrub=scrub,
                    results=results,
                )
            except SpendRefused as exc:
                say(scrub, f"refused: {exc}")
                code = EXIT_SPEND
    finally:
        guard.save()
        say(scrub, guard.summary())
    if check is not None and results:
        write_outputs(args, deps, guard, check, results, scrub)
        say(scrub, f"wrote {args.out}")
    if code == EXIT_DONE and any(r.error for r in results):
        code = EXIT_ERROR
    return SmokeResult(code, results)


def main(argv: Sequence[str] | None = None, deps: Deps | None = None) -> int:
    args = build_parser().parse_args(argv)
    if deps is None:
        deps = default_deps()
        if args.compose_project:
            deps = replace(deps, runs_reader=compose_runs_reader(args.compose_project))
    return asyncio.run(smoke(args, deps)).exit_code


if __name__ == "__main__":
    sys.exit(main())
