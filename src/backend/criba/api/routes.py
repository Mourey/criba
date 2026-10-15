"""The ``/api`` router for the Recruiter and Operator web app (``contracts/api.openapi.yaml``).

``router`` is mounted by ``app.py`` at ``/api`` and contains every route of this lane,
including the public booking page and the sim clock endpoints (which answer 404
``sim_disabled`` unless ``CRIBA_ENV=sim``). Mount it after the web chat router, which owns
``/api/chat``.
"""

from fastapi import APIRouter

from criba.api import (
    applications,
    auth,
    claims,
    conversations,
    metrics,
    queue,
    readings,
    sim,
    slots,
    takeover,
    trace,
)

router = APIRouter()
for _module in (
    auth,
    applications,
    claims,
    conversations,
    queue,
    readings,
    takeover,
    trace,
    slots,
    metrics,
    sim,
):
    router.include_router(_module.router)
router.include_router(slots.public_router)
