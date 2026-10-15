"""Direct tests for the repo functions that took over the SQL of ``api/queue.py``,
``tools/application.py`` and ``scheduler/jobs.py``: the ones with a condition worth pinning
(compare-and-set, joins with ``NOT EXISTS``, flag clearing, cutoffs). The callers' own tests
cover the plain selects."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from criba.db import repo
from criba.db.connection import iso
from criba.db.seed import CLIENT_ID, seed_users

T0 = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
PUEBLA_CENTRO = "zone_mx_puebla_centro"
REC = "usr_rec_puebla"
OPERATOR = "usr_operator"


def everything() -> tuple[str, list]:
    """The Operator's scope (``api.present.scope_sql``): no filter."""
    return "1 = 1", []


@pytest.fixture
def users(seeded: sqlite3.Connection) -> sqlite3.Connection:
    seed_users(seeded, "synthetic-test-password", now=T0)
    return seeded


def started(conn: sqlite3.Connection, external_id: str) -> dict:
    candidate, _ = repo.get_or_create_candidate(
        conn,
        client_id=CLIENT_ID,
        channel="webchat",
        external_id=external_id,
        phone=None,
        language="es-MX",
        now=T0,
    )
    app, _ = repo.resolve_application(conn, candidate["id"], "webchat", now=T0)
    return app


def qualified(conn: sqlite3.Connection, external_id: str) -> dict:
    app = started(conn, external_id)
    repo.record_answer(conn, app["id"], "zone", PUEBLA_CENTRO, now=T0)
    return repo.mark_qualified(conn, app["id"], now=T0)


# --- queue ------------------------------------------------------------------


def test_resolving_a_queue_item_is_compare_and_set(users: sqlite3.Connection) -> None:
    app = started(users, "c1")
    item = repo.open_queue_item(users, app["id"], "escalation", reason="other", now=T0)
    assert repo.has_open_queue_item(users, app["id"], "escalation")
    assert repo.queue_item_status(users, item["id"]) == "open"
    assert repo.resolve_queue_item(users, item["id"], "dismissed", REC, "note", T0) is True
    assert repo.resolve_queue_item(users, item["id"], "takeover", OPERATOR, None, T0) is False
    row = repo.get_queue_item_row(users, item["id"])
    assert (row["status"], row["resolution"], row["resolved_by"]) == ("resolved", "dismissed", REC)
    assert (row["resolution_note"], row["resolved_at"]) == ("note", iso(T0))
    assert repo.queue_item_status(users, item["id"]) == "resolved"
    assert not repo.has_open_queue_item(users, app["id"], "escalation")


def test_a_scoped_queue_item_matches_id_kind_and_scope(seeded: sqlite3.Connection) -> None:
    app = started(seeded, "c1")
    item = repo.open_queue_item(seeded, app["id"], "review", now=T0)
    found = repo.find_scoped_queue_item(seeded, item["id"], "review", *everything())
    assert found is not None and found["id"] == item["id"]
    assert repo.find_scoped_queue_item(seeded, item["id"], "approval", *everything()) is None
    assert repo.find_scoped_queue_item(seeded, item["id"], "review", "a.id = ?", ["nope"]) is None


def test_queue_listing_filters_orders_and_reads_one_extra_row(users: sqlite3.Connection) -> None:
    seeded = users
    apps = [started(seeded, f"c{i}") for i in range(3)]
    items = [
        repo.open_queue_item(
            seeded,
            a["id"],
            kind,
            reason="other" if kind == "escalation" else None,
            now=T0 + timedelta(minutes=i),
        )
        for i, (a, kind) in enumerate(
            zip(apps, ("escalation", "review", "escalation"), strict=True)
        )
    ]
    page = repo.list_queue_items(
        seeded, "1 = 1", [], status="open", kind=None, location_id=None, limit=2, offset=0
    )
    assert [r["id"] for r in page] == [i["id"] for i in items]  # limit + 1 rows, oldest first
    escalations = repo.list_queue_items(
        seeded,
        "1 = 1",
        [],
        status="open",
        kind=["escalation"],
        location_id=None,
        limit=50,
        offset=1,
    )
    assert [r["id"] for r in escalations] == [items[2]["id"]]
    repo.record_answer(seeded, apps[0]["id"], "zone", PUEBLA_CENTRO, now=T0)
    located = repo.list_queue_items(
        seeded,
        "1 = 1",
        [],
        status="open",
        kind=None,
        location_id=repo.application_location_ids(seeded, apps[0]["id"])[0],
        limit=50,
        offset=0,
    )
    assert [r["id"] for r in located] == [items[0]["id"]]
    for item, resolution, minute in ((items[0], "dismissed", 5), (items[1], "upheld", 9)):
        at = T0 + timedelta(minutes=minute)
        assert repo.resolve_queue_item(seeded, item["id"], resolution, REC, None, at)
    resolved = repo.list_queue_items(
        seeded, "1 = 1", [], status="resolved", kind=None, location_id=None, limit=50, offset=0
    )
    assert [r["id"] for r in resolved] == [items[1]["id"], items[0]["id"]]  # newest first


