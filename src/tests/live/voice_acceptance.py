"""Owner-run spoken acceptance for the voice flow (voice-agent acceptance-live VA8-VA11; R26).

Three commands, one report (`reports/voice-acceptance.md`, `live.voice_report`):

- ``python -m live.voice_acceptance --base-url URL --elevenlabs-cap-seconds N --out PATH``
  R26(a), the speech recognition run. It plays spike 4's 20 scripted calls in its three noise
  conditions (clean, 10 dB, 3 dB) against the real-model stack: per turn it synthesises the
  Candidate clip with the call's voice, mixes the condition, transcribes it with Scribe realtime
  (``scribe_v2_realtime``, ``language_code=es``) and sends the transcript as the next user message
  to the custom-LLM endpoint of a **voice conversation** (ADR 0059: ``channel: voice``, a voice
  session minted on it), with the history growing as ElevenLabs sends it. The pipeline is
  `live.voice_audio`; the Candidate audio lives in a temporary directory deleted when the run
  exits, on error and on Ctrl-C, and nothing audio-derived goes into the report (VA10).
- ``python -m live.voice_acceptance --live-template --out PATH``
  writes the ``live-calls`` and ``clip`` blocks with every field present and blank, for the owner
  to fill in (VA11). It runs no preflight and makes no call; it refuses a report that already has
  either block, so it never wipes what the owner wrote.
- ``python -m live.voice_acceptance --verify PATH``
  exits 1, naming each, while any block is missing or any field is blank; 0 when the report is
  complete.

Same preflight as `live.voice_smoke`, in the same order, each a refusal with exit code 2 before
any request to the endpoint or ElevenLabs: a positive ``CRIBA_REAL_SPEND_CAP_USD``; not a sim
stack; inside 09:00-22:00 America/Mexico_City; ``voice_agents.py --check`` exiting 0. Then the
vendor cap: ``--elevenlabs-cap-seconds`` has no default. The run projects the realtime seconds
and the text-to-speech characters first (printed whatever the outcome) and refuses above the cap.
Exit codes: 0 done, 1 a conversation errored, 2 refused, 3 the spend guard refused mid-run.

Each check is classified from two facts, the transcript (spike 4's scorer) and the answer recorded
on the Application (Operator API): **captured** when both hold the value; **caught** when the
transcript lost it, the reply read a wrong value back, the script played one correction turn (the
clean clip "No, es ...", same voice) and the record ends equal; **lost** for any other end. Values
the product does not store as an answer are classified by the record's stage or event (opt-out,
withdrawal, the confirmation, the booked Interview); the spike's postcode, vehicle, age and similar
checks have no counterpart on the record, so they are counted apart as **not stored** and never as
captured. The synthetic Candidate is cleaner than a real one, so the rates are a ceiling.

R26 owner checklist (inside 09:00-22:00 Mexico City time, on the real-model stack with
``CRIBA_VOICE=on``; the plan's Live run covers the stack and the caps. Sessions that no call
ends count as live for 30 minutes, so for the 60 conversations of step 3 raise
``CRIBA_VOICE_MAX_CONCURRENT`` to at least 60 and ``CRIBA_VOICE_DAILY_MINUTES`` to at least
1800 on the stack, or the stack refuses part-way):

1. ``voice_agents.py --check`` exits 0 with no drift lines (also this script's preflight).
2. R25: ``python -m live.voice_smoke`` (its docstring has the command).
3. R26(a): ``python -m live.voice_acceptance --base-url ... --elevenlabs-cap-seconds ...
   --out reports/voice-acceptance.md``.
4. ``python -m live.voice_acceptance --live-template --out reports/voice-acceptance.md``.
   Bring the public exposure up (only the live calls need it), place one call per country on
   ``/voice`` and the device checks (iOS Safari, Android Chrome), read the ElevenLabs conversation
   timestamps within a day (transcripts are kept one day) or time from a screen recording, bring
   the exposure down, and fill every cell of the ``live-calls`` block, exposure times included.
5. Record the clip during the five-minute recording (R26(c)), fill the ``clip`` block, run
   ``python -m live.voice_acceptance --verify reports/voice-acceptance.md`` until it
   exits 0, and commit the report. Latency is reported beside the targets and never decides
   anything; a device check you did not run is written ``not run``, never passed.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx

from live import voice_audio as audio
from live import voice_report as vr
from live import voice_smoke as vs
from live.client import Charge, LiveStack
from live.report import _table
from live.smoke import first_projection, password
from sim.spend import SpendGuard, SpendRefused
from sim.stack import is_sim_stack, login, new_phone
from sim.voice_driver import StreamedTurn, VoiceDriver, VoiceDriverError, VoiceRun

CLASSES = ("captured", "caught", "lost", "not_stored")
NOT_STORED_FIELDS = (
    "postcode",
    "vehicle",
    "engine_cc",
    "vehicle_year",
    "volunteered_age",
    "has_experience",
    "objection",
    "correction_cue",
)
CONFIRMED_STAGES = ("qualified", "approved", "interview_booked")
LOCALES = {"ES": "es-ES", "MX": "es-MX"}
STREET_BED = {"ES": "madrid", "MX": "cdmx"}
PLATFORM_ENUM = {"didi": "didi_food", "deliveroo": "other"}
DIGIT_WORDS = dict(
    zip(
        "0123456789",
        ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"],
        strict=True,
    )
)
WEEKDAYS_ES = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}
MONTHS_ES = {
    m: i
    for i, m in enumerate(
        [
            "enero",
            "febrero",
            "marzo",
            "abril",
            "mayo",
            "junio",
            "julio",
            "agosto",
            "septiembre",
            "octubre",
            "noviembre",
            "diciembre",
        ],
        start=1,
    )
}


class AcceptanceError(RuntimeError):
    """A call could not be set up. Never carries a secret or a token."""


# ---- classification ---------------------------------------------------------------------------


def _all_tokens_present(tokens: Sequence[str], found: Sequence[str]) -> bool:
    return all(any(alt in found for alt in token.split("|")) for token in tokens)


def _start_date_matches(got: Any, tokens: Sequence[str]) -> bool:
    if not got:
        return False
    try:
        recorded = date.fromisoformat(str(got))
    except ValueError:
        return False
    flat = {alt for token in tokens for alt in str(token).split("|")}
    weekdays = {WEEKDAYS_ES[w] for w in flat if w in WEEKDAYS_ES}
    months = {MONTHS_ES[w] for w in flat if w in MONTHS_ES}
    days = {int(w) for w in flat if w.isdigit()}
    if weekdays:
        return recorded.weekday() in weekdays
    if months:
        return recorded.month in months and (not days or recorded.day in days)
    return True


def recorded_check(c: Mapping[str, Any], app: Mapping[str, Any]) -> bool | None:
    """Whether the Application's record holds the value check `c` expects.

    ``None`` when the product has no counterpart for the field (`NOT_STORED_FIELDS`).
    """
    name = c["field"]
    expected: Any = c.get("expect")
    answers = app.get("answers") or {}
    if name in NOT_STORED_FIELDS:
        return None
    if name == "full_name":
        got = answers.get("full_name")
        return bool(got) and all(w in audio.words(str(got)) for w in audio.words(str(expected)))
    if name == "zone":
        ref = answers.get("zone")
        if not ref:
            return False
        text = " ".join(str(v) for v in ref.values()) if isinstance(ref, dict) else str(ref)
        return _all_tokens_present(list(expected), audio.words(text))
    if name == "driver_license":
        got = answers.get("driver_license")
        return got is not None and bool(got) == (expected == "yes")
    if name in ("availability", "preferred_schedule"):
        got = answers.get(name)
        return got is not None and set(got) == set(expected)
    if name == "experience_years":
        got = answers.get("experience_years")
        return got is not None and float(got) == float(expected)
    if name == "experience_platforms":
        got = answers.get("experience_platforms")
        wanted = {PLATFORM_ENUM.get(item, item) for item in expected}
        return got is not None and set(got) == wanted
    if name == "start_date":
        return _start_date_matches(answers.get("start_date"), list(expected))
    if name == "opt_out":
        return bool((app.get("candidate") or {}).get("opted_out"))
    if name == "withdrawn":
        return app.get("outcome") == "withdrawn"
    if name == "confirm":
        return app.get("stage") in CONFIRMED_STAGES if expected == "yes" else None
    if name == "slot":
        return app.get("interview") is not None
    return None


def classify(
    c: Mapping[str, Any], *, transcript_ok: bool, recorded_ok: bool | None, corrected: bool
) -> str:
    """captured, caught, lost, or not_stored (no counterpart on the record)."""
    if recorded_ok is None:
        return "not_stored"
    if transcript_ok and recorded_ok:
        return "captured"
    if not transcript_ok and corrected and recorded_ok:
        return "caught"
    return "lost"


def _echoed_wrong(said: str, transcript: str, reply: str) -> set[str]:
    """Tokens the transcript got wrong (four letters or more, or a number, not in what was said)
    that the reply repeats."""
    said_tokens = set(audio.norm_tokens(said))
    wrong = {
        t
        for t in audio.norm_tokens(transcript)
        if t not in said_tokens and (len(t) >= 4 or any(ch.isdigit() for ch in t))
    }
    return wrong & set(audio.norm_tokens(reply))


def read_back_wrong(said: str, transcript: str, reply: str) -> bool:
    """Whether `reply` echoes a value the transcript got wrong."""
    return bool(_echoed_wrong(said, transcript, reply))


def _value_tokens(c: Mapping[str, Any]) -> list[str]:
    if c["kind"] == "name":
        return audio.norm_tokens(str(c["expect"]))
    if c["kind"] == "digits":
        return [str(c["expect"])]
    if c["kind"] == "tokens":
        return [alt for token in c["expect"] for alt in str(token).split("|")]
    return []


def echoes_wrong_value(c: Mapping[str, Any], said: str, transcript: str, reply: str) -> bool:
    """Whether the reply read back a wrong value of the check `c`: a wrongly heard token it
    repeats must look like the check's own value (a different field's slip is not this one)."""
    echoed = _echoed_wrong(said, transcript, reply)
    if not echoed:
        return False
    targets = _value_tokens(c)
    if not targets:
        return True
    return any(SequenceMatcher(None, w, t).ratio() >= 0.6 for w in echoed for t in targets)


def correction_text(c: Mapping[str, Any], turn_text: str) -> str:
    """What the Candidate says to correct the read-back: "No, es" and the true value."""
    if c["kind"] == "name":
        return f"No, es {c['expect']}"
    if c["kind"] == "digits":
        return "No, es " + " ".join(DIGIT_WORDS[d] for d in str(c["expect"]) if d in DIGIT_WORDS)
    return f"No, es {turn_text.strip().rstrip('.')}"


# ---- records ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckOutcome:
    call: str
    country: str
    condition: str
    turn: int
    field: str
    kind: str
    klass: str
    transcript_ok: bool
    recorded_ok: bool | None


@dataclass(frozen=True)
class Usage:
    realtime_seconds: float = 0.0
    tts_characters: int = 0
    noise_seconds: int = 0


@dataclass
class ConversationResult:
    call: str
    country: str
    condition: str
    turns: int = 0
    corrections: int = 0
    charge: Charge | None = None
    elapsed_s: float = 0.0
    error: str | None = None
    not_run: str | None = None


@dataclass
class AcceptanceResult:
    exit_code: int
    checks: list[CheckOutcome] = field(default_factory=list)
    conversations: list[ConversationResult] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


def project(calls: Sequence[dict[str, Any]], conditions: Sequence[str]) -> audio.RunEstimate:
    return audio.estimate_run(calls, conditions)


# ---- the report -------------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def speech_lines(
    outcomes: Sequence[CheckOutcome],
    *,
    usage: Usage,
    baseline: Mapping[str, Any],
    projection: audio.RunEstimate,
    notes: Sequence[str] = (),
) -> list[str]:
    order = {name: i for i, name in enumerate(audio.CONDITIONS)}
    counts: dict[tuple[str, str], dict[str, int]] = {}
    by_field: dict[tuple[str, str, str], dict[str, int]] = {}
    for o in outcomes:
        counts.setdefault((o.country, o.condition), dict.fromkeys(CLASSES, 0))[o.klass] += 1
        by_field.setdefault((o.country, o.condition, o.field), dict.fromkeys(CLASSES, 0))[
            o.klass
        ] += 1
    lines = [
        "Spike 4's 20 scripted calls played through Scribe realtime into the custom-LLM endpoint "
        "of a voice conversation (ADR 0059), in three noise conditions. Each check is classified "
        "from the transcript and the answer recorded on the Application: **captured** (both hold "
        "the value), **caught** (the transcript lost it, the reply read a wrong value back, one "
        "correction turn was played and the record ends equal) or **lost** (any other end). Values "
        "the product does not store as an answer have no recorded counterpart and are counted "
        "apart as **not stored**; they are never counted as captured. The Candidate is synthetic "
        "and cleaner than a real one, so the rates are a ceiling. No text derived from the audio "
        "is kept here.",
        "",
        f"Projected before the run: {projection.realtime_seconds:.0f} realtime seconds, "
        f"{projection.tts_characters} text-to-speech characters, {projection.noise_seconds} s of "
        f"sound generation over {projection.conversations} conversations. Used: "
        f"{usage.realtime_seconds:.0f} realtime seconds, {usage.tts_characters} characters, "
        f"{usage.noise_seconds} s of sound generation.",
        "",
        "### Captured or caught, per country and condition",
        "",
    ]
    total_rows: list[list[object]] = []
    for (country, condition), row in sorted(
        counts.items(), key=lambda kv: (kv[0][0], order.get(kv[0][1], 9))
    ):
        classified = row["captured"] + row["caught"] + row["lost"]
        rate = (row["captured"] + row["caught"]) / classified if classified else None
        base = baseline.get(f"{country}|{condition}", {}).get("capture_rate")
        total_rows.append(
            [
                country,
                condition,
                classified,
                row["captured"],
                row["caught"],
                row["lost"],
                row["not_stored"],
                _pct(rate),
                _pct(base),
            ]
        )
    lines += _table(
        [
            "Country",
            "Condition",
            "Checks",
            "Captured",
            "Caught",
            "Lost",
            "Not stored",
            "Captured or caught",
            "Spike 4 baseline (no read-back)",
        ],
        total_rows,
    )
    lines += ["### Spike 4 baseline", ""]
    lines += _table(
        ["Country", "Condition", "Checks", "Captured", "Capture rate", "Word error rate"],
        [
            [
                key.split("|")[0],
                key.split("|")[1],
                value["checks"],
                value["captured"],
                _pct(value["capture_rate"]),
                _pct(value["wer"]),
            ]
            for key, value in sorted(
                baseline.items(),
                key=lambda kv: (kv[0].split("|")[0], order.get(kv[0].split("|")[1], 9)),
            )
        ],
    )
    lines += ["<details><summary>Counts per country, condition and field</summary>", ""]
    lines += _table(
        ["Country", "Condition", "Field", "Checks", "Captured", "Caught", "Lost", "Not stored"],
        [
            [
                country,
                condition,
                name,
                row["captured"] + row["caught"] + row["lost"],
                row["captured"],
                row["caught"],
                row["lost"],
                row["not_stored"],
            ]
            for (country, condition, name), row in sorted(
                by_field.items(), key=lambda kv: (kv[0][0], order.get(kv[0][1], 9), kv[0][2])
            )
        ],
    )
    lines += ["</details>", ""]
    if notes:
        lines += ["### Notes", "", *[f"- {n}" for n in notes], ""]
    return lines


# ---- the run ----------------------------------------------------------------------------------


@dataclass
class AcceptanceDeps:
    """Everything outside the process, injectable so the tests make no network call."""

    base: vs.Deps
    elevenlabs: Callable[[], httpx.Client]
    connect: Callable[..., Any] | None
    load_calls: Callable[[], list[dict[str, Any]]]
    load_voices: Callable[[], dict[str, str]]
    read_key: Callable[[], str]
    scratch_parent: Path | None
    phone_for_call: Callable[[dict[str, Any], str], str]
    baseline: Callable[[], dict[str, Any]]
    pace_s: float = 0.02
    drain_s: float = 2.0
    first_wait_s: float = 10.0


def default_deps(env: Mapping[str, str] | None = None) -> AcceptanceDeps:
    return AcceptanceDeps(
        base=vs.default_deps(env),
        elevenlabs=lambda: httpx.Client(timeout=180),
        connect=None,
        load_calls=audio.load_calls,
        load_voices=audio.load_voices,
        read_key=audio.read_api_key,
        scratch_parent=None,
        phone_for_call=lambda call, condition: new_phone(call["country"]),
        baseline=audio.load_baseline,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m live.voice_acceptance", description=(__doc__ or "").split("\n")[0]
    )
    p.add_argument("--base-url", help="the real-model stack, e.g. http://localhost:18141")
    p.add_argument("--out", type=Path, help="the report (voice-acceptance.md)")
    p.add_argument(
        "--elevenlabs-cap-seconds",
        type=float,
        help="required: the most realtime transcription seconds this run may use (no default)",
    )
    p.add_argument("--live-template", action="store_true", help="write the owner's blank blocks")
    p.add_argument(
        "--verify", type=Path, metavar="REPORT", help="exit 1 while a block or field is blank"
    )
    p.add_argument("--calls", help="comma list of spike 4 call ids (default: all 20)")
    p.add_argument(
        "--conditions",
        default=",".join(audio.CONDITIONS),
        help=f"comma list of {', '.join(audio.CONDITIONS)} (default: all)",
    )
    p.add_argument("--note", default="", help="one line for the report header")
    p.add_argument(
        "--model", default=vs.DEFAULT_MODEL, help="the turn model the stack should serve"
    )
    p.add_argument("--reply-timeout", type=float, default=90.0)
    p.add_argument("--llm-secret-file", type=Path, default=vs.DEFAULT_LLM_SECRET_FILE)
    p.add_argument("--seed-password-file", type=Path)
    return p


def _select(
    calls: Sequence[dict[str, Any]], ids: str | None, conditions: str
) -> tuple[list[dict[str, Any]], list[str]]:
    wanted_conditions = [c.strip() for c in conditions.split(",") if c.strip()]
    unknown = [c for c in wanted_conditions if c not in audio.CONDITIONS]
    if unknown or not wanted_conditions:
        raise ValueError(
            f"unknown condition {unknown[0] if unknown else ''!r}; choose from "
            f"{', '.join(audio.CONDITIONS)}"
        )
    if not ids:
        return list(calls), wanted_conditions
    by_id = {c["id"]: c for c in calls}
    picked = [i.strip() for i in ids.split(",") if i.strip()]
    missing = [i for i in picked if i not in by_id]
    if missing:
        raise ValueError(f"unknown call {missing[0]!r}; choose from {', '.join(by_id)}")
    return [by_id[i] for i in picked], wanted_conditions


class CallDriver(VoiceDriver):
    """The sim child's driver, used to start a voice conversation and to say transcripts."""

    def start_call(self, country: str, phone: str) -> VoiceRun:
        conversation = self._post(
            "/api/chat/conversations",
            {
                "country": country,
                "phone": phone,
                "locale": LOCALES[country],
                "client": "simulator",
                "channel": "voice",
            },
            201,
        )
        cid = str(conversation["conversation_id"])
        session = self._post(f"/api/chat/conversations/{cid}/voice-session", None, 201)
        return VoiceRun(conversation_id=cid, voice_token=str(session["voice_token"]))


