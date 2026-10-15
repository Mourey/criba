"""Takeover, hand-back and the live relay (server-sent events)."""

from __future__ import annotations

import json
import threading
import time
from datetime import timedelta

from criba.api import takeover as takeover_module
from criba.api.auth import COOKIE_NAME, hash_token
from criba.db import repo
from criba.db.connection import connect

from .support import LA_PAZ, NOW, OPERATOR, PUEBLA, PUEBLA_2, PUEBLA_ANGELOPOLIS, at

PUEBLA_ID = "usr_rec_puebla"


def start(client, app_id):
    return client.post(f"/api/applications/{app_id}/takeover")


def say(client, app_id, text="Hola Luis, soy del equipo."):
    return client.post(f"/api/applications/{app_id}/takeover/messages", json={"text": text})


def hand_back(client, app_id, **body):
    return client.post(f"/api/applications/{app_id}/takeover/handback", json=body)


def inbound(env, app, text):
    """A Candidate message, written on its own connection (callable from any thread)."""
    conn = connect(env.db_path)
    try:
        return repo.add_message(
            conn,
            candidate_id=app["candidate_id"],
            application_id=app["id"],
            channel="webchat",
            direction="inbound",
            sender="candidate",
            body=text,
            now=env.clock.now(),
        )[0]
    finally:
        conn.close()


def test_start_takeover(env, build, contract):
    app = build.application()
    env.clock.set(at(2))
    response = start(env.client(PUEBLA), app["id"])
    body = contract.conforms(response, "startTakeover")
    assert body == {
        "control": "human",
        "user_id": PUEBLA_ID,
        "user_name": "Recruiter Puebla (synthetic)",
        "since": "2026-10-09T10:02:00Z",
    }
    stored = repo.get_application(env.conn, app["id"])
    assert (stored["control"], stored["control_user_id"]) == ("human", PUEBLA_ID)
    assert env.notified == [(app["id"], "takeover_started")]
    started = [e for e in repo.list_events(env.conn, app["id"]) if e["kind"] == "takeover_started"]
    assert [(e["actor"], e["user_id"]) for e in started] == [("user", PUEBLA_ID)]


def test_the_candidate_is_told_a_team_member_joined(env, build):
    app = build.application()
    start(env.client(PUEBLA), app["id"])
    notice = repo.list_messages(env.conn, app["id"])[-1]
    assert (notice["sender"], notice["direction"]) == ("system", "outbound")
    assert "equipo de Grupo Sazón" in notice["body"]
    assert "Recruiter" not in notice["body"]  # never a person's name
    assert env.sender.notices == [(app["id"], "takeover_joined")]


def test_start_is_idempotent_for_the_holder_and_exclusive_otherwise(env, build, contract):
    app = build.application()
    start(env.client(PUEBLA), app["id"])
    again = start(env.client(PUEBLA), app["id"])
    assert again.status_code == 200
    assert len(env.sender.notices) == 1  # no second "joined" notice
    other = start(env.client(PUEBLA_2), app["id"])
    body = contract.conforms(other, "startTakeover")
    assert other.status_code == 409
    assert body["error"] == "control_held"
    assert "Recruiter Puebla" in body["message"]
    assert repo.get_application(env.conn, app["id"])["control_user_id"] == PUEBLA_ID


def test_takeover_race_has_one_winner(env, build):
    app = build.application()
    clients = [env.client(PUEBLA), env.client(PUEBLA_2), env.client(OPERATOR)]
    barrier = threading.Barrier(3)
    statuses: list[int] = []

    def go(client):
        barrier.wait()
        statuses.append(start(client, app["id"]).status_code)

    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(statuses) == [200, 409, 409]
    events = [e["kind"] for e in repo.list_events(env.conn, app["id"])]
    assert events.count("takeover_started") == 1


def test_takeover_on_a_closed_application_is_a_409(env, build):
    app = build.application(stage="knockout_license")
    response = start(env.client(PUEBLA), app["id"])
    assert response.status_code == 409
    assert response.json()["error"] == "application_closed"


def test_takeover_is_scoped(env, build):
    app = build.application(zone=PUEBLA_ANGELOPOLIS)
    assert start(env.client(LA_PAZ), app["id"]).status_code == 404


