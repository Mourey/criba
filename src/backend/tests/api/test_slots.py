"""Slots, signed booking links and the public booking page."""

from __future__ import annotations

from datetime import timedelta

import pytest

from criba.api.context import ApiSettings
from criba.db import repo
from criba.tools import (
    BookingTokenError,
    BookingTokenExpired,
    decode_booking_token,
    make_booking_token,
)
from criba.tools.booking import booking_link_ttl, booking_url

from .support import (
    LA_PAZ,
    LOC_PUEBLA_CENTRO,
    LOC_PUEBLA_LA_PAZ,
    NOW,
    OPERATOR,
    PUEBLA,
    PUEBLA_2,
    PUEBLA_ANGELOPOLIS,
    SECRET,
    Builder,
    at,
)

SLOT_WED_10 = "slot_mx_puebla_centro_20261014_1000"  # Wednesday 14 Oct, 10:00 in Puebla
SLOT_WED_12 = "slot_mx_puebla_centro_20261014_1200"
LA_PAZ_SLOT = "slot_mx_puebla_la_paz_20261014_1000"


def slots_json(client, **params):
    return client.get("/api/slots", params=params).json()["items"]


# --- listing -----------------------------------------------------------------------------


def test_list_slots_conforms_and_labels_the_local_time(env, contract):
    response = env.client(PUEBLA).get("/api/slots", params={"location_id": LOC_PUEBLA_CENTRO})
    body = contract.conforms(response, "listSlots")
    wed = next(s for s in body["items"] if s["id"] == SLOT_WED_10)
    assert wed["starts_at"] == "2026-10-14T16:00:00Z"
    assert wed["local_label"] == "mié 14 · 10:00"  # 14 October 2026 is a Wednesday
    assert wed["location_name"] == "Sazón Puebla Centro"
    assert wed["status"] == "open"
    assert wed["booking"] is None
    assert body["items"] == sorted(body["items"], key=lambda s: s["starts_at"])


def test_slots_are_scoped_to_the_recruiters_locations(env):
    assert {s["location_id"] for s in slots_json(env.client(LA_PAZ))} == {LOC_PUEBLA_LA_PAZ}
    assert {s["location_id"] for s in slots_json(env.client(PUEBLA))} == {
        "loc_mx_puebla_angelopolis",
        LOC_PUEBLA_CENTRO,
        LOC_PUEBLA_LA_PAZ,
    }
    assert len({s["location_id"] for s in slots_json(env.client(OPERATOR))}) == 45
    assert slots_json(env.client(LA_PAZ), location_id=LOC_PUEBLA_CENTRO) == []


def test_slot_filters(env):
    op = env.client(OPERATOR)
    day = slots_json(
        op,
        location_id=LOC_PUEBLA_CENTRO,
        **{"from": "2026-10-14T00:00:00Z", "to": "2026-10-14T23:59:59Z"},
    )
    assert [s["id"] for s in day] == [
        SLOT_WED_10,
        SLOT_WED_12,
        "slot_mx_puebla_centro_20261014_1600",
    ]
    assert all(s["status"] == "open" for s in day)
    env.clock.set(at(60 * 24 * 5.5))  # 14 Oct 22:00Z
    past = slots_json(op, location_id=LOC_PUEBLA_CENTRO, status="past")
    assert SLOT_WED_10 in {s["id"] for s in past}
    assert op.get("/api/slots", params={"status": "weird"}).status_code == 422


# --- create, move, cancel ----------------------------------------------------------------


def create(client, location=LOC_PUEBLA_CENTRO, *times):
    return client.post(
        "/api/slots",
        json={"location_id": location, "slots": [{"starts_at": s, "ends_at": e} for s, e in times]},
    )


def test_create_slots(env, contract):
    response = create(
        env.client(PUEBLA),
        LOC_PUEBLA_CENTRO,
        ("2026-10-20T16:00:00Z", "2026-10-20T16:30:00Z"),
        ("2026-10-20T18:00:00-06:00", "2026-10-20T18:30:00-06:00"),
    )
    body = contract.conforms(response, "createSlots")
    assert response.status_code == 201
    assert [s["starts_at"] for s in body["items"]] == [
        "2026-10-20T16:00:00Z",
        "2026-10-21T00:00:00Z",
    ]
    assert body["items"][0]["status"] == "open"
    assert body["items"][0]["local_label"] == "mar 20 · 10:00"
    row = env.conn.execute(
        "SELECT created_by FROM interview_slots WHERE id = ?", (body["items"][0]["id"],)
    ).fetchone()
    assert row[0] == "usr_rec_puebla"