@dataclass
class _State:
    """What one run keeps between conversations."""

    scratch: Path
    rng: random.Random
    key: str
    voices: dict[str, str]
    http: httpx.Client
    deps: AcceptanceDeps
    beds: dict[str, list[float]] | None = None
    realtime_seconds: float = 0.0
    tts_characters: int = 0
    noise_seconds: int = 0

    def usage(self) -> Usage:
        return Usage(self.realtime_seconds, self.tts_characters, self.noise_seconds)

    def noise(self) -> dict[str, list[float]]:
        """The three noise beds, requested once, on first use (a run that stops first buys none)."""
        if self.beds is None:
            made: dict[str, list[float]] = {}
            folder = self.scratch / "noise"
            folder.mkdir(parents=True, exist_ok=True)
            for name, (prompt, seconds) in audio.NOISE.items():
                pcm = audio.sound_pcm(prompt, seconds, api_key=self.key, http=self.http)
                (folder / f"{name}.pcm").write_bytes(pcm)
                made[name] = audio.browser_chain(audio.pcm_to_samples(pcm))
                self.noise_seconds += seconds
            self.beds = made
        return self.beds

    def clip(self, call: dict[str, Any], text: str, name: str) -> list[float]:
        """The Candidate's clip for `text`, made once per name and kept in the scratch dir."""
        path = self.scratch / "tts" / f"{name}.pcm"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            pcm = audio.tts_pcm(text, self.voices[call["voice"]], api_key=self.key, http=self.http)
            path.write_bytes(pcm)
            self.tts_characters += len(text)
        return audio.browser_chain(audio.pcm_to_samples(path.read_bytes()))

    async def hear(
        self,
        speech: list[float],
        *,
        country: str,
        condition: str,
        index: int,
        name: str,
        noisy: bool,
    ) -> str:
        """Mix `speech` for the condition, keep the mix in the scratch dir, transcribe it."""
        padded = audio.pad_speech(speech)
        snr = audio.CONDITIONS[condition] if noisy else None
        if snr is not None:
            beds = self.noise()
            bed = audio.noise_bed(
                self.rng,
                beds[STREET_BED[country]],
                beds["scooter"],
                len(padded),
                burst=index % 2 == 1,
            )
        else:
            bed = [0.0] * len(padded)
        pcm = audio.samples_to_pcm(audio.mix_condition(padded, bed, snr))
        path = self.scratch / "mixed" / condition / f"{name}.pcm"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pcm)
        self.realtime_seconds += len(pcm) / (audio.SR * 2)
        heard = await audio.transcribe_realtime(
            path.read_bytes(),
            api_key=self.key,
            connect=self.deps.connect,
            pace_s=self.deps.pace_s,
            first_wait_s=self.deps.first_wait_s,
            drain_s=self.deps.drain_s,
        )
        transcript = str(heard.get("transcript") or "")
        failures = [str(e.get("message_type")) for e in heard.get("events") or []]
        if failures and not transcript:
            raise audio.AudioError("Scribe realtime reported " + ", ".join(failures))
        return transcript


