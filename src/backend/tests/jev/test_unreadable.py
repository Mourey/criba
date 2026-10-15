"""Unreadable messages (ADR 0063): each counts as a miss, and the third in a row hands the
conversation to the Recruiters, in code, with a fixed message and no agent run."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from criba.channel.copy import UNREADABLE_HANDOVER
from criba.db import repo
from criba.db.connection import iso
from criba.jev import JevFailed
from criba.jev.policy import UNREADABLE_RUN, Thresholds, is_unreadable, unreadable_state
from criba.jev.store import list_signals
from criba.tools import call_tool

from ..channel.conftest import candidate_for, start
from .conftest import FakeJev, reading

NONSENSE = (
    "Blok loka ksks aaa",
    "asd as sssskkk",
    "aksda asdas asassjqw weedyas",
    "alssda asdasd asd",
)


def send(client: TestClient, conv: str, text: str) -> None:
    body = {"message_id": str(uuid.uuid4()), "text": text}
    resp = client.post(f"/api/chat/conversations/{conv}/messages", json=body)
    assert resp.status_code == 202, resp.text


def application(conn, conv: str) -> dict[str, Any]:
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    assert app is not None
    return app


def events(conn, app_id: str, kind: str) -> list[dict[str, Any]]:
    return [e for e in repo.list_events(conn, app_id) if e["kind"] == kind]


def escalations(conn, app_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM queue_items WHERE application_id = ? AND kind = 'escalation' ORDER BY id",
        (app_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def outbound(conn, app_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM messages WHERE application_id = ? AND direction = 'outbound' ORDER BY id",
        (app_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def nonsense_jev(jev: FakeJev, *, value: float = 0.92) -> None:
    """Jev reads every message in ``NONSENSE`` as unreadable at ``value``; anything else as
    readable."""
    jev.answer = lambda request: (
        reading("unclear", 0.9, unreadable=value)
        if request.message in NONSENSE
        else reading("es", 1.0, unreadable=0.02)
    )


@pytest.fixture
def conv(client: TestClient, jev: FakeJev, clock) -> str:
    """A Mexican chat whose opener was answered, so the name question is pending."""
    nonsense_jev(jev)
    conv = start(client, country="MX", locale="es-MX")
    send(client, conv, "Hola")
    clock.advance(seconds=5)
    return conv


# --------------------------------------------------------------------------- the policy


def test_a_message_with_no_letter_is_readable_whatever_jev_says() -> None:
    for text in ("👍👍", "28045", "12/10/2026", "  "):
        assert unreadable_state(text, 0.99) == "readable"
        assert not is_unreadable(text, reading(unreadable=0.99))


def test_the_band_between_the_thresholds_is_uncertain_and_a_failure_too() -> None:
    t = Thresholds()
    assert unreadable_state("asd qwe", t.unreadable) == "unreadable"
    assert unreadable_state("asd qwe", t.readable) == "uncertain"
    assert unreadable_state("asd qwe", t.readable - 0.01) == "readable"
    assert unreadable_state("asd qwe", None) == "uncertain"


def test_typos_slang_and_names_are_readable_at_what_jev_gave_them() -> None:
    # The calibration's readings of the hard negatives (docs/evidence/jev-calibration.md).
    for text, value in (("repertidor", 0.02), ("simón", 0.02), ("Iñaki Etxeberria", 0.01)):
        assert unreadable_state(text, value) == "readable"


# --------------------------------------------------------------------------- three in a row


def test_three_unreadable_messages_hand_the_chat_to_the_recruiters(
    client, conn, clock, hermes, conv
) -> None:
    for text in NONSENSE[:3]:
        send(client, conv, text)
        clock.advance(seconds=5)
    app = application(conn, conv)
    assert app["status"] == "open" and app["control"] == "agent"
    assert repo.agent_on_hold(conn, app["id"])
    [item] = escalations(conn, app["id"])
    assert (item["reason"], item["status"]) == ("unreadable", "open")
    assert "Blok" not in (item["note"] or "")  # a fixed note, never the Candidate's words
    [raised] = events(conn, app["id"], "escalation_raised")
    assert raised["data"] == {"queue_item_id": item["id"], "reason": "unreadable"}
    # Each unreadable message counted as a miss on the name question, no agent tool needed.
    misses = events(conn, app["id"], "answer_invalid")
    assert [(e["data"]["attempts"], e["data"]["reason"]) for e in misses] == [
        (1, "unreadable"),
        (2, "unreadable"),
        (3, "unreadable"),
    ]
    assert {e["data"]["field"] for e in misses} == {"full_name"}
    assert app["invalid_attempts"] == 3
    # The agent ran for the opener and the first two; the third got the fixed message only.
    assert hermes.inputs == ["Hola", NONSENSE[0], NONSENSE[1]]
    last = outbound(conn, app["id"])[-1]
    assert last["body"] == UNREADABLE_HANDOVER["es-MX"] and last["sender"] == "system"
    # The signals carry the probability; the third message's row is there too.
    assert [s["unreadable"] for s in list_signals(conn, app["id"])][-3:] == [0.92] * 3


def test_a_fourth_message_during_the_hold_never_reaches_the_agent_or_jev(
    client, conn, clock, hermes, jev: FakeJev, conv
) -> None:
    for text in NONSENSE[:3]:
        send(client, conv, text)
        clock.advance(seconds=5)
    calls, replies = len(jev.requests), len(outbound(conn, application(conn, conv)["id"]))
    send(client, conv, "hola? sigues ahí")
    app = application(conn, conv)
    assert len(jev.requests) == calls  # not read: the Recruiters have the conversation
    assert len(hermes.inputs) == 3
    assert len(outbound(conn, app["id"])) == replies
    inbound = conn.execute(
        "SELECT body FROM messages WHERE application_id = ? AND direction = 'inbound'"
        " ORDER BY id DESC LIMIT 1",
        (app["id"],),
    ).fetchone()
    assert inbound["body"] == "hola? sigues ahí"  # stored for the Recruiter to read


def test_dismissing_the_escalation_gives_the_chat_back_to_the_agent(
    client, conn, clock, hermes, conv
) -> None:
    for text in NONSENSE[:3]:
        send(client, conv, text)
        clock.advance(seconds=5)
    app = application(conn, conv)
    [item] = escalations(conn, app["id"])
    dismiss(conn, app, item, clock.now())
    clock.advance(seconds=5)
    assert not repo.agent_on_hold(conn, app["id"])
    send(client, conv, NONSENSE[3])
    # The agent runs again, and the run starts over: one unreadable message is not three.
    assert hermes.inputs[-1] == NONSENSE[3]
    assert not repo.agent_on_hold(conn, app["id"])
    # It is still a fourth miss on the name, so the usual Escalation opens (no hold).
    assert [e["reason"] for e in escalations(conn, app["id"])] == [
        "unreadable",
        "repeated_invalid_answers",
    ]


def dismiss(conn, app: dict[str, Any], item: dict[str, Any], now) -> None:
    """What ``POST /api/queue/escalations/{id}`` with ``dismissed`` writes, minus the user (the
    endpoint's own tests are in tests/api/test_queue.py; this seed has no users)."""
    conn.execute(
        "UPDATE queue_items SET status = 'resolved', resolution = 'dismissed',"
        " resolved_at = ? WHERE id = ?",
        (iso(now), item["id"]),
    )
    repo.add_event(
        conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        kind="escalation_dismissed",
        actor="system",
        data={"queue_item_id": item["id"]},
        now=now,
    )
    conn.commit()


# --------------------------------------------------------------------------- what ends a run


def test_a_readable_message_ends_the_run(client, conn, clock, hermes, conv) -> None:
    for text in (NONSENSE[0], NONSENSE[1], "Me llamo Ana", NONSENSE[2], NONSENSE[3]):
        send(client, conv, text)
        clock.advance(seconds=5)
    app = application(conn, conv)
    assert not repo.agent_on_hold(conn, app["id"])
    assert hermes.inputs[-1] == NONSENSE[3]  # the agent still runs
    # The misses still add up on the pending question: the third raised the usual Escalation
    # (Phase 1 section 5.2), which does not hold the agent.
    assert app["invalid_attempts"] == 4
    [item] = escalations(conn, app["id"])
    assert item["reason"] == "repeated_invalid_answers" and "Blok" not in item["note"]


def test_an_uncertain_reading_or_a_failed_call_neither_counts_nor_ends_the_run(
    client, conn, clock, jev: FakeJev, conv
) -> None:
    send(client, conv, NONSENSE[0])
    clock.advance(seconds=5)
    jev.answer = lambda request: reading("es", 0.99, unreadable=0.30)  # garbled, half readable
    send(client, conv, "la nhets. weh que sdkj me edsiendes")
    clock.advance(seconds=5)
    jev.failure = JevFailed("timeout", kind="timeout", latency_ms=1200)
    send(client, conv, "zzz")
    clock.advance(seconds=5)
    jev.failure = None
    nonsense_jev(jev)
    send(client, conv, NONSENSE[1])
    clock.advance(seconds=5)
    app = application(conn, conv)
    assert not repo.agent_on_hold(conn, app["id"])  # two unreadable so far
    send(client, conv, NONSENSE[2])
    assert repo.agent_on_hold(conn, application(conn, conv)["id"])


def test_a_recorded_answer_ends_the_run(client, conn, clock, jev: FakeJev, conv) -> None:
    send(client, conv, NONSENSE[0])
    send(client, conv, NONSENSE[1])
    clock.advance(seconds=5)
    # An uncertain message is passed over by the run; the answer recorded from it ends it.
    jev.answer = lambda request: reading("es", 0.9, unreadable=0.35)
    send(client, conv, "Ana Lpz")
    nonsense_jev(jev)
    app = application(conn, conv)
    status, body = call_tool(
        conn,
        "record_answer",
        {"field": "full_name", "value": "Ana López"},
        app["id"],
        now=clock.now(),
    )
    assert status == 200 and body["ok"], body
    clock.advance(seconds=5)
    send(client, conv, NONSENSE[2])
    assert not repo.agent_on_hold(conn, app["id"])


def test_an_unreadable_message_the_agent_also_misses_counts_once(client, conn, clock, conv) -> None:
    send(client, conv, NONSENSE[0])
    app = application(conn, conv)
    status, body = call_tool(
        conn,
        "record_answer",
        {"field": "full_name", "value": "Blok"},  # the agent passed one word on
        app["id"],
        now=clock.now(),
    )
    assert status == 200 and not body["ok"], body
    assert body["attempt"] == 1
    assert application(conn, conv)["invalid_attempts"] == 1
    assert len(events(conn, app["id"], "answer_invalid")) == 1


def test_nothing_from_an_unreadable_message_is_recorded_even_if_it_passes_the_check(
    client, conn, clock, conv
) -> None:
    # "Blok loka ksks aaa" passes the name check (two to eight words of letters): Haiku
    # recorded it in a live run. The backend counted the message, so it is not recorded.
    send(client, conv, NONSENSE[0])
    app = application(conn, conv)
    status, body = call_tool(
        conn,
        "record_answer",
        {"field": "full_name", "value": "Blok Loka Ksks Aaa"},
        app["id"],
        now=clock.now(),
    )
    assert status == 200 and not body["ok"], body
    assert (body["reason"], body["attempt"]) == ("unreadable", 1)
    after = application(conn, conv)
    assert after["full_name"] is None and after["invalid_attempts"] == 1
    assert not events(conn, app["id"], "answer_recorded")


def test_a_readable_answer_the_agent_misses_still_counts(client, conn, clock, conv) -> None:
    send(client, conv, "Ana")  # readable, but only a first name
    app = application(conn, conv)
    status, body = call_tool(
        conn, "record_answer", {"field": "full_name", "value": "Ana"}, app["id"], now=clock.now()
    )
    assert status == 200 and not body["ok"], body
    assert application(conn, conv)["invalid_attempts"] == 1


def test_emoji_and_names_never_count_whatever_jev_says(
    client, conn, clock, jev: FakeJev, conv
) -> None:
    jev.answer = lambda request: reading(
        "unclear", 0.9, unreadable=0.99 if request.message == "👍👍" else 0.01
    )
    for text in ("👍👍", "👍👍", "Xochitl Huerta", "👍👍"):
        send(client, conv, text)
        clock.advance(seconds=5)
    app = application(conn, conv)
    assert not events(conn, app["id"], "answer_invalid")
    assert not repo.agent_on_hold(conn, app["id"])


def test_an_open_escalation_becomes_the_hold(client, conn, clock, hermes, conv) -> None:
    app = application(conn, conv)
    item = repo.open_queue_item(conn, app["id"], "escalation", reason="wants_human", note="n")
    for text in NONSENSE[:UNREADABLE_RUN]:
        send(client, conv, text)
        clock.advance(seconds=5)
    [held] = escalations(conn, app["id"])
    assert held["id"] == item["id"] and held["reason"] == "unreadable" and held["note"] == "n"
    assert repo.agent_on_hold(conn, app["id"])
    assert [e["data"]["reason"] for e in events(conn, app["id"], "escalation_raised")] == [
        "wants_human",
        "unreadable",
    ]


def test_the_fixed_message_follows_the_stored_language(client, conn, clock, jev: FakeJev) -> None:
    jev.answer = lambda request: reading("en", 1.0, 0.96)
    conv = start(client, country="ES", locale="es-ES")
    send(client, conv, "Hello, I'm looking for a delivery job")
    clock.advance(seconds=5)
    nonsense_jev(jev)
    for text in NONSENSE[:3]:
        send(client, conv, text)
        clock.advance(seconds=5)
    app = application(conn, conv)
    assert outbound(conn, app["id"])[-1]["body"] == UNREADABLE_HANDOVER["en"]


def test_after_screening_an_unreadable_run_still_hands_over_without_a_miss(
    client, conn, clock, conv
) -> None:
    app = application(conn, conv)
    conn.execute("UPDATE applications SET stage = 'approved' WHERE id = ?", (app["id"],))
    conn.commit()
    for text in NONSENSE[:3]:
        send(client, conv, text)
        clock.advance(seconds=5)
    assert repo.agent_on_hold(conn, app["id"])
    assert not events(conn, app["id"], "answer_invalid")


def test_the_scheduler_sends_no_nudge_during_the_hold(client, conn, clock, conv) -> None:
    from criba.scheduler import jobs

    for text in NONSENSE[:3]:
        send(client, conv, text)
        clock.advance(seconds=5)
    app = application(conn, conv)
    sent: list[Any] = []
    report = jobs.StalledReport()
    jobs._stalled_one(
        conn,
        app["id"],
        clock.now() + timedelta(hours=2),
        lambda *args, **kwargs: sent.append(args),
        None,
        report,
    )
    assert sent == []
