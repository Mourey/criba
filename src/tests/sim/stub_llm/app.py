"""OpenAI-compatible stub model server (FastAPI).

Endpoints:
  POST /v1/chat/completions   streaming (SSE) and not, replies from the script
  GET  /v1/models            with `context_length`, so Hermes needs no other probe
  POST /api/show              Ollama-style model card (Hermes probes it for the context window
                              when /v1/models carries none); GET answers the same
  POST /api/alpha/decisions   the stub Jev (sim.stub_llm.jev): OpenRouter's decisions shape
  GET  /health
  GET  /__stub/requests       recorded requests (newest last); DELETE clears them
  GET  /__stub/script         name of the active script; PUT replaces it with a posted script
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from sim.stub_llm import jev as stub_jev
from sim.stub_llm.engine import Reply, respond_to
from sim.stub_llm.interpret import Interpreter, default_interpreter
from sim.stub_llm.script import (
    Script,
    ScriptError,
    ToolsContract,
    load_script,
    parse_script,
)

DEFAULT_SCRIPT = Path(__file__).resolve().parent / "scripts" / "screener.yaml"
DEFAULT_MODEL = "stub-model"


def _openai_error(status: int, message: str, type_: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": type_, "code": status}},
        status_code=status,
    )


def _sse(payload: dict[str, Any] | str) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n".encode()


def _chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _completion_body(reply: Reply, model: str, completion_id: str, created: int) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": reply.content}
    if reply.tool_calls:
        message["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": c.arguments},
            }
            for c in reply.tool_calls
        ]
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": reply.finish_reason}],
        "usage": reply.usage,
    }


async def _stream(
    reply: Reply,
    model: str,
    completion_id: str,
    created: int,
    chunk_chars: int,
    with_usage: bool,
) -> AsyncIterator[bytes]:
    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    yield _sse(chunk({"role": "assistant", "content": ""}))
    for piece in _chunks(reply.content or "", chunk_chars):
        yield _sse(chunk({"content": piece}))
    for index, call in enumerate(reply.tool_calls):
        yield _sse(
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": ""},
                        }
                    ]
                }
            )
        )
        for piece in _chunks(call.arguments, chunk_chars):
            yield _sse(chunk({"tool_calls": [{"index": index, "function": {"arguments": piece}}]}))
    yield _sse(chunk({}, reply.finish_reason))
    if with_usage:
        yield _sse(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": reply.usage,
            }
        )
    yield _sse("[DONE]")


class StubState:
    def __init__(
        self,
        script: Script,
        contract: ToolsContract | None,
        record: int,
        interpreter: Interpreter,
    ):
        self.script = script
        self.contract = contract
        self.interpreter = interpreter
        self.requests: deque[dict[str, Any]] = deque(maxlen=record or None)
        self.record = record > 0
        self.counter = 0


def create_app(
    script: Script | None = None,
    contract: ToolsContract | None = None,
    record: int | None = None,
    interpreter: Interpreter | None = None,
) -> FastAPI:
    """Build the server. Without arguments: the packaged screener script, contract discovered
    from STUB_LLM_TOOLS_JSON or contracts/tools.json, STUB_LLM_RECORD (default 200) requests
    kept for /__stub/requests, and the seed personas as the interpreter (spec 006b)."""
    if contract is None:
        contract = ToolsContract.discover()
    if script is None:
        script = load_script(os.environ.get("STUB_LLM_SCRIPT", DEFAULT_SCRIPT), contract)
    if record is None:
        record = int(os.environ.get("STUB_LLM_RECORD", "200"))
    state = StubState(script, contract, record, interpreter or default_interpreter())
    app = FastAPI(title="Criba stub model", version="0.1.0")
    app.state.stub = state

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "script": state.script.name}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": m,
                    "object": "model",
                    "created": 1_700_000_000,
                    "owned_by": "criba-stub",
                    "context_length": state.script.defaults.context_length,
                }
                for m in state.script.defaults.models
            ],
        }

    @app.api_route("/api/show", methods=["GET", "POST"])
    async def show(request: Request) -> dict[str, Any]:
        """Hermes's Ollama probe (agent/model_metadata.py `_ollama_show`): it reads
        `model_info.*context_length` (and `parameters` num_ctx) and `capabilities`. No
        "thinking" or "vision" capability, so Hermes sends neither reasoning nor images."""
        name = DEFAULT_MODEL
        if request.method == "POST":
            try:
                body = await request.json()
            except ValueError:
                body = {}
            if isinstance(body, dict):
                name = str(body.get("name") or body.get("model") or name)
        ctx = state.script.defaults.context_length
        return {
            "modelfile": "",
            "parameters": f"num_ctx {ctx}",
            "template": "",
            "details": {
                "format": "stub",
                "family": "criba-stub",
                "parameter_size": "0B",
            },
            "model_info": {"general.architecture": "stub", "stub.context_length": ctx},
            "capabilities": ["completion", "tools"],
            "model": name,
        }

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return _openai_error(400, "request body is not valid JSON")
        messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            return _openai_error(400, "'messages' must be a list of message objects")
        model = str(body.get("model") or DEFAULT_MODEL)
        reply = respond_to(state.script, messages, body.get("tools"), state.interpreter)
        state.counter += 1
        if state.record:
            state.requests.append(
                {
                    "n": state.counter,
                    "ts": time.time(),
                    "model": model,
                    "stream": bool(body.get("stream")),
                    "rule": reply.rule,
                    "body": body,
                }
            )
        if reply.delay_ms:
            await asyncio.sleep(reply.delay_ms / 1000)
        headers = {"x-stub-rule": reply.rule}
        if reply.error is not None:
            error = dict(reply.error)
            status = error.pop("status")
            return JSONResponse({"error": error}, status_code=status, headers=headers)
        completion_id = f"chatcmpl-stub-{state.counter:08d}"
        created = int(time.time())
        if body.get("stream"):
            include = bool((body.get("stream_options") or {}).get("include_usage"))
            with_usage = include or state.script.defaults.always_usage_in_stream
            return StreamingResponse(
                _stream(
                    reply,
                    model,
                    completion_id,
                    created,
                    state.script.defaults.chunk_chars,
                    with_usage,
                ),
                media_type="text/event-stream",
                headers={**headers, "cache-control": "no-cache"},
            )
        return JSONResponse(_completion_body(reply, model, completion_id, created), headers=headers)

    @app.post("/api/alpha/decisions", response_model=None)
    async def jev_decisions(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return _openai_error(400, "request body is not valid JSON")
        if not isinstance(body, dict) or not isinstance(body.get("questions"), dict):
            return _openai_error(400, "'questions' must be an object")
        return JSONResponse(stub_jev.decisions(body))

    @app.get("/__stub/requests")
    async def get_requests(limit: int = 50) -> dict[str, Any]:
        items = list(state.requests)[-limit:] if limit > 0 else []
        return {"count": len(state.requests), "total": state.counter, "requests": items}

    @app.delete("/__stub/requests")
    async def clear_requests() -> dict[str, Any]:
        state.requests.clear()
        return {"cleared": True}

    @app.get("/__stub/script")
    async def get_script() -> dict[str, Any]:
        return {"name": state.script.name, "rules": len(state.script.rules)}

    @app.put("/__stub/script", response_model=None)
    async def put_script(request: Request) -> Response:
        try:
            state.script = parse_script(await request.json(), state.contract)
        except (ScriptError, ValueError) as exc:
            return _openai_error(422, str(exc))
        return JSONResponse({"name": state.script.name, "rules": len(state.script.rules)})

    return app


def app_factory() -> FastAPI:
    """`uvicorn --factory sim.stub_llm.app:app_factory`."""
    return create_app()
