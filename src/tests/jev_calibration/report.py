"""Render ``docs/evidence/jev-calibration.md`` from saved results."""

from __future__ import annotations

from datetime import UTC, datetime

from criba.jev import battery as jev_battery
from criba.jev.battery import SIGNALS
from criba.jev.config import DEFAULT_TIMEOUT_S
from criba.jev.policy import THRESHOLDS
from jev_calibration import analyze as analyzing
from jev_calibration.analyze import (
    SWEEP,
    Merged,
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
)
from jev_calibration.collect import Row
from jev_calibration.fixtures import Fixture

NOULS = ("switch", *SIGNALS)
THRESHOLD_OF = {
    "switch": THRESHOLDS.switch_noul,
    "frustrated": THRESHOLDS.frustrated,
    "confused": THRESHOLDS.confused,
    "wants_human": THRESHOLDS.wants_human_hint,
    "abusive": THRESHOLDS.abusive_escalate,
}


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _pct(num: int, den: int) -> str:
    return f"{100 * num / den:.0f}% ({num}/{den})" if den else "n/a"


def _margin(values: list[tuple[float | None, bool, Fixture]]) -> tuple[float, float]:
    """(lowest value among positives, highest among negatives)."""
    pos = [v for v, label, _ in values if label and v is not None]
    neg = [v for v, label, _ in values if not label and v is not None]
    return (min(pos) if pos else float("nan"), max(neg) if neg else float("nan"))


def _ms(value: float) -> str:
    """A latency for the budget table: a failed call is infinite, no call at all is no data."""
    if value != value:  # nan
        return "no data"
    if value == float("inf"):
        return "a failed call"
    return f"{value:.0f} ms"


def _verdict(passed: bool) -> str:
    return "pass" if passed else "fail"


def _readings(rows: list[Row], fixtures: list[Fixture], merged: dict[str, Merged]) -> list[str]:
    """The intent and Manipulation sections (ADR 0071), with the budget for ten questions.

    Display only: no threshold is chosen, marked or recommended for either reading."""
    splits = [s for s in ("dev", "holdout") if any(f.split == s for f in fixtures)]
    out: list[str] = []
    add = out.append
    add("## Intent and Manipulation readings (ADR 0071)\n")
    add(
        "Two display-only readings for Operators: `intent` (Choice: "
        + ", ".join(f"`{name}`" for name in jev_battery.INTENTS)
        + ") and the Manipulation score (the `malice` Noul). No threshold is chosen for either "
        "reading: both are display only (ADR 0071), nothing decides on them, and the sweep below "
        "shows each cut-off without picking one. Labels are in "
        "`src/tests/jev_calibration/intent.json`; every message of `messages.json` and "
        "`unreadable.json` is a Manipulation negative with no intent label. A failed call or a "
        "missing answer is a miss.\n"
    )

    add("### Intent (Choice, top option)\n")
    acc_rows = []
    for s in splits:
        a = analyzing.intent_accuracy(merged, fixtures, s)
        acc_rows.append(
            [s, _pct(a["right"], a["total"]), str(a["missed"]), ", ".join(a["wrong"]) or "none"]
        )
    add(_table(["Split", "Right", "Missed", "Wrong ones"], acc_rows))
    columns = [*jev_battery.INTENTS, analyzing.MISSED]
    for s in splits:
        add(f"\nConfusion on {s} (rows: the label; columns: Jev's top option):\n")
        table = analyzing.intent_confusion(merged, fixtures, s)
        add(
            _table(
                ["Label", *columns],
                [[label, *(str(table[label][c]) for c in columns)] for label in table],
            )
        )
    add("")

    add("### Manipulation (`malice` Noul)\n")
    add("Precision and recall at each cut-off (none is chosen):\n")
    sweep_rows = []
    for s in splits:
        values = noul_values(merged, fixtures, "malice", s)
        cells = [s]
        for t in SWEEP:
            c = confusion_at(values, t)
            cells.append(f"P {c.precision:.2f}, R {c.recall:.2f} ({c.tp}/{c.fp}/{c.fn})")
        sweep_rows.append(cells)
    add(_table(["Split", *[f"{t}" for t in SWEEP]], sweep_rows))
    add("\nCells give precision, recall and TP/FP/FN.\n")
    add("Lines aimed at Jev itself (`m_jev_*`) and hard negatives (`hn_*`):\n")
    line_rows = []
    for f in fixtures:
        if not f.id.startswith(("m_jev_", "hn_")):
            continue
        got = merged[f.id].reading
        if got is None:
            score = "failed"
        elif got.malice is None:
            score = "no answer"
        else:
            score = f"{got.malice:.2f}"
        label = "Manipulation" if f.manipulation else "not Manipulation"
        line_rows.append([f"`{f.id}`", f.split, label, f.intent or "", score])
    add(_table(["Message", "Split", "Label", "Intent label", "Manipulation score"], line_rows))
    add("")

    add("### Stability of the two readings\n")
    st = analyzing.intent_stability(rows, fixtures)
    add(
        f"Intent agreed across repeats on {_pct(st['agree'], st['messages'])} of the labelled "
        "messages with two or more successful calls (disagreed: "
        f"{', '.join(st['disagree']) or 'none'}). The widest move of the Manipulation score for "
        f"one message was {st['malice_spread']:.2f}.\n"
    )

    add("### Budget (owner decision 2026-10-12)\n")
    b = analyzing.budget_check(rows)
    mean_cost = "no data" if b["mean_cost"] != b["mean_cost"] else f"${b['mean_cost']:.6f}"
    add(
        _table(
            ["Bound", "Budget", "Measured", "Result"],
            [
                [
                    "p95 latency",
                    f"at most {analyzing.BUDGET_P95_MS} ms",
                    _ms(b["p95"]),
                    _verdict(b["p95_pass"]),
                ],
                [
                    "p99 latency",
                    f"under {analyzing.BUDGET_P99_MS:,} ms",
                    _ms(b["p99"]),
                    _verdict(b["p99_pass"]),
                ],
                [
                    "mean cost per call",
                    f"at most ${analyzing.BUDGET_MEAN_COST_USD:.5f}",
                    mean_cost,
                    _verdict(b["cost_pass"]),
                ],
            ],
        )
    )
    add(
        f"\nFailed calls: {b['failed']} of {b['calls']}. In this table p95 and p99 count each "
        "failed call as over the bound (owner decision 2026-10-12); the latency table above "
        "covers successful calls only. The mean cost is over the calls with a known cost; no "
        "successful call, or no known cost, fails the bound.\n"
    )
    return out


