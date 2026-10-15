"""Sim mode: the startup safety check and the test-only clock endpoints (spec 004 task 3).

**Q39 safety check.** Sim mode is one switch (``docker-compose.sim.yml``) that sets
``CRIBA_ENV=sim`` and points the backend at a ``-sim`` Hermes profile whose model is the
stub. The two settings must agree: ``CRIBA_ENV=sim`` with a real profile would let a test
rig spend real money, and a ``-sim`` profile without ``CRIBA_ENV=sim`` would run production
traffic on the stub. :func:`check_sim_settings` refuses to start in either case; the
integrator calls it once from ``app.py`` startup.

**Clock endpoints** (``contracts/webchat.openapi.yaml``, ``/api/sim/clock*``) answer
404 ``sim_disabled`` unless ``CRIBA_ENV=sim``. Advancing the clock then runs every
scheduler job now due (lane W2-C's entry point, ``ApiContext.run_due_jobs``) before it
responds, so a caller can read the effects at once.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from typing import Annotated, Any
from urllib.parse import urlparse

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from criba.api.auth import Ctx
from criba.api.clock import SimClock
from criba.api.errors import ApiError, validation_error
from criba.api.routing import new_router

SIM_PROFILE_SUFFIX = "-sim"


class SimSettingsError(RuntimeError):
    """``CRIBA_ENV`` and ``CRIBA_HERMES_PROFILE`` disagree about sim mode, or sim mode points the
    Jev router at a real service."""


def _is_local_host(url: str) -> bool:
    """True for a host that is not on the public internet: a Compose service name (no dot) or
    loopback. The stub Jev lives at one of those; the real service never does."""
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and ("." not in host or host in ("localhost", "127.0.0.1", "::1"))


def check_sim_settings(env: Mapping[str, str] | None = None) -> None:
    """Raise :class:`SimSettingsError` unless sim mode is on for both settings or for neither."""
    source = os.environ if env is None else env
    sim_env = source.get("CRIBA_ENV", "").strip().lower() == "sim"
    profile = source.get("CRIBA_HERMES_PROFILE", "").strip()
    sim_profile = profile.endswith(SIM_PROFILE_SUFFIX)
    if sim_env and not sim_profile:
        raise SimSettingsError(
            "CRIBA_ENV=sim requires a -sim Hermes profile, but CRIBA_HERMES_PROFILE is "
            f"{profile or 'unset'!r}. Refusing to start: sim traffic must never reach a real model."
        )
    if sim_profile and not sim_env:
        raise SimSettingsError(
            f"CRIBA_HERMES_PROFILE={profile!r} is a sim profile but CRIBA_ENV is not 'sim'. "
            "Refusing to start: production traffic must never run on the stub model."
        )
    voice_profile = source.get("CRIBA_HERMES_VOICE_PROFILE", "").strip()
    if voice_profile:
        sim_voice = voice_profile.endswith(SIM_PROFILE_SUFFIX)
        if sim_env and not sim_voice:
            raise SimSettingsError(
                "CRIBA_ENV=sim requires a -sim voice profile, but CRIBA_HERMES_VOICE_PROFILE is "
                f"{voice_profile!r}. Refusing to start: sim traffic must never reach a real model."
            )
        if sim_voice and not sim_env:
            raise SimSettingsError(
                f"CRIBA_HERMES_VOICE_PROFILE={voice_profile!r} is a sim profile but CRIBA_ENV is "
                "not 'sim'. Refusing to start: production traffic must never run on the stub model."
            )
    jev_url = source.get("CRIBA_JEV_URL", "").strip()
    if sim_env and jev_url and not _is_local_host(jev_url):
        raise SimSettingsError(
            "CRIBA_ENV=sim requires the Jev router to use the stub server, but CRIBA_JEV_URL "
            "points at a public host. Refusing to start: sim traffic must never reach the real "
            "Jev service."
        )


# ---------------------------------------------------------------------------
# Clock endpoints
# ---------------------------------------------------------------------------

router = new_router(tags=["sim"])
_advance_lock = threading.Lock()


class AdvanceClockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: Annotated[int | None, Field(ge=1, le=7_776_000)] = None
    to: AwareDatetime | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> AdvanceClockRequest:
        if (self.seconds is None) == (self.to is None):
            raise ValueError("Send exactly one of seconds or to.")
        return self


def _sim_clock(ctx: Any) -> SimClock:
    if not ctx.settings.is_sim:
        raise ApiError(404, "sim_disabled", "The sim clock is available only when CRIBA_ENV=sim.")
    clock = ctx.clock
    if not isinstance(clock, SimClock):
        raise ApiError(500, "internal", "CRIBA_ENV=sim, but the shared clock is not a SimClock.")
    return clock


def _state(clock: SimClock, due_jobs_run: int = 0) -> dict[str, Any]:
    from criba.db.connection import iso

    return {
        "now": iso(clock.now()),
        "offset_seconds": clock.offset_seconds,
        "due_jobs_run": due_jobs_run,
    }


@router.get("/sim/clock")
def get_sim_clock(ctx: Ctx) -> dict[str, Any]:
    return _state(_sim_clock(ctx))


@router.post("/sim/clock/advance")
def advance_sim_clock(body: AdvanceClockRequest, ctx: Ctx) -> dict[str, Any]:
    clock = _sim_clock(ctx)
    with _advance_lock:
        try:
            if body.seconds is not None:
                clock.advance(body.seconds)
            else:
                assert body.to is not None
                clock.advance_to(body.to)
        except ValueError as exc:
            raise validation_error(str(exc), "to") from None
        ran = ctx.run_due_jobs() if ctx.run_due_jobs else 0
        return _state(clock, int(ran or 0))


@router.post("/sim/clock/reset")
def reset_sim_clock(ctx: Ctx) -> dict[str, Any]:
    clock = _sim_clock(ctx)
    with _advance_lock:
        clock.reset()
        return _state(clock)
