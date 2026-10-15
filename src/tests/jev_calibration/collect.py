"""Run the labelled messages against real Jev through OpenRouter and keep every answer.

Spends real money (about $0.00003 a call), so it goes through the same spend guard as the other
real-model runs (``sim.spend``): ``CRIBA_REAL_SPEND_CAP_USD`` must be positive and the call
stops before it would pass the cap; the spend is appended to ``CRIBA_REAL_SPEND_LEDGER``.

A failed call stays in the results as a failure (it counts in the denominator of every rate),
and latency is measured on the calls that succeeded, with the failures listed apart.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from criba.jev.client import JevFailed, JevPort
from jev_calibration.fixtures import Fixture
from sim.spend import SpendGuard

PROJECTED_CALL_USD = 0.0002  # the guard's allowance per call; the real one is about 0.00003


@dataclass
class Row:
    fixture_id: str
    rep: int
    ok: bool
    error: str | None = None
    latency_ms: int = 0
    model: str | None = None
    request_id: str | None = None
    cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    lang: str | None = None
    lang_confidence: float | None = None
    switch: float | None = None
    signals: dict[str, float] = field(default_factory=dict)
    asks: str | None = None
    asks_confidence: float | None = None
    unreadable: float | None = None
    # The display-only readings (ADR 0071); None on a failure or a missing answer. A results
    # file written before they existed loads with all three None.
    intent: str | None = None
    intent_confidence: float | None = None
    malice: float | None = None


def _one(client: JevPort, fixture: Fixture, rep: int) -> Row:
    started = time.monotonic()
    try:
        result = client.read(fixture.jev_input())
    except JevFailed as exc:
        wall = int((time.monotonic() - started) * 1000)
        return Row(fixture.id, rep, False, error=str(exc), latency_ms=exc.latency_ms or wall)
    reading = result.reading
    return Row(
        fixture.id,
        rep,
        True,
        latency_ms=result.latency_ms,
        model=result.model,
        request_id=result.request_id,
        cost_usd=result.cost_usd,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        lang=reading.lang,
        lang_confidence=reading.lang_confidence,
        switch=reading.switch,
        signals=dict(reading.signals),
        asks=reading.asks,
        asks_confidence=reading.asks_confidence,
        unreadable=reading.unreadable,
        intent=reading.intent,
        intent_confidence=reading.intent_confidence,
        malice=reading.malice,
    )


def collect(
    client: JevPort,
    fixtures: list[Fixture],
    *,
    repeats: int,
    workers: int,
    guard: SpendGuard,
) -> list[Row]:
    """Every fixture ``repeats`` times, ``workers`` at a time (the service takes about 8).
    Stops (returning what it has) when the guard says the next call could pass the cap."""
    jobs = [(fixture, rep) for rep in range(repeats) for fixture in fixtures]
    rows: list[Row] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(jobs), workers):
            batch = jobs[start : start + workers]
            if not guard.allow(PROJECTED_CALL_USD * len(batch)):
                break
            for row in pool.map(lambda job: _one(client, *job), batch):
                guard.spent_usd += row.cost_usd or PROJECTED_CALL_USD / 4
                rows.append(row)
    return rows


def dump(rows: list[Row]) -> str:
    return json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=1)


def load(text: str) -> list[Row]:
    raw: list[dict[str, Any]] = json.loads(text)
    return [Row(**r) for r in raw]
