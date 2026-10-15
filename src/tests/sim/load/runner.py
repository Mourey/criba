"""Load generator: plays a schedule of Candidates against a stack (spec 004 task 7, ADR 0028).

One asyncio task per Candidate sleeps until its arrival time, then plays its persona with the
sim driver through the public web chat API, over one shared HTTP client. Every Candidate runs
without the sim clock (the clock is global; `sim.load.plan` keeps clock-moving personas out).

What is measured, per Candidate: when it started and finished, how the run ended (or the error
that stopped it), and every turn's reply latency as the driver saw it (send to first agent
message, and to the last of the burst). After the run, an optional enricher reads each
Application back through the API as an Operator: the stage and Outcome reached, whether they
are what the persona expects, the agent runs and their latency (Hermes, from the usage
ledger), the models that served them and their cost.

Spend: on a stack that might serve a real model the first Candidate is a probe. It plays alone
and is read back before anyone else arrives; the spend guard (`sim.spend`) then refuses the run
if the stack served anything but the stub without ``--real`` and a cap, and in real mode
checks before every arrival that the next Candidate fits under the cap.

Optionally a sampler reads ``docker stats`` for the Compose project every few seconds, so the
report can say which container saturated first.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from sim import asserts
from sim.clock import ClockState, SimClock
from sim.driver import ChatError, PersonaDriver, RunResult
from sim.load.plan import Arrival, LoadPlan
from sim.personas.schema import Persona
from sim.spend import SpendGuard, is_refused_run
from sim.stack import usage_of

Enricher = Callable[[str], Awaitable[dict[str, Any]]]  # phone -> ApplicationDetail (with cost)


@dataclass
class TurnSample:
    latency_first_s: float | None
    latency_last_s: float | None
    timed_out: bool


@dataclass
class CandidateResult:
    index: int
    persona_id: str
    phone: str
    scheduled_s: float
    started_s: float | None = None
    finished_s: float | None = None
    ended: str = "not_started"  # the driver's `ended`, or error / spend_cap / not_started
    error: str | None = None
    error_kind: str | None = None  # http_<status>, timeout, transport, other
    conversation_id: str | None = None
    turns: list[TurnSample] = field(default_factory=list)
    record: dict[str, Any] | None = None  # filled by the enricher

    @property
    def ok(self) -> bool:
        return self.error is None and self.ended not in (
            "timeout",
            "not_started",
            "spend_cap",
        )


@dataclass
class ResourceSample:
    t_s: float
    service: str
    cpu_pct: float
    mem_mib: float


@dataclass
class LoadRun:
    plan: LoadPlan
    started_at: str
    wall_s: float
    candidates: list[CandidateResult]
    resources: list[ResourceSample] = field(default_factory=list)
    real: bool = False
    spent_usd: float = 0.0
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FrozenClock(SimClock):
    """The clock a load Candidate gets: interrupts may fire (`say` only), time never moves.

    The driver asks for a clock whenever an interrupt fires, even one with nothing to advance;
    `sim.load.plan` keeps personas that do advance it out of a load run, so any call here is a
    bug and raises.
    """

    def __init__(self) -> None:
        self.jobs_run = 0

    async def _call(self, method: str, suffix: str = "", json: dict | None = None) -> ClockState:
        raise RuntimeError(f"a load run never moves the sim clock ({method} {suffix or '/'})")


def classify_error(exc: BaseException) -> str:
    if isinstance(exc, ChatError):
        return f"http_{exc.status}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "transport"
    return "other"


def _turns(run: RunResult) -> list[TurnSample]:
    return [TurnSample(t.latency_first_s, t.latency_last_s, t.timed_out) for t in run.turns]


def refused_runs(by_model: Sequence[dict[str, Any]]) -> int:
    """Runs Hermes refused (429 at its concurrent-run cap): `unknown` model, no tokens."""
    return sum(int(e.get("calls") or 0) for e in by_model if is_refused_run(e.get("model"), e))


def summarise_record(persona: Persona, app: dict[str, Any]) -> dict[str, Any]:
    """What the load report needs from one Application: correctness, runs, models, cost."""
    failures = list(asserts.check_persona(persona, app))
    usage = usage_of(app)
    kinds = asserts.event_kinds(app)
    return {
        "application_id": app.get("id"),
        "stage": app.get("stage"),
        "status": app.get("status"),
        "outcome": app.get("outcome"),
        "correct": not failures,
        "failures": failures[:5],
        "runs": usage["runs"],
        "models": usage["models"],
        "p50_run_latency_ms": usage["p50_latency_ms"],
        "est_cost_usd": usage["est_cost_usd"],
        "by_model": usage["by_model"],
        "refused_runs": refused_runs(usage["by_model"]),
        "tokens": {
            k: usage[k]
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
        },
        "validation_failures": kinds.count("answer_invalid"),
        "escalations": kinds.count("escalation_raised"),
    }


class LoadRunner:
    """Plays `arrivals` against the backend behind `http`.

    Args:
        http: shared client pointed at the stack (or an in-process app in tests).
        enrich: phone -> ApplicationDetail with cost; None skips the read-back (and the probe).
        guard: the spend guard; stub mode by default.
        sampler: an async callable run alongside the load, given a stop event and the run's
            start time; returns resource samples.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        enrich: Enricher | None = None,
        guard: SpendGuard | None = None,
        sampler: Callable[[asyncio.Event, float], Awaitable[list[ResourceSample]]] | None = None,
        reply_timeout_s: float = 30.0,
        poll_wait_s: float = 2.0,
        enrich_concurrency: int = 8,
        probe: bool = True,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.http = http
        self.enrich = enrich
        self.guard = guard or SpendGuard()
        self.sampler = sampler
        self.reply_timeout_s = reply_timeout_s
        self.poll_wait_s = poll_wait_s
        self.enrich_concurrency = enrich_concurrency
        self.probe = probe and enrich is not None
        self.progress = progress or (lambda _msg: None)
        self._t0 = 0.0
        self._max_cost_seen = 0.0

    def _now(self) -> float:
        return time.perf_counter() - self._t0

    async def _play(self, arrival: Arrival, result: CandidateResult) -> None:
        driver = PersonaDriver(
            self.http,
            clock=FrozenClock(),
            reply_timeout_s=self.reply_timeout_s,
            poll_wait_s=self.poll_wait_s,
            settle_s=0.05,
        )
        result.started_s = self._now()
        try:
            run = await driver.run(arrival.persona, phone=arrival.phone, align_clock=False)
        # Every failure is a data point, not a crash of the run.
        except (httpx.HTTPError, RuntimeError, ValueError, LookupError, OSError) as exc:
            result.ended = "error"
            result.error = f"{type(exc).__name__}: {exc}"[:300]
            result.error_kind = classify_error(exc)
        else:
            result.ended = run.ended
            result.conversation_id = run.conversation_id
            result.turns = _turns(run)
        result.finished_s = self._now()

    async def _read_back(self, arrival: Arrival, result: CandidateResult) -> None:
        if self.enrich is None or result.conversation_id is None:
            return
        try:
            app = await self.enrich(arrival.phone)
        except (LookupError, httpx.HTTPError, RuntimeError) as exc:
            result.record = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            return
        result.record = summarise_record(arrival.persona, app)

    def _charge(self, result: CandidateResult) -> None:
        """Count one Candidate's spend; raises SpendRefused in stub mode on a real model."""
        record = result.record or {}
        if "models" not in record:
            return
        cost = self.guard.record(
            record["models"],
            record.get("tokens") or {},
            record.get("est_cost_usd") or 0.0,
            record.get("by_model") or None,
        )
        self._max_cost_seen = max(self._max_cost_seen, cost)

    def _projected(self) -> float:
        # The dearest Candidate so far, with a margin: the next one may be longer.
        return self._max_cost_seen * 1.5

    async def run(self, plan: LoadPlan, arrivals: Sequence[Arrival]) -> LoadRun:
        started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        results = [CandidateResult(a.index, a.persona.id, a.phone, a.at_s) for a in arrivals]
        self._t0 = time.perf_counter()
        stop = asyncio.Event()
        sampling = asyncio.create_task(self.sampler(stop, self._t0)) if self.sampler else None
        offset = 0.0
        rest = list(zip(arrivals, results, strict=True))
        try:
            if self.probe and rest:
                arrival, result = rest.pop(0)
                self.progress(f"probe: {arrival.persona.id}")
                await self._play(arrival, result)
                await self._read_back(arrival, result)
                self._charge(result)
                offset = self._now()  # everyone else arrives on schedule after the probe

            async def one(arrival: Arrival, result: CandidateResult) -> None:
                delay = arrival.at_s + offset - self._now()
                if delay > 0:
                    await asyncio.sleep(delay)
                if self.guard.real and not self.guard.allow(self._projected()):
                    result.ended = "spend_cap"
                    return
                await self._play(arrival, result)
                if self.guard.real:  # count real spend as it happens
                    await self._read_back(arrival, result)
                    self._charge(result)

            await asyncio.gather(*(one(a, r) for a, r in rest))
        finally:
            wall = self._now()
            stop.set()
            resources = await sampling if sampling else []
        if not self.guard.real and self.enrich is not None:
            sem = asyncio.Semaphore(self.enrich_concurrency)

            async def back(arrival: Arrival, result: CandidateResult) -> None:
                async with sem:
                    await self._read_back(arrival, result)

            await asyncio.gather(*(back(a, r) for a, r in rest))
            for _, result in rest:
                self._charge(result)
        self.guard.save()
        capped = sum(1 for r in results if r.ended == "spend_cap")
        note = f"{capped} Candidates not started: the spend cap" if capped else None
        return LoadRun(
            plan=plan,
            started_at=started_at,
            wall_s=wall,
            candidates=results,
            resources=resources,
            real=self.guard.real,
            spent_usd=round(self.guard.spent_usd, 6),
            note=note,
        )


