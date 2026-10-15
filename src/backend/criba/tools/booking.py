"""Interview booking (spec 002 task 10, ADR 0030): offer_interview_slots, book_interview,
the slot lock, the signed booking link and the ``.ics`` invite.

- ``offer_interview_slots`` refuses with ``not_approved`` unless the Application is
  Approved in the database (Q41): a system turn carries no authority of its own.
- A slot is offered as an opaque ``slot_ref`` (``slot_offers``) valid only for that
  Application until it expires; ``book_interview`` accepts nothing else.
- The lock is the schema's unique index ``bookings_one_per_slot`` plus an IMMEDIATE
  transaction: of two Candidates racing for a slot, exactly one books it.
- :func:`book_slot` is the shared booking core, also for the public booking page
  (``booked_via='booking_page'``, lane W2-D).
- **The ``.ics`` invite.** On the web chat, ``book_interview`` reports ``ics_sent: true``
  and the channel attaches the invite to the agent's next message
  (:func:`chat_invite_attachments`), as a link to the public ``invite.ics`` endpoint signed
  with the booking-link token. The booking event records which message came last before
  the booking (``invite_after_message``), so the first agent message after it carries the
  invite. On WhatsApp ``ics_sent`` stays false: the Cloud API adapter is still a stub with
  no document message, so the invite goes out there once the real adapter sends documents
  (ADR 0027).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from criba.db import iso, new_id, parse_iso, repo, transaction
from criba.tools.common import (
    ToolError,
    interview_view,
    is_approved,
    language_of,
    require_agent_control,
    require_open,
    slot_interview,
)

SLOTS_OFFERED = 3
OFFER_TTL = timedelta(hours=48)  # a slot_ref stays bookable this long
MIN_LEAD = timedelta(hours=2)  # never offer a slot starting sooner than this
OTHER_OPTION_ID = "other"
OTHER_LABEL = {"es-ES": "Otro horario", "es-MX": "Otro horario", "en": "Another time"}
INVITE_FILENAME = "entrevista.ics"  # as served by GET /api/public/booking/{token}/invite.ics
INVITE_CHANNELS = frozenset({"webchat"})  # channels that deliver the invite today

BOOKING_LINK_DAYS_ENV = "CRIBA_BOOKING_LINK_DAYS"
DEFAULT_BOOKING_LINK_DAYS = 7.0
PUBLIC_BASE_URL_ENV = "CRIBA_PUBLIC_BASE_URL"
DEFAULT_PUBLIC_BASE_URL = "http://localhost:8080"
BOOKING_SECRET_FILE_ENV = "CRIBA_BOOKING_SECRET_FILE"
TOOLS_SECRET_FILE_ENV = "CRIBA_TOOLS_SECRET_FILE"


# ---------------------------------------------------------------------------
# Open slots
# ---------------------------------------------------------------------------


def open_slots(
    conn: sqlite3.Connection, application_id: str, *, now: datetime
) -> list[dict[str, Any]]:
    """Every bookable slot at the Application's covering Locations, soonest first.

    Bookable: not cancelled, not booked by anyone, starting at least ``MIN_LEAD`` from now.
    Rows carry ``slot_id``, ``location_id``, ``starts_at``, ``ends_at``,
    ``location_name``, ``address`` and ``timezone``.
    """
    rows = repo.open_slot_rows(conn, application_id, iso(now + MIN_LEAD))
    return [dict(r) for r in rows]


def offer_interview_slots(
    conn: sqlite3.Connection, app: dict[str, Any], args: dict[str, Any], now: datetime
) -> dict[str, Any]:
    require_agent_control(app)
    require_open(app)
    if not is_approved(app):
        raise ToolError(
            "not_approved",
            "This Application is not Approved yet, so no Interview can be offered. Tell the "
            "Candidate a Recruiter will review it soon; never promise an Interview.",
        )
    reschedule = bool(args.get("reschedule", False))
    if app["stage"] == "interview_booked" and not reschedule:
        raise ToolError(
            "wrong_stage",
            "An Interview is already booked (see get_application.interview). Offer new slots "
            "only if the Candidate asks to change it, with reschedule: true.",
        )
    language = language_of(app)
    chosen: list[dict[str, Any]] = []
    labels: set[str] = set()
    for row in open_slots(conn, app["id"], now=now):
        interview = slot_interview(row, language)
        if interview["local_label"] in labels:
            continue  # one button per distinct time: identical labels confuse the Candidate
        labels.add(interview["local_label"])
        chosen.append({**row, "interview": interview})
        if len(chosen) == SLOTS_OFFERED:
            break
    if not chosen:
        raise ToolError(
            "no_slots",
            "There are no open Interview slots at the Candidate's Locations right now. Tell "
            "them the team will share times soon, and raise_escalation(reason: other).",
        )
    expires = iso(now + OFFER_TTL)
    offered = []
    with transaction(conn):
        _expire_offers(conn, app["id"], now)
        for row in chosen:
            ref = f"so_{secrets.token_urlsafe(9)}"
            repo.insert_slot_offer(conn, ref, app["id"], row["slot_id"], iso(now), expires)
            offered.append(
                {
                    "slot_ref": ref,
                    "label": row["interview"]["local_label"],
                    "interview": row["interview"],
                }
            )
        repo.add_event(
            conn,
            candidate_id=app["candidate_id"],
            application_id=app["id"],
            kind="slots_offered",
            actor="agent",
            data={
                "slot_refs": [o["slot_ref"] for o in offered],
                "slot_ids": [r["slot_id"] for r in chosen],
                "reschedule": reschedule,
                "expires_at": expires,
            },
            now=now,
        )
    return {
        "slots": offered,
        "booking_url": booking_url(app["id"], now=now),
        "other_label": OTHER_LABEL.get(language, OTHER_LABEL["es-ES"]),
    }


def _expire_offers(conn: sqlite3.Connection, application_id: str, now: datetime) -> None:
    """End every outstanding offer: a newer offer or a booking supersedes them."""
    repo.expire_slot_offers(conn, application_id, iso(now))


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


def book_interview(
    conn: sqlite3.Connection, app: dict[str, Any], args: dict[str, Any], now: datetime
) -> dict[str, Any]:
    require_agent_control(app)
    require_open(app)
    offer = repo.offered_slot(conn, args["slot_ref"], app["id"], iso(now))
    if offer is None:
        raise ToolError(
            "slot_not_offered",
            "That slot_ref was not offered in this chat or has expired. Call "
            "offer_interview_slots for fresh options; use only the slot_ref of a tapped option.",
        )
    attach = app["channel"] in INVITE_CHANNELS and _can_sign()
    extra = None
    if attach:
        last = repo.max_message_id(conn, app["id"])
        extra = {"invite_after_message": int(last)}
    result = book_slot(conn, app["id"], offer["slot_id"], via="chat", now=now, event_extra=extra)
    return {
        "stage": "interview_booked",
        "interview": result["interview"],
        # True only where the channel attaches the invite to the agent's next message
        # (web chat). WhatsApp has no document delivery until the real adapter exists.
        "ics_sent": attach and result["new_booking"],
        "released_previous": result["released_previous"],
    }


def _can_sign() -> bool:
    try:
        booking_secret()
    except (RuntimeError, OSError):
        return False
    return True


def book_slot(
    conn: sqlite3.Connection,
    application_id: str,
    slot_id: str,
    *,
    via: Literal["chat", "booking_page"],
    now: datetime,
    event_extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Book ``slot_id`` for an Approved Application, releasing its previous booking.

    Returns ``{booking_id, ics_uid, interview, released_previous, rescheduled,
    new_booking}``; ``event_extra`` is merged into the booking event's data.
    Raises :class:`ToolError` with ``application_closed``, ``wrong_stage`` (not Approved),
    or ``slot_unavailable`` (cancelled, past, or booked by someone else). Booking the slot
    the Application already holds returns the current booking unchanged.
    """
    actor: repo.Actor = "agent" if via == "chat" else "candidate"
    ts = iso(now)
    with transaction(conn):
        app = repo.require_application(conn, application_id)
        require_open(app)
        if not is_approved(app):
            raise ToolError(
                "wrong_stage",
                "Booking needs an Approved Application. Call get_application and continue "
                "from what it says.",
            )
        slot = repo.slot_row(conn, slot_id)
        current = repo.current_booking(conn, application_id)
        if current is not None and current["slot_id"] == slot_id:
            return {
                "booking_id": current["id"],
                "ics_uid": current["ics_uid"],
                "interview": interview_view(conn, app),
                "released_previous": False,
                "rescheduled": False,
                "new_booking": False,
            }
        taken = repo.slot_is_booked(conn, slot_id)
        if slot is None or slot["cancelled_at"] or taken or parse_iso(slot["starts_at"]) <= now:
            raise ToolError(
                "slot_unavailable",
                "Someone else just took that slot, or it is no longer available. Apologise "
                "briefly and call offer_interview_slots for fresh options.",
            )
        common = {"candidate_id": app["candidate_id"], "application_id": application_id}
        if current is not None:
            repo.release_booking(conn, current["id"], ts)
            repo.add_event(
                conn,
                **common,
                kind="interview_slot_released",
                actor=actor,
                data={
                    "booking_id": current["id"],
                    "slot_id": current["slot_id"],
                    "reason": "rescheduled",
                },
                now=now,
            )
        booking_id = new_id("bk", now)
        ics_uid = f"{booking_id}@criba.local"
        try:
            repo.insert_booking(conn, booking_id, application_id, slot_id, via, ics_uid, ts)
        except sqlite3.IntegrityError as exc:  # the lock: bookings_one_per_slot
            raise ToolError(
                "slot_unavailable",
                "Someone else just took that slot. Apologise briefly and call "
                "offer_interview_slots for fresh options.",
            ) from exc
        if app["stage"] != "interview_booked":
            repo.set_stage(conn, application_id, "interview_booked", actor=actor, now=now)
        _expire_offers(conn, application_id, now)
        repo.add_event(
            conn,
            **common,
            kind="interview_rescheduled" if current is not None else "interview_booked",
            actor=actor,
            data={
                "booking_id": booking_id,
                "slot_id": slot_id,
                "location_id": slot["location_id"],
                "starts_at": slot["starts_at"],
                "via": via,
                "ics_uid": ics_uid,
                **({"previous_slot_id": current["slot_id"]} if current is not None else {}),
                **(event_extra or {}),
            },
            now=now,
        )
        booked = repo.require_application(conn, application_id)
        return {
            "booking_id": booking_id,
            "ics_uid": ics_uid,
            "interview": interview_view(conn, booked),
            "released_previous": current is not None,
            "rescheduled": current is not None,
            "new_booking": True,
        }


