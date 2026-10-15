"""The router implements exactly the frozen contract, and speaks its error shape."""

from __future__ import annotations

from criba.api import present
from criba.db import repo
from criba.summary import FLAG_ORDER

from .support import OPERATOR, PUEBLA

SIM_PATHS = {"/api/sim/clock", "/api/sim/clock/advance", "/api/sim/clock/reset"}


def served_operations(env):
    schema = env.app.openapi()
    return {
        (path, method)
        for path, item in schema["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    }


def test_every_operation_in_the_contract_is_implemented(env, contract):
    served = served_operations(env)
    declared = {(url, method) for url, method, _ in contract.operations.values()}
    assert declared <= served, sorted(declared - served)


def test_nothing_is_served_that_the_contracts_do_not_declare(env, contract, chat_contract):
    served = served_operations(env)
    declared = {(url, method) for url, method, _ in contract.operations.values()}
    declared |= {(url, method) for url, method, _ in chat_contract.operations.values()}
    extra = {(p, m) for p, m in served if (p, m) not in declared}
    assert not extra, sorted(extra)


def test_the_sim_paths_are_the_ones_in_the_chat_contract(chat_contract):
    sim = {url for url, _, op in chat_contract.operations.values() if "/sim/" in url}
    assert sim == SIM_PATHS


def test_unknown_routes_are_plain_404s(env):
    assert env.client(PUEBLA).get("/api/nope").status_code == 404


def test_wrong_method_is_not_a_server_error(env):
    assert env.client(PUEBLA).put("/api/applications").status_code == 405


def test_errors_use_the_contract_shape_everywhere(env, build, contract):
    app = build.application()
    operator = env.client(OPERATOR)
    for response in (
        env.client().get("/api/applications"),
        operator.get("/api/applications/nope"),
        operator.get("/api/applications", params={"limit": "x"}),
        operator.post(f"/api/applications/{app['id']}/takeover/messages", json={"text": "hi"}),
        operator.post(
            "/api/auth/login", content="not json", headers={"content-type": "application/json"}
        ),
    ):
        assert response.status_code >= 400
        body = response.json()
        contract.validate("Error", body)
        assert set(body) <= {"error", "message", "details"}


def test_validation_errors_name_the_field(env, contract):
    response = env.client().post("/api/auth/login", json={"email": "a@b.c"})
    body = contract.conforms(response, "login")
    assert body["details"] == [{"field": "password", "message": "Field required"}]


def test_messages_carry_their_modality_read_only(env, build, contract):
    assert contract.spec["components"]["schemas"]["Message"]["properties"]["modality"]["readOnly"]
    app = build.application()
    common = {"candidate_id": app["candidate_id"], "application_id": app["id"]}
    typed, _ = repo.add_message(
        env.conn,
        **common,
        channel="webchat",
        direction="inbound",
        sender="candidate",
        body="Hola",
        client_message_id="m-typed",
    )
    spoken, _ = repo.add_message(
        env.conn,
        **common,
        channel="webchat",
        direction="outbound",
        sender="agent",
        body="Te escucho",
        modality="spoken",
    )
    client = env.client(PUEBLA)
    body = contract.conforms(client.get(f"/api/applications/{app['id']}"), "getApplication")
    modalities = {m["id"]: m["modality"] for m in body["messages"]}
    assert modalities == {typed["id"]: "typed", spoken["id"]: "spoken"}

    assert client.post(f"/api/applications/{app['id']}/takeover").status_code == 200
    stored = len(repo.list_messages(env.conn, app["id"]))
    refused = client.post(
        f"/api/applications/{app['id']}/takeover/messages",
        json={"text": "Hola", "modality": "spoken"},
    )
    assert refused.status_code == 422
    assert contract.conforms(refused, "sendTakeoverMessage")["error"] == "validation_error"
    assert len(repo.list_messages(env.conn, app["id"])) == stored


def test_flag_order_is_one_definition_matching_the_contract_enum(contract):
    assert present.FLAG_ORDER is FLAG_ORDER
    assert list(FLAG_ORDER) == contract.spec["components"]["schemas"]["Flag"]["enum"]