# ---- docker stats ------------------------------------------------------------------------------

_SIZE = re.compile(r"^\s*([\d.]+)\s*([KMGT]?i?B)", re.IGNORECASE)
_UNITS = {
    "b": 1 / 2**20,
    "kb": 1e3 / 2**20,
    "kib": 1 / 2**10,
    "mb": 1e6 / 2**20,
    "mib": 1.0,
    "gb": 1e9 / 2**20,
    "gib": 2**10,
    "tb": 1e12 / 2**20,
    "tib": 2**20,
}


def parse_mem_mib(text: str) -> float:
    """``"49.52MiB / 7.75GiB"`` -> 49.52 (the usage part, in MiB)."""
    match = _SIZE.match(text or "")
    if not match:
        return 0.0
    return round(float(match.group(1)) * _UNITS.get(match.group(2).lower(), 0.0), 2)


def parse_stats(lines: str, services: dict[str, str], t_s: float) -> list[ResourceSample]:
    """`docker stats --format '{{json .}}'` output -> samples keyed by Compose service."""
    out = []
    for line in lines.splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        name = row.get("Name") or row.get("Container") or ""
        cpu = float(str(row.get("CPUPerc", "0")).rstrip("%") or 0)
        out.append(
            ResourceSample(
                t_s=round(t_s, 2),
                service=services.get(name, name),
                cpu_pct=cpu,
                mem_mib=parse_mem_mib(str(row.get("MemUsage", ""))),
            )
        )
    return out


