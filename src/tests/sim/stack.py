"""Helpers for runs against a live stack: the seed password, the Operator login, the record.

Shared by load mode (`sim.load`), the model comparison (`sim.compare`) and the demo test
(`src/tests/e2e/test_demo.py`). Everything goes through the public API, as the rest of the
simulator does.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from sim import asserts

OPERATOR = "operator@criba.example"


def seed_password(env: Mapping[str, str] | None = None) -> str:
    """The stack's seed password, from
    ``CRIBA_E2E_SEED_PASSWORD_FILE`` or ``CRIBA_E2E_SEED_PASSWORD``."""
    env = os.environ if env is None else env
    path = env.get("CRIBA_E2E_SEED_PASSWORD_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return env.get("CRIBA_E2E_SEED_PASSWORD", "")


async def login(http: httpx.AsyncClient, password: str, email: str = OPERATOR) -> None:
    """Log `http` in (session cookie). Raises RuntimeError on anything but 200."""
    if not password:
        raise RuntimeError(
            "no seed password: set CRIBA_E2E_SEED_PASSWORD_FILE (secrets/criba_seed_password.txt)"
        )
    response = await http.post("/api/auth/login", json={"email": email, "password": password})
    if response.status_code != 200:
        raise RuntimeError(f"login as {email} -> {response.status_code}: {response.text}")


async def is_sim_stack(http: httpx.AsyncClient) -> bool:
    """True when the backend serves the sim clock, i.e. runs with CRIBA_ENV=sim (ADR 0036)."""
    try:
        return (await http.get("/api/sim/clock", timeout=5)).status_code == 200
    except httpx.HTTPError:
        return False


async def settled_application(
    http: httpx.AsyncClient,
    phone: str,
    *,
    wait_s: float = 10.0,
    include_cost: bool = True,
) -> dict[str, Any]:
    """The Application for `phone` once the work after the last turn (the summary) has landed."""
    deadline = time.monotonic() + wait_s
    app = await asserts.find_application(http, phone, include_cost=include_cost)
    while time.monotonic() < deadline and app.get("summary") is None:
        await asyncio.sleep(0.5)
        app = await asserts.find_application(http, phone, include_cost=include_cost)
    return app


def new_phone(country: str, rng: random.Random | None = None) -> str:
    """A random simulated number for `country` (ES +346..., MX +5255...)."""
    digits = f"{(rng or random).randrange(10**8):08d}"
    return f"+346{digits}" if country == "ES" else f"+5255{digits}"


def usage_of(app: Mapping[str, Any]) -> dict[str, Any]:
    """The Operator-only `cost` block of an ApplicationDetail, with defaults when absent."""
    cost = dict(app.get("cost") or {})
    return {
        "runs": int(cost.get("runs") or 0),
        "models": list(cost.get("models") or []),
        "by_model": [dict(m) for m in cost.get("by_model") or []],
        "est_cost_usd": float(cost.get("est_cost_usd") or 0.0),
        "reconciled_cost_usd": cost.get("reconciled_cost_usd"),
        "p50_latency_ms": cost.get("p50_latency_ms"),
        "input_tokens": int(cost.get("input_tokens") or 0),
        "output_tokens": int(cost.get("output_tokens") or 0),
        "cache_read_tokens": int(cost.get("cache_read_tokens") or 0),
        "cache_write_tokens": int(cost.get("cache_write_tokens") or 0),
    }


__all__ = [
    "OPERATOR",
    "is_sim_stack",
    "login",
    "new_phone",
    "seed_password",
    "settled_application",
    "usage_of",
]
