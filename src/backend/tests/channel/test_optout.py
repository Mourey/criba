"""STOP / BAJA: the opt-out keyword is recognised by code, not by the agent."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from criba.channel import copy
from criba.channel.optout import OPT_OUT_KEYWORDS, is_opt_out_request
from criba.channel.scheduling import build_scheduler
from criba.db import repo

from .conftest import FakeHermes, candidate_for, first_message, start


def send(client: TestClient, conv: str, text: str, message_id: str | None = None):
    body = {"message_id": message_id or str(uuid.uuid4()), "text": text}
    resp = client.post(f"/api/chat/conversations/{conv}/messages", json=body)
    assert resp.status_code == 202, resp.text
    return resp


def outbound(conn, conv: str) -> list[dict]:
    """Every outbound message of the Candidate (a STOP with no open Application stores its
    confirmation without one, Q42)."""
    rows = conn.execute(
        "SELECT * FROM messages WHERE candidate_id = ? AND direction = 'outbound' ORDER BY id",
        (candidate_for(conn, conv)["id"],),
    ).fetchall()
    return [dict(r) for r in rows]


def event_kinds(conn, conv: str) -> list[str]:
    """Every event of the Candidate (``opt_out_lifted`` has no Application)."""
    rows = conn.execute(
        "SELECT kind FROM events WHERE candidate_id = ? ORDER BY id",
        (candidate_for(conn, conv)["id"],),
    ).fetchall()
    return [r["kind"] for r in rows]


def opted_out(conn, conv: str) -> bool:
    return repo.get_candidate(conn, candidate_for(conn, conv)["id"])["opted_out"]


def take_over(conn, conv: str) -> None:
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    conn.execute(
        "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
        " VALUES ('u1', 'grupo_sazon', 'r@example.com', 'R', 'recruiter', 'x', '2026-10-09')"
    )
    conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'u1' WHERE id = ?",
        (app["id"],),
    )


# --------------------------------------------------------------------------- matching


def test_the_keyword_set_is_one_constant() -> None:
    assert {"stop", "baja"} == OPT_OUT_KEYWORDS


@pytest.mark.parametrize(
    "text",
    ["STOP", "stop", " Stop! ", "BAJA", "baja", "bája", "BÁJA", "¡Baja!", "  stop.\n", "Stop..."],
)
def test_whole_message_keywords_match(text: str) -> None:
    assert is_opt_out_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "no paro",
        "baja california",
        "quiero darme de baja?",
        "stop it please",
        "no stop",
        "stopp",
        "",
        "   ",
        "!!!",
        "stop baja",
        "baja-stop",
    ],
)
def test_anything_more_than_the_keyword_does_not_match(text: str) -> None:
    assert not is_opt_out_request(text)


# --------------------------------------------------------------------------- the router


@pytest.mark.parametrize("text", ["STOP", "stop", " Stop! ", "BAJA", "baja", "bája", "¡Baja!"])
def test_a_keyword_marks_the_candidate_opted_out_without_a_run(
    client: TestClient, conn, hermes: FakeHermes, text: str
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    posts = len(hermes.posts)

    send(client, conv, text)

    assert opted_out(conn, conv) is True
    assert len(hermes.posts) == posts  # no agent run
    candidate = candidate_for(conn, conv)
    app = repo.get_open_application(conn, candidate["id"])
    messages = repo.list_messages(conn, app["id"])
    assert messages[-2]["direction"] == "inbound" and messages[-2]["body"] == text  # stored
    assert messages[-1]["direction"] == "outbound"
    assert messages[-1]["body"] == copy.OPT_OUT_CONFIRMATION["es-ES"]
    kinds = event_kinds(conn, conv)
    assert kinds.count("opted_out") == 1 and "opt_out_lifted" not in kinds


@pytest.mark.parametrize(
    ("country", "locale"), [("ES", "es-ES"), ("MX", "es-MX"), ("ES", "en"), ("MX", "en")]
)
def test_the_confirmation_is_in_the_candidates_language(
    client: TestClient, conn, country: str, locale: str
) -> None:
    conv = start(client, country=country, locale=locale)
    send(client, conv, "hola")
    send(client, conv, "STOP")
    [reply] = outbound(conn, conv)[-1:]
    assert reply["body"] == copy.OPT_OUT_CONFIRMATION[locale]
    assert reply["sender"] == "system"
    assert len([m for m in outbound(conn, conv) if m["body"] == reply["body"]]) == 1


def test_a_keyword_as_the_first_message_is_handled_too(
    client: TestClient, conn, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "STOP")
    assert opted_out(conn, conv) is True
    assert hermes.posts == []
    assert len(outbound(conn, conv)) == 1
    assert repo.get_open_application(conn, candidate_for(conn, conv)["id"]) is None  # Q42


@pytest.mark.parametrize("text", ["no paro", "baja california", "quiero darme de baja?"])
def test_other_messages_fall_through_to_a_normal_run(
    client: TestClient, conn, hermes: FakeHermes, text: str
) -> None:
    conv = start(client)
    send(client, conv, text)
    assert opted_out(conn, conv) is False
    assert hermes.inputs == [text]
    assert [m["body"] for m in outbound(conn, conv)] == [first_message(f"Recibido (IA): {text}")]
    assert "opted_out" not in event_kinds(conn, conv)


def test_a_button_tap_never_matches(client: TestClient, conn, hermes: FakeHermes, fakes) -> None:
    fakes.choice = {
        "style": "buttons",
        "options": [{"id": "stop", "label": "STOP"}, {"id": "go", "label": "Seguir"}],
    }
    conv = start(client)
    send(client, conv, "hola")
    question = outbound(conn, conv)[-1]
    fakes.choice = None

    tap = client.post(
        f"/api/chat/conversations/{conv}/replies",
        json={"message_id": "tap-1", "in_reply_to": question["id"], "option_id": "stop"},
    )

    assert tap.status_code == 202, tap.text
    assert opted_out(conn, conv) is False
    assert hermes.inputs[-1] == "stop"  # the option id reached the agent as usual
    assert "opted_out" not in event_kinds(conn, conv)


def test_the_stop_message_does_not_lift_the_opt_out_but_a_later_one_does(
    client: TestClient, conn, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    repo.mark_opted_out(
        conn, candidate_for(conn, conv)["id"]
    )  # opted out earlier, say by the agent

    send(client, conv, "STOP")
    assert opted_out(conn, conv) is True
    assert "opt_out_lifted" not in event_kinds(conn, conv)
    assert len(outbound(conn, conv)) == 2  # the agent's earlier reply and the confirmation

    send(client, conv, "vuelvo")
    assert opted_out(conn, conv) is False
    assert "opt_out_lifted" in event_kinds(conn, conv)
    assert hermes.inputs[-1] == "vuelvo"


def test_stop_then_a_later_message_lifts_it_and_starts_a_run(
    client: TestClient, conn, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    send(client, conv, "STOP")
    assert opted_out(conn, conv) is True

    send(client, conv, "he cambiado de idea")

    assert opted_out(conn, conv) is False
    assert hermes.inputs[-1] == "he cambiado de idea"
    kinds = event_kinds(conn, conv)
    assert kinds.index("opted_out") < kinds.index("opt_out_lifted")


def test_stop_while_already_opted_out_confirms_again(client: TestClient, conn) -> None:
    conv = start(client)
    send(client, conv, "hola")
    send(client, conv, "STOP")
    send(client, conv, "BAJA")
    assert opted_out(conn, conv) is True
    confirmations = [
        m for m in outbound(conn, conv) if m["body"] == copy.OPT_OUT_CONFIRMATION["es-ES"]
    ]
    assert len(confirmations) == 2
    assert event_kinds(conn, conv).count("opted_out") == 1  # one opt-out, not two


def test_a_retried_stop_is_not_confirmed_twice(client: TestClient, conn) -> None:
    conv = start(client)
    send(client, conv, "STOP", message_id="m-stop")
    assert send(client, conv, "STOP", message_id="m-stop").json()["duplicate"] is True
    assert len(outbound(conn, conv)) == 1


def test_during_a_takeover_the_candidate_is_marked_but_nothing_is_sent(
    client: TestClient, conn, hermes: FakeHermes
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    take_over(conn, conv)
    before = len(outbound(conn, conv))
    posts = len(hermes.posts)

    send(client, conv, "STOP")

    assert opted_out(conn, conv) is True
    assert "opted_out" in event_kinds(conn, conv)
    assert len(outbound(conn, conv)) == before  # no automated confirmation
    assert len(hermes.posts) == posts
    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    assert repo.list_messages(conn, app["id"])[-1]["body"] == "STOP"  # the human sees it


def test_the_confirmation_is_not_held_for_quiet_hours(client: TestClient, conn, clock) -> None:
    conv = start(client)
    send(client, conv, "hola")
    clock.advance(hours=11)  # 23:00 in Madrid
    send(client, conv, "STOP")
    assert outbound(conn, conv)[-1]["body"] == copy.OPT_OUT_CONFIRMATION["es-ES"]


# --------------------------------------------------------------------------- the scheduler


def test_a_candidate_who_sends_stop_gets_no_more_nudges(
    client: TestClient, conn, channel, clock
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    send(client, conv, "STOP")
    scheduler = build_scheduler(channel)
    sent = [m["id"] for m in outbound(conn, conv)]  # the agent's reply and the confirmation

    for _ in range(6):  # three days: past the whole ladder
        clock.advance(hours=12)
        scheduler.run_due_jobs(conn)

    assert [m["id"] for m in outbound(conn, conv)] == sent
    assert "nudge_sent" not in event_kinds(conn, conv)


def test_without_stop_the_same_candidate_is_nudged(
    client: TestClient, conn, channel, clock
) -> None:
    conv = start(client)
    send(client, conv, "hola")
    scheduler = build_scheduler(channel)

    clock.advance(hours=2)
    scheduler.run_due_jobs(conn)

    app = repo.get_open_application(conn, candidate_for(conn, conv)["id"])
    # The control for the test above: the ladder does run when nobody opted out.
    assert "nudge_sent" in [e["kind"] for e in repo.list_events(conn, app["id"])]
