"""Direct tests for the repo functions that took over the SQL of ``api/slots.py`` and
``api/metrics.py``: the slot overlap check with its ``id != ?`` exclusion, the composed slot
and results filters, the cohort aggregates and the usage-ledger reads.

Each composed query is checked against a literal copy of the inline query it replaced, run on
the same seeded data, so the rows (and their column names) are the same. The API tests cover
the plain one-line selects and writes."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from criba.db import repo
from criba.db.connection import iso
from criba.db.seed import CLIENT_ID, seed_users

T0 = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
PUEBLA_CENTRO = "zone_mx_puebla_centro"
LOC_PUEBLA_CENTRO = "loc_mx_puebla_centro"
RECRUITER = "usr_rec_puebla"

# --- literal copies of the old inline SQL in api/slots.py and api/metrics.py ---------------

OLD_SLOT_SELECT = (
    "SELECT s.*, l.name AS location_name, l.timezone AS timezone,"
    " b.application_id AS b_application_id, b.booked_via AS b_via, b.created_at AS b_at,"
    " c.full_name AS candidate_name"
    " FROM interview_slots s JOIN locations l ON l.id = s.location_id"
    " LEFT JOIN bookings b ON b.slot_id = s.id AND b.status = 'booked'"
    " LEFT JOIN applications a ON a.id = b.application_id"
    " LEFT JOIN candidates c ON c.id = a.candidate_id"
)
OLD_COST = "COALESCE(u.reconciled_cost_usd, u.est_cost_usd)"


def old_list_slots(
    conn: sqlite3.Connection,
    scope: str,
    params: list[Any],
    location_id: str | None,
    from_: str | None,
    to: str | None,
) -> list[sqlite3.Row]:
    params = list(params)
    where = [scope]
    if location_id:
        where.append("s.location_id = ?")
        params.append(location_id)
    if from_:
        where.append("s.starts_at >= ?")
        params.append(from_)
    if to:
        where.append("s.starts_at <= ?")
        params.append(to)
    return conn.execute(
        f"{OLD_SLOT_SELECT} WHERE {' AND '.join(where)} ORDER BY s.starts_at, s.id", params
    ).fetchall()


def old_open_slots(
    conn: sqlite3.Connection, location_ids: list[str], now: str, limit: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT s.id, s.starts_at, s.ends_at, l.name AS location_name, l.address, l.timezone"
        " FROM interview_slots s JOIN locations l ON l.id = s.location_id"
        f" WHERE s.location_id IN ({','.join('?' * len(location_ids))})"
        " AND s.cancelled_at IS NULL AND s.starts_at > ?"
        " AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.slot_id = s.id AND b.status = 'booked')"
        " ORDER BY s.starts_at, s.id LIMIT ?",
        [*location_ids, now, limit],
    ).fetchall()


def old_results(
    conn: sqlite3.Connection,
    scope: str,
    params: list[Any],
    location_id: str | None,
    country: str | None,
    start: str,
    end: str,
) -> tuple[list[sqlite3.Row], list[sqlite3.Row], int, list[sqlite3.Row]]:
    params = list(params)
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
    base_where = " AND ".join(where)
    cohort = f"{base_where} AND a.created_at >= ? AND a.created_at < ?"
    cohort_params = [*params, start, end]
    apps = conn.execute(
        "SELECT a.id, a.status, a.stage, a.outcome, a.knockout_criterion, a.created_at,"
        f" a.qualified_at, a.stalled_since FROM applications a WHERE {cohort}",
        cohort_params,
    ).fetchall()
    events = conn.execute(
        "SELECT e.kind, count(*) AS n FROM events e JOIN applications a"
        f" ON a.id = e.application_id WHERE {cohort} AND e.kind IN"
        " ('takeover_started', 'escalation_raised', 'review_requested',"
        " 'review_upheld', 'review_overturned') GROUP BY e.kind",
        cohort_params,
    ).fetchall()
    waiting = conn.execute(
        f"SELECT count(*) FROM applications a WHERE {base_where}"
        " AND a.status = 'open' AND a.qualified_waiting = 1",
        params,
    ).fetchone()[0]
    by_location = conn.execute(
        "SELECT al.location_id AS location_id, count(*) AS applications,"
        " sum(a.qualified_at IS NOT NULL) AS qualified,"
        " sum(a.outcome = 'knockout') AS knockouts"
        " FROM applications a JOIN application_locations al ON al.application_id = a.id"
        f" WHERE {cohort}"
        + (" AND al.location_id = ?" if location_id else "")
        + " GROUP BY al.location_id ORDER BY al.location_id",
        [*cohort_params, *([location_id] if location_id else [])],
    ).fetchall()
    return apps, events, waiting, by_location


def old_usage_where(
    start: str, end: str, source: str, dataset: str | None, environment: str | None
) -> tuple[str, list[Any]]:
    where = ["u.ts >= ?", "u.ts < ?", "u.source = ?"]
    params: list[Any] = [start, end, source]
    if dataset:
        where.append("u.dataset = ?")
        params.append(dataset)
    if environment:
        where.append("u.environment = ?")
        params.append(environment)
    return " AND ".join(where), params


def recruiter_scope() -> tuple[str, list[Any]]:
    """What ``present.scope_sql`` returns for the Puebla Recruiter."""
    return (
        "(a.client_id = ? AND EXISTS (SELECT 1 FROM application_locations al"
        " JOIN user_locations ul ON ul.location_id = al.location_id"
        " WHERE al.application_id = a.id AND ul.user_id = ?))",
        [CLIENT_ID, RECRUITER],
    )


def as_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# --- builders -----------------------------------------------------------------------------


def started(conn: sqlite3.Connection, external_id: str, now: datetime = T0) -> dict:
    candidate, _ = repo.get_or_create_candidate(
        conn,
        client_id=CLIENT_ID,
        channel="webchat",
        external_id=external_id,
        phone=None,
        language="es-MX",
        now=now,
    )
    app, _ = repo.resolve_application(conn, candidate["id"], "webchat", now=now)
    return app


def in_zone(conn: sqlite3.Connection, external_id: str, now: datetime = T0) -> dict:
    app = started(conn, external_id, now)
    repo.record_answer(conn, app["id"], "zone", PUEBLA_CENTRO, now=now)
    return app


def slot_ids(conn: sqlite3.Connection, location_id: str) -> list[str]:
    return [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM interview_slots WHERE location_id = ? ORDER BY starts_at, id",
            (location_id,),
        )
    ]


@pytest.fixture
def results_db(seeded: sqlite3.Connection) -> sqlite3.Connection:
    """Applications in and out of a covering Location, in and out of the period, with
    Review, Takeover and Escalation events, one Qualified and waiting, one Knockout."""
    seed_users(seeded, "synthetic-password", now=T0)
    covered = in_zone(seeded, "r1")
    qualified = in_zone(seeded, "r2")
    seeded.execute(
        "UPDATE applications SET qualified_at = ?, qualified_waiting = 1 WHERE id = ?",
        (iso(T0 + timedelta(hours=2)), qualified["id"]),
    )
    knocked = in_zone(seeded, "r3")
    seeded.execute(
        "UPDATE applications SET status = 'closed', outcome = 'knockout',"
        " knockout_criterion = 'zone', closed_at = ? WHERE id = ?",
        (iso(T0), knocked["id"]),
    )
    started(seeded, "r4")  # no covering Location: outside the Recruiter's scope
    in_zone(seeded, "r5", now=T0 - timedelta(days=40))  # before the period
    for app, kind in (
        (covered, "review_requested"),
        (covered, "review_upheld"),
        (qualified, "review_overturned"),
        (qualified, "takeover_started"),
        (knocked, "escalation_raised"),
        (knocked, "review_requested"),
        (covered, "answer_recorded"),  # not counted
    ):
        repo.add_event(
            seeded,
            candidate_id=app["candidate_id"],
            application_id=app["id"],
            kind=kind,
            actor="system",
            now=T0,
        )
    return seeded


# --- slots --------------------------------------------------------------------------------


def test_slot_start_taken_ignores_the_slot_itself_and_cancelled_slots(
    seeded: sqlite3.Connection,
) -> None:
    first, second = slot_ids(seeded, LOC_PUEBLA_CENTRO)[:2]
    row = repo.slot_detail(seeded, first)
    assert row is not None
    location, starts = row["location_id"], row["starts_at"]
    assert repo.slot_start_taken(seeded, location, starts)
    assert not repo.slot_start_taken(seeded, location, starts, ignore=first)  # moving onto itself
    assert repo.slot_start_taken(seeded, location, starts, ignore=second)
    assert not repo.slot_start_taken(seeded, "loc_mx_puebla_la_paz_nope", starts)
    repo.cancel_interview_slot(seeded, first, iso(T0))
    assert not repo.slot_start_taken(seeded, location, starts)
    cancelled = repo.slot_detail(seeded, first)
    assert cancelled is not None and cancelled["cancelled_at"] == iso(T0)


def test_slot_writes_and_detail(seeded: sqlite3.Connection) -> None:
    seed_users(seeded, "synthetic-password", now=T0)  # created_by references users
    assert repo.active_location_exists(seeded, LOC_PUEBLA_CENTRO)
    assert not repo.active_location_exists(seeded, "loc_nowhere")
    starts = iso(T0 + timedelta(days=30))
    ends = iso(T0 + timedelta(days=30, minutes=30))
    repo.insert_interview_slot(
        seeded, "slot_new", LOC_PUEBLA_CENTRO, starts, ends, "usr_operator", iso(T0)
    )
    row = repo.slot_detail(seeded, "slot_new")
    assert row is not None
    assert (row["starts_at"], row["ends_at"], row["b_application_id"]) == (starts, ends, None)
    assert dict(row) == dict(
        seeded.execute(f"{OLD_SLOT_SELECT} WHERE s.id = ?", ("slot_new",)).fetchone()
    )
    later = iso(T0 + timedelta(days=31))
    later_end = iso(T0 + timedelta(days=31, minutes=30))
    repo.move_interview_slot(seeded, "slot_new", later, later_end)
    moved = repo.slot_detail(seeded, "slot_new")
    assert moved is not None and (moved["starts_at"], moved["ends_at"]) == (later, later_end)
    assert repo.slot_detail(seeded, "slot_nope") is None


@pytest.mark.parametrize(
    ("scoped", "location_id", "from_", "to"),
    [
        (False, None, None, None),
        (True, None, None, None),
        (True, LOC_PUEBLA_CENTRO, None, None),
        (False, LOC_PUEBLA_CENTRO, "2026-10-13T00:00:00Z", "2026-10-15T00:00:00Z"),
        (True, None, "2026-10-14T00:00:00Z", None),
        (False, None, None, "2026-10-13T00:00:00Z"),
    ],
)
def test_list_scoped_slots_matches_the_old_inline_query(
    seeded: sqlite3.Connection,
    scoped: bool,
    location_id: str | None,
    from_: str | None,
    to: str | None,
) -> None:
    booked = in_zone(seeded, "s1")
    slot = repo.open_slot_rows(seeded, booked["id"], iso(T0))[0]["slot_id"]
    repo.insert_booking(seeded, "bk_s1", booked["id"], slot, "chat", "bk_s1@criba.local", iso(T0))
    if scoped:
        locations = [LOC_PUEBLA_CENTRO, "loc_mx_puebla_la_paz"]
        scope, params = f"s.location_id IN ({','.join('?' * len(locations))})", locations
    else:
        scope, params = "1 = 1", []
    before = list(params)
    rows = repo.list_scoped_slots(
        seeded, scope, params, location_id=location_id, starts_from=from_, starts_to=to
    )
    assert params == before  # the caller's list is not mutated
    expected = old_list_slots(seeded, scope, params, location_id, from_, to)
    assert expected
    assert as_dicts(rows) == as_dicts(expected)
    for r in rows:  # the standing booking shows on its slot, and only there
        assert (r["b_application_id"] == booked["id"]) == (r["id"] == slot)


def test_public_open_slots_skip_booked_and_cancelled_and_stop_at_the_limit(
    seeded: sqlite3.Connection,
) -> None:
    app = in_zone(seeded, "p1")
    locations = repo.application_location_ids(seeded, app["id"])
    assert locations
    rows = repo.public_open_slots(seeded, locations, iso(T0), 200)
    first, second = rows[0]["id"], rows[1]["id"]
    repo.insert_booking(seeded, "bk_p1", app["id"], first, "chat", "bk_p1@criba.local", iso(T0))
    repo.cancel_interview_slot(seeded, second, iso(T0))
    after = repo.public_open_slots(seeded, locations, iso(T0), 200)
    assert {first, second}.isdisjoint(r["id"] for r in after)
    assert as_dicts(after) == as_dicts(old_open_slots(seeded, locations, iso(T0), 200))
    assert len(repo.public_open_slots(seeded, locations, iso(T0), 3)) == 3
    assert repo.public_open_slots(seeded, locations, "2099-01-01T00:00:00Z", 200) == []


# --- results ------------------------------------------------------------------------------


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize(
    ("location_id", "country"),
    [(None, None), (LOC_PUEBLA_CENTRO, None), (None, "MX"), (LOC_PUEBLA_CENTRO, "ES")],
)
def test_results_queries_match_the_old_inline_queries(
    results_db: sqlite3.Connection, scoped: bool, location_id: str | None, country: str | None
) -> None:
    scope, scope_params = recruiter_scope() if scoped else ("1 = 1", [])
    start, end = "2026-09-10T00:00:00Z", "2026-10-10T00:00:00Z"
    before = list(scope_params)
    base_where, params = repo.results_where(
        scope, scope_params, location_id=location_id, country=country
    )
    assert scope_params == before
    apps = repo.cohort_applications(results_db, base_where, params, start, end)
    events = repo.cohort_event_counts(results_db, base_where, params, start, end)
    waiting = repo.qualified_waiting_count(results_db, base_where, params)
    by_location = repo.cohort_by_location(results_db, base_where, params, start, end, location_id)
    old_apps, old_events, old_waiting, old_by_location = old_results(
        results_db, scope, scope_params, location_id, country, start, end
    )
    assert as_dicts(apps) == as_dicts(old_apps)
    assert as_dicts(events) == as_dicts(old_events)
    assert waiting == old_waiting
    assert as_dicts(by_location) == as_dicts(old_by_location)


def test_results_counts_on_the_seeded_cohort(results_db: sqlite3.Connection) -> None:
    start, end = "2026-09-10T00:00:00Z", "2026-10-10T00:00:00Z"
    scope, scope_params = recruiter_scope()
    base_where, params = repo.results_where(scope, scope_params, location_id=None, country=None)
    apps = repo.cohort_applications(results_db, base_where, params, start, end)
    assert len(apps) == 3  # the out-of-scope and the earlier Application are left out
    assert {r["kind"]: r["n"] for r in repo.cohort_event_counts(
        results_db, base_where, params, start, end
    )} == {
        "review_requested": 2,
        "review_upheld": 1,
        "review_overturned": 1,
        "takeover_started": 1,
        "escalation_raised": 1,
    }  # fmt: skip
    assert repo.qualified_waiting_count(results_db, base_where, params) == 1
    one = repo.cohort_by_location(results_db, base_where, params, start, end, LOC_PUEBLA_CENTRO)
    assert [(r["location_id"], r["applications"], r["qualified"], r["knockouts"]) for r in one] == [
        (LOC_PUEBLA_CENTRO, 3, 1, 1)
    ]


# --- usage ledger -------------------------------------------------------------------------


def add_usage(conn: sqlite3.Connection, **kw: Any) -> None:
    row: dict[str, Any] = {
        "ts": iso(T0),
        "environment": "prod",
        "source": "live",
        "dataset": None,
        "kind": "run",
        "provider": "stub",
        "model": "m1",
        "input_tokens": 100,
        "est_cost_usd": 0.002,
        "status": "ok",
        "application_id": None,
        "session_id": None,
        "reconciled_cost_usd": None,
    }
    row.update(kw)
    conn.execute(
        f"INSERT INTO usage ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
        list(row.values()),
    )


@pytest.fixture
def ledger(seeded: sqlite3.Connection) -> sqlite3.Connection:
    plain = in_zone(seeded, "u1")
    qualified = in_zone(seeded, "u2")
    seeded.execute(
        "UPDATE applications SET qualified_at = ? WHERE id = ?", (iso(T0), qualified["id"])
    )
    slot = repo.open_slot_rows(seeded, qualified["id"], iso(T0))[0]["slot_id"]
    repo.insert_booking(
        seeded, "bk_u2", qualified["id"], slot, "chat", "bk_u2@criba.local", iso(T0)
    )
    add_usage(seeded, application_id=plain["id"], session_id="s1")
    add_usage(seeded, application_id=plain["id"], session_id="s1", reconciled_cost_usd=0.5)
    add_usage(seeded, application_id=qualified["id"], session_id="s2", status="error")
    add_usage(seeded, environment="sim", est_cost_usd=0.25)
    add_usage(seeded, ts=iso(T0 - timedelta(days=60)), est_cost_usd=9.0)  # out of the period
    add_usage(seeded, source="replay", dataset="d1", ts=iso(T0 - timedelta(hours=1)), model="m2")
    add_usage(seeded, source="replay", dataset="d2", ts=iso(T0), model="m1")
    add_usage(seeded, source="replay", dataset="d2", ts=iso(T0), model="m2", status="timeout")
    return seeded


@pytest.mark.parametrize(
    ("source", "dataset", "environment"),
    [
        ("live", None, "prod"),
        ("live", None, None),
        ("live", None, "sim"),
        ("replay", "d2", None),
        ("replay", None, None),
    ],
)
def test_usage_queries_match_the_old_inline_queries(
    ledger: sqlite3.Connection, source: str, dataset: str | None, environment: str | None
) -> None:
    start, end = "2026-09-10T00:00:00Z", "2026-10-10T00:00:00Z"
    where, params = repo.usage_where(
        start, end, source=source, dataset=dataset, environment=environment
    )
    assert (where, params) == old_usage_where(start, end, source, dataset, environment)
    assert as_dicts(repo.usage_cost_rows(ledger, where, params)) == as_dicts(
        ledger.execute(f"SELECT u.*, {OLD_COST} AS cost FROM usage u WHERE {where}", params)
    )
    reach = repo.usage_reach(ledger, where, params)
    old_reach = ledger.execute(
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
    assert dict(reach) == dict(old_reach)
    assert as_dicts(repo.usage_cost_by_environment(ledger, where, params)) == as_dicts(
        ledger.execute(
            f"SELECT u.environment, sum({OLD_COST}) AS cost FROM usage u WHERE {where}"
            " GROUP BY u.environment",
            params,
        )
    )


def test_usage_reach_and_cost_on_the_seeded_ledger(ledger: sqlite3.Connection) -> None:
    start, end = "2026-09-10T00:00:00Z", "2026-10-10T00:00:00Z"
    where, params = repo.usage_where(start, end, source="live", dataset=None, environment="prod")
    rows = repo.usage_cost_rows(ledger, where, params)
    assert sorted(r["cost"] for r in rows) == [0.002, 0.002, 0.5]  # reconciled wins
    reach = repo.usage_reach(ledger, where, params)
    assert (reach["conversations"], reach["qualified"], reach["interviews"]) == (2, 1, 1)
    where_all, params_all = repo.usage_where(
        start, end, source="live", dataset=None, environment=None
    )
    by_env = {
        r["environment"]: r["cost"]
        for r in repo.usage_cost_by_environment(ledger, where_all, params_all)
    }
    assert by_env == {"prod": pytest.approx(0.504), "sim": pytest.approx(0.25)}


def test_replay_dataset_selection(seeded: sqlite3.Connection) -> None:
    assert repo.latest_replay_dataset(seeded) is None
    add_usage(seeded, source="replay", dataset="d1", ts=iso(T0))
    add_usage(seeded, source="replay", dataset="d2", ts=iso(T0))  # same ts, higher id wins
    add_usage(seeded, source="replay", dataset="d0", ts=iso(T0 - timedelta(days=1)))
    add_usage(seeded, ts=iso(T0 + timedelta(days=1)))  # a live row is never a dataset
    latest = repo.latest_replay_dataset(seeded)
    assert latest is not None and latest["dataset"] == "d2"
    rows = repo.replay_cost_rows(seeded, "d1")
    assert [r["dataset"] for r in rows] == ["d1"]
    assert as_dicts(rows) == as_dicts(
        seeded.execute(
            f"SELECT u.*, {OLD_COST} AS cost FROM usage u WHERE u.source = 'replay'"
            " AND u.dataset = ?",
            ("d1",),
        )
    )
    assert repo.replay_cost_rows(seeded, "missing") == []
    assert repo.replay_cost_rows(seeded, None) == []