def test_the_router_can_tell_who_holds_the_chat(env, build):
    app = build.application()
    start(env.client(PUEBLA), app["id"])
    detail = env.client(OPERATOR).get(f"/api/applications/{app['id']}").json()
    assert detail["control"] == "human"
    assert detail["control_user_id"] == PUEBLA_ID
    assert detail["control_user_name"] == "Recruiter Puebla (synthetic)"
    assert detail["flags"] == ["takeover"]


# --- messages ----------------------------------------------------------------------------


def test_holder_sends_a_message_to_the_candidate(env, build, contract):
    app = build.application()
    client = env.client(PUEBLA)
    start(client, app["id"])
    response = say(client, app["id"], "  Sí, la motoneta la pone la empresa.  ")
    body = contract.conforms(response, "sendTakeoverMessage")
    assert response.status_code == 201
    assert body["direction"] == "outbound"
    assert body["sender"] == "human"
    assert body["sender_name"] == "Recruiter Puebla (synthetic)"
    assert body["body"] == "Sí, la motoneta la pone la empresa."
    assert env.sender.texts == [(app["id"], PUEBLA_ID, "Sí, la motoneta la pone la empresa.")]
    stored = repo.list_messages(env.conn, app["id"])[-1]
    assert stored["sender_user_id"] == PUEBLA_ID
    assert stored["id"] == body["id"]


def test_only_the_holder_may_write(env, build, contract):
    app = build.application()
    start(env.client(PUEBLA), app["id"])
    for client in (env.client(PUEBLA_2), env.client(OPERATOR)):
        response = say(client, app["id"])
        body = contract.conforms(response, "sendTakeoverMessage")
        assert response.status_code == 403
        assert body["error"] == "not_in_control"
    assert env.sender.texts == []


def test_cannot_write_before_a_takeover(env, build):
    app = build.application()
    response = say(env.client(PUEBLA), app["id"])
    assert response.status_code == 403
    assert response.json()["error"] == "not_in_control"


def test_message_validation(env, build, contract):
    app = build.application()
    client = env.client(PUEBLA)
    start(client, app["id"])
    for payload in ({}, {"text": ""}, {"text": "   "}, {"text": "x" * 4001}, {"text": "a", "x": 1}):
        response = client.post(f"/api/applications/{app['id']}/takeover/messages", json=payload)
        assert contract.conforms(response, "sendTakeoverMessage")["error"] == "validation_error"
        assert response.status_code == 422


# --- hand-back ---------------------------------------------------------------------------


def test_hand_back_with_a_summary(env, build, contract):
    app = build.application()
    client = env.client(PUEBLA)
    start(client, app["id"])
    env.clock.set(at(5))
    response = hand_back(client, app["id"], summary="Confirmed the motorbike is provided.")
    body = contract.conforms(response, "handBack")
    assert body == {
        "control": "agent",
        "user_id": None,
        "user_name": None,
        "since": "2026-10-09T10:05:00Z",
    }
    stored = repo.get_application(env.conn, app["id"])
    assert (stored["control"], stored["control_user_id"]) == ("agent", None)
    event = next(
        e for e in repo.list_events(env.conn, app["id"]) if e["kind"] == "takeover_handed_back"
    )
    # The summary is stored as a hand-back row; the event only references it (Q38).
    handback_id = event["data"]["handback_id"]
    assert event["data"] == {"handback_id": handback_id, "generated": False, "held_by": PUEBLA_ID}
    handback = repo.get_handback(env.conn, handback_id)
    assert handback is not None
    assert handback["summary"] == "Confirmed the motorbike is provided."
    assert (handback["application_id"], handback["user_id"]) == (app["id"], PUEBLA_ID)
    assert handback["generated"] == 0
    assert env.notified[-1] == (app["id"], "takeover_handed_back")


def test_hand_back_generates_a_summary_from_the_transcript(env, build):
    app = build.application()
    client = env.client(PUEBLA)
    env.clock.set(at(0))
    inbound(env, app, "mensaje de antes")
    env.clock.set(at(1))
    start(client, app["id"])
    env.clock.set(at(2))
    inbound(env, app, "¿Dan moto?")
    say(client, app["id"], "Sí, la pone la empresa.")
    hand_back(client, app["id"])
    event = next(
        e for e in repo.list_events(env.conn, app["id"]) if e["kind"] == "takeover_handed_back"
    )
    assert event["data"]["generated"] is True
    assert "summary" not in event["data"]
    summary = repo.get_handback(env.conn, event["data"]["handback_id"])["summary"]
    assert "Candidate: ¿Dan moto?" in summary
    assert "Team member: Sí, la pone la empresa." in summary
    assert "mensaje de antes" not in summary  # only what was said during the Takeover


