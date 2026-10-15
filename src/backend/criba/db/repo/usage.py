"""The usage ledger's writes (ADR 0019) for the model calls that are not Jev's
(:mod:`criba.db.repo.signals`) or speech's (:mod:`criba.db.repo.voice_sessions`).

None of these opens a transaction. A cost is the caller's arithmetic; the statements only store.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any


def insert_summary_usage(
    conn: sqlite3.Connection,
    *,
    ts: str,
    environment: str,
    client_id: str,
    candidate_id: str,
    application_id: str,
    stage: str,
    provider: str,
    requested_model: str | None,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    latency_ms: int | None,
    status: str,
    error: str | None,
    reconciled_cost_usd: float | None,
) -> None:
    """One ledger row for a summary model call (``profile = 'summary'``, ``kind = 'auxiliary'``)."""
    conn.execute(
        "INSERT INTO usage (ts, environment, client_id, candidate_id, application_id, stage,"
        " profile, kind, provider, requested_model, model, input_tokens, output_tokens,"
        " cache_read_tokens, cache_write_tokens, latency_ms, status, error,"
        " reconciled_cost_usd)"
        " VALUES (?, ?, ?, ?, ?, ?, 'summary', 'auxiliary', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ts,
            environment,
            client_id,
            candidate_id,
            application_id,
            stage,
            provider,
            requested_model,
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            latency_ms,
            status,
            error,
            reconciled_cost_usd,
        ),
    )


_INSERT_RUN_USAGE = (
    "INSERT INTO usage (ts, environment, client_id, location_id, candidate_id,"
    " application_id, stage, profile, run_id, session_id, kind, provider, model,"
    " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, est_cost_usd,"
    " latency_ms, status, error)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'run', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
# A replayed run that already has its row is not counted again. A row an earlier attempt marked
# failed (a timeout while the run was still going, ADR 0073) takes the counts of the run's
# success instead: still one row per run.
_INSERT_RUN_USAGE_OK = (
    _INSERT_RUN_USAGE + " ON CONFLICT (run_id, kind) WHERE run_id IS NOT NULL AND kind = 'run'"
    " DO UPDATE SET status = excluded.status, error = NULL, model = excluded.model,"
    " provider = excluded.provider, input_tokens = excluded.input_tokens,"
    " output_tokens = excluded.output_tokens, cache_read_tokens = excluded.cache_read_tokens,"
    " cache_write_tokens = excluded.cache_write_tokens, est_cost_usd = excluded.est_cost_usd,"
    " latency_ms = excluded.latency_ms WHERE usage.status != 'ok'"
)
# The counts of a row that already exists stay, so its usage-unknown mark stays as it was.
_INSERT_RUN_USAGE_FAILED = (
    _INSERT_RUN_USAGE + " ON CONFLICT (run_id, kind) WHERE run_id IS NOT NULL AND kind = 'run'"
    " DO UPDATE SET status = excluded.status,"
    " error = CASE WHEN substr(usage.error, 1, ?) = ? THEN ? ELSE ? END"
)


def insert_run_usage(conn: sqlite3.Connection, values: Sequence[Any], *, failed: bool) -> None:
    """One ledger row for an agent run (``kind = 'run'``), one per ``run_id``.

    ``values`` are the 20 columns in the order ``ts, environment, client_id, location_id,
    candidate_id, application_id, stage, profile, run_id, session_id, provider, model,
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, est_cost_usd,
    latency_ms, status, error``; for a failed run they are followed by the four values of the
    conflict clause (the length of the usage-unknown mark, the mark, the marked error and the
    plain one)."""
    conn.execute(_INSERT_RUN_USAGE_FAILED if failed else _INSERT_RUN_USAGE_OK, tuple(values))
