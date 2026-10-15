"""The pending choice (Q40): which tap-to-answer options go with the agent's last message.

After each run the sender (lane W2-B) attaches ``pending_choice()`` to the agent's last
outbound message as ``interactive``; the agent writes only the question text and never
retypes options. Derived from stored records only (no table of its own), in priority order:

1. unexpired ``slot_offers`` whose slot nobody has booked: the offered slots, plus the
   "other time" option, which leads to the booking link. Once the Application holds a
   booking, only a reschedule offer made after that booking counts
   (``offer_interview_slots(reschedule=true)``): the confirmation of a booking carries no
   slot buttons, whatever the clocks say about the older offers' expiry;
2. the options of the newest ``answer_ambiguous`` event that is open: its field is still
   unanswered, or, for a correction at the confirm step, was not recorded again since
   (``record_answer``'s ``needs_choice`` writes it with ``{field, options}``; ADR 0048); an
   older open one returns when a newer one for another field closes (ADR 0054);
3. the fixed option set of ``next_field``: availability as buttons, preferred schedule as
   a list, driver's licence yes/no as buttons, the platforms as a list ending with "no app"
   (ADR 0053), labelled in the Candidate's language.

Otherwise None. A tapped option reaches the agent as its ``id`` (the router passes it as the
run input, ``channel.router.run_input``). The agent puts it in its field's structured shape
for ``record_answer`` (ADR 0042): the licence's ``yes`` or ``no`` as is; an availability,
schedule or platform code, or a Zone id, inside a list; the ``no`` of a Zone confirmation as
``null``. A ``slot_ref`` goes to ``book_interview``. Nothing here depends on the shape.

A tap on an answer option is recorded by the backend before the agent runs (ADR 0043):
:func:`tap_answer` maps the id to that field's structured value, calls ``record_answer`` and
returns the result as the agent's input, so the agent only obeys it. A tap on an offered slot
is booked the same way, through ``book_interview`` (ADR 0050). A tap on the "other time" option
gets the booking link (ADR 0064): the agent's input carries the link and the message to send, and
the router makes sure the link reaches the chat whatever the agent writes. A tap on the Zone funnel
(ADR 0068) is an answer option too: a city is recorded as that city's Zones (the same offer as a
city-only search, ADR 0062), "none of these" as a Zone answer of ``null`` that the funnel turns
into the Zone Knockout. Any tap this cannot map still reaches the agent as the bare id.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Literal, TypedDict

from criba.channel.copy import booking_link_message
from criba.db import iso, repo, utc_now
from criba.screening import field_options
from criba.screening.vocab import NO_PLATFORM
from criba.tools import funnel
from criba.tools.booking import OTHER_LABEL, OTHER_OPTION_ID, booking_url
from criba.tools.common import (
    FIELD_ORDER,
    ToolError,
    field_missing,
    is_approved,
    language_of,
    next_field,
)

TAP_RESULT_PREFIX = "[tap result] "  # the agent's input for a tap the backend already recorded
BOOKING_LINK_STATUS = "booking_link"  # the "other time" tap's result (ADR 0064)
LIST_FIELDS = frozenset({"availability", "preferred_schedule", "experience_platforms"})

MAX_BUTTONS = 3  # WhatsApp reply buttons; more options go in a list (up to 10)
MAX_ZONE_IDS = 10  # what record_answer's Zone value may carry (contracts/tools.json)

# Fixed option sets offered for next_field, and their style.
_FIELD_STYLE: dict[str, Literal["buttons", "list"]] = {
    "driver_license": "buttons",
    "availability": "buttons",
    "preferred_schedule": "list",
    "experience_platforms": "list",  # ends with the "no app" row (ADR 0053)
}


class InteractiveOption(TypedDict):
    id: str
    label: str


class Interactive(TypedDict):
    """``Interactive`` in ``contracts/webchat.openapi.yaml`` (and ``messages.interactive``)."""

    style: Literal["buttons", "list"]
    options: list[InteractiveOption]


def pending_choice(
    conn: sqlite3.Connection, application_id: str, *, now: datetime | None = None
) -> Interactive | None:
    """The options the Candidate can tap now, or None. Read-only.

    ``now`` (default: wall-clock time) decides which slot offers have expired; pass the
    ``Clock``'s time in the simulator.
    """
    app = repo.get_application(conn, application_id)
    if app is None or app["status"] != "open" or app["control"] != "agent":
        return None
    language = language_of(app)
    return (
        _slot_choice(conn, app["id"], language, now or utc_now())
        or _ambiguous_choice(conn, app)
        or _field_choice(app, language)
    )


def _reschedule_refs(conn: sqlite3.Connection, application_id: str) -> set[str] | None:
    """None if the Application holds no booking (every live offer counts); otherwise the
    ``slot_ref`` values of reschedule offers made after its latest booking (maybe empty)."""
    if repo.current_booking_id(conn, application_id) is None:
        return None
    rows = repo.slots_offered_since_booking(conn, application_id)
    refs: set[str] = set()
    for row in rows:
        data = json.loads(row["data"])
        if data.get("reschedule"):
            refs.update(data.get("slot_refs") or [])
    return refs


def _slot_choice(
    conn: sqlite3.Connection, application_id: str, language: str, now: datetime
) -> Interactive | None:
    allowed = _reschedule_refs(conn, application_id)
    if allowed is not None and not allowed:
        return None
    rows = repo.live_offer_rows(conn, application_id, iso(now))
    if allowed is not None:
        rows = [r for r in rows if r["ref"] in allowed]
    if not rows:
        return None
    options: list[InteractiveOption] = [
        {"id": r["ref"], "label": repo.local_label(r["starts_at"], r["timezone"], language)}
        for r in rows
    ]
    options.append({"id": OTHER_OPTION_ID, "label": OTHER_LABEL.get(language, "Otro horario")})
    style: Literal["buttons", "list"] = "buttons" if len(options) <= MAX_BUTTONS else "list"
    return {"style": style, "options": options}


def _ambiguous_event(conn: sqlite3.Connection, app: dict[str, Any]) -> dict[str, Any] | None:
    """The newest ambiguity's data (``{field, options, confirm?}``) that is still open.

    Open: it is its field's latest ambiguity, and it came after any Reopen and after that
    field's last recorded answer, during Screening. That covers a field still unanswered and
    a correction at the confirm step (ADR 0048): a Zone already recorded, re-asked with new
    options, keeps its buttons and its taps are recorded like the first time. When the newest
    ambiguity is closed (its field was recorded, or a No cleared it), an older one for another
    field that is still open comes back: a start-date choice the agent's Zone correction
    displaced in the same turn returns once the Zone is tapped (ADR 0054).
    """
    rows = repo.ambiguity_events_since_reopen(conn, app["id"])
    seen: set[str] = set()
    for row in rows:
        data: dict[str, Any] = json.loads(row["data"])
        field = data.get("field")
        if field in seen or field not in FIELD_ORDER:
            continue
        seen.add(field)
        if _still_open(conn, app, field, data, row["id"]):
            return data
    return None


def _still_open(
    conn: sqlite3.Connection, app: dict[str, Any], field: str, data: dict[str, Any], event_id: int
) -> bool:
    if not data.get("options"):
        return False
    if field_missing(app, field):
        return True
    answered_after = repo.answer_recorded_after(conn, app["id"], field, event_id)
    return not answered_after and app["stage"] in repo.SCREENING_STAGES


def _ambiguous_choice(conn: sqlite3.Connection, app: dict[str, Any]) -> Interactive | None:
    data = _ambiguous_event(conn, app)
    if data is None:
        return None
    clean: list[InteractiveOption] = [{"id": o["id"], "label": o["label"]} for o in data["options"]]
    style: Literal["buttons", "list"] = "buttons" if len(clean) <= MAX_BUTTONS else "list"
    return {"style": style, "options": clean}


def _field_choice(app: dict[str, Any], language: str) -> Interactive | None:
    if app["stage"] not in repo.SCREENING_STAGES:
        return None
    pending = next_field(app)
    if pending not in _FIELD_STYLE:
        return None
    options: list[InteractiveOption] = [
        {"id": o.id, "label": o.label} for o in field_options(pending, language)
    ]
    return {"style": _FIELD_STYLE[pending], "options": options}


# ---------------------------------------------------------------------------
# A tap on an answer option, recorded by the backend (ADR 0043)
# ---------------------------------------------------------------------------


def _tap_value(
    conn: sqlite3.Connection, app: dict[str, Any], option_id: str
) -> tuple[str, Any] | None:
    """``(field, structured value)`` for a tap on one of the pending answer options, or None.

    The same options ``pending_choice`` attaches (its steps 2 and 3), each in the shape
    ``record_answer`` takes (ADR 0042): the id inside a list for the list fields and the Zone,
    the licence's ``yes`` or ``no`` as is, and the ``no`` of a Zone confirmation as ``null``.
    """
    data = _ambiguous_event(conn, app)
    if data is not None:
        field = data["field"]
        if option_id not in {o["id"] for o in data["options"]}:
            return None
        if funnel.is_funnel(data):
            # The Zone funnel (ADR 0068): a city leads to its Zones, "none of these" is a Zone
            # answer that maps to nothing.
            if option_id == funnel.NO_CITY_OPTION_ID:
                return field, None
            ids = funnel.city_zone_ids(conn, option_id)
            return None if ids is None else (field, ids)
        if field == "zone" and "confirm" in data and option_id != data["confirm"]:
            return field, None
        return field, [option_id] if field in LIST_FIELDS | {"zone"} else option_id
    choice = _field_choice(app, language_of(app))
    pending = next_field(app)
    if choice is None or pending is None or option_id not in {o["id"] for o in choice["options"]}:
        return None
    if pending == "experience_platforms" and option_id == NO_PLATFORM:
        return pending, []  # no app (ADR 0053)
    return pending, [option_id] if pending in LIST_FIELDS else option_id


def tap_answer(
    conn: sqlite3.Connection, application_id: str, option_id: str, *, now: datetime
) -> str | None:
    """Record a tap on an answer option and return what the agent receives for it, or None.

    The id is the Candidate's whole answer, so no model has to read it: the backend calls
    ``record_answer`` with the field's structured value, exactly as the agent would
    (verification, Knockouts, the answer event with this message's id), and the agent gets
    the tool result after ``[tap result] `` to obey like any other result: ask ``next_field``,
    send a Knockout's ``message``, or re-ask the Zone.

    A tap on an offered slot is booked the same way (ADR 0050): ``book_interview`` with the
    tapped ``slot_ref``, so the booking never depends on the model reading an opaque id, and
    the agent confirms the Interview from the result.

    A tap on the "other time" option is answered with the booking link (ADR 0064):
    ``{"ok": true, "value": {"status": "booking_link", "booking_url", "message"}}``, the message
    being the fixed copy in the Candidate's language. It counts while a slot offer is live, and
    for an Approved Application with no Interview whose offer has expired (the buttons of an old
    message still sit in the chat); never on a voice Application (ADR 0059). Anything that is
    not a pending option is left to the agent as the bare id: None.
    """
    from criba.tools.dispatch import call_tool

    app = repo.get_application(conn, application_id)
    if app is None or app["status"] != "open" or app["control"] != "agent":
        return None
    slots = _slot_choice(conn, app["id"], language_of(app), now)
    if option_id == OTHER_OPTION_ID and _link_applies(app, slots):
        return _tap_result(_booking_link_body(app, now))
    if slots is not None:
        if option_id == OTHER_OPTION_ID or option_id not in {o["id"] for o in slots["options"]}:
            return None  # "other time" here: a voice Application, left to the agent (ADR 0059)
        _, body = call_tool(conn, "book_interview", {"slot_ref": option_id}, app["id"], now=now)
        return _tap_result(body)
    if app["stage"] not in repo.SCREENING_STAGES:
        return None
    picked = _tap_value(conn, app, option_id)
    if picked is None:
        return None
    field, value = picked
    if field == "zone" and isinstance(value, list) and len(value) > MAX_ZONE_IDS:
        # A funnel city with more Zones than a call may carry (ADR 0068).
        return _tap_result(_oversized_city_result(conn, value, language_of(app)))
    _, body = call_tool(conn, "record_answer", {"field": field, "value": value}, app["id"], now=now)
    return _tap_result(body)


def _oversized_city_result(
    conn: sqlite3.Connection, ids: list[str], language: str
) -> dict[str, Any]:
    """A city with more Zones than one list holds: the Candidate is asked for the neighbourhood in
    text, as a city-only search does (ADR 0062); a narrowing step, so no miss."""
    from criba.screening.zones import city_zones
    from criba.tools.zones_lookup import load_zones

    wanted = set(ids)
    invalid = city_zones([z for z in load_zones(conn) if z.id in wanted], language)
    return ToolError("invalid_value", invalid.hint, reason=invalid.reason).body()


def _link_applies(app: dict[str, Any], slots: Interactive | None) -> bool:
    """Whether "other time" is answered with the booking link: a slot offer is live, or the
    Application is Approved with no Interview yet (the booking page has something to offer).
    Never on a voice Application (ADR 0059): no channel carries a link to a Candidate on a
    call, and the Voice Screener tells them someone from the team will contact them."""
    if app["channel"] == "voice":
        return False
    return slots is not None or (is_approved(app) and app["stage"] == "approved")


def _booking_link_body(app: dict[str, Any], now: datetime) -> dict[str, Any]:
    url = booking_url(app["id"], now=now)
    message = booking_link_message(url, app.get("language"), app.get("country"))
    value = {"status": BOOKING_LINK_STATUS, "booking_url": url, "message": message}
    return {"ok": True, "value": value}


def booking_link_of(tap_input: str) -> tuple[str, str] | None:
    """``(booking_url, message)`` when ``tap_input`` is the agent's input for an "other time" tap
    (:func:`tap_answer`), else None. The router uses it to guarantee the link (ADR 0064)."""
    if not tap_input.startswith(TAP_RESULT_PREFIX):
        return None
    try:
        body = json.loads(tap_input[len(TAP_RESULT_PREFIX) :])
    except ValueError:
        return None
    value = body.get("value") if isinstance(body, dict) else None
    if not isinstance(value, dict) or value.get("status") != BOOKING_LINK_STATUS:
        return None
    url, message = value.get("booking_url"), value.get("message")
    return (url, message) if isinstance(url, str) and isinstance(message, str) else None


def _tap_result(body: dict[str, Any]) -> str:
    return TAP_RESULT_PREFIX + json.dumps(body, ensure_ascii=False, separators=(",", ":"))
