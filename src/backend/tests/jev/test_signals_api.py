"""Stored message signals, the Recruiter's communication notes, the summary and the aggregate
view (ADR 0051). The notes are conversation context, never a score."""

from __future__ import annotations

import json
import re
from typing import Any

from criba.db import repo
from criba.jev import store
from criba.jev.battery import Reading
from criba.jev.notes import communication_notes, format_for_summary, notes_label
from criba.screening.clock import FakeClock
from criba.summary import DeterministicSummaryClient, SummaryWriter

from ..api.support import LA_PAZ, NOW, OPERATOR, PUEBLA, at

SCORE = re.compile(r"\d\.\d|%|0\.\d")


def say(conn, app: dict[str, Any], body: str, minutes: float, stage: str | None = None) -> int:
    message, _ = repo.add_message(
        conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body=body,
        now=at(minutes),
    )
    return message["id"]


def keep(
    conn,
    app: dict[str, Any],
    message_id: int,
    minutes: float,
    *,
    stage: str,
    fired: tuple[str, ...] = (),
    status: str = "ok",
    first: bool = False,
    language_set: str | None = None,
    escalation: str | None = None,
    **signals: float,
) -> None:
    reading = Reading(
        "es",
        1.0,
        0.02,
        {"frustrated": 0.03, "confused": 0.03, "wants_human": 0.02, "abusive": 0.01, **signals},
    )
    store.record_signals(
        conn,
        message_id=message_id,
        application={**app, "stage": stage},
        now=at(minutes),
        status=status,
        model="typesafe/jev-1.13-20260917" if status == "ok" else None,
        policy_version="test",
        reading=reading if status == "ok" else None,
        first_message=first,
        language_set=language_set,
        fired=fired,
        escalation=escalation,
    )


def conversation(env, build, *, language="es-MX") -> dict[str, Any]:
    """A Qualified Application whose start-date question went badly, with the signals stored."""
    app = build.application(language=language)
    conn = env.conn
    m1 = say(conn, app, "Hola", 1)
    keep(conn, app, m1, 1, stage="greeting", first=True)
    m2 = say(conn, app, "no entiendo qué me preguntas", 5)
    keep(conn, app, m2, 5, stage="start_date", fired=("confused",), confused=0.93)
    m3 = say(conn, app, "quiero hablar con una persona", 8)
    keep(
        conn, app, m3, 8, stage="start_date", fired=("wants_human", "frustrated"),
        escalation="wants_human", wants_human=0.97, frustrated=0.9,
    )  # fmt: skip
    return repo.require_application(conn, app["id"])


# --------------------------------------------------------------------------- notes


def test_no_signals_no_notes(env, build) -> None:
    app = build.application()
    assert communication_notes(env.conn, app) == []
    assert format_for_summary([], "es-MX") is None


def test_calm_signals_say_only_what_language_they_write_in(env, build) -> None:
    app = build.application()
    m = say(env.conn, app, "Hola", 1)
    keep(env.conn, app, m, 1, stage="greeting", first=True)
    [note] = communication_notes(env.conn, repo.require_application(env.conn, app["id"]))
    assert note["kind"] == "writes_in" and "español de México" in note["text"]


def test_notes_name_the_moment_not_the_score(env, build) -> None:
    app = conversation(env, build)
    notes = communication_notes(env.conn, app)
    kinds = [n["kind"] for n in notes]
    assert kinds == ["writes_in", "unsure", "asked_for_person", "impatient"]
    asked = next(n for n in notes if n["kind"] == "asked_for_person")
    assert "en la pregunta de fecha de inicio" in asked["text"] and "(" in asked["text"]
    assert asked["stage"] == "start_date" and isinstance(asked["message_id"], int)
    for note in notes:
        assert not SCORE.search(note["text"]), note["text"]
        for word in ("0.9", "frustración", "difícil", "puntuación", "score"):
            assert word not in note["text"].lower()


def test_notes_are_in_the_applications_language(env, build) -> None:
    app = conversation(env, build, language="en")
    texts = " ".join(n["text"] for n in communication_notes(env.conn, app))
    assert "Writes in English" in texts and "Asked for a person at the start date question" in texts
    assert "pregunta" not in texts


