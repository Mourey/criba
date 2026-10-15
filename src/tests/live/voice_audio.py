"""Spike 4's audio pipeline and scorer, lifted for the owner-run speech recognition check
(voice-agent acceptance-live VA8, VA10; R26(a)).

Spike 4's script was throwaway code: it read the ElevenLabs key from a
hard-coded path, edited the import path to find `criba` and needed `numpy` and `ffmpeg`. This
module keeps its constants, its three noise conditions, its mixing rule and its scorer, and
drops those habits:

- the key is read only from this checkout's ``secrets/elevenlabs_api_key.txt``;
- nothing is imported from ``spikes/`` (its data files, ``calls.yaml`` and ``listening/key.json``,
  are read as data) and the import path is never edited;
- text-to-speech and sound generation are requested as ``pcm_16000``, so there is no decode step,
  and the mix is plain PCM16 arithmetic in the standard library (``array``, ``math``, ``random``);
- the HTTP and websocket transports are injectable, so every test runs without a network.

The synthetic Candidate clips, noise beds and mixes live in a temporary directory that
:func:`scratch_dir` removes when the run exits, on error and on Ctrl-C (VA10). Nothing audio-derived
goes into the report or the repository.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import math
import random
import re
import shutil
import sys
import tempfile
import unicodedata
from array import array
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import yaml

from criba.channel.optout import is_opt_out_request

REPO_ROOT = Path(__file__).resolve().parents[3]
KEY_FILE = REPO_ROOT / "secrets" / "elevenlabs_api_key.txt"
SPIKE_DIR = (
    REPO_ROOT / "src" / "tests" / "fixtures" / "spikes" / "004-spanish-voice"
)  # data files only, never imported
API = "https://api.elevenlabs.io"
SCRIBE_WS = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"

SR = 16_000
CONDITIONS: dict[str, float | None] = {"clean": None, "moderate": 10.0, "loud": 3.0}  # SNR in dB
TTS_MODEL = "eleven_v4_turbo"
SCRIBE_MODEL = "scribe_v2_realtime"
LANGUAGE = "es"
NOISE_SEED = 4
PEAK_CEILING = 0.95
PAD_LEAD_S = 0.4
PAD_TAIL_S = 0.8
NOISE: dict[str, tuple[str, int]] = {
    "madrid": (
        "Busy street in central Madrid at midday recorded on a phone: steady car traffic, a bus "
        "braking, distant horns, people chatting in Spanish on the pavement",
        22,
    ),
    "cdmx": (
        "Busy Mexico City street recorded on a phone: heavy traffic, a street vendor shouting "
        "through a loudspeaker, horns, a microbus engine idling, people talking",
        22,
    ),
    "scooter": (
        "A delivery motor scooter passing close by on a city street, engine buzz rising and fading",
        6,
    ),
}

# The projection (printed before the run; the run refuses above `--elevenlabs-cap-seconds`).
CHARS_PER_SECOND = 13.0
CORRECTION_ALLOWANCE_CHARS = 60
CORRECTION_ALLOWANCE_S = 5.0


class AudioError(RuntimeError):
    """A vendor call or a precondition failed. Never carries the API key."""


# ---- data and the key ------------------------------------------------------------------------


def load_calls(spike_dir: Path = SPIKE_DIR) -> list[dict[str, Any]]:
    """Spike 4's 20 scripted calls (``calls.yaml``)."""
    document = yaml.safe_load((spike_dir / "calls.yaml").read_text(encoding="utf-8"))
    return list(document["calls"])


def load_voices(spike_dir: Path = SPIKE_DIR) -> dict[str, str]:
    """``{voice name: ElevenLabs voice id}`` for the Candidate voices (``listening/key.json``)."""
    key = json.loads((spike_dir / "listening" / "key.json").read_text(encoding="utf-8"))
    return {name: str(voice["voice_id"]) for name, voice in key["candidate_voices"].items()}


def load_baseline(spike_dir: Path = SPIKE_DIR) -> dict[str, Any]:
    """Spike 4's no-read-back realtime scores per ``"<country>|<condition>"`` (the baseline)."""
    scores = json.loads((spike_dir / "results" / "scores_realtime.json").read_text("utf-8"))
    return dict(scores["summary"])


