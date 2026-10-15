"""The structured summary matches `SummaryStructured` in contracts/api.openapi.yaml."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from criba.db import repo
from criba.summary import build_structured

T0 = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)


def validator(contract: dict[str, Any], name: str) -> Draft202012Validator:
    """A validator for one component schema, with `#/components/...` refs resolving."""
    return Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": contract["components"]}
    )


def assert_valid(contract: dict[str, Any], name: str, value: Any) -> None:
    errors = sorted(validator(contract, name).iter_errors(value), key=str)
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors]


def test_a_qualified_summary_validates_against_the_contract(
    conn, contract, qualified_application
) -> None:
    app = qualified_application(conn, at=T0 + timedelta(seconds=240))
    structured = build_structured(conn, app["id"], now=T0 + timedelta(seconds=240))
    assert_valid(contract, "SummaryStructured", structured)

    assert structured["stage"] == "qualified"
    assert structured["outcome"] is None  # Qualified is not an Outcome
    assert structured["language"] == "es-MX"
    assert structured["answers"]["full_name"] == "Luis Hernández"
    assert structured["answers"]["zone"] == {
        "id": "zone_mx_puebla_centro",
        "name": "Centro",
        "city": "Puebla",
        "country": "MX",
    }
    assert (structured["nudges"], structured["escalations"], structured["flags"]) == (0, 0, [])


def test_the_validator_would_catch_a_broken_summary(conn, contract, qualified_application) -> None:
    app = qualified_application(conn)
    structured = build_structured(conn, app["id"], now=T0)
    for key in ("answers", "stage", "outcome", "language", "durations_seconds", "nudges", "flags"):
        broken = {k: v for k, v in structured.items() if k != key}
        assert list(validator(contract, "SummaryStructured").iter_errors(broken)), key
    assert list(
        validator(contract, "SummaryStructured").iter_errors({**structured, "stage": "hired"})
    )
    assert list(
        validator(contract, "SummaryStructured").iter_errors({**structured, "flags": ["nope"]})
    )


def test_a_fresh_application_validates_with_null_answers(conn, contract, new_application) -> None:
    app = new_application(conn)
    structured = build_structured(conn, app["id"], now=T0)
    assert_valid(contract, "SummaryStructured", structured)
    assert set(structured["answers"].values()) == {None}
    assert structured["durations_seconds"] == {"total": 0, "by_stage": {"greeting": 0}}


def test_a_knockout_summary_validates_and_names_the_criterion(
    conn, contract, new_application
) -> None:
    app = new_application(conn)
    repo.record_answer(conn, app["id"], "zone", "zone_mx_cdmx_milpa_alta", now=T0)
    repo.close_application(
        conn, app["id"], "knockout", knockout_criterion="zone", now=T0 + timedelta(seconds=30)
    )
    structured = build_structured(conn, app["id"], now=T0 + timedelta(days=3))
    assert_valid(contract, "SummaryStructured", structured)
    assert structured["outcome"] == "knockout"
    assert structured["knockout_criterion"] == "zone"
    assert structured["durations_seconds"]["total"] == 30  # time after closing is not counted


def test_the_language_falls_back_to_the_country_then_english(conn, new_application) -> None:
    app = new_application(conn)
    conn.execute("UPDATE applications SET language = NULL WHERE id = ?", (app["id"],))
    conn.execute("UPDATE candidates SET language = NULL WHERE id = ?", (app["candidate_id"],))
    assert build_structured(conn, app["id"], now=T0)["language"] == "es-MX"
    conn.execute("UPDATE applications SET country = NULL WHERE id = ?", (app["id"],))
    conn.execute("UPDATE candidates SET country = NULL WHERE id = ?", (app["candidate_id"],))
    assert build_structured(conn, app["id"], now=T0)["language"] == "en"


def test_durations_per_stage_add_up_to_the_total(conn, new_application) -> None:
    app = new_application(conn)
    steps = [("name", 20), ("zone", 35), ("driver_license", 65), ("confirm", 100)]
    for stage, offset in steps:
        repo.set_stage(conn, app["id"], stage, now=T0 + timedelta(seconds=offset))
    repo.mark_qualified(conn, app["id"], now=T0 + timedelta(seconds=240))

    structured = build_structured(conn, app["id"], now=T0 + timedelta(days=1))
    durations = structured["durations_seconds"]
    assert durations["by_stage"] == {
        "greeting": 20,
        "name": 15,
        "zone": 30,
        "driver_license": 35,
        "confirm": 140,
        "qualified": 0,
    }
    assert durations["total"] == 240 == sum(durations["by_stage"].values())


def test_time_while_closed_is_left_out_after_a_reopen(conn, contract, new_application) -> None:
    app = new_application(conn)
    repo.set_stage(conn, app["id"], "name", now=T0 + timedelta(seconds=10))
    repo.set_stage(conn, app["id"], "zone", now=T0 + timedelta(seconds=20))
    repo.record_answer(conn, app["id"], "zone", "zone_mx_cdmx_milpa_alta", now=T0)
    repo.close_application(
        conn, app["id"], "knockout", knockout_criterion="zone", now=T0 + timedelta(seconds=60)
    )
    back = T0 + timedelta(hours=1)
    repo.reopen_application(conn, app["id"], now=back)  # resumes at the failed check: zone
    repo.set_stage(conn, app["id"], "driver_license", now=back + timedelta(seconds=30))
    structured = build_structured(conn, app["id"], now=back + timedelta(seconds=100))

    assert_valid(contract, "SummaryStructured", structured)
    assert structured["outcome"] is None and structured["stage"] == "driver_license"
    assert structured["reopened_count"] == 1
    assert structured["durations_seconds"]["by_stage"] == {
        "greeting": 10,
        "name": 10,
        "zone": 40 + 30,
        "driver_license": 70,
    }
    assert structured["durations_seconds"]["total"] == 160


def test_counts_of_nudges_and_escalations(conn, new_application) -> None:
    app = new_application(conn)
    for step in (1, 2):
        repo.add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=app["id"],
            kind="nudge_sent",
            actor="system",
            data={"step": step},
            now=T0,
        )
    repo.open_queue_item(
        conn, app["id"], "escalation", reason="wants_human", note="asks for a person", now=T0
    )
    structured = build_structured(conn, app["id"], now=T0)
    assert (structured["nudges"], structured["escalations"]) == (2, 1)
    assert structured["flags"] == ["escalation"]


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("UPDATE applications SET stalled_since = '2026-10-12T21:00:00Z'", ["stalled"]),
        ("UPDATE applications SET start_date_far = 1", ["start_date_far"]),
        (
            "UPDATE applications SET qualified_waiting = 1, claimed_idle = 1",
            ["qualified_waiting", "claimed_idle"],
        ),
    ],
)
def test_flags_are_listed_in_the_contract_order(conn, new_application, setup, expected) -> None:
    app = new_application(conn)
    conn.execute(f"{setup} WHERE id = ?", (app["id"],))
    assert build_structured(conn, app["id"], now=T0)["flags"] == expected


def test_opted_out_takeover_and_review_flags(conn, new_application) -> None:
    app = new_application(conn)
    repo.mark_opted_out(conn, app["candidate_id"], now=T0)
    conn.execute(
        "UPDATE applications SET control = 'human', control_user_id = 'usr_rec_puebla',"
        " control_since = ? WHERE id = ?",
        ("2026-10-12T20:01:00Z", app["id"]),
    )
    repo.open_queue_item(conn, app["id"], "review", now=T0)
    flags = build_structured(conn, app["id"], now=T0)["flags"]
    assert flags == ["review_requested", "takeover", "opted_out"]
