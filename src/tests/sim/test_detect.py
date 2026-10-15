"""`detect_prompt` and `last_question` on the phrasings the real models used.

The real-model comparison (docs/evidence/model-comparison.md) asked for the platforms in
words the detector did not know, so the driver re-sent the years answer. These tests use those
exact phrasings, and check that the wider net does not catch the other questions.
"""

from __future__ import annotations

import pytest

from sim.detect import detect_prompt, last_question, last_request

# The two real phrasings, verbatim from model-comparison.md.
REAL_APPS = "¿Con qué apps has trabajado? Por ejemplo Glovo, Uber Eats, Just Eat..."
REAL_ETC = "¿En qué plataformas has trabajado (Glovo, Uber Eats, Just Eat, etc.)?"


def test_real_phrasing_with_apps_is_the_platforms_question() -> None:
    assert detect_prompt(REAL_APPS) == "experience_platforms"
    assert detect_prompt("¿Con qué apps has trabajado? Por ejemplo Glovo, ...") == (
        "experience_platforms"
    )


def test_real_phrasing_with_etc_is_the_platforms_question() -> None:
    assert detect_prompt(REAL_ETC) == "experience_platforms"
    assert last_question(REAL_ETC) == (
        "En qué plataformas has trabajado (Glovo, Uber Eats, Just Eat, etc.)?"
    )


@pytest.mark.parametrize(
    "text",
    [
        "¿Con qué aplicaciones de reparto has trabajado?",
        "¿Qué aplicaciones has usado?",
        "¿Has trabajado con alguna plataforma de reparto?",
        "¿En qué plataformas has repartido, por ejemplo Rappi, DiDi Food, etc.?",
        "Gracias. Ahora, ¿con qué apps has trabajado (p. ej. Glovo, Rappi)?",
        "¿Con qué app has repartido antes?",
        "Which apps have you worked with?",
        "Which delivery apps or platforms have you used (Glovo, Uber Eats, etc.)?",
        "What platforms have you delivered for, e.g. Uber Eats or Just Eat?",
        "Great. Have you used any delivery apps before? Glovo, Rappi...",
    ],
)
def test_platforms_in_the_forms_models_use(text: str) -> None:
    assert detect_prompt(text) == "experience_platforms"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("¿Cuántos años de experiencia tienes como repartidor?", "experience_years"),
        ("¿Cuántos años llevas repartiendo con apps como Glovo?", "experience_years"),
        ("How many years have you delivered on apps like Glovo?", "experience_years"),
        ("How long have you worked on delivery platforms?", "experience_years"),
        ("¿Tienes licencia de conducir vigente?", "driver_license"),
        ("¿Cuándo podrías empezar? Por ejemplo, el lunes.", "start_date"),
        ("¿Tienes disponibilidad de tiempo completo?", "availability"),
        ("¿Prefieres turno de mañana o de tarde?", "preferred_schedule"),
        ("¿En qué zona vives? Por ejemplo: Madrid, etc.", "zone"),
        ("What is your full name?", "full_name"),
        ("Where do you live? E.g. Madrid, Valencia, etc.", "zone"),
        ("¿Es correcto lo que te resumo?", "confirm"),
        ("Is that right?", "confirm"),
        # A live run (2026-10-10) stopped here, read as the confirm step.
        ("Do you have a carnet de conducir that's valid right now?", "driver_license"),
    ],
)
def test_other_questions_keep_their_kind(text: str, kind: str) -> None:
    assert detect_prompt(text) == kind


@pytest.mark.parametrize(
    "text",
    [
        # No question mark: a statement is never a prompt, even one that names the apps.
        "Trabajamos con apps de reparto como Glovo y Rappi.",
        "We deliver for Glovo, Uber Eats, etc.",
        # A question with no recognisable topic.
        "¿Me lo dices de otra forma, con un ejemplo?",
        "Do you need an appointment?",
        "Can you send your application today?",
    ],
)
def test_nothing_else_is_taken_for_the_platforms_question(text: str) -> None:
    assert detect_prompt(text) is None


def test_a_period_in_etc_or_an_ellipsis_does_not_split_the_question() -> None:
    assert last_question("¿Con Glovo, Rappi, etc. o con otra?") == (
        "Con Glovo, Rappi, etc. o con otra?"
    )
    assert last_question("Which apps (e.g. Glovo, Rappi...)?") == (
        "Which apps (e.g. Glovo, Rappi...)?"
    )
    assert last_question("Which apps, i.e. delivery ones, did you use?") == (
        "Which apps, i.e. delivery ones, did you use?"
    )