def test_generated_summary_without_messages_and_its_cap(env, build):
    app = build.application()
    client = env.client(PUEBLA)
    start(client, app["id"])
    assert "No messages" in takeover_module.generated_handback_summary(env.conn, app["id"], "9999")
    for n in range(40):
        say(client, app["id"], f"mensaje {n} " + "x" * 300)
    long = takeover_module.generated_handback_summary(env.conn, app["id"], "2026-01-01T00:00:00Z")
    assert len(long) <= 2000


def test_hand_back_rules(env, build, contract):
    app = build.application()
    start(env.client(PUEBLA), app["id"])
    other = hand_back(env.client(PUEBLA_2), app["id"])
    body = contract.conforms(other, "handBack")
    assert other.status_code == 403
    assert body["error"] == "not_in_control"
    assert repo.get_application(env.conn, app["id"])["control"] == "human"
    # An Operator may take the chat back from anyone.
    assert hand_back(env.client(OPERATOR), app["id"]).status_code == 200
    # Handing back when the agent already has the chat is a harmless 200.
    again = hand_back(env.client(PUEBLA), app["id"])
    assert again.status_code == 200
    assert again.json()["control"] == "agent"
    kinds = [e["kind"] for e in repo.list_events(env.conn, app["id"])]
    assert kinds.count("takeover_handed_back") == 1


def test_hand_back_accepts_no_body_and_rejects_a_long_summary(env, build):
    app = build.application()
    client = env.client(PUEBLA)
    start(client, app["id"])
    assert hand_back(client, app["id"], summary="x" * 2001).status_code == 422
    assert client.post(f"/api/applications/{app['id']}/takeover/handback").status_code == 200


def test_after_hand_back_the_chat_can_be_taken_again(env, build):
    app = build.application()
    start(env.client(PUEBLA), app["id"])
    hand_back(env.client(PUEBLA), app["id"])
    assert start(env.client(PUEBLA_2), app["id"]).status_code == 200


# --- the relay stream --------------------------------------------------------------------


def parse_sse(text):
    frames = []
    for block in text.strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            key, _, value = line.partition(": ")
            fields[key] = value
        if "event" in fields:
            frames.append(
                {
                    "id": fields.get("id"),
                    "event": fields["event"],
                    "data": json.loads(fields["data"]),
                }
            )
    return frames


