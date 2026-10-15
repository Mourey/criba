"""Application lifecycle through the repository, against a temporary database."""

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from criba.db import connect, repo, transaction
from criba.db.repo import Conflict, Forbidden, NotFound
from criba.db.seed import CLIENT_ID, seed_users

T0 = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
PUEBLA_CENTRO = "zone_mx_puebla_centro"
OUTSIDE = "zone_mx_cdmx_milpa_alta"


@pytest.fixture
def users(seeded: sqlite3.Connection) -> sqlite3.Connection:
    seed_users(seeded, "synthetic-test-password", now=T0)
    seeded.execute(
        "INSERT INTO users (id, client_id, email, name, role, password_hash, created_at)"
        " VALUES ('usr_rec_puebla_2', ?, 'second@sazon.example', 'Second', 'recruiter', 'x', ?)",
        (CLIENT_ID, "2026-10-09T00:00:00Z"),
    )
    seeded.execute(
        "INSERT INTO user_locations (user_id, location_id)"
        " SELECT 'usr_rec_puebla_2', location_id FROM user_locations"
        " WHERE user_id = 'usr_rec_puebla'"
    )
    return seeded


def new_candidate(conn: sqlite3.Connection, external_id: str = "conv-1") -> dict:
    candidate, _ = repo.get_or_create_candidate(
        conn,
        client_id=CLIENT_ID,
        channel="webchat",
        external_id=external_id,
        phone="+525512345678",
        language="es-MX",
        now=T0,
    )
    return candidate


def started(conn: sqlite3.Connection, external_id: str = "conv-1") -> dict:
    candidate = new_candidate(conn, external_id)
    app, action = repo.resolve_application(conn, candidate["id"], "webchat", now=T0)
    assert action == "started"
    return app


def kinds(conn: sqlite3.Connection, app_id: str) -> list[str]:
    return [e["kind"] for e in repo.list_events(conn, app_id)]


def knocked_out(conn: sqlite3.Connection, at: datetime = T0) -> dict:
    app = started(conn)
    repo.set_stage(conn, app["id"], "zone", now=at)
    repo.record_answer(conn, app["id"], "zone", OUTSIDE, raw_input="Milpa Alta", now=at)
    return repo.close_application(conn, app["id"], "knockout", knockout_criterion="zone", now=at)


# --- Candidates -------------------------------------------------------------


def test_candidate_lookup_or_create_by_channel_identity(seeded: sqlite3.Connection) -> None:
    first, created = repo.get_or_create_candidate(
        seeded, client_id=CLIENT_ID, channel="webchat", external_id="c1", phone="+34600111222"
    )
    again, created_again = repo.get_or_create_candidate(
        seeded, client_id=CLIENT_ID, channel="webchat", external_id="c1"
    )
    other, _ = repo.get_or_create_candidate(
        seeded, client_id=CLIENT_ID, channel="whatsapp", external_id="c1"
    )
    assert created and not created_again
    assert again["id"] == first["id"]
    assert other["id"] != first["id"]
    assert first["country"] == "ES"
    assert repo.country_from_phone("+52 55 1234 5678") == "MX"
    assert repo.country_from_phone("+1 555") is None


def test_opt_out_is_recorded_and_lifted_when_the_candidate_writes_again(
    seeded: sqlite3.Connection,
) -> None:
    app = started(seeded)
    repo.mark_opted_out(seeded, app["candidate_id"], now=T0)
    assert repo.get_candidate(seeded, app["candidate_id"])["opted_out"] is True
    assert repo.lift_opt_out(seeded, app["candidate_id"], now=T0)
    assert not repo.lift_opt_out(seeded, app["candidate_id"], now=T0)
    candidate = repo.get_candidate(seeded, app["candidate_id"])
    assert candidate["opted_out"] is False and candidate["opted_out_at"] is None
    assert "opted_out" in kinds(seeded, app["id"])


def test_language_follows_the_latest_exchange(seeded: sqlite3.Connection) -> None:
    app = started(seeded)
    repo.set_candidate_language(seeded, app["candidate_id"], "en", now=T0)
    assert repo.get_application(seeded, app["id"])["language"] == "en"


# --- One open Application ---------------------------------------------------


