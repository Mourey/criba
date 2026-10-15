"""Live smoke run: a handful of real conversations against the real stack, one report.

From the repo root, against a real stack (no sim overlay: the production `criba-recruiter`
profile on Claude Haiku 5.5 through OpenRouter)::

    export PYTHONPATH=src/tests CRIBA_E2E_SEED_PASSWORD_FILE=secrets/criba_seed_password.txt
    export CRIBA_REAL_SPEND_CAP_USD=3 CRIBA_REAL_SPEND_LEDGER=/tmp/criba-real-spend.json
    uv run --project src/backend python -m live.smoke --base-url http://localhost:18160 \\
        --out reports/live-smoke.md

It spends real money, so it is opt in twice, like the model comparison (`sim.spend`): a positive
``CRIBA_REAL_SPEND_CAP_USD`` is required (the default 0 refuses), the ledger named by
``CRIBA_REAL_SPEND_LEDGER`` carries the total across commands, and the run stops before a flow
whose projected cost would pass the cap. The last line printed is the spend line.

Exit code: 0 every flow passed, 1 a flow failed or errored, 2 refused to start (no cap, a sim
stack, bad login), 3 the spend guard refused mid-run, 4 stopped early at the cap.

`scripts/live_smoke.sh` does the whole thing: brings the stack up, runs this, tears it down.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from criba.summary import DEFAULT_SUMMARY_MODEL as SUMMARY_MODEL
from live.client import DEFAULT_MODEL, LiveStack
from live.flows import FLOWS, FlowResult, FlowSpec, run_flow, select
from live.report import RunMeta, render, short_summary
from sim.compare.runner import FIRST_PERSONA_TOKENS
from sim.spend import SpendGuard, SpendRefused, token_cost
from sim.stack import is_sim_stack, login, seed_password

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PASSWORD_FILE = REPO_ROOT / "secrets" / "criba_seed_password.txt"
# The summary is one Sonnet call per Qualified conversation, about 3,000 tokens in and 400 out.
SUMMARY_PROJECTION_USD = 0.03

Progress = Callable[[str], None]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m live.smoke", description=__doc__.split("\n")[0])
    p.add_argument("--base-url", required=True, help="the real stack, e.g. http://localhost:18160")
    p.add_argument("--out", type=Path, help="write the Markdown report here (default: print it)")
    p.add_argument("--json", type=Path, help="also write every result as JSON")
    p.add_argument(
        "--flows",
        help=f"comma list of flows to run (default: all of {', '.join(f.id for f in FLOWS)})",
    )
    p.add_argument("--model", default=DEFAULT_MODEL, help="the turn model the stack should serve")
    p.add_argument("--reply-timeout", type=float, default=90.0)
    p.add_argument(
        "--seed-password-file",
        type=Path,
        help="the stack's seed password (default: CRIBA_E2E_SEED_PASSWORD_FILE, then "
        "secrets/criba_seed_password.txt)",
    )
    p.add_argument("--note", default="", help="one line for the report header")
    return p


def git_state(root: Path = REPO_ROOT) -> tuple[str, bool]:
    """The short commit of `root` and whether tracked files have uncommitted changes."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", False
    return commit, dirty


def password(args: argparse.Namespace) -> str:
    """The seed password: the option, else the environment, else the repo's secrets file."""
    if args.seed_password_file:
        return args.seed_password_file.read_text(encoding="utf-8").strip()
    from_env = seed_password()
    if from_env:
        return from_env
    if DEFAULT_PASSWORD_FILE.exists():
        return DEFAULT_PASSWORD_FILE.read_text(encoding="utf-8").strip()
    return ""


def first_projection(guard: SpendGuard, model: str) -> float:
    """A conversation's cost before any has run: a long one at the model's list price."""
    return token_cost([model], FIRST_PERSONA_TOKENS, guard.prices) + SUMMARY_PROJECTION_USD


async def run_all(
    stack: LiveStack, flows: Sequence[FlowSpec], progress: Progress
) -> tuple[list[FlowResult], str | None]:
    """The flows in order. Stops before a flow the cap cannot take; the rest are 'not run'."""
    guard = stack.guard
    results: list[FlowResult] = []
    dearest = 0.0
    stopped: str | None = None
    for spec in flows:
        projected = dearest * 1.5 if dearest > 0 else first_projection(guard, stack.model)
        if stopped or not guard.allow(projected):
            stopped = stopped or (
                f"spend cap: ${guard.total_usd:.4f} counted, the next flow projected "
                f"${projected:.4f}, cap ${guard.cap_usd:.2f}"
            )
            skipped = FlowResult(spec.id, spec.title)
            skipped.not_run = stopped
            results.append(skipped)
            continue
        res = await run_flow(stack, spec)
        dearest = max(dearest, res.charge.guard_usd)
        progress(
            f"{spec.id}: {res.status}, {res.elapsed_s:.1f}s, ${res.charge.cost_usd:.4f} "
            f"(counted ${res.charge.guard_usd:.4f}, total ${guard.total_usd:.4f})"
        )
        for check in res.checks:
            if not check.ok:
                progress(f"  FAIL {check.name}: {check.detail}")
        if res.error:
            progress(f"  ERROR {res.error}")
        results.append(res)
    return results, stopped


def write_outputs(args: argparse.Namespace, results: Sequence[FlowResult], meta: RunMeta) -> None:
    text = render(results, meta)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(short_summary(results), flush=True)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text, flush=True)
    if args.json:
        args.json.write_text(
            json.dumps({"meta": asdict(meta), "results": [asdict(r) for r in results]}, indent=2)
            + "\n",
            encoding="utf-8",
        )


def _err(message: str) -> None:
    print(message, file=sys.stderr)


async def smoke(args: argparse.Namespace) -> int:
    try:
        flows = select([f.strip() for f in args.flows.split(",")] if args.flows else None)
        guard = SpendGuard.from_env(real=True, label="live-smoke")
    except (ValueError, SpendRefused) as exc:
        _err(f"refused: {exc}")
        _err("real-model spend: $0.0000 (nothing was called)")
        return 2
    code = 0
    try:
        code = await _smoke(args, guard, flows)
    finally:
        guard.save()
        _err(guard.summary())
    return code


async def _smoke(args: argparse.Namespace, guard: SpendGuard, flows: Sequence[FlowSpec]) -> int:
    started = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    async with (
        httpx.AsyncClient(base_url=args.base_url, timeout=30) as chat,
        httpx.AsyncClient(base_url=args.base_url, timeout=30) as operator,
    ):
        if await is_sim_stack(chat):
            _err(
                "refused: this is a sim stack (stub model, sim clock); the run needs the real stack"
            )
            return 2
        try:
            await login(operator, password(args))
        except (RuntimeError, OSError, httpx.HTTPError) as exc:
            _err(f"refused: cannot log in as the Operator: {exc}")
            return 2
        stack = LiveStack(
            chat, operator, guard, model=args.model, reply_timeout_s=args.reply_timeout
        )
        try:
            results, stopped = await run_all(stack, flows, lambda msg: _err(f"  {msg}"))
        except SpendRefused as exc:
            _err(f"refused: {exc}")
            return 3
    commit, dirty = git_state()
    meta = RunMeta(
        started_at=started,
        commit=commit,
        dirty=dirty,
        base_url=args.base_url,
        model=args.model,
        summary_model=SUMMARY_MODEL,
        spend_line=guard.summary(),
        note=args.note,
        stopped=stopped,
    )
    write_outputs(args, results, meta)
    if stopped:
        _err(f"stopped early: {stopped}")
        return 4
    return 0 if all(r.passed for r in results) else 1


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(smoke(build_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
