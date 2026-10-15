"""Agent tools served to the Hermes criba-tools plugin (spec 002 tasks 8-11, ADR 0026).

Public API for other lanes:

- :func:`pending_choice` (W2-B sender, Q40): the options to attach to the agent's last
  message.
- :func:`tap_answer` (ADR 0043, 0050): records a tap on a pending answer option, or books a
  tapped slot, before the agent runs and returns the result the agent receives for it.
- :func:`call_tool`: run one tool for a session, as the HTTP router does.
- :func:`book_slot`, :func:`open_slots`, :func:`ics_for_booking`,
  :func:`make_booking_token`, :func:`verify_booking_token`, :func:`decode_booking_token`,
  :func:`booking_url`, :func:`booking_secret`: the one booking-link format and key, used by
  the tool's links and W2-D's public booking page alike.
- :func:`knockout_message`: the approved Knockout template text.
"""

from criba.tools.booking import (
    BookingTokenError,
    BookingTokenExpired,
    active_booking_id,
    book_slot,
    booking_secret,
    booking_url,
    decode_booking_token,
    ics_for_booking,
    make_booking_token,
    open_slots,
    verify_booking_token,
)
from criba.tools.choices import Interactive, InteractiveOption, pending_choice, tap_answer
from criba.tools.common import ToolError
from criba.tools.dispatch import TOOLS, call_tool
from criba.tools.knockout import knockout_message

__all__ = [
    "TOOLS",
    "BookingTokenError",
    "BookingTokenExpired",
    "Interactive",
    "InteractiveOption",
    "ToolError",
    "active_booking_id",
    "book_slot",
    "booking_secret",
    "booking_url",
    "call_tool",
    "decode_booking_token",
    "ics_for_booking",
    "knockout_message",
    "make_booking_token",
    "open_slots",
    "pending_choice",
    "tap_answer",
    "verify_booking_token",
]
