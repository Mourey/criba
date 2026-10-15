"""A small in-process stand-in for the web chat and the Operator API, for the live module's tests.

Not a model of the product: it stores what a test puts in it and answers the handful of calls
`live.client.LiveStack` makes. A test seeds `applications` and `queue_pages`, and hooks
`on_message` / `on_reply` / `on_approve` to make the "agent" speak.

    api = FakeApi()
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://fake")
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse


def outbound(text: str, **extra: Any) -> dict[str, Any]:
    """A chat message from the agent, in the shape of `contracts/webchat.openapi.yaml`."""
    return {
        "direction": "outbound",
        "sender": "agent",
        "sender_label": "Sazón",
        "text": text,
        "interactive": None,
        "reply_to": None,
        "links": [],
        "attachments": [],
        "created_at": "2026-10-10T10:00:00Z",
        **extra,
    }


HAIKU = "anthropic/claude-haiku-5.5"
SONNET = "anthropic/claude-sonnet-5.5"


def cost_block(*, haiku_in: int = 10_000, summary: bool = True) -> dict[str, Any]:
    """An Operator `cost` block: Hermes turns on Haiku, the summary on Sonnet with its charge."""
    by_model: list[dict[str, Any]] = [
        {
            "model": HAIKU,
            "runs": 3,
            "input_tokens": haiku_in,
            "output_tokens": 500,
            "cache_read_tokens": 6_000,
            "cache_write_tokens": 0,
            "est_cost_usd": 0.001,
        }
    ]
    if summary:
        by_model.append(
            {
                "model": SONNET,
                "runs": 1,
                "input_tokens": 3_000,
                "output_tokens": 300,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "est_cost_usd": 0.009,
                "reconciled_cost_usd": 0.0085,
            }
        )
    return {
        "est_cost_usd": 0.01,
        "runs": 4 if summary else 3,
        "input_tokens": haiku_in + (3_000 if summary else 0),
        "output_tokens": 500 + (300 if summary else 0),
        "cache_read_tokens": 6_000,
        "cache_write_tokens": 0,
        "p50_latency_ms": 3000,
        "models": [HAIKU, SONNET] if summary else [HAIKU],
        "by_model": by_model,
    }


def make_application(
    app_id: str = "app_1",
    phone: str = "+34600000001",
    *,
    stage: str = "qualified",
    status: str = "open",
    prose: str | None = "A short summary of the Candidate.",
    events: Sequence[str | tuple[str, dict[str, Any]]] = (
        "application_started",
        "answer_recorded",
        "screening_finished",
    ),
    messages: Sequence[dict[str, Any]] = (),
    outcome: str | None = None,
    knockout_criterion: str | None = None,
    answers: dict[str, Any] | None = None,
    opted_out: bool = False,
    interview: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """An ApplicationDetail with the fields the live flows read."""
    return {
        "id": app_id,
        "candidate": {
            "id": "cand_1",
            "full_name": "Test",
            "phone": phone,
            "country": "ES",
            "language": "en",
            "opted_out": opted_out,
        },
        "stage": stage,
        "status": status,
        "outcome": outcome,
        "knockout_criterion": knockout_criterion,
        "answers": answers or {},
        "events": [
            {
                "id": i + 1,
                "kind": e if isinstance(e, str) else e[0],
                "actor": "agent",
                "created_at": "x",
                "data": {} if isinstance(e, str) else e[1],
            }
            for i, e in enumerate(events)
        ],
        "messages": list(messages),
        "summary": {"structured": {}, "prose": prose, "written_at": "x"} if prose else None,
        "interview": interview,
        "open_queue_items": [],
        "cost": cost if cost is not None else cost_block(),
    }


class FakeApi:
    def __init__(self, *, sim: bool = False, password: str = "pw") -> None:
        self.sim = sim
        self.password = password
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.phones: dict[str, str] = {}  # the phone each conversation was started with
        self.applications_by_conv: dict[str, dict[str, Any]] = {}  # for hooks that create them
        self.finished: list[bool] = []  # run_finished for successive reads; True when empty
        self.applications: dict[str, dict[str, Any]] = {}  # by id
        self.queue_pages: list[list[dict[str, Any]]] = [[]]
        self.approvals: list[str] = []
        self.approve_status = 200
        self.reads = 0
        self.application_reads = 0
        self.summary_after_reads = 0  # the summary prose lands on this many reads
        self.on_message: Callable[[FakeApi, str, str], None] = lambda api, cid, text: None
        self.on_reply: Callable[[FakeApi, str, int, str], None] = lambda api, cid, mid, opt: None
        self.on_approve: Callable[[FakeApi, str], None] = lambda api, item_id: None
        self.files: dict[str, tuple[str, str]] = {}  # url -> (content type, body)
        self.booking_page_status = 200  # what /api/public/booking/{token} answers
        self.app = self._build()

    # ---- helpers for tests -----------------------------------------------------------

    def push(self, cid: str, message: dict[str, Any], *, inbound: bool = False) -> dict[str, Any]:
        thread = self.messages.setdefault(cid, [])
        full = {
            "id": len(thread) + 1,
            "message_id": None,
            **(message | ({"direction": "inbound", "sender": "candidate"} if inbound else {})),
        }
        thread.append(full)
        return full

    def say(self, cid: str, text: str, **extra: Any) -> dict[str, Any]:
        return self.push(cid, outbound(text, **extra))

    # ---- the app ---------------------------------------------------------------------

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/api/sim/clock")
        async def clock():
            if not self.sim:
                return JSONResponse({"error": "not_found"}, status_code=404)
            return {"now": "2026-10-10T10:00:00Z", "offset_seconds": 0, "due_jobs_run": 0}

        @app.post("/api/auth/login")
        async def login(payload: dict[str, Any]):
            if payload.get("password") != self.password:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return {"id": "usr_op"}

        @app.post("/api/chat/conversations", status_code=201)
        async def start(payload: dict[str, Any]):
            cid = f"conv_{len(self.messages) + 1}"
            self.messages[cid] = []
            self.phones[cid] = payload.get("phone", "")
            return {"conversation_id": cid, "cursor": 0}

        @app.post("/api/chat/conversations/{cid}/messages", status_code=202)
        async def send(cid: str, payload: dict[str, Any]):
            message = self.push(cid, {"text": payload["text"]}, inbound=True)
            self.on_message(self, cid, payload["text"])
            return {"id": message["id"]}

        @app.post("/api/chat/conversations/{cid}/replies", status_code=202)
        async def reply(cid: str, payload: dict[str, Any]):
            message = self.push(
                cid,
                {"text": payload["option_id"], "reply_to": payload},
                inbound=True,
            )
            self.on_reply(self, cid, payload["in_reply_to"], payload["option_id"])
            return {"id": message["id"]}

        @app.get("/api/chat/conversations/{cid}/messages")
        async def read(cid: str, after: int = 0, wait: int = 0, limit: int = 100):
            self.reads += 1
            thread = self.messages.get(cid, [])
            fresh = [m for m in thread if m["id"] > after][:limit]
            finished = self.finished.pop(0) if self.finished else True
            return {
                "cursor": max([after, *[m["id"] for m in fresh]]),
                "messages": fresh,
                "agent_typing": not finished,
                "run_finished": finished,
            }

        @app.get("/api/applications")
        async def listing(q: str = "", limit: int = 20):
            items = [
                a for a in self.applications.values() if q in ((a["candidate"].get("phone")) or "")
            ]
            return {"items": items[:limit], "next_cursor": None, "total": len(items)}

        @app.get("/api/applications/{application_id}")
        async def detail(application_id: str):
            found = self.applications.get(application_id)
            if found is None:
                return JSONResponse({"error": "not_found"}, status_code=404)
            self.application_reads += 1
            body = dict(found)
            if self.application_reads <= self.summary_after_reads and body.get("summary"):
                body["summary"] = {**body["summary"], "prose": None}
            return body

        @app.get("/api/queue")
        async def queue(kind: str = "", limit: int = 50, cursor: str | None = Query(None)):
            index = int(cursor or 0)
            page = self.queue_pages[index]
            more = index + 1 < len(self.queue_pages)
            return {"items": page, "next_cursor": str(index + 1) if more else None}

        @app.post("/api/queue/approvals/{item_id}")
        async def approve(item_id: str, payload: dict[str, Any]):
            self.approvals.append(item_id)
            if self.approve_status != 200:
                return JSONResponse({"error": "conflict"}, status_code=self.approve_status)
            self.on_approve(self, item_id)
            return {"id": item_id, "status": "resolved"}

        @app.get("/api/public/booking/{token}")
        async def booking_page(token: str):
            if self.booking_page_status != 200:
                return JSONResponse({"error": "link_expired"}, status_code=self.booking_page_status)
            return {"slots": [{"slot_id": "slot_1"}]}

        @app.get("/files/{name}")
        async def download(name: str):
            content_type, body = self.files.get(name, ("text/plain", "missing"))
            return PlainTextResponse(body, media_type=content_type)

        return app


# ---- the voice flow (ADR 0059) -------------------------------------------------------------

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"
COMPLETIONS_PATH = "/api/voice/v1/chat/completions"
VOICE_EXPIRES_AT = "2026-10-10T10:30:00Z"


@cache
def _components(contract: str) -> dict[str, Any]:
    document = yaml.safe_load((CONTRACTS_DIR / contract).read_text(encoding="utf-8"))
    return dict(document["components"])


def contract_problem(contract: str, schema: str, value: Any) -> str | None:
    """Why `value` is not a `schema` of the contract file `contract`, or None when it is."""
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": _components(contract)}
    )
    errors = sorted(validator.iter_errors(value), key=lambda e: [str(p) for p in e.path])
    return errors[0].message if errors else None


# One scripted reply: the text; the exact deltas to stream; or a dict with `text` (or `deltas`)
# and the extra fields of the stored message (`interactive`, `attachments`).
Spoken = str | list[str] | dict[str, Any]


def _next_reply(api: FakeVoiceApi, cid: str, messages: list[dict[str, Any]]) -> Spoken:
    return api.replies.pop(0) if api.replies else "Gracias."


class FakeVoiceApi(FakeApi):
    """`FakeApi` with the voice flow: voice conversations, `voice-session` on them only, and the
    custom-LLM endpoint ElevenLabs calls.

    The new routes sit ahead of the base routes in this subclass only; for a web chat
    conversation they hand the request on to the base fake's own handler, so the base class and
    its tests are unchanged. Every body the fake accepts is checked against the contracts.

        api = FakeVoiceApi(llm_secret="...")
        api.replies.append("Gracias. ¿Cómo te llamas?")     # one scripted reply per turn
        api.replies.append(["split ht", "tps://x.example"])  # or the exact deltas to stream
    """

    def __init__(
        self,
        *,
        sim: bool = False,
        password: str = "pw",
        llm_secret: str = "llm-secret-for-tests",
        replies: Sequence[Spoken] = (),
    ) -> None:
        self.llm_secret = llm_secret
        self.replies: list[Spoken] = list(replies)
        self.reply_for: Callable[[FakeVoiceApi, str, list[dict[str, Any]]], Spoken] = _next_reply
        self.channels: dict[str, str] = {}  # conversation id -> channel, for voice ones
        self.countries: dict[str, str] = {}
        self.sessions: list[str] = []  # the conversation of each voice session started
        self.voice_tokens: dict[str, str] = {}  # minted token -> conversation id
        self.session_status = 201  # 503 makes voice-session answer `voice_unavailable`
        self.completion_requests = 0
        self.completion_bodies: list[dict[str, Any]] = []
        super().__init__(sim=sim, password=password)

    def is_voice(self, cid: str) -> bool:
        return self.channels.get(cid) == "voice"

    def voice_tokens_by_conversation(self, cid: str) -> str:
        """The voice token minted for `cid`."""
        return next(token for token, owner in self.voice_tokens.items() if owner == cid)

    def _build(self) -> FastAPI:
        from fastapi import APIRouter, Header
        from fastapi.responses import StreamingResponse
        from fastapi.routing import APIRoute

        app = super()._build()
        base = {
            (route.path, method): route.endpoint
            for route in app.router.routes
            if isinstance(route, APIRoute)
            for method in route.methods or ()
        }
        router = APIRouter()
        chat = "/api/chat/conversations"

        def not_found() -> JSONResponse:
            return JSONResponse(
                {"error": "not_found", "message": "Conversation not found."}, status_code=404
            )

        @router.post(chat, status_code=201)
        async def start_conversation(payload: dict[str, Any]):
            problem = contract_problem("webchat.openapi.yaml", "StartConversationRequest", payload)
            if problem:
                return JSONResponse(
                    {"error": "validation_error", "message": problem}, status_code=422
                )
            if payload.get("channel", "webchat") != "voice":
                return await base[(chat, "POST")](payload)
            cid = f"wv_{len(self.messages) + 1}"
            self.messages[cid] = []
            self.phones[cid] = payload.get("phone") or ""
            self.channels[cid] = "voice"
            self.countries[cid] = payload["country"]
            return {"conversation_id": cid, "cursor": 0, "channel": "voice"}

        @router.post(chat + "/{cid}/messages", status_code=202)
        async def send_typed(cid: str, payload: dict[str, Any]):
            if self.is_voice(cid):
                return not_found()  # a voice conversation takes no typed message
            return await base[(chat + "/{cid}/messages", "POST")](cid, payload)

        @router.post(chat + "/{cid}/replies", status_code=202)
        async def tap(cid: str, payload: dict[str, Any]):
            if self.is_voice(cid):
                return not_found()  # nor a tap
            return await base[(chat + "/{cid}/replies", "POST")](cid, payload)

        @router.post(chat + "/{cid}/voice-session", status_code=201)
        async def voice_session(cid: str):
            if self.session_status != 201:
                return JSONResponse(
                    {"error": "voice_unavailable", "message": "La llamada no está disponible."},
                    status_code=self.session_status,
                )
            if cid not in self.messages:
                return not_found()
            if not self.is_voice(cid):
                return JSONResponse(
                    {"error": "voice_refused", "message": "No podemos empezar la llamada ahora."},
                    status_code=409,
                )
            number = len(self.sessions) + 1
            token = f"voice-token-{number}-for-{cid}"
            self.voice_tokens[token] = cid
            self.sessions.append(cid)
            return {
                "conversation_token": f"conversation-token-{number}-for-{cid}",
                "voice_token": token,
                "expires_at": VOICE_EXPIRES_AT,
                "country": self.countries.get(cid, "ES"),
            }

        @router.post(COMPLETIONS_PATH)
        async def completions(payload: dict[str, Any], authorization: str | None = Header(None)):
            if authorization != f"Bearer {self.llm_secret}":
                return JSONResponse(
                    {"error": "unauthorized", "message": "Invalid credentials."}, status_code=401
                )
            problem = contract_problem("voice.openapi.yaml", "VoiceChatCompletionRequest", payload)
            if problem:
                return JSONResponse(
                    {"error": "validation_error", "message": problem}, status_code=422
                )
            cid = self.voice_tokens.get(payload["elevenlabs_extra_body"]["criba_voice_token"])
            if cid is None:
                return JSONResponse(
                    {"error": "unauthorized", "message": "Invalid credentials."}, status_code=401
                )
            self.completion_requests += 1
            self.completion_bodies.append(payload)
            spoken = [m for m in payload["messages"] if m["role"] == "user"][-1]["content"]
            self.push(cid, {"text": spoken}, inbound=True)
            reply = self.reply_for(self, cid, payload["messages"])
            extra: dict[str, Any] = {}
            if isinstance(reply, dict):
                extra = {k: v for k, v in reply.items() if k not in ("text", "deltas")}
                reply = reply.get("deltas") or reply["text"]
            deltas = reply if isinstance(reply, list) else re.findall(r"\S+\s*", reply)
            self.say(cid, "".join(deltas), **extra)

            def frame(delta: dict[str, Any], finish: str | None = None) -> str:
                body = {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "criba-voice",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

            async def stream():
                yield frame({"role": "assistant", "content": ""})
                for delta in deltas:
                    yield frame({"content": delta})
                yield frame({}, "stop")
                yield "data: [DONE]\n\n"

            return StreamingResponse(stream(), media_type="text/event-stream")

        app.router.routes[0:0] = router.routes
        return app
