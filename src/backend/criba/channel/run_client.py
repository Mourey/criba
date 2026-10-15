"""Hermes Runs API client (spec 002 task 18, ADR 0018, spike 1).

``POST /p/<profile>/v1/runs`` with ``session_id = application_id`` and
``Idempotency-Key = message_id``, authenticated with the profile's ``API_SERVER_KEY``.
The run is accepted with ``202`` and polled at ``GET /p/<profile>/v1/runs/{run_id}``
until it reaches a final status. A replayed key returns the same ``run_id``, so a
retried message never starts a second run.

Every run, successful or not, becomes one usage row (ADR 0019): see :func:`record_usage`.
A run the client stops waiting for is stopped upstream, best effort (ADR 0077).
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

import httpx

from criba.db import repo
from criba.db.connection import iso

FINAL_OK = "completed"
FINAL_FAILED = frozenset({"failed", "cancelled", "interrupted", "expired"})

# Terminal failure events of the run events stream, as Hermes v0.21.6 names them
# (`gateway/platforms/api_server_runs.py:878-885`, `:944-948`; spike 5 row 6). Any other
# terminal status ends the stream without one of these, and the poll reports it.
FAILURE_EVENTS = frozenset({"run.failed", "run.cancelled", "run.interrupted"})
STREAM_READ_TIMEOUT_S = 15.0
MAX_RECONNECTS = 3
# A POST whose answer was lost (a read timeout, a dropped connection after the request went
# out) may have started the run: it is sent again under the same Idempotency-Key, and Hermes
# answers with the run it took (ADR 0073). A refused connection never reached Hermes.
POST_ATTEMPTS = 2
# How long the poll keeps trying while Hermes cannot be reached at all. A run Hermes still
# reports as running is never cut short by this; only an unreachable Hermes is (ADR 0073).
POLL_UNREACHABLE_S = 5.0
# A run the client stops waiting for is stopped in Hermes too, so it spends no more tokens and
# calls no more tools after the Candidate got the holding message (ADR 0077). Hermes v0.21.6:
# `POST /v1/runs/{run_id}/stop` (`gateway/platforms/api_server_runs.py:240`, handler
# `:1253-1274`) answers a run already over with its status as it stands, usage included, and a
# live one with `stopping`; that run then ends `cancelled` with no usage (`:973-974`, `:987-988`).
STOP_TIMEOUT_S = 2.0
# Leads a ledger row's error when Hermes took the run but reported no usage for it: its zero
# token counts mean "unknown", not "nothing spent" (ADR 0077). The frozen schema has no column
# for it (`contracts/schema.sql`, `usage`).
USAGE_UNKNOWN = "[usage unknown]"

# OpenRouter USD list prices per million tokens, read from its model listing on 2026-10-10.
# Estimates only: the reconciled figure from OpenRouter billing is the one that counts (ADR 0019).
# `cache_write` is the dearer 1-hour rate: the usage rows do not say which cache lifetime was
# used, and an estimate must not fall below the bill. The simulator's spend guard keeps its own
# copy (`sim.spend.GUARD_PRICES_PER_MTOK`; the backend must not import the test package), and a
# test in src/tests/sim/test_spend.py keeps the two equal.
PRICES_PER_MTOK: dict[str, dict[str, float]] = {
    "anthropic/claude-haiku-5.5": {
        "input": 0.10,
        "output": 0.50,
        "cache_read": 0.01,
        "cache_write": 0.20,
    },
    "anthropic/claude-sonnet-5.5": {
        "input": 2.00,
        "output": 10.00,
        "cache_read": 0.10,
        "cache_write": 4.00,
    },
    # Jev through OpenRouter's decisions endpoint (ADR 0051): a bound, not a list price. A call
    # of 400 input and 70 output tokens was billed 0.000017 USD (about 0.04 per million tokens).
    # Its rows carry OpenRouter's own charge, which counts as it stands; this row only prices a
    # failed call, which has none.
    "typesafe/jev-1.13": {"input": 0.10, "output": 0.10, "cache_read": 0.10, "cache_write": 0.10},
}

# Free models: the stub model and the summary's deterministic no-model client (sim mode).
FREE_MODEL_PREFIXES = ("stub", "deterministic")


@dataclass
class RunResult:
    run_id: str
    status: str
    output: str
    usage: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


class RunFailed(Exception):
    """The run errored, timed out, or Hermes could not be reached.

    ``final``: Hermes said the run under this Idempotency-Key is over and failed (a terminal
    status or event), or refused the request (4xx). Its key then replays that failure, so a
    retry needs a new key (ADR 0073). Otherwise the run may still be running, or never
    reached Hermes, and a retry re-POSTs under the same key."""

    def __init__(
        self,
        message: str,
        *,
        kind: Literal["error", "timeout"] = "error",
        run_id: str | None = None,
        latency_ms: int = 0,
        usage: dict[str, Any] | None = None,
        runtime: dict[str, Any] | None = None,
        final: bool = False,
    ) -> None:
        super().__init__(message)
        self.final = final
        self.kind = kind
        self.run_id = run_id
        self.latency_ms = latency_ms
        self.usage = usage or {}
        self.runtime = runtime or {}


class RunClientProtocol(Protocol):
    profile: str

    def run(self, *, session_id: str, input: str, idempotency_key: str) -> RunResult: ...

    def stream(
        self, *, session_id: str, input: str, idempotency_key: str
    ) -> Generator[str, None, RunResult]: ...


class RunClient:
    def __init__(
        self,
        *,
        base_url: str,
        profile: str,
        api_key: str,
        timeout_s: float = 60.0,
        poll_interval_s: float = 0.25,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.profile = profile
        self._prefix = f"/p/{profile}/v1/runs"
        self._timeout_s = timeout_s
        self._poll = poll_interval_s
        self._sleep = sleep
        self._monotonic = monotonic
        # An injected client (tests) must carry its own base_url.
        self._http = http or httpx.Client(base_url=base_url, timeout=10.0)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def run(self, *, session_id: str, input: str, idempotency_key: str) -> RunResult:
        started = self._monotonic()

        def elapsed_ms() -> int:
            return max(0, int((self._monotonic() - started) * 1000))

        run_id = self._start(session_id, input, idempotency_key, elapsed_ms)
        return self._poll_until_final(run_id, started + self._timeout_s, elapsed_ms)

    def stream(
        self, *, session_id: str, input: str, idempotency_key: str
    ) -> Generator[str, None, RunResult]:
        """Start the run as :meth:`run` does, then follow its events: yield each
        ``message.delta`` text and return the run's result from ``run.completed``.

        A dropped read resumes with ``Last-Event-ID`` (never a second POST), at most
        ``MAX_RECONNECTS`` times; frames of another run, frames without a ``run_id`` and frames
        already handled are ignored. A stream that ends without a terminal event falls back to
        the poll. ``timeout_s`` bounds the whole call; each read waits at most the time left,
        capped at ``STREAM_READ_TIMEOUT_S``.
        """
        started = self._monotonic()

        def elapsed_ms() -> int:
            return max(0, int((self._monotonic() - started) * 1000))

        run_id = self._start(session_id, input, idempotency_key, elapsed_ms)
        deadline = started + self._timeout_s

        def timed_out(detail: str) -> RunFailed:
            return self._timed_out(run_id, detail, elapsed_ms)

        last_id: int | None = None
        drops = 0
        while True:
            left = deadline - self._monotonic()
            if left <= 0:
                raise timed_out("stream")
            headers = {**self._headers, "Accept": "text/event-stream"}
            if last_id is not None:
                headers["Last-Event-ID"] = str(last_id)
            try:
                with self._http.stream(
                    "GET",
                    f"{self._prefix}/{run_id}/events",
                    headers=headers,
                    timeout=httpx.Timeout(min(left, STREAM_READ_TIMEOUT_S)),
                ) as resp:
                    if resp.status_code != 200:
                        break  # no stream: the poll reports the final status
                    for frame_id, data in _sse_frames(resp.iter_lines()):
                        if self._monotonic() >= deadline:
                            raise timed_out("stream")
                        if data is None or data.get("run_id") != run_id:
                            continue  # a comment, or not this run's frame: never acted on
                        if frame_id is not None:
                            if last_id is not None and frame_id <= last_id:
                                continue  # replayed after a resume: already handled
                            last_id = frame_id
                        event = data.get("event")
                        if event == "message.delta":
                            delta = data.get("delta")
                            if isinstance(delta, str) and delta:
                                yield delta
                        elif event == "run.completed":
                            return RunResult(
                                run_id=run_id,
                                status=FINAL_OK,
                                output=_output_text(data.get("output")),
                                usage=data.get("usage") or {},
                                runtime=data.get("runtime") or {},
                                latency_ms=elapsed_ms(),
                            )
                        elif event in FAILURE_EVENTS:
                            raise RunFailed(
                                f"{event}: {data.get('error')}",
                                final=True,
                                run_id=run_id,
                                latency_ms=elapsed_ms(),
                                usage=data.get("usage") or {},
                                runtime=data.get("runtime") or {},
                            )
                break  # closed without a terminal event (spec concern 10)
            except httpx.TransportError as exc:
                drops += 1
                if self._monotonic() >= deadline:
                    raise timed_out(f"stream dropped: {exc}") from exc
                if drops > MAX_RECONNECTS:
                    raise RunFailed(
                        f"stream dropped {drops} times: {exc}",
                        run_id=run_id,
                        latency_ms=elapsed_ms(),
                    ) from exc
        return self._poll_until_final(run_id, deadline, elapsed_ms)

    def _start(
        self, session_id: str, input: str, idempotency_key: str, elapsed_ms: Callable[[], int]
    ) -> str:
        """POST the run; its ``run_id``, or :class:`RunFailed`. A POST whose answer was lost is
        sent again under the same key (:data:`POST_ATTEMPTS`): Hermes replays the run it took."""
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._http.post(
                    self._prefix,
                    json={"input": input, "session_id": session_id},
                    headers={**self._headers, "Idempotency-Key": idempotency_key},
                )
                break
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Never reached Hermes: nothing to replay, and the next attempt is the caller's.
                raise RunFailed(f"hermes unreachable: {exc}", latency_ms=elapsed_ms()) from exc
            except httpx.HTTPError as exc:
                if attempt >= POST_ATTEMPTS:
                    raise RunFailed(f"hermes unreachable: {exc}", latency_ms=elapsed_ms()) from exc
        if resp.status_code not in (200, 201, 202):
            raise RunFailed(
                f"run not accepted: HTTP {resp.status_code} {resp.text[:200]}",
                latency_ms=elapsed_ms(),
                final=400 <= resp.status_code < 500,
            )
        accepted = _json(resp)
        run_id = accepted.get("run_id") or accepted.get("id")
        if not run_id or not isinstance(run_id, str):
            raise RunFailed("run accepted without a valid run_id", latency_ms=elapsed_ms())
        return run_id

    def _poll_until_final(
        self, run_id: str, deadline: float, elapsed_ms: Callable[[], int]
    ) -> RunResult:
        """Poll ``GET {prefix}/{run_id}`` until the run reaches a final status or the deadline.
        Hermes unreachable for :data:`POLL_UNREACHABLE_S` in a row ends the poll early."""
        unreachable_since: float | None = None
        while True:
            try:
                poll = self._http.get(f"{self._prefix}/{run_id}", headers=self._headers)
            except httpx.HTTPError as exc:
                poll = None
                last_error = f"poll failed: {exc}"
                now = self._monotonic()
                unreachable_since = now if unreachable_since is None else unreachable_since
                if now - unreachable_since >= POLL_UNREACHABLE_S:
                    raise RunFailed(
                        f"hermes unreachable while polling: {exc}",
                        run_id=run_id,
                        latency_ms=elapsed_ms(),
                    ) from exc
            if poll is not None:
                unreachable_since = None
                if poll.status_code != 200:
                    raise RunFailed(
                        f"poll failed: HTTP {poll.status_code}",
                        run_id=run_id,
                        latency_ms=elapsed_ms(),
                    )
                info = _json(poll)
                status = info.get("status")
                if status == FINAL_OK:
                    return RunResult(
                        run_id=run_id,
                        status=status,
                        output=_output_text(info.get("output")),
                        usage=info.get("usage") or {},
                        runtime=info.get("runtime") or {},
                        latency_ms=elapsed_ms(),
                    )
                if status in FINAL_FAILED:
                    raise RunFailed(
                        f"run {status}: {info.get('error')}",
                        final=True,
                        run_id=run_id,
                        latency_ms=elapsed_ms(),
                        usage=info.get("usage") or {},
                        runtime=info.get("runtime") or {},
                    )
                last_error = f"run still {status}"
            if self._monotonic() >= deadline:
                raise self._timed_out(run_id, last_error, elapsed_ms)
            self._sleep(self._poll)

    def _timed_out(self, run_id: str, detail: str, elapsed_ms: Callable[[], int]) -> RunFailed:
        """The timeout failure, after asking Hermes to stop the run (:meth:`_stop`).

        Not final while the run may still finish: a retry under the same key replays whatever
        it became (ADR 0073). Usage the stop answer carries rides on the failure."""
        info, note = self._stop(run_id)
        usage = info.get("usage")
        runtime = info.get("runtime")
        return RunFailed(
            f"timed out after {self._timeout_s:g}s ({detail}; {note})",
            kind="timeout",
            run_id=run_id,
            latency_ms=elapsed_ms(),
            usage=usage if isinstance(usage, dict) else None,
            runtime=runtime if isinstance(runtime, dict) else None,
            final=info.get("status") in FINAL_FAILED,
        )

    def _stop(self, run_id: str) -> tuple[dict[str, Any], str]:
        """Ask Hermes to stop the run, within :data:`STOP_TIMEOUT_S`. Never raises: the answer's
        JSON (empty when there is none) and a short note for the failure message."""
        try:
            resp = self._http.post(
                f"{self._prefix}/{run_id}/stop",
                headers=self._headers,
                timeout=httpx.Timeout(STOP_TIMEOUT_S),
            )
        except Exception as exc:  # best effort: the caller's outcome is the timeout either way
            return {}, f"stop failed: {type(exc).__name__}"
        if resp.status_code != 200:
            return {}, f"stop failed: HTTP {resp.status_code}"
        info = _json(resp)
        return info, f"stop requested: {info.get('status') or 'no status'}"


def _sse_frames(lines: Iterable[str]) -> Iterator[tuple[int | None, dict[str, Any] | None]]:
    """Parse an event stream into ``(id, data)`` frames.

    A comment line (``: open``, ``: keepalive``, ``: stream closed``) gives ``(None, None)`` so
    the caller can check its deadline; a frame whose data is not a JSON object gives
    ``(id, None)``. An ``id`` that is not an integer is read as absent."""
    frame_id: int | None = None
    data_lines: list[str] = []
    for line in lines:
        if line == "":
            if data_lines:
                yield frame_id, _json_object("\n".join(data_lines))
            frame_id, data_lines = None, []
        elif line.startswith(":"):
            yield None, None
        else:
            name, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if name == "data":
                data_lines.append(value)
            elif name == "id":
                try:
                    frame_id = int(value)
                except ValueError:
                    frame_id = None
    if data_lines:
        yield frame_id, _json_object("\n".join(data_lines))


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _output_text(output: Any) -> str:
    """The run's final text. Hermes returns a string; tolerate a list of text parts."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output.strip()
    if isinstance(output, list):
        parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in output]
        return "".join(parts).strip()
    return str(output).strip()


