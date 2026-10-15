"""Responses validate against the frozen schemas in contracts/webchat.openapi.yaml."""

from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient

from criba.db import repo

from .conftest import Fakes, candidate_for, start

CONTRACT = Path(__file__).resolve().parents[4] / "contracts" / "webchat.openapi.yaml"


@pytest.fixture(scope="module")
def components() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))["components"]


def check(components: dict, name: str, value: object) -> None:
    schema = {"$ref": f"#/components/schemas/{name}", "components": components}
    jsonschema.Draft202012Validator(schema).validate(value)


def test_responses_match_the_contract(client: TestClient, components: dict, fakes: Fakes) -> None:
    created = client.post("/api/chat/conversations", json={"country": "ES"})
    check(components, "Conversation", created.json())
    conv = created.json()["conversation_id"]
    check(components, "Conversation", client.get(f"/api/chat/conversations/{conv}").json())

    fakes.choice = {"style": "buttons", "options": [{"id": "yes", "label": "Sí"}]}
    accepted = client.post(
        f"/api/chat/conversations/{conv}/messages", json={"message_id": "c1", "text": "hola"}
    )
    check(components, "MessageAccepted", accepted.json())
    page = client.get(f"/api/chat/conversations/{conv}/messages").json()
    tap = client.post(
        f"/api/chat/conversations/{conv}/replies",
        json={"message_id": "c2", "in_reply_to": page["messages"][-1]["id"], "option_id": "yes"},
    )
    check(components, "MessageAccepted", tap.json())
    page = client.get(f"/api/chat/conversations/{conv}/messages").json()
    check(components, "MessagePage", page)
    assert {m["sender"] for m in page["messages"]} == {"candidate", "agent"}

    for resp in (
        client.get("/api/chat/conversations/wc_unknown_conversation"),
        client.post("/api/chat/conversations", json={}),
    ):
        check(components, "Error", resp.json())


def test_start_with_phone_matches_contract(client: TestClient, components: dict) -> None:
    conv = start(client, phone="+34612345678")
    check(components, "Conversation", client.get(f"/api/chat/conversations/{conv}").json())


def test_messages_carry_their_modality_read_only(
    client: TestClient, components: dict, conn
) -> None:
    assert components["schemas"]["ChatMessage"]["properties"]["modality"]["readOnly"] is True
    conv = start(client)
    sent = client.post(
        f"/api/chat/conversations/{conv}/messages", json={"message_id": "c1", "text": "hola"}
    )
    assert sent.status_code == 202, sent.text
    candidate = candidate_for(conn, conv)
    application = repo.get_open_application(conn, candidate["id"])
    assert application is not None
    spoken, _ = repo.add_message(
        conn,
        candidate_id=candidate["id"],
        application_id=application["id"],
        channel="webchat",
        direction="outbound",
        sender="agent",
        body="Te escucho",
        modality="spoken",
    )

    page = client.get(f"/api/chat/conversations/{conv}/messages").json()
    check(components, "MessagePage", page)
    modalities = {m["id"]: m["modality"] for m in page["messages"]}
    assert modalities.pop(spoken["id"]) == "spoken"
    assert modalities and set(modalities.values()) == {"typed"}

    count = len(page["messages"])
    refused = client.post(
        f"/api/chat/conversations/{conv}/messages",
        json={"message_id": "c2", "text": "hola", "modality": "spoken"},
    )
    assert refused.status_code == 422
    check(components, "Error", refused.json())
    assert len(client.get(f"/api/chat/conversations/{conv}/messages").json()["messages"]) == count
