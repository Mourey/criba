"""Screening tools (spec 002 tasks 8-9): get_application, record_answer, request_review,
raise_escalation, finish_screening, record_backout.

Each tool takes ``(conn, app, args, now)``: ``app`` is the Application resolved from the
session, ``args`` already matches the contract's input schema. A tool returns the
contract's ``<tool>_output`` value or raises :class:`ToolError`. Code verifies every
answer's structured value (``criba.screening.verify``, ADR 0042) and decides Knockouts
(ADR 0026). The Candidate's words never reach a validator: a value of the wrong shape is
``invalid_args`` before the tool runs (``criba.tools.dispatch``).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from criba.channel.copy import qualified_close
from criba.db import repo, transaction
from criba.jev.config import jev_enabled
from criba.jev.policy import TONE_HINT_TEXT
from criba.screening import (
    Accepted,
    Invalid,
    Knockout,
    NeedsChoice,
    ValidationResult,
    ZoneRef,
    candidate_timezone,
    field_options,
    local_today,
    verify_value,
)
from criba.screening.verify import verify_zone
from criba.screening.zones import place_word
from criba.tools import funnel, zone_searches
from criba.tools.common import (
    FIELD_COLUMN,
    FIELD_ORDER,
    FixedClock,
    ToolError,
    answers_view,
    field_missing,
    first_name_of,
    interview_view,
    is_approved,
    language_of,
    next_field,
    require_agent_control,
    require_open,
    screening_stage,
)
from criba.tools.knockout import knockout_message
from criba.tools.zones_lookup import load_zones

ESCALATE_AFTER_MISSES = 3  # Phase 1 section 4.2: the third miss raises an Escalation
OPTIONS_FROM_MISS = 2  # ... and the second switches to buttons or a list
_TAKEOVER_SUMMARY_MESSAGES = 6
_TAKEOVER_SUMMARY_CHARS = 200
SPANISH_BY_COUNTRY = {"ES": "es-ES", "MX": "es-MX"}
KNOCKOUT_FIELDS = ("zone", "driver_license")  # their *_input columns keep the Candidate's words
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


# ---------------------------------------------------------------------------
# get_application
# ---------------------------------------------------------------------------


def get_application(
    conn: sqlite3.Connection, app: dict[str, Any], args: dict[str, Any], now: datetime
) -> dict[str, Any]:
    language = _country_language(args.get("language"), app["country"])
    if language is not None and language != app.get("language") and not jev_enabled():
        # Without the Jev router the agent reports a clear language switch, never a one-word
        # answer (ADRs 0042, 0047); stored before the record is read, so this turn already sees
        # it. With the router on, the backend decides the language before the run and the
        # report is ignored (ADR 0051): the result below carries the stored language, and the
        # agent replies in that.
        repo.set_candidate_language(conn, app["candidate_id"], language, now=now)
        app = repo.require_application(conn, app["id"])
    in_screening = app["status"] == "open" and app["stage"] in repo.SCREENING_STAGES
    today = local_today(FixedClock(now), _local_zone(conn, app))
    last_agent = _last_agent_message_at(conn, app["id"])
    # The result stays in the model's history for the rest of the session, so a key that says
    # nothing (null, false, 0) is left out and the Screener skill reads an absent key as null
    # or false (docs/evidence/turn-size.md). `next_field` stays: null means all answered.
    # `answers` rides only then (the confirm step needs the summary): while screening,
    # `next_field` is all the model needs, and the answers it recorded are already in its
    # history, so repeating them in every result grew the context with each question.
    following = next_field(app) if in_screening else None
    view: dict[str, Any] = {
        "stage": app["stage"],
        "status": app["status"],
        "outcome": app["outcome"],
        "knockout_criterion": app["knockout_criterion"],
        "control": app["control"],
        "language": language_of(app),
        "country": app["country"],
        "first_name": first_name_of(app),
        "answers": answers_view(conn, app) if following is None else None,
        "invalid_attempts": app["invalid_attempts"],
        "reopened": _pending_event(conn, app["id"], "reopened", last_agent) is not None,
        "takeover_summary": _takeover_summary(conn, app["id"], last_agent),
        "tone_hint": _tone_hint(conn, app["id"], last_agent),
        "approved": is_approved(app),
        "interview": interview_view(conn, app),
    }
    result = {key: value for key, value in view.items() if value not in (None, False, 0)}
    result["next_field"] = following
    result["today"] = today.isoformat()
    result["weekday"] = WEEKDAYS[today.weekday()]
    return result


def _country_language(language: str | None, country: str | None) -> str | None:
    """Spanish follows the Application's country (es-ES in Spain, es-MX in Mexico)."""
    if language in ("es-ES", "es-MX") and country in SPANISH_BY_COUNTRY:
        return SPANISH_BY_COUNTRY[country]
    return language


