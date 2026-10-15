"""A Candidate who is clearly awake: the exemption from quiet hours for the Approval message.

ADR 0046 (refines 0038, 0039): the one proactive message that goes out during quiet hours is
the action after Approval (``held_actions.action = 'approved'``), and only when the Candidate
wrote on that Application within the last ``ACTIVE_CANDIDATE_WINDOW`` of the clock's now.
Nothing else is exempt: Stalled nudges, the 48-hour template and every other proactive
message keep the strict rule in :mod:`criba.scheduler.quiet`, which this module leaves alone.

The window and the test live here and nowhere else. The scheduler uses it to dispatch at
once or on the next run instead of holding; the router passes the result to the sender's
guard, so the channel's ``not_now`` does not undo it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from criba.db.connection import parse_iso

ACTIVE_CANDIDATE_WINDOW = timedelta(minutes=60)


def candidate_recently_active(application: dict[str, Any], now: datetime) -> bool:
    """True when the Candidate's last inbound message on this Application is at most
    ``ACTIVE_CANDIDATE_WINDOW`` old (inclusive: exactly 60 minutes still counts).

    An Application the Candidate never wrote on is not active.
    """
    last = application.get("last_inbound_at")
    if not last:
        return False
    return now - parse_iso(last) <= ACTIVE_CANDIDATE_WINDOW
