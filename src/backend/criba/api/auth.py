"""Auth: seeded users, session cookie, roles and Location scope (spec 003 task 1).

A session is an opaque random token in the ``criba_session`` cookie (HttpOnly,
SameSite=Lax). The database keeps only its SHA-256 (``auth_sessions.id``), so a leaked
database holds no usable session. Passwords are argon2 hashes; seeding is
``criba.db.seed.seed_users`` with a password the caller supplies.

``current_user`` is the one dependency every protected route takes. It also records the
user on ``request.state`` so the route class can remove Operator-only fields from the
response (:mod:`criba.api.routing`).
"""

from __future__ import annotations

import contextlib
import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Annotated, Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from criba.api.context import ApiContext, get_context
from criba.api.errors import forbidden, unauthorized
from criba.api.routing import new_router
from criba.db import repo
from criba.db.connection import iso

COOKIE_NAME = "criba_session"

router = new_router(tags=["auth"])

_hasher = PasswordHasher()
_dummy_hash: str | None = None


@dataclass
class CurrentUser:
    id: str
    name: str
    email: str
    role: str
    client_id: str | None
    location_ids: list[str] = field(default_factory=list)

    @property
    def is_operator(self) -> bool:
        return self.role == "operator"

    def as_json(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "email": self.email, "role": self.role}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_location_ids(conn: sqlite3.Connection, user_id: str) -> list[str]:
    return repo.user_location_ids(conn, user_id)


def load_session_user(ctx: ApiContext, token: str | None) -> CurrentUser | None:
    if not token:
        return None
    with ctx.db() as conn:
        row = repo.session_user_row(conn, hash_token(token), ctx.session_clock.now())
        if row is None:
            return None
        return CurrentUser(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            role=row["role"],
            client_id=row["client_id"],
            location_ids=[] if row["role"] == "operator" else user_location_ids(conn, row["id"]),
        )


def current_user(request: Request, ctx: Annotated[ApiContext, Depends(get_context)]) -> CurrentUser:
    user = load_session_user(ctx, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise unauthorized()
    request.state.user = user
    return user


def require_operator(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
    if not user.is_operator:
        raise forbidden("Operators only.")
    return user


User = Annotated[CurrentUser, Depends(current_user)]
Operator = Annotated[CurrentUser, Depends(require_operator)]
Ctx = Annotated[ApiContext, Depends(get_context)]


def location_refs(conn: sqlite3.Connection, ids: list[str] | None = None) -> list[dict[str, Any]]:
    """LocationRef objects, for the given ids or for every active Location."""
    if ids is not None and not ids:
        return []
    return [dict(r) for r in repo.location_ref_rows(conn, ids)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: Annotated[str, Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")]
    password: Annotated[str, Field(min_length=1, max_length=1024)]


def _me(conn: sqlite3.Connection, user: CurrentUser) -> dict[str, Any]:
    return {
        "user": user.as_json(),
        "locations": [] if user.is_operator else location_refs(conn, user.location_ids),
    }


def _verify(password_hash: str | None, password: str) -> bool:
    """Verify in roughly constant time whether or not the user exists."""
    global _dummy_hash
    if password_hash is None:
        if _dummy_hash is None:
            _dummy_hash = _hasher.hash(secrets.token_urlsafe(16))
        with contextlib.suppress(VerificationError):
            _hasher.verify(_dummy_hash, password)
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@router.post("/auth/login")
def login(body: LoginRequest, response: Response, ctx: Ctx) -> dict[str, Any]:
    email = body.email.strip().lower()
    # Keyed by email only: behind nginx every client shares the proxy's address.
    key = f"email:{email}"
    now = time.monotonic()
    if ctx.login_throttle.blocked(key, now):
        raise unauthorized("Too many attempts. Try again later.")
    with ctx.db() as conn:
        row = repo.active_user_by_email(conn, email)
        if not _verify(row["password_hash"] if row else None, body.password) or row is None:
            ctx.login_throttle.fail(key, now)
            raise unauthorized("Invalid email or password.")
        ctx.login_throttle.clear(key)
        token = secrets.token_urlsafe(32)
        created = ctx.session_clock.now()
        ttl = ctx.settings.session_ttl
        repo.insert_auth_session(
            conn, hash_token(token), row["id"], iso(created), iso(created + ttl)
        )
        user = CurrentUser(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            role=row["role"],
            client_id=row["client_id"],
            location_ids=[] if row["role"] == "operator" else user_location_ids(conn, row["id"]),
        )
        payload = _me(conn, user)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=ctx.settings.cookie_secure,
        path="/",
    )
    return payload


@router.post("/auth/logout", status_code=204)
def logout(request: Request, ctx: Ctx) -> Response:
    """End the session. Idempotent: no cookie or an unknown one still answers 204."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with ctx.db() as conn:
            repo.revoke_auth_session(conn, hash_token(token), iso(ctx.session_clock.now()))
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/auth/me")
def get_me(user: User, ctx: Ctx) -> dict[str, Any]:
    with ctx.db() as conn:
        return _me(conn, user)


@router.get("/locations")
def list_locations(
    user: User, ctx: Ctx, country: Annotated[str | None, Query(pattern="^(ES|MX)$")] = None
) -> dict[str, Any]:
    with ctx.db() as conn:
        if not user.is_operator and not user.location_ids:
            return {"items": []}
        ids = None if user.is_operator else user.location_ids
        return {"items": [dict(r) for r in repo.visible_location_rows(conn, ids, country)]}


__all__ = [
    "COOKIE_NAME",
    "Ctx",
    "CurrentUser",
    "Operator",
    "User",
    "current_user",
    "hash_token",
    "location_refs",
    "require_operator",
    "router",
]
