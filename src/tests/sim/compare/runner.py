"""Model comparison runner: the same personas against a stack, one dataset line each (task 9).

The runner does not choose the model: the stack does. Point it at a stack whose screening
profile serves the model under test, run it once per model with the same persona set, and the
lines land in one dataset (``--append``). On the sim stack the stub serves every turn, so a
stub run proves the pipeline end to end (run, dataset, import, dashboard) at no cost; its
``--label`` names the line group, for example ``stub-as-haiku``.

Personas run one at a time, each with a fresh phone number, and the sim clock is reset before
each (personas that move it share one clock). Against a stack without the sim clock (``clock``
False) the personas that move it are left out.

After each persona the Application is read back as an Operator (its events and the cost block
from the usage ledger) and turned into a :class:`~sim.compare.dataset.RunRecord`.

Spend (`sim.spend`): in stub mode any served model other than the stub stops the run after the
first persona. In real mode the cap is checked before every persona against the dearest persona
so far times 1.5; the first persona is projected from :data:`FIRST_PERSONA_TOKENS` at the
expected model's guard price.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from sim import asserts
from sim.clock import SimClock
from sim.compare.dataset import RunRecord
from sim.driver import PersonaDriver, RunResult
from sim.load.plan import moves_clock
from sim.load.runner import FrozenClock
from sim.personas.schema import Persona
from sim.spend import (
    SpendGuard,
    SpendRefused,
    is_stub_model,
    reported_cost,
    token_cost,
)
from sim.stack import new_phone, usage_of

Enricher = Callable[[str], Awaitable[dict[str, Any]]]

# A long seed persona: about 25 model calls, each up to the 16,000 prompt tokens the real run
# measured (model-comparison.md), plus replies, with no cache credit. Used only to project the
# first persona's cost in real mode; later personas are projected from the dearest so far.
FIRST_PERSONA_TOKENS = {"input_tokens": 400_000, "output_tokens": 10_000}
STUB_LABEL = "stub-model"


def build_record(
    *,
    dataset: str,
    label: str,
    persona: Persona,
    rep: int,
    started_at: str,
    run: RunResult,
    app: dict[str, Any],
    guard_cost: float,
    cost: float | None = None,
) -> RunRecord:
    """One dataset line. `cost` is the run's cost for the report (see
    :func:`sim.spend.reported_cost`); without it the backend's own estimate stands."""
    failures = list(asserts.check_persona(persona, app))
    usage = usage_of(app)
    kinds = asserts.event_kinds(app)
    latencies = [t.latency_first_s for t in run.turns if t.latency_first_s is not None]
    expect = persona.expect
    return RunRecord(
        dataset=dataset,
        model=label,
        persona_id=persona.id,
        rep=rep,
        started_at=started_at,
        ended=run.ended,
        served_models=usage["models"],
        conversation_id=run.conversation_id,
        application_id=app.get("id"),
        expected={
            "stage": expect.stage,
            "status": expect.status,
            "outcome": expect.outcome,
        },
        stage=app.get("stage"),
        status=app.get("status"),
        outcome=app.get("outcome"),
        correct=not failures,
        failures=failures[:10],
        validation_failures=kinds.count("answer_invalid"),
        escalations=kinds.count("escalation_raised"),
        turns=len(run.turns),
        agent_runs=usage["runs"],
        latency_s=[round(v, 4) for v in latencies],
        tokens={
            k: usage[k]
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
        },
        cost_usd=usage["est_cost_usd"] if cost is None else cost,
        guard_cost_usd=round(guard_cost, 8),
    )


