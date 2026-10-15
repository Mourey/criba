"""Command line for the model comparison (spec 004 tasks 9 and 10).

From the repo root, against a sim stack (stub model, free)::

    export PYTHONPATH=src/tests CRIBA_E2E_SEED_PASSWORD_FILE=secrets/criba_seed_password.txt
    uv run --project src/backend python -m sim.compare run --base-url http://localhost:18106 \\
        --dataset stub-proof --label stub-as-haiku --out /tmp/compare.jsonl
    uv run --project src/backend python -m sim.compare run --base-url http://localhost:18106 \\
        --dataset stub-proof --label stub-as-sonnet --out /tmp/compare.jsonl --append
    uv run --project src/backend python -m sim.compare import /tmp/compare.jsonl \\
        --compose-project criba-w3-3 --base-url http://localhost:18106
    uv run --project src/backend python -m sim.compare report /tmp/compare.jsonl \\
        --out /tmp/model-comparison.md

A real model: ``run --real --model anthropic/claude-haiku-5.5`` against a stack that serves it,
with ``CRIBA_REAL_SPEND_CAP_USD`` set (default 0 refuses).

``spend`` prints the ledger total; ``spend --reconcile <usd> --reason <text>`` replaces an
over-count with the real OpenRouter bill (a correction entry in the ledger, logged and printed).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from criba.summary import DEFAULT_SUMMARY_MODEL as SUMMARY_MODEL
from sim.compare.dataset import markdown, read_jsonl, write_jsonl
from sim.compare.import_dataset import import_compose, import_file
from sim.compare.runner import ComparisonRunner
from sim.personas.loader import load_personas
from sim.spend import (
    LEDGER_ENV,
    SpendGuard,
    SpendRefused,
    cap_from_env,
    read_ledger,
    reconcile_ledger,
)
from sim.stack import (
    is_sim_stack,
    login,
    seed_password,
    settled_application,
    usage_of,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m sim.compare", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="play the persona set against a stack, write JSONL")
    run.add_argument("--base-url", required=True)
    run.add_argument("--dataset", required=True, help="the dataset name the lines carry")
    run.add_argument("--label", help="the model label (default: --model, else stub-model)")
    run.add_argument("--model", help="the turn model the stack serves (required with --real)")
    run.add_argument("--personas", help="comma list of persona ids (default: all)")
    run.add_argument("--reps", type=int, default=1)
    run.add_argument("--out", type=Path, required=True, help="the JSONL file")
    run.add_argument("--append", action="store_true", help="add to --out instead of replacing it")
    run.add_argument("--real", action="store_true", help="allow a real model (needs a spend cap)")
    run.add_argument("--reply-timeout", type=float, default=60.0)

    imp = sub.add_parser("import", help="load a dataset into the usage ledger as replay rows")
    imp.add_argument("files", type=Path, nargs="+")
    where = imp.add_mutually_exclusive_group(required=True)
    where.add_argument("--compose-project", help="the running stack's Compose project")
    where.add_argument("--db", type=Path, help="a SQLite file with the schema")
    imp.add_argument("--base-url", help="check GET /api/metrics/comparison afterwards")

    summ = sub.add_parser(
        "summary",
        help="one real summary: play one persona on a real stack, read the Sonnet prose back "
        "(always a real-model run: needs the spend cap)",
    )
    summ.add_argument("--base-url", required=True)
    summ.add_argument(
        "--model",
        default="anthropic/claude-haiku-5.5",
        help="the turn model the stack serves (default: Haiku 5.5, ADR 0025)",
    )
    summ.add_argument("--persona", default="qualified_es")
    summ.add_argument("--reply-timeout", type=float, default=60.0)

    spend = sub.add_parser(
        "spend",
        help="the real-model spend recorded in CRIBA_REAL_SPEND_LEDGER, against the cap; "
        "--reconcile replaces it with the real billed figure",
    )
    spend.add_argument(
        "--reconcile",
        type=float,
        metavar="USD",
        help="the real billed figure (OpenRouter): add a correction entry so the ledger total "
        "becomes USD (never below 0); needs --reason and the ledger",
    )
    spend.add_argument("--reason", help="why the ledger is corrected (required with --reconcile)")

    rep = sub.add_parser("report", help="markdown summary of one or more dataset files")
    rep.add_argument("files", type=Path, nargs="+")
    rep.add_argument("--out", type=Path)
    rep.add_argument("--title", default="Model comparison")
    return p


def _err(message: str) -> None:
    print(message, file=sys.stderr)


async def cmd_run(args: argparse.Namespace) -> int:
    try:
        guard = SpendGuard.from_env(
            real=args.real, label=f"compare:{args.dataset}:{args.label or args.model}"
        )
    except SpendRefused as exc:
        _err(f"refused: {exc}")
        _err("real-model spend: $0.0000 (nothing was called)")
        return 2
    try:
        return await _run(args, guard)
    finally:
        guard.save()
        _err(guard.summary())


async def _run(args: argparse.Namespace, guard: SpendGuard) -> int:
    personas = load_personas()
    chosen = (
        [personas[p.strip()] for p in args.personas.split(",")]
        if args.personas
        else list(personas.values())
    )
    async with (
        httpx.AsyncClient(base_url=args.base_url, timeout=30) as http,
        httpx.AsyncClient(base_url=args.base_url, timeout=30) as operator,
    ):
        sim = await is_sim_stack(http)
        if not sim and not args.real:
            _err("refused: not a sim stack (no /api/sim/clock); a real stack needs --real")
            return 2
        await login(operator, seed_password())

        async def enrich(phone: str) -> dict[str, Any]:
            return await settled_application(operator, phone, wait_s=10.0)

        try:
            runner = ComparisonRunner(
                http,
                enrich,
                dataset=args.dataset,
                label=args.label,
                guard=guard,
                expected_model=args.model,
                clock=sim,
                reply_timeout_s=args.reply_timeout,
                progress=lambda msg: _err(f"  {msg}"),
            )
            records, stopped = await runner.run(chosen, reps=args.reps)
        except SpendRefused as exc:
            _err(f"refused: {exc}")
            return 3
    n = write_jsonl(args.out, records, append=args.append)
    _err(f"wrote {n} lines to {args.out}")
    if stopped:
        _err(f"stopped early: {stopped}")
        return 4
    return 0


async def cmd_summary(args: argparse.Namespace) -> int:
    """Spec 001 criterion 5: the summary's prose comes from Sonnet 5.5 (ADR 0025), shown once."""
    try:
        guard = SpendGuard.from_env(real=True, label=f"summary:{args.persona}")
    except SpendRefused as exc:
        _err(f"refused: {exc}")
        _err("real-model spend: $0.0000 (nothing was called)")
        return 2
    try:
        return await _summary(args, guard)
    finally:
        guard.save()
        _err(guard.summary())