def test_unsure_notes_say_how_many_tries_it_took(env, build) -> None:
    app = conversation(env, build)
    for _ in range(3):
        repo.add_event(
            env.conn,
            candidate_id=app["candidate_id"],
            application_id=app["id"],
            kind="answer_invalid",
            actor="agent",
            data={"field": "start_date", "reason": "past_date"},
            now=at(6),
        )
    texts = [n["text"] for n in communication_notes(env.conn, app)]
    assert any("necesitó 3 intentos" in t for t in texts)


def test_a_language_switch_is_a_note_but_the_first_message_is_not(env, build) -> None:
    app = build.application(language="en")
    m1 = say(env.conn, app, "Hello", 1)
    keep(env.conn, app, m1, 1, stage="greeting", first=True, language_set="en")
    m2 = say(env.conn, app, "Perfecto, ¿y cuánto pagan?", 4)
    keep(env.conn, app, m2, 4, stage="name", language_set="es-MX")
    repo.set_candidate_language(env.conn, app["candidate_id"], "es-MX", now=at(4))
    notes = communication_notes(env.conn, repo.require_application(env.conn, app["id"]))
    switches = [n for n in notes if n["kind"] == "language_switch"]
    assert len(switches) == 1 and "español de México" in switches[0]["text"]


def test_an_unread_message_adds_nothing_and_is_not_calm(env, build) -> None:
    app = build.application()
    m = say(env.conn, app, "Hola", 1)
    keep(env.conn, app, m, 1, stage="greeting", status="timeout")
    assert communication_notes(env.conn, repo.require_application(env.conn, app["id"])) == []
    [row] = store.list_signals(env.conn, app["id"])
    assert row["status"] == "timeout" and row["frustrated"] is None


# --------------------------------------------------------------------------- summary


def writer(env) -> SummaryWriter:
    return SummaryWriter(DeterministicSummaryClient(), FakeClock(NOW), environment="test")


def test_the_summary_without_signals_has_no_notes(env, build) -> None:
    app = build.application()
    updated = writer(env).write(env.conn, app["id"])
    assert "communication_notes" not in updated["summary_structured"]
    assert "Cómo abordarlo" not in updated["summary_prose"]


def test_the_summary_gets_a_how_to_approach_paragraph(env, build) -> None:
    """The notes as one paragraph (`format_for_summary`) carry no score and no verdict.

    Changed for ADR 0056: the Summary no longer gets this paragraph (see the next test); the
    check now runs on the paragraph itself. The name stays so the floor guard can pair the
    assertions that kept their text."""
    app = conversation(env, build)
    notes = [n["text"] for n in communication_notes(env.conn, app)]
    assert len(notes) == 4 and not any(SCORE.search(n) for n in notes)
    prose = format_for_summary(communication_notes(env.conn, app), "es-MX")
    assert "Cómo abordarlo: " in prose and notes[1] in prose
    assert not SCORE.search(prose.split("Cómo abordarlo")[1])
    for word in ("apto", "idóneo", "calificado para el puesto", "difícil"):
        assert word not in prose.lower()


def test_the_summary_is_prose_only_even_when_signals_fired(env, build) -> None:
    """ADR 0056: the notes live on the Application detail API, never in the Summary."""
    app = conversation(env, build)
    notes = [n["text"] for n in communication_notes(env.conn, app)]
    updated = writer(env).write(env.conn, app["id"])
    assert "communication_notes" not in updated["summary_structured"]
    prose = updated["summary_prose"]
    assert prose and "\n" not in prose
    for heading in ("Cómo abordarlo", "How to approach"):
        assert heading not in prose
    assert not any(note in prose for note in notes)
    english = conversation(env, build, language="en")
    prose_en = writer(env).write(env.conn, english["id"])["summary_prose"]
    assert "How to approach" not in prose_en and "Cómo abordarlo" not in prose_en


def test_the_api_still_returns_the_notes_after_the_summary_is_written(env, build, contract) -> None:
    app = conversation(env, build)
    writer(env).write(env.conn, app["id"])
    body = contract.conforms(
        env.client(PUEBLA).get(f"/api/applications/{app['id']}"), "getApplication"
    )
    assert {n["kind"] for n in body["communication_notes"]} >= {"asked_for_person", "unsure"}
    assert "communication_notes" not in body["summary"]["structured"]
    assert "Cómo abordarlo" not in body["summary"]["prose"]


