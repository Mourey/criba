"""Operator-only Test chat and Demo stage conversations (``GET /api/conversations``).

A conversation is a ``candidate_identities`` row (``channel`` ``webchat`` or ``voice``,
``external_id`` = the conversation id, ADR 0060, ADR 0067). The identity row records the
``client`` that started it; only ``test_chat`` and ``demo`` conversations are listed. Rows from
before the origin was recorded (``client`` null), simulator runs and WhatsApp identities are
never returned. A conversation's messages, last message and Application are those of its own
channel: a voice conversation reads the spoken transcript (no audio is stored), a web chat
conversation the typed one.

Conversation ids are bearer secrets for the public web chat API, so they appear only in these
response bodies and the read's request path, and this module logs nothing. Messages are stored
per Candidate and channel, not per conversation, so two conversations that share a phone share
messages only within the same channel (a web chat and a voice conversation never do).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Annotated, Any

from fastapi import Query

from criba.api import present
from criba.api.applications import decode_cursor, encode_cursor
from criba.api.auth import Ctx, Operator
from criba.api.errors import not_found
from criba.api.routing import new_router
from criba.db import repo

router = new_router(tags=["conversations"])

_LISTED = "ci.channel IN ('webchat', 'voice') AND ci.client IN ('test_chat', 'demo')"

# The Candidate's latest message on the conversation's channel (ids only grow) and the
# Application it belongs to.
_SELECT = f"""
SELECT ci.external_id AS conversation_id, ci.channel, ci.client, ci.persona_id, c.phone,
       ci.created_at,
       (SELECT count(*) FROM candidate_identities w
         WHERE w.candidate_id = ci.candidate_id AND w.channel = ci.channel) > 1 AS shared_phone,
       lm.created_at AS last_message_at,
       a.id AS app_id, a.stage AS app_stage, a.outcome AS app_outcome, a.status AS app_status
  FROM candidate_identities ci
  JOIN candidates c ON c.id = ci.candidate_id
  LEFT JOIN messages lm ON lm.id = (
       SELECT m.id FROM messages m
        WHERE m.candidate_id = ci.candidate_id AND m.channel = ci.channel
        ORDER BY m.id DESC LIMIT 1)
  LEFT JOIN applications a ON a.id = lm.application_id
 WHERE {_LISTED}
"""


def _conversation(row: sqlite3.Row) -> dict[str, Any]:
    application = None
    if row["app_id"] is not None:
        application = {
            "id": row["app_id"],
            "stage": row["app_stage"],
            "outcome": row["app_outcome"],
            "status": row["app_status"],
        }
    return {
        "conversation_id": row["conversation_id"],
        "channel": row["channel"],
        "client": row["client"],
        "persona_id": row["persona_id"],
        "phone": row["phone"],
        "created_at": row["created_at"],
        "shared_phone": bool(row["shared_phone"]),
        "last_message_at": row["last_message_at"],
        "application": application,
    }


@router.get("/conversations")
def list_conversations(
    user: Operator,
    ctx: Ctx,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    offset = decode_cursor(cursor)
    with ctx.db() as conn:
        rows = repo.conversation_rows(conn, _SELECT, limit + 1, offset)
    return {
        "items": [_conversation(r) for r in rows[:limit]],
        "next_cursor": encode_cursor(offset + limit) if len(rows) > limit else None,
    }


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user: Operator, ctx: Ctx) -> dict[str, Any]:
    with ctx.db() as conn:
        found = repo.conversation_row(conn, _SELECT, conversation_id)
        if found is None:
            raise not_found("Conversation not found.")
        identity = repo.identity_candidate_row(conn, found["channel"], conversation_id)
        assert identity is not None
        rows = repo.candidate_channel_messages(conn, identity["candidate_id"], found["channel"])
        messages = [dict(r) for r in rows]
        for message in messages:
            if message["interactive"] is not None:
                message["interactive"] = json.loads(message["interactive"])
        names = present.names_for(
            conn, {m["sender_user_id"] for m in messages if m["sender_user_id"]}
        )
        return {
            **_conversation(found),
            "messages": [present.message_json(m, names) for m in messages],
        }
