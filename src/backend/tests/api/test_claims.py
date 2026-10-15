"""Claims: claim, release, take over an idle Claim, and the Claim race."""

from __future__ import annotations

import threading
from datetime import timedelta

from fastapi.testclient import TestClient

from criba.db import repo

from .support import LA_PAZ, OPERATOR, PUEBLA, PUEBLA_2, PUEBLA_ANGELOPOLIS

PUEBLA_2_ID = "usr_rec_puebla_2"


def claim(client, app_id):
    return client.post(f"/api/applications/{app_id}/claim")


def test_claim_returns_the_claim(env, build, contract):
    app = build.application()
    response = claim(env.client(PUEBLA), app["id"])
    body = contract.conforms(response, "claimApplication")
    assert body["user_id"] == "usr_rec_puebla"
    assert body["user_name"] == "Recruiter Puebla (synthetic)"
    assert body["idle"] is False
    stored = repo.get_application(env.conn, app["id"])
    assert stored["claim_user_id"] == "usr_rec_puebla"
    assert [e["kind"] for e in repo.list_events(env.conn, app["id"])][-1] == "claim_taken"


def test_second_recruiter_cannot_claim(env, build, contract):
    app = build.application()
    claim(env.client(PUEBLA), app["id"])
    response = claim(env.client(PUEBLA_2), app["id"])
    body = contract.conforms(response, "claimApplication")
    assert response.status_code == 409
    assert body["error"] == "already_claimed"
    assert "Recruiter Puebla (synthetic)" in body["message"]
    assert repo.get_application(env.conn, app["id"])["claim_user_id"] == "usr_rec_puebla"


def test_claiming_twice_is_idempotent_for_the_holder(env, build):
    app = build.application()
    client = env.client(PUEBLA)
    first = claim(client, app["id"]).json()
    again = claim(client, app["id"])
    assert again.status_code == 200
    assert again.json() == first
    claims = [e for e in repo.list_events(env.conn, app["id"]) if e["kind"] == "claim_taken"]
    assert len(claims) == 1


def test_an_idle_claim_is_not_claimable_only_takeable(env, build):
    app = build.application()
    claim(env.client(PUEBLA), app["id"])
    env.clock.advance(timedelta(days=8))
    response = claim(env.client(PUEBLA_2), app["id"])
    assert response.status_code == 409
    assert "take-over" in response.json()["message"]
    assert repo.get_application(env.conn, app["id"])["claim_user_id"] == "usr_rec_puebla"


def test_claim_on_a_closed_application_is_a_409(env, build):
    app = build.application(stage="knockout_license")
    response = claim(env.client(PUEBLA), app["id"])
    assert response.status_code == 409
    assert response.json()["error"] == "application_closed"


def test_release_by_the_holder(env, build, contract):
    app = build.application()
    holder = env.client(PUEBLA)
    claim(holder, app["id"])
    response = holder.delete(f"/api/applications/{app['id']}/claim")
    assert response.status_code == 204
    contract.conforms(response, "releaseClaim")
    assert repo.get_application(env.conn, app["id"])["claim_user_id"] is None
    assert claim(env.client(PUEBLA_2), app["id"]).status_code == 200


def test_release_by_someone_else_is_forbidden_but_an_operator_may(env, build, contract):
    app = build.application()
    claim(env.client(PUEBLA), app["id"])
    other = env.client(PUEBLA_2).delete(f"/api/applications/{app['id']}/claim")
    body = contract.conforms(other, "releaseClaim")
    assert other.status_code == 403
    assert body["error"] == "not_claim_holder"
    assert env.client(OPERATOR).delete(f"/api/applications/{app['id']}/claim").status_code == 204


def test_releasing_an_unclaimed_application_is_204(env, build):
    app = build.application()
    assert env.client(PUEBLA).delete(f"/api/applications/{app['id']}/claim").status_code == 204


