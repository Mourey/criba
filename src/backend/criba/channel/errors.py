"""The safety net of the channel routers: no request ends in a bare 500 or FastAPI's own
``{"detail": ...}`` (case requirement "graceful failures, no crashes on unexpected input").

:func:`guarded_route` builds the route class of a router outside ``/api`` (the web chat, the
voice custom-LLM endpoint and the post-call webhook). Its handler turns, in this order:

* a request-validation error (a query parameter that is not an integer, say) into the
  surface's 422;
* SQLite busy or locked (another writer held the database past ``busy_timeout``) into the
  surface's retry answer;
* any other exception into the surface's failure answer, with the traceback logged here and
  nothing of it, nor any Candidate text, in the response.

Each surface passes its own answers, so every body keeps its contract's shape. The ``/api``
routers have their own route class (:class:`criba.api.routing.CribaRoute`); ``app.py``
installs the last net for whatever escapes both.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

log = logging.getLogger("criba.channel")

# SQLite's INTEGER is a signed 64-bit value: a larger id or cursor cannot name a row, and
# binding one raises OverflowError.
INT64_MAX = 2**63 - 1


def is_busy(exc: BaseException) -> bool:
    """True for SQLite's "database is locked" or "database is busy"."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def validation_message(exc: RequestValidationError) -> str:
    """The first problem as ``<where>: <message>``, as the web chat reports a bad body."""
    errors = exc.errors()
    if not errors:
        return "Invalid request."
    first = errors[0]
    where = ".".join(str(p) for p in first.get("loc", ()) if p not in ("query", "path", "body"))
    return f"{where or 'request'}: {first.get('msg', 'invalid')}"


def guarded_route(
    *,
    invalid: Callable[[RequestValidationError], Response],
    busy: Callable[[], Response],
    failed: Callable[[], Response],
) -> type[APIRoute]:
    """A route class whose handler answers with ``invalid``, ``busy`` or ``failed`` instead of
    raising (see the module docstring)."""

    class GuardedRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
            original = super().get_route_handler()

            async def handler(request: Request) -> Response:
                try:
                    return await original(request)
                except RequestValidationError as exc:
                    return invalid(exc)
                except HTTPException:
                    raise  # an answer already: the app's handler writes it
                except Exception as exc:
                    if is_busy(exc):
                        log.warning("database busy in %s %s", request.method, request.url.path)
                        return busy()
                    log.exception("unhandled error in %s %s", request.method, request.url.path)
                    return failed()

            return handler

    return GuardedRoute


__all__ = ["INT64_MAX", "guarded_route", "is_busy", "validation_message"]
