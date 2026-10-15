"""The comparison dataset: JSONL, one line per persona run (spec 004 task 9).

Each line is a :class:`RunRecord`. The same persona set runs once per model (or per label, on
the stub), so a dataset holds several models side by side, and the summary compares them on
what ADR 0025 asks for:

- **latency**: reply latency per Candidate turn as the driver saw it, p50 and p95;
- **cost**: per screening (persona run) and per Qualified Application. A run's cost is
  OpenRouter's own charge where the usage ledger carries it (``reconciled_cost_usd``), else its
  tokens at the serving model's own rates (`sim.spend.reported_cost`), next to the spend guard's
  own (pessimistic) count;
- **validation failures**: ``answer_invalid`` events, per screening (personas that give a bad
  answer on purpose have some on every model; the difference between models is the signal);
- **Escalations**: screenings that raised one (``escalation_raised``);
- **outcome correctness**: screenings whose Application ended as the persona expects
  (`sim.asserts.check_persona`, without the latency checks).

Format, version 1: the fields of :class:`RunRecord`, plus ``"format": "criba-comparison/1"``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FORMAT = "criba-comparison/1"


@dataclass
class RunRecord:
    dataset: str
    model: str  # the label the dashboard groups by: the served model, or a stub label
    persona_id: str
    rep: int
    started_at: str
    ended: str
    served_models: list[str] = field(default_factory=list)
    conversation_id: str | None = None
    application_id: str | None = None
    expected: dict[str, Any] = field(default_factory=dict)  # stage, status, outcome
    stage: str | None = None
    status: str | None = None
    outcome: str | None = None
    correct: bool = False
    failures: list[str] = field(default_factory=list)
    validation_failures: int = 0
    escalations: int = 0
    turns: int = 0
    agent_runs: int = 0
    latency_s: list[float] = field(default_factory=list)  # first-reply latency per turn
    tokens: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0  # the run's cost: OpenRouter's charge, else tokens at per-model rates
    guard_cost_usd: float = 0.0  # the spend guard's count
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps({"format": FORMAT, **asdict(self)}, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRecord:
        body = dict(data)
        fmt = body.pop("format", FORMAT)
        if fmt != FORMAT:
            raise ValueError(f"unknown dataset format {fmt!r} (expected {FORMAT})")
        return cls(**body)

    @property
    def qualified(self) -> bool:
        return self.stage in ("qualified", "approved", "interview_booked") and self.outcome is None


def write_jsonl(path: Path, records: Iterable[RunRecord], *, append: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a" if append else "w", encoding="utf-8") as out:
        for record in records:
            out.write(record.to_json() + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[RunRecord]:
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(RunRecord.from_dict(json.loads(line)))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
    return records


def _pct(values: Sequence[float], q: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * q / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def summarise(records: Sequence[RunRecord]) -> list[dict[str, Any]]:
    """One row per model, in the dataset's order of first appearance."""
    groups: dict[str, list[RunRecord]] = defaultdict(list)
    for r in records:
        groups[r.model].append(r)
    rows = []
    for model, group in groups.items():
        played = [r for r in group if r.error is None]
        latencies = [v for r in played for v in r.latency_s]
        cost = sum(r.cost_usd for r in played)
        qualified = sum(1 for r in played if r.qualified)
        turns = sum(r.turns for r in played)
        n = len(played)
        rows.append(
            {
                "model": model,
                "served_models": sorted({m for r in group for m in r.served_models}),
                "screenings": n,
                "errors": len(group) - n,
                "personas": len({r.persona_id for r in group}),
                "turns": turns,
                "agent_runs": sum(r.agent_runs for r in played),
                "latency_p50_s": _round(_pct(latencies, 50)),
                "latency_p95_s": _round(_pct(latencies, 95)),
                "cost_total_usd": round(cost, 6),
                "cost_per_screening_usd": _round(cost / n if n else None, 6),
                "cost_per_qualified_usd": _round(cost / qualified if qualified else None, 6),
                "guard_cost_usd": round(sum(r.guard_cost_usd for r in group), 6),
                "validation_failures": sum(r.validation_failures for r in played),
                "validation_failures_per_screening": _round(
                    sum(r.validation_failures for r in played) / n if n else None
                ),
                "escalation_rate": _round(
                    sum(1 for r in played if r.escalations) / n if n else None
                ),
                "outcome_accuracy": _round(sum(1 for r in played if r.correct) / n if n else None),
                "incorrect": sorted({r.persona_id for r in played if not r.correct}),
            }
        )
    return rows


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def _cell(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".") if value else "0"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "none"
    return str(value)


def markdown(records: Sequence[RunRecord], *, title: str = "Model comparison") -> str:
    rows = summarise(records)
    datasets = sorted({r.dataset for r in records})
    head = [
        f"# {title}",
        "",
        (
            f"Dataset{'s' if len(datasets) > 1 else ''}: {', '.join(datasets) or 'none'}. "
            f"{len(records)} persona runs."
        ),
        "",
    ]
    cols = [
        ("Model (label)", "model"),
        ("Served", "served_models"),
        ("Screenings", "screenings"),
        ("Turns", "turns"),
        ("Latency p50 (s)", "latency_p50_s"),
        ("Latency p95 (s)", "latency_p95_s"),
        ("Cost per screening (USD)", "cost_per_screening_usd"),
        ("Cost per Qualified (USD)", "cost_per_qualified_usd"),
        ("Validation failures per screening", "validation_failures_per_screening"),
        ("Escalation rate", "escalation_rate"),
        ("Outcome accuracy", "outcome_accuracy"),
        ("Not as expected", "incorrect"),
        ("Errors", "errors"),
    ]
    table = [
        "| " + " | ".join(c for c, _ in cols) + " |",
        "|" + "---|" * len(cols),
    ]
    for row in rows:
        table.append("| " + " | ".join(_cell(row[k]) for _, k in cols) + " |")
    return "\n".join([*head, *table, ""])


__all__ = ["FORMAT", "RunRecord", "markdown", "read_jsonl", "summarise", "write_jsonl"]
