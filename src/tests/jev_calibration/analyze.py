"""Score Jev's answers against the labels, and render the calibration report.

Rules from the ``typesafe-jev-workflows`` evaluation method: every failure stays in the
denominator, latency is measured on successes only, thresholds are fitted on ``dev`` and
``holdout`` is reported once, repeats show how far the same message moves between calls, and a
Jev answer is never a label (labels are in ``messages.json``).

The ``intent`` Choice and the Manipulation Noul (``malice``, ADR 0071) are display only: they are
scored (accuracy and a confusion table, a precision and recall sweep, repeat stability) and no
threshold is chosen for either. :func:`budget_check` compares latency and cost with the owner's
budget for the ten-question request, counting every failed call as over the latency bounds.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from criba.jev.battery import INTENTS, SIGNALS, Reading
from criba.jev.policy import (
    THRESHOLDS,
    Thresholds,
    decide_language,
    decide_tone,
    family,
    is_unreadable,
)
from jev_calibration.collect import Row
from jev_calibration.fixtures import Fixture

SWEEP = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95)
MISSED = "missed"  # the confusion column of a failed call or a missing intent answer

# The owner's budget for one ten-question request (owner decision 2026-10-12, ADR 0071).
BUDGET_P95_MS = 600  # at most
BUDGET_P99_MS = 1200  # strictly under
BUDGET_MEAN_COST_USD = 0.00015  # at most, per call


@dataclass
class Merged:
    """The repeats of one message, averaged. ``spread`` is the widest max-min of any value."""

    reading: Reading | None
    calls: int
    ok: int
    spread: float


def merge(rows: list[Row], fixtures: list[Fixture]) -> dict[str, Merged]:
    by_fixture: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        by_fixture[row.fixture_id].append(row)
    merged: dict[str, Merged] = {}
    for fixture in fixtures:
        calls = by_fixture.get(fixture.id, [])
        good = [r for r in calls if r.ok]
        if not good:
            merged[fixture.id] = Merged(None, len(calls), 0, 0.0)
            continue
        votes = [r.lang for r in good]
        lang = max(set(votes), key=votes.count)
        same = [r for r in good if r.lang == lang]
        asked_votes = [r.asks or "none" for r in good]
        asks = max(sorted(set(asked_votes)), key=asked_votes.count)
        asked = [r for r in good if (r.asks or "none") == asks]

        def mean(values: list[float]) -> float:
            return round(statistics.fmean(values), 4)

        series = {
            "lang_confidence": [r.lang_confidence or 0.0 for r in same],
            "asks_confidence": [r.asks_confidence or 0.0 for r in asked],
            "switch": [r.switch or 0.0 for r in good],
            **{name: [r.signals.get(name, 0.0) for r in good] for name in SIGNALS},
            "unreadable": [r.unreadable or 0.0 for r in good],
        }
        spread = max(max(v) - min(v) for v in series.values())
        # The display-only readings, apart from ``series`` so ``spread`` keeps its inputs.
        intent_votes = [r.intent for r in good if r.intent is not None]
        intent = max(sorted(set(intent_votes)), key=intent_votes.count) if intent_votes else None
        intent_confidences = [
            r.intent_confidence
            for r in good
            if r.intent == intent and intent is not None and r.intent_confidence is not None
        ]
        malices = [r.malice for r in good if r.malice is not None]
        merged[fixture.id] = Merged(
            Reading(
                lang=lang,
                lang_confidence=mean(series["lang_confidence"]),
                switch=mean(series["switch"]),
                signals={name: mean(series[name]) for name in SIGNALS},
                asks=asks,
                asks_confidence=mean(series["asks_confidence"]),
                unreadable=mean(series["unreadable"]),
                intent=intent,
                intent_confidence=mean(intent_confidences) if intent_confidences else None,
                malice=mean(malices) if malices else None,
            ),
            len(calls),
            len(good),
            round(spread, 3),
        )
    return merged


# --------------------------------------------------------------------------- Nouls


@dataclass
class Confusion:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0


def noul_values(
    merged: dict[str, Merged], fixtures: list[Fixture], name: str, split: str
) -> list[tuple[float | None, bool, Fixture]]:
    """(value, label, fixture) for one Noul. A failed call has value None (counted as a miss)."""
    out: list[tuple[float | None, bool, Fixture]] = []
    for fixture in fixtures:
        if fixture.split != split:
            continue
        got = merged[fixture.id].reading
        if name == "switch":
            if fixture.switch is None:
                continue
            label = bool(fixture.switch)
            value = None if got is None else got.switch
        elif name == "unreadable":
            label = fixture.unreadable
            value = None if got is None else got.unreadable
        elif name == "malice":  # Manipulation: a missing answer is a miss, like a failure
            label = fixture.manipulation
            value = None if got is None else got.malice
        else:
            label = name in fixture.signals
            value = None if got is None else got.signals[name]
        out.append((value, label, fixture))
    return out


def confusion_at(values: list[tuple[float | None, bool, Fixture]], threshold: float) -> Confusion:
    c = Confusion()
    for value, label, _ in values:
        fired = value is not None and value >= threshold
        if label and fired:
            c.tp += 1
        elif label:
            c.fn += 1
        elif fired:
            c.fp += 1
        else:
            c.tn += 1
    return c


def unreadable_score(
    merged: dict[str, Merged], fixtures: list[Fixture], split: str, thresholds: Thresholds
) -> Confusion:
    """How the policy's unreadable decision (the Noul's threshold, and the no-letters rule in
    code) did against the labels. A failed call counts as readable: it never acts."""
    c = Confusion()
    for fixture in fixtures:
        if fixture.split != split:
            continue
        got = merged[fixture.id].reading
        fired = got is not None and is_unreadable(fixture.text, got, thresholds)
        _add(c, fixture.unreadable, fired)
    return c


# --------------------------------------------------------------------------- language


@dataclass
class LanguageScore:
    cases: int = 0
    correct: int = 0
    false_switch: int = 0  # changed the language when it should have stayed
    missed_switch: int = 0  # kept the language when it should have changed
    wrong_ids: tuple[str, ...] = ()


def expected_family(fixture: Fixture) -> str:
    """The language family the Application should have after this message."""
    stored = family(fixture.current_language)
    if fixture.asks in ("es", "en"):
        return fixture.asks  # an explicit request wins over the written language (ADR 0061)
    if fixture.lang not in ("es", "en"):
        return stored
    if fixture.first:
        return fixture.lang
    if fixture.switch:
        return fixture.lang
    return stored


def language_score(
    merged: dict[str, Merged],
    fixtures: list[Fixture],
    split: str,
    thresholds: Thresholds,
    *,
    first: bool | None = None,
) -> LanguageScore:
    score = LanguageScore()
    wrong: list[str] = []
    for fixture in fixtures:
        if fixture.split != split or (first is not None and fixture.first != first):
            continue
        got = merged[fixture.id].reading
        stored = family(fixture.current_language)
        after = stored
        if got is not None:
            decision = decide_language(
                fixture.current_language, fixture.country, got, first=fixture.first,
                thresholds=thresholds,
            )  # fmt: skip
            after = family(decision.target) if decision.target else stored
        want = expected_family(fixture)
        score.cases += 1
        if after == want:
            score.correct += 1
            continue
        wrong.append(fixture.id)
        if after != stored:
            score.false_switch += 1
        else:
            score.missed_switch += 1
    score.wrong_ids = tuple(wrong)
    return score


def choice_accuracy(
    merged: dict[str, Merged], fixtures: list[Fixture], split: str
) -> dict[str, Any]:
    """Argmax accuracy of the ``lang`` Choice, and accuracy among answers at a confidence."""
    pairs = [
        (merged[f.id].reading, f)
        for f in fixtures
        if f.split == split and merged[f.id].reading is not None
    ]
    total = sum(1 for f in fixtures if f.split == split)
    right = sum(1 for r, f in pairs if r is not None and r.lang == f.lang)
    by_conf = []
    for t in (0.5, 0.6, 0.7, 0.8, 0.9):
        kept = [(r, f) for r, f in pairs if r is not None and r.lang_confidence >= t]
        ok = sum(1 for r, f in kept if r is not None and r.lang == f.lang)
        by_conf.append((t, len(kept), ok))
    wrong = [f.id for r, f in pairs if r is not None and r.lang != f.lang]
    return {
        "total": total,
        "answered": len(pairs),
        "right": right,
        "by_conf": by_conf,
        "wrong": wrong,
    }


# --------------------------------------------------------------------------- intent


def intent_accuracy(
    merged: dict[str, Merged], fixtures: list[Fixture], split: str
) -> dict[str, Any]:
    """Top-option accuracy of the ``intent`` Choice over the intent-labelled messages of a
    split. A failed call or a missing answer is a miss and stays in the denominator."""
    total = right = missed = 0
    wrong: list[str] = []
    for fixture in fixtures:
        if fixture.split != split or fixture.intent is None:
            continue
        total += 1
        got = merged[fixture.id].reading
        answer = None if got is None else got.intent
        if answer == fixture.intent:
            right += 1
            continue
        wrong.append(fixture.id)
        if answer is None:
            missed += 1
    return {"total": total, "right": right, "missed": missed, "wrong": wrong}


def intent_confusion(
    merged: dict[str, Merged], fixtures: list[Fixture], split: str
) -> dict[str, dict[str, int]]:
    """Label -> answer -> count over the intent-labelled messages of a split, with a
    :data:`MISSED` column for a failed call or a missing answer."""
    table = {label: dict.fromkeys((*INTENTS, MISSED), 0) for label in INTENTS}
    for fixture in fixtures:
        if fixture.split != split or fixture.intent is None:
            continue
        got = merged[fixture.id].reading
        answer = got.intent if got is not None and got.intent is not None else MISSED
        table[fixture.intent][answer] += 1
    return table


def intent_stability(rows: list[Row], fixtures: list[Fixture]) -> dict[str, Any]:
    """How the two display-only readings move between repeats of the same message.

    ``messages`` counts the intent-labelled messages with at least two successful calls,
    ``agree`` those whose calls all gave the same intent (a missing answer is an answer of its
    own), ``disagree`` lists the others, and ``malice_spread`` is the widest max-min of the
    Manipulation score over any message's successful calls."""
    by_fixture: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        if row.ok:
            by_fixture[row.fixture_id].append(row)
    messages = agree = 0
    disagree: list[str] = []
    malice_spread = 0.0
    for fixture in fixtures:
        good = by_fixture.get(fixture.id, [])
        malices = [r.malice for r in good if r.malice is not None]
        if len(malices) >= 2:
            malice_spread = max(malice_spread, max(malices) - min(malices))
        if fixture.intent is None or len(good) < 2:
            continue
        messages += 1
        if len({r.intent for r in good}) == 1:
            agree += 1
        else:
            disagree.append(fixture.id)
    return {
        "messages": messages,
        "agree": agree,
        "share": agree / messages if messages else float("nan"),
        "disagree": disagree,
        "malice_spread": round(malice_spread, 3),
    }