# ---------------------------------------------------------------------------
# .ics invite (RFC 5545)
# ---------------------------------------------------------------------------

_ICS_TEXT = {
    "es": ("Entrevista con Grupo Sazón", "Entrevista para repartidor en {location}."),
    "en": ("Interview with Grupo Sazón", "Delivery driver interview at {location}."),
}


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_fold(line: str) -> str:
    """Fold a content line at 75 octets, never splitting a UTF-8 character."""
    out: list[str] = []
    current = b""
    for ch in line:
        encoded = ch.encode("utf-8")
        limit = 75 if not out else 74  # continuation lines start with a space
        if len(current) + len(encoded) > limit:
            out.append(current.decode("utf-8"))
            current = b""
        current += encoded
    out.append(current.decode("utf-8"))
    return "\r\n ".join(out)


def _ics_time(value: str) -> str:
    return parse_iso(value).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def ics_for_booking(conn: sqlite3.Connection, booking_id: str) -> str:
    """The booking as an iCalendar file (``text/calendar``). A released booking is CANCELLED."""
    row = repo.booking_for_ics(conn, booking_id)
    if row is None:
        raise repo.NotFound(f"no booking {booking_id}")
    lang = "en" if language_of(dict(row)) == "en" else "es"
    summary, description = _ICS_TEXT[lang]
    cancelled = row["status"] != "booked"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Criba//Screening//EN",
        "CALSCALE:GREGORIAN",
        f"METHOD:{'CANCEL' if cancelled else 'PUBLISH'}",
        "BEGIN:VEVENT",
        f"UID:{row['ics_uid']}",
        f"DTSTAMP:{_ics_time(row['released_at'] or row['created_at'])}",
        f"DTSTART:{_ics_time(row['starts_at'])}",
        f"DTEND:{_ics_time(row['ends_at'])}",
        f"SUMMARY:{_ics_escape(f'{summary} · {row["location_name"]}')}",
        f"DESCRIPTION:{_ics_escape(description.format(location=row['location_name']))}",
        f"LOCATION:{_ics_escape(row['address'] or row['location_name'])}",
        f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}",
        f"SEQUENCE:{1 if cancelled else 0}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


