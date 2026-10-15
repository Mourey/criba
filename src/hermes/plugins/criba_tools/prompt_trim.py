"""Drops the Hermes boilerplate no profile setting can turn off from every model request.

The system prompt Hermes builds ends with blocks that a client-facing screening agent
has no use for, about 2,100 characters on every model call (docs/evidence/turn-size.md):

- "You run on Hermes Agent (by Nous Research) ... documentation at ...", a pointer to the
  Hermes docs (agent/prompt_builder.py ``HERMES_AGENT_HELP_GUIDANCE_NO_SKILLS``);
- "## Mid-turn user steering", the briefing for /steer messages, which the Runs API never
  delivers (agent/prompt_builder.py ``STEER_CHANNEL_NOTE``);
- "Active Hermes profile: ...", which names the profile home and the default profile's
  directories (agent/system_prompt.py ``_active_profile_line``);
- "Conversation started: <date> ... Model: ... Provider: ... Platform: ..." (agent/system_prompt.py
  ``_timestamp_line``); the date comes from ``get_application.today``;
- the "# Hermes runtime environment" block with the host OS, home, working and scratch
  directories (agent/prompt_builder.py ``build_environment_hints``); a host path once leaked
  into a reply (spike 1, gotcha 3).

None of them has a config key in v0.21.6 (the two that do, task completion and parallel tool
call guidance, are off in the profile config). This module registers ``llm_request``
middleware (hermes_cli/middleware.py ``apply_llm_request_middleware``) that removes them from
the system message just before the request goes out. It fails open twice over: a block whose
text a later Hermes changes simply stays, and a callback that raises is skipped by
hermes_cli/plugins_dispatch.py ``invoke_middleware``. The sim suite checks that none of the
blocks reaches the stub model (``test_scenarios.py``, ``_prompt_leaks``).
"""

from __future__ import annotations

import re
from typing import Any

# Paragraphs (split on a blank line) that start with one of these are dropped.
DROPPED_PARAGRAPH_PREFIXES = (
    "You run on Hermes Agent (by Nous Research).",
    "## Mid-turn user steering",
    "Active Hermes profile:",
    "Conversation started:",
)

# The runtime block is delimited by Hermes's own markers (agent/prompt_builder.py
# ``RUNTIME_ENVIRONMENT_HEADING`` and ``RUNTIME_ENVIRONMENT_END``).
_RUNTIME_BLOCK = re.compile(
    r"# Hermes runtime environment\n.*?<!-- End Hermes runtime environment -->\s*",
    re.DOTALL,
)

SOURCE = "criba-tools"


def trim_system_text(text: str) -> str:
    """The system prompt without the blocks above; the rest is left byte for byte."""
    without_runtime = _RUNTIME_BLOCK.sub("", text)
    paragraphs = without_runtime.split("\n\n")
    kept = [p for p in paragraphs if not p.lstrip().startswith(DROPPED_PARAGRAPH_PREFIXES)]
    if without_runtime == text and len(kept) == len(paragraphs):
        return text
    return "\n\n".join(p for p in kept if p.strip()).strip()


def _trim_content(content: Any) -> Any:
    """A message or ``system`` value: a string, or a list of text blocks (the layout Hermes
    uses when it marks the prompt for caching, with other keys such as ``cache_control``)."""
    if isinstance(content, str):
        return trim_system_text(content)
    if isinstance(content, list):
        trimmed = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text = trim_system_text(part["text"])
                if text.strip():
                    trimmed.append({**part, "text": text})
                # a block that held nothing else but a dropped block is left out
            else:
                trimmed.append(part)
        return trimmed
    return content


def trim_request(request: dict[str, Any]) -> dict[str, Any]:
    """The request with its system message trimmed (chat completions ``messages[0]`` or a
    top-level ``system``, the Anthropic layout); everything else is untouched."""
    out = dict(request)
    messages = request.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict) and first.get("role") in ("system", "developer"):
            out["messages"] = [
                {**first, "content": _trim_content(first.get("content"))},
                *messages[1:],
            ]
    if "system" in request:
        out["system"] = _trim_content(request["system"])
    return out


def llm_request(request: dict[str, Any] | None = None, **_: Any) -> dict[str, Any] | None:
    """``llm_request`` middleware: return the trimmed request, or None when nothing changed."""
    if not isinstance(request, dict):
        return None
    trimmed = trim_request(request)
    if trimmed == request:
        return None
    return {"request": trimmed, "source": SOURCE, "reason": "drop Hermes boilerplate"}