def test_create_slots_rules(env, contract):
    recruiter = env.client(PUEBLA)
    ok = ("2026-10-20T16:00:00Z", "2026-10-20T16:30:00Z")
    outside = create(env.client(LA_PAZ), LOC_PUEBLA_CENTRO, ok)
    assert contract.conforms(outside, "createSlots")["error"] == "not_found"
    assert outside.status_code == 404
    assert (
        env.client(OPERATOR)
        .post(
            "/api/slots",
            json={
                "location_id": LOC_PUEBLA_CENTRO,
                "slots": [{"starts_at": ok[0], "ends_at": ok[1]}],
            },
        )
        .status_code
        == 201
    )
    cases = {
        "past": create(
            recruiter, LOC_PUEBLA_CENTRO, ("2026-10-08T16:00:00Z", "2026-10-08T16:30:00Z")
        ),
        "backwards": create(
            recruiter, LOC_PUEBLA_CENTRO, ("2026-10-21T16:30:00Z", "2026-10-21T16:00:00Z")
        ),
        "too long": create(
            recruiter, LOC_PUEBLA_CENTRO, ("2026-10-21T08:00:00Z", "2026-10-21T20:00:00Z")
        ),
        "naive": create(
            recruiter, LOC_PUEBLA_CENTRO, ("2026-10-21T16:00:00", "2026-10-21T16:30:00")
        ),
        "duplicate": create(recruiter, LOC_PUEBLA_CENTRO, ok),
        "twice in one request": create(
            recruiter,
            LOC_PUEBLA_CENTRO,
            ("2026-10-22T16:00:00Z", "2026-10-22T16:30:00Z"),
            ("2026-10-22T16:00:00Z", "2026-10-22T17:00:00Z"),
        ),
        "unknown location": env.client(OPERATOR).post(
            "/api/slots",
            json={"location_id": "loc_nope", "slots": [{"starts_at": ok[0], "ends_at": ok[1]}]},
        ),
        "none": recruiter.post("/api/slots", json={"location_id": LOC_PUEBLA_CENTRO, "slots": []}),
    }
    for name, response in cases.items():
        assert response.status_code == 422, name
        assert contract.conforms(response, "createSlots")["error"] == "validation_error", name
    # A failed batch creates nothing.
    assert (
        env.conn.execute(
            "SELECT count(*) FROM interview_slots WHERE starts_at LIKE '2026-10-22%'"
            " AND location_id = ?",
            (LOC_PUEBLA_CENTRO,),
        ).fetchone()[0]
        == 0
    )


def test_move_a_slot(env, contract):
    response = env.client(PUEBLA).patch(
        f"/api/slots/{SLOT_WED_10}",
        json={"starts_at": "2026-10-14T17:00:00Z", "ends_at": "2026-10-14T17:30:00Z"},
    )
    body = contract.conforms(response, "updateSlot")
    assert body["starts_at"] == "2026-10-14T17:00:00Z"
    assert body["local_label"] == "mié 14 · 11:00"


def test_move_rules(env, build, contract):
    recruiter = env.client(PUEBLA)
    new = {"starts_at": "2026-10-14T17:00:00Z", "ends_at": "2026-10-14T17:30:00Z"}
    assert env.client(LA_PAZ).patch(f"/api/slots/{SLOT_WED_10}", json=new).status_code == 404
    assert recruiter.patch("/api/slots/slot_nope", json=new).status_code == 404
    clash = recruiter.patch(
        f"/api/slots/{SLOT_WED_10}",
        json={"starts_at": "2026-10-14T18:00:00Z", "ends_at": "2026-10-14T18:30:00Z"},
    )
    assert clash.status_code == 422  # 12:00 local already exists
    past = recruiter.patch(
        f"/api/slots/{SLOT_WED_10}",
        json={"starts_at": "2026-10-01T16:00:00Z", "ends_at": "2026-10-01T16:30:00Z"},
    )
    assert past.status_code == 422
    app = build.application(stage="approved")
    env.ctx.services.booking.book(
        env.conn, application_id=app["id"], slot_id=SLOT_WED_10, via="chat", now=NOW
    )
    booked = recruiter.patch(f"/api/slots/{SLOT_WED_10}", json=new)
    assert contract.conforms(booked, "updateSlot")["error"] == "slot_booked"
    assert booked.status_code == 409
    recruiter.delete(f"/api/slots/{SLOT_WED_12}")
    cancelled = recruiter.patch(f"/api/slots/{SLOT_WED_12}", json=new)
    assert cancelled.status_code == 409


