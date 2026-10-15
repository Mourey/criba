"""Results and usage-ledger reads behind ``criba.api.metrics`` (ADR 0019).

Only the statements live here: they return rows (or one count) and ``api/metrics.py`` keeps
the arithmetic, rounding, percentiles and response shapes. None of these opens a transaction.
The caller's Location scope arrives as a SQL fragment on ``applications a`` with its
parameters (``present.scope_sql``), so ``db/`` does not import from ``api/``. A cost is the
reconciled cost when there is one, the estimate otherwise (:data:`_COST`).
"""

from __future__ import annotations

import sqlite3
from typing import Any

_COST = "COALESCE(u.reconciled_cost_usd, u.est_cost_usd)"


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def results_where(
    scope: str, scope_params: list[Any], *, location_id: str | None, country: str | None
) -> tuple[str, list[Any]]:
    """The results filter on ``applications a``: scope, covering Location and country."""
    params = [*scope_params]
    where = [scope]
    if location_id:
        where.append(
            "EXISTS (SELECT 1 FROM application_locations f WHERE f.application_id = a.id"
            " AND f.location_id = ?)"
        )
        params.append(location_id)
    if country:
        where.append("a.country = ?")
        params.append(country)
    return " AND ".join(where), params


def _cohort(base_where: str, params: list[Any], start: str, end: str) -> tuple[str, list[Any]]:
    """``base_where`` limited to Applications created in ``[start, end)``."""
    return f"{base_where} AND a.created_at >= ? AND a.created_at < ?", [*params, start, end]


def cohort_applications(
    conn: sqlite3.Connection, base_where: str, params: list[Any], start: str, end: str
) -> list[sqlite3.Row]:
    """The cohort's Applications (``id``, ``status``, ``stage``, ``outcome``,
    ``knockout_criterion``, ``created_at``, ``qualified_at``, ``stalled_since``)."""
    cohort, cohort_params = _cohort(base_where, params, start, end)
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT a.id, a.status, a.stage, a.outcome, a.knockout_criterion, a.created_at,"
        f" a.qualified_at, a.stalled_since FROM applications a WHERE {cohort}",
        cohort_params,
    ).fetchall()
    return rows


def cohort_event_counts(
    conn: sqlite3.Connection, base_where: str, params: list[Any], start: str, end: str
) -> list[sqlite3.Row]:
    """Per ``kind`` (``n``), the cohort's Takeover, Escalation and Review events."""
    cohort, cohort_params = _cohort(base_where, params, start, end)
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT e.kind, count(*) AS n FROM events e JOIN applications a"
        f" ON a.id = e.application_id WHERE {cohort} AND e.kind IN"
        " ('takeover_started', 'escalation_raised', 'review_requested',"
        " 'review_upheld', 'review_overturned') GROUP BY e.kind",
        cohort_params,
    ).fetchall()
    return rows


def qualified_waiting_count(conn: sqlite3.Connection, base_where: str, params: list[Any]) -> int:
    """How many open Applications under ``base_where`` are Qualified and waiting (any age)."""
    waiting: int = conn.execute(
        f"SELECT count(*) FROM applications a WHERE {base_where}"
        " AND a.status = 'open' AND a.qualified_waiting = 1",
        params,
    ).fetchone()[0]
    return waiting


def cohort_by_location(
    conn: sqlite3.Connection,
    base_where: str,
    params: list[Any],
    start: str,
    end: str,
    location_id: str | None,
) -> list[sqlite3.Row]:
    """Per covering Location (``applications``, ``qualified``, ``knockouts``), the cohort;
    only ``location_id`` when given."""
    cohort, cohort_params = _cohort(base_where, params, start, end)
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT al.location_id AS location_id, count(*) AS applications,"
        " sum(a.qualified_at IS NOT NULL) AS qualified,"
        " sum(a.outcome = 'knockout') AS knockouts"
        " FROM applications a JOIN application_locations al ON al.application_id = a.id"
        f" WHERE {cohort}"
        + (" AND al.location_id = ?" if location_id else "")
        + " GROUP BY al.location_id ORDER BY al.location_id",
        [*cohort_params, *([location_id] if location_id else [])],
    ).fetchall()
    return rows


# ---------------------------------------------------------------------------
# Usage ledger (economics and the model comparison)
# ---------------------------------------------------------------------------


def usage_where(
    start: str,
    end: str,
    *,
    source: str,
    dataset: str | None,
    environment: str | None,
) -> tuple[str, list[Any]]:
    """The ledger filter on ``usage u``: period ``[start, end)``, source, dataset, environment."""
    where = ["u.ts >= ?", "u.ts < ?", "u.source = ?"]
    params: list[Any] = [start, end, source]
    if dataset:
        where.append("u.dataset = ?")
        params.append(dataset)
    if environment:
        where.append("u.environment = ?")
        params.append(environment)
    return " AND ".join(where), params


def usage_cost_rows(conn: sqlite3.Connection, where: str, params: list[Any]) -> list[sqlite3.Row]:
    """Every ledger row under ``where``, with its ``cost``."""
    rows: list[sqlite3.Row] = conn.execute(
        f"SELECT u.*, {_COST} AS cost FROM usage u WHERE {where}", params
    ).fetchall()
    return rows


def usage_reach(conn: sqlite3.Connection, where: str, params: list[Any]) -> sqlite3.Row:
    """Distinct Applications in the ledger under ``where``: ``conversations``, ``qualified``
    (reached Qualified) and ``interviews`` (hold a standing booking)."""
    row: sqlite3.Row = conn.execute(
        "SELECT count(DISTINCT u.application_id) AS conversations,"
        " count(DISTINCT CASE WHEN a.qualified_at IS NOT NULL THEN u.application_id END)"
        "   AS qualified,"
        " count(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM bookings b"
        "   WHERE b.application_id = u.application_id AND b.status = 'booked')"
        "   THEN u.application_id END) AS interviews"
        " FROM usage u JOIN applications a ON a.id = u.application_id"
        f" WHERE {where}",
        params,
    ).fetchone()
    return row


def usage_cost_by_environment(
    conn: sqlite3.Connection, where: str, params: list[Any]
) -> list[sqlite3.Row]:
    """Per ``environment``, the summed ``cost`` of the ledger rows under ``where``."""
    rows: list[sqlite3.Row] = conn.execute(
        f"SELECT u.environment, sum({_COST}) AS cost FROM usage u WHERE {where}"
        " GROUP BY u.environment",
        params,
    ).fetchall()
    return rows


def latest_replay_dataset(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The newest replay ledger row's ``dataset``, or None when there is no replay row."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT dataset FROM usage WHERE source = 'replay' ORDER BY ts DESC, id DESC LIMIT 1"
    ).fetchone()
    return row


def replay_cost_rows(conn: sqlite3.Connection, dataset: str | None) -> list[sqlite3.Row]:
    """Every replay ledger row of ``dataset``, with its ``cost``."""
    rows: list[sqlite3.Row] = conn.execute(
        f"SELECT u.*, {_COST} AS cost FROM usage u WHERE u.source = 'replay' AND u.dataset = ?",
        (dataset,),
    ).fetchall()
    return rows


def live_usage_rows(conn: sqlite3.Connection, application_id: str) -> list[sqlite3.Row]:
    """The Application's live usage-ledger rows (``kind``, ``model``, the token counts, both
    costs and ``latency_ms``)."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT kind, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,"
        " est_cost_usd, reconciled_cost_usd, latency_ms FROM usage WHERE application_id = ?"
        " AND source = 'live'",
        (application_id,),
    ).fetchall()
    return rows
