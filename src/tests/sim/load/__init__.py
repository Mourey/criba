"""Load mode (spec 004 tasks 7 and 8, ADR 0028): Candidates at a weekly rate, then a report.

- `sim.load.plan`: the rate, Poisson arrivals and the Candidates (seed personas, fresh numbers);
- `sim.load.runner`: asyncio workers that play them against a stack, plus resource sampling;
- `sim.load.report`: throughput, latency percentiles, errors, cost and the first saturated
  component, as data and as markdown (`docs/evidence/load-results.md`).

Run it with ``python -m sim.load --help`` (from ``src/tests``, see `sim.load.__main__`).
"""
