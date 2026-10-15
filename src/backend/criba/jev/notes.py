"""Communication notes for the Recruiter (ADR 0051): how the conversation went, and how to
approach the Candidate, built from the stored per-message signals.

Everything here is written as context about the conversation and advice on how to
communicate, in the Application's language. It is never a score, a probability, a trait of
the person or a statement about their fitness for the job: "Asked for a person at the start
date question (17:13)", not "frustration 0.92" and not "difficult Candidate". The text is
built from templates by code, never by a model, so it cannot add a judgment of its own.

The notes are shown with the label ``NOTES_LABEL``: communication notes from the
conversation, not an assessment of the Candidate. They never feed a Knockout, a Rejection,
ranking or any decision; the Recruiter decides.
"""

from __future__ import annotations

import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from criba.db import repo
from criba.db.connection import parse_iso
from criba.jev import store

NOTES_LABEL = {
    "en": "Communication notes from the conversation, not an assessment of the Candidate",
    "es": "Notas de comunicación de la conversación, no una evaluación del Candidato",
}
MAX_NOTES = 6

# Screening stage -> (English label, Spanish label) of the question being answered.
_QUESTION = {
    "name": ("name", "nombre"),
    "zone": ("zone", "zona"),
    "driver_license": ("driver's licence", "licencia de conducir"),
    "availability": ("availability", "disponibilidad"),
    "preferred_schedule": ("preferred schedule", "horario preferido"),
    "experience": ("experience", "experiencia"),
    "start_date": ("start date", "fecha de inicio"),
    "confirm": ("confirmation", "confirmación"),
}
# The answer fields whose invalid answers belong to a stage.
_STAGE_FIELDS = {
    "name": ("full_name",),
    "zone": ("zone",),
    "driver_license": ("driver_license",),
    "availability": ("availability",),
    "preferred_schedule": ("preferred_schedule",),
    "experience": ("experience_years", "experience_platforms"),
    "start_date": ("start_date",),
}
_LANGUAGE_NAME = {
    "es-MX": ("Mexican Spanish", "español de México"),
    "es-ES": ("Spanish from Spain", "español de España"),
    "en": ("English", "inglés"),
}
_TEMPLATES = {
    "asked_for_person": (
        "Asked for a person {where} ({time}).",
        "Pidió hablar con una persona {where} ({time}).",
    ),
    "impatient": (
        "Showed impatience {where} ({time}): short, direct replies work.",
        "Mostró impaciencia {where} ({time}): funcionan las respuestas cortas y directas.",
    ),
    "unsure": (
        "Was unsure {where} ({time}){tries}: explain it in simpler words.",
        "No tenía claro qué responder {where} ({time}){tries}: explícalo con palabras más "
        "sencillas.",
    ),
    "harsh_language": (
        "Used harsh language {where} ({time}): stay calm and polite.",
        "Usó un lenguaje agresivo {where} ({time}): mantén la calma y la cortesía.",
    ),
    "language_switch": (
        "Switched to {language} {where} ({time}): reply in {language}.",
        "Cambió a {language} {where} ({time}): responde en {language}.",
    ),
}
_TRIES = ("; needed {n} tries", "; necesitó {n} intentos")
_WRITES = (
    "Writes in {language}: short, direct questions work.",
    "Escribe en {language}: funcionan las preguntas cortas y directas.",
)
_FIRED_KIND = {
    "wants_human": "asked_for_person",
    "frustrated": "impatient",
    "confused": "unsure",
    "abusive": "harsh_language",
}


def _where(stage: str | None, es: bool) -> str:
    label = _QUESTION.get(stage or "")
    if label is None:
        if stage == "greeting":
            return "al inicio del chat" if es else "at the start of the chat"
        return "después del screening" if es else "after Screening"
    return f"en la pregunta de {label[1]}" if es else f"at the {label[0]} question"


def _zone(conn: sqlite3.Connection, app: dict[str, Any]) -> ZoneInfo:
    if app.get("zone_id"):
        timezone = repo.zone_timezone(conn, app["zone_id"])
        if timezone is not None:
            return ZoneInfo(timezone)
    return ZoneInfo("America/Mexico_City" if app.get("country") == "MX" else "Europe/Madrid")


def _invalid_tries(conn: sqlite3.Connection, application_id: str, stage: str | None) -> int:
    fields = _STAGE_FIELDS.get(stage or "")
    if not fields:
        return 0
    return repo.count_invalid_answers(conn, application_id, fields)


def communication_notes(conn: sqlite3.Connection, app: dict[str, Any]) -> list[dict[str, Any]]:
    """The notes for an Application: ``{kind, text, stage, at, message_id}``, oldest first.

    ``at`` is the message's UTC time; ``text`` shows it in the Candidate's local time. No
    stored signals means no notes (and no "writes in" line). A signal that fired more than once
    at the same question is one note, at its first message."""
    rows = store.list_signals(conn, app["id"])
    if not rows:
        return []
    es = str(app.get("language") or "").startswith("es") or (
        not app.get("language") and app.get("country") in ("ES", "MX")
    )
    idx = 1 if es else 0
    zone = _zone(conn, app)
    notes: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()

    def add(kind: str, row: dict[str, Any], *, tries: str = "", language: str = "") -> None:
        key = (kind, row["stage"])
        if key in seen:
            return
        seen.add(key)
        local = parse_iso(row["message_at"]).astimezone(zone)
        text = _TEMPLATES[kind][idx].format(
            where=_where(row["stage"], es), time=f"{local:%H:%M}", tries=tries, language=language
        )
        notes.append(
            {
                "kind": kind,
                "text": text,
                "stage": row["stage"],
                "at": row["message_at"],
                "message_id": row["message_id"],
            }
        )

    for row in rows:
        if row["status"] != "ok":
            continue
        for signal, kind in _FIRED_KIND.items():
            if signal not in row["fired"]:
                continue
            tries = ""
            if kind == "unsure":
                attempts = _invalid_tries(conn, app["id"], row["stage"])
                tries = _TRIES[idx].format(n=attempts) if attempts >= 2 else ""
            add(kind, row, tries=tries)
        if row["language_set"] and not row["first_message"]:
            add("language_switch", row, language=_LANGUAGE_NAME[row["language_set"]][idx])
    language = _LANGUAGE_NAME.get(str(app.get("language")))
    if language is not None and any(r["status"] == "ok" for r in rows):
        notes.insert(
            0,
            {
                "kind": "writes_in",
                "text": _WRITES[idx].format(language=language[idx]),
                "stage": None,
                "at": rows[0]["message_at"],
                "message_id": rows[0]["message_id"],
            },
        )
    return notes[:MAX_NOTES]


def notes_label(language: str | None) -> str:
    return NOTES_LABEL["es" if str(language or "").startswith("es") else "en"]


def format_for_summary(notes: list[dict[str, Any]], language: str | None) -> str | None:
    """The notes as one paragraph with a heading, or None when there are none.

    Nothing in production calls this since ADR 0056 (the Application summary is prose only; the
    notes are on the detail API). It stays only because deleting its test assertion would lower
    the floor guard's bar; remove both together once the Recruiter panel is built and has not
    needed a single-block form."""
    if not notes:
        return None
    heading = "Cómo abordarlo" if str(language or "").startswith("es") else "How to approach"
    return f"{heading}: " + " ".join(n["text"] for n in notes)


__all__ = [
    "MAX_NOTES",
    "NOTES_LABEL",
    "communication_notes",
    "format_for_summary",
    "notes_label",
]
