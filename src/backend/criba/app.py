"""FastAPI application: health check plus the web chat, ``/api`` and internal-tools routers.

Router mounting and wiring are owned by the integrator. Lanes add routes inside their
own modules (``criba.api``, ``criba.channel``, ``criba.tools``); this module mounts them,
and at startup:

1. runs the sim safety check (Q39, :func:`criba.api.sim.check_sim_settings`);
2. builds one :class:`criba.wiring.Runtime` (one shared clock for the channel, the
   scheduler, the tools router and the API context);
3. runs the scheduler loop for the app's lifetime.

Mount order: the web chat router at ``/api/chat`` comes before the ``/api`` router, so the
general API never shadows the chat paths (``contracts/webchat.openapi.yaml``); the voice
post-call webhook router and the custom-LLM router at ``/api/voice`` come before it for the
same reason (``contracts/voice.openapi.yaml``).

Errors: each router answers its own failures in its contract's shape (``CribaRoute`` for
``/api``, :func:`criba.channel.errors.guarded_route` for the chat and voice routers, the
dispatcher for the tools). :func:`unexpected_error` is the last net for whatever escapes them:
a JSON 500 in the surface's shape, never a stack trace or Candidate text.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from criba.api.routes import router as api_router
from criba.api.sim import check_sim_settings
from criba.channel.voice import voice_router
from criba.channel.voice_webhook import voice_webhook_router
from criba.channel.webchat import chat_router
from criba.db import iso
from criba.scheduler import running
from criba.tools.http import get_after_call, get_clock
from criba.tools.http import router as tools_router
from criba.wiring import Runtime, build_runtime

log = logging.getLogger("criba.app")

TOOLS_PREFIX = "/internal/tools"


async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """An exception no router answered: logged with its traceback, answered with a JSON 500.
    The tools proxy gets the contract's ``tool_error`` (``contracts/tools.json``); every other
    path the ``Error`` shape both ``/api`` contracts share."""
    log.error("unhandled error in %s %s", request.method, request.url.path, exc_info=exc)
    if request.url.path.startswith(TOOLS_PREFIX + "/"):
        body = {"ok": False, "error": "internal", "hint": "Unexpected backend error."}
        return JSONResponse(body, status_code=500)
    return JSONResponse({"error": "internal", "message": "Unexpected error."}, status_code=500)


def install_runtime(app: FastAPI, runtime: Runtime) -> None:
    """Point every router at the runtime's shared services and clock."""
    app.state.criba_runtime = runtime
    app.state.criba_channel = runtime.channel
    app.state.api_context = runtime.api_context
    # setdefault: an override a test installed first wins.
    app.dependency_overrides.setdefault(get_clock, lambda: runtime.clock)
    app.dependency_overrides.setdefault(get_after_call, lambda: runtime.after_tool_call)


def create_app(runtime_factory: Callable[[], Runtime] | None = None) -> FastAPI:
    factory = runtime_factory or build_runtime

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        check_sim_settings()
        runtime = factory()
        install_runtime(app, runtime)
        async with running(runtime.scheduler, runtime.scheduler_interval_s):
            yield

    app = FastAPI(title="Criba backend", version="0.1.0", lifespan=lifespan)
    app.add_exception_handler(Exception, unexpected_error)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/alarms", tags=["health"])
    def alarms() -> dict[str, list[dict[str, Any]]]:
        """The open system alarms (ADR 0078): codes and times, for the deployment's monitor.
        Internal like ``/health``: the web proxy forwards only ``/api``."""
        runtime: Runtime | None = getattr(app.state, "criba_runtime", None)
        open_alarms = runtime.channel.router.alarms.open() if runtime else []
        return {
            "alarms": [
                {"cause": a.cause, "sources": list(a.sources), "since": iso(a.since)}
                for a in open_alarms
            ]
        }

    app.include_router(chat_router, prefix="/api/chat")
    app.include_router(voice_webhook_router, prefix="/api/voice")
    app.include_router(voice_router, prefix="/api/voice")
    app.include_router(api_router, prefix="/api")
    app.include_router(tools_router, prefix=TOOLS_PREFIX)
    return app


app = create_app()
