"""The client side of the live smoke run: the existing driver, plus the few calls it does not make.

`LiveStack` holds two HTTP clients against one base URL: `chat` (anonymous, the Candidate's web
chat) and `operator` (logged in as the seeded Operator, the REST API). Conversations are played
by `sim.driver.PersonaDriver` as it is; this module adds only what a flow needs beyond that:
reading a turn the driver did not start (the Approval's slot offer, the booking confirmation),
tapping a slot, resolving an Approval, listing Applications and queue items, and charging a
conversation's usage to the spend guard (`sim.spend`).

The stack under test is the real one: no stub model and no sim clock. `FrozenClock` is the
driver's clock here, and it refuses to move time.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from sim import asserts
from sim.driver import PersonaDriver, RunResult, WebChatClient
from sim.load.runner import FrozenClock
from sim.personas.schema import Persona
from sim.spend import TOKEN_KEYS, SpendGuard, reported_cost
from sim.stack import new_phone, usage_of

DEFAULT_MODEL = "anthropic/claude-haiku-5.5"


@dataclass
class Charge:
    """What one conversation cost, as the usage ledger records it.

    `cost_usd` is the report figure (`sim.spend.reported_cost`): OpenRouter's own charge where a
    usage row carries one (the Sonnet summary), else the row's tokens at list prices (Hermes
    runs carry no cost). `billed_usd` is only the OpenRouter-reported part. `guard_usd` is what
    the spend guard counted against the cap (an upper bound by design).
    """

    cost_usd: float = 0.0
    billed_usd: float = 0.0
    guard_usd: float = 0.0
    models: list[str] = field(default_factory=list)
    runs: int = 0
    tokens: dict[str, int] = field(default_factory=dict)


@dataclass
class Turn:
    """The agent's answer to something the flow sent: messages, and the time they took."""

    messages: list[dict[str, Any]]
    first_s: float | None
    last_s: float | None
    cursor: int
    finished: bool

    def attachments(self, kind: str) -> list[dict[str, Any]]:
        return [a for m in self.messages for a in m.get("attachments") or [] if a["type"] == kind]

    def interactive(self) -> dict[str, Any] | None:
        """The first message that carries buttons or a list."""
        return next((m for m in self.messages if m.get("interactive")), None)


def summary_prose(app: dict[str, Any]) -> str:
    return str(((app.get("summary") or {}).get("prose")) or "").strip()