def test_inbound_messages_resolve_to_the_one_open_application(seeded: sqlite3.Connection) -> None:
    app = started(seeded)
    assert app["stage"] == "greeting" and app["status"] == "open"
    assert app["country"] == "MX" and app["language"] == "es-MX"
    again, action = repo.resolve_application(seeded, app["candidate_id"], "webchat", now=T0)
    assert action == "existing" and again["id"] == app["id"]
    assert kinds(seeded, app["id"]) == ["application_started"]


def test_a_candidate_never_has_two_open_applications(seeded: sqlite3.Connection) -> None:
    app = started(seeded)
    with pytest.raises(Conflict):
        repo.start_application(seeded, app["candidate_id"], "webchat", now=T0)
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO applications (id, candidate_id, client_id, channel, created_at,"
            " updated_at) VALUES ('app_dup', ?, ?, 'webchat', 'x', 'x')",
            (app["candidate_id"], CLIENT_ID),
        )


def test_unknown_application_is_not_found(seeded: sqlite3.Connection) -> None:
    with pytest.raises(NotFound):
        repo.set_stage(seeded, "app_missing", "name")


# --- Stages and answers -----------------------------------------------------


def test_stage_changes_are_logged(seeded: sqlite3.Connection) -> None:
    app = started(seeded)
    repo.set_stage(seeded, app["id"], "name", now=T0)
    repo.set_stage(seeded, app["id"], "name", now=T0)
    events = repo.list_events(seeded, app["id"])
    assert [e["data"] for e in events if e["kind"] == "stage_changed"] == [
        {"from": "greeting", "to": "name"}
    ]
    with pytest.raises(ValueError):
        repo.set_stage(seeded, app["id"], "hired")


def test_answers_are_stored_canonically(seeded: sqlite3.Connection) -> None:
    app = started(seeded)
    repo.record_answer(seeded, app["id"], "full_name", "Luis Hernández", now=T0)
    repo.record_answer(seeded, app["id"], "zone", PUEBLA_CENTRO, raw_input="centro", now=T0)
    repo.record_answer(seeded, app["id"], "driver_license", True, raw_input="sí", now=T0)
    repo.record_answer(seeded, app["id"], "availability", ["full_time"], now=T0)
    repo.record_answer(seeded, app["id"], "experience_years", 0.5, now=T0)
    stored = repo.record_answer(
        seeded, app["id"], "start_date", "2026-12-20", start_date_far=True, now=T0
    )
    assert stored["full_name"] == "Luis Hernández"
    assert stored["zone_id"] == PUEBLA_CENTRO and stored["zone_input"] == "centro"
    assert stored["driver_license"] is True and stored["driver_license_input"] == "sí"
    assert stored["availability"] == ["full_time"]
    assert stored["experience_years"] == 0.5
    assert stored["start_date_far"] is True
    assert repo.get_candidate(seeded, app["candidate_id"])["full_name"] == "Luis Hernández"
    with pytest.raises(ValueError):
        repo.record_answer(seeded, app["id"], "age", 30)


def test_zone_answer_assigns_every_covering_location(seeded: sqlite3.Connection) -> None:
    app = started(seeded)
    repo.record_answer(seeded, app["id"], "zone", PUEBLA_CENTRO, now=T0)
    covering = repo.covering_location_ids(seeded, PUEBLA_CENTRO)
    assert len(covering) >= 2
    assert repo.application_location_ids(seeded, app["id"]) == covering
    # A correction replaces the Locations.
    repo.record_answer(seeded, app["id"], "zone", OUTSIDE, now=T0)
    assert repo.application_location_ids(seeded, app["id"]) == []


def test_misses_are_counted_and_reset_by_a_valid_answer(seeded: sqlite3.Connection) -> None:
    app = started(seeded)
    assert repo.record_invalid_answer(seeded, app["id"], "full_name", message_id=41) == 1
    assert (
        repo.record_invalid_answer(seeded, app["id"], "zone", ambiguous=True, options=["a", "b"])
        == 2
    )
    assert repo.record_answer(seeded, app["id"], "full_name", "Ana Pérez")["invalid_attempts"] == 0
    assert kinds(seeded, app["id"])[1:3] == ["answer_invalid", "answer_ambiguous"]
    invalid, ambiguous = repo.list_events(seeded, app["id"])[1:3]
    # The event references the inbound message, never the Candidate's words (Q38).
    assert invalid["data"] == {"field": "full_name", "attempts": 1, "message_id": 41}
    assert ambiguous["data"] == {"field": "zone", "attempts": 2, "options": ["a", "b"]}