async def _converse(
    call: dict[str, Any],
    condition: str,
    *,
    state: _State,
    driver: CallDriver,
    stack: LiveStack,
    scrub: vr.Scrubber,
) -> tuple[ConversationResult, list[CheckOutcome]]:
    result = ConversationResult(call["id"], call["country"], condition)
    pending: list[tuple[int, dict[str, Any], bool, set[str]]] = []  # (turn, check, ok, corrected)
    phone = state.deps.phone_for_call(call, condition)
    run = driver.start_call(call["country"], phone)
    scrub.add(run.voice_token)
    for index, turn in enumerate(call["turns"]):
        name = f"{call['id']}_t{index:02d}"
        speech = state.clip(call, turn["text"], name)
        transcript = await state.hear(
            speech, country=call["country"], condition=condition, index=index, name=name, noisy=True
        )
        result.turns += 1
        corrected: set[str] = set()
        if transcript:
            reply: StreamedTurn = driver.say(run, transcript)[0]
            failing = [c for c in turn["checks"] if not audio.check(c, transcript)[0]]
            wrong = [
                c for c in failing if echoes_wrong_value(c, turn["text"], transcript, reply.text)
            ]
            if wrong:
                text = correction_text(wrong[0], turn["text"])
                fix = state.clip(call, text, f"{name}_fix")
                again = await state.hear(
                    fix,
                    country=call["country"],
                    condition=condition,
                    index=index,
                    name=f"{name}_fix",
                    noisy=False,
                )
                result.corrections += 1
                if again:
                    driver.say(run, again)
                corrected = {wrong[0]["field"]}  # one correction turn: only its field is caught
        for c in turn["checks"]:
            pending.append(
                (index, c, bool(transcript) and audio.check(c, transcript)[0], corrected)
            )
    try:
        app = await stack.application(phone)
        if app.get("stage") in vs.SUMMARY_STAGES:
            app = await stack.application(phone, with_summary=True, timeout_s=60.0)
        result.charge = stack.charge(app)
    except LookupError:
        app = {}
    outcomes = []
    for index, c, transcript_ok, corrected in pending:
        recorded = recorded_check(c, app)
        outcomes.append(
            CheckOutcome(
                call=call["id"],
                country=call["country"],
                condition=condition,
                turn=index,
                field=c["field"],
                kind=c["kind"],
                klass=classify(
                    c,
                    transcript_ok=transcript_ok,
                    recorded_ok=recorded,
                    corrected=c["field"] in corrected,
                ),
                transcript_ok=transcript_ok,
                recorded_ok=recorded,
            )
        )
    return result, outcomes