def test_cancel_a_slot(env, build, contract):
    recruiter = env.client(PUEBLA)
    response = recruiter.delete(f"/api/slots/{SLOT_WED_10}")
    assert response.status_code == 204
    contract.conforms(response, "cancelSlot")
    assert (
        next(
            s
            for s in slots_json(recruiter, location_id=LOC_PUEBLA_CENTRO)
            if s["id"] == SLOT_WED_10
        )["status"]
        == "cancelled"
    )
    assert recruiter.delete(f"/api/slots/{SLOT_WED_10}").status_code == 204  # idempotent
    assert env.client(LA_PAZ).delete(f"/api/slots/{SLOT_WED_12}").status_code == 404


def test_cancelling_a_booked_slot_is_a_409_and_shows_the_booking(env, build, contract):
    app = build.application(stage="approved", name="Luis Hernández")
    env.ctx.services.booking.book(
        env.conn, application_id=app["id"], slot_id=SLOT_WED_10, via="chat", now=NOW
    )
    recruiter = env.client(PUEBLA)
    response = recruiter.delete(f"/api/slots/{SLOT_WED_10}")
    assert contract.conforms(response, "cancelSlot")["error"] == "slot_booked"
    assert response.status_code == 409
    slot = next(
        s for s in slots_json(recruiter, location_id=LOC_PUEBLA_CENTRO) if s["id"] == SLOT_WED_10
    )
    assert slot["status"] == "booked"
    assert slot["booking"]["application_id"] == app["id"]
    assert slot["booking"]["candidate_name"] == "Luis Hernández"
    assert slot["booking"]["booked_via"] == "chat"


# --- booking links -----------------------------------------------------------------------


def link(client, app_id):
    return client.post(f"/api/applications/{app_id}/booking-link")


def test_booking_link_for_an_approved_application(env, build, contract):
    app = build.application(stage="approved")
    env.clock.set(at(30))
    response = link(env.client(PUEBLA), app["id"])
    body = contract.conforms(response, "createBookingLink")
    assert response.status_code == 201
    assert body["expires_at"] == "2026-10-16T10:30:00Z"
    assert body["url"] == f"http://localhost:8080/book/{body['token']}"
    application_id, expires = decode_booking_token(
        body["token"], now=env.clock.now(), secret=SECRET
    )
    assert application_id == app["id"]
    assert expires.isoformat() == "2026-10-16T10:30:00+00:00"
    sent = [e for e in repo.list_events(env.conn, app["id"]) if e["kind"] == "booking_link_sent"]
    assert sent[0]["data"] == {"expires_at": "2026-10-16T10:30:00Z"}


def test_booking_link_needs_an_approved_open_application(env, build, contract):
    qualified = build.application()
    response = link(env.client(PUEBLA), qualified["id"])
    assert contract.conforms(response, "createBookingLink")["error"] == "wrong_stage"
    closed = build.application(stage="knockout_license")
    assert link(env.client(OPERATOR), closed["id"]).json()["error"] == "application_closed"
    assert link(env.client(PUEBLA), "app_nope").status_code == 404


def test_booking_link_uses_the_configured_origin(make_env):
    env = make_env(public_base_url="https://empleo.gruposazon.example")
    app = Builder(env).application(stage="approved")
    body = link(env.client(PUEBLA), app["id"]).json()
    assert body["url"] == f"https://empleo.gruposazon.example/book/{body['token']}"