# --------------------------------------------------------------------------- requests


def ask_values(
    merged: dict[str, Merged], fixtures: list[Fixture], split: str
) -> list[tuple[float | None, str, Fixture]]:
    """(confidence of the asked language or None, the language Jev chose, fixture) per message.

    A failed call has no reading: value None and choice ``failed`` (a miss on a request, never a
    false alarm)."""
    out: list[tuple[float | None, str, Fixture]] = []
    for fixture in fixtures:
        if fixture.split != split:
            continue
        got = merged[fixture.id].reading
        if got is None:
            out.append((None, "failed", fixture))
        else:
            out.append((got.asks_confidence, got.asks, fixture))
    return out


def ask_confusion_at(
    values: list[tuple[float | None, str, Fixture]], threshold: float
) -> Confusion:
    """A request counts as detected when Jev picked the right language at or above the
    threshold. Picking the wrong one is both a miss and a false alarm (it would switch the
    chat the wrong way); a request for a language on a message that asks nothing is a false
    alarm."""
    c = Confusion()
    for confidence, choice, fixture in values:
        fired = confidence is not None and choice in ("es", "en") and confidence >= threshold
        if fixture.asks in ("es", "en"):
            if fired and choice == fixture.asks:
                c.tp += 1
            else:
                c.fn += 1
                if fired:
                    c.fp += 1
        elif fired:
            c.fp += 1
        else:
            c.tn += 1
    return c