def test_take_over_an_idle_claim(env, build, contract):
    app = build.application()
    claim(env.client(PUEBLA), app["id"])
    env.clock.advance(timedelta(days=7, minutes=1))
    response = env.client(PUEBLA_2).post(f"/api/applications/{app['id']}/claim/takeover")
    body = contract.conforms(response, "takeOverIdleClaim")
    assert body["user_id"] == PUEBLA_2_ID
    assert body["idle"] is False
    events = repo.list_events(env.conn, app["id"])
    assert events[-1]["kind"] == "claim_taken_over"
    assert events[-1]["data"] == {"previous_user_id": "usr_rec_puebla"}


def test_take_over_refuses_an_active_claim(env, build, contract):
    app = build.application()
    claim(env.client(PUEBLA), app["id"])
    env.clock.advance(timedelta(days=6))
    response = env.client(PUEBLA_2).post(f"/api/applications/{app['id']}/claim/takeover")
    body = contract.conforms(response, "takeOverIdleClaim")
    assert response.status_code == 409
    assert body["error"] == "already_claimed"


def test_take_over_without_a_claim_is_a_409(env, build):
    app = build.application()
    response = env.client(PUEBLA).post(f"/api/applications/{app['id']}/claim/takeover")
    assert response.status_code == 409


def test_recruiter_actions_keep_a_claim_from_going_idle(env, build):
    app = build.application()
    holder = env.client(PUEBLA)
    claim(holder, app["id"])
    env.clock.advance(timedelta(days=6))
    holder.post(f"/api/applications/{app['id']}/takeover")
    env.clock.advance(timedelta(days=6))
    # 12 days after the claim, but only 6 after the last action.
    response = env.client(PUEBLA_2).post(f"/api/applications/{app['id']}/claim/takeover")
    assert response.status_code == 409


def test_a_recruiter_outside_the_scope_cannot_claim(env, build):
    app = build.application(zone=PUEBLA_ANGELOPOLIS)
    assert claim(env.client(LA_PAZ), app["id"]).status_code == 404


def _race(env, app_id, emails, path):
    """Fire one request per user at the same moment and return the status codes."""
    clients = [env.client(email) for email in emails]
    barrier = threading.Barrier(len(clients))
    results: list[int] = []
    lock = threading.Lock()

    def worker(client: TestClient) -> None:
        barrier.wait()
        status = client.post(f"/api/applications/{app_id}{path}").status_code
        with lock:
            results.append(status)

    threads = [threading.Thread(target=worker, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return sorted(results)


def _more_recruiters(env, count):
    pw = env.conn.execute("SELECT password_hash FROM users WHERE id = 'usr_operator'").fetchone()[0]
    emails = []
    for n in range(count):
        email = f"racer{n}@sazon.example"
        env.conn.execute(
            "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
            " VALUES (?, 'grupo_sazon', ?, ?, 'recruiter', ?, '2026-10-09T10:00:00Z')",
            (f"usr_racer{n}", email, f"Racer {n}", pw),
        )
        env.conn.execute(
            "INSERT INTO user_locations SELECT ?, location_id FROM user_locations"
            " WHERE user_id = 'usr_rec_puebla'",
            (f"usr_racer{n}",),
        )
        emails.append(email)
    return emails


def test_claim_race_has_exactly_one_winner(env, build):
    app = build.application()
    emails = _more_recruiters(env, 8)
    statuses = _race(env, app["id"], emails, "/claim")
    assert statuses == [200] + [409] * 7
    holder = repo.get_application(env.conn, app["id"])["claim_user_id"]
    assert holder in {f"usr_racer{n}" for n in range(8)}
    taken = [e for e in repo.list_events(env.conn, app["id"]) if e["kind"] == "claim_taken"]
    assert len(taken) == 1
    assert taken[0]["user_id"] == holder


def test_take_over_race_has_exactly_one_winner(env, build):
    app = build.application()
    claim(env.client(PUEBLA), app["id"])
    env.clock.advance(timedelta(days=8))
    emails = _more_recruiters(env, 6)
    statuses = _race(env, app["id"], emails, "/claim/takeover")
    assert statuses == [200] + [409] * 5
    events = [e["kind"] for e in repo.list_events(env.conn, app["id"])]
    assert events.count("claim_taken_over") == 1
