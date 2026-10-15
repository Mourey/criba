"""Channel test fixtures: a migrated, seeded SQLite file, a fake clock, a fake Hermes
API server (``httpx.MockTransport``), and the web chat router mounted on a bare app.

No real model is called. W2-A's ``pending_choice`` and interview offer are fakes here;
the integrator wires the real ones.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from criba.channel import service as spoken_service
from criba.channel.config import ChannelSettings
from criba.channel.copy import PRIVACY_NOTICE, PRIVACY_URL
from criba.channel.run_client import RunClient
from criba.channel.service import Channel
from criba.channel.webchat import chat_router
from criba.db import connect, migrate, repo
from criba.db.connection import iso
from criba.db.seed import seed
from criba.screening.clock import FakeClock

# 12:00 in Madrid, 04:00 in Mexico City: daytime in Spain.
NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
PROFILE = "criba-recruiter"
API_KEY = "test-profile-key"
BASE_URL = "http://hermes:8642"


def first_message(text: str, language: str = "es-ES") -> str:
    """``text`` as the Candidate reads it when it is the conversation's first agent message: the
    router adds the privacy notice link to a first message that lacks it (ADR 0068)."""
    return f"{text}\n{PRIVACY_NOTICE[language].format(url=PRIVACY_URL)}"


class FakeHermes:
    """The Runs API surface the run client uses (spike 1): 202 on POST, poll by id,
    idempotency replay on the same key, per-profile key check."""

    def __init__(self, profile: str = PROFILE, key: str = API_KEY) -> None:
        self.profile = profile
        self.key = key
        self.posts: list[dict[str, Any]] = []
        self.polls = 0
        self.runs: dict[str, dict[str, Any]] = {}
        self.by_key: dict[str, str] = {}
        self.reply: Callable[[str], str] = lambda text: f"Recibido (IA): {text}"
        self.final_status = "completed"
        # The error text a failed run reports; Hermes's is "HTTP <status>: <message>" for an
        # upstream refusal (`agent/api_error_summary.py:71-73,196`).
        self.run_error = "model error"
        self.running_polls = 1  # polls answered "running" before the final status
        self.down = False
        self.crash: Exception | None = None  # raised as is: not an httpx error
        self.accepted_run_id: Any = None  # replaces the run_id in the POST answer
        self.usage: Any = {
            "input_tokens": 120,
            "output_tokens": 40,
            "total_tokens": 160,
            "cache_read_tokens": 7000,
            "cache_write_tokens": 10,
        }
        self.runtime = {"provider": "openrouter", "model": "anthropic/claude-haiku-5.5"}
        self._ids = itertools.count(1)
        # The run events stream (spike 3 finding 1; spike 5 row 6). Each events request takes
        # the next script; with none left it answers the run's deltas then `run.completed`.
        self.event_scripts: list[list[str | Exception]] = []
        self.event_requests: list[dict[str, Any]] = []
        self.delta_chunks = 3
        # `POST /v1/runs` answered with this status instead of 202 (a 429 refusal, say).
        self.refuse_status: int | None = None
        # `POST /v1/runs/{id}/stop` as Hermes v0.21.6 answers it
        # (`gateway/platforms/api_server_runs.py:1253-1274`): a finished run's status as it
        # stands, else `{"status": "stopping"}`, after which the run ends `cancelled` with no
        # usage (`:973-974`). `stop_error` is raised instead; `stop_status_code` replaces it.
        self.stops: list[dict[str, Any]] = []
        self.stop_error: Exception | None = None
        self.stop_status_code: int | None = None
        # The stop answers `stopping`, but the agent finishes the turn before it notices the
        # interrupt, so the run ends `completed` (`api_server_runs.py:185-191`, `:966-986`).
        self.stop_loses_race = False

    def _final_info(self, run_id: str, run: dict[str, Any]) -> dict[str, Any]:
        if run.get("stopped"):
            return {
                "run_id": run_id,
                "status": "cancelled",
                "completed": False,
                "partial": False,
                "interrupted": True,
            }
        info: dict[str, Any] = {
            "run_id": run_id,
            "status": self.final_status,
            "session_id": run["session_id"],
            "usage": self.usage,
            "runtime": self.runtime,
        }
        if self.final_status == "completed":
            info["output"] = self.reply(run["input"])
        else:
            info["error"] = self.run_error
        return info

    def _stop(self, request: httpx.Request, run_id: str) -> httpx.Response:
        self.stops.append({"run_id": run_id, "timeout": request.extensions.get("timeout")})
        if self.stop_error is not None:
            raise self.stop_error
        if self.stop_status_code is not None:
            return httpx.Response(self.stop_status_code, json={"error": "stop refused"})
        run = self.runs.get(run_id)
        if run is None:
            return httpx.Response(404, json={"error": "run_not_found"})
        if run.get("stopped") or run.get("polls", 0) > self.running_polls:
            return httpx.Response(200, json=self._final_info(run_id, run))
        run["stopped"] = not self.stop_loses_race
        return httpx.Response(200, json={"run_id": run_id, "status": "stopping"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if self.crash is not None:
            raise self.crash
        parts = request.url.path.strip("/").split("/")
        if len(parts) < 4 or parts[0] != "p" or parts[2:4] != ["v1", "runs"]:
            return httpx.Response(404, json={"error": "not_found"})
        if parts[1] != self.profile:
            return httpx.Response(404, json={"error": "unknown_profile"})
        if request.headers.get("Authorization") != f"Bearer {self.key}":
            return httpx.Response(401, json={"error": "unauthorized"})
        if request.method == "POST" and len(parts) == 4:
            body = json.loads(request.content)
            key = request.headers.get("Idempotency-Key", "")
            self.posts.append({"body": body, "idempotency_key": key})
            if self.refuse_status is not None:
                return httpx.Response(self.refuse_status, json={"error": "refused"})
            if key in self.by_key:
                run_id = self.by_key[key]
                return httpx.Response(
                    202, json={"run_id": run_id}, headers={"Idempotency-Replayed": "true"}
                )
            run_id = f"run_{next(self._ids)}"
            self.by_key[key] = run_id
            self.runs[run_id] = {"input": body["input"], "session_id": body["session_id"]}
            if self.accepted_run_id is not None:
                return httpx.Response(202, json={"run_id": self.accepted_run_id})
            return httpx.Response(202, json={"run_id": run_id, "status": "queued"})
        if request.method == "GET" and len(parts) == 5:
            self.polls += 1
            run = self.runs.get(parts[4])
            if run is None:
                return httpx.Response(404, json={"error": "not_found"})
            run["polls"] = run.get("polls", 0) + 1
            if run["polls"] <= self.running_polls and not run.get("stopped"):
                return httpx.Response(200, json={"run_id": parts[4], "status": "running"})
            return httpx.Response(200, json=self._final_info(parts[4], run))
        if request.method == "POST" and len(parts) == 6 and parts[5] == "stop":
            return self._stop(request, parts[4])
        if request.method == "GET" and len(parts) == 6 and parts[5] == "events":
            return self._events(request, parts[4])
        return httpx.Response(405)

    def _events(self, request: httpx.Request, run_id: str) -> httpx.Response:
        self.event_requests.append(
            {
                "last_event_id": request.headers.get("Last-Event-ID"),
                "accept": request.headers.get("Accept"),
                "timeout": request.extensions.get("timeout"),
            }
        )
        run = self.runs.get(run_id)
        if run is None:
            return httpx.Response(404, json={"error": "not_found"})
        script = self.event_scripts.pop(0) if self.event_scripts else self.completed_events(run_id)
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=_Script(script)
        )

    def completed_events(self, run_id: str) -> list[str | Exception]:
        """What Hermes sends for a completed run: `: open`, the reply's deltas, then
        `run.completed` with output, usage and runtime, then `: stream closed`."""
        text = self.reply(self.runs[run_id]["input"])
        size = max(1, -(-len(text) // self.delta_chunks))
        chunks = [text[i : i + size] for i in range(0, len(text), size)]
        script: list[str | Exception] = [": open\n\n"]
        for seq, chunk in enumerate(chunks):
            script.append(sse(seq, {"event": "message.delta", "run_id": run_id, "delta": chunk}))
        script.append(
            sse(
                len(chunks),
                {
                    "event": "run.completed",
                    "run_id": run_id,
                    "completed": True,
                    "output": text,
                    "usage": self.usage,
                    "runtime": self.runtime,
                },
            )
        )
        script.append(": stream closed\n\n")
        return script

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(self.handler))

    @property
    def inputs(self) -> list[str]:
        return [p["body"]["input"] for p in self.posts]


def sse(seq: int | None, data: dict[str, Any]) -> str:
    """One event frame as Hermes writes it: `id:` (the run's sequence) and `data:` JSON."""
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}data: {json.dumps({**data, 'seq': seq} if seq is not None else data)}\n\n"