@pytest.mark.parametrize("days", ["3", "0.5", "14"])
def test_booking_link_lifetime_is_one_setting_for_the_tool_and_the_api(
    make_env, monkeypatch: pytest.MonkeyPatch, days: str
):
    """``CRIBA_BOOKING_LINK_DAYS`` sets both the agent tool's link and the Recruiter link."""
    monkeypatch.setenv("CRIBA_BOOKING_LINK_DAYS", days)
    env = make_env(booking_link_ttl=ApiSettings.from_env().booking_link_ttl)
    app = Builder(env).application(stage="approved")
    env.clock.set(at(30))
    api_body = link(env.client(PUEBLA), app["id"]).json()
    tool_token = booking_url(app["id"], now=env.clock.now(), secret=SECRET).rsplit("/", 1)[1]
    _, tool_expires = decode_booking_token(tool_token, now=env.clock.now(), secret=SECRET)
    _, api_expires = decode_booking_token(api_body["token"], now=env.clock.now(), secret=SECRET)
    assert tool_expires == api_expires == env.clock.now() + timedelta(days=float(days))


def test_booking_link_lifetime_defaults_to_seven_days_and_rejects_nonsense():
    assert booking_link_ttl({}) == timedelta(days=7)
    assert ApiSettings.from_env({}).booking_link_ttl == timedelta(days=7)
    for bad in ("soon", "0", "-2"):
        with pytest.raises(ValueError):
            booking_link_ttl({"CRIBA_BOOKING_LINK_DAYS": bad})


# --- tokens ------------------------------------------------------------------------------


def test_token_round_trip_expiry_and_tampering():
    expires = NOW + timedelta(days=7)
    token = make_booking_token("app_1", expires, secret=SECRET)
    assert decode_booking_token(token, now=NOW, secret=SECRET) == ("app_1", expires)
    with pytest.raises(BookingTokenExpired) as expired:
        decode_booking_token(token, now=expires, secret=SECRET)
    assert expired.value.application_id == "app_1"
    payload, signature = token.split(".")
    forged = make_booking_token("app_2", expires, secret=SECRET).split(".")[0]
    for bad in (
        f"{forged}.{signature}",
        f"{payload}.{signature[:-2]}AA",
        token + "x",
        payload,
        "",
        "a.b.c",
        "....",
    ):
        with pytest.raises(BookingTokenError):
            decode_booking_token(bad, now=NOW, secret=SECRET)
    with pytest.raises(BookingTokenError):
        decode_booking_token(token, now=NOW, secret=b"another-secret-0123456789")


# --- the public page ---------------------------------------------------------------------


@pytest.fixture
def booking(env, build):
    """An Approved Application in Angelópolis and Centro, with a signed link."""
    app = build.application(stage="approved", zone=PUEBLA_ANGELOPOLIS, name="Luis Hernández")
    token = link(env.client(PUEBLA), app["id"]).json()["token"]
    return app, token


def test_public_page(env, booking, contract):
    app, token = booking
    response = env.client().get(f"/api/public/booking/{token}")  # no session
    body = contract.conforms(response, "getBookingPage")
    assert body["first_name"] == "Luis"
    assert body["language"] == "es-MX"
    assert body["expires_at"] == "2026-10-16T10:00:00Z"
    assert body["current_booking"] is None
    assert {s["location_name"] for s in body["slots"]} == {
        "Sazón Puebla Angelópolis",
        "Sazón Puebla Centro",
    }
    assert all(s["address"] for s in body["slots"])
    assert body["slots"] == sorted(body["slots"], key=lambda s: s["starts_at"])
    first = next(s for s in body["slots"] if s["slot_id"] == SLOT_WED_10)
    assert first["local_label"] == "mié 14 · 10:00"
    # Only what the page needs: no ids, phone or full name.
    text = response.text
    for secret in (app["id"], app["candidate_id"], "Hernández", "+5255"):
        assert secret not in text


def test_public_page_hides_taken_cancelled_and_past_slots(env, booking):
    app, token = booking
    other = env.client(PUEBLA)
    other.delete(f"/api/slots/{SLOT_WED_12}")
    page = env.client().get(f"/api/public/booking/{token}").json()
    ids = {s["slot_id"] for s in page["slots"]}
    assert SLOT_WED_12 not in ids and SLOT_WED_10 in ids
    env.clock.set(at(60 * 24 * 5))  # 14 Oct 10:00Z, before the 16:00Z slot
    env.conn.execute(
        "UPDATE interview_slots SET starts_at = '2026-10-14T09:00:00Z',"
        " ends_at = '2026-10-14T09:30:00Z' WHERE id = ?",
        (SLOT_WED_10,),
    )
    token_now = link(env.client(PUEBLA), app["id"]).json()["token"]
    ids = {
        s["slot_id"] for s in env.client().get(f"/api/public/booking/{token_now}").json()["slots"]
    }
    assert SLOT_WED_10 not in ids


