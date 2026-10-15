"""Review queue: Approvals, Reviews, Escalations and Rejections."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from criba.db import repo
from criba.db.connection import connect

from .support import (
    LA_PAZ,
    NOW,
    OPERATOR,
    PUEBLA,
    PUEBLA_2,
    PUEBLA_ANGELOPOLIS,
    PUEBLA_CENTRO,
    at,
)


def item_for(env, app_id, kind):
    return env.conn.execute(
        "SELECT * FROM queue_items WHERE application_id = ? AND kind = ?", (app_id, kind)
    ).fetchone()


def kinds(env, app_id):
    return [e["kind"] for e in repo.list_events(env.conn, app_id)]


# --- listing -----------------------------------------------------------------------------


def test_queue_lists_open_items_oldest_first(env, build, contract):
    first = build.application(now=at(0))
    second = build.application(stage="knockout_license", now=at(5))
    review = build.queue_item(second["id"], "review", now=at(6))
    escalation = build.queue_item(
        first["id"], "escalation", reason="wants_human", note="hola", now=at(9)
    )
    body = contract.conforms(env.client(PUEBLA).get("/api/queue"), "listQueue")
    assert [i["kind"] for i in body["items"]] == ["approval", "review", "escalation"]
    assert body["items"][0]["application"]["id"] == first["id"]
    assert {i["id"] for i in body["items"]} >= {review["id"], escalation["id"]}
    assert body["items"][2]["reason"] == "wants_human"
    assert body["next_cursor"] is None


def test_queue_filters_by_kind_location_and_status(env, build, contract):
    qualified = build.application()
    narrow = build.application(zone=PUEBLA_ANGELOPOLIS)
    op = env.client(OPERATOR)
    assert len(op.get("/api/queue", params={"kind": "approval"}).json()["items"]) == 2
    assert op.get("/api/queue", params={"kind": "review"}).json()["items"] == []
    la_paz = op.get("/api/queue", params={"location_id": "loc_mx_puebla_la_paz"}).json()
    assert [i["application"]["id"] for i in la_paz["items"]] == [qualified["id"]]
    approval = item_for(env, narrow["id"], "approval")
    op.post(f"/api/queue/approvals/{approval['id']}", json={"decision": "approved"})
    resolved = contract.conforms(op.get("/api/queue", params={"status": "resolved"}), "listQueue")
    assert [i["id"] for i in resolved["items"]] == [approval["id"]]
    assert resolved["items"][0]["resolution"] == "approved"


def test_resolved_queue_shows_the_newest_first(env, build):
    a, b = build.application(), build.application()
    op = env.client(OPERATOR)
    for app_id, minutes in ((a["id"], 1), (b["id"], 2)):
        env.clock.set(at(minutes))
        op.post(
            f"/api/queue/approvals/{item_for(env, app_id, 'approval')['id']}",
            json={"decision": "approved"},
        )
    body = op.get("/api/queue", params={"status": "resolved"}).json()
    assert [i["application"]["id"] for i in body["items"]] == [b["id"], a["id"]]


def test_queue_pagination(env, build):
    for n in range(3):
        build.application(now=at(n))
    op = env.client(OPERATOR)
    page = op.get("/api/queue", params={"limit": 2}).json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    rest = op.get("/api/queue", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None


def test_queue_is_scoped_to_the_recruiters_locations(env, build):
    wide = build.application()
    build.application(zone=PUEBLA_ANGELOPOLIS)
    items = env.client(LA_PAZ).get("/api/queue").json()["items"]
    assert [i["application"]["id"] for i in items] == [wide["id"]]
    assert env.client(OPERATOR).get("/api/queue").json()["items"][0]["application"]["candidate"]


# --- approvals ---------------------------------------------------------------------------


def test_approve(env, build, contract):
    app = build.application()
    item = item_for(env, app["id"], "approval")
    env.clock.set(at(30))
    response = env.client(PUEBLA).post(
        f"/api/queue/approvals/{item['id']}", json={"decision": "approved", "note": "ok"}
    )
    body = contract.conforms(response, "resolveApproval")
    assert body["status"] == "resolved"
    assert body["resolution"] == "approved"
    assert body["resolution_note"] == "ok"
    assert body["resolved_by"] == "usr_rec_puebla"
    assert body["resolved_at"] == "2026-10-09T10:30:00Z"
    assert body["application"]["stage"] == "approved"
    assert body["application"]["status"] == "open"
    stored = repo.get_application(env.conn, app["id"])
    assert stored["approved_by"] == "usr_rec_puebla"
    assert "approved" in kinds(env, app["id"])
    # Handed to the scheduler once, after the commit (no transaction open on the connection).
    assert env.submitted == [(app["id"], "approved", False)]
    assert env.notified == []


def test_approval_twice_is_already_resolved(env, build, contract):
    app = build.application()
    item = item_for(env, app["id"], "approval")
    client = env.client(PUEBLA)
    client.post(f"/api/queue/approvals/{item['id']}", json={"decision": "approved"})
    again = env.client(PUEBLA_2).post(
        f"/api/queue/approvals/{item['id']}", json={"decision": "approved"}
    )
    body = contract.conforms(again, "resolveApproval")
    assert again.status_code == 409
    assert body["error"] == "already_resolved"
    assert env.submitted == [(app["id"], "approved", False)]


def test_concurrent_approvals_resolve_once(env, build):
    app = build.application()
    item = item_for(env, app["id"], "approval")
    clients = [env.client(PUEBLA), env.client(PUEBLA_2), env.client(OPERATOR)]
    barrier = threading.Barrier(3)
    statuses: list[int] = []

    def go(client):
        barrier.wait()
        statuses.append(
            client.post(
                f"/api/queue/approvals/{item['id']}", json={"decision": "approved"}
            ).status_code
        )

    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(statuses) == [200, 409, 409]
    assert kinds(env, app["id"]).count("approved") == 1


def test_reject_at_approval_with_a_reason(env, build, contract):
    app = build.application()
    item = item_for(env, app["id"], "approval")
    response = env.client(PUEBLA).post(
        f"/api/queue/approvals/{item['id']}",
        json={"decision": "rejected", "reason": "role_filled", "note": None},
    )
    body = contract.conforms(response, "resolveApproval")
    assert body["resolution"] == "rejected"
    assert body["application"]["status"] == "closed"
    assert body["application"]["outcome"] == "rejection"
    assert body["application"]["rejection_reason"] == "role_filled"
    assert body["application"]["rejection_note"] is None
    assert "rejection" in kinds(env, app["id"])
    assert env.sender.notices == [(app["id"], "rejection")]
    assert env.notified == [(app["id"], "rejection")]
    assert env.submitted == []
    message = repo.list_messages(env.conn, app["id"])[-1]
    assert (message["sender"], message["direction"], message["template_name"]) == (
        "system",
        "outbound",
        "rejection",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "rejected"},
        {"decision": "rejected", "reason": "other"},
        {"decision": "rejected", "reason": "other", "note": "   "},
        {"decision": "rejected", "reason": "tired"},
        {"decision": "maybe"},
        {"decision": "approved", "reason": "no_show"},
        {},
    ],
)
def test_invalid_approval_bodies_are_422_and_change_nothing(env, build, contract, payload):
    app = build.application()
    item = item_for(env, app["id"], "approval")
    response = env.client(PUEBLA).post(f"/api/queue/approvals/{item['id']}", json=payload)
    body = contract.conforms(response, "resolveApproval")
    assert response.status_code == 422
    assert body["error"] == "validation_error"
    assert item_for(env, app["id"], "approval")["status"] == "open"


def test_other_reason_asks_for_a_note_in_the_contract_wording(env, build):
    app = build.application()
    item = item_for(env, app["id"], "approval")
    body = (
        env.client(PUEBLA)
        .post(
            f"/api/queue/approvals/{item['id']}", json={"decision": "rejected", "reason": "other"}
        )
        .json()
    )
    assert body["message"] == "A note is required when the reason is other."


def test_reject_with_other_and_a_note(env, build):
    app = build.application()
    item = item_for(env, app["id"], "approval")
    body = (
        env.client(PUEBLA)
        .post(
            f"/api/queue/approvals/{item['id']}",
            json={"decision": "rejected", "reason": "other", "note": "Documentos falsos"},
        )
        .json()
    )
    assert body["application"]["rejection_reason"] == "other"
    assert body["application"]["rejection_note"] == "Documentos falsos"


def test_approval_of_the_wrong_kind_or_scope_is_404(env, build):
    app = build.application(zone=PUEBLA_ANGELOPOLIS)
    approval = item_for(env, app["id"], "approval")
    assert (
        env.client(LA_PAZ)
        .post(f"/api/queue/approvals/{approval['id']}", json={"decision": "approved"})
        .status_code
        == 404
    )
    assert (
        env.client(PUEBLA)
        .post(f"/api/queue/reviews/{approval['id']}", json={"decision": "upheld"})
        .status_code
        == 404
    )
    assert (
        env.client(PUEBLA)
        .post("/api/queue/approvals/qi_nope", json={"decision": "approved"})
        .status_code
        == 404
    )


def test_approval_touches_the_claim(env, build):
    app = build.application()
    client = env.client(PUEBLA)
    client.post(f"/api/applications/{app['id']}/claim")
    env.clock.advance(timedelta(days=5))
    item = item_for(env, app["id"], "approval")
    client.post(f"/api/queue/approvals/{item['id']}", json={"decision": "approved"})
    stored = repo.get_application(env.conn, app["id"])
    assert stored["claim_last_action_at"] == "2026-10-14T10:00:00Z"


# --- reviews -----------------------------------------------------------------------------


def test_review_upheld(env, build, contract):
    app = build.application(stage="knockout_license")
    item = build.queue_item(app["id"], "review")
    response = env.client(PUEBLA).post(
        f"/api/queue/reviews/{item['id']}", json={"decision": "upheld", "note": "Sin licencia"}
    )
    body = contract.conforms(response, "resolveReview")
    assert body["resolution"] == "upheld"
    assert body["application"]["status"] == "closed"
    assert "review_upheld" in kinds(env, app["id"])
    assert env.notified == [(app["id"], "review_upheld")]


def test_review_overturned_reopens_at_the_failed_check(env, build, contract):
    app = build.application(stage="knockout_license")
    item = build.queue_item(app["id"], "review")
    response = env.client(PUEBLA).post(
        f"/api/queue/reviews/{item['id']}", json={"decision": "overturned"}
    )
    body = contract.conforms(response, "resolveReview")
    assert body["resolution"] == "overturned"
    assert body["application"]["status"] == "open"
    assert body["application"]["outcome"] is None
    assert body["application"]["stage"] == "driver_license"
    events = kinds(env, app["id"])
    assert "reopened" in events and "review_overturned" in events
    assert env.notified == [(app["id"], "review_overturned")]
    reopened = next(e for e in repo.list_events(env.conn, app["id"]) if e["kind"] == "reopened")
    assert reopened["data"]["via"] == "review_overturned"
    assert reopened["actor"] == "user"


def test_review_of_an_application_that_is_no_longer_a_knockout_is_a_409(env, build, contract):
    app = build.application(stage="knockout_license")
    item = build.queue_item(app["id"], "review")
    repo.reopen_application(env.conn, app["id"], now=NOW)  # the Candidate wrote again
    response = env.client(PUEBLA).post(
        f"/api/queue/reviews/{item['id']}", json={"decision": "upheld"}
    )
    body = contract.conforms(response, "resolveReview")
    assert response.status_code == 409
    assert body["error"] == "wrong_stage"
    assert item_for(env, app["id"], "review")["status"] == "open"


def test_review_decision_is_validated(env, build):
    app = build.application(stage="knockout_license")
    item = build.queue_item(app["id"], "review")
    response = env.client(PUEBLA).post(
        f"/api/queue/reviews/{item['id']}", json={"decision": "dismissed"}
    )
    assert response.status_code == 422


# --- escalations -------------------------------------------------------------------------


def test_escalation_dismissed(env, build, contract):
    app = build.application(stage="greeting")
    item = build.queue_item(app["id"], "escalation", reason="frustrated")
    response = env.client(PUEBLA).post(
        f"/api/queue/escalations/{item['id']}", json={"decision": "dismissed"}
    )
    # Not in scope yet (no Zone, so no covering Location): only an Operator sees it.
    assert response.status_code == 404
    response = env.client(OPERATOR).post(
        f"/api/queue/escalations/{item['id']}",
        json={"decision": "dismissed", "note": "falsa alarma"},
    )
    body = contract.conforms(response, "resolveEscalation")
    assert body["resolution"] == "dismissed"
    assert body["resolution_note"] == "falsa alarma"
    assert "escalation_dismissed" in kinds(env, app["id"])
    assert repo.get_application(env.conn, app["id"])["control"] == "agent"


def test_escalation_takeover_starts_a_takeover_for_the_caller(env, build, contract):
    app = build.application()
    item = build.queue_item(app["id"], "escalation", reason="wants_human")
    response = env.client(PUEBLA).post(
        f"/api/queue/escalations/{item['id']}", json={"decision": "takeover"}
    )
    body = contract.conforms(response, "resolveEscalation")
    assert body["resolution"] == "takeover"
    assert body["application"]["control"] == "human"
    assert body["application"]["control_user_id"] == "usr_rec_puebla"
    started = next(
        e for e in repo.list_events(env.conn, app["id"]) if e["kind"] == "takeover_started"
    )
    assert started["data"] == {"queue_item_id": item["id"]}
    assert (app["id"], "takeover_joined") in env.sender.notices


def test_escalation_takeover_when_someone_else_holds_the_chat_changes_nothing(env, build, contract):
    app = build.application()
    item = build.queue_item(app["id"], "escalation", reason="wants_human")
    env.client(PUEBLA_2).post(f"/api/applications/{app['id']}/takeover")
    response = env.client(PUEBLA).post(
        f"/api/queue/escalations/{item['id']}", json={"decision": "takeover"}
    )
    body = contract.conforms(response, "resolveEscalation")
    assert response.status_code == 409
    assert body["error"] == "control_held"
    assert item_for(env, app["id"], "escalation")["status"] == "open"


def test_escalation_decision_is_validated(env, build):
    app = build.application()
    item = build.queue_item(app["id"], "escalation", reason="abuse")
    response = env.client(PUEBLA).post(
        f"/api/queue/escalations/{item['id']}", json={"decision": "approved"}
    )
    assert response.status_code == 422


# --- unreadable messages (ADR 0063) ----------------------------------------------------------


def test_an_unreadable_escalation_is_listed_and_dismissing_it_releases_the_agent(
    env, build, contract
):
    app = build.application()
    item = repo.hold_for_recruiters(env.conn, app["id"], note="fixed note", now=at(1))
    env.conn.commit()
    assert repo.agent_on_hold(env.conn, app["id"])
    body = contract.conforms(env.client(PUEBLA).get("/api/queue"), "listQueue")
    listed = next(i for i in body["items"] if i["id"] == item["id"])
    assert listed["reason"] == "unreadable"
    response = env.client(PUEBLA).post(
        f"/api/queue/escalations/{item['id']}", json={"decision": "dismissed"}
    )
    assert contract.conforms(response, "resolveEscalation")["resolution"] == "dismissed"
    assert not repo.agent_on_hold(env.conn, app["id"])
    assert repo.get_application(env.conn, app["id"])["control"] == "agent"


def test_taking_over_from_the_queue_and_handing_back_releases_the_agent(env, build, contract):
    app = build.application()
    item = repo.hold_for_recruiters(env.conn, app["id"], note="fixed note", now=at(1))
    env.conn.commit()
    client = env.client(PUEBLA)
    response = client.post(f"/api/queue/escalations/{item['id']}", json={"decision": "takeover"})
    assert contract.conforms(response, "resolveEscalation")["resolution"] == "takeover"
    assert not repo.agent_on_hold(env.conn, app["id"])
    client.post(f"/api/applications/{app['id']}/takeover/handback", json={})
    assert repo.get_application(env.conn, app["id"])["control"] == "agent"
    assert not repo.agent_on_hold(env.conn, app["id"])


def test_a_takeover_from_the_application_resolves_the_unreadable_hold(env, build):
    app = build.application()
    item = repo.hold_for_recruiters(env.conn, app["id"], note="fixed note", now=at(1))
    env.conn.commit()
    client = env.client(PUEBLA)
    assert client.post(f"/api/applications/{app['id']}/takeover").status_code == 200
    resolved = item_for(env, app["id"], "escalation")
    assert resolved["id"] == item["id"]
    assert (resolved["status"], resolved["resolution"]) == ("resolved", "takeover")
    assert resolved["resolved_by"] == "usr_rec_puebla"
    client.post(f"/api/applications/{app['id']}/takeover/handback", json={})
    assert repo.get_application(env.conn, app["id"])["control"] == "agent"
    assert not repo.agent_on_hold(env.conn, app["id"])


def test_a_takeover_leaves_other_escalations_open(env, build):
    app = build.application()
    build.queue_item(app["id"], "escalation", reason="wants_human")
    env.client(PUEBLA).post(f"/api/applications/{app['id']}/takeover")
    assert item_for(env, app["id"], "escalation")["status"] == "open"


# --- rejection at any stage --------------------------------------------------------------


@pytest.mark.parametrize("stage", ["greeting", "qualified", "approved"])
def test_reject_at_any_stage(env, build, contract, stage):
    app = build.application(stage=stage, zone=PUEBLA_CENTRO)
    response = env.client(OPERATOR).post(
        f"/api/applications/{app['id']}/rejection", json={"reason": "no_show"}
    )
    body = contract.conforms(response, "rejectApplication")
    assert body["status"] == "closed"
    assert body["outcome"] == "rejection"
    assert body["rejection_reason"] == "no_show"
    assert env.sender.notices == [(app["id"], "rejection")]
    assert item_for(env, app["id"], "approval") is None or (
        item_for(env, app["id"], "approval")["status"] == "resolved"
    )


def test_rejection_releases_a_booked_interview(env, build):
    app = build.application(stage="approved")
    slot = env.conn.execute(
        "SELECT id FROM interview_slots WHERE location_id = ? LIMIT 1", ("loc_mx_puebla_centro",)
    ).fetchone()[0]
    env.ctx.services.booking.book(
        env.conn, application_id=app["id"], slot_id=slot, via="chat", now=NOW
    )
    env.client(PUEBLA).post(f"/api/applications/{app['id']}/rejection", json={"reason": "no_show"})
    slots = env.client(PUEBLA).get("/api/slots", params={"location_id": "loc_mx_puebla_centro"})
    assert next(s for s in slots.json()["items"] if s["id"] == slot)["status"] == "open"
    assert "interview_slot_released" in kinds(env, app["id"])


def test_rejection_ends_a_takeover_and_the_claim(env, build):
    app = build.application()
    client = env.client(PUEBLA)
    client.post(f"/api/applications/{app['id']}/claim")
    client.post(f"/api/applications/{app['id']}/takeover")
    body = client.post(
        f"/api/applications/{app['id']}/rejection", json={"reason": "failed_call"}
    ).json()
    assert body["control"] == "agent"
    assert body["claim"] is None
    assert "takeover_handed_back" in kinds(env, app["id"])


def test_reject_twice_and_validation(env, build, contract):
    app = build.application()
    client = env.client(PUEBLA)
    assert (
        client.post(
            f"/api/applications/{app['id']}/rejection", json={"reason": "other"}
        ).status_code
        == 422
    )
    assert client.post(f"/api/applications/{app['id']}/rejection", json={}).status_code == 422
    assert (
        client.post(
            f"/api/applications/{app['id']}/rejection", json={"reason": "no_show", "x": 1}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/applications/{app['id']}/rejection", json={"reason": "no_show"}
        ).status_code
        == 200
    )
    second = client.post(f"/api/applications/{app['id']}/rejection", json={"reason": "no_show"})
    body = contract.conforms(second, "rejectApplication")
    assert second.status_code == 409
    assert body["error"] == "application_closed"


def test_contract_examples_are_accepted_by_the_request_models(contract):
    from pydantic import TypeAdapter

    from criba.api.queue import (
        ApprovalDecision,
        EscalationDecision,
        RejectionRequest,
        ReviewDecision,
    )

    approve = contract.spec["paths"]["/api/queue/approvals/{item_id}"]["post"]
    for example in approve["requestBody"]["content"]["application/json"]["examples"].values():
        TypeAdapter(ApprovalDecision).validate_python(example["value"])
    for operation, model in (
        ("resolveReview", ReviewDecision),
        ("resolveEscalation", EscalationDecision),
        ("rejectApplication", RejectionRequest),
    ):
        _, _, op = contract.operations[operation]
        model.model_validate(op["requestBody"]["content"]["application/json"]["example"])


# --- the queue stream --------------------------------------------------------------------


def read_queue_stream(client, act):
    import json
    import time

    worker = threading.Thread(target=lambda: (time.sleep(0.25), act()))
    worker.start()
    with client.stream("GET", "/api/queue/stream") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())
    worker.join()
    frames = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.partition(": ")[::2] for line in block.splitlines() if ": " in line)
        if fields.get("event") == "queue":
            frames.append({"id": fields.get("id"), **json.loads(fields["data"])})
    return frames


def test_queue_stream_announces_new_and_resolved_items_in_scope(env, build, contract):
    wide = build.application()
    narrow = build.application(zone=PUEBLA_ANGELOPOLIS)
    wide_item = item_for(env, wide["id"], "approval")

    def act():
        conn = connect(env.db_path)
        try:
            repo.open_queue_item(
                conn, wide["id"], "escalation", reason="wants_human", now=env.clock.now()
            )
            repo.open_queue_item(
                conn, narrow["id"], "escalation", reason="abuse", now=env.clock.now()
            )
        finally:
            conn.close()
        env.client(OPERATOR).post(
            f"/api/queue/approvals/{wide_item['id']}", json={"decision": "approved"}
        )

    frames = read_queue_stream(env.client(LA_PAZ), act)
    for frame in frames:
        contract.validate("StreamEvent", {k: frame[k] for k in ("type", "at", "data")})
    created = [f for f in frames if f["data"]["status"] == "open"]
    assert [(f["data"]["kind"], f["data"]["application_id"]) for f in created] == [
        ("escalation", wide["id"])
    ]
    assert created[0]["id"].startswith("q-")
    resolved = [f["data"] for f in frames if f["data"]["status"] == "resolved"]
    assert [(f["item_id"], f["resolution"]) for f in resolved] == [(wide_item["id"], "approved")]
    # La Paz cannot see the narrow Application, so nothing about it leaks.
    assert narrow["id"] not in str(frames)


def test_queue_stream_requires_a_session(env):
    assert env.client().get("/api/queue/stream").status_code == 401


def test_queue_poll_ends_when_the_session_is_revoked(env, build):
    from criba.api import queue as queue_module
    from criba.api.auth import COOKIE_NAME, hash_token, load_session_user

    build.application()
    client = env.client(PUEBLA)
    user = load_session_user(env.ctx, client.cookies[COOKIE_NAME])
    token_hash = hash_token(client.cookies[COOKIE_NAME])
    state = {
        "rowid": 0,
        "event_id": 0,
        "resolved_since": "2000-01-01T00:00:00Z",
        "seen_resolved": set(),
    }
    frames, alive = queue_module._poll_queue(env.ctx, user, token_hash, state)
    assert alive and frames
    assert client.post("/api/auth/logout").status_code == 204
    assert queue_module._poll_queue(env.ctx, user, token_hash, state) == ([], False)


def test_queue_stream_does_not_replay_old_items(env, build):
    build.application()
    frames = read_queue_stream(env.client(PUEBLA), lambda: None)
    assert frames == []


def test_a_failing_scheduler_does_not_undo_the_approval(env, build):
    app = build.application()
    item = item_for(env, app["id"], "approval")

    def boom(conn, application_id, action):
        raise RuntimeError("scheduler down")

    env.ctx.services.submit_action = boom
    response = env.client(PUEBLA).post(
        f"/api/queue/approvals/{item['id']}", json={"decision": "approved"}
    )
    assert response.status_code == 200
    assert repo.get_application(env.conn, app["id"])["stage"] == "approved"
