"""The lifted audio pipeline and scorer (voice-agent acceptance-live VA8, VA10).

Nothing here calls ElevenLabs: text-to-speech and sound generation go through an
`httpx.MockTransport`, the realtime transcription through a fake websocket, and the scorer reads
spike 4's committed data files (`calls.yaml`, `results/*.json`).
"""

from __future__ import annotations

import ast
import asyncio
import base64
import json
import math
import random
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from live import voice_audio as va

SPIKE = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "tests"
    / "fixtures"
    / "spikes"
    / "004-spanish-voice"
)
SECRET_WORD = "-".join(("elevenlabs", "key", "for", "tests"))


def calls() -> list[dict[str, Any]]:
    return va.load_calls()


# ---- the scorer -------------------------------------------------------------------------------


def test_scorer_self_check() -> None:
    """Spike 4's `step_selfcheck`: the scorer must pass every check on the ground-truth text."""
    assert va.selfcheck(calls()) == []


def test_scorer_self_check_catches_a_broken_expectation() -> None:
    broken = [
        {
            "id": "x",
            "country": "ES",
            "turns": [
                {
                    "text": "Vivo en Madrid.",
                    "checks": [{"kind": "tokens", "field": "zone", "expect": ["barcelona"]}],
                }
            ],
        }
    ]
    failures = va.selfcheck(broken)
    assert len(failures) == 1
    assert failures[0]["field"] == "zone"


def test_scorer_reproduces_the_realtime_summary() -> None:
    rows = json.loads((SPIKE / "results" / "transcripts_realtime.json").read_text("utf-8"))
    expected = json.loads((SPIKE / "results" / "scores_realtime.json").read_text("utf-8"))
    scored = va.score_rows(rows, calls())
    assert scored["summary"] == expected["summary"]
    assert len(scored["failures"]) == len(expected["failures"])


def test_baseline_is_spike_4s_no_read_back_realtime_summary() -> None:
    baseline = va.load_baseline()
    assert set(baseline) == {f"{c}|{n}" for c in ("ES", "MX") for n in va.CONDITIONS}
    assert all(0 < v["capture_rate"] <= 1 for v in baseline.values())


def test_digitiser_reads_spoken_postcodes() -> None:
    assert va.digit_runs(va.norm_tokens("el veintiocho cero diez")) == ["28010"]
    assert va.digit_runs(va.norm_tokens("código postal 28.010")) == ["28010"]
    assert va.digit_runs(va.norm_tokens("cero cuatro cero cero uno")) == ["04001"]
    assert va.spoken_digits("veintiocho cero diez") == "28010"


def test_check_kinds_are_the_spikes() -> None:
    assert va.check({"kind": "digits", "expect": "28010"}, "es veintiocho cero diez")[0]
    assert not va.check({"kind": "digits", "expect": "28011"}, "es veintiocho cero diez")[0]
    assert va.check({"kind": "yesno", "expect": "no"}, "no, para nada")[0]
    assert va.check({"kind": "number", "expect": 1.5}, "año y medio")[0]
    assert va.check({"kind": "name", "expect": "Lucía Gil"}, "soy lucia gil")[0]
    with pytest.raises(ValueError):
        va.check({"kind": "nope"}, "x")


# ---- constants --------------------------------------------------------------------------------


def test_conditions_and_constants() -> None:
    assert va.CONDITIONS == {"clean": None, "moderate": 10.0, "loud": 3.0}
    assert va.SR == 16_000
    assert va.TTS_MODEL == "eleven_v4_turbo"
    assert va.SCRIBE_MODEL == "scribe_v2_realtime"
    assert va.NOISE_SEED == 4
    url = urlparse(va.scribe_url())
    query = parse_qs(url.query)
    assert url.scheme == "wss"
    assert url.netloc == "api.elevenlabs.io"
    assert url.path == "/v1/speech-to-text/realtime"
    assert query == {
        "model_id": ["scribe_v2_realtime"],
        "audio_format": ["pcm_16000"],
        "commit_strategy": ["manual"],
        "language_code": ["es"],
    }
    assert set(va.NOISE) == {"madrid", "cdmx", "scooter"}


