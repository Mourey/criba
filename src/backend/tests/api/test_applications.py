"""Applications: list, filters, sorting, pagination, detail, export, scope."""

from __future__ import annotations

from datetime import timedelta

from criba.db import repo
from criba.db.connection import iso

from .support import (
    LA_PAZ,
    MADRID,
    NOW,
    OPERATOR,
    PUEBLA,
    PUEBLA_ANGELOPOLIS,
    PUEBLA_CENTRO,
    at,
)


def ids(body):
    return [item["id"] for item in body["items"]]


def test_list_conforms_and_describes_each_application(env, build, contract):
    app = build.application()
    body = contract.conforms(env.client(PUEBLA).get("/api/applications"), "listApplications")
    assert ids(body) == [app["id"]]
    assert body["total"] == 1
    assert body["next_cursor"] is None
    item = body["items"][0]
    assert item["stage"] == "qualified"
    assert item["status"] == "open"
    assert item["candidate"]["full_name"] == "Luis Hernández"
    assert item["zone"]["id"] == PUEBLA_CENTRO
    assert {loc["id"] for loc in item["locations"]} == {
        "loc_mx_puebla_angelopolis",
        "loc_mx_puebla_centro",
        "loc_mx_puebla_la_paz",
    }
    assert item["control"] == "agent"
    assert item["control_user_id"] is None
    assert item["claim"] is None


def test_recruiters_see_only_applications_at_their_locations(env, build, contract):
    wide = build.application(zone=PUEBLA_CENTRO)  # covers La Paz too
    narrow = build.application(zone=PUEBLA_ANGELOPOLIS)  # Angelópolis and Centro only
    build.application(stage="greeting")  # no Zone yet: no covering Location

    la_paz = contract.conforms(env.client(LA_PAZ).get("/api/applications"), "listApplications")
    assert ids(la_paz) == [wide["id"]]
    assert la_paz["total"] == 1
    assert set(ids(env.client(PUEBLA).get("/api/applications").json())) == {
        wide["id"],
        narrow["id"],
    }
    assert env.client(MADRID).get("/api/applications").json()["items"] == []
    assert env.client(OPERATOR).get("/api/applications").json()["total"] == 3


def test_out_of_scope_application_is_a_404_everywhere(env, build, contract):
    narrow = build.application(zone=PUEBLA_ANGELOPOLIS)
    la_paz = env.client(LA_PAZ)
    for method, suffix in (
        ("get", ""),
        ("get", "/export"),
        ("post", "/claim"),
        ("delete", "/claim"),
        ("post", "/claim/takeover"),
        ("post", "/takeover"),
        ("post", "/booking-link"),
    ):
        response = la_paz.request(method.upper(), f"/api/applications/{narrow['id']}{suffix}")
        assert response.status_code == 404, f"{method} {suffix}"
        assert response.json()["error"] == "not_found"
    assert (
        la_paz.post(f"/api/applications/{narrow['id']}/rejection", json={"reason": "no_show"})
    ).status_code == 404
    detail = env.client(PUEBLA).get(f"/api/applications/{narrow['id']}")
    assert detail.status_code == 200
    contract.conforms(la_paz.get(f"/api/applications/{narrow['id']}"), "getApplication")


def test_unknown_application_is_a_404_for_operators_too(env):
    assert env.client(OPERATOR).get("/api/applications/app_nope").status_code == 404


def test_filters(env, build):
    qualified = build.application()
    knocked = build.application(stage="knockout_license")
    approved = build.application(stage="approved")
    early = build.application(stage="greeting")
    op = env.client(OPERATOR)

    def search(**params):
        return set(ids(op.get("/api/applications", params=params).json()))

    assert search(status="open") == {qualified["id"], approved["id"], early["id"]}
    assert search(status="closed") == {knocked["id"]}
    assert search(stage="qualified") == {qualified["id"]}
    assert search(stage=["qualified", "approved"]) == {qualified["id"], approved["id"]}
    assert search(outcome="knockout") == {knocked["id"]}
    assert search(country="MX") == {qualified["id"], knocked["id"], approved["id"], early["id"]}
    assert search(country="ES") == set()
    assert search(location_id="loc_mx_puebla_la_paz") == {
        qualified["id"],
        knocked["id"],
        approved["id"],
    }
    assert search(location_id="loc_es_madrid_chamberi") == set()
    assert search(created_from="2026-10-10") == set()
    assert search(created_from="2026-10-09", created_to="2026-10-09") == {
        qualified["id"],
        knocked["id"],
        approved["id"],
        early["id"],
    }
    assert search(created_to="2026-10-08") == set()


