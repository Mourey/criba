"""What a load run offers: the rate, the arrival times and the Candidates (spec 004 task 7).

**Rate.** A weekly rate (1,000 or 10,000 Candidates a week) becomes an arrival rate per second
of simulated time, multiplied by a peak factor (the busiest hour against the weekly average).
ADR 0028 puts 10,000 a week at about 0.7 messages a second at peak: about 0.0165 Candidates a
second on average, times a peak factor of 3, times roughly 14 messages each.

**Speed-up.** Simulated time can run faster than the wall clock: with ``speedup`` 10, thirty
minutes of simulated peak traffic arrive in three real minutes. Arrivals compress; a
Candidate's own replies do not slow down to match, because the driver answers each agent
message as soon as it lands (no typing time). That is harsher than reality, where a
conversation spreads over minutes, so the numbers are on the safe side.

**Arrivals** are a Poisson process: exponential gaps at the arrival rate, seeded, so a plan
is reproducible.

**Candidates** are the seed personas that never move the sim clock (the clock is global to
the backend, so a persona that advances it would move time under every other Candidate), each
with a fresh phone number. Their words stay as written: the stub model interprets only the
seed personas' messages (`sim.stub_llm.interpret`), so new wording would read as a miss.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sim.personas.schema import Persona
from sim.stack import new_phone

SECONDS_PER_WEEK = 7 * 24 * 3600


@dataclass(frozen=True)
class LoadPlan:
    weekly_rate: float
    sim_duration_s: float
    speedup: float = 1.0
    peak_factor: float = 1.0
    seed: int = 0

    def __post_init__(self) -> None:
        if self.weekly_rate <= 0:
            raise ValueError("weekly_rate must be positive")
        if self.sim_duration_s <= 0:
            raise ValueError("sim_duration_s must be positive")
        if self.speedup <= 0 or self.peak_factor <= 0:
            raise ValueError("speedup and peak_factor must be positive")

    @property
    def sim_rate_per_s(self) -> float:
        """Candidates per second of simulated time (at the peak factor)."""
        return self.weekly_rate / SECONDS_PER_WEEK * self.peak_factor

    @property
    def wall_rate_per_s(self) -> float:
        """Candidates per second of wall-clock time."""
        return self.sim_rate_per_s * self.speedup

    @property
    def wall_duration_s(self) -> float:
        return self.sim_duration_s / self.speedup

    @property
    def expected_arrivals(self) -> float:
        return self.sim_rate_per_s * self.sim_duration_s

    def describe(self) -> str:
        return (
            f"{self.weekly_rate:,.0f}/week x peak {self.peak_factor:g} over "
            f"{self.sim_duration_s / 60:g} min of sim time at speed-up {self.speedup:g} "
            f"({self.wall_duration_s / 60:.1f} min wall, "
            f"about {self.expected_arrivals:.0f} Candidates)"
        )


def poisson_arrivals(rate_per_s: float, duration_s: float, rng: random.Random) -> list[float]:
    """Arrival offsets in seconds within `duration_s`, exponential gaps at `rate_per_s`."""
    if rate_per_s <= 0:
        return []
    times: list[float] = []
    t = rng.expovariate(rate_per_s)
    while t < duration_s:
        times.append(t)
        t += rng.expovariate(rate_per_s)
    return times


def moves_clock(persona: Persona) -> bool:
    return any(i.advance for i in persona.interrupts)


def load_personas_for(personas: Mapping[str, Persona]) -> dict[str, Persona]:
    """The personas a load run may use: those that never advance the sim clock."""
    return {pid: p for pid, p in sorted(personas.items()) if not moves_clock(p)}


@dataclass(frozen=True)
class Arrival:
    index: int
    at_s: float  # wall-clock offset from the start of the run
    persona: Persona
    phone: str


def schedule(
    plan: LoadPlan,
    personas: Mapping[str, Persona],
    *,
    mix: Mapping[str, float] | None = None,
) -> list[Arrival]:
    """The run's Candidates: arrival times (wall clock) and who arrives.

    `mix` weights persona ids (default: every usable persona equally). Personas that move the
    clock are refused, not silently dropped.
    """
    usable = load_personas_for(personas)
    weights = dict(mix) if mix else dict.fromkeys(usable, 1.0)
    unknown = sorted(set(weights) - set(personas))
    if unknown:
        raise ValueError(f"unknown personas in the mix: {', '.join(unknown)}")
    clock_movers = sorted(set(weights) - set(usable))
    if clock_movers:
        raise ValueError(
            f"personas that move the sim clock cannot run under load: {', '.join(clock_movers)}"
        )
    ids: Sequence[str] = [pid for pid, w in weights.items() if w > 0]
    if not ids:
        raise ValueError("the mix has no persona with a positive weight")
    rng = random.Random(plan.seed)
    offsets = poisson_arrivals(plan.wall_rate_per_s, plan.wall_duration_s, rng)
    phones: set[str] = set()
    arrivals = []
    for index, at in enumerate(offsets):
        pid = rng.choices(ids, weights=[weights[i] for i in ids])[0]
        persona = usable[pid]
        phone = new_phone(persona.identity.country, rng)
        while phone in phones:
            phone = new_phone(persona.identity.country, rng)
        phones.add(phone)
        arrivals.append(Arrival(index=index, at_s=at, persona=persona, phone=phone))
    return arrivals


__all__ = [
    "SECONDS_PER_WEEK",
    "Arrival",
    "LoadPlan",
    "load_personas_for",
    "moves_clock",
    "poisson_arrivals",
    "schedule",
]