def test_tts_and_sound_requests_use_pcm_16000_and_the_key_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"\x00\x01" * 8)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert va.tts_pcm("Hola", "voice-id-1", api_key=SECRET_WORD, http=http) == b"\x00\x01" * 8
    assert va.sound_pcm("a street", 6, api_key=SECRET_WORD, http=http) == b"\x00\x01" * 8
    tts, sound = seen
    assert tts.url.path == "/v1/text-to-speech/voice-id-1"
    assert tts.url.params["output_format"] == "pcm_16000"
    assert json.loads(tts.content) == {
        "text": "Hola",
        "model_id": "eleven_v4_turbo",
        "language_code": "es",
    }
    assert sound.url.path == "/v1/sound-generation"
    assert sound.url.params["output_format"] == "pcm_16000"
    assert json.loads(sound.content)["duration_seconds"] == 6
    for request in seen:
        assert request.headers["xi-api-key"] == SECRET_WORD
        assert SECRET_WORD not in str(request.url)


def test_a_vendor_error_does_not_carry_the_key() -> None:
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no")))
    with pytest.raises(va.AudioError) as raised:
        va.tts_pcm("Hola", "v", api_key=SECRET_WORD, http=http)
    assert "401" in str(raised.value)
    assert SECRET_WORD not in str(raised.value)


# ---- the mix ----------------------------------------------------------------------------------


def tone(seconds: float, amplitude: float = 0.2, hz: float = 220.0) -> list[float]:
    n = int(seconds * va.SR)
    return [amplitude * math.sin(2 * math.pi * hz * i / va.SR) for i in range(n)]


def rms(values: list[float]) -> float:
    return math.sqrt(sum(v * v for v in values) / len(values))


@pytest.mark.parametrize("snr_db", [10.0, 3.0])
def test_mix_hits_the_target_snr(snr_db: float) -> None:
    speech = va.pad_speech(tone(1.0))
    rng = random.Random(7)
    bed = [rng.gauss(0.0, 0.05) for _ in speech]
    mixed = va.mix_condition(speech, bed, snr_db)
    noise_part = [m - s for m, s in zip(mixed, speech, strict=True)]
    measured = 20 * math.log10(va.active_rms(speech) / rms(noise_part))
    assert abs(measured - snr_db) < 0.5
    assert max(abs(v) for v in mixed) <= 0.95 + 1e-9


def test_the_clean_condition_leaves_the_speech_alone() -> None:
    speech = va.pad_speech(tone(0.5))
    assert va.mix_condition(speech, [0.5] * len(speech), None) == speech


def test_mix_never_clips_above_the_ceiling() -> None:
    speech = va.pad_speech(tone(0.5, amplitude=0.9))
    mixed = va.mix_condition(speech, [0.4] * len(speech), 3.0)
    assert max(abs(v) for v in mixed) <= 0.95 + 1e-9


def test_pad_speech_adds_the_spikes_silence() -> None:
    padded = va.pad_speech([0.1] * 100)
    assert len(padded) == 100 + int(0.4 * va.SR) + int(0.8 * va.SR)
    assert padded[: int(0.4 * va.SR)] == [0.0] * int(0.4 * va.SR)


def test_noise_bed_is_deterministic_and_has_a_burst_on_odd_turns() -> None:
    street = tone(1.0, 0.1, 100.0)
    scooter = tone(0.2, 0.3, 300.0)
    one = va.noise_bed(random.Random(va.NOISE_SEED), street, scooter, 24_000, burst=False)
    two = va.noise_bed(random.Random(va.NOISE_SEED), street, scooter, 24_000, burst=False)
    assert one == two
    assert len(one) == 24_000
    with_burst = va.noise_bed(random.Random(va.NOISE_SEED), street, scooter, 24_000, burst=True)
    assert max(with_burst) > max(one)


def test_pcm_round_trip() -> None:
    samples = [0.0, 0.5, -0.5, 1.0, -1.0]
    back = va.pcm_to_samples(va.samples_to_pcm(samples))
    assert len(back) == len(samples)
    for a, b in zip(samples, back, strict=True):
        assert abs(a - b) < 1e-3
    # Out-of-range values are clipped, not wrapped.
    assert va.pcm_to_samples(va.samples_to_pcm([5.0, -5.0])) == pytest.approx([1.0, -1.0], abs=1e-3)


