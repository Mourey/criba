"""``criba-tools``: the Screening Agent's tools, proxied to the Criba backend.

Each tool in ``contracts/tools.json`` is registered with its contract schema. When the model
calls one, the handler forwards ``{tool, args, session_id}`` to the backend, which resolves
the Application from ``session_id`` (ADR 0026). ``session_id`` is supplied by the Hermes
dispatcher and equals the Runs API ``session_id`` (spike 2, findings 1 and 2), so identity
never comes from an argument the model supplies.

Hermes facts (image ``nousresearch/hermes-agent:v0.21.6``):
- ``tools/registry.py`` ``ToolRegistry.dispatch`` passes ``task_id`` and ``session_id`` to a
  handler only if its signature accepts them, so the handler declares both.
- ``model_tools.py`` ``_execute_tool`` builds those kwargs from the running agent's session.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from . import client, contract, history_trim, prompt_trim, turn_date

logger = logging.getLogger("criba_tools")

TOOLSET = "criba-tools"


def make_handler(tool: str) -> Callable[..., str]:
    """Build the handler for one tool. One closure per tool, never ``functools.partial``:
    the registry inspects the handler's signature to decide which kwargs to pass."""

    def handle(
        args: dict[str, Any],
        task_id: str | None = None,
        session_id: str | None = None,
        **_: Any,
    ) -> str:
        return client.forward(tool, args, session_id)

    handle.__name__ = f"criba_tool_{tool}"
    return handle


def register(ctx: Any) -> None:
    specs = contract.tool_specs()
    for spec in specs:
        ctx.register_tool(
            name=spec.name,
            toolset=TOOLSET,
            schema=spec.schema(),
            handler=make_handler(spec.name),
            description=spec.description,
        )
    # Drop the Hermes boilerplate no config key turns off (prompt_trim.py). A context without
    # middleware support (an older Hermes, a test double) just keeps the longer prompt.
    register_middleware = getattr(ctx, "register_middleware", None)
    if callable(register_middleware):
        register_middleware("llm_request", prompt_trim.llm_request)
        # Shrink what finished turns left in the history (history_trim.py).
        register_middleware("llm_request", history_trim.llm_request)
        # Today's date in the Candidate's local calendar on every call (turn_date.py, ADR 0055).
        register_middleware("llm_request", turn_date.llm_request)
    logger.info(
        "criba-tools registered %d tools: %s",
        len(specs),
        ", ".join(s.name for s in specs),
    )