# --- Qualified, Approval, Outcomes ---------------------------------------------


def test_qualified_waits_for_approval_then_approved(users: sqlite3.Connection) -> None:
    app = started(users)
    repo.set_stage(users, app["id"], "confirm", now=T0)
    qualified = repo.mark_qualified(users, app["id"], now=T0)
    assert qualified["status"] == "open" and qualified["stage"] == "qualified"
    assert qualified["qualified_at"] == "2026-10-09T10:00:00Z"
    item = users.execute(
        "SELECT kind, status FROM queue_items WHERE application_id = ?", (app["id"],)
    ).fetchone()
    assert tuple(item) == ("approval", "open")
    with pytest.raises(Conflict):
        repo.mark_qualified(users, app["id"], now=T0)

    approved = repo.mark_approved(users, app["id"], "usr_rec_puebla", now=T0)
    assert approved["stage"] == "approved" and approved["approved_by"] == "usr_rec_puebla"
    resolution = users.execute(
        "SELECT status, resolution, resolved_by FROM queue_items WHERE application_id = ?",
        (app["id"],),
    ).fetchone()
    assert tuple(resolution) == ("resolved", "approved", "usr_rec_puebla")
    assert {"screening_finished", "approval_requested", "approved"} <= set(kinds(users, app["id"]))


def test_knockout_closes_with_its_criterion(seeded: sqlite3.Connection) -> None:
    closed = knocked_out(seeded)
    assert closed["status"] == "closed" and closed["outcome"] == "knockout"
    assert closed["knockout_criterion"] == "zone" and closed["stage"] == "zone"
    assert closed["closed_at"] == "2026-10-09T10:00:00Z"
    assert kinds(seeded, closed["id"])[-2:] == ["knockout", "screening_finished"]
    with pytest.raises(Conflict):
        repo.record_answer(seeded, closed["id"], "full_name", "Ana Pérez")
    with pytest.raises(Conflict):
        repo.close_application(seeded, closed["id"], "withdrawn")


def test_outcome_arguments_are_checked(users: sqlite3.Connection) -> None:
    app = started(users)
    with pytest.raises(ValueError):
        repo.close_application(users, app["id"], "knockout")
    with pytest.raises(ValueError):
        repo.close_application(users, app["id"], "rejection", rejection_reason="no_show")
    with pytest.raises(ValueError):
        repo.close_application(
            users,
            app["id"],
            "rejection",
            rejection_reason="other",
            actor="user",
            user_id="usr_rec_puebla",
        )
    with pytest.raises(ValueError):
        repo.close_application(users, app["id"], "hired")
    with pytest.raises(ValueError):
        repo.close_application(
            users, app["id"], "rejection", actor="user", user_id="usr_rec_puebla"
        )
    with pytest.raises(ValueError):
        repo.close_application(users, app["id"], "withdrawn", rejection_reason="no_show")
    assert repo.get_application(users, app["id"])["status"] == "open"


def test_rejection_resolves_the_approval_and_ends_claim_and_booking(
    users: sqlite3.Connection,
) -> None:
    app = started(users)
    repo.record_answer(users, app["id"], "zone", PUEBLA_CENTRO, now=T0)
    repo.mark_qualified(users, app["id"], now=T0)
    repo.claim_application(users, app["id"], "usr_rec_puebla", now=T0)
    slot = users.execute(
        "SELECT id FROM interview_slots WHERE location_id = 'loc_mx_puebla_centro' LIMIT 1"
    ).fetchone()[0]
    users.execute(
        "INSERT INTO bookings (id, application_id, slot_id, booked_via, ics_uid, created_at)"
        " VALUES ('bk_1', ?, ?, 'chat', 'uid-1', 'x')",
        (app["id"], slot),
    )
    closed = repo.close_application(
        users,
        app["id"],
        "rejection",
        rejection_reason="other",
        rejection_note="Moved away",
        actor="user",
        user_id="usr_rec_puebla",
        now=T0,
    )
    assert closed["outcome"] == "rejection" and closed["rejection_note"] == "Moved away"
    assert closed["claim_user_id"] is None
    booking = users.execute("SELECT status FROM bookings WHERE id = 'bk_1'").fetchone()[0]
    assert booking == "released"
    resolution = users.execute(
        "SELECT resolution FROM queue_items WHERE application_id = ?", (app["id"],)
    ).fetchone()[0]
    assert resolution == "rejected"
    assert {"claim_released", "interview_slot_released", "rejection"} <= set(
        kinds(users, app["id"])
    )