def _projection_message(projection: audio.RunEstimate) -> str:
    return (
        f"this run projects {projection.realtime_seconds:.0f} realtime seconds of transcription "
        f"and {projection.tts_characters} text-to-speech characters (plus "
        f"{projection.noise_seconds} s of sound generation) over {projection.conversations} "
        "conversations"
    )


async def _run_calls(
    calls: Sequence[dict[str, Any]],
    conditions: Sequence[str],
    *,
    cap: float,
    model: str,
    guard: SpendGuard,
    state: _State,
    driver: CallDriver,
    stack: LiveStack,
    scrub: vr.Scrubber,
    outcomes: list[CheckOutcome],
    conversations: list[ConversationResult],
) -> None:
    """Every call in every condition, in order. Stops before a conversation a cap cannot take
    (the rest are "not run") and after an error."""
    dearest = 0.0
    stopped: str | None = None
    for condition in conditions:
        for call in calls:
            need = audio.estimate_run([call], [condition]).realtime_seconds
            projected = dearest * 1.5 if dearest > 0 else first_projection(guard, model)
            if stopped is None and not guard.allow(projected):
                stopped = (
                    f"spend cap: ${guard.total_usd:.4f} counted, the next conversation "
                    f"projected ${projected:.4f}, cap ${guard.cap_usd:.2f}"
                )
            if stopped is None and state.realtime_seconds + need > cap:
                stopped = (
                    f"ElevenLabs cap: {state.realtime_seconds:.0f} s used, the next "
                    f"conversation needs about {need:.0f} s, cap {cap:g} s"
                )
            if stopped is not None:
                conversations.append(
                    ConversationResult(call["id"], call["country"], condition, not_run=stopped)
                )
                continue
            try:
                done, found = await _converse(
                    call, condition, state=state, driver=driver, stack=stack, scrub=scrub
                )
            except (VoiceDriverError, audio.AudioError, httpx.HTTPError) as exc:
                done = ConversationResult(
                    call["id"], call["country"], condition, error=scrub.clean(str(exc))
                )
                found = []
                stopped = f"stopped after an error in {call['id']} ({condition})"
            outcomes += found
            conversations.append(done)
            if done.charge is not None:
                dearest = max(dearest, done.charge.guard_usd)
            vs.say(
                scrub,
                f"  {call['id']} {condition}: {done.error or 'done'}, {done.turns} turns, "
                f"{done.corrections} corrections (total ${guard.total_usd:.4f})",
            )


