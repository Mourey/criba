"""The slot lock's last line (ADR 0030): when the ``taken`` read misses a booking, the unique
index ``bookings_one_per_slot`` refuses the insert, ``sqlite3.IntegrityError`` comes out of
``repo.insert_booking`` and ``book_slot`` turns it into ``slot_unavailable``, rolling back the
whole booking, including the release of the Candidate's previous slot."""

from __future__ import annotations

import sqlite3

import pytest

from criba.db import repo
from criba.tools import ToolError, active_booking_id, book_slot
from criba.tools.booking import open_slots


def test_the_unique_index_turns_a_missed_race_into_slot_unavailable(
    tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = tools.new_app()
    b = tools.new_app()
    tools.approve(a)
    tools.approve(b)
    first, second = (row["slot_id"] for row in open_slots(tools.conn, a, now=tools.clock.now())[:2])
    booked_a = book_slot(tools.conn, a, first, via="chat", now=tools.clock.now())
    held_b = book_slot(tools.conn, b, second, via="chat", now=tools.clock.now())
    events_before = len(tools.events(b, "interview_slot_released"))

    # The read that normally refuses first is made to miss, as in a race it could.
    monkeypatch.setattr(repo, "slot_is_booked", lambda conn, slot_id: False)
    with pytest.raises(ToolError) as caught:
        book_slot(tools.conn, b, first, via="booking_page", now=tools.clock.now())
    assert caught.value.error == "slot_unavailable"
    assert isinstance(caught.value.__cause__, sqlite3.IntegrityError)

    # Nothing of the failed booking stays: b keeps its slot, its release event is rolled back.
    assert active_booking_id(tools.conn, a) == booked_a["booking_id"]
    assert active_booking_id(tools.conn, b) == held_b["booking_id"]
    assert len(tools.events(b, "interview_slot_released")) == events_before
    assert tools.app(b)["stage"] == "interview_booked"