def test_book_from_the_page(env, booking, contract):
    app, token = booking
    public = env.client()
    response = public.post(f"/api/public/booking/{token}", json={"slot_id": SLOT_WED_10})
    body = contract.conforms(response, "bookFromPage")
    assert response.status_code == 201
    assert body["interview"]["starts_at"] == "2026-10-14T16:00:00Z"
    assert body["interview"]["location_name"] == "Sazón Puebla Centro"
    assert body["interview"]["local_label"] == "mié 14 · 10:00"
    assert body["message"] == (
        "¡Listo, Luis! Tu entrevista quedó para el miércoles 14 a las 10:00 en Sazón Puebla "
        "Centro. Descarga la invitación para tu calendario."
    )
    assert body["ics_url"] == f"/api/public/booking/{token}/invite.ics"
    stored = repo.get_application(env.conn, app["id"])
    assert stored["stage"] == "interview_booked" and stored["status"] == "open"
    kinds = [e["kind"] for e in repo.list_events(env.conn, app["id"])]
    assert kinds.count("interview_booked") == 1
    row = env.conn.execute(
        "SELECT booked_via FROM bookings WHERE application_id = ?", (app["id"],)
    ).fetchone()
    assert row[0] == "booking_page"
    assert env.notified[-1] == (app["id"], "interview_booked")
    page = public.get(f"/api/public/booking/{token}").json()
    assert page["current_booking"]["starts_at"] == "2026-10-14T16:00:00Z"
    assert SLOT_WED_10 not in {s["slot_id"] for s in page["slots"]}
    recruiter_view = next(
        s
        for s in slots_json(env.client(PUEBLA), location_id=LOC_PUEBLA_CENTRO)
        if s["id"] == SLOT_WED_10
    )
    assert recruiter_view["booking"]["booked_via"] == "booking_page"


def test_booking_in_english(env, build):
    app = build.application(stage="approved", language="en")
    token = link(env.client(PUEBLA), app["id"]).json()["token"]
    body = env.client().post(f"/api/public/booking/{token}", json={"slot_id": SLOT_WED_10}).json()
    assert body["message"].startswith("Done, Luis! Your interview is on Wednesday the 14 at 10:00")


def test_two_candidates_cannot_take_the_same_slot(env, build, booking, contract):
    _, token = booking
    other = build.application(stage="approved", zone=PUEBLA_ANGELOPOLIS, name="María Pérez")
    other_token = link(env.client(PUEBLA), other["id"]).json()["token"]
    public = env.client()
    assert (
        public.post(f"/api/public/booking/{token}", json={"slot_id": SLOT_WED_10}).status_code
        == 201
    )
    clash = public.post(f"/api/public/booking/{other_token}", json={"slot_id": SLOT_WED_10})
    body = contract.conforms(clash, "bookFromPage")
    assert clash.status_code == 409
    assert body["error"] == "slot_taken"
    assert "Elige otro" in body["message"]
    assert repo.get_application(env.conn, other["id"])["stage"] == "approved"


def test_rebooking_moves_the_interview_and_frees_the_old_slot(env, booking):
    app, token = booking
    public = env.client()
    public.post(f"/api/public/booking/{token}", json={"slot_id": SLOT_WED_10})
    assert (
        public.post(f"/api/public/booking/{token}", json={"slot_id": SLOT_WED_10}).status_code
        == 409
    )
    moved = public.post(f"/api/public/booking/{token}", json={"slot_id": SLOT_WED_12})
    assert moved.status_code == 201
    page = public.get(f"/api/public/booking/{token}").json()
    assert page["current_booking"]["starts_at"] == "2026-10-14T18:00:00Z"
    assert SLOT_WED_10 in {s["slot_id"] for s in page["slots"]}
    kinds = [e["kind"] for e in repo.list_events(env.conn, app["id"])]
    assert "interview_rescheduled" in kinds and "interview_slot_released" in kinds
    assert (
        env.conn.execute(
            "SELECT count(*) FROM bookings WHERE application_id = ? AND status = 'booked'",
            (app["id"],),
        ).fetchone()[0]
        == 1
    )