def test_the_stream_reads_created_and_resolved_items_after_their_cursors(
    users: sqlite3.Connection,
) -> None:
    seeded = users
    assert repo.max_queue_item_rowid(seeded) == 0
    app = started(seeded, "c1")
    first = repo.open_queue_item(seeded, app["id"], "escalation", reason="other", now=T0)
    cursor = repo.max_queue_item_rowid(seeded)
    second = repo.open_queue_item(seeded, app["id"], "review", now=T0)
    created = repo.queue_items_created_after(seeded, cursor, *everything())
    assert [(r["id"], r["rid"]) for r in created] == [(second["id"], cursor + 1)]
    assert repo.resolve_queue_item(seeded, first["id"], "dismissed", REC, None, T0)
    later = iso(T0 + timedelta(seconds=1))
    assert [r["id"] for r in repo.queue_items_resolved_since(seeded, iso(T0), *everything())] == [
        first["id"]
    ]
    assert repo.queue_items_resolved_since(seeded, later, *everything()) == []


def test_only_rejections_outside_the_queue_are_direct(users: sqlite3.Connection) -> None:
    cursor = repo.max_event_id(users)
    via_queue = qualified(users, "c1")  # its Rejection resolves the open Approval
    direct = started(users, "c2")  # no Approval item to resolve
    for app in (via_queue, direct):
        repo.close_application(
            users,
            app["id"],
            "rejection",
            rejection_reason="no_show",
            actor="user",
            user_id="usr_rec_puebla",
            now=T0,
        )
    rows = repo.direct_rejections_after(users, cursor, *everything())
    assert [r["application_id"] for r in rows] == [direct["id"]]
    assert repo.direct_rejections_after(users, rows[0]["id"], *everything()) == []
    assert repo.max_event_id(users) >= rows[0]["id"]


# --- control, sessions, erasure --------------------------------------------


def test_clearing_control_hands_the_chat_to_the_agent(users: sqlite3.Connection) -> None:
    app = started(users, "c1")
    users.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'usr_rec_puebla',"
        " control_since = ? WHERE id = ?",
        (iso(T0), app["id"]),
    )
    repo.clear_control(users, app["id"])
    after = repo.require_application(users, app["id"])
    assert (after["control"], after["control_user_id"], after["control_since"]) == (
        "agent",
        None,
        None,
    )


def test_a_session_is_alive_until_revoked_expired_or_its_user_deactivated(
    users: sqlite3.Connection,
) -> None:
    expires = iso(T0 + timedelta(hours=1))
    users.execute(
        "INSERT INTO auth_sessions (id, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        ("h1", "usr_rec_puebla", iso(T0), expires),
    )
    assert repo.session_is_alive(users, "h1", T0)
    assert not repo.session_is_alive(users, "h1", T0 + timedelta(hours=1))
    assert not repo.session_is_alive(users, "missing", T0)
    users.execute("UPDATE users SET active = 0 WHERE id = 'usr_rec_puebla'")
    assert not repo.session_is_alive(users, "h1", T0)
    users.execute("UPDATE users SET active = 1 WHERE id = 'usr_rec_puebla'")
    users.execute("UPDATE auth_sessions SET revoked_at = ? WHERE id = 'h1'", (iso(T0),))
    assert not repo.session_is_alive(users, "h1", T0)


def test_an_erasure_request_keeps_the_first_time(seeded: sqlite3.Connection) -> None:
    app = started(seeded, "c1")
    repo.stamp_erasure_request(seeded, app["candidate_id"], now=T0)
    repo.stamp_erasure_request(seeded, app["candidate_id"], now=T0 + timedelta(days=1))
    row = seeded.execute(
        "SELECT erasure_requested_at, updated_at FROM candidates WHERE id = ?",
        (app["candidate_id"],),
    ).fetchone()
    assert tuple(row) == (iso(T0), iso(T0 + timedelta(days=1)))


# --- screening reads -------------------------------------------------------