async def acceptance(args: argparse.Namespace, deps: AcceptanceDeps) -> AcceptanceResult:
    """The speech recognition run: preflight, the calls in order, the report."""
    base = deps.base
    scrub = vr.Scrubber()
    try:
        if args.base_url is None or args.out is None:
            raise ValueError("the run needs --base-url and --out")
        if args.out.name == "live-smoke.md":
            raise ValueError("--out is the live smoke's report; use voice-acceptance.md")
        calls, conditions = _select(deps.load_calls(), args.calls, args.conditions)
        secret = vs.read_secret(args.llm_secret_file)
        scrub.add(secret)
        scrub.add(base.env.get("CRIBA_VOICE_PUBLIC_BASE_URL", ""))
        guard = SpendGuard.from_env(real=True, label="voice-acceptance", env=base.env)
    except (ValueError, SpendRefused) as exc:
        vs.say(scrub, f"refused: {exc}")
        vs.say(scrub, "real-model spend: $0.0000 (nothing was called)")
        return AcceptanceResult(vs.EXIT_REFUSED)

    projection = project(calls, conditions)

    def refuse(message: str) -> AcceptanceResult:
        vs.say(scrub, f"refused: {message}")
        return AcceptanceResult(vs.EXIT_REFUSED)

    code = vs.EXIT_DONE
    outcomes: list[CheckOutcome] = []
    conversations: list[ConversationResult] = []
    usage = Usage()
    notes: list[str] = []
    check: vs.AgentsCheck | None = None
    try:
        async with (
            base.async_client(args.base_url) as chat,
            base.async_client(args.base_url) as operator,
        ):
            if await is_sim_stack(chat):
                return refuse(
                    "this is a sim stack (stub model, sim clock); the run needs the real stack"
                )
            if not vs.in_window(base.now()):
                return refuse("outside 09:00-22:00 America/Mexico_City")
            check = base.check_agents()
            if check.exit_code != 0:
                return refuse(
                    f"voice_agents.py --check exited {check.exit_code} (1 drift, 2 cannot decide)"
                )
            cap = args.elevenlabs_cap_seconds
            if cap is None or cap <= 0:
                return refuse(
                    "--elevenlabs-cap-seconds is required and must be above 0 (it has no default); "
                    + _projection_message(projection)
                )
            if projection.realtime_seconds > cap:
                return refuse(
                    f"the projection is above --elevenlabs-cap-seconds {cap:g}: "
                    + _projection_message(projection)
                )
            vs.say(scrub, _projection_message(projection) + f" (cap {cap:g} s)")
            try:
                key = deps.read_key()
            except audio.AudioError as exc:
                return refuse(str(exc))
            scrub.add(key)
            try:
                seed = password(args)
                scrub.add(seed)
                await login(operator, seed)
            except (RuntimeError, OSError, httpx.HTTPError) as exc:
                return refuse(f"cannot log in as the Operator: {exc}")
            stack = LiveStack(
                chat, operator, guard, model=args.model, reply_timeout_s=args.reply_timeout
            )
            http = deps.elevenlabs()
            with (
                audio.scratch_dir(deps.scratch_parent) as scratch,
                base.sync_client(args.base_url) as client,
            ):
                state = _State(
                    scratch=scratch,
                    rng=random.Random(audio.NOISE_SEED),
                    key=key,
                    voices=deps.load_voices(),
                    http=http,
                    deps=deps,
                )
                driver = CallDriver(client, llm_secret=secret, reply_timeout_s=args.reply_timeout)
                try:
                    await _run_calls(
                        calls,
                        conditions,
                        cap=cap,
                        model=args.model,
                        guard=guard,
                        state=state,
                        driver=driver,
                        stack=stack,
                        scrub=scrub,
                        outcomes=outcomes,
                        conversations=conversations,
                    )
                except SpendRefused as exc:
                    vs.say(scrub, f"refused: {exc}")
                    code = vs.EXIT_SPEND
                usage = state.usage()
    finally:
        guard.save()
        vs.say(scrub, guard.summary())
    if check is not None and conversations:
        notes += [
            f"{c.call} ({c.condition}): not run: {c.not_run}" for c in conversations if c.not_run
        ] + [f"{c.call} ({c.condition}): {c.error}" for c in conversations if c.error]
        stamp = vs.run_stamp(base)
        header = vs.header_meta(
            args, base, guard, check, stamp, "not read by this run (the smoke section records it)"
        )
        section = vr.section_body(
            "Speech recognition run (R26(a))",
            stamp,
            speech_lines(
                outcomes, usage=usage, baseline=deps.baseline(), projection=projection, notes=notes
            ),
        )
        vr.write_report(
            args.out, {"header": vr.render_header(header), "speech": section}, scrubber=scrub
        )
        vs.say(scrub, f"wrote {args.out}")
    if code == vs.EXIT_DONE and any(c.error for c in conversations):
        code = vs.EXIT_ERROR
    return AcceptanceResult(code, outcomes, conversations, usage)