def test_search_by_name_or_phone_and_wildcards_are_literal(env, build):
    luis = build.application(name="Luis Hernández")
    build.application(name="María Pérez")
    op = env.client(OPERATOR)
    assert ids(op.get("/api/applications", params={"q": "hernán"}).json()) == [luis["id"]]
    phone = repo.get_candidate(env.conn, luis["candidate_id"])["phone"]
    assert ids(op.get("/api/applications", params={"q": phone[-6:]}).json()) == [luis["id"]]
    assert op.get("/api/applications", params={"q": "%"}).json()["items"] == []
    assert op.get("/api/applications", params={"q": "_"}).json()["items"] == []


def test_flag_filters_and_flags(env, build):
    review = build.application(stage="knockout_license")
    build.queue_item(review["id"], "review")
    escalated = build.application(stage="greeting")
    build.queue_item(escalated["id"], "escalation", reason="wants_human")
    waiting = build.application()
    env.conn.execute("UPDATE applications SET qualified_waiting = 1 WHERE id = ?", (waiting["id"],))
    stalled = build.application(stage="greeting")
    env.conn.execute(
        "UPDATE applications SET stalled_since = ? WHERE id = ?", (iso(NOW), stalled["id"])
    )
    op = env.client(OPERATOR)

    def flagged(*flags):
        body = op.get("/api/applications", params={"flag": list(flags)}).json()
        return {i["id"]: i["flags"] for i in body["items"]}

    assert flagged("review_requested") == {review["id"]: ["review_requested"]}
    assert flagged("escalation") == {escalated["id"]: ["escalation"]}
    assert flagged("qualified_waiting") == {waiting["id"]: ["qualified_waiting"]}
    assert flagged("stalled") == {stalled["id"]: ["stalled"]}
    assert set(flagged("stalled", "escalation")) == {stalled["id"], escalated["id"]}
    assert flagged("takeover") == {}


def test_takeover_and_claimed_idle_flags(env, build):
    app = build.application()
    env.client(PUEBLA).post(f"/api/applications/{app['id']}/claim")
    env.client(PUEBLA).post(f"/api/applications/{app['id']}/takeover")
    op = env.client(OPERATOR)
    assert op.get("/api/applications", params={"flag": "claimed_idle"}).json()["items"] == []
    row = op.get("/api/applications", params={"flag": "takeover"}).json()["items"][0]
    assert row["flags"] == ["takeover"]
    assert row["control"] == "human"
    assert row["control_user_id"] == "usr_rec_puebla"
    assert row["control_user_name"] == "Recruiter Puebla (synthetic)"
    env.clock.advance(timedelta(days=8))
    idle = op.get("/api/applications", params={"flag": "claimed_idle"}).json()["items"]
    assert [i["id"] for i in idle] == [app["id"]]
    assert idle[0]["claim"]["idle"] is True
    assert idle[0]["flags"] == ["takeover", "claimed_idle"]


def test_claimed_by_filter(env, build):
    mine = build.application()
    other = build.application()
    free = build.application()
    env.client(PUEBLA).post(f"/api/applications/{mine['id']}/claim")
    env.client("recruiter.puebla2@sazon.example").post(f"/api/applications/{other['id']}/claim")
    puebla = env.client(PUEBLA)

    def claimed(value):
        return set(ids(puebla.get("/api/applications", params={"claimed_by": value}).json()))

    assert claimed("me") == {mine["id"]}
    assert claimed("none") == {free["id"]}
    assert claimed("usr_rec_puebla_2") == {other["id"]}


def test_sort_and_pagination(env, build):
    first = build.application(now=at(0))
    second = build.application(now=at(10))
    third = build.application(now=at(20))
    op = env.client(OPERATOR)
    newest = ids(op.get("/api/applications", params={"sort": "-created_at"}).json())
    assert newest == [third["id"], second["id"], first["id"]]
    oldest = ids(op.get("/api/applications", params={"sort": "created_at"}).json())
    assert oldest == newest[::-1]

    page_one = op.get("/api/applications", params={"sort": "created_at", "limit": 2}).json()
    assert ids(page_one) == [first["id"], second["id"]]
    assert page_one["total"] == 3
    assert page_one["next_cursor"]
    page_two = op.get(
        "/api/applications",
        params={"sort": "created_at", "limit": 2, "cursor": page_one["next_cursor"]},
    ).json()
    assert ids(page_two) == [third["id"]]
    assert page_two["next_cursor"] is None


def test_sort_by_time_in_stage_puts_the_longest_wait_first(env, build):
    waiting_long = build.application(stage="greeting", now=at(0))
    waiting_short = build.application(stage="greeting", now=at(30))
    closed = build.application(stage="knockout_license", now=at(0))
    body = env.client(OPERATOR).get("/api/applications", params={"sort": "-time_in_stage"}).json()
    order = ids(body)
    assert order[:2] == [waiting_long["id"], waiting_short["id"]]
    assert order[-1] == closed["id"]


