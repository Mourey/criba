"""The speech recognition run and its owner commands (voice-agent acceptance-live VA8-VA11).

ElevenLabs is a fake (text-to-speech and sound generation over an `httpx.MockTransport`, Scribe
over a scripted socket) and the stack is `FakeVoiceApi`, so nothing here reaches a network and no
audio is ever heard. R26 itself is never run here: no Scribe, no text-to-speech, no live call.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from live import voice_acceptance as va
from live import voice_audio as audio
from live import voice_report as vr
from live import voice_smoke as vs
from live.fake_api import FakeVoiceApi, make_application
from live.test_voice_smoke import LLM_SECRET, MEXICO, SEED, T0, StepClock

SECRET_WORD = "-".join(("elevenlabs", "key", "for", "tests"))
STAMP = vr.RunStamp("2026-10-11 20:00", "abc1234", False)


# ---- fakes for ElevenLabs ---------------------------------------------------------------------


def tone_pcm(seconds: float = 0.5) -> bytes:
    count = int(seconds * audio.SR)
    return audio.samples_to_pcm(
        [0.2 * math.sin(2 * math.pi * 220 * i / audio.SR) for i in range(count)]
    )


class FakeEleven:
    """Text-to-speech and sound generation over a mock transport, Scribe over a queue."""

    def __init__(self, transcripts: list[str] | None = None) -> None:
        self.transcripts = list(transcripts or [])
        self.requests: list[httpx.Request] = []
        self.tts_texts: list[str] = []
        self.sent_audio: list[bytes] = []
        self.connections = 0
        self.error_event: str | None = None
        self.before_transcribe: Callable[[], None] = lambda: None
        self.http = httpx.Client(transport=httpx.MockTransport(self._handler))

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.startswith("/v1/text-to-speech/"):
            self.tts_texts.append(json.loads(request.content)["text"])
            return httpx.Response(200, content=tone_pcm(0.5))
        if request.url.path == "/v1/sound-generation":
            seconds = int(json.loads(request.content)["duration_seconds"])
            return httpx.Response(200, content=tone_pcm(min(seconds, 2)))
        return httpx.Response(404)

    def connect(self, url: str, **kwargs: Any) -> Any:
        fake = self

        class Socket:
            def __init__(self) -> None:
                self.sent: list[dict[str, Any]] = []
                self.queue: list[str] = []
                self.answered = False

            async def send(self, message: str) -> None:
                self.sent.append(json.loads(message))

            async def recv(self) -> str:
                if not self.queue and self.answered:
                    await asyncio.sleep(10)  # nothing more to say: the reader times out
                if not self.queue:
                    self.answered = True
                    fake.before_transcribe()
                    text = fake.transcripts.pop(0) if fake.transcripts else ""
                    if fake.error_event:
                        self.queue = [json.dumps({"message_type": fake.error_event})]
                        return self.queue.pop(0)
                    self.queue = [
                        json.dumps({"message_type": "committed_transcript", "text": text}),
                        json.dumps({"message_type": "committed_transcript_with_timestamps"}),
                    ]
                    fake.sent_audio.append(
                        b"".join(base64.b64decode(m["audio_base_64"]) for m in self.sent)
                    )
                return self.queue.pop(0)

        class Connection:
            async def __aenter__(self) -> Socket:
                fake.connections += 1
                return Socket()

            async def __aexit__(self, *exc: object) -> None:
                return None

        return Connection()


# ---- the harness ------------------------------------------------------------------------------

CALLS: list[dict[str, Any]] = [
    {
        "id": f"t{n}",
        "country": "ES",
        "voice": "es_c01",
        "turns": [
            {
                "text": "Me llamo Lucía Gil.",
                "checks": [{"kind": "name", "field": "full_name", "expect": "Lucía Gil"}],
            }
        ],
    }
    for n in range(1, 5)
]


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        cap: str | None = "3",
        now: datetime = T0,
    ) -> None:
        self.api = FakeVoiceApi(password=SEED, llm_secret=LLM_SECRET)
        self.eleven = FakeEleven()
        self.tmp = tmp_path
        self.scratch = tmp_path / "scratch"
        self.scratch.mkdir()
        self.llm_file = tmp_path / "llm_secret.txt"
        self.llm_file.write_text(LLM_SECRET + "\n", encoding="utf-8")
        self.password_file = tmp_path / "seed_password.txt"
        self.password_file.write_text(SEED + "\n", encoding="utf-8")
        for name in (
            "CRIBA_REAL_SPEND_CAP_USD",
            "CRIBA_REAL_SPEND_LEDGER",
            "CRIBA_REAL_PRICES_JSON",
        ):
            monkeypatch.delenv(name, raising=False)
        self.env: dict[str, str] = {"CRIBA_REAL_SPEND_LEDGER": str(tmp_path / "ledger.json")}
        if cap is not None:
            self.env["CRIBA_REAL_SPEND_CAP_USD"] = cap
        self.now = now
        self.agents_exit = 0
        self.calls = [json.loads(json.dumps(c)) for c in CALLS]

    def phone(self, call: dict[str, Any], condition: str) -> str:
        index = int(re.sub(r"\D", "", call["id"]))
        return f"+3460009{index:04d}"

    def app(self, n: int, *, full_name: str | None) -> None:
        phone = self.phone({"id": f"t{n}"}, "clean")
        answers = {"full_name": full_name} if full_name is not None else {}
        self.api.applications[f"app_{n}"] = make_application(
            f"app_{n}", phone, stage="zone", answers=answers
        )

    def deps(self) -> va.AcceptanceDeps:
        api = self.api
        base = vs.Deps(
            env=self.env,
            now=lambda: self.now,
            turn_clock=StepClock(),
            check_agents=lambda: vs.AgentsCheck(exit_code=self.agents_exit, at=T0),
            runs_reader=None,
            sync_client=lambda base_url: TestClient(api.app, base_url=base_url),
            async_client=lambda base_url: httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api.app), base_url=base_url
            ),
            phone_for=lambda persona: persona.phone,
            load_personas=lambda: [],
            git_state=lambda: ("abc1234", False),
        )
        return va.AcceptanceDeps(
            base=base,
            elevenlabs=lambda: self.eleven.http,
            connect=self.eleven.connect,
            load_calls=lambda: self.calls,
            load_voices=lambda: {"es_c01": "voice-es-1", "mx_c01": "voice-mx-1"},
            read_key=lambda: SECRET_WORD,
            scratch_parent=self.scratch,
            phone_for_call=self.phone,
            baseline=audio_baseline,
            pace_s=0.0,
            drain_s=0.05,
            first_wait_s=2.0,
        )

    def argv(self, *extra: str, cap_seconds: str | None = "100000") -> list[str]:
        argv = [
            "--base-url",
            "http://fake",
            "--out",
            str(self.report),
            "--llm-secret-file",
            str(self.llm_file),
            "--seed-password-file",
            str(self.password_file),
            "--conditions",
            "clean",
        ]
        if cap_seconds is not None:
            argv += ["--elevenlabs-cap-seconds", cap_seconds]
        return [*argv, *extra]

    @property
    def report(self) -> Path:
        return self.tmp / "voice-acceptance.md"

    def run(self, *extra: str, cap_seconds: str | None = "100000") -> int:
        return va.main(self.argv(*extra, cap_seconds=cap_seconds), self.deps())

    def play(self, *extra: str) -> va.AcceptanceResult:
        args = va.build_parser().parse_args(self.argv(*extra))
        return asyncio.run(va.acceptance(args, self.deps()))

    def untouched(self) -> bool:
        return (
            self.eleven.requests == []
            and self.eleven.connections == 0
            and self.api.completion_requests == 0
            and self.api.sessions == []
        )


def audio_baseline() -> dict[str, Any]:
    return audio.load_baseline()


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(tmp_path, monkeypatch)


# ---- VA8: opt in, and the ElevenLabs cap ------------------------------------------------------


def test_refuses_without_the_cap(harness: Harness, capsys: pytest.CaptureFixture[str]) -> None:
    assert harness.run(cap_seconds=None) == 2
    assert harness.untouched()
    err = capsys.readouterr().err
    assert "--elevenlabs-cap-seconds" in err
    assert "realtime seconds" in err  # the projection is printed
    assert "characters" in err
    assert not harness.report.exists()


def test_refuses_above_the_cap(harness: Harness, capsys: pytest.CaptureFixture[str]) -> None:
    assert harness.run(cap_seconds="5") == 2
    assert harness.untouched()
    err = capsys.readouterr().err
    assert "above" in err
    assert "realtime seconds" in err
    assert not harness.report.exists()


def test_a_cap_that_is_not_a_positive_number_is_refused(harness: Harness) -> None:
    assert harness.run(cap_seconds="0") == 2
    assert harness.run(cap_seconds="-5") == 2
    assert harness.untouched()


def test_the_spike_calls_project_against_the_cap(harness: Harness) -> None:
    harness.calls = audio.load_calls()
    projection = va.project(harness.calls, ["clean", "moderate", "loud"])
    assert projection.conversations == 60
    assert (
        harness.run(
            "--conditions",
            "clean,moderate,loud",
            cap_seconds=str(int(projection.realtime_seconds) - 1),
        )
        == 2
    )
    assert harness.untouched()


def refuse_no_cap(h: Harness) -> None:
    h.env.pop("CRIBA_REAL_SPEND_CAP_USD")


def refuse_sim(h: Harness) -> None:
    h.api.sim = True


def refuse_window(h: Harness) -> None:
    h.now = datetime(2026, 10, 11, 22, 0, tzinfo=MEXICO)


def refuse_check(h: Harness) -> None:
    h.agents_exit = 1


@pytest.mark.parametrize("refusal", [refuse_no_cap, refuse_sim, refuse_window, refuse_check])
def test_the_same_preflight_as_the_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: Callable[[Harness], None]
) -> None:
    h = Harness(tmp_path, monkeypatch)
    refusal(h)
    assert h.run() == 2
    assert h.untouched()


# ---- VA9: captured, caught or lost, on the record ---------------------------------------------


def test_classes(harness: Harness) -> None:
    # t1 captured; t2 misheard, read back, corrected; t3 misheard, read back, correction heard
    # wrong; t4 misheard and never read back.
    harness.eleven.transcripts = [
        "Me llamo Lucía Gil.",
        "Me llamo Lucha Gil.",
        "No, es Lucía Gil.",
        "Me llamo Lucha Gil.",
        "No, es Lucha Gil.",
        "Me llamo.",
    ]
    harness.api.replies.extend(
        [
            "Gracias, Lucía.",
            "¿Lucha Gil, correcto?",
            "Perfecto, Lucía Gil.",
            "¿Lucha Gil, correcto?",
            "Gracias.",
            "Gracias.",
        ]
    )
    harness.app(1, full_name="Lucía Gil")
    harness.app(2, full_name="Lucía Gil")
    harness.app(3, full_name="Lucha Gil")
    harness.app(4, full_name=None)
    outcome = harness.play()
    assert outcome.exit_code == 0
    by_call = {o.call: o.klass for o in outcome.checks}
    assert by_call == {"t1": "captured", "t2": "caught", "t3": "lost", "t4": "lost"}
    # Text-to-speech: the four turns, and the correction for t2 and t3, said cleanly with the
    # true value.
    assert harness.eleven.tts_texts.count("Me llamo Lucía Gil.") == 4
    assert harness.eleven.tts_texts.count("No, es Lucía Gil") == 2
    assert harness.api.completion_requests == 6
    assert harness.eleven.connections == 6


def test_a_value_the_record_holds_but_the_script_did_not_correct_is_lost() -> None:
    check = {"kind": "name", "field": "full_name", "expect": "Lucía Gil"}
    assert va.classify(check, transcript_ok=False, recorded_ok=True, corrected=False) == "lost"
    assert va.classify(check, transcript_ok=True, recorded_ok=False, corrected=False) == "lost"
    assert va.classify(check, transcript_ok=True, recorded_ok=True, corrected=False) == "captured"
    assert va.classify(check, transcript_ok=False, recorded_ok=True, corrected=True) == "caught"
    assert va.classify(check, transcript_ok=False, recorded_ok=False, corrected=True) == "lost"
    assert va.classify(check, transcript_ok=True, recorded_ok=None, corrected=False) == "not_stored"


def test_read_back_is_a_wrong_value_the_reply_echoes() -> None:
    said = "Me llamo Lucía Gil."
    assert va.read_back_wrong(said, "Me llamo Lucha Gil.", "¿Lucha Gil, correcto?")
    assert not va.read_back_wrong(said, "Me llamo Lucha Gil.", "Gracias.")
    assert not va.read_back_wrong(said, "Me llamo.", "Gracias.")
    assert va.read_back_wrong(
        "El código es veintiocho cero diez", "El código es 28011", "¿El 28011, verdad?"
    )
    # Short filler words the transcript added are not a value.
    assert not va.read_back_wrong(said, "Eh me llamo Lucía Gil.", "Eh, vale.")


def test_correction_text_says_the_true_value() -> None:
    assert (
        va.correction_text(
            {"kind": "name", "field": "full_name", "expect": "Lucía Gil"}, "Me llamo Lucía Gil."
        )
        == "No, es Lucía Gil"
    )
    assert (
        va.correction_text(
            {"kind": "digits", "field": "postcode", "expect": "28010"}, "El veintiocho cero diez"
        )
        == "No, es dos ocho cero uno cero"
    )
    assert (
        va.correction_text(
            {"kind": "tokens", "field": "zone", "expect": ["chamberi"]}, "Vivo en Chamberí"
        )
        == "No, es Vivo en Chamberí"
    )


def app_with(**over: Any) -> dict[str, Any]:
    app = make_application("app_x", "+34600000001", stage="zone")
    app["answers"] = over.pop("answers", {})
    app.update(over)
    return app


def test_recorded_answers_are_compared_field_by_field() -> None:
    rec = va.recorded_check
    name = {"kind": "name", "field": "full_name", "expect": "Lucía Fernández Gil"}
    assert rec(name, app_with(answers={"full_name": "Lucia Fernandez Gil"})) is True
    assert rec(name, app_with(answers={"full_name": "Lucia Gil"})) is False
    assert rec(name, app_with(answers={})) is False
    zone = {"kind": "tokens", "field": "zone", "expect": ["chamberi"]}
    ref = {"id": "z1", "name": "Chamberí", "city": "Madrid", "country": "ES"}
    assert rec(zone, app_with(answers={"zone": ref})) is True
    assert rec(zone, app_with(answers={"zone": {**ref, "name": "Vallecas"}})) is False
    lic = {"kind": "yesno", "field": "driver_license", "expect": "yes"}
    assert rec(lic, app_with(answers={"driver_license": True})) is True
    assert rec(lic, app_with(answers={"driver_license": False})) is False
    assert rec({**lic, "expect": "no"}, app_with(answers={"driver_license": False})) is True
    avail = {
        "kind": "choice",
        "group": "availability",
        "field": "availability",
        "expect": ["full_time"],
    }
    assert rec(avail, app_with(answers={"availability": ["full_time"]})) is True
    assert rec(avail, app_with(answers={"availability": ["part_time"]})) is False
    platforms = {
        "kind": "choice",
        "group": "platform",
        "field": "experience_platforms",
        "expect": ["glovo", "uber_eats"],
    }
    assert (
        rec(platforms, app_with(answers={"experience_platforms": ["uber_eats", "glovo"]})) is True
    )
    didi = {**platforms, "expect": ["didi"]}
    assert rec(didi, app_with(answers={"experience_platforms": ["didi_food"]})) is True
    years = {"kind": "number", "field": "experience_years", "expect": 1.5}
    assert rec(years, app_with(answers={"experience_years": 1.5})) is True
    assert rec(years, app_with(answers={"experience_years": 2})) is False
    monday = {"kind": "tokens", "field": "start_date", "expect": ["lunes"]}
    assert rec(monday, app_with(answers={"start_date": "2026-10-12"})) is True  # a Monday
    assert rec(monday, app_with(answers={"start_date": "2026-10-13"})) is False
    novemeber = {"kind": "tokens", "field": "start_date", "expect": ["1", "noviembre"]}
    assert rec(novemeber, app_with(answers={"start_date": "2026-11-01"})) is True
    assert rec(novemeber, app_with(answers={"start_date": "2026-11-02"})) is False


def test_values_not_stored_are_classified_by_stage_or_event() -> None:
    rec = va.recorded_check
    optout = {"kind": "optout", "field": "opt_out", "expect": True}
    opted = app_with()
    opted["candidate"]["opted_out"] = True
    assert rec(optout, opted) is True
    assert rec(optout, app_with()) is False
    withdrawn = {"kind": "withdraw", "field": "withdrawn", "expect": True}
    assert rec(withdrawn, app_with(outcome="withdrawn", status="closed")) is True
    assert rec(withdrawn, app_with()) is False
    confirm = {"kind": "yesno", "field": "confirm", "expect": "yes"}
    assert rec(confirm, app_with(stage="qualified")) is True
    assert rec(confirm, app_with(stage="zone")) is False
    slot = {"kind": "tokens", "field": "slot", "expect": ["jueves", "10"]}
    assert rec(slot, app_with(interview={"id": "i1"})) is True
    assert rec(slot, app_with(interview=None)) is False
    # No counterpart on the record at all: neither captured nor lost.
    for field_name, kind in (
        ("postcode", "digits"),
        ("vehicle", "choice"),
        ("engine_cc", "number"),
        ("vehicle_year", "number"),
        ("volunteered_age", "number"),
        ("has_experience", "yesno"),
        ("objection", "yesno"),
        ("correction_cue", "tokens"),
    ):
        assert rec({"kind": kind, "field": field_name, "expect": "x"}, app_with()) is None


def test_every_check_in_the_spike_calls_has_a_known_recorded_rule() -> None:
    stored = {
        "full_name",
        "zone",
        "driver_license",
        "availability",
        "preferred_schedule",
        "experience_years",
        "experience_platforms",
        "start_date",
        "opt_out",
        "withdrawn",
        "confirm",
        "slot",
    }
    fields = {c["field"] for call in audio.load_calls() for t in call["turns"] for c in t["checks"]}
    assert fields - stored == set(va.NOT_STORED_FIELDS)
    assert fields >= stored


# ---- VA9: the report --------------------------------------------------------------------------


def outcome(call: str, country: str, condition: str, field: str, klass: str) -> va.CheckOutcome:
    return va.CheckOutcome(call, country, condition, 0, field, "name", klass, True, True)


def test_counts_per_country_condition_and_field_beside_the_baseline() -> None:
    outcomes = [
        outcome("es01", "ES", "clean", "full_name", "captured"),
        outcome("es01", "ES", "clean", "zone", "captured"),
        outcome("es02", "ES", "clean", "full_name", "caught"),
        outcome("es03", "ES", "loud", "full_name", "lost"),
        outcome("mx01", "MX", "moderate", "postcode", "not_stored"),
    ]
    lines = va.speech_lines(
        outcomes,
        usage=va.Usage(realtime_seconds=120.0, tts_characters=900, noise_seconds=50),
        baseline=audio.load_baseline(),
        projection=va.project(audio.load_calls(), ["clean", "moderate", "loud"]),
    )
    text = "\n".join(lines)
    baseline = audio.load_baseline()
    for key in ("ES|clean", "ES|moderate", "ES|loud", "MX|clean", "MX|moderate", "MX|loud"):
        rate = baseline[key]["capture_rate"]
        assert f"{rate * 100:.1f}%" in text, key
    # ES clean: 3 checks classified, 2 captured, 1 caught, 0 lost.
    row = next(line for line in lines if line.startswith("| ES | clean |"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[2:6] == ["3", "2", "1", "0"]
    # The per-field table.
    assert any(line.startswith("| ES | clean | full_name |") for line in lines)
    assert any(line.startswith("| MX | moderate | postcode |") for line in lines)
    assert "not stored" in text.lower()
    assert "synthetic" in text.lower()  # concern 13: a ceiling, not a forecast
    assert "120" in text  # realtime seconds used
    assert "900" in text  # characters used


def test_the_run_writes_header_and_speech_and_no_transcript_text(harness: Harness) -> None:
    harness.eleven.transcripts = ["Me llamo Lucía Gil."] * 4
    harness.api.replies.extend(["Gracias."] * 4)
    for n in range(1, 5):
        harness.app(n, full_name="Lucía Gil")
    assert harness.run() == 0
    text = harness.report.read_text(encoding="utf-8")
    assert vr.start_marker("header") in text
    assert vr.start_marker("speech") in text
    assert "Me llamo" not in text  # counts only: no text derived from the audio
    assert SECRET_WORD not in text
    assert LLM_SECRET not in text
    assert "abc1234" in text


def test_a_correction_is_credited_only_to_the_field_it_corrected(harness: Harness) -> None:
    """Two checks fail in one turn; only the one whose wrong value the reply read back gets a
    correction, so only that one can be "caught"."""
    harness.calls = [
        {
            "id": "t1",
            "country": "ES",
            "voice": "es_c01",
            "turns": [
                {
                    "text": "Me llamo Lucía Gil y vivo en Chamberí.",
                    "checks": [
                        {"kind": "name", "field": "full_name", "expect": "Lucía Gil"},
                        {"kind": "tokens", "field": "zone", "expect": ["chamberi"]},
                    ],
                }
            ],
        }
    ]
    harness.eleven.transcripts = ["Me llamo Lucha Gil y vivo en Chambery.", "No, es Lucía Gil."]
    harness.api.replies.extend(["¿Lucha Gil, correcto?", "Perfecto, Lucía Gil."])
    zone = {"id": "z1", "name": "Chamberí", "city": "Madrid", "country": "ES"}
    harness.api.applications["app_1"] = make_application(
        "app_1",
        harness.phone({"id": "t1"}, "clean"),
        stage="zone",
        answers={"full_name": "Lucía Gil", "zone": zone},
    )
    outcome = harness.play()
    assert outcome.exit_code == 0
    assert {o.field: o.klass for o in outcome.checks} == {"full_name": "caught", "zone": "lost"}
    assert harness.eleven.tts_texts.count("No, es Lucía Gil") == 1


def test_a_clean_run_buys_no_noise(harness: Harness) -> None:
    harness.calls = harness.calls[:1]
    harness.eleven.transcripts = ["Me llamo Lucía Gil."]
    harness.api.replies.append("Gracias.")
    harness.app(1, full_name="Lucía Gil")
    assert harness.run() == 0
    assert not [r for r in harness.eleven.requests if r.url.path == "/v1/sound-generation"]


def test_a_scribe_error_event_stops_the_run_and_is_reported(harness: Harness) -> None:
    harness.eleven.error_event = "quota_exceeded"
    harness.app(1, full_name="Lucía Gil")
    assert harness.run() == 1
    text = harness.report.read_text(encoding="utf-8")
    assert "quota_exceeded" in text
    assert "not run" in text.lower()  # the other three conversations never started
    assert harness.eleven.connections == 1
    assert harness.api.completion_requests == 0


# ---- VA10: no Candidate audio kept ------------------------------------------------------------


def test_audio_lives_in_the_scratch_dir_while_the_run_goes(harness: Harness) -> None:
    seen: list[list[str]] = []
    harness.eleven.before_transcribe = lambda: seen.append(
        sorted(p.name for p in harness.scratch.rglob("*") if p.is_file())
    )
    harness.eleven.transcripts = ["Me llamo Lucía Gil."] * 4
    harness.api.replies.extend(["Gracias."] * 4)
    for n in range(1, 5):
        harness.app(n, full_name="Lucía Gil")
    harness.calls = harness.calls[:1]
    assert harness.run() == 0
    assert seen and any(name.endswith(".pcm") for name in seen[0])
    assert list(harness.scratch.iterdir()) == []


def test_temp_dir_removed_after_a_mid_call_error(harness: Harness) -> None:
    harness.app(1, full_name="Lucía Gil")
    harness.calls = harness.calls[:1]

    def boom() -> None:
        assert any(p.is_file() for p in harness.scratch.rglob("*"))  # audio exists right now
        raise RuntimeError("the socket died")

    harness.eleven.before_transcribe = boom
    with pytest.raises(RuntimeError):
        harness.run()
    assert list(harness.scratch.iterdir()) == []


def test_temp_dir_removed_on_ctrl_c(harness: Harness) -> None:
    harness.app(1, full_name="Lucía Gil")
    harness.calls = harness.calls[:1]

    def interrupt() -> None:
        raise KeyboardInterrupt

    harness.eleven.before_transcribe = interrupt
    with pytest.raises(KeyboardInterrupt):
        harness.run()
    assert list(harness.scratch.iterdir()) == []


def test_each_condition_gets_its_own_mix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(tmp_path, monkeypatch)
    h.calls = h.calls[:1]
    h.eleven.transcripts = ["Me llamo Lucía Gil."] * 3
    h.api.replies.extend(["Gracias."] * 3)
    h.app(1, full_name="Lucía Gil")
    assert h.run("--conditions", "clean,moderate,loud") == 0
    clean, moderate, loud = h.eleven.sent_audio
    assert len(clean) == len(moderate) == len(loud)
    assert clean != moderate != loud
    # The speech is synthesised once and mixed three times; only noise differs.
    assert h.eleven.tts_texts == ["Me llamo Lucía Gil."]


def test_the_spend_cap_stops_before_a_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path, monkeypatch, cap="0.01")  # under the first projection
    assert h.run() == 0
    assert h.api.sessions == []
    assert "not run" in h.report.read_text(encoding="utf-8").lower()


# ---- VA11: the template and --verify ----------------------------------------------------------


def fill_in(text: str) -> str:
    out = []
    for line in text.splitlines():
        if line.startswith("|") and not set(line) <= set("|- :"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            line = "| " + " | ".join(c or "observed" for c in cells) + " |"
        out.append(line)
    return "\n".join(out) + "\n"


def test_live_template_and_verify_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "voice-acceptance.md"
    deps = va.default_deps({})
    assert va.main(["--live-template", "--out", str(out)], deps) == 0
    text = out.read_text(encoding="utf-8")
    assert vr.start_marker("live-calls") in text
    assert vr.start_marker("clip") in text
    assert vr.blank_fields(text)

    # --verify: exit 1 and every blank named, while the report is the template.
    assert va.main(["--verify", str(out)], deps) == 1
    err = capsys.readouterr()
    assert "Browser and device" in err.out + err.err

    # A second --live-template must not wipe what the owner has filled in.
    out.write_text(fill_in(text), encoding="utf-8")
    assert va.main(["--live-template", "--out", str(out)], deps) == 2
    assert out.read_text(encoding="utf-8") == fill_in(text)

    # The template alone leaves the header, smoke and speech blocks missing: still not verified.
    assert va.main(["--verify", str(out)], deps) == 1
    # A complete report verifies.
    stamp = vr.RunStamp("2026-10-11 20:00", "abc1234", False)
    complete = vr.update_report(
        out.read_text(encoding="utf-8"),
        {
            "header": vr.section_body("Run facts", stamp, ["h"]),
            "smoke": vr.section_body("Smoke", stamp, ["s"]),
            "speech": vr.section_body("Speech", stamp, ["s"]),
        },
    )
    out.write_text(complete, encoding="utf-8")
    assert va.main(["--verify", str(out)], deps) == 0


def test_verify_and_template_refuse_what_they_cannot_do(tmp_path: Path) -> None:
    deps = va.default_deps({})
    assert va.main(["--verify", str(tmp_path / "missing.md")], deps) == 2
    assert va.main(["--live-template"], deps) == 2  # needs --out
    live_smoke = tmp_path / "live-smoke.md"
    assert va.main(["--live-template", "--out", str(live_smoke)], deps) == 2
    assert not live_smoke.exists()


def test_the_template_command_runs_no_preflight_and_no_vendor_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path, monkeypatch, cap=None)  # no spend cap, no ElevenLabs cap
    out = tmp_path / "voice-acceptance.md"
    assert va.main(["--live-template", "--out", str(out)], h.deps()) == 0
    assert h.untouched()


def test_owner_checklist_is_in_the_docstring() -> None:
    doc = va.__doc__ or ""
    for needle in (
        "python -m live.voice_acceptance",
        "--elevenlabs-cap-seconds",
        "--live-template",
        "--verify",
        "voice_agents.py --check",
        "/voice",
        "iOS Safari",
        "Android Chrome",
        "within a day",
        "exposure",
        "R26",
    ):
        assert needle in doc, needle
