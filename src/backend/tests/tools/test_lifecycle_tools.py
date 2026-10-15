"""finish_screening, request_review, raise_escalation, record_backout, and get_application's
one-turn fields (spec 002 task 9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from criba.channel.copy import qualified_close
from criba.db import repo


def queue_items(conn, app_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT kind, status, reason, note FROM queue_items WHERE application_id = ? ORDER BY 1",
        (app_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# --- finish_screening -------------------------------------------------------


def test_finish_screening_marks_qualified_and_queues_approval(tools) -> None:
    app_id = tools.new_app()
    tools.answer_all(app_id)
    app = tools.app(app_id)
    assert tools.ok(app_id, "finish_screening") == {
        "stage": "qualified",
        "message": qualified_close("es-ES", "ES", app["full_name"].split()[0]),
    }
    app = tools.app(app_id)
    assert app["stage"] == "qualified"
    assert app["status"] == "open"
    assert queue_items(tools.conn, app_id) == [
        {"kind": "approval", "status": "open", "reason": None, "note": None}
    ]
    assert tools.err(app_id, "finish_screening")["error"] == "wrong_stage"


def test_finish_screening_with_missing_fields_is_incomplete(tools) -> None:
    app_id = tools.new_app()
    tools.ok(app_id, "record_answer", field="full_name", value="Ana García")
    err = tools.err(app_id, "finish_screening")
    assert err["error"] == "incomplete"
    assert "zone" in err["hint"]
    assert tools.app(app_id)["stage"] != "qualified"


def test_finish_screening_without_platforms_when_years_is_zero(tools) -> None:
    app_id = tools.new_app()
    for field, answer in [
        ("full_name", "Ana García"),
        ("zone", ["zone_es_madrid_chamberi"]),
        ("driver_license", "yes"),
        ("availability", ["full_time"]),
        ("preferred_schedule", ["afternoon"]),
        ("experience_years", 0),
        ("start_date", "2026-10-10"),
    ]:
        tools.ok(app_id, "record_answer", field=field, value=answer)
    assert tools.ok(app_id, "finish_screening") == {
        "stage": "qualified",
        "message": qualified_close("es-ES", "ES", tools.app(app_id)["full_name"].split()[0]),
    }


@pytest.mark.parametrize(
    ("country", "language", "needle", "absent"),
    [
        ("ES", "es-ES", "revisará tu solicitud", "will review your application"),
        ("MX", "es-MX", "revisará tu solicitud", "will review your application"),
        ("MX", "en", "will review your application", "revisará tu solicitud"),
        ("ES", "en", "will review your application", "revisará tu solicitud"),
    ],
)
def test_the_qualified_close_follows_the_stored_language(
    tools, country, language, needle, absent
) -> None:
    app_id = tools.new_app(country=country, language=language)
    tools.answer_all(app_id, country)
    value = tools.ok(app_id, "finish_screening")
    assert set(value) == {"stage", "message"}
    assert needle in value["message"] and absent not in value["message"]
    assert tools.app(app_id)["language"] == language


def test_the_qualified_close_uses_the_first_name_when_there_is_one(tools) -> None:
    app_id = tools.new_app(country="MX", language="en")
    tools.answer_all(app_id, "MX")
    first = tools.app(app_id)["full_name"].split()[0]
    assert tools.ok(app_id, "finish_screening")["message"].startswith(f"All set, {first}!")


def test_a_language_reported_mid_screening_is_what_the_close_uses(tools) -> None:
    """The reply and the record stay in step: the close is built from what get_application
    stored, so a switch reported on the turn is the language of the close."""
    app_id = tools.new_app(country="MX")
    tools.answer_all(app_id, "MX")
    assert tools.ok(app_id, "get_application", language="en")["language"] == "en"
    assert "will review your application" in tools.ok(app_id, "finish_screening")["message"]


# --- request_review ---------------------------------------------------------


def test_request_review_after_a_knockout(tools) -> None:
    app_id = tools.new_app()
    tools.ok(app_id, "record_answer", field="driver_license", value="no")
    value = tools.ok(app_id, "request_review", note="Sí tengo carnet, me equivoqué")
    assert value == {"status": "review_requested"}
    assert queue_items(tools.conn, app_id) == [
        {
            "kind": "review",
            "status": "open",
            "reason": None,
            "note": "Sí tengo carnet, me equivoqué",
        }
    ]
    assert tools.err(app_id, "request_review")["error"] == "already_requested"


def test_request_review_without_a_knockout_is_wrong_stage(tools) -> None:
    app_id = tools.new_app()
    assert tools.err(app_id, "request_review")["error"] == "wrong_stage"


# --- raise_escalation -------------------------------------------------------


def test_raise_escalation_once(tools) -> None:
    app_id = tools.new_app()
    value = tools.ok(
        app_id, "raise_escalation", reason="unanswered_question", note="¿Ponen la moto?"
    )
    assert value == {"status": "escalation_raised"}
    assert queue_items(tools.conn, app_id) == [
        {
            "kind": "escalation",
            "status": "open",
            "reason": "unanswered_question",
            "note": "¿Ponen la moto?",
        }
    ]
    err = tools.err(app_id, "raise_escalation", reason="wants_human")
    assert err["error"] == "already_requested"


# --- record_backout ---------------------------------------------------------


def test_withdrawn_closes_the_application(tools) -> None:
    app_id = tools.new_app()
    value = tools.ok(app_id, "record_backout", kind="withdrawn", note="Encontró otro trabajo.")
    assert value == {"kind": "withdrawn", "status": "closed", "outcome": "withdrawn"}
    (event,) = tools.events(app_id, "withdrawn")
    # The agent's note is not kept: events hold no free text (Q38).
    assert event == {"stage": "greeting"}
    assert tools.err(app_id, "record_backout", kind="withdrawn")["error"] == "application_closed"


def test_withdrawn_after_qualified(tools) -> None:
    app_id = tools.new_app()
    tools.answer_all(app_id)
    tools.ok(app_id, "finish_screening")
    value = tools.ok(app_id, "record_backout", kind="withdrawn")
    assert value["outcome"] == "withdrawn"


def test_opted_out_marks_the_candidate_and_keeps_the_application(tools) -> None:
    app_id = tools.new_app()
    value = tools.ok(app_id, "record_backout", kind="opted_out")
    assert value == {"kind": "opted_out", "status": "open", "outcome": None}
    candidate = repo.get_candidate(tools.conn, tools.app(app_id)["candidate_id"])
    assert candidate["opted_out"] is True
    assert tools.events(app_id, "opted_out") == [{}]


def test_erasure_request_is_logged(tools) -> None:
    app_id = tools.new_app()
    value = tools.ok(app_id, "record_backout", kind="erasure_request", note="borrad mis datos")
    assert value == {"kind": "erasure_request", "status": "open", "outcome": None}
    candidate = repo.get_candidate(tools.conn, tools.app(app_id)["candidate_id"])
    assert candidate["erasure_requested_at"] == "2026-10-09T10:00:00Z"
    assert tools.events(app_id, "erasure_requested") == [{}]


# --- get_application: one-turn fields --------------------------------------


def agent_says(tools, app_id: str, text: str = "ok") -> None:
    app = tools.app(app_id)
    repo.add_message(
        tools.conn,
        candidate_id=app["candidate_id"],
        application_id=app_id,
        channel="webchat",
        direction="outbound",
        sender="agent",
        body=text,
        now=tools.clock.now(),
    )


def test_reopened_is_true_until_the_agent_replies(tools) -> None:
    app_id = tools.new_app()
    tools.ok(app_id, "record_answer", field="full_name", value="Ana García")
    tools.ok(app_id, "record_answer", field="driver_license", value="no")
    agent_says(tools, app_id, "knockout message")
    tools.clock.advance(days=2)
    repo.reopen_application(tools.conn, app_id, now=tools.clock.now())
    value = tools.ok(app_id, "get_application")
    assert value["reopened"] is True
    assert value["stage"] == "driver_license"
    assert value["next_field"] == "zone"  # the licence was cleared; zone never answered
    tools.clock.advance(seconds=5)
    agent_says(tools, app_id, "¡Hola de nuevo!")
    assert tools.ok(app_id, "get_application")["reopened"] is False


def test_tone_hint_applies_to_one_turn(tools) -> None:
    app_id = tools.new_app()
    app = tools.app(app_id)
    repo.add_event(
        tools.conn,
        candidate_id=app["candidate_id"],
        application_id=app_id,
        kind="tone_hint",
        actor="system",
        data={"hint": "use simpler wording"},
        now=tools.clock.now(),
    )
    assert tools.ok(app_id, "get_application")["tone_hint"] == "use simpler wording"
    tools.clock.advance(seconds=3)
    agent_says(tools, app_id)
    assert tools.ok(app_id, "get_application")["tone_hint"] is None


def test_takeover_summary_after_hand_back(tools) -> None:
    app_id = tools.new_app()
    app = tools.app(app_id)
    common = {"candidate_id": app["candidate_id"], "application_id": app_id}
    repo.add_event(
        tools.conn, **common, kind="takeover_started", actor="user", user_id="user_rec",
        now=tools.clock.now(),
    )  # fmt: skip
    for sender, text in [("candidate", "¿Pagan las propinas?"), ("human", "Sí, íntegras.")]:
        tools.clock.advance(seconds=10)
        repo.add_message(
            tools.conn,
            **common,
            channel="webchat",
            direction="inbound" if sender == "candidate" else "outbound",
            sender=sender,
            sender_user_id="user_rec" if sender == "human" else None,
            body=text,
            now=tools.clock.now(),
        )
    tools.clock.advance(timedelta(seconds=10))
    repo.add_event(
        tools.conn, **common, kind="takeover_handed_back", actor="user", user_id="user_rec",
        now=tools.clock.now(),
    )  # fmt: skip
    summary = tools.ok(app_id, "get_application")["takeover_summary"]
    assert summary == "Candidate: ¿Pagan las propinas?\nTeam: Sí, íntegras."
    tools.clock.advance(seconds=3)
    agent_says(tools, app_id)
    assert tools.ok(app_id, "get_application")["takeover_summary"] is None


def test_takeover_summary_reads_the_stored_handback(tools) -> None:
    """The hand-back summary lives in ``handbacks``; the event only holds ``handback_id``."""
    app_id = tools.new_app()
    app = tools.app(app_id)
    common = {"candidate_id": app["candidate_id"], "application_id": app_id}
    tools.clock.advance(seconds=10)
    handback_id = repo.add_handback(
        tools.conn,
        app_id,
        user_id=None,
        summary="Confirmed the company provides the motorbike.",
        generated=False,
        now=tools.clock.now(),
    )
    repo.add_event(
        tools.conn,
        **common,
        kind="takeover_handed_back",
        actor="system",
        data={"handback_id": handback_id, "generated": False, "held_by": None},
        now=tools.clock.now(),
    )
    summary = tools.ok(app_id, "get_application")["takeover_summary"]
    assert summary == "Confirmed the company provides the motorbike."


# --- get_application: language and today (ADR 0042) ---------------------------------


def stored_language(tools, app_id: str) -> tuple[str, str]:
    app = tools.app(app_id)
    candidate = repo.get_candidate(tools.conn, app["candidate_id"])
    assert candidate is not None
    return app["language"], candidate["language"]


def test_language_switches_to_english_and_back(tools) -> None:
    app_id = tools.new_app(country="ES")
    assert tools.ok(app_id, "get_application", language="en")["language"] == "en"
    assert stored_language(tools, app_id) == ("en", "en")
    assert tools.ok(app_id, "get_application", language="es-ES")["language"] == "es-ES"
    assert stored_language(tools, app_id) == ("es-ES", "es-ES")


def test_spanish_is_stored_as_the_countrys_variant(tools) -> None:
    mx = tools.new_app(country="MX")
    tools.ok(mx, "get_application", language="en")
    assert tools.ok(mx, "get_application", language="es-ES")["language"] == "es-MX"
    assert stored_language(tools, mx) == ("es-MX", "es-MX")
    es = tools.new_app(country="ES")
    assert tools.ok(es, "get_application", language="es-MX")["language"] == "es-ES"


def test_an_omitted_language_leaves_it_unchanged(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "get_application", language="en")
    assert tools.ok(app_id, "get_application")["language"] == "en"
    assert stored_language(tools, app_id) == ("en", "en")


def test_the_same_language_writes_nothing(tools) -> None:
    app_id = tools.new_app(country="ES")
    before = tools.app(app_id)["updated_at"]
    tools.clock.advance(minutes=5)
    tools.ok(app_id, "get_application", language="es-ES")
    assert tools.app(app_id)["updated_at"] == before


def test_the_knockout_template_follows_the_stored_language(tools) -> None:
    app_id = tools.new_app(country="MX")
    tools.ok(app_id, "record_answer", field="full_name", value="Luis Hernández")
    tools.ok(app_id, "record_answer", field="zone", value=["zone_mx_cdmx_polanco"])
    tools.ok(app_id, "get_application", language="en")
    value = tools.ok(app_id, "record_answer", field="driver_license", value="no")
    assert value["template"] == "knockout_driver_license.en"
    assert value["message"].startswith("Thank you, Luis.")


def test_an_unknown_language_is_invalid_args(tools) -> None:
    status, body = tools.raw(tools.new_app(), "get_application", language="fr")
    assert status == 422 and body["error"] == "invalid_args"


def test_today_and_weekday_in_the_candidates_calendar(tools) -> None:
    # Friday 23:30 UTC is already Saturday in Madrid, still Friday in Mexico City.
    tools.clock.set(datetime(2026, 10, 9, 23, 30, tzinfo=UTC))
    es = tools.ok(tools.new_app(country="ES"), "get_application")
    mx = tools.ok(tools.new_app(country="MX"), "get_application")
    assert (es["today"], es["weekday"]) == ("2026-10-10", "saturday")
    assert (mx["today"], mx["weekday"]) == ("2026-10-09", "friday")


def test_today_uses_the_zones_time_zone_before_the_countrys(tools) -> None:
    # 06:30 UTC on Saturday: 00:30 in Mexico City, but 23:30 on Friday in Tijuana.
    tools.clock.set(datetime(2026, 10, 10, 6, 30, tzinfo=UTC))
    app_id = tools.new_app(country="MX")
    assert tools.ok(app_id, "get_application")["weekday"] == "saturday"
    repo.record_answer(tools.conn, app_id, "zone", "zone_mx_tijuana_tijuana", now=tools.clock.now())
    value = tools.ok(app_id, "get_application")
    assert (value["today"], value["weekday"]) == ("2026-10-09", "friday")