def test_summary_is_stored_and_overwritten(seeded: sqlite3.Connection) -> None:
    app = started(seeded)
    repo.set_summary(seeded, app["id"], {"result": "qualified"}, "First.", now=T0)
    later = T0 + timedelta(hours=1)
    stored = repo.set_summary(seeded, app["id"], {"result": "knockout"}, None, now=later)
    assert stored["summary_structured"] == {"result": "knockout"}
    assert stored["summary_prose"] is None
    assert stored["summary_at"] == "2026-10-09T11:00:00Z"


# --- Reopen -----------------------------------------------------------------


def test_writing_again_within_30_days_leaves_a_knockout_closed(
    seeded: sqlite3.Connection,
) -> None:
    """ADR 0033: any message (here a "revisión") lands on the closed Knockout, unchanged."""
    closed = knocked_out(seeded)
    later = T0 + timedelta(days=29)
    app, action = repo.resolve_application(seeded, closed["candidate_id"], "webchat", now=later)
    assert action == "knockout" and app["id"] == closed["id"]
    assert app["status"] == "closed" and app["outcome"] == "knockout"
    assert app["knockout_criterion"] == "zone" and app["reopened_count"] == 0
    assert "reopened" not in kinds(seeded, app["id"])
    assert repo.get_open_application(seeded, closed["candidate_id"]) is None


def test_the_candidate_reopen_of_a_knockout_resumes_at_the_failed_check(
    seeded: sqlite3.Connection,
) -> None:
    """The Reopen itself (called by record_answer on a valid new answer, ADR 0033)."""
    closed = knocked_out(seeded)
    app = repo.reopen_application(seeded, closed["id"], now=T0 + timedelta(days=29))
    assert app["status"] == "open" and app["outcome"] is None and app["closed_at"] is None
    assert app["knockout_criterion"] is None
    assert app["stage"] == "zone" and app["zone_id"] is None
    assert app["reopened_count"] == 1
    assert repo.application_location_ids(seeded, app["id"]) == []
    reopened = repo.list_events(seeded, app["id"])[-1]
    assert reopened["kind"] == "reopened"
    assert reopened["data"] == {
        "previous_outcome": "knockout",
        "via": "candidate",
        "criterion": "zone",
        "previous_value": OUTSIDE,
    }


def test_the_reopen_window_is_30_days(seeded: sqlite3.Connection) -> None:
    closed = knocked_out(seeded)
    assert repo.within_reopen_window(closed, now=T0 + timedelta(days=30))
    assert not repo.within_reopen_window(closed, now=T0 + timedelta(days=30, seconds=1))
    assert not repo.within_reopen_window(started(seeded, "conv-2"), now=T0)


def test_after_30_days_a_new_application_starts(seeded: sqlite3.Connection) -> None:
    closed = knocked_out(seeded)
    later = T0 + timedelta(days=30, seconds=1)
    app, action = repo.resolve_application(seeded, closed["candidate_id"], "webchat", now=later)
    assert action == "started" and app["id"] != closed["id"]
    assert repo.get_application(seeded, closed["id"])["status"] == "closed"
    assert len(repo.list_candidate_applications(seeded, closed["candidate_id"])) == 2
    with pytest.raises(Conflict):
        repo.reopen_application(seeded, closed["id"], now=later)


def test_abandoned_reopens_at_the_pending_question(seeded: sqlite3.Connection) -> None:
    app = started(seeded)
    repo.set_stage(seeded, app["id"], "availability", now=T0)
    repo.close_application(seeded, app["id"], "abandoned", actor="system", now=T0)
    reopened, action = repo.resolve_application(
        seeded, app["candidate_id"], "webchat", now=T0 + timedelta(days=3)
    )
    assert action == "reopened" and reopened["stage"] == "availability"