# --------------------------------------------------------------------------- tone


def tone_score(
    merged: dict[str, Merged], fixtures: list[Fixture], split: str, thresholds: Thresholds
) -> dict[str, Confusion]:
    """Per signal, how the hint thresholds did, plus the Escalations (``escalate_*``)."""
    out = {name: Confusion() for name in SIGNALS}
    out["escalate_wants_human"] = Confusion()
    out["escalate_abuse"] = Confusion()
    for fixture in fixtures:
        if fixture.split != split:
            continue
        got = merged[fixture.id].reading
        decision = decide_tone(got, thresholds) if got is not None else None
        fired = set(decision.signals) if decision else set()
        for name in SIGNALS:
            _add(out[name], name in fixture.signals, name in fired)
        _add(
            out["escalate_wants_human"],
            "wants_human" in fixture.signals,
            bool(decision and decision.escalation == "wants_human"),
        )
        _add(
            out["escalate_abuse"],
            "abusive" in fixture.signals,
            bool(decision and decision.escalation == "abuse"),
        )
    return out


def _add(c: Confusion, label: bool, fired: bool) -> None:
    if label and fired:
        c.tp += 1
    elif label:
        c.fn += 1
    elif fired:
        c.fp += 1
    else:
        c.tn += 1


def calm_false_alarms(
    merged: dict[str, Merged], fixtures: list[Fixture], split: str, thresholds: Thresholds
) -> list[str]:
    """Calm messages (no signal true) that still got a hint or an Escalation."""
    out = []
    for fixture in fixtures:
        got = merged[fixture.id].reading
        if fixture.split != split or fixture.signals or got is None:
            continue
        decision = decide_tone(got, thresholds)
        if decision.hint:
            out.append(f"{fixture.id} ({decision.hint})")
    return out