def _local_zone(conn: sqlite3.Connection, app: dict[str, Any]) -> ZoneInfo:
    """The Candidate's time zone: their Zone's, else their country's (else UTC)."""
    if app.get("zone_id"):
        timezone = repo.zone_timezone(conn, app["zone_id"])
        if timezone is not None:
            return ZoneInfo(timezone)
    return candidate_timezone(None, app.get("country"))


def _last_agent_message_at(conn: sqlite3.Connection, application_id: str) -> str | None:
    return repo.last_agent_message_at(conn, application_id)


def _pending_event(
    conn: sqlite3.Connection, application_id: str, kind: str, last_agent: str | None
) -> dict[str, Any] | None:
    """The latest ``kind`` event, if the agent has not written since (one turn only)."""
    row = repo.latest_event(conn, application_id, kind)
    if row is None or (last_agent is not None and last_agent >= row["created_at"]):
        return None
    return {"id": row["id"], "data": json.loads(row["data"]), "created_at": row["created_at"]}


def _tone_hint(conn: sqlite3.Connection, application_id: str, last_agent: str | None) -> str | None:
    """The tone hint for this turn, as text for the agent, or None.

    The Jev router writes a ``tone_hint`` event holding a code and the triggering message's id
    (ADR 0051, 0035); the text comes from :data:`criba.jev.policy.TONE_HINT_TEXT`. The hint
    lasts until the agent has replied: no agent message after the Candidate's message that
    caused it. An older event with a ``hint`` text and no message id keeps its original rule
    (no agent message since the event)."""
    row = repo.latest_tone_hint(conn, application_id)
    if row is None:
        return None
    data = json.loads(row["data"])
    message_id = data.get("message_id")
    if isinstance(message_id, int):
        if repo.agent_wrote_after(conn, application_id, message_id):
            return None
        text = TONE_HINT_TEXT.get(str(data.get("code")))
        return text
    event = _pending_event(conn, application_id, "tone_hint", last_agent)
    if event is None:
        return None
    hint = event["data"].get("hint") or event["data"].get("tone_hint") or event["data"].get("text")
    return hint if isinstance(hint, str) and hint else None


