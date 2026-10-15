"""The Recruiter's copy of the Interview invite: ``GET /api/applications/{id}/booking/invite.ics``.

ADR 0030 delivers the ``.ics`` to both sides. The Candidate gets it from the public booking
page; the Recruiter downloads it here. Criba never writes to an external calendar (ADR 0012).
"""

from __future__ import annotations

import pytest

from .support import LA_PAZ, OPERATOR, PUEBLA, PUEBLA_2, PUEBLA_ANGELOPOLIS

SLOT = "slot_mx_puebla_centro_20261014_1000"  # Wednesday 14 Oct, 10:00 in Puebla


def invite_url(app_id: str) -> str:
    return f"/api/applications/{app_id}/booking/invite.ics"


def uid_of(ics: str) -> str:
    return next(line for line in ics.split("\r\n") if line.startswith("UID:"))


@pytest.fixture
def booked(env, build):
    """An Approved Application (Angelópolis and Centro) that the Candidate booked from the page."""
    app = build.application(stage="approved", zone=PUEBLA_ANGELOPOLIS)
    token = env.client(PUEBLA).post(f"/api/applications/{app['id']}/booking-link").json()["token"]
    page = env.client().post(f"/api/public/booking/{token}", json={"slot_id": SLOT})
    assert page.status_code == 201
    return app, token


@pytest.mark.parametrize("email", [OPERATOR, PUEBLA, PUEBLA_2])
def test_operator_and_covering_recruiters_get_the_candidates_event(env, booked, email):
    app, token = booked
    response = env.client(email).get(invite_url(app["id"]))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert 'filename="entrevista.ics"' in response.headers["content-disposition"]
    candidate = env.client().get(f"/api/public/booking/{token}/invite.ics")
    assert candidate.status_code == 200
    assert uid_of(response.text) == uid_of(candidate.text)
    assert response.text == candidate.text  # one builder, so one event in both calendars
    lines = response.text.split("\r\n")
    assert "DTSTART:20261014T160000Z" in lines
    assert "STATUS:CONFIRMED" in lines


def test_the_uid_is_the_stored_booking_uid(env, booked):
    app, _ = booked
    stored = env.conn.execute(
        "SELECT ics_uid FROM bookings WHERE application_id = ?", (app["id"],)
    ).fetchone()[0]
    assert f"UID:{stored}" in env.client(PUEBLA).get(invite_url(app["id"])).text.split("\r\n")


def test_a_recruiter_outside_the_locations_gets_404(env, booked):
    app, _ = booked
    response = env.client(LA_PAZ).get(invite_url(app["id"]))
    assert response.status_code == 404


def test_a_session_is_required(env, booked):
    app, _ = booked
    assert env.client().get(invite_url(app["id"])).status_code == 401


def test_an_unknown_application_is_404(env):
    assert env.client(OPERATOR).get(invite_url("app_missing")).status_code == 404


def test_no_booking_is_404(env, build):
    app = build.application(stage="approved", zone=PUEBLA_ANGELOPOLIS)
    for email in (OPERATOR, PUEBLA):
        response = env.client(email).get(invite_url(app["id"]))
        assert response.status_code == 404
        assert response.json()["message"] == "There is no booked Interview."


def test_a_released_booking_is_404(env, booked):
    app, _ = booked
    recruiter = env.client(PUEBLA)
    assert recruiter.get(invite_url(app["id"])).status_code == 200
    rejected = recruiter.post(
        f"/api/applications/{app['id']}/rejection", json={"reason": "no_show"}
    )
    assert rejected.status_code == 200
    status = env.conn.execute(
        "SELECT status FROM bookings WHERE application_id = ?", (app["id"],)
    ).fetchone()[0]
    assert status == "released"
    assert recruiter.get(invite_url(app["id"])).status_code == 404