def test_browser_chain_removes_low_rumble_and_keeps_speech_band() -> None:
    rumble = va.browser_chain(tone(1.0, 0.3, 30.0))
    voice = va.browser_chain(tone(1.0, 0.3, 1000.0))
    assert rms(rumble[4000:]) < 0.3 * 0.7071 * 0.5
    assert rms(voice[4000:]) > 0.3 * 0.7071 * 0.9


# ---- the key ----------------------------------------------------------------------------------


def test_key_is_read_only_from_this_checkout(tmp_path: Path) -> None:
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "elevenlabs_api_key.txt").write_text(f"  {SECRET_WORD}\n")
    assert va.read_api_key(tmp_path) == SECRET_WORD


def test_a_missing_key_is_refused_without_looking_elsewhere(tmp_path: Path) -> None:
    with pytest.raises(va.AudioError) as raised:
        va.read_api_key(tmp_path)
    assert "secrets/elevenlabs_api_key.txt" in str(raised.value)
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "elevenlabs_api_key.txt").write_text("\n")
    with pytest.raises(va.AudioError):
        va.read_api_key(tmp_path)


def test_default_key_path_is_this_checkouts_secrets_file() -> None:
    assert va.KEY_FILE == va.REPO_ROOT / "secrets" / "elevenlabs_api_key.txt"
    assert Path(__file__).resolve().parents[3] == va.REPO_ROOT