# ---------------------------------------------------------------------------
# Usage ledger (ADR 0019)
# ---------------------------------------------------------------------------


def estimate_cost(model: str, usage: dict[str, Any]) -> float:
    """Estimated USD cost of one call, priced for the model that served it.

    Hermes and OpenRouter report `input_tokens` including the cache reads, so the uncached input
    is `input_tokens - cache_read_tokens`. Cache reads are priced once, at the cache-read rate,
    cache writes at the cache-write rate and output at the output rate. When `input_tokens` is
    below `cache_read_tokens` the source cannot have included them, and the input stands as
    reported; cache writes also stay in the uncached input (an upper bound).

    An estimate never falls below the bill: a model with no price row (and not a free one) is
    priced at the dearest row, not at zero.
    """
    if model.lower().startswith(FREE_MODEL_PREFIXES):
        return 0.0
    prices = PRICES_PER_MTOK.get(model) or max(
        PRICES_PER_MTOK.values(), key=lambda row: row["output"]
    )
    inp = _int(usage.get("input_tokens"))
    read = _int(usage.get("cache_read_tokens"))
    uncached = inp - read if inp >= read else inp
    total = (
        uncached * prices["input"]
        + read * prices["cache_read"]
        + _int(usage.get("cache_write_tokens")) * prices["cache_write"]
        + _int(usage.get("output_tokens")) * prices["output"]
    )
    return round(total / 1_000_000, 8)


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def record_usage(
    conn: sqlite3.Connection,
    *,
    application: dict[str, Any],
    profile: str,
    environment: str,
    now: datetime,
    result: RunResult | None = None,
    failure: RunFailed | None = None,
) -> None:
    """Write the usage row for one run. A replayed run_id is not counted twice; a success
    after a failed attempt of the same run replaces that attempt's row.

    A failure whose run already has a row (the run completed, then storing or sending its
    reply failed) marks that row failed; its token counts stay as recorded.

    A failure of a run Hermes took (it has a ``run_id``) with no usage reported has unknown
    counts, not zero ones: its error starts with :data:`USAGE_UNKNOWN` (ADR 0077)."""
    source = result if result is not None else failure
    assert source is not None
    usage = source.usage
    runtime = source.runtime
    model = runtime.get("model") or "unknown"
    location_id = repo.first_location_id(conn, application["id"])
    status = "ok" if failure is None else failure.kind
    error: str | None = None
    conflict: tuple[Any, ...] = ()
    if failure is not None:
        plain = str(failure)[:500]
        marked = f"{USAGE_UNKNOWN} {failure}"[:500]
        error = marked if failure.run_id is not None and not usage else plain
        conflict = (len(USAGE_UNKNOWN), USAGE_UNKNOWN, marked, plain)
    repo.insert_run_usage(
        conn,
        (
            iso(now),
            environment,
            application["client_id"],
            location_id,
            application["candidate_id"],
            application["id"],
            application["stage"],
            profile,
            source.run_id,
            application["id"],
            runtime.get("provider") or "unknown",
            model,
            _int(usage.get("input_tokens")),
            _int(usage.get("output_tokens")),
            _int(usage.get("cache_read_tokens")),
            _int(usage.get("cache_write_tokens")),
            estimate_cost(model, usage),
            source.latency_ms,
            status,
            error,
            *conflict,
        ),
        failed=failure is not None,
    )