async def _summary(args: argparse.Namespace, guard: SpendGuard) -> int:
    persona = load_personas()[args.persona]
    seen: dict[str, Any] = {}
    async with (
        httpx.AsyncClient(base_url=args.base_url, timeout=30) as http,
        httpx.AsyncClient(base_url=args.base_url, timeout=30) as operator,
    ):
        if await is_sim_stack(http):
            _err("refused: a sim stack serves the stub and a deterministic summary")
            return 2
        await login(operator, seed_password())

        async def enrich(phone: str) -> dict[str, Any]:
            seen.update(await settled_application(operator, phone, wait_s=60.0))
            return seen

        try:
            runner = ComparisonRunner(
                http,
                enrich,
                dataset="summary",
                guard=guard,
                expected_model=args.model,
                clock=False,
                reply_timeout_s=args.reply_timeout,
                progress=lambda msg: _err(f"  {msg}"),
            )
            if not runner.usable([persona]):
                _err(f"refused: {persona.id} moves the sim clock; pick another persona")
                return 2
            records, stopped = await runner.run([persona])
        except SpendRefused as exc:
            _err(f"refused: {exc}")
            return 3
    if stopped or not records:
        _err(f"stopped before the persona ran: {stopped}")
        return 4
    prose = ((seen.get("summary") or {}).get("prose")) or ""
    models = usage_of(seen)["models"]
    _err(f"models served: {', '.join(models)}")
    if SUMMARY_MODEL not in models or not prose.strip():
        _err(
            f"no summary prose from {SUMMARY_MODEL}: prose {'present' if prose else 'null'}, "
            f"models {models}"
        )
        return 6
    _err(f"summary prose by {SUMMARY_MODEL}:")
    print(prose.strip())
    return 0


async def cmd_import(args: argparse.Namespace) -> int:
    records = [r for f in args.files for r in read_jsonl(f)]
    if args.db:
        written = import_file(args.db, records)
    else:
        written = import_compose(args.compose_project, records)
    _err(f"wrote {written} replay rows")
    if args.base_url:
        async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as operator:
            await login(operator, seed_password())
            for name in sorted({r.dataset for r in records}):
                response = await operator.get("/api/metrics/comparison", params={"dataset": name})
                if response.status_code != 200:
                    _err(f"comparison for {name}: {response.status_code} {response.text}")
                    return 5
                body = response.json()
                models = ", ".join(f"{m['model']} ({m['runs']} runs)" for m in body["models"])
                _err(f"dashboard replay {name}: {body['personas']} screenings; {models}")
    return 0


def cmd_spend(args: argparse.Namespace) -> int:
    ledger = os.environ.get(LEDGER_ENV)
    try:
        cap = cap_from_env()
        if args.reconcile is not None:
            if not ledger:
                raise SpendRefused(f"--reconcile needs {LEDGER_ENV} (the ledger to correct)")
            entry = reconcile_ledger(Path(ledger), args.reconcile, args.reason or "")
            _err(
                f"ledger reconciled: was ${entry['total_before_usd']:.4f}, now "
                f"${entry['billed_usd']:.4f} (adjustment {entry['spent_usd']:+.4f}): "
                f"{entry['reason']}"
            )
        elif args.reason is not None:
            raise SpendRefused("--reason goes with --reconcile")
    except SpendRefused as exc:
        _err(f"refused: {exc}")
        return 2
    spent = read_ledger(Path(ledger) if ledger else None)
    where = ledger or f"no {LEDGER_ENV} set"
    _err(f"real-model spend recorded in the ledger ({where}): ${spent:.4f} of the ${cap:.2f} cap")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    records = [r for f in args.files for r in read_jsonl(f)]
    text = markdown(records, title=args.title)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return asyncio.run(cmd_run(args))
    if args.command == "import":
        return asyncio.run(cmd_import(args))
    if args.command == "summary":
        return asyncio.run(cmd_summary(args))
    if args.command == "spend":
        return cmd_spend(args)
    return cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
