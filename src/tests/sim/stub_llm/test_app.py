"""The HTTP surface: chat completions (plain and SSE), models, errors, admin, module entry point."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from sim.stub_llm.app import create_app
from sim.stub_llm.script import ToolsContract, parse_script

SIM_PARENT = Path(__file__).resolve().parents[2]  # src/tests

SCRIPT = parse_script(
    {
        "name": "http",
        "rules": [
            {
                "name": "call",
                "match": {"user_contains": ["tool"]},
                "respond": {
                    "tool_calls": [
                        {
                            "name": "record_answer",
                            "arguments": {"field": "zone", "value": "Centro, Puebla"},
                        },
                        {"name": "get_application"},
                    ]
                },
            },
            {
                "name": "fail",
                "match": {"user_contains": ["fail"]},
                "respond": {"error": {"status": 503, "message": "down"}},
            },
        ],
        "fallback": {"content": "Hola, soy un asistente virtual. ¿Cómo te llamas?"},
    },
    ToolsContract.discover(),
)
BODY = {
    "model": "anthropic/claude-haiku-5.5",
    "messages": [{"role": "user", "content": "hola"}],
}


@pytest.fixture
def client():
    with TestClient(create_app(SCRIPT, record=10)) as c:
        yield c


def sse_events(response):
    events = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            events.append(line[len("data: ") :])
    return events


def test_models_listing(client):
    body = client.get("/v1/models").json()
    assert body["object"] == "list"
    assert {m["id"] for m in body["data"]} >= {"stub-model"}
    assert all(m["object"] == "model" for m in body["data"])


def test_models_carry_a_context_window_so_hermes_needs_no_other_probe(client):
    """Hermes reads `context_length` from /v1/models (agent/model_metadata.py
    `_resolve_endpoint_context_length`); without it, it POSTs an Ollama /api/show probe."""
    body = client.get("/v1/models").json()
    assert all(m["context_length"] == 200_000 for m in body["data"])


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_api_show_answers_the_ollama_probe(client, method):
    kwargs = {"json": {"name": "stub-model"}} if method == "POST" else {}
    r = client.request(method, "/api/show", **kwargs)
    assert r.status_code == 200
    body = r.json()
    # What Hermes reads (`_ollama_show_context`, `query_ollama_supports_vision`, reasoning).
    assert body["model_info"]["stub.context_length"] == 200_000
    assert "num_ctx 200000" in body["parameters"]
    assert "thinking" not in body["capabilities"]
    assert "vision" not in body["capabilities"]
    assert client.post("/api/show", content=b"not json").status_code == 200


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "script": "http"}


def test_non_streaming_text_reply_has_openai_shape_and_usage(client):
    r = client.post("/v1/chat/completions", json=BODY)
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == BODY["model"]
    choice = body["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["role"] == "assistant"
    assert "asistente virtual" in choice["message"]["content"]
    usage = body["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    assert usage["prompt_tokens"] > 0 and usage["completion_tokens"] > 0
    assert "cached_tokens" in usage["prompt_tokens_details"]
    assert r.headers["x-stub-rule"] == "fallback"


def test_non_streaming_tool_calls_in_openai_format(client):
    r = client.post(
        "/v1/chat/completions",
        json={**BODY, "messages": [{"role": "user", "content": "use a tool"}]},
    )
    choice = r.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    calls = choice["message"]["tool_calls"]
    assert [c["function"]["name"] for c in calls] == [
        "record_answer",
        "get_application",
    ]
    assert all(c["type"] == "function" and c["id"].startswith("call_") for c in calls)
    assert json.loads(calls[0]["function"]["arguments"]) == {
        "field": "zone",
        "value": "Centro, Puebla",
    }
    assert choice["message"]["content"] is None


def test_streaming_text_reassembles_and_ends_with_usage_then_done(client):
    with client.stream("POST", "/v1/chat/completions", json={**BODY, "stream": True}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = sse_events(r)
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    assert text == "Hola, soy un asistente virtual. ¿Cómo te llamas?"
    assert len([c for c in chunks if c["choices"] and c["choices"][0]["delta"].get("content")]) > 1
    finish = [c for c in chunks if c["choices"] and c["choices"][0]["finish_reason"]]
    assert [c["choices"][0]["finish_reason"] for c in finish] == ["stop"]
    assert chunks[-1]["choices"] == []
    assert chunks[-1]["usage"]["total_tokens"] > 0


def test_streaming_tool_calls_reassemble_by_index(client):
    body = {
        **BODY,
        "stream": True,
        "messages": [{"role": "user", "content": "tool please"}],
    }
    with client.stream("POST", "/v1/chat/completions", json=body) as r:
        chunks = [json.loads(e) for e in sse_events(r) if e != "[DONE]"]
    calls: dict[int, dict] = {}
    for c in chunks:
        if not c["choices"]:
            continue
        for tc in c["choices"][0]["delta"].get("tool_calls", []):
            slot = calls.setdefault(tc["index"], {"id": None, "name": None, "args": ""})
            slot["id"] = tc.get("id", slot["id"])
            fn = tc.get("function", {})
            slot["name"] = fn.get("name") or slot["name"]
            slot["args"] += fn.get("arguments", "")
    assert [calls[i]["name"] for i in sorted(calls)] == [
        "record_answer",
        "get_application",
    ]
    assert json.loads(calls[0]["args"]) == {"field": "zone", "value": "Centro, Puebla"}
    assert calls[1]["args"] == "{}"
    assert [
        c["choices"][0]["finish_reason"]
        for c in chunks
        if c["choices"] and c["choices"][0]["finish_reason"]
    ] == ["tool_calls"]


def test_usage_chunk_can_be_limited_to_include_usage_requests():
    script = parse_script(
        {"defaults": {"always_usage_in_stream": False}, "fallback": {"content": "hola"}}
    )
    with TestClient(create_app(script, record=0)) as c:
        with c.stream("POST", "/v1/chat/completions", json={**BODY, "stream": True}) as r:
            plain = [json.loads(e) for e in sse_events(r) if e != "[DONE]"]
        with c.stream(
            "POST",
            "/v1/chat/completions",
            json={**BODY, "stream": True, "stream_options": {"include_usage": True}},
        ) as r:
            with_usage = [json.loads(e) for e in sse_events(r) if e != "[DONE]"]
    assert not any("usage" in chunk for chunk in plain)
    assert "usage" in with_usage[-1]


def test_scripted_error_is_an_openai_error_body(client):
    r = client.post(
        "/v1/chat/completions",
        json={**BODY, "messages": [{"role": "user", "content": "fail"}]},
    )
    assert r.status_code == 503
    assert r.json()["error"]["message"] == "down"


@pytest.mark.parametrize("payload", [{"model": "m"}, {"messages": "nope"}, {"messages": ["x"]}])
def test_bad_requests_get_a_400(client, payload):
    r = client.post("/v1/chat/completions", json=payload)
    assert r.status_code == 400
    assert "error" in r.json()


def test_non_json_body_gets_a_400(client):
    r = client.post(
        "/v1/chat/completions",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400


def test_requests_are_recorded_and_can_be_cleared(client):
    client.post("/v1/chat/completions", json=BODY)
    client.post("/v1/chat/completions", json=BODY)
    listing = client.get("/__stub/requests", params={"limit": 1}).json()
    assert listing["total"] == 2 and len(listing["requests"]) == 1
    assert listing["requests"][0]["body"]["messages"] == BODY["messages"]
    assert listing["requests"][0]["rule"] == "fallback"
    client.delete("/__stub/requests")
    assert client.get("/__stub/requests").json()["count"] == 0


def test_script_can_be_replaced_at_runtime_and_is_validated(client):
    ok = client.put("/__stub/script", json={"name": "swapped", "fallback": {"content": "nuevo"}})
    assert ok.status_code == 200
    assert (
        client.post("/v1/chat/completions", json=BODY).json()["choices"][0]["message"]["content"]
        == "nuevo"
    )
    bad = client.put(
        "/__stub/script",
        json={"rules": [{"respond": {"tool_calls": [{"name": "nope"}]}}]},
    )
    assert bad.status_code == 422
    assert client.get("/__stub/script").json()["name"] == "swapped"


def test_default_app_loads_the_packaged_screener_script():
    with TestClient(create_app()) as c:
        assert c.get("/health").json()["script"] == "screener"
        first = c.post("/v1/chat/completions", json=BODY).json()["choices"][0]["message"]
        assert first["tool_calls"][0]["function"]["name"] == "get_application"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_runnable_as_a_module():
    """`python -m sim.stub_llm` serves /v1 over real HTTP (the container entry point)."""
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(SIM_PARENT), "STUB_LLM_PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "sim.stub_llm", "--host", "127.0.0.1"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 20
        while True:
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            assert proc.poll() is None, "stub server exited early"
            assert time.time() < deadline, "stub server did not start"
            time.sleep(0.1)
        assert httpx.get(f"{base}/v1/models").json()["data"]
        r = httpx.post(f"{base}/v1/chat/completions", json=BODY)
        assert (
            r.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"]
            == "get_application"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_check_flag_validates_the_script_without_serving():
    env = {**os.environ, "PYTHONPATH": str(SIM_PARENT)}
    done = subprocess.run(
        [sys.executable, "-m", "sim.stub_llm", "--check"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "screener" in done.stdout