def test_the_model_never_sees_the_notes(env, build) -> None:
    app = conversation(env, build)
    seen: list[dict[str, Any]] = []

    class Spy(DeterministicSummaryClient):
        def write_prose(self, request):
            seen.append(request.structured)
            return super().write_prose(request)

    SummaryWriter(Spy(), FakeClock(NOW), environment="test").write(env.conn, app["id"])
    assert seen and "communication_notes" not in seen[0]


# --------------------------------------------------------------------------- API


def test_the_detail_carries_flags_and_notes_never_probabilities(env, build, contract) -> None:
    app = conversation(env, build)
    response = env.client(PUEBLA).get(f"/api/applications/{app['id']}")
    body = contract.conforms(response, "getApplication")
    flags = body["message_signals"]
    assert [f["fired"] for f in flags] == [["confused"], ["wants_human", "frustrated"]]
    assert flags[1]["escalated"] is True and flags[0]["escalated"] is False
    assert {n["kind"] for n in body["communication_notes"]} >= {"asked_for_person", "unsure"}
    wire = json.dumps({"s": body["message_signals"], "n": body["communication_notes"]})
    for leaked in ("0.97", "0.93", 'wants_human": 0', 'frustrated": 0', "typesafe", "model"):
        assert leaked not in wire


def test_the_detail_of_an_unread_conversation_has_empty_lists(env, build, contract) -> None:
    app = build.application()
    body = contract.conforms(
        env.client(PUEBLA).get(f"/api/applications/{app['id']}"), "getApplication"
    )
    assert body["message_signals"] == [] and body["communication_notes"] == []


def test_signals_follow_the_applications_location_scope(env, build) -> None:
    app = conversation(env, build)
    assert env.client(LA_PAZ).get(f"/api/applications/{app['id']}").status_code == 200
    outsider = env.client("recruiter.madrid@sazon.example")
    assert outsider.get(f"/api/applications/{app['id']}").status_code == 404


def test_the_aggregate_counts_by_stage_without_naming_anyone(env, build, contract) -> None:
    conversation(env, build)
    other = build.application()
    m = say(env.conn, other, "hola", 2)
    keep(env.conn, other, m, 2, stage="start_date", status="error")
    body = contract.conforms(
        env.client(PUEBLA).get("/api/metrics/conversation-signals?from=2026-10-09&to=2026-10-09"),
        "getConversationSignals",
    )
    stages = {row["stage"]: row for row in body["by_stage"]}
    start = stages["start_date"]
    assert (start["messages"], start["unread"]) == (2, 1)
    assert (start["confused"], start["wants_human"], start["frustrated"]) == (1, 1, 1)
    assert start["shares"]["wants_human"] == 0.5
    assert body["totals"]["messages"] == 3 and body["totals"]["unread"] == 1
    assert "application" not in json.dumps(body) and "candidate" not in json.dumps(body)


def test_the_aggregate_respects_the_callers_scope_and_auth(env, build) -> None:
    conversation(env, build)
    assert env.client().get("/api/metrics/conversation-signals").status_code == 401
    madrid = env.client("recruiter.madrid@sazon.example")
    assert (
        madrid.get("/api/metrics/conversation-signals?from=2026-10-09&to=2026-10-09").json()[
            "totals"
        ]["messages"]
        == 0
    )
    operator = env.client(OPERATOR)
    assert (
        operator.get("/api/metrics/conversation-signals?from=2026-10-09&to=2026-10-09").json()[
            "totals"
        ]["messages"]
        == 3
    )


def test_the_label_is_the_one_the_recruiter_sees() -> None:
    assert notes_label("es-ES").startswith("Notas de comunicación")
    assert notes_label("en") == (
        "Communication notes from the conversation, not an assessment of the Candidate"
    )


# --------------------------------------------------------------------------- retention, erasure


def test_deleting_a_message_deletes_its_signals(env, build) -> None:
    app = conversation(env, build)
    message_id = store.list_signals(env.conn, app["id"])[0]["message_id"]
    env.conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
    assert len(store.list_signals(env.conn, app["id"])) == 2


def test_purge_deletes_only_signals_older_than_the_period(env, build) -> None:
    app = conversation(env, build)
    assert store.purge_signals(env.conn, at(60 * 24 * 179), 180) == 0
    assert store.purge_signals(env.conn, at(60 * 24 * 181), 180) == 3
    assert store.list_signals(env.conn, app["id"]) == []
