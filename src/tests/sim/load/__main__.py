"""Command line for load mode (spec 004 tasks 7 and 8).

From the repo root, against a sim stack (stub model, free)::

    PYTHONPATH=src/tests CRIBA_E2E_SEED_PASSWORD_FILE=secrets/criba_seed_password.txt \\
        uv run --project src/backend python -m sim.load \\
        --base-url http://localhost:18106 --compose-project criba-w3-3 \\
        --weekly 10000 --peak-factor 3 --sim-minutes 30 --speedup 10 \\
        --out /tmp/load-results.md --json /tmp/load.json

``--speedup 10,30,60`` runs a ramp: one step per speed-up, the same mix, a fresh seed each.

Real model: add ``--real`` and set ``CRIBA_REAL_SPEND_CAP_USD`` (default 0 refuses). Without
``--real`` the target must be a sim stack, and the first Candidate (a probe, read back before
anyone else arrives) must have been served by the stub, or the run stops.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from sim.load.plan import LoadPlan, schedule
from sim.load.report import compute, find_bottleneck, markdown
from sim.load.runner import DockerStatsSampler, LoadRunner
from sim.personas.loader import load_personas
from sim.spend import SpendGuard, SpendRefused
from sim.stack import is_sim_stack, login, seed_password, settled_application


def parse_mix(text: str | None) -> dict[str, float] | None:
    """``"qualified_es=3,no_license_mx=1"`` -> weights; None for the default (all equal)."""
    if not text:
        return None
    mix = {}
    for part in text.split(","):
        name, _, weight = part.partition("=")
        mix[name.strip()] = float(weight) if weight else 1.0
    return mix


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m sim.load", description=__doc__.split("\n")[0])
    p.add_argument("--base-url", required=True, help="the stack's web URL")
    p.add_argument("--weekly", type=float, default=10_000, help="Candidates a week (default 10000)")
    p.add_argument("--peak-factor", type=float, default=3.0, help="peak hour vs weekly average")
    p.add_argument("--sim-minutes", type=float, default=30.0, help="simulated minutes per step")
    p.add_argument("--speedup", default="10", help="speed-up, or a comma list for a ramp")
    p.add_argument("--mix", help="persona weights, e.g. qualified_es=3,no_license_mx=1")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--compose-project", help="sample docker stats for this Compose project")
    p.add_argument("--reply-timeout", type=float, default=30.0)
    p.add_argument("--no-read-back", action="store_true", help="skip reading Applications back")
    p.add_argument("--real", action="store_true", help="allow a real model (needs a spend cap)")
    p.add_argument("--out", type=Path, help="write the markdown report here")
    p.add_argument("--json", type=Path, help="write the raw runs and metrics here")
    p.add_argument("--title", default="Load results")
    return p


async def main_async(args: argparse.Namespace) -> int:
    try:
        guard = SpendGuard.from_env(real=args.real, label="load")
    except SpendRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        print("real-model spend: $0.0000 (nothing was called)", file=sys.stderr)
        return 2
    try:
        return await _run(args, guard)
    finally:
        guard.save()
        print(guard.summary(), file=sys.stderr)


async def _run(args: argparse.Namespace, guard: SpendGuard) -> int:
    personas = load_personas()
    limits = httpx.Limits(max_connections=2000, max_keepalive_connections=200)
    async with (
        httpx.AsyncClient(base_url=args.base_url, timeout=30, limits=limits) as http,
        httpx.AsyncClient(base_url=args.base_url, timeout=30) as operator,
    ):
        sim = await is_sim_stack(http)
        if not sim and not args.real:
            print(
                "refused: not a sim stack (no /api/sim/clock); a real stack needs --real",
                file=sys.stderr,
            )
            return 2
        if sim:
            await http.post("/api/sim/clock/reset")
        enrich = None
        if not args.no_read_back:
            await login(operator, seed_password())

            async def enrich(phone: str) -> dict[str, Any]:
                return await settled_application(operator, phone, wait_s=5.0)

        elif args.real:
            print("refused: --real needs the read-back to count spend", file=sys.stderr)
            return 2
        sampler = DockerStatsSampler(args.compose_project) if args.compose_project else None
        steps = []
        raw = []
        for i, speedup in enumerate(float(s) for s in args.speedup.split(",")):
            plan = LoadPlan(
                weekly_rate=args.weekly,
                sim_duration_s=args.sim_minutes * 60,
                speedup=speedup,
                peak_factor=args.peak_factor,
                seed=args.seed + i,
            )
            arrivals = schedule(plan, personas, mix=parse_mix(args.mix))
            print(
                f"step {i + 1}: {plan.describe()}: {len(arrivals)} Candidates",
                file=sys.stderr,
            )
            runner = LoadRunner(
                http,
                enrich=enrich,
                guard=guard,
                sampler=sampler,
                reply_timeout_s=args.reply_timeout,
                progress=lambda msg: print(f"  {msg}", file=sys.stderr),
            )
            try:
                run = await runner.run(plan, arrivals)
            except SpendRefused as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 3
            metrics = compute(run)
            bottleneck = find_bottleneck(metrics, real=args.real)
            label = f"Step {i + 1}: speed-up {speedup:g}"
            steps.append((label, metrics, bottleneck))
            raw.append(
                {
                    "label": label,
                    "metrics": metrics,
                    "bottleneck": bottleneck,
                    "run": run.to_dict(),
                }
            )
            lat = metrics["latency_s"]
            print(
                f"  done: {metrics['candidates']['finished']}/"
                f"{metrics['candidates']['started']} finished, "
                f"p95 {lat['p95']} s, errors {metrics['errors']['rate']}, "
                f"bottleneck {bottleneck['component']} "
                f"({'saturated' if bottleneck['saturated'] else 'headroom'})",
                file=sys.stderr,
            )
            if bottleneck["saturated"]:
                break
    report = markdown(steps, title=args.title)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    else:
        print(report)
    if args.json:
        args.json.write_text(json.dumps(raw, indent=1, default=str), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(build_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