class ComparisonRunner:
    """Plays personas against one stack and returns their dataset lines.

    Args:
        http: client for the web chat (and the sim clock).
        enrich: phone -> ApplicationDetail with the Operator cost block.
        dataset: the dataset name the lines carry.
        label: the model label; defaults to `expected_model`, else ``stub-model``.
        guard: the spend guard (stub mode by default).
        expected_model: the turn model the stack should serve; required in real mode, and
            checked against what was served.
        clock: the stack has the sim clock (CRIBA_ENV=sim).
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        enrich: Enricher,
        *,
        dataset: str,
        label: str | None = None,
        guard: SpendGuard | None = None,
        expected_model: str | None = None,
        clock: bool = True,
        reply_timeout_s: float = 60.0,
        poll_wait_s: float = 5.0,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.http = http
        self.enrich = enrich
        self.dataset = dataset
        self.guard = guard or SpendGuard()
        self.expected_model = expected_model
        self.label = label or expected_model or STUB_LABEL
        self.clock = clock
        self.reply_timeout_s = reply_timeout_s
        self.poll_wait_s = poll_wait_s
        self.progress = progress or (lambda _msg: None)
        self._max_cost = 0.0
        if self.guard.real and not expected_model:
            raise SpendRefused("a real-model comparison needs --model (the turn model to expect)")

    def usable(self, personas: Sequence[Persona]) -> list[Persona]:
        return [p for p in personas if self.clock or not moves_clock(p)]

    def projected(self) -> float:
        if self._max_cost > 0:
            return self._max_cost * 1.5
        if self.expected_model and not is_stub_model(self.expected_model):
            return token_cost([self.expected_model], FIRST_PERSONA_TOKENS, self.guard.prices)
        return 0.0

    async def run_one(self, persona: Persona, rep: int) -> RunRecord:
        started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        clock = SimClock(self.http) if self.clock else FrozenClock()
        driver = PersonaDriver(
            self.http,
            clock=clock,
            reply_timeout_s=self.reply_timeout_s,
            poll_wait_s=self.poll_wait_s,
        )
        if self.clock:
            await clock.reset()
        phone = new_phone(persona.identity.country)
        t0 = time.perf_counter()
        run = await driver.run(persona, phone=phone, align_clock=self.clock)
        app = await self.enrich(phone)
        usage = usage_of(app)
        if self.expected_model and self.guard.real and self.expected_model not in usage["models"]:
            raise SpendRefused(
                f"expected {self.expected_model}, the stack served {usage['models']}: "
                "stopping before the dataset mixes models"
            )
        tokens = {
            k: usage[k]
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
        }
        guard_cost = self.guard.record(
            usage["models"], tokens, usage["est_cost_usd"], usage["by_model"]
        )
        self._max_cost = max(self._max_cost, guard_cost)
        record = build_record(
            dataset=self.dataset,
            label=self.label,
            persona=persona,
            rep=rep,
            started_at=started_at,
            run=run,
            app=app,
            guard_cost=guard_cost,
            cost=reported_cost(usage["by_model"], self.guard.prices, usage["est_cost_usd"]),
        )
        self.progress(
            f"{persona.id} #{rep}: {record.ended}, stage {record.stage}, "
            f"{'ok' if record.correct else 'NOT as expected'}, {time.perf_counter() - t0:.1f}s, "
            f"${guard_cost:.4f} (total ${self.guard.total_usd:.4f})"
        )
        return record

    async def run(
        self, personas: Sequence[Persona], reps: int = 1
    ) -> tuple[list[RunRecord], str | None]:
        """Every usable persona `reps` times.

        Returns the lines and why it stopped early, if it did.
        """
        records: list[RunRecord] = []
        for rep in range(1, reps + 1):
            for persona in self.usable(personas):
                if not self.guard.allow(self.projected()):
                    return records, (
                        f"spend cap: ${self.guard.total_usd:.4f} spent, next persona projected "
                        f"${self.projected():.4f}, cap ${self.guard.cap_usd:.2f}"
                    )
                try:
                    records.append(await self.run_one(persona, rep))
                except (httpx.HTTPError, RuntimeError, LookupError) as exc:
                    if isinstance(exc, SpendRefused):
                        raise
                    if self.guard.real:
                        # What the failed persona spent is unknown: stop rather than guess.
                        raise SpendRefused(
                            f"{persona.id} failed ({exc}); its spend is unknown, so the run stops"
                        ) from exc
                    records.append(
                        RunRecord(
                            dataset=self.dataset,
                            model=self.label,
                            persona_id=persona.id,
                            rep=rep,
                            started_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            ended="error",
                            error=f"{type(exc).__name__}: {exc}"[:300],
                        )
                    )
                    self.progress(f"{persona.id} #{rep}: error {exc}")
        return records, None


__all__ = ["FIRST_PERSONA_TOKENS", "STUB_LABEL", "ComparisonRunner", "build_record"]
