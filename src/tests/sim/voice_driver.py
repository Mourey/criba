"""Voice driver: plays a voice persona through the custom-LLM endpoint (voice-agent sim-personas
SM5; R24.2).

The driver does what ElevenLabs and the browser do on a call, with no audio:

1. `POST /api/chat/conversations` with `channel: voice` (the typed persona's country and locale):
   voice is its own flow, and only a voice conversation starts a call (ADR 0059);
2. `POST /api/chat/conversations/{id}/voice-session`, reading `voice_token` from the 201 body;
3. each utterance as one `POST /api/voice/v1/chat/completions` with the bearer, the token in
   `elevenlabs_extra_body.criba_voice_token` and the growing `messages` history, read to
   `data: [DONE]` (`stream_turn`);
4. after each `[DONE]`, the chat long poll, for what the stream does not carry: options, links
   and attachments. Options are said as their label; nothing posts to `/messages` or `/replies`.

It plays the typed counterpart's replies the way `sim.driver._Session.play` does, reacting to
the streamed text (`sim.detect`) and the latest stored message's options, with the voice
persona's moments in place of the typed `interrupts`. The bearer is held only on the driver and
the voice token only in `VoiceRun.voice_token` (never in its repr); neither reaches an error.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from sim.detect import asks_something, detect_prompt, normalize, zone_options
from sim.personas.schema import FIELD_ORDER, Persona, Reply
from sim.personas.voice import (
    Correction,
    VoicePersona,
    is_tap,
    typed_of,
    typed_reply,
)

COMPLETIONS_PATH = "/api/voice/v1/chat/completions"
MODEL = "criba-voice"
FIRST_CHUNK = "... "  # spoken-turn's filler when the reply is slow; not part of the reply
KNOCKOUT_FIELDS = ("zone", "driver_license")
# Slot labels are "mar 14 · 10:00" (`repo.local_label`); said as "El martes a las diez de la
# mañana". Only the seed's SLOT_TIMES have hour words.
WEEKDAY_NAMES = {
    "lun": "lunes",
    "mar": "martes",
    "mié": "miércoles",
    "jue": "jueves",
    "vie": "viernes",
    "sáb": "sábado",
    "dom": "domingo",
}
HOUR_WORDS = {10: "diez de la mañana", 12: "doce del mediodía", 16: "cuatro de la tarde"}
_SLOT_LABEL = re.compile(r"^(?P<day>\S+)\s+\d{1,2}\s+·\s+(?P<hour>\d{2}):(?P<minute>\d{2})$")
_YES = ("si", "yes")


class VoiceDriverError(RuntimeError):
    """A call failed or the run cannot go on. Never carries the bearer or the voice token."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class VoiceUnavailableError(VoiceDriverError):
    """The voice-session endpoint refused to start a session (503 `voice_unavailable`)."""


@dataclass
class StreamedTurn:
    """One custom-LLM response: the text spoken, every frame before `[DONE]`, and the seconds
    from the request to its first and its last byte."""

    text: str
    frames: list[dict[str, Any]]
    first_byte_s: float
    last_byte_s: float


@dataclass
class VoiceRun:
    conversation_id: str
    voice_token: str = field(repr=False)
    ended: str = ""  # completed, knockout, opted_out, booked, no_reply, max_turns, ...
    turns: list[StreamedTurn] = field(default_factory=list)
    said: list[str] = field(default_factory=list)  # one utterance per turn, in order
    messages: list[dict[str, Any]] = field(default_factory=list)  # stored, from the long poll


AfterTurn = Callable[[VoiceRun, StreamedTurn], None]


def _error(response: httpx.Response, what: str) -> VoiceDriverError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    code = body.get("error") if isinstance(body, dict) else None
    message = f"{what} -> {response.status_code}: {code or 'error'}"
    kind = VoiceUnavailableError if code == "voice_unavailable" else VoiceDriverError
    return kind(message, status=response.status_code, code=code)


def _require_secret(llm_secret: str) -> str:
    if not isinstance(llm_secret, str) or not llm_secret.strip():
        raise ValueError("the voice-LLM secret is empty; the endpoint refuses an empty bearer")
    return llm_secret.strip()


