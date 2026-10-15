"""The Zone funnel no longer depends on the agent's search arguments or on a ``null`` (ADR 0076).

The demo chat (Application app_01M4RPWTFKGP1CKJ48TS0MS1K7, real Haiku, 2026-10-12): an MX web
chat in English, "I live in Texas!" seven times. Every turn the agent searched twice, with
``country`` ES and then MX, found nothing, answered in prose and never recorded ``null``. ADR 0068
offered the funnel only for a search with no ``country``, so it was never offered: a loop.
"""

from __future__ import annotations

from criba.db import repo
from criba.tools import funnel, zone_searches
from criba.tools.application import SILENT_TURNS_FOR_FUNNEL, ZONE_NOTE, zone_backstop

from .test_tap_answer import at_zone

MX_CITIES = ["Ciudad de México", "Guadalajara", "Monterrey", "Puebla"]


def funnel_events(tools, app_id: str) -> list[dict]:
    return [e for e in tools.events(app_id, "answer_ambiguous") if e.get("funnel")]


def english_mx(tools) -> str:
    app_id = tools.new_app(country="MX", language="en")
    tools.say(app_id, "Cody Walker")
    tools.ok(app_id, "record_answer", field="full_name", value="Cody Walker")
    return app_id


def backstop(tools, app_id: str, message_id: int) -> dict | None:
    return zone_backstop(tools.conn, tools.app(app_id), message_id, tools.clock.now())


# --------------------------------------------------------------------------- the search


def test_the_demo_searches_offer_the_funnel_at_the_own_countrys_search(tools) -> None:
    app_id = english_mx(tools)
    message_id = tools.say(app_id, "I live in Texas!")
    spain = tools.ok(app_id, "search_zones", query="Texas", country="ES")
    assert spain["zones"] == [] and "message" not in spain and funnel_events(tools, app_id) == []
    mexico = tools.ok(app_id, "search_zones", query="Texas", country="MX")
    assert mexico["zones"] == [] and mexico["options_attached"] is True
    assert mexico["message"].startswith("We hire delivery drivers in these cities: ")
    for city in MX_CITIES:
        assert city in mexico["message"]
    (event,) = funnel_events(tools, app_id)
    assert event["message_id"] == message_id
    assert tools.app(app_id)["invalid_attempts"] == 0


def test_an_own_country_search_that_matches_another_country_offers_nothing(tools) -> None:
    """A Spanish place searched in MX on a Mexican chat: nothing in MX, but it is a Zone in Spain
    (ADR 0057 moves the Application there), so it is no place outside every Zone."""
    app_id = at_zone(tools)
    message_id = tools.say(app_id, "Madrid")
    value = tools.ok(app_id, "search_zones", query="Chamberí", country="MX")
    assert value["zones"] == [] and "message" not in value and "service_cities" not in value
    assert funnel_events(tools, app_id) == []
    assert zone_searches.matched_nothing(app_id, message_id) is False


def test_a_foreign_search_alone_offers_nothing_but_is_remembered(tools) -> None:
    app_id = english_mx(tools)
    message_id = tools.say(app_id, "I live in Texas!")
    value = tools.ok(app_id, "search_zones", query="Texas", country="ES")
    assert "message" not in value and "options_attached" not in value
    assert funnel_events(tools, app_id) == []
    assert zone_searches.matched_nothing(app_id, message_id) is True


def test_one_matching_search_for_the_message_is_enough(tools) -> None:
    app_id = at_zone(tools)
    message_id = tools.say(app_id, "Polanco o Texas")
    tools.ok(app_id, "search_zones", query="Texas", country="ES")
    tools.ok(app_id, "search_zones", query="Polanco")
    tools.ok(app_id, "search_zones", query="Texas", country="ES")
    assert zone_searches.matched_nothing(app_id, message_id) is False
    assert zone_searches.matched_nothing(app_id, message_id + 1) is False  # never searched


def test_the_memory_keeps_the_newest_messages_only(tools) -> None:
    zone_searches.clear()
    for n in range(zone_searches.MAX_KEPT + 1):
        zone_searches.note("app_x", n, matched=False)
    assert zone_searches.matched_nothing("app_x", 0) is False  # forgotten
    assert zone_searches.matched_nothing("app_x", zone_searches.MAX_KEPT) is True
    zone_searches.note("app_x", None, matched=False)
    assert zone_searches.matched_nothing("app_x", None) is False


def test_the_zone_note_asks_for_a_search_with_no_country() -> None:
    assert "with no country" in ZONE_NOTE


# --------------------------------------------------------------------------- the backstop


def test_a_foreign_search_that_found_nothing_gets_the_funnel_after_the_run(tools) -> None:
    app_id = english_mx(tools)
    message_id = tools.say(app_id, "I live in Texas!")
    tools.ok(app_id, "search_zones", query="Texas", country="ES")
    assert backstop(tools, app_id, message_id) is None
    (event,) = funnel_events(tools, app_id)
    assert event["message_id"] == message_id
    assert tools.app(app_id)["invalid_attempts"] == 0


