"""Import a comparison dataset into the usage ledger for the dashboard replay (spec 004 task 10).

The economics dashboard's comparison view (`GET /api/metrics/comparison`,
`criba.api.metrics.UsageComparison`) reads ``usage`` rows with ``source = 'replay'`` and a
``dataset`` name, grouped by ``model``. This module turns dataset lines into such rows:

- one ``kind = 'run'`` row per Candidate turn, ``latency_ms`` the turn's reply latency, the
  persona run's tokens and cost spread evenly over its turns (so the cost per screening adds
  up), ``session_id`` naming the persona run (the dashboard's screening key);
- one more row, ``kind = 'auxiliary'``, ``status = 'error'``, no tokens or cost, for a persona
  run whose outcome is not what the persona expects (or that failed to play).

What the dashboard then shows exactly: runs (= turns), latency p50/p95/p99, cost per
screening, and outcome accuracy (the share of screenings with no error row). What it cannot
show from today's schema: ``validation_failures`` there counts error rows, so it reads as
"screenings not as expected", and ``escalations`` is always 0. The dataset file and its
markdown summary (`sim.compare.dataset.markdown`) carry the exact five figures; showing them on
the dashboard needs a backend change (columns or a replay table read by `UsageComparison`).

A re-import replaces the dataset's rows. Rows carry no Application, Candidate or Client id:
the persona runs may come from another stack.

Two ways in: a SQLite file (``--db``, tests and local copies), or the running stack's database
through ``docker compose exec`` on the backend container (``--compose-project``), which runs
the same :func:`apply_rows` inside the container. That second path writes to the database
from a second process, outside the backend: the busy timeout covers it, but it is a stop-gap
for a backend import endpoint.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sim.compare.dataset import RunRecord
from sim.spend import is_stub_model

TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)
REPO_ROOT = Path(__file__).resolve().parents[4]


def apply_rows(conn: sqlite3.Connection, dataset: str, rows: list[dict[str, Any]]) -> int:
    """Replace `dataset`'s replay rows with `rows` in one transaction; returns rows written.

    The one implementation of the write: called locally, and sent as source to the backend
    container (plain sqlite3 only, the container has no `sim` package).
    """
    columns = (
        "ts",
        "environment",
        "source",
        "dataset",
        "stage",
        "profile",
        "session_id",
        "kind",
        "provider",
        "requested_model",
        "model",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "est_cost_usd",
        "latency_ms",
        "status",
        "error",
    )
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM usage WHERE source = 'replay' AND dataset = ?", (dataset,))
        conn.executemany(
            "INSERT INTO usage ("
            + ", ".join(columns)
            + ") VALUES ("
            + ", ".join("?" for _ in columns)
            + ")",
            [tuple(row[c] for c in columns) for row in rows],
        )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return len(rows)


_CONTAINER_MAIN = """
import json, os, sys
payload = json.loads(sys.stdin.read())
conn = sqlite3.connect(
    os.environ.get("CRIBA_DB_PATH", "/data/criba.db"), isolation_level=None, timeout=10
)
print(apply_rows(conn, payload["dataset"], payload["rows"]))
"""


def container_program() -> str:
    """The Python program `import_compose` runs in the backend container."""
    header = "from __future__ import annotations\nimport sqlite3\n"
    return header + inspect.getsource(apply_rows) + _CONTAINER_MAIN


def _split(total: float, parts: int, *, whole: bool) -> list[float | int]:
    if parts <= 0:
        return []
    if whole:
        base, rest = divmod(int(total), parts)
        return [base + (1 if i < rest else 0) for i in range(parts)]
    return [round(float(total) / parts, 10)] * parts


def usage_rows(
    records: Sequence[RunRecord], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """The ledger rows for `records` (see the module docstring)."""
    ts = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows: list[dict[str, Any]] = []
    for r in records:
        session = f"{r.dataset}/{r.model}/{r.persona_id}/{r.rep}"
        provider = (
            "stub" if all(is_stub_model(m) for m in r.served_models or [r.model]) else "openrouter"
        )
        common = {
            "ts": ts,
            "environment": "sim",
            "source": "replay",
            "dataset": r.dataset,
            "stage": r.stage,
            "profile": None,
            "session_id": session,
            "provider": provider,
            "requested_model": r.model,
            "model": r.model,
        }
        n = len(r.latency_s)
        tokens = {k: _split(r.tokens.get(k, 0), n, whole=True) for k in TOKEN_KEYS}
        costs = _split(r.cost_usd, n, whole=False)
        for i, latency in enumerate(r.latency_s):
            rows.append(
                {
                    **common,
                    "kind": "run",
                    **{k: tokens[k][i] for k in TOKEN_KEYS},
                    "est_cost_usd": costs[i],
                    "latency_ms": max(0, round(latency * 1000)),
                    "status": "ok",
                    "error": None,
                }
            )
        if r.error is not None or not r.correct:
            why = r.error or (r.failures[0] if r.failures else "not as the persona expects")
            rows.append(
                {
                    **common,
                    "kind": "auxiliary",
                    **dict.fromkeys(TOKEN_KEYS, 0),
                    "est_cost_usd": 0.0,
                    "latency_ms": None,
                    "status": "error",
                    "error": f"outcome_incorrect: {why}"[:500],
                }
            )
    return rows


def _datasets(records: Sequence[RunRecord]) -> list[str]:
    names = sorted({r.dataset for r in records})
    if not names:
        raise ValueError("the dataset is empty")
    return names


def import_into(
    conn: sqlite3.Connection,
    records: Sequence[RunRecord],
    *,
    now: datetime | None = None,
) -> int:
    """Write `records` into an open connection's usage ledger. Returns the rows written."""
    written = 0
    for name in _datasets(records):
        mine = [r for r in records if r.dataset == name]
        written += apply_rows(conn, name, usage_rows(mine, now=now))
    return written


def import_file(db: Path, records: Sequence[RunRecord], *, now: datetime | None = None) -> int:
    conn = sqlite3.connect(str(db), isolation_level=None, timeout=10)
    try:
        return import_into(conn, records, now=now)
    finally:
        conn.close()


Run = Callable[..., subprocess.CompletedProcess[str]]


def import_compose(
    project: str,
    records: Sequence[RunRecord],
    *,
    service: str = "backend",
    run: Run = subprocess.run,
    now: datetime | None = None,
) -> int:
    """Write `records` into the database of a running Compose project's backend."""
    written = 0
    for name in _datasets(records):
        mine = [r for r in records if r.dataset == name]
        payload = json.dumps({"dataset": name, "rows": usage_rows(mine, now=now)})
        result = run(
            [
                "docker",
                "compose",
                "-p",
                project,
                "exec",
                "-T",
                service,
                "python",
                "-c",
                container_program(),
            ],
            input=payload,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"import into {project}/{service} failed: {result.stderr.strip()}")
        written += int(result.stdout.strip().splitlines()[-1])
    return written


__all__ = [
    "apply_rows",
    "container_program",
    "import_compose",
    "import_file",
    "import_into",
    "usage_rows",
]
