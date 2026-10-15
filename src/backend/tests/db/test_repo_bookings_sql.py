"""Direct tests for the repo functions that took over the SQL of ``tools/booking.py`` and
``scheduler/deferral.py``: the slot lock (the unique index ``bookings_one_per_slot`` raising
out of :func:`repo.insert_booking`), the offer expiry, and the held-action compare-and-set
claim, finish and failure record. The tool and scheduler tests cover the plain selects."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from criba.db import repo, transaction
from criba.db.connection import iso
from criba.db.seed import CLIENT_ID

T0 = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
PUEBLA_CENTRO = "zone_mx_puebla_centro"


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


def in_zone(conn: sqlite3.Connection, external_id: str) -> dict:
    """An Application whose Zone answer gives it covering Locations (and so slots)."""
    app = started(conn, external_id)
    repo.record_answer(conn, app["id"], "zone", PUEBLA_CENTRO, now=T0)
    return app


# --- bookings ----------------------------------------------------------------


def test_a_second_booking_of_a_slot_raises_integrity_error(seeded: sqlite3.Connection) -> None:
    a = in_zone(seeded, "c1")
    b = in_zone(seeded, "c2")
    slots = repo.open_slot_rows(seeded, a["id"], iso(T0))
    assert slots
    slot_id = slots[0]["slot_id"]
    assert [r["slot_id"] for r in repo.open_slot_rows(seeded, b["id"], iso(T0))] == [
        r["slot_id"] for r in slots
    ]
    assert not repo.slot_is_booked(seeded, slot_id)
    repo.insert_booking(seeded, "bk_a", a["id"], slot_id, "chat", "bk_a@criba.local", iso(T0))
    assert repo.slot_is_booked(seeded, slot_id)
    assert slot_id not in {r["slot_id"] for r in repo.open_slot_rows(seeded, b["id"], iso(T0))}

    # The lock: the unique index refuses a second standing booking, and db/ does not translate
    # the error. Inside a savepoint the failed insert rolls back alone.
    with pytest.raises(sqlite3.IntegrityError), transaction(seeded):
        repo.insert_booking(
            seeded, "bk_b", b["id"], slot_id, "booking_page", "bk_b@criba.local", iso(T0)
        )
    assert repo.current_booking(seeded, b["id"]) is None
    assert repo.current_booking_id(seeded, b["id"]) is None

    current = repo.current_booking(seeded, a["id"])
    assert current is not None
    assert (current["id"], current["slot_id"], current["ics_uid"]) == (
        "bk_a",
        slot_id,
        "bk_a@criba.local",
    )
    assert repo.current_booking_id(seeded, a["id"]) == "bk_a"
    assert repo.booking_is_standing(seeded, "bk_a")
    assert not repo.booking_is_standing(seeded, None)

    # Releasing frees the slot for the next Candidate.
    repo.release_booking(seeded, "bk_a", iso(T0 + timedelta(minutes=1)))
    assert not repo.booking_is_standing(seeded, "bk_a")
    assert repo.current_booking_id(seeded, a["id"]) is None
    assert not repo.slot_is_booked(seeded, slot_id)
    repo.insert_booking(
        seeded, "bk_b", b["id"], slot_id, "booking_page", "bk_b@criba.local", iso(T0)
    )
    assert repo.current_booking_id(seeded, b["id"]) == "bk_b"
    ics = repo.booking_for_ics(seeded, "bk_a")
    assert ics is not None
    assert (ics["status"], ics["released_at"]) == ("released", iso(T0 + timedelta(minutes=1)))
    assert repo.booking_for_ics(seeded, "bk_none") is None


def test_offers_are_valid_until_they_expire(seeded: sqlite3.Connection) -> None:
    app = in_zone(seeded, "c1")
    other = in_zone(seeded, "c2")
    slot_id = repo.open_slot_rows(seeded, app["id"], iso(T0))[0]["slot_id"]
    later = iso(T0 + timedelta(hours=48))
    repo.insert_slot_offer(seeded, "so_1", app["id"], slot_id, iso(T0), later)
    offer = repo.offered_slot(seeded, "so_1", app["id"], iso(T0))
    assert offer is not None and offer["slot_id"] == slot_id
    assert repo.offered_slot(seeded, "so_1", other["id"], iso(T0)) is None  # not offered to it
    assert repo.offered_slot(seeded, "so_1", app["id"], later) is None  # expired by then

    repo.expire_slot_offers(seeded, app["id"], iso(T0 + timedelta(minutes=5)))
    assert repo.offered_slot(seeded, "so_1", app["id"], iso(T0 + timedelta(minutes=5))) is None
    assert repo.offered_slot(seeded, "so_1", app["id"], iso(T0)) is not None  # ended, not deleted


def test_the_invite_reads_messages_and_booking_events(seeded: sqlite3.Connection) -> None:
    app = started(seeded, "c1")
    assert repo.max_message_id(seeded, app["id"]) == 0
    assert repo.first_agent_message_after(seeded, app["id"], 0) is None
    common = {
        "candidate_id": app["candidate_id"],
        "application_id": app["id"],
        "channel": "webchat",
        "now": T0,
    }
    inbound, _ = repo.add_message(
        seeded, direction="inbound", sender="candidate", body="hola", **common
    )
    assert repo.max_message_id(seeded, app["id"]) == inbound["id"]
    ids = {"candidate_id": app["candidate_id"], "application_id": app["id"], "now": T0}
    repo.add_event(
        seeded,
        kind="interview_booked",
        actor="agent",
        data={"booking_id": "bk_1", "invite_after_message": inbound["id"]},
        **ids,
    )
    assert repo.latest_invite_booking_event(seeded, app["id"], inbound["id"]) is None
    reply, _ = repo.add_message(
        seeded, direction="outbound", sender="agent", body="listo", **common
    )
    row = repo.latest_invite_booking_event(seeded, app["id"], reply["id"])
    assert row is not None and '"bk_1"' in row["data"]
    assert repo.first_agent_message_after(seeded, app["id"], inbound["id"]) == reply["id"]
    assert repo.first_agent_message_after(seeded, app["id"], reply["id"]) is None


# --- held actions --------------------------------------------------------------


def held_id(conn: sqlite3.Connection, application_id: str) -> int:
    rows = repo.held_action_history(conn, application_id)
    assert len(rows) == 1
    return int(rows[0]["id"])


def test_an_action_is_held_once_and_listed_soonest_first(seeded: sqlite3.Connection) -> None:
    early = started(seeded, "c1")
    late = started(seeded, "c2")
    repo.hold_action(seeded, late["id"], "approved", iso(T0 + timedelta(hours=2)), iso(T0))
    repo.hold_action(seeded, early["id"], "approved", iso(T0 + timedelta(hours=1)), iso(T0))
    repo.hold_action(seeded, early["id"], "approved", iso(T0 + timedelta(hours=9)), iso(T0))
    assert [r["application_id"] for r in repo.pending_held_actions(seeded)] == [
        early["id"],
        late["id"],
    ]
    assert [r["application_id"] for r in repo.waiting_held_actions(seeded)] == [
        early["id"],
        late["id"],
    ]
    row = repo.held_action_history(seeded, early["id"])[0]
    assert (row["not_before"], row["attempts"], row["status"]) == (
        iso(T0 + timedelta(hours=1)),
        0,
        "held",
    )
    repo.reschedule_held_action(seeded, row["id"], iso(T0 + timedelta(hours=3)), iso(T0))
    assert [r["application_id"] for r in repo.waiting_held_actions(seeded)] == [
        late["id"],
        early["id"],
    ]


def test_the_claim_is_compare_and_set_on_attempts(seeded: sqlite3.Connection) -> None:
    app = started(seeded, "c1")
    repo.hold_action(seeded, app["id"], "approved", iso(T0), iso(T0))
    held = held_id(seeded, app["id"])
    assert repo.claim_held_action(seeded, held, 0, iso(T0)) is True
    assert repo.claim_held_action(seeded, held, 0, iso(T0)) is False  # another runner won
    assert repo.held_action_history(seeded, app["id"])[0]["attempts"] == 1

    repo.record_held_action_failure(seeded, held, "held", "RuntimeError: boom", iso(T0))
    row = repo.held_action_history(seeded, app["id"])[0]
    assert (row["status"], row["last_error"]) == ("held", "RuntimeError: boom")
    assert repo.claim_held_action(seeded, held, 1, iso(T0)) is True
    repo.record_held_action_failure(seeded, held, "failed", "RuntimeError: again", iso(T0))
    row = repo.held_action_history(seeded, app["id"])[0]
    assert (row["status"], row["attempts"], row["last_error"]) == (
        "failed",
        2,
        "RuntimeError: again",
    )
    # Once out of ``held`` nothing moves the row any more.
    assert repo.claim_held_action(seeded, held, 2, iso(T0)) is False
    assert repo.finish_held_action(seeded, held, "dispatched", iso(T0)) is False
    repo.record_held_action_failure(seeded, held, "held", "late", iso(T0))
    assert repo.held_action_history(seeded, app["id"])[0]["status"] == "failed"
    assert repo.pending_held_actions(seeded) == []


def test_finishing_is_compare_and_set_on_status(seeded: sqlite3.Connection) -> None:
    app = started(seeded, "c1")
    repo.hold_action(seeded, app["id"], "approved", iso(T0), iso(T0))
    held = held_id(seeded, app["id"])
    assert repo.finish_held_action(seeded, held, "dispatched", iso(T0)) is True
    assert repo.finish_held_action(seeded, held, "dropped", iso(T0)) is False
    assert repo.held_action_history(seeded, app["id"])[0]["status"] == "dispatched"
    # A new hold for the same action is allowed once the old row left ``held``.
    repo.hold_action(seeded, app["id"], "approved", iso(T0), iso(T0))
    assert [r["status"] for r in repo.held_action_history(seeded, app["id"])] == [
        "dispatched",
        "held",
    ]