def chat_invite_attachments(
    conn: sqlite3.Connection,
    message: Mapping[str, Any],
    *,
    now: datetime,
    secret: bytes | None = None,
) -> list[dict[str, str]]:
    """The web chat ``attachments`` of a stored message: the ``.ics`` invite on the first
    agent message after a chat booking (``invite_after_message`` in its event), while that
    booking still stands. The URL is the public ``invite.ics`` endpoint with a fresh
    booking-link token, so it needs no login and no second secret. Never raises."""
    app_id = message.get("application_id")
    if message.get("direction") != "outbound" or message.get("sender") != "agent" or not app_id:
        return []
    row = repo.latest_invite_booking_event(conn, app_id, message["id"])
    if row is None:
        return []
    data = json.loads(row["data"])
    first = repo.first_agent_message_after(conn, app_id, data["invite_after_message"])
    booked = repo.booking_is_standing(conn, data.get("booking_id"))
    if first != message["id"] or not booked:
        return []
    try:
        token = make_booking_token(app_id, now + booking_link_ttl(), secret=secret)
    except (RuntimeError, OSError):
        return []
    url = f"/api/public/booking/{token}/invite.ics"
    return [{"type": "ics", "url": url, "filename": INVITE_FILENAME}]


def active_booking_id(conn: sqlite3.Connection, application_id: str) -> str | None:
    return repo.current_booking_id(conn, application_id)


