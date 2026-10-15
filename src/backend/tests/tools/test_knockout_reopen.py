"""record_answer and request_review on a closed Knockout (ADR 0033).

Only a valid new answer to the failed criterion, within 30 days, reopens the Application;
anything else leaves it closed so a Review can still be requested.
"""

from __future__ import annotations

from datetime import timedelta

from criba.db import repo


def knocked_out_on_licence(tools) -> str:
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_cdmx_polanco"])
    value = tools.ok(app_id, "record_answer", field="driver_license", value="no")
    assert value["status"] == "knockout"
    return app_id


def test_get_application_works_on_a_closed_knockout(tools) -> None:
    app_id = knocked_out_on_licence(tools)
    tools.clock.advance(days=3)
    value = tools.ok(app_id, "get_application")
    assert value["status"] == "closed" and value["outcome"] == "knockout"
    assert value["knockout_criterion"] == "driver_license"
    assert value["next_field"] is None and value["reopened"] is False


def test_revision_leaves_it_closed_and_request_review_succeeds(tools) -> None:
    app_id = knocked_out_on_licence(tools)
    tools.clock.advance(days=3)
    assert tools.ok(app_id, "request_review", note="revisión") == {"status": "review_requested"}
    app = tools.app(app_id)
    assert app["status"] == "closed" and app["outcome"] == "knockout"
    assert tools.err(app_id, "request_review")["error"] == "already_requested"


def test_a_valid_new_answer_within_30_days_reopens_and_records_it(tools) -> None:
    app_id = knocked_out_on_licence(tools)
    tools.clock.advance(days=29)
    tools.say(app_id, "ya tengo carnet")
    value = tools.ok(app_id, "record_answer", field="driver_license", value="yes")
    assert value["status"] == "accepted"
    assert value["next_field"] == "availability" and value["stage"] == "availability"
    value = {"answers": tools.answers(app_id)}  # get_application sends them only when complete
    assert value["answers"]["driver_license"] is True
    app = tools.app(app_id)
    assert app["status"] == "open" and app["outcome"] is None and app["reopened_count"] == 1
    assert app["driver_license_input"] == "ya tengo carnet"
    reopened = tools.events(app_id, "reopened")
    assert reopened == [
        {
            "previous_outcome": "knockout",
            "via": "candidate",
            "criterion": "driver_license",
            "previous_value": False,
        }
    ]
    assert tools.events(app_id, "answer_recorded")[-1]["value"] is True
    assert tools.ok(app_id, "get_application")["reopened"] is True


def test_a_still_no_answer_keeps_it_closed(tools) -> None:
    app_id = knocked_out_on_licence(tools)
    tools.clock.advance(days=2)
    tools.say(app_id, "todavía no")
    err = tools.err(app_id, "record_answer", field="driver_license", value="pending")
    assert err["error"] == "application_closed"
    assert "request_review" in err["hint"]
    app = tools.app(app_id)
    assert app["status"] == "closed" and app["reopened_count"] == 0
    assert tools.events(app_id, "reopened") == []
    assert tools.ok(app_id, "request_review") == {"status": "review_requested"}


def test_the_candidates_words_keep_it_closed_without_counting_a_miss(tools) -> None:
    """Words are the wrong shape (ADR 0042): invalid_args, nothing reopens, no miss."""
    app_id = knocked_out_on_licence(tools)
    status, body = tools.raw(app_id, "record_answer", field="driver_license", value="revisión")
    assert status == 422 and body["error"] == "invalid_args"
    app = tools.app(app_id)
    assert app["status"] == "closed" and app["invalid_attempts"] == 0
    assert tools.events(app_id, "reopened") == []


def test_another_field_on_a_closed_knockout_is_application_closed(tools) -> None:
    app_id = knocked_out_on_licence(tools)
    err = tools.err(app_id, "record_answer", field="availability", value=["full_time"])
    assert err["error"] == "application_closed"
    assert tools.app(app_id)["status"] == "closed"


def test_past_30_days_a_valid_answer_does_not_reopen(tools) -> None:
    app_id = knocked_out_on_licence(tools)
    tools.clock.advance(days=30, seconds=1)
    err = tools.err(app_id, "record_answer", field="driver_license", value="yes")
    assert err["error"] == "application_closed"
    assert tools.app(app_id)["status"] == "closed"


def test_a_zone_now_covered_reopens_a_zone_knockout(tools) -> None:
    app_id = knocked_out_on_zone(tools)
    tools.clock.advance(days=5)
    value = tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_cdmx_polanco"])
    assert value["status"] == "accepted" and value["next_field"] == "driver_license"
    app = tools.app(app_id)
    assert app["status"] == "open" and app["zone_id"] is not None
    assert repo.application_location_ids(tools.conn, app_id)


