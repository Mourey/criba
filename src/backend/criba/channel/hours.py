"""The WhatsApp 24-hour customer-service window.

Outside it, only an approved template may be sent; web chat has no window (spec 002,
decided 2026-10-09). Quiet hours are not here: the one rule lives in
:mod:`criba.scheduler.quiet`, and the sender imports it from there.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from criba.db.connection import parse_iso

WHATSAPP_WINDOW = timedelta(hours=24)


def inside_whatsapp_window(application: dict[str, Any], now: datetime) -> bool:
    """True when the Candidate wrote within the last 24 hours."""
    last = application.get("last_inbound_at")
    if not last:
        return False
    return now - parse_iso(last) < WHATSAPP_WINDOW