def read_api_key(repo_root: Path = REPO_ROOT) -> str:
    """The key from this checkout's ``secrets/elevenlabs_api_key.txt``, and nowhere else."""
    path = repo_root / "secrets" / "elevenlabs_api_key.txt"
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AudioError(
            "no ElevenLabs key in this checkout's secrets/elevenlabs_api_key.txt"
        ) from exc
    if not key:
        raise AudioError("secrets/elevenlabs_api_key.txt is empty")
    return key


def _redact(text: str, api_key: str) -> str:
    return text.replace(api_key, "[redacted]") if api_key else text


# ---- PCM and the mix --------------------------------------------------------------------------


def pcm_to_samples(data: bytes) -> list[float]:
    """Little-endian PCM16 to floats in [-1, 1]."""
    pcm = array("h")
    pcm.frombytes(data[: len(data) // 2 * 2])
    if sys.byteorder == "big":
        pcm.byteswap()
    return [v / 32768.0 for v in pcm]


def samples_to_pcm(samples: Sequence[float]) -> bytes:
    """Floats to little-endian PCM16, clipped to [-1, 1]."""
    pcm = array("h", (round(max(-1.0, min(1.0, v)) * 32767) for v in samples))
    if sys.byteorder == "big":
        pcm.byteswap()
    return pcm.tobytes()


def _biquad(
    samples: Sequence[float], b: tuple[float, float, float], a: tuple[float, float]
) -> list[float]:
    b0, b1, b2 = b
    a1, a2 = a
    x1 = x2 = y1 = y2 = 0.0
    out: list[float] = []
    for x in samples:
        y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        out.append(y)
        x2, x1 = x1, x
        y2, y1 = y1, y
    return out


def _butterworth(samples: Sequence[float], hz: float, *, high: bool) -> list[float]:
    """A second-order Butterworth high-pass or low-pass (RBJ cookbook) at `hz`."""
    w0 = 2 * math.pi * hz / SR
    alpha = math.sin(w0) / (2 * (1 / math.sqrt(2)))
    cos_w0 = math.cos(w0)
    a0 = 1 + alpha
    a = ((-2 * cos_w0) / a0, (1 - alpha) / a0)
    if high:
        b = ((1 + cos_w0) / 2 / a0, -(1 + cos_w0) / a0, (1 + cos_w0) / 2 / a0)
    else:
        b = ((1 - cos_w0) / 2 / a0, (1 - cos_w0) / a0, (1 - cos_w0) / 2 / a0)
    return _biquad(samples, b, a)


def browser_chain(samples: Sequence[float]) -> list[float]:
    """The browser-microphone-like band limit of spike 4: high-pass 100 Hz, low-pass 7.5 kHz."""
    return _butterworth(_butterworth(samples, 100.0, high=True), 7500.0, high=False)


def active_rms(x: Sequence[float]) -> float:
    """RMS of the frames (20 ms) whose energy is above a tenth of the loudest (spike 4)."""
    frame = SR // 50
    n = len(x) // frame
    if n == 0:
        raise ValueError("speech shorter than one 20 ms frame")
    energy = [
        math.sqrt(sum(v * v for v in x[i * frame : (i + 1) * frame]) / frame + 1e-12)
        for i in range(n)
    ]
    top = max(energy)
    active = [e for e in energy if e > top * 0.1]
    return math.sqrt(sum(e * e for e in active) / len(active))


def pad_speech(speech: Sequence[float]) -> list[float]:
    """The clip with spike 4's lead and tail silence (0.4 s, 0.8 s)."""
    return [0.0] * int(PAD_LEAD_S * SR) + list(speech) + [0.0] * int(PAD_TAIL_S * SR)


def noise_bed(
    rng: random.Random,
    street: Sequence[float],
    scooter: Sequence[float],
    n: int,
    *,
    burst: bool,
) -> list[float]:
    """`n` samples of the street bed from a random start, with a scooter burst when `burst`."""
    start = rng.randrange(len(street))
    rolled = list(street[start:]) + list(street[:start])
    bed = [rolled[i % len(rolled)] for i in range(n)]
    if burst:
        at = rng.randrange(max(1, n - len(scooter) // 2))
        segment = scooter[: n - at]
        for i, v in enumerate(segment):
            bed[at + i] += v * 1.5
    return bed


def mix_condition(
    speech: Sequence[float], bed: Sequence[float], snr_db: float | None
) -> list[float]:
    """`speech` with `bed` at `snr_db` against its active speech level; `None` is clean.

    The peak is held under 0.95 so the PCM never clips.
    """
    if snr_db is None:
        mixed = list(speech)
    else:
        speech_rms = active_rms(speech)
        bed_rms = math.sqrt(sum(v * v for v in bed) / len(bed) + 1e-12)
        gain = (speech_rms / bed_rms) / (10 ** (snr_db / 20))
        mixed = [s + b * gain for s, b in zip(speech, bed, strict=True)]
    peak = max((abs(v) for v in mixed), default=0.0)
    if peak > PEAK_CEILING:
        scale = PEAK_CEILING / peak
        mixed = [v * scale for v in mixed]
    return mixed


# ---- vendor calls (injectable transports) -----------------------------------------------------


def _client(http: httpx.Client | None) -> httpx.Client:
    return http if http is not None else httpx.Client(timeout=120)


def _post(
    http: httpx.Client,
    path: str,
    *,
    params: dict[str, str],
    body: dict[str, Any],
    api_key: str,
    what: str,
) -> bytes:
    response = http.post(
        API + path, params=params, json=body, headers={"xi-api-key": api_key}, timeout=180
    )
    if response.status_code != 200:
        raise AudioError(
            f"{what} failed: HTTP {response.status_code} {_redact(response.text[:200], api_key)}"
        )
    return response.content


def tts_pcm(text: str, voice_id: str, *, api_key: str, http: httpx.Client | None = None) -> bytes:
    """A Candidate clip as PCM16 at 16 kHz."""
    return _post(
        _client(http),
        f"/v1/text-to-speech/{voice_id}",
        params={"output_format": "pcm_16000"},
        body={"text": text, "model_id": TTS_MODEL, "language_code": LANGUAGE},
        api_key=api_key,
        what=f"text-to-speech {voice_id}",
    )


def sound_pcm(
    prompt: str, seconds: int, *, api_key: str, http: httpx.Client | None = None
) -> bytes:
    """A noise bed as PCM16 at 16 kHz."""
    return _post(
        _client(http),
        "/v1/sound-generation",
        params={"output_format": "pcm_16000"},
        body={"text": prompt, "duration_seconds": seconds, "prompt_influence": 0.5},
        api_key=api_key,
        what="sound generation",
    )


def scribe_url() -> str:
    """The realtime transcription URL: manual commit, 16 kHz PCM, Spanish. No credentials."""
    query = urlencode(
        {
            "model_id": SCRIBE_MODEL,
            "audio_format": "pcm_16000",
            "commit_strategy": "manual",
            "language_code": LANGUAGE,
        }
    )
    return f"{SCRIBE_WS}?{query}"


def _default_connect() -> Callable[..., Any]:
    import websockets  # in the backend lock transitively; only the owner-run path needs it

    return websockets.connect


async def _stream_to_scribe(
    pcm: bytes,
    *,
    api_key: str,
    connect: Callable[..., Any] | None = None,
    pace_s: float = 0.02,
    first_wait_s: float = 10.0,
    drain_s: float = 2.0,
) -> dict[str, Any]:
    """Stream `pcm` to Scribe realtime in 100 ms chunks, commit on the last, return the transcript.

    ``{"transcript": text, "language_code"?, "events"?, "timeout"?}`` as spike 4's `rt_one`.
    """
    opener = connect if connect is not None else _default_connect()
    chunk = SR * 2 // 10
    texts: list[str] = []
    stamped: list[str] = []
    meta: dict[str, Any] = {}
    async with opener(
        scribe_url(), additional_headers={"xi-api-key": api_key}, max_size=None
    ) as ws:
        for offset in range(0, len(pcm), chunk):
            last = offset + chunk >= len(pcm)
            await ws.send(
                json.dumps(
                    {
                        "message_type": "input_audio_chunk",
                        "audio_base_64": base64.b64encode(pcm[offset : offset + chunk]).decode(),
                        "commit": last,
                        "sample_rate": SR,
                    }
                )
            )
            await asyncio.sleep(pace_s)
        wait = first_wait_s
        try:
            while True:
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=wait))
                kind = message.get("message_type")
                if kind in ("committed_transcript", "committed_transcript_with_timestamps"):
                    (texts if kind == "committed_transcript" else stamped).append(
                        message.get("text") or ""
                    )
                    if message.get("language_code"):
                        meta["language_code"] = message["language_code"]
                    wait = drain_s  # the paired message follows at once; then stop
                elif kind not in ("session_started", "partial_transcript"):
                    meta.setdefault("events", []).append(
                        {k: v for k, v in message.items() if k != "config"}
                    )
                    if kind and (
                        "error" in kind or kind in ("quota_exceeded", "rate_limited", "input_error")
                    ):
                        break
        except TimeoutError:
            if not texts and not stamped:
                meta["timeout"] = True
    transcript = " ".join(t for t in (texts or stamped) if t).strip()
    return {"transcript": transcript, **meta}


async def transcribe_realtime(
    pcm: bytes,
    *,
    api_key: str,
    connect: Callable[..., Any] | None = None,
    pace_s: float = 0.02,
    first_wait_s: float = 10.0,
    drain_s: float = 2.0,
) -> dict[str, Any]:
    """:func:`_stream_to_scribe`, with a failed connection reported as an :class:`AudioError`
    (without the key) instead of the websocket library's own exception."""
    try:
        return await _stream_to_scribe(
            pcm,
            api_key=api_key,
            connect=connect,
            pace_s=pace_s,
            first_wait_s=first_wait_s,
            drain_s=drain_s,
        )
    except Exception as exc:
        if isinstance(exc, OSError) or type(exc).__module__.startswith("websockets"):
            reason = _redact(f"{type(exc).__name__}: {exc}", api_key)
            raise AudioError(f"realtime transcription failed ({reason})") from exc
        raise


# ---- the projection and the scratch directory ------------------------------------------------


@dataclass(frozen=True)
class RunEstimate:
    conversations: int
    realtime_seconds: float
    tts_characters: int
    noise_seconds: int


def estimate_run(
    calls: Sequence[dict[str, Any]], conditions: Sequence[str] | None = None
) -> RunEstimate:
    """Realtime transcription seconds and text-to-speech characters, before the run starts.

    Each call is played once per condition. A turn's speech is estimated from its text length;
    every conversation gets one correction turn's allowance. The clips are synthesised once per
    turn and mixed per condition, so the text counts once.
    """
    names = list(conditions) if conditions is not None else list(CONDITIONS)
    pad = PAD_LEAD_S + PAD_TAIL_S
    conversations = len(calls) * len(names)
    pass_seconds = 0.0
    characters = 0
    for call in calls:
        for turn in call["turns"]:
            size = len(turn["text"])
            characters += size
            pass_seconds += size / CHARS_PER_SECOND + pad
    return RunEstimate(
        conversations=conversations,
        realtime_seconds=len(names) * pass_seconds + conversations * CORRECTION_ALLOWANCE_S,
        tts_characters=characters + conversations * CORRECTION_ALLOWANCE_CHARS,
        noise_seconds=sum(seconds for _, seconds in NOISE.values()),
    )


@contextlib.contextmanager
def scratch_dir(parent: Path | None = None) -> Iterator[Path]:
    """A temporary directory for clips, beds and mixes, removed on exit, error and Ctrl-C.

    Refused inside the repository: nothing audio-derived is kept there (VA10).
    """
    if parent is not None and (
        parent.resolve() == REPO_ROOT or REPO_ROOT in parent.resolve().parents
    ):
        raise AudioError("the scratch directory must be outside the repository")
    made = Path(tempfile.mkdtemp(prefix="criba-voice-audio-", dir=parent))
    try:
        yield made
    finally:
        shutil.rmtree(made, ignore_errors=True)


# ---- normalising and scoring (spike 4, unchanged) --------------------------------------------

UNITS = {
    "cero": 0,
    "uno": 1,
    "un": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
}
TEENS = {
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiuno": 21,
    "veintiun": 21,
    "veintiuna": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
}
TENS = {
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
}
HUNDREDS = {
    "cien": 100,
    "ciento": 100,
    "doscientos": 200,
    "trescientos": 300,
    "cuatrocientos": 400,
    "quinientos": 500,
    "seiscientos": 600,
    "setecientos": 700,
    "ochocientos": 800,
    "novecientos": 900,
}


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def words(text: str) -> list[str]:
    t = strip_accents(text)
    t = re.sub(r"(?<=\d)[.,](?=\d{3}\b)", "", t)  # 28.007 -> 28007
    t = re.sub(r"(?<=\d)[:h](?=\d)", " ", t)  # 10:30 -> 10 30
    t = re.sub(r"\b(\d+)[.,](\d{1,2})\b", r"\1d\2", t)  # 1,5 -> 1d5 (decimal)
    t = re.sub(r"(\d+)\s*(am|pm|a\.m\.|p\.m\.)", r"\1", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return t.split()


def digitize(tokens: list[str]) -> list[str]:
    """Replace Spanish number words with digit strings ("veintiocho mil siete" -> "28007")."""
    out: list[str] = []
    total = seg = 0
    last: str | None = None
    i = 0

    def flush() -> None:
        nonlocal total, seg, last
        if last is not None:
            out.append(str(total + seg))
        total = seg = 0
        last = None

    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if t in UNITS:
            v = UNITS[t]
            if last == "tens_y" or (last in ("hundreds", "mil") and v != 0):
                seg += v
            else:
                flush()
                seg = v
            last = "units"
        elif t in TEENS:
            if last in ("hundreds", "mil"):
                seg += TEENS[t]
            else:
                flush()
                seg = TEENS[t]
            last = "teens"
        elif t in TENS:
            if last in ("hundreds", "mil"):
                seg += TENS[t]
            else:
                flush()
                seg = TENS[t]
            last = "tens"
        elif t in HUNDREDS:
            if last == "mil":
                seg += HUNDREDS[t]
            else:
                flush()
                seg = HUNDREDS[t]
            last = "hundreds"
        elif t == "mil":
            total = (total + (seg or 1)) * 1000
            seg = 0
            last = "mil"
        elif t == "y" and last == "tens" and nxt in UNITS:
            last = "tens_y"
        else:
            flush()
            out.append(t)
        i += 1
    flush()
    return out


def norm_tokens(text: str) -> list[str]:
    return digitize(words(text))


def digit_runs(tokens: list[str]) -> list[str]:
    runs, cur = [], ""
    for t in tokens:
        if t.isdigit():
            cur += t
        else:
            if cur:
                runs.append(cur)
            cur = ""
    if cur:
        runs.append(cur)
    return runs


def spoken_digits(text: str) -> str:
    """The digit runs of `text` joined, with number words digitised ("veintiocho cero diez")."""
    return "".join(digit_runs(norm_tokens(text)))


def numbers(tokens: list[str]) -> set[float]:
    nums: set[float] = set()
    for j, t in enumerate(tokens):
        nxt = tokens[j + 1 : j + 4]
        if re.fullmatch(r"\d+d\d+", t):
            nums.add(float(t.replace("d", ".")))
        if t.isdigit():
            n = float(t)
            nums.add(n)
            if nxt[:1] in (["meses"], ["mes"]):
                nums.add(round(n / 12, 2))
            if "medio" in nxt and "y" in nxt:
                nums.add(n + 0.5)
        if t in ("ano", "anos") and nxt[:2] == ["y", "medio"]:
            nums.add(1.5)
        if t == "medio" and nxt[:1] in (["ano"],):
            nums.add(0.5)
    return nums


CHOICES = {
    "vehicle": {
        "moto": [
            "moto",
            "motos",
            "motoneta",
            "motocicleta",
            "motociclista",
            "scooter",
            "italika",
            "vespa",
        ],
        "car": ["coche", "carro", "auto", "automovil"],
        "bike": ["bici", "bicicleta"],
        "e_scooter": ["patinete"],
    },
    "availability": {
        "full_time": ["jornada completa", "tiempo completo", "full time"],
        "part_time": ["media jornada", "medio tiempo", "part time", "tiempo parcial"],
        "weekends": ["fines de semana", "fin de semana", "weekends", "weekend"],
    },
    "schedule": {
        "morning": ["manana", "mananas", "morning"],
        "afternoon": ["tarde", "tardes"],
        "evening": ["noche", "noches", "evening"],
        "flexible": ["flexible", "me da igual"],
    },
    "platform": {
        "glovo": ["glovo"],
        "uber_eats": ["uber eats", "ubereats", "uber eat"],
        "rappi": ["rappi", "rapi"],
        "didi": ["didi", "didi food"],
        "just_eat": ["just eat", "justeat"],
        "deliveroo": ["deliveroo"],
    },
}


def found_choices(group: str, tokens: list[str]) -> set[str]:
    text = " " + " ".join(t.replace("_", " ") for t in tokens) + " "
    text = text.replace(" part time ", " part time ").replace(" fulltime ", " full time ")
    return {canon for canon, syns in CHOICES[group].items() if any(f" {s} " in text for s in syns)}


YES = [
    "si",
    "claro",
    "por supuesto",
    "vale",
    "simon",
    "aja",
    "como no",
    "orale",
    "andale",
    "okay",
    "ok",
    "yes",
    "correcto",
    "eso es",
    "exacto",
    "perfecto",
]
NO = ["no", "que va", "para nada", "nel", "nop", "nunca"]


def yesno(tokens: list[str]) -> str | None:
    text = " " + " ".join(tokens) + " "
    text = text.replace(" como no ", " como_si ")
    has_no = any(f" {w} " in text for w in NO)
    has_yes = any(f" {w} " in text for w in YES) or " como_si " in text
    if has_no:
        return "no"
    return "yes" if has_yes else None


OPTOUT_CUES = [
    "baja",
    "stop",
    "no me llameis",
    "no me llamen",
    "no me marquen",
    "borrenme",
    "borradme",
    "dejad de",
    "dejen de",
    "no me escribais",
    "no me escriban",
]
WITHDRAW_CUES = [
    "no me interesa",
    "encontrado otro",
    "encontre otro",
    "otro curro",
    "otro jale",
    "otro trabajo",
]


def has_cue(cues: list[str], tokens: list[str]) -> bool:
    text = " " + " ".join(tokens) + " "
    return any(f" {c} " in text for c in cues)


def check(c: dict[str, Any], transcript: str) -> tuple[bool, str]:
    """Whether `transcript` holds the value check `c` expects, and what was read."""
    toks = norm_tokens(transcript)
    kind = c["kind"]
    exp: Any = c.get("expect")
    if kind == "name":
        want = words(exp)
        return all(w in toks for w in want), " ".join(toks)
    if kind == "digits":
        runs = digit_runs(toks)
        return any(exp in r for r in runs), ",".join(runs)
    if kind == "yesno":
        got = yesno(toks)
        return got == exp, str(got)
    if kind == "choice":
        found = found_choices(c["group"], toks)
        return found == set(exp), ",".join(sorted(found))
    if kind == "number":
        nums = numbers(toks)
        return float(exp) in nums, ",".join(f"{n:g}" for n in sorted(nums))
    if kind == "tokens":
        ok = all(any(alt in toks for alt in tok.split("|")) for tok in exp)
        return ok, " ".join(toks)
    if kind == "optout":
        return has_cue(OPTOUT_CUES, toks), f"rule={is_opt_out_request(transcript)}"
    if kind == "withdraw":
        return has_cue(WITHDRAW_CUES, toks), ""
    raise ValueError(kind)


def wer_tokens(text: str) -> list[str]:
    out: list[str] = []
    for t in norm_tokens(text):
        if t.isdigit() and out and out[-1].isdigit():
            out[-1] += t
        else:
            out.append(t)
    return [t for t in out if t not in ("eh", "ehh", "mmm", "uy", "este", "ah")]


def edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def selfcheck(calls: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Spike 4's `step_selfcheck`: every check the scorer fails on the ground-truth text."""
    bad: list[dict[str, Any]] = []
    for call in calls:
        for index, turn in enumerate(call["turns"]):
            for c in turn["checks"]:
                ok, got = check(c, turn["text"])
                if not ok:
                    bad.append(
                        {
                            "call": call["id"],
                            "turn": index,
                            "field": c["field"],
                            "kind": c["kind"],
                            "read_as": got,
                        }
                    )
    return bad


def score_rows(rows: Sequence[dict[str, Any]], calls: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Spike 4's `step_score` over transcript rows: ``{"summary", "failures", "optout"}``."""
    by_turn = {(c["id"], i): t for c in calls for i, t in enumerate(c["turns"])}
    summary: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    optout_rows: list[dict[str, Any]] = []
    for r in rows:
        turn = by_turn[(r["call"], r["turn"])]
        key = f"{r['country']}|{r['condition']}"
        s = summary.setdefault(
            key,
            {
                "checks": 0,
                "captured": 0,
                "turns": 0,
                "turns_all_ok": 0,
                "errs": 0,
                "ref_words": 0,
                "rt_errors": 0,
            },
        )
        s["turns"] += 1
        if r.get("error") or r.get("timeout"):
            s["rt_errors"] += 1
        ref, hyp = wer_tokens(r["text"]), wer_tokens(r["transcript"])
        s["errs"] += edit_distance(ref, hyp)
        s["ref_words"] += len(ref)
        all_ok = True
        for c in turn["checks"]:
            ok, got = check(c, r["transcript"])
            s["checks"] += 1
            s["captured"] += ok
            if not ok:
                all_ok = False
                failures.append(
                    {
                        "call": r["call"],
                        "turn": r["turn"],
                        "condition": r["condition"],
                        "field": c["field"],
                        "kind": c["kind"],
                        "expect": c.get("expect"),
                        "said": r["text"],
                        "transcript": r["transcript"],
                        "read_as": got,
                    }
                )
            if c["kind"] == "optout":
                optout_rows.append(
                    {
                        "call": r["call"],
                        "turn": r["turn"],
                        "condition": r["condition"],
                        "said": r["text"],
                        "transcript": r["transcript"],
                        "cue": ok,
                        "rule_on_said": is_opt_out_request(r["text"]),
                        "rule_on_transcript": is_opt_out_request(r["transcript"]),
                    }
                )
        s["turns_all_ok"] += all_ok
    for s in summary.values():
        s["capture_rate"] = round(s["captured"] / s["checks"], 4) if s["checks"] else None
        s["wer"] = round(s["errs"] / s["ref_words"], 4) if s["ref_words"] else None
    return {"summary": summary, "failures": failures, "optout": optout_rows}


__all__ = [
    "API",
    "CHARS_PER_SECOND",
    "CONDITIONS",
    "CORRECTION_ALLOWANCE_CHARS",
    "CORRECTION_ALLOWANCE_S",
    "KEY_FILE",
    "NOISE",
    "NOISE_SEED",
    "PAD_LEAD_S",
    "PAD_TAIL_S",
    "REPO_ROOT",
    "SCRIBE_MODEL",
    "SR",
    "TTS_MODEL",
    "AudioError",
    "RunEstimate",
    "active_rms",
    "browser_chain",
    "check",
    "digit_runs",
    "digitize",
    "edit_distance",
    "estimate_run",
    "load_baseline",
    "load_calls",
    "load_voices",
    "mix_condition",
    "noise_bed",
    "norm_tokens",
    "pad_speech",
    "pcm_to_samples",
    "read_api_key",
    "samples_to_pcm",
    "score_rows",
    "scratch_dir",
    "scribe_url",
    "selfcheck",
    "sound_pcm",
    "spoken_digits",
    "transcribe_realtime",
    "tts_pcm",
    "wer_tokens",
    "words",
]