def test_the_second_answer_that_matches_nothing_is_the_zone_knockout(tools) -> None:
    app_id = english_mx(tools)
    first = tools.say(app_id, "I live in Texas!")
    tools.ok(app_id, "search_zones", query="Texas", country="MX")  # the funnel, for `first`
    assert backstop(tools, app_id, first) is None  # the run offered it: nothing more
    second = tools.say(app_id, "I live in Texas!")
    again = tools.ok(app_id, "search_zones", query="Texas", country="MX")
    assert again["service_cities"] == MX_CITIES and "message" not in again
    result = backstop(tools, app_id, second)
    assert result is not None
    assert (result["status"], result["criterion"]) == ("knockout", "zone")
    app = tools.app(app_id)
    assert (app["status"], app["outcome"], app["knockout_criterion"]) == (
        "closed",
        "knockout",
        "zone",
    )
    assert app["zone_input"] == "I live in Texas!" and app["zone_id"] is None


def test_a_run_with_no_search_gets_one_follow_up_then_the_funnel(tools) -> None:
    app_id = english_mx(tools)
    first = tools.say(app_id, "I live in Texas!")
    assert backstop(tools, app_id, first) is None
    assert funnel_events(tools, app_id) == [] and tools.app(app_id)["invalid_attempts"] == 0
    second = tools.say(app_id, "I live in Texas!")
    assert SILENT_TURNS_FOR_FUNNEL == 2
    assert backstop(tools, app_id, second) is None
    (event,) = funnel_events(tools, app_id)
    assert event["message_id"] == second


def test_after_the_funnel_each_message_left_unanswered_is_a_miss_up_to_an_escalation(
    tools,
) -> None:
    app_id = english_mx(tools)
    tools.say(app_id, "I live in Texas!")
    tools.ok(app_id, "search_zones", query="Texas")  # the funnel
    for attempt in (1, 2, 3):
        message_id = tools.say(app_id, "I live in Texas!")  # prose, no tool, every time
        assert backstop(tools, app_id, message_id) is None
        (miss,) = [e for e in tools.events(app_id, "answer_invalid") if e["attempts"] == attempt]
        assert (miss["field"], miss["reason"], miss["message_id"]) == (
            "zone",
            "unresolved",
            message_id,
        )
    item = tools.conn.execute(
        "SELECT reason, note FROM queue_items WHERE application_id = ? AND kind = 'escalation'",
        (app_id,),
    ).fetchone()
    assert item["reason"] == "repeated_invalid_answers"
    assert item["note"].startswith("3 invalid answers for zone")
    assert tools.app(app_id)["status"] == "open"
    assert len(funnel_events(tools, app_id)) == 1


def test_a_message_the_agent_handled_is_left_alone(tools) -> None:
    app_id = at_zone(tools)
    searched = tools.say(app_id, "Polanco")
    tools.ok(app_id, "search_zones", query="Polanco")  # found: the agent may ask a follow-up
    assert backstop(tools, app_id, searched) is None
    city = tools.say(app_id, "Ciudad de México")
    tools.ok(app_id, "search_zones", query="Ciudad de México")  # the city's Zones, offered
    assert backstop(tools, app_id, city) is None
    assert funnel_events(tools, app_id) == [] and tools.events(app_id, "answer_invalid") == []


def test_an_unreadable_message_already_counted_is_not_counted_again(tools) -> None:
    app_id = at_zone(tools)
    tools.say(app_id, "Texas")
    message_id = tools.say(app_id, "asd asd kkk")
    repo.record_invalid_answer(
        tools.conn, app_id, "zone", message_id=message_id, reason="unreadable"
    )
    assert backstop(tools, app_id, message_id) is None
    assert len(tools.events(app_id, "answer_invalid")) == 1 and funnel_events(tools, app_id) == []


def test_the_backstop_stays_out_of_other_steps(tools) -> None:
    name_step = tools.new_app(country="MX")
    message_id = tools.say(name_step, "Texas")
    tools.say(name_step, "Texas")
    assert backstop(tools, name_step, message_id + 1) is None
    assert tools.events(name_step, "answer_invalid") == []

    confirming = at_zone(tools)
    tools.say(confirming, "por la salida a Toluca")
    tools.ok(confirming, "record_answer", field="zone", value=["zone_mx_toluca_toluca"])
    message_id = tools.say(confirming, "Texas")
    tools.say(confirming, "Texas")
    assert backstop(tools, confirming, message_id + 1) is None
    assert funnel_events(tools, confirming) == []

    stale = at_zone(tools)
    old = tools.say(stale, "Texas")
    tools.say(stale, "Texas")
    assert backstop(tools, stale, old) is None  # a newer message settles it
    assert funnel_events(tools, stale) == []


def test_no_funnel_to_offer_counts_a_miss_instead(tools, monkeypatch) -> None:
    monkeypatch.setattr(funnel, "options", lambda conn, app: None)
    app_id = english_mx(tools)
    message_id = tools.say(app_id, "I live in Texas!")
    tools.ok(app_id, "search_zones", query="Texas", country="ES")
    assert backstop(tools, app_id, message_id) is None
    assert funnel_events(tools, app_id) == []
    (miss,) = tools.events(app_id, "answer_invalid")
    assert (miss["reason"], miss["attempts"]) == ("unresolved", 1)