@pytest.mark.parametrize("outcome", ["withdrawn", "rejection"])
def test_withdrawn_and_rejected_applications_never_reopen(
    users: sqlite3.Connection, outcome: str
) -> None:
    app = started(users)
    extra = (
        {"rejection_reason": "role_filled", "actor": "user", "user_id": "usr_operator"}
        if outcome == "rejection"
        else {"actor": "candidate"}
    )
    repo.close_application(users, app["id"], outcome, now=T0, **extra)
    again, action = repo.resolve_application(
        users, app["candidate_id"], "webchat", now=T0 + timedelta(days=1)
    )
    assert action == "started" and again["id"] != app["id"]
    with pytest.raises(Conflict):
        repo.reopen_application(users, app["id"], now=T0)


def test_an_overturned_review_reopens_a_knockout_without_the_time_limit(
    users: sqlite3.Connection,
) -> None:
    closed = knocked_out(users)
    reopened = repo.reopen_application(
        users,
        closed["id"],
        actor="user",
        user_id="usr_rec_puebla",
        via="review_overturned",
        now=T0 + timedelta(days=60),
    )
    assert reopened["status"] == "open"
    assert repo.list_events(users, closed["id"])[-1]["user_id"] == "usr_rec_puebla"


def test_review_applies_only_to_knockouts(users: sqlite3.Connection) -> None:
    app = started(users)
    repo.close_application(users, app["id"], "abandoned", actor="system", now=T0)
    with pytest.raises(Conflict):
        repo.reopen_application(
            users, app["id"], actor="user", user_id="usr_operator", via="review_overturned"
        )


# --- Messages and events ----------------------------------------------------


def test_messages_are_stored_verbatim_and_deduplicated(seeded: sqlite3.Connection) -> None:
    app = started(seeded)
    common = {"candidate_id": app["candidate_id"], "application_id": app["id"]}
    first, created = repo.add_message(
        seeded,
        **common,
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body="  Hola 👋 <b>",
        client_message_id="m-1",
        now=T0,
    )
    dup, created_again = repo.add_message(
        seeded,
        **common,
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body="different",
        client_message_id="m-1",
        now=T0,
    )
    reply, _ = repo.add_message(
        seeded,
        **common,
        channel="webchat",
        direction="outbound",
        sender="agent",
        body="¿Cómo te llamas?",
        content_type="interactive",
        interactive={"style": "buttons", "options": [{"id": "a", "label": "A"}]},
        run_id="run-1",
        now=T0 + timedelta(seconds=3),
    )
    assert created and not created_again and dup["id"] == first["id"]
    assert first["body"] == "  Hola 👋 <b>"
    assert reply["interactive"]["style"] == "buttons"
    assert [m["id"] for m in repo.list_messages(seeded, app["id"])] == [first["id"], reply["id"]]
    assert [m["id"] for m in repo.list_messages(seeded, app["id"], after_id=first["id"])] == [
        reply["id"]
    ]
    stored = repo.get_application(seeded, app["id"])
    assert stored["last_inbound_at"] == "2026-10-09T10:00:00Z"
    assert stored["last_outbound_at"] == "2026-10-09T10:00:03Z"


def test_messages_are_typed_unless_stored_as_spoken(seeded: sqlite3.Connection) -> None:
    app = started(seeded)
    common = {"candidate_id": app["candidate_id"], "application_id": app["id"]}
    typed, _ = repo.add_message(
        seeded,
        **common,
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body="Hola",
        client_message_id="m-typed",
        now=T0,
    )
    spoken, _ = repo.add_message(
        seeded,
        **common,
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body="Me llamo Ana",
        client_message_id="m-spoken",
        modality="spoken",
        now=T0 + timedelta(seconds=1),
    )
    reply, _ = repo.add_message(
        seeded,
        **common,
        channel="webchat",
        direction="outbound",
        sender="agent",
        body="Gracias, Ana",
        modality="spoken",
        now=T0 + timedelta(seconds=2),
    )
    assert (typed["modality"], spoken["modality"], reply["modality"]) == (
        "typed",
        "spoken",
        "spoken",
    )
    stored = repo.list_messages(seeded, app["id"])
    assert [(m["id"], m["modality"]) for m in stored] == [
        (typed["id"], "typed"),
        (spoken["id"], "spoken"),
        (reply["id"], "spoken"),
    ]
    # A repeated inbound message returns the original row, with its own modality.
    dup, created = repo.add_message(
        seeded,
        **common,
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body="Me llamo Ana",
        client_message_id="m-spoken",
        now=T0 + timedelta(seconds=3),
    )
    assert not created
    assert (dup["id"], dup["modality"]) == (spoken["id"], "spoken")
    assert len(repo.list_messages(seeded, app["id"])) == 3