def _takeover_summary(
    conn: sqlite3.Connection, application_id: str, last_agent: str | None
) -> str | None:
    """What was said during the Takeover that just ended, for the agent to continue from.

    Uses the hand-back's stored summary (``handbacks``, referenced by the event's
    ``handback_id``) if one was written; otherwise a short transcript of the Takeover's
    last messages (no model call here).
    """
    ended = _pending_event(conn, application_id, "takeover_handed_back", last_agent)
    if ended is None:
        return None
    handback_id = ended["data"].get("handback_id")
    handback = repo.get_handback(conn, handback_id) if isinstance(handback_id, int) else None
    if handback is not None and handback["summary"]:
        return handback["summary"]
    started = repo.takeover_started_before(conn, application_id, ended["id"])
    since = started["created_at"] if started else ""
    rows = repo.takeover_messages(
        conn, application_id, since, ended["created_at"], _TAKEOVER_SUMMARY_MESSAGES
    )
    if not rows:
        return "A team member held the chat; nothing was said during the Takeover."
    lines = []
    for row in reversed(rows):
        who = "Team" if row["sender"] == "human" else "Candidate"
        body = " ".join(row["body"].split())
        if len(body) > _TAKEOVER_SUMMARY_CHARS:
            body = body[: _TAKEOVER_SUMMARY_CHARS - 1] + "…"
        lines.append(f"{who}: {body}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# record_answer
# ---------------------------------------------------------------------------


def _answer_ref(
    conn: sqlite3.Connection, application_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """``data`` plus the Candidate's latest inbound ``message_id``, if any.

    Answer events reference the message the answer came from; the Candidate's words
    never enter ``events.data`` (Q38).
    """
    message_id = repo.last_inbound_message_id(conn, application_id)
    return data if message_id is None else {**data, "message_id": message_id}


def _candidate_words(conn: sqlite3.Connection, application_id: str) -> str | None:
    """The Candidate's own words for ``zone_input`` / ``driver_license_input`` (ADR 0042).

    Read from their latest inbound message, never from the value the agent passed.
    """
    message = repo.last_inbound_message(conn, application_id)
    return None if message is None else message["body"]


def _live_zone_choice(conn: sqlite3.Connection, application_id: str) -> dict[str, Any] | None:
    """The latest Zone options we offered that are still open, or None.

    Open means written after the last Reopen and the last recorded Zone. Their ids tell
    whether one uncovered id is the Candidate's own tap (a Knockout), and a ``confirm``
    among them means a Zone confirmation is waiting for its Sí or No.
    """
    row = repo.live_zone_choice(conn, application_id)
    return None if row is None else json.loads(row["data"])


def _evaluate(
    conn: sqlite3.Connection,
    app: dict[str, Any],
    field: str,
    value: Any,
    now: datetime,
    *,
    after_knockout: bool = False,
) -> ValidationResult:
    """Verify a structured value or null (ADR 0042), already shaped by its field's schema.

    The start date is checked against today in the Candidate's local calendar (their
    Zone's time zone, else their country's), the same ``today`` ``get_application`` reports.
    """
    offered: set[str] = set()
    if field == "zone":
        choice = _live_zone_choice(conn, app["id"]) or {}
        offered = {o["id"] for o in choice.get("options") or []}
    return verify_value(
        field,
        value,
        zones=load_zones(conn) if field == "zone" else (),
        today=local_today(FixedClock(now), _local_zone(conn, app)),
        language=language_of(app),
        offered=offered,
        confirm_uncovered=not after_knockout,
    )


def _keeps_recorded(conn: sqlite3.Connection, app: dict[str, Any], field: str) -> bool:
    """Whether a null for ``field`` would only wipe an answer already on the record.

    Real Haiku sent ``record_answer(zone, null)`` for a Zone it had recorded and the null
    counted a miss. A null is a clearing, not a miss, only in a correction: at Confirm, or
    while a Zone choice written after the recorded Zone is still open.
    """
    if app.get(FIELD_COLUMN[field]) is None or app["stage"] == "confirm":
        return False
    return field != "zone" or _live_zone_choice(conn, app["id"]) is None


def record_answer(
    conn: sqlite3.Connection, app: dict[str, Any], args: dict[str, Any], now: datetime
) -> dict[str, Any]:
    require_agent_control(app)
    field, value = args["field"], args["value"]
    if _answers_knockout(app, field, now):
        return _answer_after_knockout(conn, app, field, value, now)
    require_open(app)
    if app["stage"] not in repo.SCREENING_STAGES:
        raise ToolError(
            "wrong_stage",
            f"Screening has ended (stage {app['stage']}); answers can no longer change. "
            "Call get_application and continue from what it says.",
        )
    if (
        value is None
        and field == "zone"
        and "confirm" in (_live_zone_choice(conn, app["id"]) or {})
    ):
        raise _zone_not_confirmed(conn, app, now)
    if value is None and _keeps_recorded(conn, app, field):
        raise ToolError(
            "invalid_value",
            f"{field} is already recorded. Keep it: do not call record_answer with null for a "
            "field you have recorded. This counts no miss. Ask the next question from "
            "get_application, or go to Confirm when nothing is left.",
            reason="already_answered",
        )
    if _counted_unreadable(conn, app["id"], repo.last_inbound_message_id(conn, app["id"])):
        # The backend could not read the latest message and counted it as a miss (ADR 0063):
        # nothing taken from it is recorded ("Blok loka ksks aaa" passes the name check).
        raise _miss(
            conn, app, field, Invalid(reason="unreadable", hint=UNREADABLE_HINT), value, now
        )
    if value is None and field == "zone":
        unmatched = _unmatched_place(conn, app, now)
        if unmatched is not None:
            return unmatched
    result = _evaluate(conn, app, field, value, now)

    if isinstance(result, Accepted):
        return _accept(conn, app, field, result, now)
    if isinstance(result, NeedsChoice):
        return _needs_choice(conn, app, field, result, now)
    if isinstance(result, Knockout):
        return _knock_out(conn, app, field, result, now)
    assert isinstance(result, Invalid)
    if result.narrowing:
        raise _narrow(conn, app, field, result, now)
    if not result.counted:
        raise ToolError("invalid_value", result.hint, reason=result.reason)
    raise _miss(conn, app, field, result, value, now)


def _answers_knockout(app: dict[str, Any], field: str, now: datetime) -> bool:
    """A new answer to the failed criterion of a Knockout closed within 30 days (ADR 0033)."""
    return (
        app["status"] == "closed"
        and app["outcome"] == "knockout"
        and field == app["knockout_criterion"]
        and repo.within_reopen_window(app, now=now)
    )


_STAYS_CLOSED = (
    " The Application stays closed after its Knockout until a valid answer to this "
    "question arrives."
)


def _answer_after_knockout(
    conn: sqlite3.Connection, app: dict[str, Any], field: str, value: Any, now: datetime
) -> dict[str, Any]:
    """The Candidate answers the failed criterion again after a Knockout (ADR 0033).

    A valid answer reopens the Application (the Candidate's Reopen: stage back to the
    criterion, a ``reopened`` event) and records it, in one transaction. Anything else
    leaves it closed: an ambiguous Zone asks for a choice, an invalid answer is an
    ``invalid_value`` (not counted as a miss), and a still-failing answer is
    ``application_closed``, with no Zone confirmation first (spec 006, decision 3).
    """
    result = _evaluate(conn, app, field, value, now, after_knockout=True)
    if isinstance(result, Accepted):
        with transaction(conn):
            reopened = repo.reopen_application(conn, app["id"], actor="candidate", now=now)
            return _accept(conn, reopened, field, result, now)
    if isinstance(result, NeedsChoice):
        return _needs_choice(conn, app, field, result, now)
    if isinstance(result, Knockout):
        raise ToolError(
            "application_closed",
            "The answer still fails this requirement, so the Application stays closed after "
            "its Knockout. Say so kindly and briefly; they can write again once it changes, "
            "and if they think the decision is wrong, call request_review.",
        )
    assert isinstance(result, Invalid)
    if result.narrowing:
        raise _narrow(conn, app, field, result, now)
    raise ToolError("invalid_value", result.hint + _STAYS_CLOSED, reason=result.reason)


def _accept(
    conn: sqlite3.Connection,
    app: dict[str, Any],
    field: str,
    result: Accepted,
    now: datetime,
) -> dict[str, Any]:
    value = result.value.id if isinstance(result.value, ZoneRef) else result.value
    with transaction(conn):
        updated = repo.record_answer(
            conn,
            app["id"],
            field,
            value,
            raw_input=_candidate_words(conn, app["id"]) if field in KNOCKOUT_FIELDS else None,
            message_id=repo.last_inbound_message_id(conn, app["id"]),
            start_date_far=("start_date_far" in result.flags) if field == "start_date" else None,
            now=now,
        )
        stage = screening_stage(updated)
        if stage != updated["stage"]:
            updated = repo.set_stage(conn, app["id"], stage, now=now)
    following = next_field(updated)
    accepted: dict[str, Any] = {
        "status": "accepted",
        "field": field,
        "next_field": following,
        "stage": updated["stage"],
        "flags": list(result.flags),
    }
    if updated["country"] != app["country"]:
        # The recorded Zone moved the Application to its country (ADR 0057): the next question
        # in this turn already uses that country's words.
        accepted["country"] = updated["country"]
    if following is None:
        # Every field is answered, so the model confirms with the whole summary and gets the
        # answers here. Until then they are in its last get_application result.
        accepted["answers"] = answers_view(conn, updated)
    return accepted


def _needs_choice(
    conn: sqlite3.Connection,
    app: dict[str, Any],
    field: str,
    result: NeedsChoice,
    now: datetime,
) -> dict[str, Any]:
    """Offer 2-3 options as buttons (ADR 0037); with ``confirm``, the Zone's Sí/No.

    The ``answer_ambiguous`` event carries the options (and ``confirm``), so the sender
    attaches them to the agent's message (``pending_choice``). Counts no miss.
    """
    options = [o.as_dict() for o in result.options]
    data: dict[str, Any] = {"field": field, "options": options}
    if result.confirm is not None:
        data["confirm"] = result.confirm
    _offer(conn, app, data, now)
    output: dict[str, Any] = {"status": "needs_choice", "field": field, "options": options}
    if result.confirm is not None:
        output["confirm"] = result.confirm
    return output


def _offer(
    conn: sqlite3.Connection, app: dict[str, Any], data: dict[str, Any], now: datetime
) -> None:
    """Write the ``answer_ambiguous`` event that offers ``data``'s options (ADR 0037).

    A Zone choice already open with the same options for the same Candidate message is not
    written twice: ``search_zones`` offers a city's Zones (ADR 0062) and the agent's
    ``record_answer`` with those ids in the same turn finds them already offered.
    """
    data = _answer_ref(conn, app["id"], data)
    if data["field"] == "zone" and _live_zone_choice(conn, app["id"]) == data:
        return
    repo.add_event(
        conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        kind="answer_ambiguous",
        actor="agent",
        data=data,
        now=now,
    )


def offer_city_zones(
    conn: sqlite3.Connection, app: dict[str, Any], city: list[ZoneRef], now: datetime
) -> bool:
    """Offer a known city's Zones when ``search_zones`` matched the city but no Zone in it.

    ADR 0062: the Candidate named a city plus a place that is not a Zone ("Ciudad de México
    en Tepito"), or the city alone. ``record_answer`` with those ids would offer them as
    buttons (2-3) or a list (4-10, Q45); the search offers them already, so the Candidate
    sees the options even when the agent asks without recording. Only while the Zone is the
    question: open, agent control, during Screening, the Zone unanswered or a correction at
    the confirm step (ADR 0048), and no Zone confirmation waiting for its Sí or No. Counts
    no miss and stores no answer. True when the options are offered.
    """
    if len(city) < 2 or len({(z.country, z.city) for z in city}) != 1:
        return False
    if app["status"] != "open" or app["control"] != "agent":
        return False
    if app["stage"] not in repo.SCREENING_STAGES:
        return False
    if not (field_missing(app, "zone") or app["stage"] == "confirm"):
        return False
    if "confirm" in (_live_zone_choice(conn, app["id"]) or {}):
        return False
    result = verify_zone([z.id for z in city], city, language=language_of(app))
    if not isinstance(result, NeedsChoice | Invalid) or not result.options:
        return False
    _offer(conn, app, {"field": "zone", "options": [o.as_dict() for o in result.options]}, now)
    return True


def _zone_not_confirmed(conn: sqlite3.Connection, app: dict[str, Any], now: datetime) -> ToolError:
    """No to "¿Vives en …?" (spec 006, decision 3): clear the buttons, count no miss.

    An ``answer_ambiguous`` with no options ends the pending choice, so the agent's next
    message carries no buttons, and the agent asks for the Zone again.
    """
    repo.add_event(
        conn,
        candidate_id=app["candidate_id"],
        application_id=app["id"],
        kind="answer_ambiguous",
        actor="agent",
        data=_answer_ref(conn, app["id"], {"field": "zone", "options": []}),
        now=now,
    )
    return ToolError(
        "invalid_value",
        f"They do not live in that area. Ask again where they live ({place_word(language_of(app))}"
        " and city) "
        "and look it up with search_zones. This counts no miss.",
        reason="zone_not_confirmed",
    )


def _funnel_applies(conn: sqlite3.Connection, app: dict[str, Any]) -> bool:
    """Whether the Zone funnel can be offered now (ADR 0068): the Zone is the question, or a
    correction at the confirm step (ADR 0048). Never on a voice call: the Candidate only hears,
    and a list of cities to tap reaches no one."""
    return (
        app["status"] == "open"
        and app["control"] == "agent"
        and app.get("channel") != "voice"
        and app["stage"] in repo.SCREENING_STAGES
        and (next_field(app) == "zone" or app["stage"] == "confirm")
        and not repo.last_inbound_is_spoken(conn, app["id"])
    )


def offer_funnel(conn: sqlite3.Connection, app: dict[str, Any], now: datetime) -> str | None:
    """Offer the Zone funnel for a place that matches no Zone; the question to send, or None.

    ADR 0068: once per Application, the Candidate is asked which of the cities with a Service
    Area in their country they can work in; the cities and "none of these" are the pending
    choice (an ``answer_ambiguous`` event with ``funnel: true``), and the question is the fixed
    copy the reply must carry. Asking again for the same Candidate message (the agent searched
    and then recorded ``null`` in one turn) returns the same question and writes nothing. None
    when the funnel was offered before for an earlier message, when no Zone confirmation or
    other ground is open for it (:func:`_funnel_applies`), or when the country has no cities to
    list. Counts no miss.
    """
    if not _funnel_applies(conn, app):
        return None
    live = _live_zone_choice(conn, app["id"]) or {}
    if "confirm" in live:
        return None
    if funnel.is_funnel(live) and live.get("message_id") == repo.last_inbound_message_id(
        conn, app["id"]
    ):
        return funnel.question(app, live["options"])
    if funnel.last_offer(conn, app["id"]) is not None:
        return None
    found = funnel.options(conn, app)
    if found is None:
        return None
    _offer(conn, app, {"field": "zone", "options": found, "funnel": True}, now)
    return funnel.question(app, found)


def funnel_awaits_answer(conn: sqlite3.Connection, app: dict[str, Any]) -> bool:
    """Whether the cities question was asked for an earlier Candidate message and is still the
    pending choice: the message now being read is the Candidate's answer to it (ADR 0068)."""
    if not _funnel_applies(conn, app):
        return False
    live = _live_zone_choice(conn, app["id"]) or {}
    return funnel.is_funnel(live) and live.get("message_id") != repo.last_inbound_message_id(
        conn, app["id"]
    )


ZONE_NOTE = (
    "[zone] If they name a place, look it up with search_zones, with no country, before you "
    "reply, whatever the place."
)
ZONE_FUNNEL_NOTE = (
    "[zone] They may be answering the cities question. If they name a place, look it up with "
    "search_zones; with no match, call record_answer(zone, null) and send what it returns. A "
    "question gets an answer in words."
)


def zone_note(conn: sqlite3.Connection, app: dict[str, Any]) -> str | None:
    """The backend's one-line note for a typed message read while the Zone is the question, or
    None (ADR 0068). Real Haiku judged "Texas" and "the united states" by itself and answered in
    prose, without searching, so no funnel was offered and no Knockout was decided. The note rides
    in the run input with the Candidate's message, where the agent decides, and the router writes
    it only from the record: whether the Zone is next, and whether the cities question is open."""
    if app["status"] != "open" or app["control"] != "agent" or app["stage"] != "zone":
        return None
    if app.get("channel") == "voice" or next_field(app) != "zone":
        return None
    return ZONE_FUNNEL_NOTE if funnel_awaits_answer(conn, app) else ZONE_NOTE


SILENT_TURNS_FOR_FUNNEL = 2  # the skill's one follow-up without a call, then the funnel


def zone_backstop(
    conn: sqlite3.Connection, app: dict[str, Any], message_id: int, now: datetime
) -> dict[str, Any] | None:
    """Settle the Zone step after a typed run that did nothing with it (ADR 0076).

    Real Haiku filtered every search by country and answered "Texas" in prose seven times, never
    recording ``null``, so ADR 0068's funnel was never offered and the chat looped. After a run on
    the typed message ``message_id``, read while the Zone is the next field, in which nothing was
    recorded, offered or counted for that message, code acts in place of the agent:

    - the cities question is open for an earlier message and a search for this one matched no
      Zone anywhere: the Zone Knockout (ADR 0068's second answer), returned as the Knockout
      result whose ``message`` is the reply;
    - the funnel was never offered, and a search for this message matched no Zone anywhere or
      this is the second typed message in a row with nothing done: the funnel is offered, as
      :func:`offer_funnel` (the router then sends its question);
    - otherwise, once the funnel has been offered (or cannot be) and the agent still records
      nothing: a miss (``unresolved``), so the third raises the usual Escalation.

    Only for an open Application under agent control at the ``zone`` stage, typed, not voice;
    never while a Zone confirmation waits for its Sí or No. Returns the Knockout result or None.
    """
    if zone_note(conn, app) is None or repo.last_inbound_is_spoken(conn, app["id"]):
        return None
    if repo.last_inbound_message_id(conn, app["id"]) != message_id:
        return None  # a newer message arrived: its own run settles it
    if repo.answered_message(conn, app["id"], message_id):
        return None
    live = _live_zone_choice(conn, app["id"]) or {}
    if "confirm" in live:
        return None
    nothing = zone_searches.matched_nothing(app["id"], message_id)
    if nothing and funnel_awaits_answer(conn, app):
        return _funnel_knockout(conn, app, now)
    silent = repo.typed_messages_since_answer(conn, app["id"])
    offered = funnel.last_offer(conn, app["id"]) is not None
    if not offered and (nothing or silent >= SILENT_TURNS_FOR_FUNNEL):
        if offer_funnel(conn, app, now) is not None:
            return None
    elif not offered:
        return None  # the one follow-up the skill allows before a null
    with transaction(conn):
        _count_miss(
            conn,
            app,
            "zone",
            message_id,
            "unresolved",
            repo.last_typed_inbound_text(conn, app["id"]) or "",
            now,
        )
    return None


def _unmatched_place(
    conn: sqlite3.Connection, app: dict[str, Any], now: datetime
) -> dict[str, Any] | None:
    """``record_answer(zone, null)``: the Candidate's place maps to no Zone (ADR 0068).

    - After the funnel was asked for an earlier message (the Candidate tapped "none of these",
      or typed a second place that still matches no Zone): the Zone Knockout, returned as the
      Knockout result.
    - The first time: the funnel is offered and the agent is told to send its question (a
      ``zone_funnel`` error that counts no miss).
    - Otherwise None: an ordinary miss.
    """
    live = _live_zone_choice(conn, app["id"]) or {}
    if funnel.is_funnel(live) and live.get("message_id") != repo.last_inbound_message_id(
        conn, app["id"]
    ):
        return _funnel_knockout(conn, app, now)
    question = offer_funnel(conn, app, now)
    if question is None:
        return None
    raise ToolError(
        "invalid_value",
        "No Zone matches where they live. The backend asks them, once, which of our cities they "
        "can work in, as a list. Send `message` exactly as your whole reply. This counts no miss.",
        reason="zone_funnel",
        message=question,
    )


def _funnel_knockout(
    conn: sqlite3.Connection, app: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """The Zone Knockout for a place outside every Service Area that matched no Zone (ADR 0068).

    Decided by the Candidate's answer to the funnel, never by reading their words (ADR 0042).
    No Zone id is stored: the Application closes with the Zone criterion and the Candidate's own
    typed words in ``zone_input`` for a Review. The template is the Zone Knockout's, sent at once.
    """
    with transaction(conn):
        repo.set_raw_input(
            conn, app["id"], "zone", repo.last_typed_inbound_text(conn, app["id"]), now=now
        )
        template, message = knockout_message(
            "zone",
            "outside_service_areas",
            language_of(app),
            first_name=first_name_of(app),
        )
        repo.close_application(
            conn,
            app["id"],
            "knockout",
            knockout_criterion="zone",
            actor="agent",
            data={"reason": "outside_service_areas", "template": template},
            now=now,
        )
    return {
        "status": "knockout",
        "field": "zone",
        "criterion": "zone",
        "outcome": "knockout",
        "template": template,
        "message": message,
    }


def _knock_out(
    conn: sqlite3.Connection,
    app: dict[str, Any],
    field: str,
    result: Knockout,
    now: datetime,
) -> dict[str, Any]:
    zone = result.value if isinstance(result.value, ZoneRef) else None
    with transaction(conn):
        updated = repo.record_answer(
            conn,
            app["id"],
            field,
            zone.id if zone else result.value,
            raw_input=_candidate_words(conn, app["id"]),
            message_id=repo.last_inbound_message_id(conn, app["id"]),
            now=now,
        )
        # From the record just written: a Zone in another country moved the language (ADR 0057).
        template, message = knockout_message(
            result.criterion,
            result.reason,
            language_of(updated),
            first_name=first_name_of(updated),
            zone=zone.name if zone else None,
            spoken=repo.last_inbound_is_spoken(conn, app["id"]),
        )
        repo.close_application(
            conn,
            app["id"],
            "knockout",
            knockout_criterion=result.criterion,
            actor="agent",
            # The text goes out as the agent's reply; the channel adds that message's id
            # (repo.link_knockout_message). The text, with the first name, stays out (Q38).
            data={"reason": result.reason, "template": template},
            now=now,
        )
    return {
        "status": "knockout",
        "field": field,
        "criterion": result.criterion,
        "outcome": "knockout",
        "template": template,
        "message": message,
    }


def _narrow(
    conn: sqlite3.Connection,
    app: dict[str, Any],
    field: str,
    result: Invalid,
    now: datetime,
) -> ToolError:
    """A known city without a single Zone (Q45): offer its Zones as a list, count no miss.

    Like ``needs_choice`` it writes ``answer_ambiguous`` with the options, so the sender
    attaches them to the agent's message as a list (``pending_choice``). It is an
    ``invalid_value`` because nothing was stored; ``attempt`` is left out on purpose.
    """
    options = [o.as_dict() for o in result.options]
    _offer(conn, app, {"field": field, "options": options}, now)
    hint = result.hint
    if app["status"] == "closed":
        hint += " The Application stays closed after its Knockout until a valid answer arrives."
    return ToolError("invalid_value", hint, reason=result.reason, options=options)


def _miss(
    conn: sqlite3.Connection,
    app: dict[str, Any],
    field: str,
    result: Invalid,
    value: Any,
    now: datetime,
) -> ToolError:
    """Count the miss (Phase 1 section 4.2) and build the ``invalid_value`` error.

    A message the backend already counted as unreadable (ADR 0063) is not counted twice: the
    miss stands and the attempt is the one already recorded."""
    with transaction(conn):
        message_id = repo.last_inbound_message_id(conn, app["id"])
        if _counted_unreadable(conn, app["id"], message_id):
            attempt = repo.require_application(conn, app["id"])["invalid_attempts"]
        else:
            words = _candidate_words(conn, app["id"])
            if words is None:
                words = value if isinstance(value, str) else json.dumps(value)
            attempt = _count_miss(conn, app, field, message_id, result.reason, words, now)
        hint = result.hint
        if attempt >= ESCALATE_AFTER_MISSES:
            hint += (
                " This is the third miss: an Escalation has been raised. Stay friendly, say "
                "someone from the team will help, and you may keep trying."
            )
    extra: dict[str, Any] = {"reason": result.reason, "attempt": attempt}
    options = result.options or field_options(field, language_of(app))
    if attempt >= OPTIONS_FROM_MISS and options:
        extra["options"] = [o.as_dict() for o in options]
    return ToolError("invalid_value", hint, **extra)


def _count_miss(
    conn: sqlite3.Connection,
    app: dict[str, Any],
    field: str,
    message_id: int | None,
    reason: str | None,
    words: str,
    now: datetime,
) -> int:
    """Count a miss on ``field`` for ``message_id`` and return the attempt; the third raises the
    ``repeated_invalid_answers`` Escalation, unless one is open (Phase 1 section 4.2). Runs in the
    caller's transaction. ``words`` (the Candidate's) go into the queue item's note only (Q38)."""
    attempt = repo.record_invalid_answer(
        conn, app["id"], field, message_id=message_id, reason=reason, now=now
    )
    if attempt >= ESCALATE_AFTER_MISSES and not _open_item(conn, app["id"], "escalation"):
        repo.open_queue_item(
            conn,
            app["id"],
            "escalation",
            reason="repeated_invalid_answers",
            note=f"{attempt} invalid answers for {field}; last: {words[:200]}",
            now=now,
        )
    return attempt


UNREADABLE_HINT = (
    "The backend could not read the Candidate's latest message (random letters, not words), so "
    "nothing was recorded. Ask the question again kindly, with an example."
)


def _counted_unreadable(
    conn: sqlite3.Connection, application_id: str, message_id: int | None
) -> bool:
    """Whether the backend counted ``message_id`` as an unreadable miss (ADR 0063)."""
    if message_id is None:
        return False
    return repo.counted_unreadable(conn, application_id, message_id)


# ---------------------------------------------------------------------------
# request_review, raise_escalation
# ---------------------------------------------------------------------------


def _open_item(conn: sqlite3.Connection, application_id: str, kind: str) -> bool:
    return repo.has_open_queue_item(conn, application_id, kind)


def request_review(
    conn: sqlite3.Connection, app: dict[str, Any], args: dict[str, Any], now: datetime
) -> dict[str, Any]:
    require_agent_control(app)
    if app["outcome"] != "knockout":
        raise ToolError(
            "wrong_stage",
            "A Review is only for a disputed Knockout, and this Application did not end in "
            "one. Call get_application and continue from what it says.",
        )
    with transaction(conn):
        if _open_item(conn, app["id"], "review"):
            raise ToolError(
                "already_requested",
                "A Review is already waiting for the team. Tell the Candidate a person will "
                "look at it soon; do not request it again.",
            )
        repo.open_queue_item(conn, app["id"], "review", note=args.get("note"), now=now)
    return {"status": "review_requested"}


def raise_escalation(
    conn: sqlite3.Connection, app: dict[str, Any], args: dict[str, Any], now: datetime
) -> dict[str, Any]:
    require_agent_control(app)
    with transaction(conn):
        if _open_item(conn, app["id"], "escalation"):
            raise ToolError(
                "already_requested",
                "An Escalation is already open, so the team has been told. Stay friendly and "
                "carry on with the pending question.",
            )
        repo.open_queue_item(
            conn, app["id"], "escalation", reason=args["reason"], note=args.get("note"), now=now
        )
    return {"status": "escalation_raised"}


# ---------------------------------------------------------------------------
# finish_screening
# ---------------------------------------------------------------------------


def finish_screening(
    conn: sqlite3.Connection, app: dict[str, Any], args: dict[str, Any], now: datetime
) -> dict[str, Any]:
    require_agent_control(app)
    require_open(app)
    if app["stage"] not in repo.SCREENING_STAGES:
        raise ToolError(
            "wrong_stage",
            f"Screening has already ended (stage {app['stage']}). Call get_application and "
            "continue from what it says.",
        )
    missing = [f for f in FIELD_ORDER if field_missing(app, f)]
    if missing:
        raise ToolError(
            "incomplete",
            f"Some answers are missing: {', '.join(missing)}. Ask for {missing[0]} next, "
            "then confirm the summary again.",
        )
    repo.mark_qualified(conn, app["id"], now=now)
    # Built from the stored language, so the last message agrees with the record.
    return {
        "stage": "qualified",
        "message": qualified_close(
            language_of(app),
            app["country"],
            first_name_of(app),
            spoken=repo.last_inbound_is_spoken(conn, app["id"]),
        ),
    }


# ---------------------------------------------------------------------------
# record_backout
# ---------------------------------------------------------------------------


def record_backout(
    conn: sqlite3.Connection, app: dict[str, Any], args: dict[str, Any], now: datetime
) -> dict[str, Any]:
    require_agent_control(app)
    # The tool accepts a ``note`` (contracts/tools.json) but it is not kept: it would be
    # the agent's paraphrase of the Candidate, and events hold no free text (Q38). The
    # Candidate's own words are in their messages.
    kind = args["kind"]
    if kind == "withdrawn":
        require_open(app)
        repo.close_application(conn, app["id"], "withdrawn", actor="candidate", now=now)
    elif kind == "opted_out":
        repo.mark_opted_out(conn, app["candidate_id"], now=now)
    else:
        with transaction(conn):
            repo.stamp_erasure_request(conn, app["candidate_id"], now=now)
            repo.add_event(
                conn,
                candidate_id=app["candidate_id"],
                application_id=app["id"],
                kind="erasure_requested",
                actor="candidate",
                now=now,
            )
    current = repo.require_application(conn, app["id"])
    return {"kind": kind, "status": current["status"], "outcome": current["outcome"]}
