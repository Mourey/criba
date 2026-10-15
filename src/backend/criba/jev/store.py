"""What the Jev router keeps (ADR 0051): the usage row of each call, and each message's signals.

``message_signals`` rows are tied to a message and an Application, hold probabilities and codes
and never text, and are deleted after :data:`DEFAULT_RETENTION_DAYS` by the scheduler
(:func:`purge_signals`) or with the message on an Erasure request. No decision path reads this
module: Knockouts, Rejections and every other rule take their inputs from the answers
(a test, ``tests/jev/test_not_a_decision_input.py``, keeps it that way). Its readers are the
Recruiter's communication notes (:mod:`criba.jev.notes`) and the aggregate view, and the router
step itself, which reads back the run of unreadable messages before this one
(:func:`unreadable_run`, ADR 0063) to raise an Escalation, never a Knockout or Rejection.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from criba.db import repo
from criba.db.connection import iso
from criba.jev.battery import SIGNALS, Reading
from criba.jev.client import JevFailed, JevResult
from criba.jev.policy import THRESHOLDS, Thresholds, unreadable_state

DEFAULT_RETENTION_DAYS = 180
RETENTION_ENV = "CRIBA_SIGNALS_RETENTION_DAYS"


def retention_days(env: Mapping[str, str] | None = None) -> int:
    source = os.environ if env is None else env
    try:
        days = int(source.get(RETENTION_ENV, DEFAULT_RETENTION_DAYS))
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return days if days > 0 else DEFAULT_RETENTION_DAYS


# ---------------------------------------------------------------------------
# The usage ledger (ADR 0019)
# ---------------------------------------------------------------------------


def record_usage(
    conn: sqlite3.Connection,
    *,
    application: Mapping[str, Any],
    environment: str,
    now: datetime,
    requested_model: str,
    result: JevResult | None = None,
    failure: JevFailed | None = None,
) -> None:
    """One ledger row per Jev call, as its own kind. OpenRouter's own charge goes in
    ``reconciled_cost_usd`` (a bill, not an estimate); a failed call keeps no cost (unknown,
    not zero) and its status says why. The error text is a code, never a response body."""
    if result is not None:
        provider, model, tokens_in, tokens_out = (
            result.provider,
            result.model,
            result.input_tokens,
            result.output_tokens,
        )
        cost, latency, status, error = result.cost_usd, result.latency_ms, "ok", None
    elif failure is not None:
        provider, model, tokens_in, tokens_out = "typesafe", requested_model, 0, 0
        cost, latency, status, error = None, failure.latency_ms, failure.kind, str(failure)[:200]
    else:
        raise ValueError("a Jev usage row needs a result or a failure")
    repo.insert_jev_usage(
        conn,
        ts=iso(now),
        environment=environment,
        client_id=application["client_id"],
        candidate_id=application["candidate_id"],
        application_id=application["id"],
        stage=application["stage"],
        provider=provider,
        requested_model=requested_model,
        model=model,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        reconciled_cost_usd=cost,
        latency_ms=latency,
        status=status,
        error=error,
    )


# ---------------------------------------------------------------------------
# message_signals
# ---------------------------------------------------------------------------


def record_signals(
    conn: sqlite3.Connection,
    *,
    message_id: int,
    application: Mapping[str, Any],
    now: datetime,
    status: str,
    model: str | None = None,
    policy_version: str | None = None,
    reading: Reading | None = None,
    first_message: bool = False,
    language_set: str | None = None,
    language_reason: str | None = None,
    fired: tuple[str, ...] = (),
    hint: str | None = None,
    escalation: str | None = None,
) -> None:
    """Store what Jev read from one message. A failed call is stored too, with its status and
    no values: it is never a calm reading. ``intent``, ``intent_confidence`` and ``malice``
    (ADR 0071) are stored as the reading holds them, None included: the router passes them
    as None for a spoken turn."""
    values: dict[str, float | None] = dict.fromkeys(SIGNALS)
    if reading is not None:
        values = {name: reading.signals.get(name) for name in SIGNALS}
    repo.upsert_signal_row(
        conn,
        (
            message_id,
            application["id"],
            application["stage"],
            status,
            model,
            policy_version,
            reading.lang if reading else None,
            reading.lang_confidence if reading else None,
            reading.switch if reading else None,
            values["frustrated"],
            values["confused"],
            values["wants_human"],
            values["abusive"],
            1 if first_message else 0,
            language_set,
            json.dumps(list(fired)),
            hint,
            escalation,
            iso(now),
            reading.asks if reading else None,
            reading.asks_confidence if reading else None,
            language_reason if language_set else None,
            reading.unreadable if reading else None,
            reading.intent if reading else None,
            reading.intent_confidence if reading else None,
            reading.malice if reading else None,
        ),
    )


# What ends a run of unreadable messages besides a readable one: a recorded answer (a tap
# included), and a Recruiter's decision on the conversation (ADR 0063).
RUN_RESETS = ("answer_recorded", "escalation_dismissed", "takeover_started", "takeover_handed_back")
_RUN_LOOKBACK = 20


def unreadable_run(
    conn: sqlite3.Connection,
    application_id: str,
    before_message_id: int,
    thresholds: Thresholds = THRESHOLDS,
) -> int:
    """How many of the Candidate's messages right before ``before_message_id`` were unreadable
    in a row (ADR 0063). Newest first: an unreadable message counts; an uncertain reading or a
    failed call is passed over (it neither counts nor ends the run); a readable message, a
    message with no reading (a tap, a media message, a message under a Takeover) or one before
    the last recorded answer or Recruiter decision ends it."""
    since = repo.latest_event_time_of_kinds(conn, application_id, RUN_RESETS)
    rows = repo.inbound_with_signals_before(
        conn, application_id, before_message_id, since, _RUN_LOOKBACK
    )
    run = 0
    for row in rows:
        if row["signals_id"] is None:
            break
        if row["status"] != "ok":
            continue
        state = unreadable_state(row["body"], row["unreadable"], thresholds)
        if state == "readable":
            break
        if state == "unreadable":
            run += 1
    return run


def list_signals(conn: sqlite3.Connection, application_id: str) -> list[dict[str, Any]]:
    """The Application's stored signals with each message's time, oldest first."""
    rows = repo.signals_with_message_time(conn, application_id)
    return [{**dict(r), "fired": json.loads(r["fired"])} for r in rows]


def purge_signals(conn: sqlite3.Connection, now: datetime, days: int) -> int:
    """Delete signals older than ``days``. Returns how many rows went."""
    cutoff = iso(now - timedelta(days=days))
    removed = repo.delete_signals_before(conn, cutoff)
    conn.commit()
    return removed


def aggregate_by_stage(
    conn: sqlite3.Connection, scope_sql: str, params: list[Any], start: str, end: str
) -> list[dict[str, Any]]:
    """Counts per screening stage over a period, for improving the bot (ADR 0051).

    ``scope_sql`` is the caller's Location scope on ``applications a``. Only counts, never a
    row of one Candidate: how many messages were read at each stage, how many a signal fired
    on, and how many changed the language. A message Jev could not read is counted apart."""
    rows = repo.signal_counts_by_stage(conn, scope_sql, params, start, end)
    return [
        {
            "stage": r["stage"],
            "messages": r["messages"] or 0,
            "unread": r["unread"] or 0,
            **{
                name: r[name] or 0
                for name in (
                    "frustrated",
                    "confused",
                    "wants_human",
                    "abusive",
                    "language_switches",
                )
            },
        }
        for r in rows
    ]