class _Script(httpx.SyncByteStream):
    """A scripted events body: strings are sent as they are, an exception is raised where it
    stands (a dropped connection or a read timeout)."""

    def __init__(self, items: list[str | Exception]) -> None:
        self._items = items

    def __iter__(self) -> Iterator[bytes]:
        for item in self._items:
            if isinstance(item, Exception):
                raise item
            yield item.encode()


def drop(message: str = "connection reset") -> httpx.ReadError:
    return httpx.ReadError(message)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "criba.db"
    conn = connect(path)
    migrate(conn)
    seed(conn, slots_from=date(2026, 10, 12), days=7, now=NOW)
    conn.close()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator:
    connection = connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def hermes() -> FakeHermes:
    return FakeHermes()


def make_run_client(hermes: FakeHermes, **kwargs: Any) -> RunClient:
    options: dict[str, Any] = {"timeout_s": 5.0, "poll_interval_s": 0.0}
    options.update(kwargs)
    return RunClient(
        base_url=BASE_URL, profile=PROFILE, api_key=API_KEY, http=hermes.client(), **options
    )


class Fakes:
    """W2-A stand-ins, swappable per test."""

    def __init__(self) -> None:
        self.choice: dict[str, Any] | None = None
        self.offer: dict[str, Any] | None = None
        self.choice_calls: list[str] = []
        self.choice_nows: list[datetime] = []

    def pending_choice(
        self, conn: Any, application_id: str, *, now: datetime
    ) -> dict[str, Any] | None:
        self.choice_calls.append(application_id)
        self.choice_nows.append(now)
        return self.choice

    def interview_offer(self, conn: Any, application_id: str, now: datetime) -> dict[str, Any]:
        if self.offer is None:
            raise RuntimeError("no offer configured")
        return self.offer


