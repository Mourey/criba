"""API errors in the contract's ``Error`` shape (``contracts/api.openapi.yaml``).

Handlers raise :class:`ApiError`; repository errors (``NotFound``, ``Conflict``,
``Forbidden``, each with a stable ``code``) are translated by :func:`from_repo_error`.
The route class in :mod:`criba.api.routing` turns both into JSON responses, so the
routers work without the application installing exception handlers.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from criba.db.repo import Conflict, Forbidden, NotFound, RepoError

# Codes of the contract's Error.error enum.
ERROR_CODES = frozenset(
    {
        "unauthorized",
        "forbidden",
        "not_found",
        "validation_error",
        "already_claimed",
        "not_claim_holder",
        "already_resolved",
        "wrong_stage",
        "application_closed",
        "control_held",
        "not_in_control",
        "slot_taken",
        "slot_booked",
        "link_expired",
        "outside_whatsapp_window",
        "internal",
        # Sim clock endpoints (contracts/webchat.openapi.yaml).
        "sim_disabled",
    }
)

# Repository conflict codes that name a contract code differently.
_CONFLICT_CODES = {
    "claimed": "already_claimed",
    "not_qualified": "wrong_stage",
    "not_in_screening": "wrong_stage",
    "not_reopenable": "wrong_stage",
    "reopen_window_passed": "wrong_stage",
    "application_open": "wrong_stage",
}


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        assert code in ERROR_CODES, code
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def body(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out

    def response(self) -> JSONResponse:
        return JSONResponse(self.body(), status_code=self.status)


def unauthorized(message: str = "Log in to continue.") -> ApiError:
    return ApiError(401, "unauthorized", message)


def forbidden(message: str = "Not allowed.", code: str = "forbidden") -> ApiError:
    return ApiError(403, code, message)


def not_found(message: str = "Not found.") -> ApiError:
    return ApiError(404, "not_found", message)


def conflict(code: str, message: str) -> ApiError:
    return ApiError(409, code, message)


def gone(message: str) -> ApiError:
    return ApiError(410, "link_expired", message)


def validation_error(message: str, field: str | None = None) -> ApiError:
    details = [{"field": field, "message": message}] if field else None
    return ApiError(422, "validation_error", message, details)


def from_repo_error(exc: RepoError) -> ApiError:
    """Translate a repository error into the contract's error, keeping its message."""
    message = str(exc)
    if isinstance(exc, NotFound):
        return ApiError(404, "not_found", message)
    if isinstance(exc, Forbidden):
        if exc.code == "not_claim_holder":
            return ApiError(403, "not_claim_holder", message)
        if exc.code == "out_of_scope":
            return ApiError(404, "not_found", "Not found.")
        return ApiError(403, "forbidden", message)
    if isinstance(exc, Conflict):
        code = _CONFLICT_CODES.get(exc.code, exc.code)
        if code not in ERROR_CODES:
            code = "wrong_stage"
        return ApiError(409, code, message)
    return ApiError(500, "internal", "Unexpected error.")