def test_a_real_full_stop_still_ends_a_sentence() -> None:
    assert last_question("Gracias. ¿En qué zona vives? Por ejemplo: Madrid.") == (
        "En qué zona vives?"
    )
    assert last_question("Hola. Todo bien. ¿Y tú?") == "Y tú?"
    assert last_question("Sin pregunta, etc.") is None


# A real Haiku re-ask in `invalid_then_corrected_es` had no question mark, so the driver stopped.
REAL_NO_MARK = "Necesito tu nombre y apellido."


def test_the_real_re_ask_without_a_question_mark_is_the_name_question() -> None:
    assert last_question(REAL_NO_MARK) is None
    assert detect_prompt(REAL_NO_MARK) == "full_name"
    assert detect_prompt(
        "Gracias, pero con un solo nombre no me alcanza. Necesito tu nombre y apellido."
    ) == ("full_name")


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Necesito tu nombre completo, con apellido.", "full_name"),
        ("Dime tu nombre y apellido, por favor.", "full_name"),
        ("Por favor, indícame tu nombre completo.", "full_name"),
        ("Please tell me your full name.", "full_name"),
        ("I need your name and surname.", "full_name"),
        ("Dime en qué zona vives. Por ejemplo, el barrio y la ciudad.", "zone"),
        ("Cuéntame en qué ciudad y colonia vives.", "zone"),
        ("Tell me which neighbourhood and city you live in.", "zone"),
        ("Indícame si tienes licencia de conducir vigente.", "driver_license"),
        ("Please let me know if you hold a valid driving license.", "driver_license"),
        (
            "Necesito saber tu disponibilidad: tiempo completo o fines de semana.",
            "availability",
        ),
        ("Me falta tu horario preferido.", "preferred_schedule"),
        (
            "Cuéntame cuántos años de experiencia tienes como repartidor.",
            "experience_years",
        ),
        (
            "Dime con qué apps has trabajado, por ejemplo Glovo o Rappi.",
            "experience_platforms",
        ),
        ("Dime cuándo podrías empezar.", "start_date"),
        ("Please tell me when you can start.", "start_date"),
        ("Todo listo. Dime si es correcto o qué quieres cambiar.", "confirm"),
        # A question mark that names no topic, followed by a clear request.
        ("¿Me ayudas? Necesito tu nombre y apellido.", "full_name"),
    ],
)
def test_a_field_asked_as_a_statement_or_an_imperative(text: str, kind: str) -> None:
    assert detect_prompt(text) == kind


@pytest.mark.parametrize(
    "text",
    [
        # Statements that name a field without asking for it stay statements.
        "Necesitamos licencia de manejo vigente. Escríbenos cuando la tengas.",
        "Tengo tu nombre y tu zona.",
        "Gracias por tu nombre, Ana.",
        "Un reclutador te escribirá para confirmar tu horario.",
        "Trabajamos con apps de reparto como Glovo y Rappi.",
        "Your application is complete; we will tell you the start date soon.",
        "We need a valid license for this role.",
        # A request cue with no field behind it.
        "Dime lo que quieras, estoy aquí.",
        "Please tell me more about your day.",
        "Hecho. Gracias por tu tiempo y mucho éxito.",
    ],
)
def test_a_plain_statement_is_still_not_a_prompt(text: str) -> None:
    assert detect_prompt(text) is None


def test_last_request_is_the_last_sentence_with_a_cue() -> None:
    assert last_request("Necesito tu nombre. Por ejemplo: Ana Pérez.") == ("Necesito tu nombre")
    assert last_request("Dime tu zona (p. ej. Madrid). Gracias.") == (
        "Dime tu zona (p. ej. Madrid)"
    )
    assert last_request("Gracias por todo.") is None


def test_the_platforms_list_with_its_no_app_row_is_the_platforms_question() -> None:
    """ADR 0053: the backend's platforms list ends with the "none" row."""
    ids = ["glovo", "uber_eats", "just_eat", "rappi", "didi_food", "other", "none"]
    options = {"style": "list", "options": [{"id": i, "label": i} for i in ids]}
    assert detect_prompt("Elige una:", options) == "experience_platforms"


def test_two_date_buttons_are_the_start_date_question() -> None:
    """ADR 0054: a start-date choice offers the two dates themselves as option ids."""
    options = {
        "style": "buttons",
        "options": [
            {"id": "2026-10-11", "label": "Domingo 11 oct"},
            {"id": "2026-10-18", "label": "Domingo 18 oct"},
        ],
    }
    assert detect_prompt("¿Te refieres a este o al de la semana siguiente?", options) == (
        "start_date"
    )