def test_a_slot_at_an_uncovering_location_is_refused(env, booking):
    _, token = booking
    response = env.client().post(f"/api/public/booking/{token}", json={"slot_id": LA_PAZ_SLOT})
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    for slot_id in ("slot_nope", "", None):
        bad = env.client().post(f"/api/public/booking/{token}", json={"slot_id": slot_id})
        assert bad.status_code in (404, 422), slot_id


def test_booking_validation(env, booking, contract):
    _, token = booking
    response = env.client().post(
        f"/api/public/booking/{token}", json={"slot_id": SLOT_WED_10, "x": 1}
    )
    assert contract.conforms(response, "bookFromPage")["error"] == "validation_error"


def test_invalid_tampered_and_foreign_links_are_404(env, booking, contract):
    app, token = booking
    public = env.client()
    payload, signature = token.split(".")
    forged = make_booking_token(app["id"], NOW + timedelta(days=30), secret=b"x" * 24)
    for bad in ("garbage", f"{payload}.{signature}0", forged):
        response = public.get(f"/api/public/booking/{bad}")
        assert contract.conforms(response, "getBookingPage")["error"] == "not_found"
        assert response.status_code == 404
        assert (
            public.post(f"/api/public/booking/{bad}", json={"slot_id": SLOT_WED_10}).status_code
            == 404
        )
        assert public.get(f"/api/public/booking/{bad}/invite.ics").status_code == 404


def test_expired_link_is_410_with_a_message_in_the_candidates_language(env, build, contract):
    spanish = build.application(stage="approved")
    english = build.application(stage="approved", language="en")
    tokens = {
        a["id"]: link(env.client(PUEBLA), a["id"]).json()["token"] for a in (spanish, english)
    }
    env.clock.advance(timedelta(days=7, seconds=1))
    public = env.client()
    es = public.get(f"/api/public/booking/{tokens[spanish['id']]}")
    body = contract.conforms(es, "getBookingPage")
    assert es.status_code == 410
    assert body["error"] == "link_expired"
    assert "caducó" in body["message"]
    en = public.get(f"/api/public/booking/{tokens[english['id']]}")
    assert "expired" in en.json()["message"]
    post = public.post(
        f"/api/public/booking/{tokens[spanish['id']]}", json={"slot_id": SLOT_WED_10}
    )
    assert post.status_code == 410
    assert public.get(f"/api/public/booking/{tokens[spanish['id']]}/invite.ics").status_code == 410


def test_link_dies_with_the_application(env, build, booking):
    app, token = booking
    env.client(PUEBLA).post(f"/api/applications/{app['id']}/rejection", json={"reason": "no_show"})
    assert env.client().get(f"/api/public/booking/{token}").status_code == 404


def test_link_for_a_not_yet_approved_application_is_404(env, build):
    qualified = build.application()
    token = make_booking_token(qualified["id"], NOW + timedelta(days=1), secret=SECRET)
    assert env.client().get(f"/api/public/booking/{token}").status_code == 404


def test_download_invite(env, booking):
    app, token = booking
    public = env.client()
    assert (
        public.get(f"/api/public/booking/{token}/invite.ics").status_code == 404
    )  # nothing booked
    public.post(f"/api/public/booking/{token}", json={"slot_id": SLOT_WED_10})
    response = public.get(f"/api/public/booking/{token}/invite.ics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert 'filename="entrevista.ics"' in response.headers["content-disposition"]
    lines = response.text.split("\r\n")
    assert "DTSTART:20261014T160000Z" in lines
    assert "DTEND:20261014T163000Z" in lines
    assert any(line.startswith("SUMMARY:Entrevista con Grupo Sazón") for line in lines)
    assert any(line.startswith("LOCATION:(synthetic)") for line in lines)
    ics_uid = env.conn.execute(
        "SELECT ics_uid FROM bookings WHERE application_id = ?", (app["id"],)
    ).fetchone()[0]
    assert f"UID:{ics_uid}" in lines


def test_public_routes_need_no_session_but_recruiter_routes_do(env, booking):
    app, _ = booking
    assert env.client().post(f"/api/applications/{app['id']}/booking-link").status_code == 401
    assert (
        env.client(PUEBLA_2).post(f"/api/applications/{app['id']}/booking-link").status_code == 201
    )
