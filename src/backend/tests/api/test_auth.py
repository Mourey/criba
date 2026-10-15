"""Auth: login, session cookie, logout, expiry, throttle, Location scope on /locations."""

from __future__ import annotations

from datetime import timedelta

import pytest

from criba.api.auth import COOKIE_NAME, hash_token
from criba.api.context import LoginThrottle

from .support import LA_PAZ, MADRID, OPERATOR, PASSWORD, PUEBLA

pytestmark = pytest.mark.usefixtures("env")


def test_login_sets_http_only_session_cookie_and_returns_me(env, contract):
    client = env.client()
    response = client.post("/api/auth/login", json={"email": PUEBLA, "password": PASSWORD})
    body = contract.conforms(response, "login")
    assert body["user"]["role"] == "recruiter"
    assert body["user"]["email"] == PUEBLA
    assert len(body["locations"]) == 3
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie


def test_cookie_is_secure_when_configured(make_env):
    env = make_env(cookie_secure=True)
    response = env.client().post("/api/auth/login", json={"email": PUEBLA, "password": PASSWORD})
    assert "Secure" in response.headers["set-cookie"]


def test_session_token_is_stored_only_as_a_hash(env):
    client = env.client(PUEBLA)
    token = client.cookies[COOKIE_NAME]
    ids = [r[0] for r in env.conn.execute("SELECT id FROM auth_sessions")]
    assert hash_token(token) in ids
    assert token not in ids


def test_operator_me_has_no_location_scope(env, contract):
    me = env.client(OPERATOR).get("/api/auth/me")
    body = contract.conforms(me, "getMe")
    assert body["user"]["role"] == "operator"
    assert body["locations"] == []


def test_me_requires_a_session(env, contract):
    response = env.client().get("/api/auth/me")
    assert response.status_code == 401
    assert contract.conforms(response, "getMe")["error"] == "unauthorized"


@pytest.mark.parametrize(
    ("email", "password"),
    [(PUEBLA, "wrong"), ("nobody@sazon.example", PASSWORD), (PUEBLA.upper(), "wrong")],
)
def test_bad_credentials_are_a_plain_401(env, contract, email, password):
    response = env.client().post("/api/auth/login", json={"email": email, "password": password})
    body = contract.conforms(response, "login")
    assert response.status_code == 401
    assert body["error"] == "unauthorized"
    assert COOKIE_NAME not in response.cookies


def test_login_email_is_case_insensitive(env):
    response = env.client().post(
        "/api/auth/login", json={"email": PUEBLA.upper(), "password": PASSWORD}
    )
    assert response.status_code == 200


def test_login_validates_the_body(env, contract):
    response = env.client().post("/api/auth/login", json={"email": "not-an-email"})
    body = contract.conforms(response, "login")
    assert response.status_code == 422
    assert {d["field"] for d in body["details"]} == {"email", "password"}


def test_inactive_user_cannot_log_in(env):
    env.conn.execute("UPDATE users SET active = 0 WHERE email = ?", (PUEBLA,))
    response = env.client().post("/api/auth/login", json={"email": PUEBLA, "password": PASSWORD})
    assert response.status_code == 401


def test_deactivating_a_user_ends_their_session(env):
    client = env.client(PUEBLA)
    assert client.get("/api/auth/me").status_code == 200
    env.conn.execute("UPDATE users SET active = 0 WHERE email = ?", (PUEBLA,))
    assert client.get("/api/auth/me").status_code == 401


def test_logout_revokes_the_session(env, contract):
    client = env.client(PUEBLA)
    stolen = client.cookies[COOKIE_NAME]
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    contract.conforms(response, "logout")
    assert client.get("/api/auth/me").status_code == 401
    # The revoked token is dead even when replayed by hand.
    replay = env.client()
    replay.cookies.set(COOKIE_NAME, stolen)
    assert replay.get("/api/auth/me").status_code == 401


def test_logout_without_a_session_is_still_204(env):
    assert env.client().post("/api/auth/logout").status_code == 204


def test_session_expires(env):
    client = env.client(PUEBLA)
    env.session_clock.advance(env.ctx.settings.session_ttl - timedelta(minutes=1))
    assert client.get("/api/auth/me").status_code == 200
    env.session_clock.advance(timedelta(minutes=2))
    assert client.get("/api/auth/me").status_code == 401


def test_sim_clock_does_not_expire_sessions(env):
    """Sessions follow the wall clock: advancing the simulated clock must not log anyone out."""
    client = env.client(PUEBLA)
    env.clock.advance(timedelta(days=30))
    assert client.get("/api/auth/me").status_code == 200


def test_repeated_failures_lock_the_email_without_locking_others(env):
    client = env.client()
    for _ in range(5):
        assert (
            client.post("/api/auth/login", json={"email": PUEBLA, "password": "x"}).status_code
            == 401
        )
    locked = client.post("/api/auth/login", json={"email": PUEBLA, "password": PASSWORD})
    assert locked.status_code == 401
    assert "Too many attempts" in locked.json()["message"]
    assert (
        client.post("/api/auth/login", json={"email": MADRID, "password": PASSWORD}).status_code
        == 200
    )


def test_login_throttle_window_expires():
    throttle = LoginThrottle(limit=2, window_seconds=10)
    throttle.fail("a", 0)
    throttle.fail("a", 1)
    assert throttle.blocked("a", 2)
    assert not throttle.blocked("a", 20)
    throttle.fail("b", 0)
    throttle.clear("b")
    assert not throttle.blocked("b", 0)


def test_locations_are_scoped_for_recruiters(env, contract):
    recruiter = contract.conforms(env.client(LA_PAZ).get("/api/locations"), "listLocations")
    assert [loc["id"] for loc in recruiter["items"]] == ["loc_mx_puebla_la_paz"]
    everyone = contract.conforms(env.client(OPERATOR).get("/api/locations"), "listLocations")
    assert len(everyone["items"]) == 45
    spain = env.client(OPERATOR).get("/api/locations", params={"country": "ES"}).json()
    assert len(spain["items"]) == 25
    assert env.client(PUEBLA).get("/api/locations", params={"country": "ES"}).json() == {
        "items": []
    }


def test_locations_rejects_an_unknown_country(env):
    assert env.client(OPERATOR).get("/api/locations", params={"country": "FR"}).status_code == 422


def test_every_protected_operation_requires_a_session(env, contract):
    """Only login, logout and the public booking routes answer without a session."""
    anonymous = env.client()
    public = {"login", "logout", "getBookingPage", "bookFromPage", "downloadInvite"}
    for operation_id, (url, method, _) in contract.operations.items():
        if operation_id in public:
            continue
        path = (
            url.replace("{application_id}", "app_x")
            .replace("{item_id}", "qi_x")
            .replace("{slot_id}", "slot_x")
            .replace("{token}", "t")
        )
        if path.endswith("/stream"):
            continue  # streams are covered by the takeover tests
        response = anonymous.request(method.upper(), path, json={} if method != "get" else None)
        assert response.status_code == 401, f"{operation_id}: {response.status_code}"