def stream_turn(
    client: httpx.Client,
    *,
    llm_secret: str,
    voice_token: str,
    messages: list[dict[str, str]],
) -> StreamedTurn:
    """Post one custom-LLM request and read its stream to `data: [DONE]`."""
    bearer = _require_secret(llm_secret)
    if not voice_token:
        raise ValueError("the voice token is empty")
    body = {
        "model": MODEL,
        "stream": True,
        "messages": messages,
        "elevenlabs_extra_body": {"criba_voice_token": voice_token},
    }
    frames: list[dict[str, Any]] = []
    started = time.perf_counter()
    first: float | None = None
    last = 0.0
    with client.stream(
        "POST",
        COMPLETIONS_PATH,
        json=body,
        headers={"Authorization": f"Bearer {bearer}", "Accept": "text/event-stream"},
        timeout=httpx.Timeout(90.0),
    ) as response:
        if response.status_code != 200:
            response.read()
            raise _error(response, f"POST {COMPLETIONS_PATH}")
        for line in response.iter_lines():
            now = time.perf_counter() - started
            if first is None:
                first = now
            last = now
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            frames.append(json.loads(data))
    chunks = [
        str(c["delta"]["content"])
        for f in frames
        for c in f.get("choices", [])
        if c.get("delta", {}).get("content")
    ]
    if chunks and chunks[0] == FIRST_CHUNK:
        chunks = chunks[1:]
    return StreamedTurn(
        text="".join(chunks),
        frames=frames,
        first_byte_s=first if first is not None else last,
        last_byte_s=last,
    )


def _is_yes_no(interactive: dict[str, Any] | None) -> bool:
    """Two options, one label starting "Sí" or "Yes" and one "No" (spoken-checks' choice)."""
    options = (interactive or {}).get("options") or []
    if len(options) != 2:
        return False
    words = [re.findall(r"[a-z0-9]+", normalize(str(o.get("label", "")))) for o in options]
    yes = [w for w in words if w and w[0] in _YES]
    no = [w for w in words if w == ["no"]]
    return len(yes) == 1 and len(no) == 1


