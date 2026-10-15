"""``python -m jev_calibration collect | show | report``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from criba.jev.battery import SIGNALS
from criba.jev.client import OpenRouterJev
from criba.jev.config import DEFAULT_MODEL
from criba.jev.policy import THRESHOLDS
from jev_calibration import collect as collecting
from jev_calibration.analyze import (
    SWEEP,
    ask_confusion_at,
    ask_values,
    choice_accuracy,
    confusion_at,
    cost_stats,
    language_score,
    latency_stats,
    merge,
    noul_values,
    tone_score,
    unreadable_score,
)
from jev_calibration.fixtures import load_fixtures
from sim.spend import SpendGuard, SpendRefused

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_KEY_FILE = REPO_ROOT / "secrets" / "openrouter_api_key.txt"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m jev_calibration")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect", help="call real Jev for every fixture and save the answers")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--repeats", type=int, default=2)
    c.add_argument("--workers", type=int, default=4)
    c.add_argument("--model", default=DEFAULT_MODEL)
    c.add_argument("--timeout", type=float, default=5.0, help="seconds; long, to measure latency")
    c.add_argument("--label", default="jev-calibration")
    c.add_argument(
        "--splits",
        default="dev",
        help="comma list: dev, holdout. Run holdout once, after the thresholds are chosen",
    )
    s = sub.add_parser("show", help="print the sweeps and the decisions for saved answers")
    s.add_argument("results", type=Path)
    r = sub.add_parser("report", help="write the Markdown calibration report")
    r.add_argument("results", type=Path)
    r.add_argument("--out", type=Path)
    r.add_argument("--note", default="")
    return p


def key_file() -> Path:
    return Path(os.environ.get("CRIBA_OPENROUTER_API_KEY_FILE") or DEFAULT_KEY_FILE)


def cmd_collect(args: argparse.Namespace) -> int:
    try:
        guard = SpendGuard.from_env(real=True, label=args.label)
    except SpendRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    key = key_file().read_text(encoding="utf-8").strip()
    client = OpenRouterJev(api_key=key, model=args.model, timeout_s=args.timeout)
    wanted = set(args.splits.split(","))
    fixtures = [f for f in load_fixtures() if f.split in wanted]
    rows = collecting.collect(
        client, fixtures, repeats=args.repeats, workers=args.workers, guard=guard
    )
    args.out.write_text(collecting.dump(rows), encoding="utf-8")
    guard.save()
    ok = sum(1 for r in rows if r.ok)
    print(f"{len(rows)} calls, {ok} ok, wrote {args.out}")
    print(guard.summary())
    return 0 if ok == len(rows) else 1


def cmd_show(args: argparse.Namespace) -> int:
    rows = collecting.load(args.results.read_text(encoding="utf-8"))
    fixtures = load_fixtures()
    seen = {r.fixture_id for r in rows}
    fixtures = [f for f in fixtures if f.id in seen]
    merged = merge(rows, fixtures)
    print("latency", latency_stats(rows), "cost", cost_stats(rows))
    for split in sorted({f.split for f in fixtures}):
        acc = choice_accuracy(merged, fixtures, split)
        print(f"\n== {split}: lang argmax {acc['right']}/{acc['total']} wrong {acc['wrong']}")
        for t, kept, ok in acc["by_conf"]:
            print(f"   conf>={t}: {ok}/{kept}")
        for name in ("switch", *SIGNALS, "unreadable"):
            values = noul_values(merged, fixtures, name, split)
            line = []
            for t in SWEEP:
                c = confusion_at(values, t)
                line.append(f"{t}:{c.tp}/{c.fp}/{c.fn}")
            print(f"   {name:12} tp/fp/fn  " + "  ".join(line))
        asked = ask_values(merged, fixtures, split)
        line = []
        for t in SWEEP:
            c = ask_confusion_at(asked, t)
            line.append(f"{t}:{c.tp}/{c.fp}/{c.fn}")
        print(f"   {'asks':12} tp/fp/fn  " + "  ".join(line))
        score = language_score(merged, fixtures, split, THRESHOLDS)
        print(
            f"   language decisions {score.correct}/{score.cases} false_switch={score.false_switch}"
            f" missed={score.missed_switch} wrong={score.wrong_ids}"
        )
        for name, conf in tone_score(merged, fixtures, split, THRESHOLDS).items():
            print(f"   tone {name:22} tp={conf.tp} fp={conf.fp} fn={conf.fn} tn={conf.tn}")
        u = unreadable_score(merged, fixtures, split, THRESHOLDS)
        print(f"   unreadable (policy)         tp={u.tp} fp={u.fp} fn={u.fn} tn={u.tn}")
    print("\nper message (dev + holdout):")
    for f in fixtures:
        got = merged[f.id].reading
        if got is None:
            print(f"  {f.id}: FAILED")
            continue
        sig = " ".join(f"{k[:4]}={v:.2f}" for k, v in got.signals.items())
        print(
            f"  {f.id:24} [{f.split[:3]}] want {f.lang:7} got {got.lang:7}"
            f" c={got.lang_confidence:.2f} sw={got.switch:.2f}({'Y' if f.switch else 'n'}) {sig}"
            f" unr={got.unreadable:.2f}({'Y' if f.unreadable else 'n'})"
            f"  want={sorted(f.signals)} asks={f.asks}/{got.asks}:{got.asks_confidence:.2f}"
        )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from jev_calibration.report import render

    rows = collecting.load(args.results.read_text(encoding="utf-8"))
    text = render(rows, load_fixtures(), note=args.note)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return {"collect": cmd_collect, "show": cmd_show, "report": cmd_report}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