def test_invalid_query_parameters_are_422(env):
    op = env.client(OPERATOR)
    for params in (
        {"stage": "nope"},
        {"sort": "name"},
        {"limit": 0},
        {"limit": 201},
        {"cursor": "garbage"},
        {"created_from": "yesterday"},
        {"flag": "loud"},
    ):
        response = op.get("/api/applications", params=params)
        assert response.status_code == 422, params
        assert response.json()["error"] == "validation_error"


def test_time_per_step_and_total(env, build):
    app = build.application(stage="greeting", now=at(0))
    conn = env.conn
    repo.set_stage(conn, app["id"], "name", now=at(1))
    repo.set_stage(conn, app["id"], "zone", now=at(3))
    env.clock.set(at(10))
    item = env.client(OPERATOR).get("/api/applications").json()["items"][0]
    assert item["stage_durations_seconds"] == {"greeting": 60, "name": 120, "zone": 420}
    assert item["time_in_stage_seconds"] == 420
    assert item["total_seconds"] == 600


def test_closed_application_stops_the_clock(env, build):
    app = build.application(stage="greeting", now=at(0))
    repo.set_stage(env.conn, app["id"], "name", now=at(2))
    repo.close_application(env.conn, app["id"], "withdrawn", now=at(5))
    env.clock.set(at(500))
    item = env.client(OPERATOR).get("/api/applications").json()["items"][0]
    assert item["total_seconds"] == 300
    assert item["time_in_stage_seconds"] == 0
    assert item["stage_durations_seconds"] == {"greeting": 120, "name": 180}


def test_reopen_does_not_count_the_closed_gap(env, build):
    app = build.application(stage="knockout_license", now=at(0))
    repo.reopen_application(
        env.conn,
        app["id"],
        actor="user",
        user_id="usr_operator",
        via="review_overturned",
        now=at(60),
    )
    env.clock.set(at(70))
    detail = env.client(OPERATOR).get(f"/api/applications/{app['id']}").json()
    assert detail["stage"] == "driver_license"
    assert detail["time_in_stage_seconds"] == 600
    assert detail["stage_durations_seconds"]["driver_license"] == 600


def test_detail_conforms_and_has_every_section(env, build, contract):
    app = build.application()
    repo.add_message(
        env.conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body="Hola, quiero trabajar",
        now=NOW,
    )
    repo.add_message(
        env.conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        channel="webchat",
        direction="outbound",
        sender="agent",
        body="¡Hola!",
        content_type="interactive",
        interactive={
            "style": "buttons",
            "options": [{"id": "full_time", "label": "Tiempo completo"}],
        },
        now=NOW,
    )
    repo.set_summary(
        env.conn,
        app["id"],
        {
            "answers": repo.export_application(env.conn, app["id"])["answers"],
            "stage": "qualified",
            "outcome": None,
            "language": "es-MX",
            "durations_seconds": {"total": 240},
            "nudges": 0,
            "escalations": 0,
            "flags": [],
        },
        "Luis lives in central Puebla.",
        now=NOW,
    )
    body = contract.conforms(
        env.client(PUEBLA).get(f"/api/applications/{app['id']}"), "getApplication"
    )
    assert body["answers"]["full_name"] == "Luis Hernández"
    assert body["answers"]["zone"]["id"] == PUEBLA_CENTRO
    assert body["answers"]["availability"] == ["full_time"]
    assert [m["body"] for m in body["messages"]] == ["Hola, quiero trabajar", "¡Hola!"]
    assert body["messages"][1]["interactive"]["options"][0]["id"] == "full_time"
    assert body["summary"]["prose"] == "Luis lives in central Puebla."
    assert body["files"] == []
    assert body["interview"] is None
    assert [q["kind"] for q in body["open_queue_items"]] == ["approval"]
    kinds = [e["kind"] for e in body["events"]]
    assert kinds[0] == "application_started"
    assert "summary_written" in kinds


def test_detail_lists_the_candidates_other_applications(env, build, contract):
    old = build.application(stage="knockout_license", now=at(-60 * 24 * 60))
    candidate = repo.get_candidate(env.conn, old["candidate_id"])
    new = repo.start_application(env.conn, candidate["id"], "webchat", now=NOW)
    body = contract.conforms(
        env.client(OPERATOR).get(f"/api/applications/{new['id']}"), "getApplication"
    )
    assert [o["id"] for o in body["other_applications"]] == [old["id"]]
    assert body["other_applications"][0]["outcome"] == "knockout"