def test_a_failed_write_leaves_nothing_behind(seeded: sqlite3.Connection) -> None:
    app = started(seeded)
    before = kinds(seeded, app["id"])
    with pytest.raises(sqlite3.IntegrityError), transaction(seeded):
        repo.set_stage(seeded, app["id"], "name", now=T0)
        repo.add_event(
            seeded,
            candidate_id=app["candidate_id"],
            application_id=app["id"],
            kind="not_a_kind",
            actor="agent",
        )
    assert kinds(seeded, app["id"]) == before
    assert repo.get_application(seeded, app["id"])["stage"] == "greeting"


# --- Claims -----------------------------------------------------------------


def claimable(conn: sqlite3.Connection) -> dict:
    app = started(conn)
    repo.record_answer(conn, app["id"], "zone", PUEBLA_CENTRO, now=T0)
    return repo.mark_qualified(conn, app["id"], now=T0)


def test_one_recruiter_holds_a_claim(users: sqlite3.Connection) -> None:
    app = claimable(users)
    claimed = repo.claim_application(users, app["id"], "usr_rec_puebla", now=T0)
    assert claimed["claim_user_id"] == "usr_rec_puebla"
    assert repo.claim_application(users, app["id"], "usr_rec_puebla", now=T0)["claimed_at"]
    with pytest.raises(Conflict) as err:
        repo.claim_application(users, app["id"], "usr_rec_puebla_2", now=T0 + timedelta(days=6))
    assert err.value.code == "claimed"


def test_recruiters_outside_the_covering_locations_cannot_claim(
    users: sqlite3.Connection,
) -> None:
    app = claimable(users)
    with pytest.raises(Forbidden):
        repo.claim_application(users, app["id"], "usr_rec_madrid", now=T0)
    assert repo.user_can_access(users, "usr_operator", app["id"])
    assert not repo.user_can_access(users, "usr_rec_madrid", app["id"])


def test_an_idle_claim_can_be_taken_over(users: sqlite3.Connection) -> None:
    app = claimable(users)
    repo.claim_application(users, app["id"], "usr_rec_puebla", now=T0)
    assert repo.touch_claim(users, app["id"], "usr_rec_puebla", now=T0 + timedelta(days=2))
    assert not repo.touch_claim(users, app["id"], "usr_rec_puebla_2", now=T0)
    with pytest.raises(Conflict):
        repo.claim_application(users, app["id"], "usr_rec_puebla_2", now=T0 + timedelta(days=8))
    taken = repo.claim_application(users, app["id"], "usr_rec_puebla_2", now=T0 + timedelta(days=9))
    assert taken["claim_user_id"] == "usr_rec_puebla_2"
    last = repo.list_events(users, app["id"])[-1]
    assert last["kind"] == "claim_taken_over"
    assert last["data"] == {"previous_user_id": "usr_rec_puebla"}


def test_only_the_holder_or_an_operator_releases_a_claim(users: sqlite3.Connection) -> None:
    app = claimable(users)
    repo.claim_application(users, app["id"], "usr_rec_puebla", now=T0)
    with pytest.raises(Forbidden):
        repo.release_claim(users, app["id"], "usr_rec_puebla_2", now=T0)
    released = repo.release_claim(users, app["id"], "usr_operator", now=T0)
    assert released["claim_user_id"] is None and released["claimed_at"] is None
    repo.claim_application(users, app["id"], "usr_rec_puebla_2", now=T0)
    assert repo.release_claim(users, app["id"], "usr_rec_puebla_2")["claim_user_id"] is None


def test_racing_claims_have_exactly_one_winner(users: sqlite3.Connection, db_path: Path) -> None:
    app = claimable(users)
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def attempt(user_id: str) -> None:
        conn = connect(db_path)
        try:
            barrier.wait()
            repo.claim_application(conn, app["id"], user_id, now=T0)
            results[user_id] = "won"
        except Conflict:
            results[user_id] = "lost"
        finally:
            conn.close()

    threads = [
        threading.Thread(target=attempt, args=(u,)) for u in ("usr_rec_puebla", "usr_rec_puebla_2")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values()) == ["lost", "won"]
    winner = next(u for u, r in results.items() if r == "won")
    assert repo.get_application(users, app["id"])["claim_user_id"] == winner
    assert kinds(users, app["id"]).count("claim_taken") == 1