def read_stream(client, path, *, headers=None, act=None):
    """Read one whole (bounded) stream; ``act`` runs on another thread after a short delay."""
    worker = None
    if act:
        worker = threading.Thread(target=lambda: (time.sleep(0.25), act()))
        worker.start()
    with client.stream("GET", path, headers=headers or {}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        text = "".join(response.iter_text())
    if worker:
        worker.join()
    return parse_sse(text)


def test_relay_streams_messages_control_and_events_live(env, build, contract):
    app = build.application()
    watcher = env.client(PUEBLA)
    actor = env.client(PUEBLA_2)

    def act():
        start(actor, app["id"])
        say(actor, app["id"], "Hola, soy del equipo.")
        inbound(env, app, "¡Perfecto, gracias!")
        hand_back(actor, app["id"], summary="Todo claro.")

    frames = read_stream(watcher, f"/api/applications/{app['id']}/stream", act=act)
    by_type = {}
    for f in frames:
        by_type.setdefault(f["event"], []).append(f)
        contract.validate("StreamEvent", f["data"])
    messages = [f["data"]["data"]["body"] for f in by_type["message"]]
    assert "Hola, soy del equipo." in messages
    assert "¡Perfecto, gracias!" in messages
    for f in by_type["message"]:
        contract.validate("Message", f["data"]["data"])
        assert f["id"] == f"m-{f['data']['data']['id']}"
    controls = [f["data"]["data"] for f in by_type["control"]]
    assert [c["control"] for c in controls] == ["human", "agent"]
    assert controls[0]["user_id"] == "usr_rec_puebla_2"
    for c in controls:
        contract.validate("TakeoverState", c)
    kinds = [f["data"]["data"]["kind"] for f in by_type["event"]]
    assert "takeover_started" in kinds and "takeover_handed_back" in kinds
    for f in by_type["event"]:
        contract.validate("Event", f["data"]["data"])
        assert f["id"] == f"e-{f['data']['data']['id']}"
    # It starts from "now": nothing from before the connection is replayed.
    assert all(f["data"]["data"].get("kind") != "application_started" for f in by_type["event"])


def test_relay_resumes_from_last_event_id(env, build):
    app = build.application()
    client = env.client(PUEBLA)
    first = inbound(env, app, "uno")
    env.clock.set(at(1))
    second = inbound(env, app, "dos")
    env.clock.set(at(2))
    third = inbound(env, app, "tres")
    frames = read_stream(
        client,
        f"/api/applications/{app['id']}/stream",
        headers={"Last-Event-ID": f"m-{first['id']}"},
    )
    bodies = [f["data"]["data"]["body"] for f in frames if f["event"] == "message"]
    assert bodies == ["dos", "tres"]
    assert second["id"] < third["id"]


def test_relay_resume_by_event_id_replays_later_messages_not_earlier_ones(env, build):
    app = build.application()
    inbound(env, app, "antes")
    env.clock.set(at(5))
    start(env.client(PUEBLA), app["id"])
    started = max(e["id"] for e in repo.list_events(env.conn, app["id"]))
    env.clock.set(at(10))
    inbound(env, app, "después")
    frames = read_stream(
        env.client(PUEBLA),
        f"/api/applications/{app['id']}/stream",
        headers={"Last-Event-ID": f"e-{started}"},
    )
    bodies = [f["data"]["data"]["body"] for f in frames if f["event"] == "message"]
    assert "después" in bodies and "antes" not in bodies


def test_relay_ignores_a_garbled_last_event_id(env, build):
    app = build.application()
    frames = read_stream(
        env.client(PUEBLA),
        f"/api/applications/{app['id']}/stream",
        headers={"Last-Event-ID": "zzz"},
    )
    assert frames == []


def test_relay_sends_heartbeats_when_idle(env, build):
    app = build.application()
    client = env.client(PUEBLA)
    with client.stream("GET", f"/api/applications/{app['id']}/stream") as response:
        text = "".join(response.iter_text())
    assert text.startswith(": connected")
    assert ": ping" in text


def test_relay_requires_a_session_and_scope(env, build):
    app = build.application(zone=PUEBLA_ANGELOPOLIS)
    assert env.client().get(f"/api/applications/{app['id']}/stream").status_code == 401
    assert env.client(LA_PAZ).get(f"/api/applications/{app['id']}/stream").status_code == 404
    assert env.client(PUEBLA).get("/api/applications/app_nope/stream").status_code == 404


def test_poll_stops_when_the_session_is_revoked(env, build):
    app = build.application()
    client = env.client(PUEBLA)
    token_hash = hash_token(client.cookies[COOKIE_NAME])
    cursors = [0, 0]
    frames, alive = takeover_module._poll_application(env.ctx, app["id"], token_hash, cursors)
    assert alive and frames
    client.post("/api/auth/logout")
    assert takeover_module._poll_application(env.ctx, app["id"], token_hash, cursors) == ([], False)


def test_poll_returns_new_rows_once_in_time_order(env, build):
    app = build.application()
    client = env.client(PUEBLA)
    token_hash = hash_token(client.cookies[COOKIE_NAME])
    cursors = [0, 0]
    takeover_module._poll_application(env.ctx, app["id"], token_hash, cursors)
    assert takeover_module._poll_application(env.ctx, app["id"], token_hash, cursors) == ([], True)
    env.clock.set(NOW + timedelta(minutes=1))
    inbound(env, app, "hola")
    env.clock.set(NOW + timedelta(minutes=2))
    start(client, app["id"])
    frames, _ = takeover_module._poll_application(env.ctx, app["id"], token_hash, cursors)
    names = [f.split("\n")[1] if f.startswith("id:") else f.split("\n")[0] for f in frames]
    assert names[0] == "event: message"
    assert "event: control" in names
    again, _ = takeover_module._poll_application(env.ctx, app["id"], token_hash, cursors)
    assert again == []