def test_answer_status(env, build):
    app = build.application(stage="greeting")
    repo.record_answer(env.conn, app["id"], "full_name", "Luis", raw_input="soy luis", now=NOW)
    repo.record_invalid_answer(env.conn, app["id"], "zone", now=NOW)
    repo.record_invalid_answer(env.conn, app["id"], "zone", now=NOW)
    repo.record_answer(
        env.conn, app["id"], "start_date", "2026-12-30", start_date_far=True, now=NOW
    )
    body = env.client(OPERATOR).get(f"/api/applications/{app['id']}").json()
    by_field = {s["field"]: s for s in body["answer_status"]}
    assert [s["field"] for s in body["answer_status"]] == list(repo.ANSWER_FIELDS)
    assert by_field["full_name"]["status"] == "valid"
    # Only zone and driver_license keep the Candidate's words (on the Application, Q38).
    assert by_field["full_name"]["raw_input"] is None
    assert by_field["zone"]["status"] == "invalid"
    assert by_field["zone"]["attempts"] == 2
    assert by_field["driver_license"]["status"] == "pending"
    assert by_field["driver_license"]["attempts"] == 0
    assert by_field["start_date"]["status"] == "flagged"
    assert by_field["start_date"]["note"] == "more than 60 days out"


def test_knockout_answer_is_marked_knockout(env, build):
    app = build.application(stage="knockout_license")
    body = env.client(OPERATOR).get(f"/api/applications/{app['id']}").json()
    by_field = {s["field"]: s for s in body["answer_status"]}
    assert by_field["driver_license"]["status"] == "knockout"
    assert by_field["driver_license"]["raw_input"] == "no"
    assert body["knockout_criterion"] == "driver_license"


def test_a_zone_knockout_with_no_zone_value_is_marked_knockout(env, build):
    """A place that matched no Zone ends in the Zone Knockout with no Zone stored (ADR 0068):
    the Application detail still marks the Zone as the failed check and shows the words."""
    app = build.application(stage="zone")
    repo.add_event(
        env.conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        kind="answer_ambiguous",
        actor="agent",
        data={"field": "zone", "options": [], "funnel": True},
        now=NOW,
    )
    repo.set_raw_input(env.conn, app["id"], "zone", "I live in Texas", now=NOW)
    repo.close_application(
        env.conn, app["id"], "knockout", knockout_criterion="zone", actor="agent", now=NOW
    )
    body = env.client(OPERATOR).get(f"/api/applications/{app['id']}").json()
    zone = next(s for s in body["answer_status"] if s["field"] == "zone")
    assert zone["status"] == "knockout" and zone["raw_input"] == "I live in Texas"
    assert body["knockout_criterion"] == "zone"


def test_timeline_never_carries_the_candidates_words(env, build):
    app = build.application()
    repo.record_invalid_answer(env.conn, app["id"], "zone", now=NOW)
    build.queue_item(app["id"], "escalation", reason="unanswered_question", note="¿Dan moto?")
    events = env.client(OPERATOR).get(f"/api/applications/{app['id']}").json()["events"]
    text = str(events)
    assert "mi tía" not in text
    assert "Dan moto" not in text
    assert all("raw_input" not in e["data"] for e in events)
    recorded = next(e for e in events if e["kind"] == "answer_recorded")
    assert recorded["data"]["field"] == "full_name"


def test_event_user_names(env, build):
    app = build.application()
    env.client(PUEBLA).post(f"/api/applications/{app['id']}/claim")
    events = env.client(PUEBLA).get(f"/api/applications/{app['id']}").json()["events"]
    claim = next(e for e in events if e["kind"] == "claim_taken")
    assert claim["actor"] == "user"
    assert claim["user_name"] == "Recruiter Puebla (synthetic)"


def test_export_matches_the_repository_export(env, build, contract):
    app = build.application()
    response = env.client(PUEBLA).get(f"/api/applications/{app['id']}/export")
    body = contract.conforms(response, "exportApplication")
    assert body == repo.export_application(env.conn, app["id"])
    assert response.headers["content-disposition"] == f'attachment; filename="{app["id"]}.json"'
    assert body["locations"] == repo.application_location_ids(env.conn, app["id"])
    assert "cost" not in str(body)


def test_interview_shows_in_detail(env, build, contract):
    app = build.application(stage="approved")
    slot = env.conn.execute(
        "SELECT id FROM interview_slots WHERE location_id = 'loc_mx_puebla_centro'"
        " ORDER BY starts_at LIMIT 1"
    ).fetchone()[0]
    env.ctx.services.booking.book(
        env.conn, application_id=app["id"], slot_id=slot, via="chat", now=NOW
    )
    body = contract.conforms(
        env.client(PUEBLA).get(f"/api/applications/{app['id']}"), "getApplication"
    )
    assert body["stage"] == "interview_booked"
    assert body["interview"]["location_name"] == "Sazón Puebla Centro"
    assert body["interview"]["local_label"].endswith("10:00")