def test_the_live_zone_choice_ends_at_a_reopen_or_a_recorded_zone(
    seeded: sqlite3.Connection,
) -> None:
    app = started(seeded, "c1")
    ids = {"candidate_id": app["candidate_id"], "application_id": app["id"], "now": T0}
    assert repo.live_zone_choice(seeded, app["id"]) is None
    repo.add_event(
        seeded, kind="answer_ambiguous", actor="agent", data={"field": "zone", "n": 1}, **ids
    )
    repo.add_event(
        seeded, kind="answer_ambiguous", actor="agent", data={"field": "name", "n": 2}, **ids
    )
    row = repo.live_zone_choice(seeded, app["id"])
    assert row is not None and '"n":1' in row["data"].replace(" ", "")
    repo.add_event(
        seeded, kind="answer_recorded", actor="agent", data={"field": "full_name"}, **ids
    )
    assert repo.live_zone_choice(seeded, app["id"]) is not None
    repo.add_event(seeded, kind="answer_recorded", actor="agent", data={"field": "zone"}, **ids)
    assert repo.live_zone_choice(seeded, app["id"]) is None


def test_an_unreadable_miss_is_found_by_its_message(seeded: sqlite3.Connection) -> None:
    app = started(seeded, "c1")
    ids = {"candidate_id": app["candidate_id"], "application_id": app["id"], "now": T0}
    repo.add_event(
        seeded,
        kind="answer_invalid",
        actor="system",
        data={"message_id": 7, "reason": "invalid"},
        **ids,
    )
    assert not repo.counted_unreadable(seeded, app["id"], 7)
    repo.add_event(
        seeded,
        kind="answer_invalid",
        actor="system",
        data={"message_id": 7, "reason": "unreadable"},
        **ids,
    )
    assert repo.counted_unreadable(seeded, app["id"], 7)
    assert not repo.counted_unreadable(seeded, app["id"], 8)


# --- scheduling --------------------------------------------------------------


def test_flags_are_due_at_their_cutoffs_and_cleared_when_stale(users: sqlite3.Connection) -> None:
    waiting = qualified(users, "c1")
    claimed = qualified(users, "c2")
    repo.claim_application(users, claimed["id"], "usr_rec_puebla", now=T0)
    assert [r["id"] for r in repo.qualified_waiting_due(users, iso(T0))] == [waiting["id"]]
    assert repo.qualified_waiting_due(users, iso(T0 - timedelta(seconds=1))) == []
    idle = repo.claimed_idle_due(users, iso(T0))
    assert [(r["id"], r["claim_user_id"], r["last_action"]) for r in idle] == [
        (claimed["id"], "usr_rec_puebla", iso(T0))
    ]
    assert repo.claimed_idle_due(users, iso(T0 - timedelta(seconds=1))) == []

    repo.set_qualified_waiting(users, waiting["id"])
    repo.set_claimed_idle(users, claimed["id"])
    assert repo.qualified_waiting_due(users, iso(T0)) == []
    assert repo.claimed_idle_due(users, iso(T0)) == []
    assert repo.clear_stale_flags(users) == 0  # both still apply

    # Claiming and releasing clear the flags themselves; set them again to leave them stale.
    repo.claim_application(users, waiting["id"], "usr_rec_puebla", now=T0)  # waiting ends
    repo.release_claim(users, claimed["id"], "usr_rec_puebla", now=T0)  # idle ends
    repo.set_qualified_waiting(users, waiting["id"])
    repo.set_claimed_idle(users, claimed["id"])
    assert repo.clear_stale_flags(users) == 2
    flags = users.execute(
        "SELECT qualified_waiting, claimed_idle FROM applications WHERE id IN (?, ?)",
        (waiting["id"], claimed["id"]),
    ).fetchall()
    assert all(tuple(f) == (0, 0) for f in flags)


def test_the_stalled_ladder_columns(seeded: sqlite3.Connection) -> None:
    app = started(seeded, "c1")
    assert repo.screening_application_ids(seeded) == [app["id"]]
    repo.set_stalled_since(seeded, app["id"], iso(T0))
    repo.set_nudges_sent(seeded, app["id"], 2)
    row = repo.require_application(seeded, app["id"])
    assert (row["stalled_since"], row["nudges_sent"]) == (iso(T0), 2)
    repo.reset_stalled(seeded, app["id"])
    row = repo.require_application(seeded, app["id"])
    assert (row["stalled_since"], row["nudges_sent"]) == (None, 0)
    repo.close_application(seeded, app["id"], "withdrawn", actor="candidate", now=T0)
    assert repo.screening_application_ids(seeded) == []