class LiveStack:
    """The real stack as a flow sees it."""

    def __init__(
        self,
        chat: httpx.AsyncClient,
        operator: httpx.AsyncClient,
        guard: SpendGuard,
        *,
        model: str = DEFAULT_MODEL,
        reply_timeout_s: float = 90.0,
        poll_wait_s: float = 5.0,
    ) -> None:
        self.chat = chat
        self.operator = operator
        self.guard = guard
        self.model = model
        self.reply_timeout_s = reply_timeout_s
        self.poll_wait_s = poll_wait_s
        self.client = WebChatClient(chat)

    # ---- the Candidate ----------------------------------------------------------------

    async def play(self, persona: Persona, *, max_turns: int = 60) -> tuple[RunResult, str]:
        """Play `persona` to completion with the existing driver, on a fresh phone number.

        ``max_turns`` bounds the Candidate's messages: a flow that expects a short conversation
        lowers it, so an agent that loops costs a handful of turns, not sixty."""
        phone = new_phone(persona.identity.country)
        driver = PersonaDriver(
            self.chat,
            clock=FrozenClock(),
            reply_timeout_s=self.reply_timeout_s,
            poll_wait_s=self.poll_wait_s,
            max_turns=max_turns,
        )
        return await driver.run(persona, phone=phone, align_clock=False), phone

    async def start(self, country: str, locale: str, phone: str) -> tuple[str, int]:
        """Open a conversation; returns its id and read cursor."""
        conv = await self.client.start(country, phone=phone, locale=locale, client="simulator")
        return conv["conversation_id"], int(conv.get("cursor", 0))

    async def say(self, conversation_id: str, text: str, after: int) -> Turn:
        """Send a text message and wait for the agent's turn to finish."""
        started = time.perf_counter()
        await self.client.send(conversation_id, text)
        return await self.read_turn(conversation_id, after, started=started)

    async def tap(self, conversation_id: str, offer_id: int, option_id: str) -> Turn:
        """Tap one option of the interactive message `offer_id` and wait for the turn."""
        started = time.perf_counter()
        accepted = await self.client.reply(conversation_id, offer_id, option_id)
        return await self.read_turn(
            conversation_id, int(accepted.get("id") or offer_id), started=started
        )

    async def read_turn(
        self,
        conversation_id: str,
        after: int,
        *,
        started: float | None = None,
        timeout_s: float | None = None,
    ) -> Turn:
        """Outbound messages after `after`, once the agent's run has finished.

        `first_s` is the time from `started` (default: now) to the first agent message, measured
        as the driver measures it; `last_s` to the last one of the burst.
        """
        started = time.perf_counter() if started is None else started
        deadline = started + (timeout_s or self.reply_timeout_s)
        messages: list[dict[str, Any]] = []
        cursor, first, last, finished = after, None, None, False
        seen: tuple[bool, bool] | None = None
        while time.perf_counter() < deadline:
            remaining = deadline - time.perf_counter()
            page = await self.client.read(
                conversation_id,
                cursor,
                wait=max(0.0, min(self.poll_wait_s, remaining)),
                seen=seen,
            )
            cursor = max(cursor, int(page["cursor"]))
            fresh = [m for m in page["messages"] if m["direction"] == "outbound"]
            if fresh:
                now = time.perf_counter() - started
                first = now if first is None else first
                last = now
                messages += fresh
            finished = bool(page.get("run_finished"))
            if messages and finished:
                break
            if page.get("run_finished") is not None:
                seen = (bool(page.get("agent_typing")), finished)
        return Turn(messages, first, last, cursor, finished)

    async def download(self, url: str) -> httpx.Response:
        return await self.chat.get(url)

    # ---- the Operator API -------------------------------------------------------------

    async def application(
        self, phone: str, *, with_summary: bool = False, timeout_s: float = 90.0
    ) -> dict[str, Any]:
        """The newest Application for `phone`, with the Operator's cost block.

        With `with_summary` waits (up to `timeout_s`) for the summary's prose to land: it is
        written after the last turn, by a separate model call.
        """
        deadline = time.monotonic() + timeout_s
        app = await asserts.find_application(self.operator, phone, include_cost=True)
        while with_summary and not summary_prose(app) and time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            app = await asserts.find_application(self.operator, phone, include_cost=True)
        return app

    async def applications_for(self, phone: str) -> list[dict[str, Any]]:
        """Every Application listed for the Candidate's phone number."""
        listing = await self.operator.get("/api/applications", params={"q": phone, "limit": 20})
        listing.raise_for_status()
        return [i for i in listing.json()["items"] if (i["candidate"].get("phone") or "") == phone]

    async def queue_items(self, application_id: str, kind: str) -> list[dict[str, Any]]:
        """The open queue items of `kind` (approval, review, escalation) for one Application."""
        found: list[dict[str, Any]] = []
        params: dict[str, Any] = {"kind": kind, "limit": 200}
        while True:
            response = await self.operator.get("/api/queue", params=params)
            response.raise_for_status()
            page = response.json()
            found += [i for i in page["items"] if i["application"]["id"] == application_id]
            if not page.get("next_cursor"):
                return found
            params["cursor"] = page["next_cursor"]

    async def approve(self, item_id: str) -> httpx.Response:
        return await self.operator.post(
            f"/api/queue/approvals/{item_id}", json={"decision": "approved"}
        )

    async def detail(self, application_id: str) -> dict[str, Any]:
        response = await self.operator.get(f"/api/applications/{application_id}")
        response.raise_for_status()
        return response.json()

    # ---- spend ------------------------------------------------------------------------

    def charge(self, app: dict[str, Any]) -> Charge:
        """Count the Application's usage against the cap and return what it cost."""
        usage = usage_of(app)
        tokens = {k: int(usage[k]) for k in TOKEN_KEYS}
        guard_usd = self.guard.record(
            usage["models"], tokens, usage["est_cost_usd"], usage["by_model"]
        )
        billed = sum(
            max(0.0, float(m["reconciled_cost_usd"]))
            for m in usage["by_model"]
            if m.get("reconciled_cost_usd") is not None
        )
        return Charge(
            cost_usd=reported_cost(usage["by_model"], self.guard.prices, usage["est_cost_usd"]),
            billed_usd=round(billed, 8),
            guard_usd=round(guard_usd, 8),
            models=usage["models"],
            runs=usage["runs"],
            tokens=tokens,
        )


__all__ = ["DEFAULT_MODEL", "Charge", "LiveStack", "Turn", "summary_prose"]