Run = Callable[..., subprocess.CompletedProcess[str]]


class DockerStatsSampler:
    """Samples CPU and memory of every container in a Compose project."""

    def __init__(self, project: str, *, interval_s: float = 3.0, run: Run = subprocess.run):
        self.project = project
        self.interval_s = interval_s
        self._run = run

    def containers(self) -> dict[str, str]:
        """Container name -> Compose service name."""
        result = self._run(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                '{{.Names}}\t{{.Label "com.docker.compose.service"}}',
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        pairs = [line.split("\t", 1) for line in result.stdout.splitlines() if "\t" in line]
        return {name: service for name, service in pairs}

    def sample(self, services: dict[str, str], t_s: float) -> list[ResourceSample]:
        if not services:
            return []
        result = self._run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", *services],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return parse_stats(result.stdout, services, t_s)

    async def __call__(self, stop: asyncio.Event, t0: float) -> list[ResourceSample]:
        services = await asyncio.to_thread(self.containers)
        samples: list[ResourceSample] = []
        while not stop.is_set():
            t_s = time.perf_counter() - t0
            samples += await asyncio.to_thread(self.sample, services, t_s)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_s)
            except TimeoutError:
                continue
        return samples


__all__ = [
    "CandidateResult",
    "DockerStatsSampler",
    "FrozenClock",
    "LoadRun",
    "LoadRunner",
    "ResourceSample",
    "TurnSample",
    "classify_error",
    "parse_mem_mib",
    "parse_stats",
    "summarise_record",
]
