"""Default Candidate sender: store the outbound message (lane W2-B replaces it).

On the web chat, "sending" is storing: the Candidate's client reads outbound messages
from the ``messages`` table. W2-B's sender adds the channel rules (quiet hours, the
WhatsApp 24-hour window, delivery).

**SYNTHETIC wording.** The texts below are placeholders until Grupo Sazón approves the
real templates (Phase 1 detailed spec: the Candidate is told by a client-approved
template). Team members are never named to the Candidate.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from criba.api.services import Notice
from criba.db import repo

_JOINED = {
    "es-ES": "Alguien del equipo de {client} se ha unido a la conversación.",
    "es-MX": "Alguien del equipo de {client} se unió a la conversación.",
    "en": "Someone from the {client} team has joined the conversation.",
}
_REJECTION = {
    "es-ES": (
        "Gracias por tu interés en trabajar con {client}. Por ahora no podemos continuar "
        "con tu candidatura. Te deseamos mucho éxito."
    ),
    "es-MX": (
        "Gracias por tu interés en trabajar con {client}. Por ahora no podemos continuar "
        "con tu candidatura. Te deseamos mucho éxito."
    ),
    "en": (
        "Thank you for your interest in working with {client}. We can't take your "
        "application further for now. We wish you every success."
    ),
}


def notice_language(app: dict[str, Any]) -> str:
    if app.get("language") in ("es-ES", "es-MX", "en"):
        return app["language"]
    return "es-ES" if app.get("country") == "ES" else "es-MX"


def notice_text(notice: Notice, language: str, client: str) -> str:
    table = _JOINED if notice == "takeover_joined" else _REJECTION
    return table[language].format(client=client)


class StoredMessageSender:
    def send_human_text(
        self,
        conn: sqlite3.Connection,
        *,
        application_id: str,
        user_id: str,
        text: str,
        now: datetime,
    ) -> dict[str, Any]:
        app = repo.require_application(conn, application_id)
        message, _ = repo.add_message(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            channel=app["channel"],
            direction="outbound",
            sender="human",
            sender_user_id=user_id,
            body=text,
            now=now,
        )
        return message

    def send_notice(
        self, conn: sqlite3.Connection, *, application_id: str, notice: Notice, now: datetime
    ) -> dict[str, Any] | None:
        app = repo.require_application(conn, application_id)
        client = repo.client_name(conn, app["client_id"]) or "el equipo"
        body = notice_text(notice, notice_language(app), client)
        message, _ = repo.add_message(
            conn,
            candidate_id=app["candidate_id"],
            application_id=application_id,
            channel=app["channel"],
            direction="outbound",
            sender="system",
            body=body,
            content_type="template" if notice == "rejection" else "text",
            template_name="rejection" if notice == "rejection" else None,
            now=now,
        )
        return message
