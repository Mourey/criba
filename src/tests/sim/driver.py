"""Sim driver: plays a persona against the web chat API to completion (spec 004 task 2).

The driver talks only to the public web chat channel (`contracts/webchat.openapi.yaml`): start a
conversation, send messages with unique `message_id`s, read outbound messages after a cursor and
answer interactive options through `POST .../replies`. It works against any base URL: the real
backend, the Prism mock, or an in-process ASGI app.

How a persona is played (rules policy, ADR 0017; the `model` policy is the comparison run's job):

- The opening goes first (the Candidate writes first, ADR 0013).
- After every agent message the driver works out which question was asked (`detect_prompt`) and
  sends the persona's n-th reply for that field; when the replies run out it repeats the last.
- An agent message that asks something it cannot place (an invalid-answer re-ask, an option
  list) repeats the pending field with the next reply.
- `interrupts` fire when the agent is about to ask their field, or at a named moment. See
  `_Session.fire` for the order of `advance` and `say`.
- The run ends when the persona is done (`then: stop`), the agent says something that asks
  nothing, a Knockout has been delivered and answered, or a timeout or turn limit is hit.

Nothing here asserts on the agent's prose; the transcript is data for `sim.asserts`.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from sim.clock import SimClock
from sim.detect import asks_something, detect_prompt, normalize, zone_options
from sim.personas.schema import FIELD_ORDER, Persona, Reply

Detector = Callable[[str, dict[str, Any] | None], str | None]

_MEDIA_STAND_INS = {
    "voice_note": {"type": "audio", "filename": "nota-de-voz.ogg"},
    "image": {"type": "image", "filename": "foto.jpg"},
}


KNOCKOUT_FIELDS = ("zone", "driver_license")


class ChatError(RuntimeError):
    """The web chat API answered with an error."""

    def __init__(self, message: str, *, status: int, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class Entry:
    """One message in the transcript, with when the driver sent or first saw it."""

    direction: str  # inbound (Candidate) or outbound
    sender: str  # candidate, agent, human, system
    text: str
    t: float  # seconds since the run started
    id: int | None = None
    message_id: str | None = None
    sender_label: str | None = None
    interactive: dict[str, Any] | None = None
    reply_to: dict[str, Any] | None = None
    links: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    created_at: str | None = None
    lang: str | None = None  # language the persona wrote in (inbound only)
    purpose: str | None = None  # why the driver sent it (inbound only)


@dataclass
class Turn:
    """A Candidate message and the agent messages it drew, with reply timings in seconds."""

    sent: Entry
    replies: list[Entry] = field(default_factory=list)
    latency_first_s: float | None = None  # send to first agent message
    latency_last_s: float | None = None  # send to last agent message of the burst
    timed_out: bool = False


@dataclass
class RunResult:
    persona_id: str
    conversation_id: str
    phone: str | None
    ended: str  # completed, knockout, stopped, no_reply, timeout, max_turns, no_scripted_reply:<f>
    entries: list[Entry] = field(default_factory=list)  # transcript in observed order
    turns: list[Turn] = field(default_factory=list)
    clock_steps: list[dict[str, Any]] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def agent_entries(self) -> list[Entry]:
        return [e for e in self.entries if e.direction == "outbound"]

    @property
    def first_agent_message(self) -> Entry | None:
        return next(iter(self.agent_entries), None)

    def to_dict(self) -> dict[str, Any]:
        """JSON-able form, one line of a JSONL dataset for load and comparison runs."""
        return asdict(self)


class ClockRequiredError(RuntimeError):
    """The persona moves time but the driver has no `SimClock`."""


class WebChatClient:
    """Thin async client for the `/api/chat` operations in the contract."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        id_factory: Callable[[], str] | None = None,
        max_retries: int = 3,
    ):
        self._http = http
        self._new_id = id_factory or (lambda: str(uuid.uuid4()))
        self._max_retries = max_retries

    def new_message_id(self) -> str:
        return self._new_id()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """One call, retried on transport errors (messages are idempotent on `message_id`)
        and on 429 after `Retry-After`."""
        attempt = 0
        while True:
            try:
                response = await self._http.request(method, url, **kwargs)
            except httpx.TransportError:
                attempt += 1
                if attempt > self._max_retries:
                    raise
                await asyncio.sleep(0.05 * attempt)
                continue
            if response.status_code == 429 and attempt < self._max_retries:
                attempt += 1
                delay = min(float(response.headers.get("Retry-After", "1") or 1), 5.0)
                await asyncio.sleep(delay)
                continue
            return response

    @staticmethod
    def _ok(response: httpx.Response, expected: set[int]) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code not in expected:
            message = (
                body.get("message", response.text) if isinstance(body, dict) else response.text
            )
            code = body.get("error") if isinstance(body, dict) else None
            raise ChatError(
                f"{response.request.method} {response.request.url.path} -> "
                f"{response.status_code}: {message}",
                status=response.status_code,
                code=code,
            )
        return body

    async def start(
        self,
        country: str,
        *,
        phone: str | None = None,
        locale: str | None = None,
        client: str = "simulator",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"country": country, "client": client}
        if phone:
            payload["phone"] = phone
        if locale:
            payload["locale"] = locale
        response = await self._request("POST", "/api/chat/conversations", json=payload)
        return self._ok(response, {201})

    async def send(
        self,
        conversation_id: str,
        text: str,
        *,
        media: dict[str, str] | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message_id": message_id or self._new_id(),
            "text": text,
        }
        if media:
            payload["media"] = media
        url = f"/api/chat/conversations/{conversation_id}/messages"
        return self._ok(await self._request("POST", url, json=payload), {202})

    async def reply(
        self,
        conversation_id: str,
        in_reply_to: int,
        option_id: str,
        *,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "message_id": message_id or self._new_id(),
            "in_reply_to": in_reply_to,
            "option_id": option_id,
        }
        url = f"/api/chat/conversations/{conversation_id}/replies"
        return self._ok(await self._request("POST", url, json=payload), {202})

    async def read(
        self,
        conversation_id: str,
        after: int = 0,
        *,
        wait: float = 0,
        limit: int = 100,
        seen: tuple[bool, bool] | None = None,
    ) -> dict[str, Any]:
        """One page. `seen` is the (agent_typing, run_finished) of the last page read: the poll
        compares the run state against it, so a run that ended between two polls returns at once."""
        url = f"/api/chat/conversations/{conversation_id}/messages"
        params: dict[str, Any] = {"after": after, "wait": math.ceil(wait), "limit": limit}
        if seen is not None:
            params |= {"agent_typing": seen[0], "run_finished": seen[1]}
        timeout = httpx.Timeout(max(10.0, wait + 10.0))
        response = await self._request("GET", url, params=params, timeout=timeout)
        return self._ok(response, {200})