# ---------------------------------------------------------------------------
# Signed, expiring booking link (contracts/api.openapi.yaml: HMAC over id and expiry)
# ---------------------------------------------------------------------------


class BookingTokenError(Exception):
    """The booking token is malformed or its signature does not match (HTTP 404)."""


class BookingTokenExpired(BookingTokenError):
    """The booking token was valid but has expired (HTTP 410).

    Carries the Application id and expiry, so the page can answer in the Candidate's
    language.
    """

    def __init__(self, application_id: str, expires_at: datetime) -> None:
        super().__init__("booking link expired")
        self.application_id = application_id
        self.expires_at = expires_at


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def booking_secret(env: Mapping[str, str] | None = None) -> bytes:
    """The HMAC key for booking links: the tool's links and the public booking page.

    ``$CRIBA_BOOKING_SECRET_FILE`` if set; otherwise derived from the tools shared secret
    (``$CRIBA_TOOLS_SECRET_FILE``) with a fixed label, so the two never share a raw key.
    """
    env = os.environ if env is None else env
    own = env.get(BOOKING_SECRET_FILE_ENV)
    if own:
        return Path(own).read_text(encoding="utf-8").strip().encode()
    tools = env.get(TOOLS_SECRET_FILE_ENV)
    base = Path(tools).read_text(encoding="utf-8").strip() if tools else ""
    if not base:
        raise RuntimeError("no booking secret: set CRIBA_BOOKING_SECRET_FILE")
    return hmac.new(base.encode(), b"criba-booking-link", hashlib.sha256).digest()


def make_booking_token(
    application_id: str, expires_at: datetime, *, secret: bytes | None = None
) -> str:
    payload = _b64(
        json.dumps(
            {"app": application_id, "exp": int(expires_at.timestamp())}, separators=(",", ":")
        ).encode()
    )
    key = secret if secret is not None else booking_secret()
    signature = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def decode_booking_token(
    token: str, *, now: datetime, secret: bytes | None = None
) -> tuple[str, datetime]:
    """``(application_id, expires_at)`` for a valid token. Raises BookingTokenError / Expired."""
    try:
        payload, signature = token.split(".")
        key = secret if secret is not None else booking_secret()
        expected = _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            raise BookingTokenError("bad signature")
        data = json.loads(_unb64(payload))
        application_id, expiry = str(data["app"]), int(data["exp"])
        expires_at = datetime.fromtimestamp(expiry, UTC)
    except BookingTokenError:
        raise
    except (ValueError, KeyError, TypeError, OverflowError, OSError) as exc:
        raise BookingTokenError("malformed token") from exc
    if now.timestamp() >= expiry:
        raise BookingTokenExpired(application_id, expires_at)
    return application_id, expires_at


def verify_booking_token(token: str, *, now: datetime, secret: bytes | None = None) -> str:
    """The Application id the token was issued for. Raises BookingTokenError / Expired."""
    return decode_booking_token(token, now=now, secret=secret)[0]


def booking_link_ttl(environ: Mapping[str, str] | None = None) -> timedelta:
    """How long a booking link (and the invite link on the confirmation) stays valid.

    The one place ``CRIBA_BOOKING_LINK_DAYS`` is read (default 7 days): the agent tools and
    the API's Recruiter link both call this, so the two lifetimes cannot drift apart.
    """
    env = os.environ if environ is None else environ
    raw = (env.get(BOOKING_LINK_DAYS_ENV) or "").strip()
    try:
        days = float(raw) if raw else DEFAULT_BOOKING_LINK_DAYS
    except ValueError:
        raise ValueError(f"{BOOKING_LINK_DAYS_ENV} must be a number of days, got {raw!r}") from None
    if not days > 0:
        raise ValueError(f"{BOOKING_LINK_DAYS_ENV} must be positive, got {raw!r}")
    return timedelta(days=days)


def booking_url(application_id: str, *, now: datetime, secret: bytes | None = None) -> str:
    base = (os.environ.get(PUBLIC_BASE_URL_ENV) or DEFAULT_PUBLIC_BASE_URL).rstrip("/")
    token = make_booking_token(application_id, now + booking_link_ttl(), secret=secret)
    return f"{base}/book/{token}"