@pytest.fixture
def fakes() -> Fakes:
    return Fakes()


@pytest.fixture
def channel(db_path: Path, clock: FakeClock, hermes: FakeHermes, fakes: Fakes) -> Channel:
    return Channel(
        connect=lambda: connect(db_path),
        run_client=make_run_client(hermes),
        clock=clock,
        settings=ChannelSettings(db_path=str(db_path), environment="test"),
        pending_choice=fakes.pending_choice,
        interview_offer=fakes.interview_offer,
    )


@pytest.fixture
def client(channel: Channel) -> TestClient:
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/chat")
    app.state.criba_channel = channel
    return TestClient(app)


def start(client: TestClient, **body: Any) -> str:
    payload = {"country": "ES", "locale": "es-ES", **body}
    resp = client.post("/api/chat/conversations", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["conversation_id"]


def candidate_for(conn: Any, conversation_id: str, channel: str = "webchat") -> dict[str, Any]:
    found = repo.find_candidate_by_identity(conn, channel, conversation_id)
    assert found is not None
    return found


# --------------------------------------------------------------------------- spoken turns
# Added by spoken-turn below spoken-stream's events surface; spoken-checks reuses them.

WAIT_S = 5.0  # the longest any spoken-turn test waits on a worker


class RecordingSpokenChecks:
    """A ``SpokenChecks`` fake: records each call in ``calls`` (in order, shared with the
    ``GatedRunClient`` when given the same list) and returns ``decision``. ``gate``, when set,
    holds ``before_accept`` until it is set (at most ``WAIT_S``); ``after_error`` is raised by
    ``after_reply``; ``on_after`` is called with the ``after_reply`` arguments."""

    def __init__(self, calls: list[str] | None = None) -> None:
        self.calls: list[str] = [] if calls is None else calls
        self.decision = spoken_service.SpokenDecision("plain")
        self.gate: threading.Event | None = None
        self.entered = threading.Event()
        self.after: list[dict[str, Any]] = []
        self.after_error: Exception | None = None
        self.on_after: Callable[[dict[str, Any]], None] | None = None

    def before_accept(
        self, conn: Any, candidate: dict[str, Any], *, text: str, now: datetime
    ) -> spoken_service.SpokenDecision:
        self.calls.append("before_accept")
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(WAIT_S)
        return self.decision

    def after_reply(
        self,
        conn: Any,
        app: dict[str, Any],
        *,
        message_pk: int,
        reply_pk: int,
        text: str,
        now: datetime,
    ) -> None:
        self.calls.append("after_reply")
        call = {"application_id": app["id"], "message_pk": message_pk, "reply_pk": reply_pk}
        self.after.append(call)
        if self.on_after is not None:
            self.on_after(call)
        if self.after_error is not None:
            raise self.after_error


class GatedRunClient:
    """Wraps a run client: ``stream`` yields the inner stream's deltas and, before each one,
    calls ``before_delta(index)`` and, when ``hold`` is set, waits on it (at most ``WAIT_S``).
    A test writes ``control='human'`` from ``before_delta`` to place a Takeover between
    deltas, or holds the run on ``hold`` to act mid-stream."""

    def __init__(self, inner: Any, calls: list[str] | None = None) -> None:
        self.inner = inner
        self.profile = inner.profile
        self.calls = calls
        self.hold: threading.Event | None = None
        self.holding = threading.Event()  # set while a delta waits on `hold`
        self.before_delta: Callable[[int], None] = lambda index: None
        self.streams = 0

    def run(self, *, session_id: str, input: str, idempotency_key: str) -> Any:
        return self.inner.run(session_id=session_id, input=input, idempotency_key=idempotency_key)

    def stream(self, *, session_id: str, input: str, idempotency_key: str) -> Iterator[str]:
        self.streams += 1
        if self.calls is not None:
            self.calls.append("stream")
        inner = self.inner.stream(
            session_id=session_id, input=input, idempotency_key=idempotency_key
        )
        index = 0
        while True:
            try:
                delta = next(inner)
            except StopIteration as stop:
                return stop.value
            self.before_delta(index)
            if self.hold is not None:
                self.holding.set()
                self.hold.wait(WAIT_S)
            yield delta
            index += 1


@pytest.fixture
def spoken_calls() -> list[str]:
    return []


@pytest.fixture
def spoken_checks(spoken_calls: list[str]) -> RecordingSpokenChecks:
    return RecordingSpokenChecks(spoken_calls)


@pytest.fixture
def gated(hermes: FakeHermes, spoken_calls: list[str]) -> GatedRunClient:
    # The fake agent answers with the last line of its input: the utterance.
    hermes.reply = lambda text: f"Vale: {text.rsplit(chr(10), 1)[-1]}"
    return GatedRunClient(make_run_client(hermes), spoken_calls)


@pytest.fixture
def spoken_channel(
    db_path: Path,
    clock: FakeClock,
    fakes: Fakes,
    gated: GatedRunClient,
    spoken_checks: RecordingSpokenChecks,
) -> Channel:
    """The ``channel`` fixture's arguments, with the gated run client and the recording hook."""
    return Channel(
        connect=lambda: connect(db_path),
        run_client=gated,
        clock=clock,
        settings=ChannelSettings(db_path=str(db_path), environment="test"),
        pending_choice=fakes.pending_choice,
        interview_offer=fakes.interview_offer,
        spoken_checks=spoken_checks,
    )


def voice_session(
    conn: Any, conversation_id: str, *, country: str = "ES", ended: bool = False
) -> tuple[str, dict[str, Any]]:
    """Plant a voice conversation (its Candidate, if new) and a ``voice_sessions`` row for
    it. Returns ``(voice_session_id, candidate)``. Voice is its own channel (ADR 0059)."""
    candidate, _ = repo.get_or_create_candidate(
        conn,
        client_id="grupo_sazon",
        channel="voice",
        external_id=conversation_id,
        country=country,
    )
    voice_session_id = f"vs_{next(_SESSION_IDS):032x}"
    conn.execute(
        "INSERT INTO voice_sessions (id, conversation_id, candidate_id, country, started_at,"
        " ended_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            voice_session_id,
            conversation_id,
            candidate["id"],
            country,
            iso(NOW),
            iso(NOW) if ended else None,
        ),
    )
    return voice_session_id, candidate


_SESSION_IDS = itertools.count(1)


def drain(events: Any, timeout_s: float = WAIT_S) -> list[Any]:
    """Every event of a ``DeltaQueue`` up to and including the terminal one; fails if the
    terminal event does not come within ``timeout_s``."""
    out: list[Any] = []
    deadline = time.monotonic() + timeout_s
    while True:
        event = events.get(max(0.0, deadline - time.monotonic()))
        assert event is not None, f"no terminal event within {timeout_s}s: {out}"
        out.append(event)
        if event.kind != "text":
            return out


# --------------------------------------------------------------------------- owed turns (ADR 0073)


def run_retries(channel: Channel, clock: FakeClock, *, rounds: int = 6) -> int:
    """Drive the turn recovery in the foreground: move the clock past every owed turn's next
    attempt and resume what is due, until nothing is (a retry succeeded, or the attempts are
    spent). Returns how many rounds started something."""
    started = 0
    for _ in range(rounds):
        clock.advance(seconds=150)  # past the longest wait of OWED_BACKOFF
        if not channel.resume_owed_turns(background=False):
            break
        started += 1
    return started


def owed_rows(conn: Any, application_id: str) -> list[dict[str, Any]]:
    return repo.owed_turns_of(conn, application_id)