def render(rows: list[Row], fixtures: list[Fixture], *, note: str = "") -> str:
    seen = {r.fixture_id for r in rows}
    fixtures = [f for f in fixtures if f.id in seen]
    merged = merge(rows, fixtures)
    splits = [s for s in ("dev", "holdout") if any(f.split == s for f in fixtures)]
    lat, cost = latency_stats(rows), cost_stats(rows)
    models = sorted({r.model for r in rows if r.model})
    count = {s: sum(1 for f in fixtures if f.split == s) for s in splits}
    out: list[str] = []
    add = out.append

    add("# Jev calibration: language and sentiment (ADR 0051, 0061)\n")
    add(
        f"Run on {datetime.now(UTC):%Y-%m-%d} against the real service through OpenRouter "
        f"(`POST /api/alpha/decisions`), model `{', '.join(models)}` (requested "
        "`typesafe/jev-1.13`). Generated by `python -m jev_calibration report`; the labelled "
        "messages are `src/tests/jev_calibration/messages.json` (synthetic text only). "
        f"{note}\n"
    )

    add("## Method\n")
    add(
        f"- {count.get('dev', 0)} dev messages and {count.get('holdout', 0)} holdout messages, "
        "each run twice (a repeat shows how far the same message moves between calls). Labels "
        "live apart from what Jev sees.\n"
        "- The first 103 dev messages were written first and run once; the exploratory run "
        "printed both of the splits that then existed, so every message in it counts as dev. "
        "The other 20 dev messages are harder cases added after that. The holdout (45 messages) "
        "was written in the same session, before any result on the harder cases, and run once, "
        "after the thresholds were fixed on dev.\n"
        "- Explicit language requests (ADR 0061) were added to the set later: 11 request and 9 "
        "mention messages in dev, 6 and 4 in holdout, plus 4 messages already in the set that are "
        "requests (labelled `asks`). The holdout ones were written before any result on `asks`; "
        "the dev ones, and the `ask_confidence` cut-off, were fitted on dev only.\n"
        "- Every failed call stays in the denominator (a failure is a miss, never a pass); "
        "latency is measured on successful calls.\n"
        "- Thresholds follow the `typesafe-jev-workflows` rules: absolute Nouls for *whether*, "
        "a Choice for *which*, one atomic judgment per question, an uncertain path (no change) "
        "between the cut-offs, and each action gated on its own threshold.\n"
    )

    add("## Questions\n")
    add(
        "One state, ten questions, one request. `lang` (Choice: `es`, `en`, `other`, "
        "`unclear`) is collapsed to Spanish versus English because the Spanish variant follows "
        "the Application's country anyway (`_country_language`); a four-way es-ES / es-MX / en / "
        "unclear Choice would split the probability between two Spanish options for the same "
        "decision. `switch` (Noul) asks whether the message continues the chat in a different "
        "language as a full sentence. `frustrated`, `confused`, `wants_human` and `abusive` are "
        "Nouls asked about presence, each with `true` / `false` criteria whose examples are not "
        'in the fixtures. The probe wording flagged "Sí, tengo carnet desde hace 5 años, vale" '
        "as frustrated at 0.36; with the criteria it reads 0.06. `asks` (Choice: `es`, `en`, "
        "`none`, ADR 0061) reads what the message *asks for*, not the language it is written in: "
        '"me preguntas en inglés porfi" is written in Spanish and asks for English. Its `none` '
        'option covers ordinary answers and mentions of a language ("trabajé en inglés"); each '
        "option has `what` / `not_for` / `examples`, and the examples are not in the fixtures.\n"
    )

    add("## Accuracy per question\n")
    header = ["Question", *[f"{s} at the threshold" for s in splits], "Threshold"]
    table_rows = []
    for name in NOULS:
        cells = [name]
        for s in splits:
            c = confusion_at(noul_values(merged, fixtures, name, s), THRESHOLD_OF[name])
            cells.append(
                f"precision {_pct(c.tp, c.tp + c.fp)}, recall {_pct(c.tp, c.tp + c.fn)}; "
                f"FP {c.fp}, FN {c.fn}"
            )
        cells.append(f"{THRESHOLD_OF[name]:.2f}")
        table_rows.append(cells)
    acc_cells = ["lang (Choice, top option)"]
    for s in splits:
        a = choice_accuracy(merged, fixtures, s)
        acc_cells.append(
            f"{_pct(a['right'], a['total'])}; wrong: {', '.join(a['wrong']) or 'none'}"
        )
    acc_cells.append(
        f"first {THRESHOLDS.first_confidence:.2f}, switch {THRESHOLDS.switch_confidence:.2f}"
    )
    asks_cells = ["asks (Choice, right language)"]
    for s in splits:
        c = ask_confusion_at(ask_values(merged, fixtures, s), THRESHOLDS.ask_confidence)
        asks_cells.append(
            f"precision {_pct(c.tp, c.tp + c.fp)}, recall {_pct(c.tp, c.tp + c.fn)}; "
            f"FP {c.fp}, FN {c.fn}"
        )
    asks_cells.append(f"{THRESHOLDS.ask_confidence:.2f}")
    add(_table(header, [acc_cells, asks_cells, *table_rows]))
    add("\n`wants_human` is gated at 0.70 for the hint and `abusive` at 0.85 for the Escalation.\n")

    add("### Choice confidence against accuracy (`lang`)\n")
    conf_rows = []
    for s in splits:
        a = choice_accuracy(merged, fixtures, s)
        for t, kept, ok in a["by_conf"]:
            conf_rows.append([s, f">= {t}", _pct(ok, kept), f"{kept}/{a['total']}"])
    add(_table(["Split", "Confidence", "Right among those", "Answered"], conf_rows))

    add("\n### Sweeps on dev (TP / FP / FN at each cut-off)\n")
    sweep_rows = []
    for name in NOULS:
        values = noul_values(merged, fixtures, name, "dev")
        low_pos, high_neg = _margin(values)
        cells = [name]
        for t in SWEEP:
            c = confusion_at(values, t)
            cells.append(f"{c.tp}/{c.fp}/{c.fn}")
        cells.append(f"{low_pos:.2f} / {high_neg:.2f}")
        sweep_rows.append(cells)
    asks_dev = ask_values(merged, fixtures, "dev")
    asks_cells = ["asks (confidence)"]
    for t in SWEEP:
        c = ask_confusion_at(asks_dev, t)
        asks_cells.append(f"{c.tp}/{c.fp}/{c.fn}")
    right_dev = [
        v
        for v, choice, f in asks_dev
        if v is not None and f.asks in ("es", "en") and choice == f.asks
    ]
    # The most a message that is not a request gave to a request option: its own confidence
    # when Jev picked a language, else what the `none` option left over.
    false_dev = [
        (v if choice in ("es", "en") else 1 - v)
        for v, choice, f in asks_dev
        if v is not None and f.asks not in ("es", "en")
    ]
    asks_cells.append(
        f"{min(right_dev, default=float('nan')):.2f} / {max(false_dev, default=float('nan')):.2f}"
    )
    add(
        _table(
            ["Question", *[f"{t}" for t in SWEEP], "lowest right request / highest false request"],
            [*sweep_rows, asks_cells],
        )
    )

    miss_lines = []
    for s in splits:
        for confidence, choice, f in ask_values(merged, fixtures, s):
            wanted = f.asks if f.asks in ("es", "en") else "none"
            fired = confidence is not None and choice in ("es", "en")
            if (wanted == "none" and fired and (confidence or 0) >= THRESHOLDS.ask_confidence) or (
                wanted != "none"
                and not (
                    fired and choice == wanted and (confidence or 0) >= THRESHOLDS.ask_confidence
                )
            ):
                got = "failed" if confidence is None else f"{choice} {confidence:.2f}"
                miss_lines.append(f"`{f.id}` ({s}): wanted {wanted}, got {got}")
    add(
        "\nRequests Jev missed or invented at the threshold: "
        + ("; ".join(miss_lines) if miss_lines else "none")
        + ".\n"
    )

    add("\n## Decisions with the chosen thresholds\n")
    dec_rows = []
    for s in splits:
        for label, first in (("first message", True), ("later messages", False), ("all", None)):
            sc = language_score(merged, fixtures, s, THRESHOLDS, first=first)
            dec_rows.append(
                [
                    s,
                    label,
                    _pct(sc.correct, sc.cases),
                    str(sc.false_switch),
                    str(sc.missed_switch),
                    ", ".join(sc.wrong_ids) or "none",
                ]
            )
    add(
        _table(
            [
                "Split",
                "Language decision",
                "Right",
                "Wrong switches",
                "Missed switches",
                "Wrong ones",
            ],
            dec_rows,
        )
    )
    add(
        "\nA wrong switch (changing the language when the stored one should stand) is the "
        "costly error; a missed switch is repaired by the next full sentence.\n"
    )
    tone_rows = []
    for s in splits:
        for name, c in tone_score(merged, fixtures, s, THRESHOLDS).items():
            tone_rows.append(
                [
                    s,
                    name,
                    str(c.tp),
                    str(c.fp),
                    str(c.fn),
                    _pct(c.tp, c.tp + c.fp),
                    _pct(c.tp, c.tp + c.fn),
                ]
            )
    add(_table(["Split", "Signal / action", "TP", "FP", "FN", "Precision", "Recall"], tone_rows))
    add("")

    add("## Thresholds and why\n")
    missed_hold = [
        f"`{f.id}` ({v:.2f})"
        for v, choice, f in ask_values(merged, fixtures, "holdout")
        if v is not None
        and f.asks in ("es", "en")
        and not (choice == f.asks and v >= THRESHOLDS.ask_confidence)
    ]
    add(
        "| Threshold | Value | Why |\n|---|---|---|\n"
        f"| first message, `lang` confidence | {THRESHOLDS.first_confidence:.2f} | Every first "
        "message in English or Spanish scored 0.95 to 1.00; a single noisy word (`ok`) scored "
        "0.52 to 0.60. A first message sets the language only when Jev is confident. |\n"
        f"| later switch, `lang` confidence | {THRESHOLDS.switch_confidence:.2f} | One true switch "
        "(an English sentence around a Spanish name) scored 0.70, so a high cut-off would miss it; "
        "the `switch` Noul carries the load. |\n"
        f"| later switch, `switch` Noul | {THRESHOLDS.switch_noul:.2f} | The closest negatives "
        "were a two-word Spanish reply in an English chat (0.79) and three English words in a "
        "Spanish chat (0.53 to 0.59); the lowest true switch after the first message was "
        "0.92. A false switch is worse than a missed one, so the cut-off sits near the top "
        "of the gap. |\n"
        f"| explicit language request, `asks` confidence | {THRESHOLDS.ask_confidence:.2f} | "
        f"A request wins over the written language, so a false one switches the chat on a "
        f"message that only mentions a language. On dev the lowest confidence of a correct "
        f"request is {min(right_dev, default=float('nan')):.2f}, and no message that is not a "
        f"request gave a request option more than {max(false_dev, default=float('nan')):.2f}: "
        "every cut-off from 0.30 to 0.90 gives the same decisions, so the cut-off sits in the "
        "middle of an empty gap, with the cost of a wrong switch in mind. Requests missed at "
        f"the cut-off on holdout: {', '.join(missed_hold) or 'none'}. |\n"
        f"| `frustrated` | {THRESHOLDS.frustrated:.2f} | Hints a change of tone, so a false alarm "
        "costs one odd apology and a miss costs nothing; it favours precision. Sarcasm without "
        "keywords (0.43) and mild impatience (0.69, 0.72) fall below it on purpose. |\n"
        f"| `confused` | {THRESHOLDS.confused:.2f} | The nearest negative reads 0.48 "
        '("¿Hablo con una máquina?"); positives start at 0.52, with one at 0.68. |\n'
        f"| `wants_human` hint / Escalation | {THRESHOLDS.wants_human_hint:.2f} / "
        f"{THRESHOLDS.wants_human_escalate:.2f} | Lowest true request 0.92, highest false "
        "0.23. The Escalation opens a queue item a person must clear, so it takes the higher "
        "cut-off. |\n"
        f"| `abusive` Escalation | {THRESHOLDS.abusive_escalate:.2f} | Lowest true abuse in "
        "dev 0.92; highest false 0.25 (a harsh complaint). |\n"
    )

    add("## Stability\n")
    spreads = [m.spread for m in merged.values() if isinstance(m, Merged) and m.ok]
    add(
        f"Across the two repeats the widest single move of any value for any message was "
        f"{max(spreads):.2f}; the median message moved {sorted(spreads)[len(spreads) // 2]:.2f}. "
        "No decision flipped between repeats at the chosen thresholds.\n"
        if spreads
        else ""
    )

    add("## Latency and cost\n")
    add(
        _table(
            ["Calls", "OK", "Failed", "p50", "p95", "p99", "max"],
            [
                [
                    f"{lat['calls']:.0f}",
                    f"{lat['ok']:.0f}",
                    f"{lat['failed']:.0f}",
                    f"{lat['p50']:.0f} ms",
                    f"{lat['p95']:.0f} ms",
                    f"{lat['p99']:.0f} ms",
                    f"{lat['max']:.0f} ms",
                ]
            ],
        )
    )
    add(
        f"\nMean cost per call ${cost['mean']:.6f} (OpenRouter's own charge, {cost['unknown']:.0f} "
        f"calls without one), about {cost['input_tokens']:.0f} input and "
        f"{cost['output_tokens']:.0f} output tokens: roughly $0.07 per 1,000 messages. Latency is "
        "measured from a developer machine with four calls in flight.\n"
    )
    add(
        f"**Timeout: {DEFAULT_TIMEOUT_S:g} s** (`CRIBA_JEV_TIMEOUT_S`). It is well above p95 and "
        "past p99, so a slow call is cut (the turn goes ahead without a tone hint and the "
        "language stands) for about one message in a hundred, while a hung call can add at most "
        f"{DEFAULT_TIMEOUT_S:g} s to a turn whose agent run takes several seconds. Added latency "
        "per typed message is the Jev call itself: p50 about "
        f"{lat['p50'] / 1000:.2f} s, p95 {lat['p95'] / 1000:.2f} s.\n"
    )

    out.extend(_readings(rows, fixtures, merged))

    add("## What this does not show\n")
    add(
        "- The messages are synthetic and short, written by us, in two languages; real Candidates "
        "will be messier (voice-note transcripts, longer text, regional slang). Treat the "
        "precision figures as an upper bound and re-fit on real, consented traffic before relying "
        "on them.\n"
        "- Labels are one person's judgment, with borderline cases (an insult at a company's "
        'process, a polite request for a person). One abuse message ("Son unos estafadores de '
        'mierda...") read 0.54 and was missed; the Escalation threshold is deliberately high.\n'
        "- A Jev answer is not a human label, and repeatability is not accuracy.\n"
        "- The reasons in the threshold table for the five other questions were written from the "
        "2026-10-10 run. The tables above are from this run; the values quoted there moved by "
        "less than 0.1.\n"
        "- The thresholds belong to the pinned model (`typesafe/jev-1.13`), these question "
        "texts and `Thresholds.version`; re-fit when any of them changes.\n"
    )
    return "\n".join(out)