class VoiceDriver:
    """Runs voice personas on one backend through the custom-LLM endpoint.

    Args:
        client: `httpx.Client` pointed at the backend (base URL or a mock transport).
        llm_secret: the bearer `criba_voice_llm_secret` holds; refused when empty.
        after_turn: called with the run and the turn after each turn is read in full.
        poll_wait_s: long-poll window per read (whole seconds).
        reply_timeout_s: how long to wait for a turn's stored reply.
        max_turns: safety limit on utterances per run.
        max_reprompts: consecutive unplaced re-asks answered before the run stops.
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        llm_secret: str,
        after_turn: AfterTurn | None = None,
        poll_wait_s: float = 2.0,
        reply_timeout_s: float = 60.0,
        max_turns: int = 60,
        max_reprompts: int = 4,
    ):
        self._secret = _require_secret(llm_secret)
        self.client = client
        self.after_turn = after_turn
        self.poll_wait_s = poll_wait_s
        self.reply_timeout_s = reply_timeout_s
        self.max_turns = max_turns
        self.max_reprompts = max_reprompts

    def __repr__(self) -> str:
        return f"VoiceDriver(base_url={str(self.client.base_url)!r})"

    # ---- calls ----------------------------------------------------------------------------

    def _post(self, path: str, payload: dict[str, Any] | None, expected: int) -> dict[str, Any]:
        response = self.client.post(path, json=payload)
        if response.status_code != expected:
            raise _error(response, f"POST {path}")
        body: dict[str, Any] = response.json()
        return body

    def start(self, persona: VoicePersona, *, phone: str | None = None) -> VoiceRun:
        """A voice conversation for `persona` and a voice session on it."""
        ident = typed_of(persona).identity
        conv = self._post(
            "/api/chat/conversations",
            {
                "country": ident.country,
                "phone": phone or persona.phone,
                "locale": ident.locale,
                "client": "simulator",
                "channel": "voice",
            },
            201,
        )
        conversation_id = str(conv["conversation_id"])
        session = self._post(f"/api/chat/conversations/{conversation_id}/voice-session", None, 201)
        return VoiceRun(conversation_id=conversation_id, voice_token=str(session["voice_token"]))

    def read(
        self, run: VoiceRun, *, wait: float = 0, seen: tuple[bool, bool] | None = None
    ) -> dict[str, Any]:
        """One long-poll page after the last stored message seen; its messages join the run.

        `seen` is the (agent_typing, run_finished) of the last page: the poll compares the run
        state against it, so a run that ended between two polls returns at once."""
        after = max([0, *[int(m["id"]) for m in run.messages]])
        params: dict[str, Any] = {"after": after, "wait": int(wait), "limit": 100}
        if seen is not None:
            params |= {"agent_typing": seen[0], "run_finished": seen[1]}
        response = self.client.get(
            f"/api/chat/conversations/{run.conversation_id}/messages",
            params=params,
            timeout=httpx.Timeout(max(10.0, wait + 10.0)),
        )
        if response.status_code != 200:
            raise _error(response, "GET messages")
        page: dict[str, Any] = response.json()
        known = {m["id"] for m in run.messages}
        run.messages.extend(m for m in page.get("messages", []) if m["id"] not in known)
        return page

    def _history(self, run: VoiceRun, utterance: str) -> list[dict[str, str]]:
        history: list[dict[str, str]] = []
        for said, turn in zip(run.said, run.turns, strict=True):
            history.append({"role": "user", "content": said})
            history.append({"role": "assistant", "content": turn.text})
        history.append({"role": "user", "content": utterance})
        return history

    def say(self, run: VoiceRun, utterance: str) -> tuple[StreamedTurn, list[dict[str, Any]]]:
        """One utterance: the streamed reply, then the stored outbound messages it drew."""
        seen = len(run.messages)
        turn = stream_turn(
            self.client,
            llm_secret=self._secret,
            voice_token=run.voice_token,
            messages=self._history(run, utterance),
        )
        run.said.append(utterance)
        run.turns.append(turn)
        deadline = time.monotonic() + self.reply_timeout_s
        state: tuple[bool, bool] | None = None
        while True:
            remaining = deadline - time.monotonic()
            page = self.read(run, wait=max(0.0, min(self.poll_wait_s, remaining)), seen=state)
            stored = [m for m in run.messages[seen:] if m.get("direction") == "outbound"]
            if (stored and page.get("run_finished", True)) or remaining <= 0:
                break
            if page.get("run_finished") is not None:
                state = (bool(page.get("agent_typing")), bool(page["run_finished"]))
            if self.poll_wait_s <= 0:
                time.sleep(min(0.2, remaining))
        if self.after_turn is not None:
            self.after_turn(run, turn)
        return turn, stored

    # ---- runs -----------------------------------------------------------------------------

    def play(self, persona: VoicePersona, *, phone: str | None = None) -> VoiceRun:
        """Play `persona` from the opening to its end. `phone` overrides the persona's own."""
        run = self.start(persona, phone=phone)
        _VoicePlay(self, persona, run).play()
        return run

    def book(self, run: VoiceRun, persona: VoicePersona) -> VoiceRun:
        """After the Approval: wait for the slot offer, say a slot, read the confirmation."""
        booking = persona.booking
        if booking is None:
            raise VoiceDriverError(f"{persona.id} has no booking")
        offer = self._wait_for(run, lambda m: _slots(m) != [], "a slot offer")
        slots = _slots(offer)
        labels = [label for _, label, _, _ in slots]
        pick = next(
            (
                (day, hour)
                for _, _, day, hour in slots
                if (booking.weekday is None or day == booking.weekday)
                and (booking.hour is None or hour == booking.hour)
            ),
            None,
        )
        if pick is None:
            raise VoiceDriverError(
                f"{persona.id}: no offered slot fits weekday={booking.weekday} "
                f"hour={booking.hour}; offered: {labels}"
            )
        day, hour = pick
        if hour not in HOUR_WORDS:
            raise VoiceDriverError(f"{persona.id}: no hour words for {hour}; offered: {labels}")
        self.say(run, f"El {day} a las {HOUR_WORDS[hour]}")
        self._wait_for(
            run,
            lambda m: any(a.get("type") == "ics" for a in m.get("attachments") or []),
            "the booking confirmation's .ics",
        )
        run.ended = "booked"
        return run

    def _wait_for(
        self, run: VoiceRun, wanted: Callable[[dict[str, Any]], bool], what: str
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.reply_timeout_s
        while True:
            found = next(
                (
                    m
                    for m in reversed(run.messages)
                    if m.get("direction") == "outbound" and wanted(m)
                ),
                None,
            )
            if found is not None:
                return found
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VoiceDriverError(f"no {what} within {self.reply_timeout_s:.0f} s")
            self.read(run, wait=min(self.poll_wait_s, remaining))
            if self.poll_wait_s <= 0:
                time.sleep(min(0.2, remaining))


def _slots(message: dict[str, Any]) -> list[tuple[str, str, str, int]]:
    """`(option id, label, weekday name, hour)` of each offered slot in `message`."""
    found = []
    for option in (message.get("interactive") or {}).get("options") or []:
        if not str(option.get("id", "")).startswith("so_"):
            continue
        label = str(option.get("label", ""))
        match = _SLOT_LABEL.match(label.strip())
        if match is None or match.group("day") not in WEEKDAY_NAMES:
            continue
        found.append(
            (
                str(option["id"]),
                label,
                WEEKDAY_NAMES[match.group("day")],
                int(match.group("hour")),
            )
        )
    return found


class _VoicePlay:
    """State of one voice persona run (mirrors `sim.driver._Session.play`)."""

    def __init__(self, driver: VoiceDriver, persona: VoicePersona, run: VoiceRun):
        self.d = driver
        self.persona = persona
        self.typed: Persona = typed_of(persona)
        self.run = run
        self.asked: dict[str, int] = {}
        self.confirm_used = 0
        self.corrected: dict[str, Reply] = {}
        self.fired: set[str] = set()

    # ---- what to say ------------------------------------------------------------------------

    def _knockout_expected(self) -> bool:
        return self.typed.expect.outcome == "knockout" or any(
            i.at == "after_knockout" for i in self.typed.interrupts
        )

    def _next_unasked(self) -> str | None:
        covered = set(self.typed.opening.covers)
        return next((f for f in FIELD_ORDER if f not in covered and not self.asked.get(f)), None)

    def _confirm_reply(self) -> Reply:
        index = self.confirm_used
        self.confirm_used += 1
        correction = self.persona.moments.correction
        if index == 0 and correction is not None:
            reply = _correction_reply(correction, self.typed)
            self.corrected[correction.field] = reply
            return reply
        if index < len(self.typed.confirm):
            reply = self.typed.confirm[index]
            if reply.field is not None:
                self.corrected[reply.field] = reply
            return reply
        return Reply(text="Sí")

    def _next_reply(self, kind: str) -> tuple[Reply | None, str | None]:
        """The typed reply for `kind` and the utterance text the voice persona gives it."""
        if kind == "confirm":
            return self._confirm_reply(), None
        if kind in self.corrected:
            return self.corrected[kind], None
        index = self.asked.get(kind, 0)
        reply = typed_reply(self.typed, kind, index)
        if reply is None:
            return None, None
        self.asked[kind] = index + 1
        said = {str(k): v for k, v in self.persona.utterances.items()}.get(kind) or []
        text = said[index].text if index < len(said) else None
        return reply, text

    def _zone_pick(self, options: list[dict[str, Any]]) -> int:
        """`sim.driver._Session._zone_pick`: the persona's `zone_choice`, else the option
        sharing the most words with what it said about its Zone, else the first."""
        hint = self.typed.zone_choice
        if isinstance(hint, int) and 0 <= hint < len(options):
            return hint
        if isinstance(hint, str):
            needle = normalize(hint).strip()
            for index, option in enumerate(options):
                if needle in normalize(str(option.get("label", ""))):
                    return index
        correction = self.corrected.get("zone")
        if correction is not None:
            words = [correction.text, correction.query or ""]
        else:
            words = [r.text for r in self.typed.answers.get("zone", [])]
            if "zone" in self.typed.opening.covers:
                words.append(self.typed.opening.text)
        wanted = {w for w in re.findall(r"[a-z0-9]+", normalize(" ".join(words))) if len(w) > 2}
        best, best_score = 0, 0
        for index, option in enumerate(options):
            label = set(re.findall(r"[a-z0-9]+", normalize(str(option.get("label", "")))))
            score = len(wanted & label)
            if score > best_score:
                best, best_score = index, score
        return best

    def _utterance(
        self, kind: str, reply: Reply, text: str | None, interactive: dict[str, Any] | None
    ) -> str:
        """What is said: an option's label where the typed persona taps, else the words."""
        options = (interactive or {}).get("options") or []
        if is_tap(reply) and options:
            if text is not None:
                return text
            choice = reply.pick_option
            if isinstance(choice, int):
                option = options[choice] if 0 <= choice < len(options) else None
            else:
                needle = normalize(str(choice))
                option = next(
                    (o for o in options if needle in normalize(str(o.get("label", "")))), None
                )
            if option is not None:
                return str(option["label"])
        if kind == "zone" and zone_options(interactive) and not is_tap(reply):
            return str(options[self._zone_pick(options)]["label"])
        return text if text is not None else reply.text

    # ---- the loop ---------------------------------------------------------------------------

    def _say(self, utterance: str) -> tuple[StreamedTurn, dict[str, Any] | None]:
        turn, stored = self.d.say(self.run, utterance)
        latest = stored[-1] if stored else None
        return turn, latest

    def play(self) -> None:
        run = self.run
        turn, latest = self._say(self.typed.opening.text)
        pending: str | None = None
        last_sent_field: str | None = None
        reprompts = 0
        for _ in range(self.d.max_turns):
            if latest is None and not turn.text:
                run.ended = "no_reply"
                return
            interactive = (latest or {}).get("interactive")
            text = turn.text or (latest or {}).get("text", "")
            kind = detect_prompt(text, interactive)

            if (
                self._knockout_expected()
                and last_sent_field in KNOCKOUT_FIELDS
                and kind is None
                and not asks_something(text, interactive)
            ):
                run.ended = "knockout"
                return

            if kind is None:
                if (
                    asks_something(text, interactive)
                    and reprompts < self.d.max_reprompts
                    and (guess := pending or self._next_unasked()) is not None
                ):
                    kind = guess
                    reprompts += 1
                else:
                    run.ended = "completed"
                    return
            else:
                reprompts = 0

            optout = self.persona.moments.optout
            if optout is not None and kind == optout.at and "optout" not in self.fired:
                self.fired.add("optout")
                self._say(optout.phrase)
                self._say(optout.then)
                run.ended = "opted_out"
                return

            conflict = self.persona.moments.yes_no_conflict
            if (
                conflict is not None
                and kind == conflict.at
                and "yes_no_conflict" not in self.fired
                and _is_yes_no(interactive)
            ):
                self.fired.add("yes_no_conflict")
                self._say(conflict.text)
                self.asked[kind] = self.asked.get(kind, 0) + 1  # the typed reply, consumed
                pending, last_sent_field = kind, kind
                turn, latest = self._say(conflict.then)
                continue

            reply, said = self._next_reply(kind)
            if reply is None:
                run.ended = f"no_scripted_reply:{kind}"
                return
            utterance = self._utterance(kind, reply, said, interactive)
            pending = kind
            last_sent_field = None if kind == "confirm" else kind
            turn, latest = self._say(utterance)
        run.ended = "max_turns"


def _correction_reply(correction: Correction, typed: Persona) -> Reply:
    """The correction as the typed reply it replaces, with the typed `query` or `value` when
    the moment gives none."""
    first = typed.confirm[0] if typed.confirm else None
    inherited = first if first is not None and first.field == correction.field else None
    given = correction.model_fields_set
    update: dict[str, Any] = {"text": correction.text, "field": correction.field}
    if "query" in given or inherited is None:
        update["query"] = correction.query
    if "value" in given or inherited is None:
        update["value"] = correction.value
    base = inherited or Reply(text=correction.text, field=correction.field)
    return base.model_copy(update=update)


__all__ = [
    "COMPLETIONS_PATH",
    "HOUR_WORDS",
    "AfterTurn",
    "StreamedTurn",
    "VoiceDriver",
    "VoiceDriverError",
    "VoiceRun",
    "VoiceUnavailableError",
    "stream_turn",
]