# --------------------------------------------------------------------------- latency


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def latency_stats(rows: list[Row]) -> dict[str, float]:
    ok = [float(r.latency_ms) for r in rows if r.ok]
    return {
        "calls": len(rows),
        "ok": len(ok),
        "failed": len(rows) - len(ok),
        "p50": percentile(ok, 0.5),
        "p95": percentile(ok, 0.95),
        "p99": percentile(ok, 0.99),
        "max": max(ok) if ok else float("nan"),
    }


def cost_stats(rows: list[Row]) -> dict[str, float]:
    costs = [r.cost_usd for r in rows if r.ok and r.cost_usd is not None]
    return {
        "total": round(sum(costs), 6),
        "mean": round(statistics.fmean(costs), 7) if costs else float("nan"),
        "unknown": sum(1 for r in rows if r.ok and r.cost_usd is None),
        "input_tokens": statistics.fmean([r.input_tokens for r in rows if r.ok] or [0]),
        "output_tokens": statistics.fmean([r.output_tokens for r in rows if r.ok] or [0]),
    }


def budget_check(rows: list[Row]) -> dict[str, Any]:
    """Each bound of the owner's budget, pass or fail.

    p95 and p99 are taken over every call, each failed call counting as ``float("inf")`` (over
    both bounds), so failures alone can fail the budget (owner decision 2026-10-12); unlike
    :func:`latency_stats`, which covers successful calls. The cost bound is checked on the
    unrounded mean of the known costs. No successful call, or no known cost, fails."""
    latencies = [float(r.latency_ms) if r.ok else float("inf") for r in rows]
    ok = sum(1 for r in rows if r.ok)
    costs = [r.cost_usd for r in rows if r.ok and r.cost_usd is not None]
    p95 = percentile(latencies, 0.95)
    p99 = percentile(latencies, 0.99)
    mean_cost = statistics.fmean(costs) if costs else float("nan")
    p95_pass = ok > 0 and p95 <= BUDGET_P95_MS
    p99_pass = ok > 0 and p99 < BUDGET_P99_MS
    cost_pass = bool(costs) and mean_cost <= BUDGET_MEAN_COST_USD
    return {
        "calls": len(rows),
        "ok": ok,
        "failed": len(rows) - ok,
        "p95": p95,
        "p99": p99,
        "mean_cost": mean_cost,
        "p95_pass": p95_pass,
        "p99_pass": p99_pass,
        "cost_pass": cost_pass,
        "pass": p95_pass and p99_pass and cost_pass,
    }


def default_thresholds() -> Thresholds:
    return THRESHOLDS