def test_a_city_alone_after_a_zone_knockout_gets_the_list_and_stays_closed(tools) -> None:
    app_id = knocked_out_on_zone(tools)
    city = tools.ok(app_id, "search_zones", query="Ciudad de México")["zones"]
    err = tools.err(app_id, "record_answer", field="zone", value=[z["zone_id"] for z in city])
    assert err["error"] == "invalid_value" and len(err["options"]) == 9
    assert "attempt" not in err and "stays closed" in err["hint"]
    assert tools.app(app_id)["status"] == "closed"
    value = tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_cdmx_polanco"])
    assert value["status"] == "accepted"
    assert tools.app(app_id)["status"] == "open"


def test_abandoned_is_not_reopened_by_record_answer(tools) -> None:
    """Abandoned reopens on any inbound message in the router, never through a tool."""
    app_id = tools.new_app()
    repo.close_application(tools.conn, app_id, "abandoned", actor="system", now=tools.clock.now())
    tools.clock.advance(timedelta(days=1))
    err = tools.err(app_id, "record_answer", field="full_name", value="Ana García")
    assert err["error"] == "application_closed"


# --- structured values after a Knockout (ADR 0042) ----------------------------------


def knocked_out_on_zone(tools) -> str:
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_toluca_toluca"])
    value = tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_toluca_toluca"])
    assert value["status"] == "knockout" and value["criterion"] == "zone"
    return app_id


def test_a_yes_code_within_30_days_reopens_a_licence_knockout(tools) -> None:
    app_id = knocked_out_on_licence(tools)
    tools.clock.advance(days=3)
    tools.say(app_id, "ya me la dieron")
    value = tools.ok(app_id, "record_answer", field="driver_license", value="yes")
    assert value["status"] == "accepted" and value["next_field"] == "availability"
    app = tools.app(app_id)
    assert app["status"] == "open" and app["driver_license_input"] == "ya me la dieron"


def test_another_knockout_code_after_a_knockout_stays_closed(tools) -> None:
    app_id = knocked_out_on_licence(tools)
    err = tools.err(app_id, "record_answer", field="driver_license", value="pending")
    assert err["error"] == "application_closed"
    assert tools.app(app_id)["status"] == "closed"


def test_null_after_a_knockout_stays_closed_without_a_miss(tools) -> None:
    app_id = knocked_out_on_licence(tools)
    err = tools.err(app_id, "record_answer", field="driver_license", value=None)
    assert (err["error"], err["reason"]) == ("invalid_value", "unresolved")
    assert "attempt" not in err and "stays closed" in err["hint"]
    app = tools.app(app_id)
    assert app["status"] == "closed" and app["invalid_attempts"] == 0


def test_a_still_uncovered_zone_after_a_knockout_is_closed_with_no_confirmation(tools) -> None:
    app_id = knocked_out_on_zone(tools)
    tools.clock.advance(days=1)
    for zone in ("zone_mx_toluca_toluca", "zone_mx_cdmx_milpa_alta"):
        err = tools.err(app_id, "record_answer", field="zone", value=[zone])
        assert err["error"] == "application_closed"
    assert tools.app(app_id)["status"] == "closed"


def test_a_covered_zone_id_reopens_a_zone_knockout(tools) -> None:
    app_id = knocked_out_on_zone(tools)
    tools.clock.advance(days=5)
    value = tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_cdmx_polanco"])
    assert value["status"] == "accepted"
    assert tools.app(app_id)["status"] == "open"


def test_a_citys_ids_after_a_zone_knockout_get_the_list_and_stay_closed(tools) -> None:
    app_id = knocked_out_on_zone(tools)
    ids = [
        "zone_mx_puebla_angelopolis",
        "zone_mx_puebla_centro",
        "zone_mx_puebla_la_paz",
    ]
    value = tools.ok(app_id, "record_answer", field="zone", value=ids)
    assert value["status"] == "needs_choice" and "confirm" not in value
    assert tools.app(app_id)["status"] == "closed"


def test_an_unknown_zone_id_after_a_knockout_stays_closed(tools) -> None:
    app_id = knocked_out_on_zone(tools)
    err = tools.err(app_id, "record_answer", field="zone", value=["zone_mx_atlantis"])
    assert (err["error"], err["reason"]) == ("invalid_value", "unknown_zone_id")
    assert "stays closed" in err["hint"]