# ---- the owner's commands ---------------------------------------------------------------------


def template_command(args: argparse.Namespace, deps: AcceptanceDeps) -> int:
    if args.out is None:
        print("refused: --live-template needs --out", file=sys.stderr)
        return vs.EXIT_REFUSED
    if args.out.name == "live-smoke.md":
        print("refused: --out is the live smoke's report", file=sys.stderr)
        return vs.EXIT_REFUSED
    existing = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
    for block in vr.OWNER_SECTIONS:
        if vr.start_marker(block) in existing:
            print(
                f"refused: {args.out.name} already has the {block} block; edit it by hand "
                "(the template never overwrites what you wrote)",
                file=sys.stderr,
            )
            return vs.EXIT_REFUSED
    stamp = vs.run_stamp(deps.base)
    vr.write_report(args.out, vr.live_template(stamp))
    print(f"wrote the blank live-calls and clip blocks to {args.out}", file=sys.stderr)
    return vs.EXIT_DONE


def verify_command(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"refused: cannot read {path}: {exc.strerror}", file=sys.stderr)
        return vs.EXIT_REFUSED
    try:
        problems = vr.verify(text)
    except vr.ReportError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return vs.EXIT_REFUSED
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} problem(s): the report is not complete", file=sys.stderr)
        return vs.EXIT_ERROR
    print("the report is complete")
    return vs.EXIT_DONE


def main(argv: Sequence[str] | None = None, deps: AcceptanceDeps | None = None) -> int:
    args = build_parser().parse_args(argv)
    deps = deps or default_deps()
    if args.verify is not None and args.live_template:
        print("refused: choose --verify or --live-template, not both", file=sys.stderr)
        return vs.EXIT_REFUSED
    if args.verify is not None:
        return verify_command(args.verify)
    if args.live_template:
        return template_command(args, deps)
    return asyncio.run(acceptance(args, deps)).exit_code


if __name__ == "__main__":
    sys.exit(main())