def test_no_import_from_spikes() -> None:
    source = Path(va.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any("spike" in alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert "spike" not in (node.module or "")
        if isinstance(node, ast.Attribute) and node.attr == "path":
            assert not (isinstance(node.value, ast.Name) and node.value.id == "sys")
    assert "sys.path" not in source
    assert "/Users/" not in source


# ---- realtime transcription (fake websocket) --------------------------------------------------


class FakeSocket:
    def __init__(self, incoming: list[dict[str, Any]]) -> None:
        self.incoming = [json.dumps(m) for m in incoming]
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        if not self.incoming:
            await asyncio.sleep(10)
        return self.incoming.pop(0)


class FakeConnect:
    def __init__(self, socket: FakeSocket) -> None:
        self.socket = socket
        self.url = ""
        self.headers: dict[str, str] = {}

    def __call__(self, url: str, **kwargs: Any) -> FakeConnect:
        self.url = url
        self.headers = kwargs.get("additional_headers", {})
        return self

    async def __aenter__(self) -> FakeSocket:
        return self.socket

    async def __aexit__(self, *exc: object) -> None:
        return None


def test_realtime_transcription_commits_on_the_last_chunk_and_reads_the_transcript() -> None:
    socket = FakeSocket(
        [
            {"message_type": "session_started"},
            {"message_type": "partial_transcript", "text": "ho"},
            {"message_type": "committed_transcript", "text": "hola", "language_code": "es"},
            {"message_type": "committed_transcript_with_timestamps", "text": "hola"},
        ]
    )
    connect = FakeConnect(socket)
    pcm = va.samples_to_pcm(tone(0.35))
    result = asyncio.run(
        va.transcribe_realtime(pcm, api_key=SECRET_WORD, connect=connect, pace_s=0.0, drain_s=0.05)
    )
    assert result["transcript"] == "hola"
    assert result["language_code"] == "es"
    chunk_bytes = va.SR * 2 // 10
    assert len(socket.sent) == math.ceil(len(pcm) / chunk_bytes)
    assert [m["commit"] for m in socket.sent] == [False] * (len(socket.sent) - 1) + [True]
    assert all(m["sample_rate"] == va.SR for m in socket.sent)
    assert all(m["message_type"] == "input_audio_chunk" for m in socket.sent)
    assert b"".join(base64.b64decode(m["audio_base_64"]) for m in socket.sent) == pcm
    assert connect.url == va.scribe_url()
    assert connect.headers == {"xi-api-key": SECRET_WORD}
    assert SECRET_WORD not in connect.url


def test_realtime_transcription_reports_a_timeout_with_no_text() -> None:
    socket = FakeSocket([{"message_type": "session_started"}])
    result = asyncio.run(
        va.transcribe_realtime(
            va.samples_to_pcm(tone(0.2)),
            api_key=SECRET_WORD,
            connect=FakeConnect(socket),
            pace_s=0.0,
            first_wait_s=0.05,
        )
    )
    assert result["transcript"] == ""
    assert result["timeout"] is True


def test_realtime_transcription_stops_on_a_vendor_error_event() -> None:
    socket = FakeSocket([{"message_type": "quota_exceeded", "error": "no credits"}])
    result = asyncio.run(
        va.transcribe_realtime(
            va.samples_to_pcm(tone(0.2)),
            api_key=SECRET_WORD,
            connect=FakeConnect(socket),
            pace_s=0.0,
            first_wait_s=0.5,
        )
    )
    assert result["transcript"] == ""
    assert result["events"][0]["message_type"] == "quota_exceeded"


# ---- the projection and the scratch directory ------------------------------------------------


def test_projection_counts_realtime_seconds_and_characters() -> None:
    tiny = [
        {
            "id": "a",
            "country": "ES",
            "voice": "v",
            "turns": [{"text": "x" * 26, "checks": []}, {"text": "y" * 13, "checks": []}],
        }
    ]
    est = va.estimate_run(tiny)
    assert est.conversations == 3  # one call, three conditions
    assert est.tts_characters == 26 + 13 + va.CORRECTION_ALLOWANCE_CHARS * 3
    pad = va.PAD_LEAD_S + va.PAD_TAIL_S
    one_pass = (26 / va.CHARS_PER_SECOND + pad) + (13 / va.CHARS_PER_SECOND + pad)
    assert est.realtime_seconds == pytest.approx(
        3 * (one_pass + va.CORRECTION_ALLOWANCE_S), rel=1e-6
    )
    assert est.noise_seconds == sum(seconds for _, seconds in va.NOISE.values())


def test_projection_of_the_spike_calls_matches_the_spikes_scale() -> None:
    est = va.estimate_run(calls())
    assert est.conversations == 60
    assert 15 * 60 < est.realtime_seconds < 45 * 60  # "about 20 minutes" of realtime
    assert est.tts_characters < 10_000 + 60 * va.CORRECTION_ALLOWANCE_CHARS


def test_scratch_dir_is_removed_on_exit_and_on_error(tmp_path: Path) -> None:
    with va.scratch_dir(parent=tmp_path) as made:
        (made / "clip.pcm").write_bytes(b"\x00\x00")
        assert made.is_dir()
    assert not made.exists()
    with pytest.raises(RuntimeError), va.scratch_dir(parent=tmp_path) as erred:
        (erred / "clip.pcm").write_bytes(b"\x00\x00")
        raise RuntimeError("boom")
    assert not erred.exists()
    with pytest.raises(KeyboardInterrupt), va.scratch_dir(parent=tmp_path) as interrupted:
        raise KeyboardInterrupt
    assert not interrupted.exists()
    assert list(tmp_path.iterdir()) == []


def test_scratch_dir_refuses_the_repository() -> None:
    with pytest.raises(va.AudioError), va.scratch_dir(parent=va.REPO_ROOT):
        pass
    with pytest.raises(va.AudioError), va.scratch_dir(parent=va.REPO_ROOT / "docs"):
        pass


def test_a_dropped_connection_is_an_audio_error_without_the_key() -> None:
    def refuse(url: str, **kwargs: Any) -> Any:
        raise ConnectionResetError(f"reset while sending {SECRET_WORD}")

    with pytest.raises(va.AudioError) as raised:
        asyncio.run(
            va.transcribe_realtime(
                va.samples_to_pcm(tone(0.2)), api_key=SECRET_WORD, connect=refuse
            )
        )
    assert "ConnectionResetError" in str(raised.value)
    assert SECRET_WORD not in str(raised.value)

    # Anything that is not a transport failure is not swallowed.
    def explode(url: str, **kwargs: Any) -> Any:
        raise ValueError("a bug")

    with pytest.raises(ValueError):
        asyncio.run(
            va.transcribe_realtime(
                va.samples_to_pcm(tone(0.2)), api_key=SECRET_WORD, connect=explode
            )
        )
