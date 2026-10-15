"""Shrinks what earlier turns left in the history of every model request (turn-size.md).

Everything Hermes keeps in a session is replayed on every model call. A screening is one
Candidate message per turn, and each turn leaves tool results and the model's hidden reasoning
behind. Both are only useful while the turn that made them is running:

- A tool result is read and acted on in its own turn (``get_application`` says what to ask,
  ``record_answer`` says what is next, ``search_zones`` lists the Zones to record). The next
  turn starts with a fresh ``get_application``, and the skill says to trust it over the chat.
  Left whole, a nine-row Zone list, an ``ambiguous_zone`` error that repeats it and a dozen
  ``get_application`` results made up about a third of the longest call.
- ``reasoning_details`` on an assistant message is the signed record of the model's hidden
  reasoning, which OpenRouter replays on every later call (agent/transports/chat_completions.py:
  ``_route_replays_reasoning_details``). On real Haiku 5.5 it was about 1,400 characters of
  signature per reasoning turn. Only the turn in progress needs it: the provider rejects a tool
  call whose reasoning is missing, but ignores that of finished turns.

This module registers ``llm_request`` middleware (hermes_cli/middleware.py
``apply_llm_request_middleware``) that, on the copy of the request that goes out, rewrites the
messages before the latest user message: a tool result becomes the few keys that say what it
was, plus a trace of what the agent passed on to the Candidate from it (ADR 0050: a text sent
word for word, the offered slots, the booked Interview), and the reasoning keys of an assistant
message are dropped. It never touches the stored
session (the Hermes history keeps everything), the turn in progress, user and assistant text,
or the tool calls themselves, so the model still sees which tool it called with which
arguments. It is idempotent and stable from turn to turn: once an old message is rewritten it
is rewritten the same way every later call, so the provider's prompt cache keeps its prefix
from there on.

It fails open: a message of a shape it does not know (content that is not JSON, a list of
non-text blocks) is left alone, and a callback that raises is skipped by
hermes_cli/plugins_dispatch.py ``invoke_middleware``.
"""

from __future__ import annotations

import json
from typing import Any

SOURCE = "criba-tools"

# A result at or under this size is already as short as its summary.
MIN_COMPACT_CHARS = 90

# Assistant keys that carry hidden reasoning (OpenRouter and OpenAI-compatible routes).
REASONING_KEYS = ("reasoning_details", "reasoning_content", "reasoning")

# Keys of a result that stay: the outcome, what was recorded, and why a call failed.
_KEPT_TOP = ("ok", "error", "reason", "attempt")
_KEPT_VALUE = ("status", "field", "stage")
# The choice a Candidate may still answer in words next turn (a Zone confirmation, two or three
# Zones, a fixed field's options) stays when it is short; a ten-row Zone list does not (the
# skill searches again, and the model's own record_answer call keeps the ids it sent).
_CHOICE_KEYS = ("options", "confirm")
MAX_CHOICE_CHARS = 200

TAP_RESULT_PREFIX = "[tap result] "

# What the agent passed on to the Candidate keeps a trace in its result (ADR 0050). Without it a
# later turn shows the model its own reply (a Knockout's requirements link, an Interview's time and
# place) after a result that no longer says any of it, and the model takes it back as made up.
# A text sent word for word becomes this marker; the facts of slots and an Interview stay.
SENT_MARKER = "(approved text from the backend, sent word for word in your reply)"
_INTERVIEW_KEYS = ("local_label", "location_name", "address")
# A slot keeps its ref, so a Candidate who names a slot in words later can still be booked.
_SLOT_KEYS = ("slot_ref", "label")


def _relayed(value: dict[str, Any]) -> dict[str, Any]:
    """The trace of what a result gave the agent to pass on: ``message`` as :data:`SENT_MARKER`,
    the offered slots' refs, labels and Locations, the Interview's label, place and address, and
    ``ics_sent``. Already-compacted input comes back the same (the rewrite is idempotent)."""
    out: dict[str, Any] = {}
    if isinstance(value.get("message"), str):
        out["message"] = SENT_MARKER
    slots = value.get("slots")
    if isinstance(slots, list):
        kept: list[Any] = []
        places: list[Any] = list(value.get("locations") or [])  # already compacted
        for slot in slots:
            if not isinstance(slot, dict):
                kept.append(slot)
                continue
            kept.append({k: slot[k] for k in _SLOT_KEYS if k in slot})
            interview = slot.get("interview")
            place = interview.get("location_name") if isinstance(interview, dict) else None
            if place and place not in places:
                places.append(place)
        out["slots"] = kept
        out["locations"] = places
    interview = value.get("interview")
    if isinstance(interview, dict):
        out["interview"] = {k: interview[k] for k in _INTERVIEW_KEYS if k in interview}
    if "ics_sent" in value:
        out["ics_sent"] = value["ics_sent"]
    return out


