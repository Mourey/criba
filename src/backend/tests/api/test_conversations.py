"""Operator-only Test chat and Demo stage conversations (GET /api/conversations, ADR 0060)."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from criba.db import repo

from .support import OPERATOR, PUEBLA, at

CLIENT = "grupo_sazon"


class Seed:
    """Synthetic web chat and voice conversations, written through the repository."""

    def __init__(self, env: Any) -> None:
        self.env = env
        self.conn = env.conn
        self.count = 0

    def conversation(
        self,
        client: str | None,
        *,
        minutes: float,
        persona_id: str | None = None,
        phone: str | None = None,
        channel: str = "webchat",
        external_id: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """A Candidate with one identity row; ``client`` None leaves the origin unrecorded."""
        self.count += 1
        external_id = external_id or f"wc_{self.count:032x}"
        candidate, _ = repo.get_or_create_candidate(
            self.conn,
            client_id=CLIENT,
            channel=channel,
            external_id=external_id,
            phone=phone,
            country="MX",
            now=at(minutes),
            client=client,
            persona_id=persona_id,
        )
        return candidate, external_id

    def second_identity(self, candidate: dict[str, Any], *, minutes: float) -> str:
        self.count += 1
        external_id = f"wc_{self.count:032x}"
        self.conn.execute(
            "INSERT INTO candidate_identities"
            " (candidate_id, channel, external_id, created_at, client) VALUES (?, ?, ?, ?, ?)",
            (candidate["id"], "webchat", external_id, at(minutes).isoformat(), "test_chat"),
        )
        return external_id

    def message(
        self,
        candidate: dict[str, Any],
        application_id: str | None,
        body: str,
        *,
        minutes: float,
        channel: str = "webchat",
        inbound: bool = True,
    ) -> dict[str, Any]:
        message, _ = repo.add_message(
            self.conn,
            candidate_id=candidate["id"],
            application_id=application_id,
            channel=channel,
            direction="inbound" if inbound else "outbound",
            sender="candidate" if inbound else "agent",
            body=body,
            now=at(minutes),
        )
        return message


@pytest.fixture
def seed(env: Any) -> Seed:
    return Seed(env)


def ids(body: dict[str, Any]) -> list[str]:
    return [item["conversation_id"] for item in body["items"]]


# --------------------------------------------------------------------------- the list


def test_the_list_holds_only_test_chat_and_demo_conversations(env, seed, operator, contract):
    _, test_chat = seed.conversation("test_chat", minutes=1, phone="+525511111111")
    _, demo = seed.conversation("demo", minutes=2, persona_id="diego_es")
    seed.conversation("simulator", minutes=3)
    seed.conversation(None, minutes=4)
    seed.conversation("demo", minutes=5, channel="whatsapp", external_id="5215522222222")
    seed.conversation("simulator", minutes=6, channel="voice", external_id="wv_sim")

    body = contract.conforms(operator.get("/api/conversations"), "listConversations")
    assert ids(body) == [demo, test_chat]
    assert body["next_cursor"] is None
    assert set(body) == {"items", "next_cursor"}

    first = body["items"][1]
    assert first == {
        "conversation_id": test_chat,
        "channel": "webchat",
        "client": "test_chat",
        "persona_id": None,
        "phone": "+525511111111",
        "created_at": at(1).isoformat().replace("+00:00", "Z"),
        "shared_phone": False,
        "last_message_at": None,
        "application": None,
    }
    assert body["items"][0]["client"] == "demo"
    assert body["items"][0]["persona_id"] == "diego_es"
    assert body["items"][0]["phone"] is None


def test_the_list_is_newest_first_then_by_id(seed, operator):
    _, oldest = seed.conversation("test_chat", minutes=1)
    _, tie_a = seed.conversation("test_chat", minutes=5, external_id="wc_a")
    _, tie_b = seed.conversation("demo", minutes=5, external_id="wc_b")
    _, newest = seed.conversation("test_chat", minutes=9)
    assert ids(operator.get("/api/conversations").json()) == [newest, tie_b, tie_a, oldest]


def test_shared_phone_is_true_only_for_a_candidate_with_two_web_chat_identities(
    seed, operator, contract
):
    shared, first = seed.conversation("test_chat", minutes=1, phone="+525533333333")
    second = seed.second_identity(shared, minutes=2)
    _, alone = seed.conversation("test_chat", minutes=3, phone="+525544444444")
    # A WhatsApp identity does not make a web chat conversation shared.
    mixed, web = seed.conversation("demo", minutes=4, phone="+525555555555")
    seed.conversation(None, minutes=5, channel="whatsapp", external_id="5215555555555")
    seed.env.conn.execute(
        "INSERT INTO candidate_identities (candidate_id, channel, external_id, created_at)"
        " VALUES (?, 'whatsapp', 'wa_mixed', ?)",
        (mixed["id"], at(6).isoformat()),
    )

    body = contract.conforms(operator.get("/api/conversations"), "listConversations")
    flags = {item["conversation_id"]: item["shared_phone"] for item in body["items"]}
    assert flags == {first: True, second: True, alone: False, web: False}


def test_application_and_last_message_follow_the_latest_web_chat_message(seed, operator, contract):
    conn = seed.env.conn
    candidate, first = seed.conversation("test_chat", minutes=1, phone="+525566666666")
    second = seed.second_identity(candidate, minutes=30)
    one = repo.start_application(conn, candidate["id"], "webchat", now=at(2))
    seed.message(candidate, one["id"], "hola", minutes=3)
    repo.close_application(conn, one["id"], "withdrawn", actor="candidate", now=at(10))
    two = repo.start_application(conn, candidate["id"], "webchat", now=at(31))
    seed.message(candidate, two["id"], "otra vez", minutes=32)
    seed.message(candidate, two["id"], "claro", minutes=33, inbound=False)
    # A later message on another channel does not count.
    seed.message(candidate, two["id"], "wa", minutes=50, channel="whatsapp")

    body = contract.conforms(operator.get("/api/conversations"), "listConversations")
    assert ids(body) == [second, first]
    for item in body["items"]:
        assert item["application"] == {
            "id": two["id"],
            "stage": "greeting",
            "outcome": None,
            "status": "open",
        }
        assert item["last_message_at"] == at(33).isoformat().replace("+00:00", "Z")


def test_a_closed_application_shows_its_outcome(seed, operator, contract):
    conn = seed.env.conn
    candidate, conv = seed.conversation("demo", minutes=1, persona_id="ana")
    app = repo.start_application(conn, candidate["id"], "webchat", now=at(2))
    seed.message(candidate, app["id"], "hola", minutes=3)
    repo.close_application(conn, app["id"], "withdrawn", actor="candidate", now=at(4))
    item = contract.conforms(operator.get("/api/conversations"), "listConversations")["items"][0]
    assert item["conversation_id"] == conv
    assert item["application"] == {
        "id": app["id"],
        "stage": "greeting",
        "outcome": "withdrawn",
        "status": "closed",
    }


def test_a_conversation_without_a_message_has_no_application_and_no_last_message(
    seed, operator, contract
):
    candidate, conv = seed.conversation("test_chat", minutes=1)
    # An Application exists but no message was exchanged: still null (the rule is by message).
    repo.start_application(seed.env.conn, candidate["id"], "webchat", now=at(2))
    item = contract.conforms(operator.get("/api/conversations"), "listConversations")["items"][0]
    assert item["conversation_id"] == conv
    assert item["application"] is None
    assert item["last_message_at"] is None


def test_a_message_without_an_application_gives_a_null_application(seed, operator, contract):
    candidate, _ = seed.conversation("test_chat", minutes=1)
    seed.message(candidate, None, "hola", minutes=2)
    item = contract.conforms(operator.get("/api/conversations"), "listConversations")["items"][0]
    assert item["application"] is None
    assert item["last_message_at"] == at(2).isoformat().replace("+00:00", "Z")


def test_the_list_pages_with_a_cursor(seed, operator, contract):
    made = [seed.conversation("test_chat", minutes=m)[1] for m in range(1, 6)]
    expected = list(reversed(made))

    pages = []
    cursor = None
    for _ in range(3):
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = contract.conforms(
            operator.get("/api/conversations", params=params), "listConversations"
        )
        pages.append(ids(body))
        cursor = body["next_cursor"]
    assert pages == [expected[0:2], expected[2:4], expected[4:5]]
    assert cursor is None


def test_the_list_default_page_holds_every_row_up_to_the_limit(seed, operator):
    for minute in range(1, 4):
        seed.conversation("demo", minutes=minute)
    body = operator.get("/api/conversations", params={"limit": 3}).json()
    assert len(body["items"]) == 3 and body["next_cursor"] is None


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 201}, {"limit": "many"}, {"cursor": "not-a-cursor"}],
)
def test_bad_paging_is_a_validation_error(operator, contract, params):
    response = operator.get("/api/conversations", params=params)
    assert response.status_code == 422
    assert contract.conforms(response, "listConversations")["error"] == "validation_error"


# --------------------------------------------------------------------------- the read


def test_the_read_returns_the_item_and_the_web_chat_messages_oldest_first(seed, operator, contract):
    conn = seed.env.conn
    candidate, conv = seed.conversation("demo", minutes=1, persona_id="diego_es")
    app = repo.start_application(conn, candidate["id"], "webchat", now=at(2))
    first = seed.message(candidate, app["id"], "Hola", minutes=3)
    second = seed.message(candidate, app["id"], "Hola, soy el asistente", minutes=4, inbound=False)
    third = seed.message(candidate, app["id"], "Me llamo Luis", minutes=5)
    seed.message(candidate, app["id"], "no es de este chat", minutes=6, channel="whatsapp")

    response = operator.get(f"/api/conversations/{conv}")
    body = contract.conforms(response, "getConversation")
    assert [m["id"] for m in body["messages"]] == [first["id"], second["id"], third["id"]]
    assert [m["body"] for m in body["messages"]] == [
        "Hola",
        "Hola, soy el asistente",
        "Me llamo Luis",
    ]
    assert body["messages"][1]["sender"] == "agent"
    assert body["messages"][0]["modality"] == "typed"
    assert body["conversation_id"] == conv
    assert (body["client"], body["persona_id"]) == ("demo", "diego_es")
    assert body["application"]["id"] == app["id"]
    assert body["last_message_at"] == at(5).isoformat().replace("+00:00", "Z")

    listed = operator.get("/api/conversations").json()["items"][0]
    assert {k: v for k, v in body.items() if k != "messages"} == listed


def test_the_read_of_a_conversation_with_no_message(seed, operator, contract):
    _, conv = seed.conversation("test_chat", minutes=1)
    body = contract.conforms(operator.get(f"/api/conversations/{conv}"), "getConversation")
    assert body["messages"] == []
    assert body["application"] is None and body["last_message_at"] is None


def test_the_read_carries_buttons_and_sender_names(seed, operator, contract):
    conn = seed.env.conn
    candidate, conv = seed.conversation("test_chat", minutes=1)
    app = repo.start_application(conn, candidate["id"], "webchat", now=at(2))
    repo.add_message(
        conn,
        candidate_id=candidate["id"],
        application_id=app["id"],
        channel="webchat",
        direction="outbound",
        sender="agent",
        body="Elige",
        content_type="interactive",
        interactive={"style": "buttons", "options": [{"id": "yes", "label": "Sí"}]},
        now=at(3),
    )
    repo.add_message(
        conn,
        candidate_id=candidate["id"],
        application_id=app["id"],
        channel="webchat",
        direction="outbound",
        sender="human",
        sender_user_id="usr_rec_puebla",
        body="Soy Ana",
        now=at(4),
    )
    body = contract.conforms(operator.get(f"/api/conversations/{conv}"), "getConversation")
    buttons, human = body["messages"]
    assert buttons["interactive"]["options"] == [{"id": "yes", "label": "Sí"}]
    assert human["sender_name"]


def test_conversations_sharing_a_phone_share_their_messages(seed, operator):
    candidate, first = seed.conversation("test_chat", minutes=1, phone="+525577777777")
    second = seed.second_identity(candidate, minutes=2)
    seed.message(candidate, None, "una", minutes=3)
    for conv in (first, second):
        body = operator.get(f"/api/conversations/{conv}").json()
        assert body["shared_phone"] is True
        assert [m["body"] for m in body["messages"]] == ["una"]


def test_the_read_answers_404_for_conversations_that_are_not_listed(seed, operator, contract):
    _, simulator = seed.conversation("simulator", minutes=1)
    _, unrecorded = seed.conversation(None, minutes=2)
    seed.conversation("demo", minutes=3, channel="whatsapp", external_id="5215588888888")
    seed.conversation("simulator", minutes=4, channel="voice", external_id="wv_9")
    for conv in (simulator, unrecorded, "5215588888888", "wv_9", "wc_" + "0" * 32):
        response = operator.get(f"/api/conversations/{conv}")
        assert response.status_code == 404, conv
        assert contract.conforms(response, "getConversation")["error"] == "not_found"


# --------------------------------------------------------------------------- voice conversations


def voice(seed: Seed, client: str | None, *, minutes: float, **kwargs: Any):
    seed.count += 1
    return seed.conversation(
        client, minutes=minutes, channel="voice", external_id=f"wv_{seed.count:032x}", **kwargs
    )


def test_the_list_holds_voice_conversations_with_their_channel(seed, operator, contract):
    _, typed = seed.conversation("test_chat", minutes=1)
    _, spoken = voice(seed, "test_chat", minutes=2, phone="+525512345678")
    _, demo_spoken = voice(seed, "demo", minutes=3, persona_id="diego_es")
    voice(seed, "simulator", minutes=4)
    voice(seed, None, minutes=5)

    body = contract.conforms(operator.get("/api/conversations"), "listConversations")
    assert ids(body) == [demo_spoken, spoken, typed]
    assert [item["channel"] for item in body["items"]] == ["voice", "voice", "webchat"]
    assert spoken.startswith("wv_") and typed.startswith("wc_")
    assert body["items"][1]["phone"] == "+525512345678"
    assert body["items"][0]["persona_id"] == "diego_es"


def test_a_voice_conversation_shows_its_own_application_and_last_message(seed, operator, contract):
    conn = seed.env.conn
    candidate, conv = voice(seed, "test_chat", minutes=1, phone="+525512340000")
    seed.second_identity(candidate, minutes=2)  # a web chat identity of the same Candidate
    # An earlier, closed typed Application (one open at most, ADR 0021) with the latest message.
    typed_app = repo.start_application(conn, candidate["id"], "webchat", now=at(2))
    repo.close_application(conn, typed_app["id"], "withdrawn", actor="candidate", now=at(2.5))
    app = repo.start_application(conn, candidate["id"], "voice", now=at(3))
    seed.message(candidate, app["id"], "hola por voz", minutes=4, channel="voice")
    # A later typed message of the same Candidate is not part of the voice conversation, and
    # neither is the Application it belongs to.
    seed.message(candidate, typed_app["id"], "escrito", minutes=9)

    items = contract.conforms(operator.get("/api/conversations"), "listConversations")["items"]
    item = next(i for i in items if i["conversation_id"] == conv)
    assert item["channel"] == "voice"
    assert item["shared_phone"] is False
    assert item["application"] == {
        "id": app["id"],
        "stage": "greeting",
        "outcome": None,
        "status": "open",
    }
    assert item["last_message_at"] == at(4).isoformat().replace("+00:00", "Z")


def test_the_read_of_a_voice_conversation_returns_only_its_spoken_transcript(
    seed, operator, contract
):
    conn = seed.env.conn
    candidate, conv = voice(seed, "test_chat", minutes=1, phone="+525512341111")
    app = repo.start_application(conn, candidate["id"], "voice", now=at(2))
    disclosure, _ = repo.add_message(
        conn,
        candidate_id=candidate["id"],
        application_id=app["id"],
        channel="voice",
        direction="outbound",
        sender="agent",
        body="Esta llamada la atiende un asistente virtual.",
        modality="spoken",
        now=at(3),
    )
    utterance, _ = repo.add_message(
        conn,
        candidate_id=candidate["id"],
        application_id=app["id"],
        channel="voice",
        direction="inbound",
        sender="candidate",
        body="Me llamo Luis",
        modality="spoken",
        now=at(4),
    )
    seed.message(candidate, app["id"], "no es de esta llamada", minutes=5)

    body = contract.conforms(operator.get(f"/api/conversations/{conv}"), "getConversation")
    assert body["channel"] == "voice"
    assert [m["id"] for m in body["messages"]] == [disclosure["id"], utterance["id"]]
    assert [m["modality"] for m in body["messages"]] == ["spoken", "spoken"]
    assert body["last_message_at"] == at(4).isoformat().replace("+00:00", "Z")
    listed = next(
        i
        for i in operator.get("/api/conversations").json()["items"]
        if i["conversation_id"] == conv
    )
    assert {k: v for k, v in body.items() if k != "messages"} == listed


def test_voice_and_web_chat_conversations_of_one_phone_do_not_share_messages(seed, operator):
    candidate, typed = seed.conversation("test_chat", minutes=1, phone="+525512342222")
    spoken = seed.env.conn.execute(
        "INSERT INTO candidate_identities (candidate_id, channel, external_id, created_at, client)"
        " VALUES (?, 'voice', 'wv_shared', ?, 'test_chat')",
        (candidate["id"], at(2).isoformat()),
    )
    assert spoken.rowcount == 1
    seed.message(candidate, None, "escrito", minutes=3)
    seed.message(candidate, None, "hablado", minutes=4, channel="voice")

    typed_body = operator.get(f"/api/conversations/{typed}").json()
    spoken_body = operator.get("/api/conversations/wv_shared").json()
    assert [m["body"] for m in typed_body["messages"]] == ["escrito"]
    assert [m["body"] for m in spoken_body["messages"]] == ["hablado"]
    assert typed_body["shared_phone"] is False and spoken_body["shared_phone"] is False


def test_the_read_answers_404_for_an_unlisted_voice_conversation(seed, operator, contract):
    _, simulator = voice(seed, "simulator", minutes=1)
    _, unrecorded = voice(seed, None, minutes=2)
    for conv in (simulator, unrecorded):
        response = operator.get(f"/api/conversations/{conv}")
        assert response.status_code == 404
        assert contract.conforms(response, "getConversation")["error"] == "not_found"


# --------------------------------------------------------------------------- access and methods


def test_a_recruiter_gets_403_and_an_anonymous_caller_401_on_both_paths(env, seed, contract):
    _, conv = seed.conversation("demo", minutes=1)
    recruiter = env.client(PUEBLA)
    for path, operation in (
        ("/api/conversations", "listConversations"),
        (f"/api/conversations/{conv}", "getConversation"),
        ("/api/conversations/wc_unknown", "getConversation"),
    ):
        denied = recruiter.get(path)
        assert denied.status_code == 403, path
        assert contract.conforms(denied, operation)["error"] == "forbidden"
        anonymous = env.client().get(path)
        assert anonymous.status_code == 401, path
        assert contract.conforms(anonymous, operation)["error"] == "unauthorized"


def test_the_item_path_has_no_write_method(seed, operator):
    _, conv = seed.conversation("test_chat", minutes=1)
    path = f"/api/conversations/{conv}"
    for method in ("post", "put", "patch", "delete"):
        assert getattr(operator, method)(path).status_code == 405, method
    assert operator.get(path).status_code == 200
    for method in ("post", "put", "patch", "delete"):
        assert getattr(operator, method)("/api/conversations").status_code in (404, 405), method


def test_the_operator_session_is_the_only_credential(env, seed):
    _, conv = seed.conversation("test_chat", minutes=1)
    assert env.client(OPERATOR).get(f"/api/conversations/{conv}").status_code == 200


def test_the_app_never_logs_a_conversation_id(env, seed, caplog):
    _, listed = seed.conversation("demo", minutes=1, persona_id="diego_es")
    _, hidden = seed.conversation("simulator", minutes=2)
    operator = env.client(OPERATOR)
    recruiter = env.client(PUEBLA)
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    assert operator.get("/api/conversations").status_code == 200
    assert operator.get(f"/api/conversations/{listed}").status_code == 200
    assert operator.get(f"/api/conversations/{hidden}").status_code == 404
    assert recruiter.get(f"/api/conversations/{listed}").status_code == 403
    # The test client's own request log (httpx) and the event loop are not the app's.
    ours = [r for r in caplog.records if not r.name.startswith(("httpx", "httpcore", "asyncio"))]
    seen = "\n".join(f"{r.getMessage()} {r.args!r} {r.exc_text or ''}" for r in ours)
    assert listed not in seen and hidden not in seen
