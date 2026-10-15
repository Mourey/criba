"""offer_interview_slots, book_interview, the slot lock, the .ics and booking links
(spec 002 task 10, ADR 0030, Q41)."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from criba.db import connect, repo
from criba.tools import (
    BookingTokenError,
    BookingTokenExpired,
    ToolError,
    active_booking_id,
    book_slot,
    ics_for_booking,
    make_booking_token,
    verify_booking_token,
)

KEY = b"k" * 32


def offered_slot_ids(tools, refs) -> set[str]:
    return {
        tools.conn.execute("SELECT slot_id FROM slot_offers WHERE ref = ?", (ref,)).fetchone()[0]
        for ref in refs
    }


@pytest.mark.parametrize("stage", ["greeting", "confirm", "qualified"])
def test_offer_before_approval_is_not_approved(tools, stage: str) -> None:
    app_id = tools.new_app()
    if stage != "greeting":
        tools.answer_all(app_id)
    if stage == "qualified":
        tools.ok(app_id, "finish_screening")
    err = tools.err(app_id, "offer_interview_slots")
    assert err["error"] == "not_approved"
    assert not tools.events(app_id, "slots_offered")


def test_offer_needs_approval_in_the_database_not_just_the_stage(tools) -> None:
    """Q41: a system turn carries no authority; only a recorded Approval counts."""
    app_id = tools.new_app()
    tools.answer_all(app_id)
    tools.ok(app_id, "finish_screening")
    tools.conn.execute("UPDATE applications SET stage = 'approved' WHERE id = ?", (app_id,))
    assert tools.err(app_id, "offer_interview_slots")["error"] == "not_approved"


def test_offer_after_approval_gives_three_distinct_slots(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    assert tools.ok(app_id, "get_application")["approved"] is True
    value = tools.ok(app_id, "offer_interview_slots")
    slots = value["slots"]
    assert len(slots) == 3
    assert len({s["label"] for s in slots}) == 3
    assert len({s["slot_ref"] for s in slots}) == 3
    first = slots[0]
    assert first["label"] == "lun 12 · 10:00"  # Monday 10:00 Madrid
    assert first["interview"]["local_label"] == first["label"]
    assert first["interview"]["location_name"].startswith("Sazón Madrid")
    assert value["other_label"] == "Otro horario"
    assert value["booking_url"].startswith("https://empleo.gruposazon.example/book/")
    covering = set(repo.application_location_ids(tools.conn, app_id))
    rows = tools.conn.execute(
        "SELECT s.location_id FROM slot_offers o JOIN interview_slots s ON s.id = o.slot_id"
        " WHERE o.application_id = ?",
        (app_id,),
    ).fetchall()
    assert {r[0] for r in rows} <= covering
    (event,) = tools.events(app_id, "slots_offered")
    assert event["slot_refs"] == [s["slot_ref"] for s in slots]


def test_english_labels(tools) -> None:
    app_id = tools.new_app(language="en")
    tools.approve(app_id)
    value = tools.ok(app_id, "offer_interview_slots")
    assert value["slots"][0]["label"] == "Mon 12 · 10:00"
    assert value["other_label"] == "Another time"


def test_no_open_slots(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    tools.conn.execute("UPDATE interview_slots SET cancelled_at = '2026-10-09T09:00:00Z'")
    assert tools.err(app_id, "offer_interview_slots")["error"] == "no_slots"


def test_book_an_offered_slot(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    offer = tools.ok(app_id, "offer_interview_slots")["slots"][1]
    value = tools.ok(app_id, "book_interview", slot_ref=offer["slot_ref"])
    assert value["stage"] == "interview_booked"
    assert value["interview"] == offer["interview"]
    assert value["released_previous"] is False
    assert value["ics_sent"] is True  # web chat: the channel attaches it to the next message
    app = tools.app(app_id)
    assert app["stage"] == "interview_booked"
    got = tools.ok(app_id, "get_application")
    assert got["interview"] == offer["interview"]
    assert got["approved"] is True
    (event,) = tools.events(app_id, "interview_booked")
    assert event["via"] == "chat"
    assert event["invite_after_message"] == 0  # no message yet in this Application
    # Booking supersedes the other offers.
    other = tools.err(app_id, "book_interview", slot_ref=offer["slot_ref"].replace("so_", "zz_"))
    assert other["error"] == "slot_not_offered"


def test_booking_needs_a_ref_offered_to_this_application(tools) -> None:
    a = tools.new_app()
    b = tools.new_app()
    tools.approve(a)
    tools.approve(b)
    ref_for_a = tools.ok(a, "offer_interview_slots")["slots"][0]["slot_ref"]
    assert tools.err(b, "book_interview", slot_ref=ref_for_a)["error"] == "slot_not_offered"
    assert tools.err(b, "book_interview", slot_ref="so_made_up")["error"] == "slot_not_offered"


def test_offered_refs_expire(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    ref = tools.ok(app_id, "offer_interview_slots")["slots"][0]["slot_ref"]
    tools.clock.advance(hours=49)
    assert tools.err(app_id, "book_interview", slot_ref=ref)["error"] == "slot_not_offered"


def test_two_candidates_cannot_book_the_same_slot(tools) -> None:
    a = tools.new_app()
    b = tools.new_app()
    tools.approve(a)
    tools.approve(b)  # same Zone: the same slots are offered to both
    ref_a = tools.ok(a, "offer_interview_slots")["slots"][0]
    ref_b = tools.ok(b, "offer_interview_slots")["slots"][0]
    assert ref_a["interview"] == ref_b["interview"]
    tools.ok(a, "book_interview", slot_ref=ref_a["slot_ref"])
    err = tools.err(b, "book_interview", slot_ref=ref_b["slot_ref"])
    assert err["error"] == "slot_unavailable"
    assert tools.app(b)["stage"] == "approved"
    # The next offer to b skips the booked slot.
    fresh = tools.ok(b, "offer_interview_slots")["slots"]
    booked = offered_slot_ids(tools, [ref_a["slot_ref"]])
    assert not booked & offered_slot_ids(tools, [s["slot_ref"] for s in fresh])


def test_concurrent_bookings_lock_the_slot(tools, db_path) -> None:
    """Separate connections in separate threads, as two requests would be."""
    apps = [tools.new_app() for _ in range(6)]
    for app_id in apps:
        tools.approve(app_id)
    slot_id = tools.conn.execute(
        "SELECT s.id FROM interview_slots s JOIN application_locations al"
        " ON al.location_id = s.location_id WHERE al.application_id = ?"
        " ORDER BY s.starts_at LIMIT 1",
        (apps[0],),
    ).fetchone()[0]
    barrier = threading.Barrier(len(apps))
    results: dict[str, str] = {}

    def book(app_id: str) -> None:
        conn = connect(db_path)
        try:
            barrier.wait()
            book_slot(conn, app_id, slot_id, via="booking_page", now=tools.clock.now())
            results[app_id] = "booked"
        except ToolError as exc:
            results[app_id] = exc.error
        finally:
            conn.close()

    threads = [threading.Thread(target=book, args=(a,)) for a in apps]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values()) == ["booked"] + ["slot_unavailable"] * (len(apps) - 1)
    count = tools.conn.execute(
        "SELECT count(*) FROM bookings WHERE slot_id = ? AND status = 'booked'", (slot_id,)
    ).fetchone()[0]
    assert count == 1


def test_reschedule_keeps_the_booking_until_a_new_slot_is_booked(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    first = tools.ok(app_id, "offer_interview_slots")["slots"][0]
    tools.ok(app_id, "book_interview", slot_ref=first["slot_ref"])
    assert tools.err(app_id, "offer_interview_slots")["error"] == "wrong_stage"
    second = tools.ok(app_id, "offer_interview_slots", reschedule=True)["slots"]
    held = offered_slot_ids(tools, [first["slot_ref"]])
    assert not held & offered_slot_ids(tools, [s["slot_ref"] for s in second])
    old_booking = active_booking_id(tools.conn, app_id)
    assert old_booking is not None  # still booked while choosing
    value = tools.ok(app_id, "book_interview", slot_ref=second[0]["slot_ref"])
    assert value["released_previous"] is True
    assert value["interview"] == second[0]["interview"]
    statuses = tools.conn.execute(
        "SELECT status FROM bookings WHERE application_id = ? ORDER BY created_at, id", (app_id,)
    ).fetchall()
    assert sorted(r[0] for r in statuses) == ["booked", "released"]
    assert tools.events(app_id, "interview_rescheduled")[0]["previous_slot_id"]
    assert tools.events(app_id, "interview_slot_released")[0]["reason"] == "rescheduled"


def test_booking_the_held_slot_again_is_a_no_op(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    tools.ok(app_id, "offer_interview_slots")
    slot_id = tools.conn.execute(
        "SELECT slot_id FROM slot_offers WHERE application_id = ? LIMIT 1", (app_id,)
    ).fetchone()[0]
    now = tools.clock.now()
    first = book_slot(tools.conn, app_id, slot_id, via="booking_page", now=now)
    again = book_slot(tools.conn, app_id, slot_id, via="booking_page", now=now)
    assert again["booking_id"] == first["booking_id"]
    assert again["released_previous"] is False


def test_rejection_releases_the_slot_and_blocks_booking(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    ref = tools.ok(app_id, "offer_interview_slots")["slots"][0]["slot_ref"]
    repo.close_application(
        tools.conn, app_id, "rejection", rejection_reason="role_filled", actor="user",
        user_id="user_rec", now=tools.clock.now(),
    )  # fmt: skip
    assert tools.err(app_id, "book_interview", slot_ref=ref)["error"] == "application_closed"
    assert tools.err(app_id, "offer_interview_slots")["error"] == "application_closed"


# --- .ics -------------------------------------------------------------------


def test_ics_invite(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    ref = tools.ok(app_id, "offer_interview_slots")["slots"][0]["slot_ref"]
    tools.ok(app_id, "book_interview", slot_ref=ref)
    booking_id = active_booking_id(tools.conn, app_id)
    ics = ics_for_booking(tools.conn, booking_id)
    assert ics.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
    assert ics.endswith("END:VCALENDAR\r\n")
    assert f"UID:{booking_id}@criba.local" in ics
    assert "DTSTART:20261012T080000Z" in ics  # 10:00 Madrid (CEST)
    assert "DTEND:20261012T083000Z" in ics
    assert "STATUS:CONFIRMED" in ics
    assert "SUMMARY:Entrevista con Grupo Sazón · Sazón Madrid" in ics
    assert "LOCATION:(synthetic) Calle Ejemplo" in ics
    assert "\\," in ics  # commas in the address are escaped
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))


def test_ics_for_a_released_booking_is_cancelled(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    ref = tools.ok(app_id, "offer_interview_slots")["slots"][0]["slot_ref"]
    tools.ok(app_id, "book_interview", slot_ref=ref)
    booking_id = active_booking_id(tools.conn, app_id)
    tools.ok(app_id, "record_backout", kind="withdrawn")
    ics = ics_for_booking(tools.conn, booking_id)
    assert "METHOD:CANCEL" in ics
    assert "STATUS:CANCELLED" in ics


# --- booking links ----------------------------------------------------------


def test_booking_token_round_trip(clock) -> None:
    now = clock.now()
    token = make_booking_token("app_123", now + timedelta(days=7), secret=KEY)
    assert token.startswith("eyJhcHAiOiJ")  # base64url of {"app":"
    assert verify_booking_token(token, now=now, secret=KEY) == "app_123"


def test_booking_token_expired_or_tampered(clock) -> None:
    now = clock.now()
    token = make_booking_token("app_123", now + timedelta(hours=1), secret=KEY)
    with pytest.raises(BookingTokenExpired):
        verify_booking_token(token, now=now + timedelta(hours=2), secret=KEY)
    with pytest.raises(BookingTokenError):
        verify_booking_token(token, now=now, secret=b"other")
    _, signature = token.split(".")
    forged = make_booking_token("app_999", now + timedelta(hours=1), secret=b"x").split(".")[0]
    with pytest.raises(BookingTokenError):
        verify_booking_token(f"{forged}.{signature}", now=now, secret=KEY)
    for junk in ["", "abc", "a.b.c", "!!!.???"]:
        with pytest.raises(BookingTokenError):
            verify_booking_token(junk, now=now, secret=KEY)


def test_offered_booking_url_verifies_with_the_derived_secret(tools) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    url = tools.ok(app_id, "offer_interview_slots")["booking_url"]
    token = url.rsplit("/", 1)[1]
    assert verify_booking_token(token, now=tools.clock.now()) == app_id
    with pytest.raises(BookingTokenExpired):
        verify_booking_token(token, now=tools.clock.now() + timedelta(days=8))


def test_ics_is_not_sent_on_whatsapp(tools) -> None:
    """The WhatsApp adapter is a stub with no document message: no invite, so ics_sent is
    false and the booking event names no message to attach it to."""
    app_id = tools.new_app()
    tools.conn.execute("UPDATE applications SET channel = 'whatsapp' WHERE id = ?", (app_id,))
    tools.approve(app_id)
    ref = tools.ok(app_id, "offer_interview_slots")["slots"][0]["slot_ref"]
    value = tools.ok(app_id, "book_interview", slot_ref=ref)
    assert value["ics_sent"] is False
    (event,) = tools.events(app_id, "interview_booked")
    assert "invite_after_message" not in event


def test_ics_is_not_sent_without_a_signing_secret(tools, monkeypatch) -> None:
    app_id = tools.new_app()
    tools.approve(app_id)
    ref = tools.ok(app_id, "offer_interview_slots")["slots"][0]["slot_ref"]
    monkeypatch.delenv("CRIBA_TOOLS_SECRET_FILE")
    assert tools.ok(app_id, "book_interview", slot_ref=ref)["ics_sent"] is False
