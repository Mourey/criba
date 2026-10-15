"""Recruiters never receive cost fields; Operators do (ADR 0019, spec 003)."""

from __future__ import annotations

from criba.api.auth import User
from criba.api.routing import OPERATOR_ONLY_KEYS, new_router, strip_operator_fields

from .support import OPERATOR, PUEBLA


def keys_in(value, found=None):
    """Every dict key at any depth."""
    found = set() if found is None else found
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(k)
            keys_in(v, found)
    elif isinstance(value, list):
        for item in value:
            keys_in(item, found)
    return found


def test_the_filter_covers_every_operator_only_property_in_the_contract(contract):
    marked = set()

    def walk(node):
        if isinstance(node, dict):
            for name, prop in (node.get("properties") or {}).items():
                if isinstance(prop, dict) and prop.get("x-roles") == ["operator"]:
                    marked.add(name)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(contract.spec["components"]["schemas"])
    assert marked == {"cost"}
    assert marked <= OPERATOR_ONLY_KEYS
    # The fields inside `cost` are gone with it; the ledger names stay filtered anywhere else.
    assert {"est_cost_usd", "reconciled_cost_usd", "cost_usd"} <= OPERATOR_ONLY_KEYS


def test_operator_gets_cost_and_recruiter_does_not(env, build, contract):
    app = build.application()
    build.usage(application_id=app["id"], est_cost_usd=0.003, reconciled_cost_usd=0.004)
    build.usage(
        application_id=app["id"],
        est_cost_usd=0.001,
        input_tokens=500,
        kind="auxiliary",
        model="anthropic/claude-sonnet-5.5",
        latency_ms=None,
    )

    operator = contract.conforms(
        env.client(OPERATOR).get(f"/api/applications/{app['id']}"), "getApplication"
    )
    assert operator["cost"]["est_cost_usd"] == 0.004
    assert operator["cost"]["reconciled_cost_usd"] == 0.004
    assert operator["cost"]["runs"] == 1
    assert operator["cost"]["input_tokens"] == 1500
    assert operator["cost"]["models"] == [
        "anthropic/claude-haiku-5.5",
        "anthropic/claude-sonnet-5.5",
    ]

    recruiter = contract.conforms(
        env.client(PUEBLA).get(f"/api/applications/{app['id']}"), "getApplication"
    )
    assert "cost" not in recruiter
    assert not keys_in(recruiter) & OPERATOR_ONLY_KEYS
    assert "input_tokens" not in keys_in(recruiter)


def test_operator_cost_splits_tokens_by_the_model_that_served_them(env, build, contract):
    haiku, sonnet = "anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5"
    app = build.application()
    for _ in range(2):  # two Haiku turns, priced from tokens
        build.usage(
            application_id=app["id"],
            input_tokens=10_000,
            output_tokens=100,
            cache_read_tokens=9_800,
            cache_write_tokens=100,
            est_cost_usd=0.001,
        )
    build.usage(  # a Sonnet summary that OpenRouter billed
        application_id=app["id"],
        kind="auxiliary",
        model=sonnet,
        input_tokens=1_350,
        output_tokens=499,
        cache_read_tokens=0,
        est_cost_usd=0,
        reconciled_cost_usd=0.0077,
    )
    build.usage(  # a Sonnet call without a billed cost
        application_id=app["id"],
        kind="auxiliary",
        model=sonnet,
        input_tokens=500,
        output_tokens=50,
        cache_read_tokens=0,
        est_cost_usd=0,
    )

    cost = contract.conforms(
        env.client(OPERATOR).get(f"/api/applications/{app['id']}"), "getApplication"
    )["cost"]
    assert [(g["model"], g["reconciled_cost_usd"]) for g in cost["by_model"]] == [
        (haiku, None),
        (sonnet, 0.0077),
        (sonnet, None),
    ]
    turns, billed, unbilled = cost["by_model"]
    assert turns["calls"] == 2 and turns["input_tokens"] == 20_000
    assert (turns["cache_read_tokens"], turns["cache_write_tokens"]) == (19_600, 200)
    assert turns["output_tokens"] == 200 and turns["est_cost_usd"] == 0.002
    assert (unbilled["calls"], unbilled["input_tokens"]) == (1, 500)
    assert (billed["calls"], billed["input_tokens"], billed["output_tokens"]) == (1, 1_350, 499)
    # The split adds up to the block's own totals.
    assert sum(g["input_tokens"] for g in cost["by_model"]) == cost["input_tokens"]
    assert sum(g["output_tokens"] for g in cost["by_model"]) == cost["output_tokens"]


def test_the_cost_split_never_reaches_a_recruiter(env, build):
    app = build.application()
    build.usage(application_id=app["id"])
    recruiter = env.client(PUEBLA).get(f"/api/applications/{app['id']}").json()
    assert "cost" not in recruiter and "by_model" not in keys_in(recruiter)


def test_operator_cost_is_zero_not_missing_without_usage(env, build, contract):
    app = build.application()
    body = contract.conforms(
        env.client(OPERATOR).get(f"/api/applications/{app['id']}"), "getApplication"
    )
    assert body["cost"]["est_cost_usd"] == 0
    assert body["cost"]["reconciled_cost_usd"] is None
    assert body["cost"]["models"] == []


def test_no_recruiter_response_carries_cost_keys(env, build):
    app = build.application()
    build.queue_item(app["id"], "escalation", reason="wants_human")
    build.usage(application_id=app["id"])
    client = env.client(PUEBLA)
    for path in (
        "/api/applications",
        f"/api/applications/{app['id']}",
        f"/api/applications/{app['id']}/export",
        "/api/queue",
        "/api/metrics/results",
        "/api/slots",
        "/api/locations",
        "/api/auth/me",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert not keys_in(response.json()) & OPERATOR_ONLY_KEYS, path


def test_economics_and_comparison_are_operator_only(env, contract):
    recruiter = env.client(PUEBLA)
    for path, operation in (
        ("/api/metrics/economics", "getEconomics"),
        ("/api/metrics/comparison", "getModelComparison"),
    ):
        response = recruiter.get(path)
        body = contract.conforms(response, operation)
        assert response.status_code == 403
        assert body["error"] == "forbidden"
        assert env.client().get(path).status_code == 401


def test_the_route_class_strips_cost_even_when_a_handler_leaks_it(env):
    """Defence in depth: the filter sits on the route, not in each handler."""
    leaky = new_router()

    @leaky.get("/leak")
    def leak(user: User):
        return {"ok": 1, "cost": {"est_cost_usd": 1.0}, "nested": [{"reconciled_cost_usd": 2}]}

    env.app.include_router(leaky, prefix="/api")
    assert env.client(PUEBLA).get("/api/leak").json() == {"ok": 1, "nested": [{}]}
    assert "cost" in env.client(OPERATOR).get("/api/leak").json()


def test_strip_operator_fields_is_recursive_and_non_destructive():
    original = {"a": [{"cost": 1, "keep": 2}], "cost_usd": 3, "b": {"est_cost_usd": 4, "c": 5}}
    assert strip_operator_fields(original) == {"a": [{"keep": 2}], "b": {"c": 5}}
    assert "cost" in original["a"][0]
