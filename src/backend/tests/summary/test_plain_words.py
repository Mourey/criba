"""The Summary is prose in a Recruiter's words (ADR 0056): no stage, field or flag key and no id
in the text the model reads for it, in the text the sim writer produces, or in what is stored."""

import logging
from datetime import UTC, datetime

import pytest

from criba.db import repo
from criba.summary import (
    DeterministicSummaryClient,
    ProseRequest,
    ProseResult,
    SummaryWriter,
    build_structured,
    render_user_prompt,
)
from criba.summary_text import FLAG_LABELS, flag_text, leaked_codes, render_facts

T0 = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
ALL_FLAGS = list(FLAG_LABELS)
LEAKY = "La solicitud ha superado el filtro. Etapa alcanzada: qualified, zona indicada: Puebla."
CLEAN = "La solicitud ha superado el filtro y espera la aprobación de un reclutador."


class Sequence:
    """A stub model that answers from a list, in order, and remembers each request."""

    model = "stub-sonnet"

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.requests: list[ProseRequest] = []

    def write_prose(self, request: ProseRequest) -> ProseResult:
        self.requests.append(request)
        return ProseResult(text=self.texts[len(self.requests) - 1], model=self.model)


def usage_rows(conn, application_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM usage WHERE application_id = ?", (application_id,))
    return [dict(r) for r in rows.fetchall()]


# --------------------------------------------------------------------------- the code check


@pytest.mark.parametrize(
    "text",
    [
        "Etapa alcanzada: qualified, zona indicada: Polanco.",
        "Stage reached: qualified.",
        "Estado: knockout",
        "Pidió su fecha en el campo start_date.",
        "El campo full_name está vacío.",
        "Avisos activos: review_requested, stalled.",
        "La etapa `qualified` ya está lista.",
        "Application app_01M4KGAMJJ9NHHDQVN0SZX26AC passed.",
        "Zona zone_mx_puebla_centro.",
        "Uses uber_eats and just_eat.",
    ],
)
def test_internal_codes_are_found(text: str) -> None:
    assert leaked_codes(text)


@pytest.mark.parametrize(
    "text",
    [
        "The application is qualified and waiting for a Recruiter's approval.",
        "La solicitud ha superado el filtro; la zona indicada es Polanco.",
        "Pide un horario flexible y puede empezar el lunes.",
        "Experience on Glovo and Uber Eats; the zone is Chamberí.",
        "Escribe como juan_perez@correo.example en su mensaje.",
        "Ended in a Knockout on the driving licence.",
    ],
)
def test_ordinary_prose_is_clean(text: str) -> None:
    assert leaked_codes(text) == []


def test_the_codes_are_reported_once_in_order_and_the_known_id_is_caught() -> None:
    found = leaked_codes("full_name, then full_name and start_date, in app-1", ids=("app-1",))
    assert found == ["full_name", "start_date", "app-1"]


def test_every_flag_has_words_in_both_languages() -> None:
    for flag in ALL_FLAGS:
        for language in ("es-MX", "en"):
            assert flag not in flag_text(flag, language)
            assert leaked_codes(flag_text(flag, language)) == []
    assert flag_text("unknown_flag", "en") == "unknown flag"


# --------------------------------------------------------------------------- what the model reads


def test_the_model_reads_a_fact_sheet_with_no_codes(conn, clock, qualified_application) -> None:
    app = qualified_application(conn)
    structured = build_structured(conn, app["id"], now=clock.now())
    structured["flags"] = ALL_FLAGS
    facts = render_facts(structured)
    assert leaked_codes(facts, ids=(app["id"], app["candidate_id"])) == []
    assert "passed Screening and is waiting for a Recruiter's approval" in facts
    assert "- Name: Luis Hernández" in facts
    assert "- Availability: full time" in facts and "DiDi Food" in facts
    prompt = render_user_prompt(ProseRequest(structured, [], "finished", "es-MX"))
    assert '"stage"' not in prompt and "full_name" not in prompt
    assert leaked_codes(prompt.split("<transcript>")[0]) == []


def test_the_fact_sheet_describes_a_closed_application_in_words(conn, new_application) -> None:
    app = new_application(conn)
    repo.record_answer(conn, app["id"], "driver_license", False, now=T0)
    repo.close_application(conn, app["id"], "knockout", knockout_criterion="driver_license", now=T0)
    facts = render_facts(build_structured(conn, app["id"], now=T0))
    assert "ended in a Knockout on the driving licence" in facts
    assert "- Driving licence: no" in facts and "- Name: not given" in facts
    assert leaked_codes(facts) == []


@pytest.mark.parametrize(
    ("structured", "words"),
    [
        ({"stage": "approved"}, "approved by a Recruiter"),
        ({"stage": "interview_booked"}, "an Interview is booked"),
        ({"stage": "start_date"}, "while asking for the start date"),
        ({"stage": "experience", "outcome": "abandoned"}, "abandoned after the Candidate"),
        ({"stage": "zone", "outcome": "withdrawn"}, "withdrawn by the Candidate"),
        ({"stage": "weird"}, "Screening is still in progress."),
        ({"stage": "qualified", "reopened_count": 2}, "Reopened 2 time(s)"),
    ],
)
def test_the_state_line_is_always_words(structured, words: str) -> None:
    facts = render_facts(structured)
    assert words in facts
    assert leaked_codes(facts) == []


# --------------------------------------------------------------------------- the sim writer


@pytest.mark.parametrize("language", ["es-MX", "es-ES", "en"])
def test_the_deterministic_prose_has_no_stage_line_and_no_codes(
    conn, clock, qualified_application, language: str
) -> None:
    app = qualified_application(conn)
    structured = build_structured(conn, app["id"], now=clock.now())
    structured["flags"] = ALL_FLAGS
    request = ProseRequest(structured, [], "finished", language)
    text = DeterministicSummaryClient().write_prose(request).text
    assert leaked_codes(text, ids=(app["id"],)) == []
    for stage_line in ("Etapa alcanzada", "Stage reached", "Active flags"):
        assert stage_line not in text
    assert "Puebla" in text or "Centro" in text


def test_the_deterministic_prose_for_a_closed_application_is_clean(conn, new_application) -> None:
    app = new_application(conn)
    repo.close_application(conn, app["id"], "abandoned", now=T0)
    structured = build_structured(conn, app["id"], now=T0)
    for language in ("es-MX", "en"):
        text = (
            DeterministicSummaryClient()
            .write_prose(ProseRequest(structured, [], "finished", language))
            .text
        )
        assert leaked_codes(text, ids=(app["id"],)) == []


def test_the_stored_sim_summary_has_no_codes(conn, clock, qualified_application) -> None:
    app = qualified_application(conn)
    done = SummaryWriter(DeterministicSummaryClient(), clock, environment="test").write(
        conn, app["id"]
    )
    assert leaked_codes(done["summary_prose"], ids=(app["id"],)) == []
    assert "Etapa alcanzada" not in done["summary_prose"]


# ----------------------------------------------------------------------- the check on the output


def write(conn, clock, client, app) -> dict:
    return SummaryWriter(client, clock, environment="test").write(conn, app["id"])


def test_prose_with_a_code_is_asked_again_with_the_code_named(
    conn, clock, qualified_application, caplog
) -> None:
    app = qualified_application(conn)
    model = Sequence(LEAKY, CLEAN)
    with caplog.at_level(logging.WARNING, logger="criba.summary"):
        done = write(conn, clock, model, app)
    assert done["summary_prose"] == CLEAN
    first, second = model.requests
    assert first.avoid == () and second.avoid == ("qualified",)
    assert "system codes (qualified)" in render_user_prompt(second)
    assert "system codes" not in render_user_prompt(first)
    assert "internal codes" in caplog.text
    assert len(usage_rows(conn, app["id"])) == 2


def test_prose_that_keeps_a_code_is_dropped_not_stored(
    conn, clock, qualified_application, caplog
) -> None:
    app = qualified_application(conn)
    model = Sequence(LEAKY, "El campo full_name está completo.")
    with caplog.at_level(logging.ERROR, logger="criba.summary"):
        done = write(conn, clock, model, app)
    assert done["summary_prose"] is None
    assert done["summary_structured"]["stage"] == "qualified"
    assert len(model.requests) == 2 and "carrying internal codes" in caplog.text


def test_the_application_id_in_prose_counts_as_a_code(conn, clock, qualified_application) -> None:
    app = qualified_application(conn)
    model = Sequence(f"La solicitud {app['id']} ha superado el filtro.", CLEAN)
    done = write(conn, clock, model, app)
    assert done["summary_prose"] == CLEAN
    assert model.requests[1].avoid == (app["id"],)


def test_clean_prose_costs_one_call(conn, clock, qualified_application) -> None:
    app = qualified_application(conn)
    model = Sequence(CLEAN)
    assert write(conn, clock, model, app)["summary_prose"] == CLEAN
    assert len(model.requests) == 1