def _short_choice(source: dict[str, Any]) -> dict[str, Any]:
    """``options`` and ``confirm`` of a result, when the options are short enough to keep."""
    options = source.get("options")
    if options is None or len(json.dumps(options, ensure_ascii=False)) > MAX_CHOICE_CHARS:
        return {}
    return {k: source[k] for k in _CHOICE_KEYS if k in source}


def compact_result(text: str) -> str:
    """A tool result (or a ``[tap result]`` JSON) reduced to its outcome.

    ``{"ok":true,"value":{"status":"accepted","field":"zone","next_field":...}}`` becomes
    ``{"ok":true,"value":{"status":"accepted","field":"zone"}}``; a failure keeps ``error``,
    ``reason`` and ``attempt`` and loses the hint; a short list of ``options`` (and the
    ``confirm`` id that goes with it) stays, a long one goes. What the agent passed on to the
    Candidate keeps a trace (:func:`_relayed`, ADR 0050). Text that is not a JSON object, or is
    already short, comes back unchanged.
    """
    if len(text) <= MIN_COMPACT_CHARS:
        return text
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if not isinstance(data, dict):
        return text
    out: dict[str, Any] = {k: data[k] for k in _KEPT_TOP if k in data}
    out.update(_short_choice(data))
    value = data.get("value")
    if isinstance(value, dict):
        kept = {k: value[k] for k in _KEPT_VALUE if k in value}
        # a status already names the outcome; the stage is kept only when nothing else says it
        if "status" in kept:
            kept.pop("stage", None)
        kept.update(_short_choice(value))
        kept.update(_relayed(value))
        out["value"] = kept
    compact = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    return compact if len(compact) < len(text) else text


def _compact_content(content: Any, *, prefix: str = "") -> Any:
    """``content`` of a message with its text compacted: a string, or a list of text blocks
    (the layout Hermes uses when it marks a message for caching, other keys kept)."""
    if isinstance(content, str):
        if prefix:
            if not content.startswith(prefix):
                return content
            return prefix + compact_result(content[len(prefix) :])
        return compact_result(content)
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append({**part, "text": _compact_content(part["text"], prefix=prefix)})
            else:
                parts.append(part)
        return parts
    return content


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str)
        )
    return ""


def trim_history(messages: list[Any]) -> list[Any]:
    """``messages`` with the turns before the latest user message shrunk (see the module doc)."""
    last_user = max(
        (i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "user"),
        default=-1,
    )
    if last_user <= 0:
        return messages
    out: list[Any] = []
    for index, message in enumerate(messages):
        if index >= last_user or not isinstance(message, dict):
            out.append(message)
            continue
        role = message.get("role")
        if role == "tool" and "content" in message:
            changed = {**message, "content": _compact_content(message["content"])}
        elif role == "user" and _content_text(message.get("content")).startswith(TAP_RESULT_PREFIX):
            changed = {
                **message,
                "content": _compact_content(message["content"], prefix=TAP_RESULT_PREFIX),
            }
        elif role == "assistant" and any(k in message for k in REASONING_KEYS):
            changed = {k: v for k, v in message.items() if k not in REASONING_KEYS}
        else:
            out.append(message)
            continue
        out.append(changed)
    return out


def trim_request(request: dict[str, Any]) -> dict[str, Any]:
    """The request with its chat-completions ``messages`` shrunk; the rest is untouched."""
    messages = request.get("messages")
    if not isinstance(messages, list):
        return request
    trimmed = trim_history(messages)
    if trimmed == messages:
        return request
    return {**request, "messages": trimmed}


def llm_request(request: dict[str, Any] | None = None, **_: Any) -> dict[str, Any] | None:
    """``llm_request`` middleware: return the trimmed request, or None when nothing changed."""
    if not isinstance(request, dict):
        return None
    trimmed = trim_request(request)
    if trimmed is request or trimmed == request:
        return None
    return {"request": trimmed, "source": SOURCE, "reason": "shrink finished turns"}
