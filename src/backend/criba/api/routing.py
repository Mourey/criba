"""Router factory with the API's error handling and the role-aware response filter.

``new_router`` returns an ``APIRouter`` whose routes (a) turn :class:`ApiError`,
repository errors and request-validation errors into the contract's ``Error`` JSON,
and (b) remove Operator-only fields from every JSON response and every server-sent event
frame sent to a Recruiter, through the one function :func:`strip_operator_fields`.
Both live on the route class, so they travel with the routers when the integrator
mounts them and need no application-level handlers.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterable, AsyncIterator, Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from criba.api.errors import ApiError, from_repo_error
from criba.db.repo import RepoError

log = logging.getLogger("criba.api")

# Cost fields (contracts/api.openapi.yaml, properties marked ``x-roles: [operator]``).
# Recruiters never receive them: absent from the JSON, not null (ADR 0019).
OPERATOR_ONLY_KEYS = frozenset({"cost", "cost_usd", "est_cost_usd", "reconciled_cost_usd"})

_HTTP_CODES = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "not_found",
    422: "validation_error",
}


def strip_operator_fields(value: Any) -> Any:
    """Return ``value`` without the Operator-only keys, at any depth."""
    if isinstance(value, dict):
        return {
            k: strip_operator_fields(v) for k, v in value.items() if k not in OPERATOR_ONLY_KEYS
        }
    if isinstance(value, list):
        return [strip_operator_fields(v) for v in value]
    return value


def strip_operator_fields_from_frames(chunk: str | bytes | memoryview) -> str | bytes:
    """Return SSE text with Operator-only keys removed from each ``data:`` line's JSON.

    Uses :func:`strip_operator_fields`, the same filter as JSON responses. Other lines
    (``id:``, ``event:``, comments, blank separators) pass through unchanged, and so does a
    ``data:`` line that is not JSON.
    """
    text = chunk if isinstance(chunk, str) else bytes(chunk).decode()
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not line.startswith("data:"):
            continue
        raw = line[5:].removeprefix(" ")
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        cleaned = json.dumps(
            strip_operator_fields(value), ensure_ascii=False, separators=(",", ":")
        )
        lines[i] = f"data: {cleaned}"
    out = "\n".join(lines)
    return out if isinstance(chunk, str) else out.encode()


async def _filtered_frames(
    body: AsyncIterable[str | bytes | memoryview],
) -> AsyncIterator[str | bytes]:
    async for chunk in body:
        yield strip_operator_fields_from_frames(chunk)


def _validation_response(exc: RequestValidationError) -> Response:
    details = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
        message = str(err.get("msg", "")).removeprefix("Value error, ")
        details.append({"field": ".".join(loc) or "body", "message": message})
    message = details[0]["message"] if details else "Invalid request."
    return ApiError(422, "validation_error", message, details).response()


def _filter(request: Request, response: Response) -> Response:
    user = getattr(request.state, "user", None)
    if user is None or user.role == "operator":
        return response
    if isinstance(response, StreamingResponse):
        if "text/event-stream" in (response.media_type or ""):
            # Each chunk the stream helpers yield is whole frames, so lines never split.
            response.body_iterator = _filtered_frames(response.body_iterator)
        return response
    body = getattr(response, "body", None)
    if not body or "json" not in (response.media_type or ""):
        return response
    cleaned = json.dumps(
        strip_operator_fields(json.loads(body)), ensure_ascii=False, separators=(",", ":")
    ).encode()
    response.body = cleaned
    response.headers["content-length"] = str(len(cleaned))
    return response


class CribaRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except ApiError as exc:
                response = exc.response()
            except RepoError as exc:
                response = from_repo_error(exc).response()
            except RequestValidationError as exc:
                response = _validation_response(exc)
            except StarletteHTTPException as exc:
                code = _HTTP_CODES.get(exc.status_code, "internal")
                response = ApiError(exc.status_code, code, str(exc.detail)).response()
            except Exception:
                log.exception("unhandled error in %s %s", request.method, request.url.path)
                response = ApiError(500, "internal", "Unexpected error.").response()
            return _filter(request, response)

        return handler


def new_router(**kwargs: Any) -> APIRouter:
    return APIRouter(route_class=CribaRoute, **kwargs)