class PersonaDriver:
    """Runs personas against one backend.

    Args:
        http: `httpx.AsyncClient` pointed at the backend (base URL or ASGI transport).
        clock: needed only by personas that move time (`advance`) or when `align_clock` is set.
        reply_timeout_s: how long to wait for an agent reply before giving up. Raise it for the
            real model.
        poll_wait_s: long-poll window per read (whole seconds, at most 30).
        settle_s: pause after a reply before a last read, to catch a trailing message.
        max_turns: safety limit on Candidate messages per run.
        max_reprompts: consecutive unplaced re-asks answered before the run stops.
        detector: maps (text, interactive) to the question asked; see `sim.detect`.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        clock: SimClock | None = None,
        reply_timeout_s: float = 30.0,
        poll_wait_s: float = 2.0,
        settle_s: float = 0.05,
        max_turns: int = 60,
        max_reprompts: int = 4,
        detector: Detector = detect_prompt,
        id_factory: Callable[[], str] | None = None,
    ):
        self.chat = WebChatClient(http, id_factory=id_factory)
        self.clock = clock
        self.reply_timeout_s = reply_timeout_s
        self.poll_wait_s = poll_wait_s
        self.settle_s = settle_s
        self.max_turns = max_turns
        self.max_reprompts = max_reprompts
        self.detector = detector
        self._owned_http: httpx.AsyncClient | None = None

    @classmethod
    def for_base_url(cls, base_url: str, **kwargs: Any) -> PersonaDriver:
        """A driver with its own HTTP client; the caller closes it with `await driver.aclose()`."""
        http = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(15.0))
        clock = kwargs.pop("clock", None) or SimClock(http)
        driver = cls(http, clock=clock, **kwargs)
        driver._owned_http = http
        return driver

    async def aclose(self) -> None:
        if self._owned_http is not None:
            await self._owned_http.aclose()

    async def run(
        self, persona: Persona, *, phone: str | None = None, align_clock: bool = True
    ) -> RunResult:
        """Play `persona` to completion.

        `phone` overrides the persona's simulated number (load runs need unique ones).
        `align_clock` moves the sim clock to the persona's `timing.start_local_time` first; it
        is skipped when the driver has no clock.
        """
        session = _Session(self, persona, phone or persona.identity.phone)
        return await session.play(align_clock=align_clock)


class _Session:
    """State of one persona run."""

    def __init__(self, driver: PersonaDriver, persona: Persona, phone: str):
        self.d = driver
        self.persona = persona
        self.phone = phone
        self.conversation_id = ""
        self.cursor = 0
        self.t0 = time.perf_counter()
        self.entries: list[Entry] = []
        self.turns: list[Turn] = []
        self.clock_steps: list[dict[str, Any]] = []
        self.ended = "completed"
        self.asked: dict[str, int] = {}
        self.confirm_used = 0
        # A correction at the confirm step, per field: the Candidate's answer from then on.
        self.corrected: dict[str, Reply] = {}
        self.fired: set[int] = set()
        self.default_lang = "en" if persona.identity.locale == "en" else "es"
        self._seen_ids: set[int] = set()
        self._opened_options: dict[int, dict[str, Any]] = {}

    # ---- plumbing ---------------------------------------------------------------------

    def _now(self) -> float:
        return time.perf_counter() - self.t0

    def _result(self) -> RunResult:
        return RunResult(
            persona_id=self.persona.id,
            conversation_id=self.conversation_id,
            phone=self.phone,
            ended=self.ended,
            entries=self.entries,
            turns=self.turns,
            clock_steps=self.clock_steps,
            elapsed_s=self._now(),
        )

    def _need_clock(self) -> SimClock:
        if self.d.clock is None:
            raise ClockRequiredError(
                f"persona {self.persona.id} moves the sim clock; give PersonaDriver a SimClock"
            )
        return self.d.clock

    def _absorb(self, page: dict[str, Any], turn: Turn | None) -> list[Entry]:
        """Add unseen outbound messages from a page to the transcript; return them."""
        fresh: list[Entry] = []
        for m in page.get("messages", []):
            mid = m["id"]
            self.cursor = max(self.cursor, mid)
            if mid in self._seen_ids:
                continue
            self._seen_ids.add(mid)
            if m.get("direction") == "inbound":
                continue  # our own message, already in the transcript
            entry = Entry(
                direction="outbound",
                sender=m.get("sender", "agent"),
                text=m.get("text", ""),
                t=self._now(),
                id=mid,
                sender_label=m.get("sender_label"),
                interactive=m.get("interactive"),
                links=m.get("links") or [],
                attachments=m.get("attachments") or [],
                created_at=m.get("created_at"),
            )
            self.entries.append(entry)
            if entry.interactive:
                self._opened_options[mid] = entry.interactive
            if turn is not None:
                turn.replies.append(entry)
            fresh.append(entry)
        return fresh

    async def _await_reply(self, turn: Turn, started: float) -> list[Entry]:
        """Wait for the agent's reply to `turn`: at least one message and the run finished.

        The page's `run_finished` says when the turn is over (every reply is stored). A
        backend without that field falls back to the old heuristic: `agent_typing` false,
        then a settle pause and one more read to catch a trailing message.
        """
        deadline = started + self.d.reply_timeout_s
        got: list[Entry] = []
        seen: tuple[bool, bool] | None = None
        while True:
            remaining = deadline - self._now()
            if remaining <= 0:
                turn.timed_out = not got
                break
            wait = max(0, min(self.d.poll_wait_s, remaining))
            page = await self.d.chat.read(self.conversation_id, self.cursor, wait=wait, seen=seen)
            fresh = self._absorb(page, turn)
            got.extend(fresh)
            finished = page.get("run_finished")
            if finished is not None:
                if got and finished:
                    break
                seen = (bool(page.get("agent_typing")), bool(finished))
                continue
            if got and not page.get("agent_typing"):
                if self.d.settle_s:
                    await asyncio.sleep(self.d.settle_s)
                    page = await self.d.chat.read(self.conversation_id, self.cursor, wait=0)
                    got.extend(self._absorb(page, turn))
                    if page.get("agent_typing"):
                        continue
                break
        if got:
            turn.latency_first_s = got[0].t - started
            turn.latency_last_s = got[-1].t - started
        if turn.timed_out:
            self.ended = "timeout"
        return got

    async def _drain(self) -> list[Entry]:
        """Collect agent messages that arrived without a Candidate message (nudges)."""
        collected: list[Entry] = []
        while True:
            page = await self.d.chat.read(self.conversation_id, self.cursor, wait=0)
            fresh = self._absorb(page, None)
            collected.extend(fresh)
            if not fresh:
                return collected

    # ---- sending ----------------------------------------------------------------------

    def _lang_of(self, reply: Reply) -> str:
        return reply.lang or self.default_lang

    def _pick_option(
        self, reply: Reply, interactive: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if reply.pick_option is None or not interactive:
            return None
        options = interactive.get("options", [])
        choice = reply.pick_option
        if isinstance(choice, int):
            return options[choice] if 0 <= choice < len(options) else None
        needle = normalize(str(choice))
        return next((o for o in options if needle in normalize(str(o.get("label", "")))), None)

    async def say(
        self,
        reply: Reply,
        purpose: str,
        prompt_msg: Entry | None = None,
    ) -> list[Entry]:
        """Send one persona reply (text, media stand-in or option tap) and wait for the agent."""
        chat = self.d.chat
        message_id = chat.new_message_id()
        option = self._pick_option(reply, prompt_msg.interactive if prompt_msg else None)
        started = self._now()
        entry = Entry(
            direction="inbound",
            sender="candidate",
            text=reply.text,
            t=started,
            message_id=message_id,
            lang=self._lang_of(reply),
            purpose=purpose,
        )
        accepted: dict[str, Any]
        if option is not None and prompt_msg is not None and prompt_msg.id is not None:
            try:
                accepted = await chat.reply(
                    self.conversation_id,
                    prompt_msg.id,
                    str(option["id"]),
                    message_id=message_id,
                )
                entry.text = str(option.get("label", reply.text))
                entry.reply_to = {
                    "message_id": prompt_msg.id,
                    "option_id": option["id"],
                }
            except ChatError as exc:
                if exc.code != "option_expired":
                    raise
                accepted = await chat.send(self.conversation_id, reply.text, message_id=message_id)
        else:
            media = _MEDIA_STAND_INS.get(reply.media) if reply.media else None
            accepted = await chat.send(
                self.conversation_id, reply.text, media=media, message_id=message_id
            )
        entry.id = accepted.get("id")
        if entry.id is not None:
            self._seen_ids.add(entry.id)
            self.cursor = max(self.cursor, entry.id)
        self.entries.append(entry)
        turn = Turn(sent=entry)
        self.turns.append(turn)
        return await self._await_reply(turn, started)

    # ---- interrupts -------------------------------------------------------------------

    async def _advance(self, steps: list[str]) -> None:
        clock = self._need_clock()
        for step in steps:
            state = await clock.advance(step)
            self.clock_steps.append(
                {
                    "t": self._now(),
                    "step": step,
                    "now": state.now.isoformat(),
                    "due_jobs_run": state.due_jobs_run,
                }
            )
            await self._drain()  # nudges and Abandoned notices arrive between steps

    async def fire(self, at: str, prompt_msg: Entry | None = None) -> tuple[bool, list[Entry]]:
        """Run the persona's interrupts for `at`. Returns (stop, agent messages after the last say).

        Order of `advance` and `say` inside one interrupt, which the persona schema leaves open:
        at `after_backout_prompt` the Candidate answers at once and time passes afterwards
        (silence after "Yes, please stop"); everywhere else time passes first and the Candidate
        speaks late (a Knockout, then ten days, then "I moved"). `say` alone and `advance` alone
        behave as written.
        """
        last: list[Entry] = []
        stop = False
        for idx, interrupt in enumerate(self.persona.interrupts):
            if interrupt.at != at or idx in self.fired:
                continue
            self.fired.add(idx)
            say_first = at == "after_backout_prompt"
            if not say_first:
                await self._advance(interrupt.advance)
            if interrupt.say is not None:
                last = await self.say(interrupt.say, f"interrupt:{at}", prompt_msg)
            if say_first:
                await self._advance(interrupt.advance)
            if interrupt.then == "stop":
                stop = True
                break
        if self.ended == "timeout":
            stop = True
        return stop, last

    def _has_pending(self, at: str) -> bool:
        return any(
            i.at == at and idx not in self.fired for idx, i in enumerate(self.persona.interrupts)
        )

    # ---- the loop ---------------------------------------------------------------------

    def _prompt_of(self, agent: list[Entry]) -> tuple[str | None, Entry | None]:
        """The question asked by the latest agent message that asks a recognisable one."""
        for entry in reversed(agent):
            kind = self.d.detector(entry.text, entry.interactive)
            if kind:
                return kind, entry
        return None, None

    def _next_reply(self, kind: str) -> Reply | None:
        persona = self.persona
        if kind == "confirm":
            index = self.confirm_used
            self.confirm_used += 1
            if index < len(persona.confirm):
                correction = persona.confirm[index]
                if correction.field is not None:
                    self.corrected[correction.field] = correction
                return correction
            return Reply(
                text="Yes" if self.default_lang == "en" else "Sí",
                lang=self.default_lang,
            )
        if kind in self.corrected:
            # The agent asks again for a field the Candidate just corrected (a Zone list to
            # pick from, "which one?"): they repeat the correction, never their first answer.
            return self.corrected[kind]
        replies = persona.answers.get(kind)  # type: ignore[call-overload]
        if not replies:
            return None
        n = self.asked.get(kind, 0)
        self.asked[kind] = n + 1
        return replies[min(n, len(replies) - 1)]

    def _zone_pick(self, options: list[dict[str, Any]]) -> int:
        """Index of the Zone option the persona taps (`Persona.zone_choice`).

        An explicit index or label substring wins. Otherwise the first option whose label
        shares the most words with what the persona said about its Zone (its neighbourhood
        or city; after a Zone correction at the confirm step, only the correction), and the
        first option when none does.
        """
        hint = self.persona.zone_choice
        if isinstance(hint, int) and 0 <= hint < len(options):
            return hint
        if isinstance(hint, str):
            needle = normalize(hint).strip()
            for index, option in enumerate(options):
                if needle in normalize(str(option.get("label", ""))):
                    return index
        correction = self.corrected.get("zone")
        if correction is not None:
            said = [correction.text, correction.query or ""]
        else:
            said = [r.text for r in self.persona.answers.get("zone", [])]
            if "zone" in self.persona.opening.covers:
                said.append(self.persona.opening.text)
        wanted = {w for w in re.findall(r"[a-z0-9]+", normalize(" ".join(said))) if len(w) > 2}
        best, best_score = 0, 0
        for index, option in enumerate(options):
            label = set(re.findall(r"[a-z0-9]+", normalize(str(option.get("label", "")))))
            score = len(wanted & label)
            if score > best_score:
                best, best_score = index, score
        return best

    def _with_zone_choice(self, reply: Reply, prompt_msg: Entry) -> Reply:
        """Tap a Zone option when the agent offers Zone ids and the reply names none."""
        options = zone_options(prompt_msg.interactive)
        if (
            not options
            and reply is self.corrected.get("zone")
            and isinstance(reply.pick_option, str)
        ):
            # A corrected Zone asked again in text ("¿En qué colonia?"): the Candidate types
            # the Zone they would tap instead of repeating the correction.
            return Reply(text=reply.pick_option, lang=reply.lang)
        if not options or reply.pick_option is not None:
            return reply
        return reply.model_copy(update={"pick_option": self._zone_pick(options)})

    def _next_unasked(self) -> str | None:
        """First field in collection order that the persona has neither sent nor covered."""
        covered = set(self.persona.opening.covers)
        return next((f for f in FIELD_ORDER if f not in covered and not self.asked.get(f)), None)

    async def play(self, *, align_clock: bool) -> RunResult:
        persona = self.persona
        ident = persona.identity
        if self.d.clock is not None and align_clock:
            state = await self.d.clock.advance_to_local_time(
                persona.timing.start_local_time, ident.country
            )
            self.clock_steps.append(
                {
                    "t": self._now(),
                    "step": f"align:{persona.timing.start_local_time}",
                    "now": state.now.isoformat(),
                    "due_jobs_run": state.due_jobs_run,
                }
            )
        conv = await self.d.chat.start(
            ident.country, phone=self.phone, locale=ident.locale, client="simulator"
        )
        self.conversation_id = conv["conversation_id"]
        self.cursor = int(conv.get("cursor", 0))

        stop, _ = await self.fire("start")
        if stop:
            self.ended = "stopped" if self.ended != "timeout" else self.ended
            return self._result()

        agent = await self.say(persona.opening, "opening")
        pending: str | None = None
        last_sent_field: str | None = None
        reprompts = 0
        expect = persona.expect

        for _ in range(self.d.max_turns):
            if self.ended == "timeout":
                return self._result()
            if not agent:
                self.ended = "no_reply"
                return self._result()
            kind, prompt_msg = self._prompt_of(agent)

            # A Knockout answers a Knock-out check (Zone, license) with a message that asks
            # nothing. Anything that asks, even about the same field, is a clarification or a
            # re-ask (ambiguous Zone buttons, "which city?", an invalid answer), not a Knockout.
            # The persona expects a Knockout when it ends as one or reacts to it.
            knocked_out = (
                (expect.outcome == "knockout" or self._has_pending("after_knockout"))
                and last_sent_field in KNOCKOUT_FIELDS
                and kind is None
                and not any(asks_something(e.text, e.interactive) for e in agent)
            )
            if knocked_out:
                self.ended = "knockout"
                if self._has_pending("after_knockout"):
                    await self.fire("after_knockout")
                return self._result()

            if kind is None:
                if (
                    asks_something(agent[-1].text, agent[-1].interactive)
                    and reprompts < self.d.max_reprompts
                    and (guess := pending or self._next_unasked()) is not None
                ):
                    kind, prompt_msg = guess, agent[-1]
                    reprompts += 1
                else:
                    self.ended = "completed"
                    return self._result()
            else:
                reprompts = 0

            # Interrupts tied to this question run before the normal answer.
            stop, after = await self.fire(kind, prompt_msg)
            if stop:
                self.ended = self.ended if self.ended == "timeout" else "stopped"
                return self._result()
            if after and self._has_pending("after_backout_prompt"):
                stop, after = await self.fire("after_backout_prompt", after[-1])
                if stop:
                    self.ended = self.ended if self.ended == "timeout" else "stopped"
                    return self._result()
            reply = self._next_reply(kind)
            if reply is None:
                self.ended = f"no_scripted_reply:{kind}"
                return self._result()
            if kind == "zone" and prompt_msg is not None:
                reply = self._with_zone_choice(reply, prompt_msg)
            pending = kind
            last_sent_field = None if kind == "confirm" else kind
            agent = await self.say(reply, f"answer:{kind}", prompt_msg)

        self.ended = "max_turns"
        return self._result()


async def run_persona(
    persona: Persona, base_url: str, *, clock: bool = True, **kwargs: Any
) -> RunResult:
    """Run one persona against `base_url` with a throwaway driver.

    `clock=False` skips the sim clock (a Prism mock has none to move).
    """
    http = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(15.0))
    try:
        sim_clock = SimClock(http) if clock else None
        driver = PersonaDriver(http, clock=sim_clock, **kwargs)
        return await driver.run(persona)
    finally:
        await http.aclose()


__all__ = [
    "ChatError",
    "ClockRequiredError",
    "Detector",
    "Entry",
    "PersonaDriver",
    "RunResult",
    "Turn",
    "WebChatClient",
    "run_persona",
]
