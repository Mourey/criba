"""An in-process fake web chat server, for testing the driver without the real stack.

`FakeWebChat().app` is a FastAPI app that follows `contracts/webchat.openapi.yaml`: conversations,
idempotent messages, interactive replies, long-poll reads, and the sim clock endpoints. Behind it
a small rules-based "agent" plays the Screening Agent well enough for the seed personas:
disclosure in the first message, fields in order, the Knockout message, ambiguous Zone options,
invalid answers, the confirm step, Review, withdrawing, opting out, the Stalled nudges and
Abandoned, Reopen, and unreadable messages (ADR 0063: each a miss, the third in a row an
`unreadable` Escalation, a fixed message and no more replies). It records an Application per
conversation and returns it in the `ApplicationDetail` shape (`application_detail`), so
`sim.asserts` runs against it.

This is test scaffolding, not a stand-in for the real backend or the stub model: it keeps prose
simple and does not parse free text beyond what the seed personas send.

    fake = FakeWebChat()
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url="http://fake")
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from criba.channel.copy import UNREADABLE_HANDOVER
from sim.detect import guess_language, normalize
from sim.personas.schema import FIELD_ORDER
from sim.stub_llm import jev as stub_jev
from sim.stub_llm.jev import asked_language

HOUR = 3600
# Template the backend sends as the third nudge (`criba.scheduler.jobs.STALLED_TEMPLATE`).
STALLED_TEMPLATE = "stalled_resume"
NUDGE_KINDS = ("nudge", "last_nudge", "template")  # by step, 1 to 3
KNOCKOUT_LINK = "https://empleo.gruposazon.example/requisitos"

ZONES: dict[str, dict[str, Any]] = {
    "chamberi": {
        "id": "zone_es_madrid_chamberi",
        "name": "Chamberí",
        "city": "Madrid",
        "country": "ES",
        "covered": True,
    },
    "retiro": {
        "id": "zone_es_madrid_retiro",
        "name": "Retiro",
        "city": "Madrid",
        "country": "ES",
        "covered": True,
    },
    "toledo": {
        "id": "zone_es_toledo_toledo",
        "name": "Toledo",
        "city": "Toledo",
        "country": "ES",
        "covered": False,
    },
    "toluca": {
        "id": "zone_mx_toluca",
        "name": "Toluca",
        "city": "Toluca",
        "country": "MX",
        "covered": False,
    },
}


def _mx_zone(slug: str, name: str, city: str) -> dict[str, Any]:
    return {
        "id": f"zone_mx_{slug}",
        "name": name,
        "city": city,
        "country": "MX",
        "covered": True,
    }


# The three seeded Puebla Zones ("Puebla" alone names all of them) and the other "Centro" Zones.
PUEBLA_ZONES = [
    _mx_zone("puebla_angelopolis", "Angelópolis", "Puebla"),
    _mx_zone("puebla_centro", "Centro", "Puebla"),
    _mx_zone("puebla_la_paz", "La Paz", "Puebla"),
]
# "Ciudad de México" alone names a city of Zones: the backend offers them as a list (Q45).
CDMX_ZONES = [
    _mx_zone("cdmx_polanco", "Polanco", "Ciudad de México"),
    _mx_zone("cdmx_condesa", "Condesa", "Ciudad de México"),
    _mx_zone("cdmx_roma", "Roma", "Ciudad de México"),
    _mx_zone("cdmx_narvarte", "Narvarte", "Ciudad de México"),
]
# Uncovered Zones that get the Sí/No confirmation ("¿Vives en Toluca?") before the Knockout.
CONFIRMED_ZONES = frozenset({"zone_mx_toluca"})
# The Zone funnel (ADR 0068): the cities with a Service Area, asked once when a place matches no
# Zone. The option ids are the backend's (`criba.tools.funnel.city_option_id`).
FUNNEL_NONE = "city_none"
FUNNEL_CITIES: dict[str, list[tuple[str, str]]] = {
    "MX": [
        ("city_mx_ciudad_de_mexico", "Ciudad de México"),
        ("city_mx_guadalajara", "Guadalajara"),
        ("city_mx_monterrey", "Monterrey"),
        ("city_mx_puebla", "Puebla"),
    ],
    "ES": [
        ("city_es_barcelona", "Barcelona"),
        ("city_es_madrid", "Madrid"),
        ("city_es_malaga", "Málaga"),
        ("city_es_sevilla", "Sevilla"),
        ("city_es_valencia", "Valencia"),
    ],
}
ZONE_BY_ID: dict[str, dict[str, Any]] = {
    z["id"]: z
    for z in (
        *ZONES.values(),
        *PUEBLA_ZONES,
        *CDMX_ZONES,
        _mx_zone("cdmx_centro", "Centro", "CDMX"),
        _mx_zone("merida_centro", "Centro", "Mérida"),
    )
}
CENTRO_OPTIONS = [
    {"id": "zone_mx_cdmx_centro", "label": "Centro, CDMX"},
    {"id": "zone_mx_puebla_centro", "label": "Centro, Puebla"},
    {"id": "zone_mx_merida_centro", "label": "Centro, Mérida"},
]
PUEBLA_OPTIONS = [{"id": z["id"], "label": f"{z['name']} ({z['city']})"} for z in PUEBLA_ZONES]
CDMX_OPTIONS = [{"id": z["id"], "label": f"{z['name']} ({z['city']})"} for z in CDMX_ZONES]
# A funnel city tap leads to that city's Zones (the two this fake models).
FUNNEL_CITY_ZONES = {"city_mx_ciudad_de_mexico": CDMX_OPTIONS, "city_mx_puebla": PUEBLA_OPTIONS}

# "<name>, vivo en <zone> y tengo carnet": several fields in one answer (the shape the stub
# screener's multi-name rule reads).
_SEVERAL_FIELDS = re.compile(
    r"^(?P<full_name>[^,\d]+?),\s*vivo en\s+(?P<zone>.+?)\s+y\s+(?P<driver_license>tengo\s+.+)$",
    re.IGNORECASE,
)
_SEVERAL_FIELD_NAMES = ("full_name", "zone", "driver_license")

STAGE_OF = {
    "full_name": "name",
    "zone": "zone",
    "driver_license": "driver_license",
    "availability": "availability",
    "preferred_schedule": "preferred_schedule",
    "experience_years": "experience",
    "experience_platforms": "experience",
    "start_date": "start_date",
    "confirm": "confirm",
}
# `Knockout.reason` for each failed criterion (`criba.screening`): the only ones the fake plays.
KNOCKOUT_REASON = {"zone": "outside_service_areas", "driver_license": "no"}
# Stages in which a close also writes `screening_finished` (`repo.SCREENING_STAGES`).
SCREENING_STAGES = frozenset({"greeting", *STAGE_OF.values()})

QUESTIONS: dict[str, tuple[str, str]] = {
    "full_name": ("¿Cómo te llamas (nombre y apellido)?", "What is your full name?"),
    "zone": (
        "¿En qué zona o ciudad vives? Por ejemplo: Madrid, Chamberí.",
        "Which area or city do you live in?",
    ),
    "driver_license": (
        "¿Tienes {lic} vigente?",
        "Do you have a valid driver's license?",
    ),
    "availability": (
        "¿Qué disponibilidad tienes: tiempo completo, medio tiempo o fines de semana?",
        "What is your availability: full-time, part-time or weekends?",
    ),
    "preferred_schedule": (
        "¿Qué horario prefieres: mañana, tarde, noche o flexible?",
        "Which schedule do you prefer: morning, afternoon, evening or flexible?",
    ),
    "experience_years": (
        "¿Cuánta experiencia tienes como repartidor?",
        "How much experience do you have as a delivery driver?",
    ),
    "experience_platforms": (
        "¿En qué plataformas has repartido?",
        "Which platforms have you delivered for?",
    ),
    "start_date": ("¿Cuándo podrías empezar?", "When could you start?"),
}
OPTIONS = {
    "availability": [
        {"id": "full_time", "label": "Tiempo completo"},
        {"id": "part_time", "label": "Medio tiempo"},
        {"id": "weekends", "label": "Fines de semana"},
    ],
    "experience_platforms": [  # the backend's list, ending with "no app" (ADR 0053)
        {"id": "glovo", "label": "Glovo"},
        {"id": "uber_eats", "label": "Uber Eats"},
        {"id": "just_eat", "label": "Just Eat"},
        {"id": "rappi", "label": "Rappi"},
        {"id": "didi_food", "label": "DiDi Food"},
        {"id": "other", "label": "Otra"},
        {"id": "none", "label": "Ninguna app"},
    ],
}


# Weekday words (accents stripped) to date.weekday(), for "el próximo domingo" (ADR 0054).
_WEEKDAY_WORDS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Msg:
    id: int
    direction: str
    sender: str
    text: str
    created_at: datetime
    visible_at: float
    message_id: str | None = None
    interactive: dict[str, Any] | None = None
    reply_to: dict[str, Any] | None = None
    links: list[dict[str, Any]] = field(default_factory=list)
    content_type: str = "text"
    template_name: str | None = None
    media_type: str | None = None
    option_id: str | None = None

    def chat_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "direction": self.direction,
            "sender": self.sender,
            "sender_label": "Sazón Empleo" if self.sender == "agent" else None,
            "text": self.text,
            "interactive": self.interactive,
            "reply_to": self.reply_to,
            "links": self.links,
            "attachments": [],
            "created_at": _iso(self.created_at),
        }


@dataclass
class Conv:
    id: str
    phone: str | None
    country: str
    locale: str
    created_at: datetime
    messages: list[Msg] = field(default_factory=list)
    idempotency: dict[str, tuple[int, tuple]] = field(default_factory=dict)
    lang: str = "es"
    started: bool = False
    status: str = "open"
    outcome: str | None = None
    criterion: str | None = None
    closed_at: datetime | None = None
    asking: str | None = None
    answers: dict[str, Any] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    recorded_at: dict[str, datetime] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    pending_backout: str | None = None
    confirming: str | None = None  # the uncovered Zone a Sí/No confirmation waits on
    funnel_offered: bool = False  # the cities funnel was asked (once per Application, ADR 0068)
    funnel_live: bool = False  # ... and its list is the pending choice
    opted_out: bool = False
    nudges: int = 0
    last_candidate_at: datetime | None = None
    stage: str = "greeting"
    # The Application's country: the Candidate's until a recorded Zone moves it (ADR 0057).
    app_country: str | None = None
    # Unreadable messages in a row, and whether they handed the chat to people (ADR 0063).
    unreadable_run: int = 0
    held: bool = False


class FakeWebChat:
    """The fake server. `reply_delay_s` holds each agent message back to exercise long polls."""

    def __init__(
        self,
        *,
        reply_delay_s: float = 0.0,
        zone_clarification: Literal["buttons", "text"] = "buttons",
        run_finished: bool = True,
        zone_funnel: bool = True,
    ):
        self.reply_delay_s = reply_delay_s
        # A place that matches no Zone is asked as the cities funnel (ADR 0068); False plays the
        # backend before it, which re-asked the Zone as an invalid answer.
        self.zone_funnel = zone_funnel
        # Emit the page's `run_finished` (False: an older backend, for the driver's fallback).
        self.run_finished = run_finished
        # How an ambiguous Zone ("Puebla", "Centro") is clarified: Zone buttons (2 or 3
        # matches) or a text re-ask for the city or neighbourhood (more than 3 matches).
        self.zone_clarification = zone_clarification
        self.offset = timedelta(0)
        self.convs: dict[str, Conv] = {}
        self.by_phone: dict[str, Conv] = {}
        self._ids = 0
        self._event_ids = 0
        self._queue_ids = 0
        self.log: list[dict[str, Any]] = []  # every exchange: method, path, request, status, body
        self.app = self._build_app()

    # ---- time ---------------------------------------------------------------------------

    def now(self) -> datetime:
        return datetime.now(UTC) + self.offset

    def run_jobs(self) -> int:
        """Scheduler: nudges at 1h, 20h and 48h of Candidate silence, Abandoned at 7d."""
        ran = 0
        now = self.now()
        for c in self.convs.values():
            if (
                c.status != "open"
                or c.opted_out
                or not c.started
                or c.stage in ("qualified", "confirm")
            ):
                continue
            assert c.last_candidate_at is not None
            silent = (now - c.last_candidate_at).total_seconds()
            for index, threshold in enumerate((HOUR, 20 * HOUR, 48 * HOUR)):
                if c.nudges == index and silent >= threshold:
                    if index == 0:
                        self._event(c, "stalled", "system", {"stage": c.stage})
                        ran += 1
                    c.nudges += 1
                    template = index == 2
                    self._event(
                        c,
                        "nudge_sent",
                        "system",
                        {
                            "step": c.nudges,
                            "kind": NUDGE_KINDS[index],
                            "stage": c.stage,
                        },
                    )
                    msg = self._outbound(
                        c,
                        "¿Sigues interesado? Escríbenos cuando quieras."
                        if c.lang == "es"
                        else "Still interested? Write to us whenever you like.",
                        content_type="template" if template else "text",
                        template_name=STALLED_TEMPLATE if template else None,
                    )
                    if template:
                        self._template_sent(c, msg)
                    ran += 1
            if c.nudges == 3 and silent >= 7 * 24 * HOUR:
                c.status, c.outcome, c.closed_at = "closed", "abandoned", now
                self._close_events(
                    c,
                    "abandoned",
                    "system",
                    {"stage": c.stage, "nudges_sent": c.nudges},
                )
                ran += 1
        return ran

    # ---- storage helpers ----------------------------------------------------------------

    def _next_id(self) -> int:
        self._ids += 1
        return self._ids

    def _event(
        self, c: Conv, kind: str, actor: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._event_ids += 1
        event = {
            "id": self._event_ids,
            "kind": kind,
            "actor": actor,
            "user_name": None,
            "data": data or {},
            "created_at": _iso(self.now()),
        }
        c.events.append(event)
        return event

    def _last_inbound_id(self, c: Conv) -> int | None:
        """The Candidate's latest message (`repo.last_inbound_message_id`): what events cite
        instead of copying their words."""
        return next((m.id for m in reversed(c.messages) if m.direction == "inbound"), None)

    def _with_message_id(self, c: Conv, data: dict[str, Any]) -> dict[str, Any]:
        message_id = self._last_inbound_id(c)
        return data if message_id is None else {**data, "message_id": message_id}

    def _template_sent(self, c: Conv, msg: Msg) -> None:
        """The `template_sent` event the sender writes for any template message."""
        assert msg.template_name is not None
        self._event(
            c,
            "template_sent",
            "system",
            {"template": msg.template_name, "message_id": msg.id},
        )

    def _close_events(self, c: Conv, kind: str, actor: str, data: dict[str, Any]) -> dict[str, Any]:
        """What `repo.close_application` writes: the Outcome's event (returned), then
        `screening_finished {result}` when the close ends a screening stage."""
        event = self._event(c, kind, actor, data)
        if c.stage in SCREENING_STAGES:
            self._event(c, "screening_finished", actor, {"result": kind})
        return event

    def _locale(self, c: Conv) -> str:
        country = c.app_country or c.country
        return "en" if c.lang == "en" else ("es-MX" if country == "MX" else "es-ES")

    def _outbound(
        self,
        c: Conv,
        text: str,
        *,
        interactive: dict[str, Any] | None = None,
        content_type: str = "text",
        template_name: str | None = None,
        links: list[dict[str, Any]] | None = None,
    ) -> Msg:
        msg = Msg(
            id=self._next_id(),
            direction="outbound",
            sender="agent",
            text=text,
            created_at=self.now(),
            visible_at=time.monotonic() + self.reply_delay_s,
            interactive=interactive,
            links=links or [],
            content_type="interactive" if interactive and content_type == "text" else content_type,
            template_name=template_name,
        )
        c.messages.append(msg)
        return msg

    # ---- the agent ----------------------------------------------------------------------

    def _say(self, c: Conv, es: str, en: str, **kw: Any) -> Msg:
        mx = (c.app_country or c.country) == "MX"
        lic = "licencia de manejo" if mx else "carnet de conducir"
        hood = "colonia" if mx else "barrio"  # the skill's rule: Spain barrio, Mexico colonia
        text = (en if c.lang == "en" else es).replace("{lic}", lic).replace("{hood}", hood)
        return self._outbound(c, text, **kw)

    def _ask(self, c: Conv, name: str) -> None:
        c.asking = name
        if c.stage != "greeting" or c.answers:
            # The backend's stage follows what is recorded: it stays `greeting` until the
            # first answer, so asking the name does not move it.
            c.stage = STAGE_OF[name]
        if name == "confirm":
            self._say(
                c,
                f"Ya tengo todo, {c.answers.get('full_name')}. ¿Es correcto?",
                f"I have everything, {c.answers.get('full_name')}. Is that right?",
            )
            return
        es, en = QUESTIONS[name]
        options = OPTIONS.get(name)
        self._say(
            c,
            es,
            en,
            interactive=(
                {"style": "buttons" if len(options) <= 3 else "list", "options": options}
                if options
                else None
            ),
        )

    def _next_field(self, c: Conv) -> str:
        for name in FIELD_ORDER:
            if name in c.answers:
                continue
            if name == "experience_platforms" and c.answers.get("experience_years") == 0:
                continue
            return name
        return "confirm"

    def _record(self, c: Conv, name: str, value: Any) -> None:
        c.answers[name] = value
        c.recorded_at[name] = self.now()
        c.tool_calls.append("record_answer")
        # `repo.record_answer`: the canonical value (a Zone is its id) and the Candidate's
        # latest message by id. The Candidate's name stays out of events, and so do their words.
        data: dict[str, Any] = {"field": name}
        if name != "full_name":
            data["value"] = value["id"] if name == "zone" else value
        # `repo._follow_zone_country`: a Zone in another country moves the Application there,
        # and a Spanish language follows it (ADR 0057).
        before = c.app_country or c.country
        if name == "zone" and value.get("country") and value["country"] != before:
            spanish = c.lang != "en"
            old_locale = self._locale(c)
            c.app_country = value["country"]
            data["country_changed"] = {"from": before, "to": value["country"]}
            if spanish:
                data["language_changed"] = {"from": old_locale, "to": self._locale(c)}
        self._event(c, "answer_recorded", "agent", self._with_message_id(c, data))

    def _knockout(self, c: Conv, name: str) -> None:
        c.status, c.outcome, c.criterion, c.closed_at = (
            "closed",
            "knockout",
            name,
            self.now(),
        )
        c.stage = STAGE_OF[name]
        # `criba.tools.application._knock_out`: reason code, template name and the failed
        # criterion; `knockout_message` names the template `knockout_<criterion>.<locale>`
        # (the reason suffix only exists for the licence variants this fake does not play).
        # The text is never copied here: the event points at the reply by id, as
        # `repo.link_knockout_message` does once the reply is sent.
        knockout = self._close_events(
            c,
            "knockout",
            "agent",
            {
                "reason": KNOCKOUT_REASON[name],
                "template": f"knockout_{name}.{self._locale(c)}",
                "criterion": name,
                "stage": c.stage,
            },
        )
        reason_es = (
            "necesitamos {lic} vigente"
            if name == "driver_license"
            else "todavía no cubrimos tu zona"
        )
        reason_en = (
            "we need a valid driver's license"
            if name == "driver_license"
            else "we do not cover your area yet"
        )
        reply = self._say(
            c,
            f"Gracias. Por ahora {reason_es}: es un requisito legal y del seguro para repartir. "
            f"Aquí están los requisitos: {KNOCKOUT_LINK}. Cuando cambie, escríbenos y seguimos "
            'donde lo dejamos. Si crees que hay un error, responde "revisión" y una persona '
            "lo verá.",
            f"Thank you. For now {reason_en}. Here are the requirements: {KNOCKOUT_LINK}. "
            "When that changes, write to us and we will pick up where we left off. If you think "
            'this is a mistake, reply "review" and a person will look.',
            links=[{"url": KNOCKOUT_LINK, "label": "Requisitos"}],
        )
        knockout["data"]["message_id"] = reply.id

    def _parse(self, c: Conv, name: str, text: str, option_id: str | None) -> tuple[str, Any]:
        """Returns (status, value): ok, invalid, choice or knockout."""
        norm = normalize(text)
        if name == "full_name":
            words = text.split()
            ok = len(words) >= 2 and all(w.replace("-", "").isalpha() for w in words)
            return ("ok", text.strip()) if ok else ("invalid", None)
        if name == "zone":
            if option_id:
                zone = ZONE_BY_ID.get(option_id) or {**PUEBLA_ZONES[1], "id": option_id}
                # A tapped uncovered Zone is the Candidate's own choice: the Knockout.
                return ("ok" if zone["covered"] else "knockout", zone)
            if "puebla" in norm:
                named = next((z for z in PUEBLA_ZONES if normalize(z["name"]) in norm), None)
                return ("ok", named) if named else ("choice", PUEBLA_OPTIONS)
            only_puebla = next(  # "La Paz", "Angelópolis": one Zone without the city
                (z for z in PUEBLA_ZONES if z["name"] != "Centro" and normalize(z["name"]) in norm),
                None,
            )
            if only_puebla:
                return ("ok", only_puebla)
            if "ciudad de mexico" in norm:
                return ("choice", CDMX_OPTIONS)
            if re.search(r"\bcentro\b", norm):
                return ("choice", CENTRO_OPTIONS)
            zone = next((z for key, z in ZONES.items() if key in norm), None)
            if zone is None and "madrid" in norm:
                zone = ZONES["chamberi"]
            if zone is None:
                return ("unmatched", None)  # no Zone at all (ADR 0068)
            if zone["covered"]:
                return ("ok", zone)
            # The real backend confirms one uncovered Zone before any Knockout (ADR 0042). The
            # fake plays that for the Zones in CONFIRMED_ZONES only; Toledo keeps the direct
            # Knockout that older tests lock.
            return ("confirm" if zone["id"] in CONFIRMED_ZONES else "knockout", zone)
        if name == "driver_license":
            if re.search(r"^\W*no\b|no tengo|sin\b", norm):
                return ("knockout", False)
            if re.search(r"\bsi\b|\byes\b|tengo|\bhave\b", norm):
                return ("ok", True)
            return ("invalid", None)
        if name == "availability":
            values = [
                v
                for v, pat in (
                    ("full_time", r"completo|full"),
                    ("part_time", r"medio tiempo|part"),
                    ("weekends", r"fines de semana|weekend"),
                )
                if re.search(pat, norm)
            ]
            if option_id:
                values = [option_id]
            return ("ok", values) if values else ("invalid", None)
        if name == "preferred_schedule":
            values = [
                v
                for v, pat in (
                    ("morning", r"manana|morning"),
                    ("afternoon", r"tarde|afternoon"),
                    ("evening", r"noche|evening|night"),
                    ("flexible", r"flexible"),
                )
                if re.search(pat, norm)
            ]
            return ("ok", values) if values else ("invalid", None)
        if name == "experience_years":
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*(mes|month)?", norm)
            if not m:
                return ("invalid", None)
            number = float(m.group(1).replace(",", "."))
            years = number / 12 if m.group(2) else number
            return ("ok", int(years) if years == int(years) else years)
        if name == "experience_platforms":
            # ADR 0053: the "no app" row, and delivering on their own, are the empty list.
            if option_id is not None:
                return ("ok", [] if option_id == "none" else [option_id])
            if re.search(r"ningun|por mi cuenta|on my own|no app|\bnone\b", norm):
                return ("ok", [])
            values = [
                v
                for v, pat in (
                    ("glovo", r"glovo"),
                    ("uber_eats", r"uber"),
                    ("just_eat", r"just ?eat"),
                    ("rappi", r"rappi"),
                    ("didi_food", r"didi"),
                )
                if re.search(pat, norm)
            ]
            return ("ok", values or ["other"])
        if name == "start_date":
            if option_id is not None:
                return ("ok", option_id)  # a tapped date (ADR 0054)
            if re.search(r"ayer|yesterday", norm):
                return ("invalid", None)
            nxt = re.search(r"(?:proximo|next) (" + "|".join(_WEEKDAY_WORDS) + ")", norm)
            if nxt:
                # "el próximo domingo": the coming one or the one a week later (ADR 0054).
                day = _WEEKDAY_WORDS[nxt.group(1)]
                today = self.now().date()
                first = today + timedelta(days=(day - today.weekday()) % 7 or 7)
                return (
                    "choice",
                    [
                        {"id": d.isoformat(), "label": d.isoformat()}
                        for d in (first, first + timedelta(days=7))
                    ],
                )
            return ("ok", (self.now() + timedelta(days=3)).date().isoformat())
        return ("invalid", None)

    def _answer_zone_confirmation(self, c: Conv, option_id: str | None) -> None:
        """The Sí tap is the Knockout; No counts no miss, clears the buttons and asks again."""
        zone_id, c.confirming = c.confirming, None
        c.tool_calls.append("record_answer")
        if zone_id is not None and option_id == zone_id:
            self._record(c, "zone", ZONE_BY_ID[zone_id])
            self._knockout(c, "zone")
            return
        # An `answer_ambiguous` with no options ends the pending choice (no `attempts` bump).
        self._event(
            c,
            "answer_ambiguous",
            "agent",
            self._with_message_id(c, {"field": "zone", "options": []}),
        )
        self._ask(c, "zone")

    def _offer_funnel(self, c: Conv) -> None:
        """A place that matches no Zone: ask once which of our cities they can work in, as a list
        of the cities and "none of these". Counts no miss and writes no answer."""
        c.funnel_offered = c.funnel_live = True
        cities = FUNNEL_CITIES[c.app_country or c.country]
        none_label = "None of these" if c.lang == "en" else "Ninguna de estas"
        options = [{"id": i, "label": label} for i, label in cities]
        options.append({"id": FUNNEL_NONE, "label": none_label})
        self._event(
            c,
            "answer_ambiguous",
            "agent",
            self._with_message_id(c, {"field": "zone", "options": options, "funnel": True}),
        )
        names = ", ".join(label for _, label in cities)
        self._say(
            c,
            f"Contratamos repartidores en estas ciudades: {names}. ¿Puedes trabajar en alguna?",
            f"We hire delivery drivers in these cities: {names}. Can you work in one of them?",
            interactive={"style": "list", "options": options},
        )

    def _answer_funnel(self, c: Conv, text: str, option_id: str | None) -> bool:
        """The Candidate answers the funnel. "None of these", or a typed place that still matches
        no Zone, is the Zone Knockout (no Zone stored); a city is its Zones as a list. True when
        handled; False when the typed text names a Zone and the normal flow takes it."""
        c.funnel_live = False
        if option_id is None:
            status, _ = self._parse(c, "zone", text, None)
            if status != "unmatched":
                return False
        if option_id is None:
            c.tool_calls.append("search_zones")  # the second place, looked up and not found
        c.tool_calls.append("record_answer")
        options = FUNNEL_CITY_ZONES.get(option_id or "")
        if option_id != FUNNEL_NONE and options is not None:
            self._event(
                c,
                "answer_ambiguous",
                "agent",
                self._with_message_id(c, {"field": "zone", "options": options}),
            )
            self._say(
                c,
                "¿Cuál es tu zona? Elígela en la lista.",
                "Which one is your area? Please pick it from the list.",
                interactive={
                    "style": "buttons" if len(options) <= 3 else "list",
                    "options": options,
                },
            )
            return True
        self._knockout(c, "zone")
        return True

    def _handle_answer(self, c: Conv, text: str, option_id: str | None) -> None:
        name = c.asking
        if name == "confirm":
            m = re.search(r"apellido es (.+)", text, re.IGNORECASE)
            zone_fix = re.search(r"mi zona es (.+)", text, re.IGNORECASE)
            fixed = self._parse(c, "zone", zone_fix.group(1), None) if zone_fix else None
            if m:
                first = str(c.answers["full_name"]).split()[0]
                c.answers["full_name"] = f"{first} {m.group(1).strip()}"
                self._ask(c, "confirm")
            elif fixed is not None and fixed[0] == "ok":
                c.tool_calls.append("record_answer")
                self._record(c, "zone", fixed[1])
                self._ask(c, "confirm")
            elif fixed is not None and fixed[0] == "choice":
                # Only the city: its Zones as buttons, although the Zone is already answered
                # (ADR 0048). The stage stays confirm; a tap records it and confirms again.
                c.tool_calls += ["search_zones", "record_answer"]
                self._event(
                    c,
                    "answer_ambiguous",
                    "agent",
                    self._with_message_id(c, {"field": "zone", "options": fixed[1]}),
                )
                c.asking = "zone"
                self._say(
                    c,
                    "Hay varias opciones. ¿Cuál es la tuya?",
                    "There are a few matches. Which one is yours?",
                    interactive={"style": "buttons", "options": fixed[1]},
                )
            elif re.search(r"^\W*(s[ií]|yes|ok|claro|correct)", normalize(text)):
                c.tool_calls.append("finish_screening")
                self._event(c, "screening_finished", "agent", {"result": "qualified"})
                c.stage, c.asking = "qualified", None
                self._say(
                    c,
                    "¡Listo! Un reclutador de Grupo Sazón revisará tu solicitud.",
                    "Done! A Grupo Sazón recruiter will review your application.",
                )
            else:
                self._say(
                    c,
                    "Perdona, ¿es correcto lo que te resumí? Responde sí o dime qué cambiar.",
                    "Sorry, is the summary right? Reply yes, or tell me what to change.",
                )
            return
        assert name is not None
        several = _SEVERAL_FIELDS.search(text) if name == "full_name" else None
        parsed = (
            [(f, self._parse(c, f, several.group(f), None)) for f in _SEVERAL_FIELD_NAMES]
            if several
            else []
        )
        if parsed and all(status == "ok" for _, (status, _) in parsed):
            # One message answers name, Zone and license: record each, skip their questions.
            for f, (_, value) in parsed:
                c.tool_calls.append("record_answer")
                self._record(c, f, value)
            self._ask(c, self._next_field(c))
            return
        if name == "zone" and c.confirming:
            self._answer_zone_confirmation(c, option_id)
            return
        if name == "zone" and c.funnel_live and self._answer_funnel(c, text, option_id):
            return
        status, value = self._parse(c, name, text, option_id)
        if name == "zone" and option_id is None:
            c.tool_calls.append("search_zones")  # the agent looks a typed Zone up first (006)
        if status == "unmatched":
            if self.zone_funnel and not c.funnel_offered:
                self._offer_funnel(c)
                return
            status = "invalid"
        c.attempts[name] = c.attempts.get(name, 0) + 1
        if status == "choice" and name == "zone" and self.zone_clarification == "text":
            self._event(
                c,
                "answer_invalid",
                "agent",
                self._with_message_id(c, {"field": name, "attempts": c.attempts[name]}),
            )
            c.tool_calls.append("record_answer")
            self._say(
                c,
                "Ese lugar tiene varias zonas. ¿En qué {hood} y ciudad vives? "
                "Por ejemplo: Centro, Puebla.",
                "That place has several areas. Which neighbourhood and city do you live in? "
                "For example: Centro, Puebla.",
            )
        elif status == "confirm":
            c.confirming = value["id"]
            buttons = [
                {"id": value["id"], "label": "Sí"},
                {"id": "no", "label": "No"},
            ]
            self._event(
                c,
                "answer_ambiguous",
                "agent",
                self._with_message_id(
                    c, {"field": name, "options": buttons, "confirm": value["id"]}
                ),
            )
            c.tool_calls.append("record_answer")
            self._say(
                c,
                "¿Vives en esa zona? Responde con los botones.",
                "Do you live there? Answer with the buttons.",
                interactive={"style": "buttons", "options": buttons},
            )
        elif status == "choice":
            self._event(
                c,
                "answer_ambiguous",
                "agent",
                self._with_message_id(c, {"field": name, "options": value}),
            )
            c.tool_calls.append("record_answer")
            self._say(
                c,
                "Hay varias opciones. ¿Cuál es la tuya?",
                "There are a few matches. Which one is yours?",
                interactive={
                    "style": "buttons" if len(value) <= 3 else "list",
                    "options": value,
                },
            )
        elif status == "invalid":
            self._event(
                c,
                "answer_invalid",
                "agent",
                self._with_message_id(c, {"field": name, "attempts": c.attempts[name]}),
            )
            c.tool_calls.append("record_answer")
            self._say(
                c,
                "No me ha quedado claro. ¿Me lo dices de otra forma, con un ejemplo?",
                "I did not quite get that. Could you say it another way, with an example?",
            )
        elif status == "knockout":
            self._record(c, name, value)
            self._knockout(c, name)
        else:
            self._record(c, name, value)
            self._ask(c, self._next_field(c))

    def _unreadable(self, c: Conv, text: str) -> bool:
        """The backend's unreadable rule (ADR 0063), with the stub Jev's reading: the miss,
        the run, and at three the hold with its fixed message. True when it answered."""
        if not stub_jev.unreadable(text):
            c.unreadable_run = 0
            return False
        c.unreadable_run += 1
        name = c.asking
        if name in FIELD_ORDER:
            c.attempts[name] = c.attempts.get(name, 0) + 1
            data = {"field": name, "attempts": c.attempts[name], "reason": "unreadable"}
            self._event(c, "answer_invalid", "agent", self._with_message_id(c, data))
        if c.unreadable_run >= 3:
            c.held = True
            self._queue_ids += 1
            self._event(
                c, "escalation_raised", "agent", {"queue_item_id": f"q_{self._queue_ids}",
                                                 "reason": "unreadable"}
            )  # fmt: skip
            msg = self._outbound(c, UNREADABLE_HANDOVER[self._locale(c)])
            msg.sender = "system"
            return True
        self._say(
            c,
            "No logré leer tu mensaje. ¿Me lo escribes de nuevo?",
            "I couldn't read that. Could you write it again?",
        )
        return True

    def _agent_turn(self, c: Conv, text: str, option_id: str | None) -> None:
        if c.held:
            return  # the Recruiters have the chat (ADR 0063): stored, no reply
        norm = normalize(text)
        c.tool_calls.append("get_application")
        c.last_candidate_at = self.now()
        previous_lang = c.lang
        # An explicit request for a language wins over the language the text is written in, as in
        # the Jev router (ADR 0061); the stub Jev's rule is the one the sim stack runs.
        asked = asked_language(text.lower())
        c.lang = asked if asked != "none" else guess_language(text) or previous_lang
        if c.status == "closed":
            if c.outcome == "knockout" and re.search(r"revision|review|una persona", norm):
                c.tool_calls.append("request_review")
                self._queue_ids += 1
                self._event(
                    c,
                    "review_requested",
                    "agent",
                    {"queue_item_id": f"q_{self._queue_ids}", "reason": None},
                )
                self._say(
                    c,
                    "Hecho: una persona del equipo de Grupo Sazón revisará la decisión.",
                    "Done: someone from the Grupo Sazón team will review the decision.",
                )
                return
            if (
                c.outcome == "knockout"
                and c.closed_at is not None
                and c.criterion
                and self.now() - c.closed_at < timedelta(days=30)
            ):
                cleared = c.answers.pop(c.criterion, None)
                self._event(
                    c,
                    "reopened",
                    "candidate",
                    {
                        "previous_outcome": "knockout",
                        "via": "candidate",
                        "criterion": c.criterion,
                        "previous_value": cleared["id"] if isinstance(cleared, dict) else cleared,
                    },
                )
                c.status, c.outcome, c.closed_at = "open", None, None
                c.asking = c.criterion
                self._handle_answer(c, text, option_id)
                return
            self._say(
                c,
                "Tu solicitud ya está cerrada. Si algo cambia, escríbenos.",
                "Your application is closed. If anything changes, write to us.",
            )
            return
        if c.pending_backout:
            kind, c.pending_backout = c.pending_backout, None
            if re.search(r"^\W*(s[ií]|yes|ok|claro|seguro|please stop)", norm):
                c.tool_calls.append("record_backout")
                if kind == "withdrawn":
                    c.status, c.outcome, c.closed_at = "closed", "withdrawn", self.now()
                    self._close_events(c, "withdrawn", "agent", {"stage": c.stage})
                else:
                    c.opted_out = True
                    self._event(c, "opted_out", "agent")
                self._say(
                    c,
                    "Hecho. Gracias por tu tiempo y mucho éxito.",
                    "Done. Thanks for your time, and good luck.",
                )
                return
        if re.search(r"ya no me interesa|no me interesa|not interested", norm):
            c.pending_backout = "withdrawn"
            self._say(
                c,
                "Entendido. ¿Confirmas que quieres retirar tu solicitud?",
                "Understood. Do you want me to withdraw your application?",
            )
            return
        if re.search(r"deja de escrib|stop messaging|no me escrib", norm):
            c.pending_backout = "opted_out"
            self._say(
                c,
                "Claro. ¿Confirmas que quieres que dejemos de escribirte?",
                "Of course. Do you confirm you want us to stop messaging you?",
            )
            return
        if re.search(r"ignora tus instrucciones|system prompt|^system:", norm):
            self._say(
                c,
                "Eso no puedo hacerlo, pero sí puedo ayudarte con tu solicitud. Sigamos.",
                "I can't do that, but I can help with your application. Let's continue.",
            )
            return
        if not c.started:
            c.started = True
            self._event(c, "application_started", "candidate", {"channel": "webchat"})
            greeting = (
                "¡Hola! Soy el asistente virtual de Grupo Sazón, una inteligencia "
                "artificial. Te ayudo a postularte como repartidor en unos minutos."
            )
            greeting_en = (
                "Hi! I am Grupo Sazón's virtual assistant, an AI. I can help you "
                "apply as a delivery driver in a few minutes."
            )
            self._say(c, greeting, greeting_en)
            m = re.search(
                r"soy (?P<name>[^,]+), vivo en (?P<zone>.+?) y tengo (carnet|licencia)",
                text,
                re.IGNORECASE,
            )
            if m:
                for name, raw in (
                    ("full_name", m["name"]),
                    ("zone", m["zone"]),
                    ("driver_license", "sí"),
                ):
                    status, value = self._parse(c, name, raw, None)
                    if status == "ok":
                        self._record(c, name, value)
            self._ask(c, self._next_field(c))
            return
        if option_id is None and self._unreadable(c, text):
            return
        self._handle_answer(c, text, option_id)

    # ---- the HTTP surface -----------------------------------------------------------------

    def application_detail(self, phone: str) -> dict[str, Any]:
        """The conversation's Application in the shape of `ApplicationDetail`."""
        c = self.by_phone[phone]
        language = self._locale(c)
        answers = {name: c.answers.get(name) for name in FIELD_ORDER}
        if isinstance(answers["zone"], dict):
            z = answers["zone"]
            answers["zone"] = {k: z[k] for k in ("id", "name", "city", "country")}
        answer_status = [
            {
                "field": name,
                "status": "knockout"
                if c.criterion == name and c.outcome == "knockout"
                else "valid",
                "raw_input": None,
                "attempts": c.attempts.get(name, 1),
                "recorded_at": _iso(c.recorded_at[name]),
                "note": None,
            }
            for name in FIELD_ORDER
            if name in c.recorded_at
        ]
        messages = [
            {
                "id": m.id,
                "direction": m.direction,
                "sender": m.sender,
                "sender_name": None,
                "content_type": m.content_type,
                "body": m.text,
                "interactive": m.interactive,
                "reply_option_id": m.option_id,
                "template_name": m.template_name,
                "media_type": m.media_type,
                "created_at": _iso(m.created_at),
            }
            for m in c.messages
        ]
        now = _iso(self.now())
        return {
            "id": f"app_{c.id[3:]}",
            "candidate": {
                "id": f"cand_{c.id[3:]}",
                "full_name": c.answers.get("full_name"),
                "phone": c.phone,
                "country": c.country,
                "language": language,
                "opted_out": c.opted_out,
            },
            "channel": "webchat",
            "status": c.status,
            "stage": c.stage,
            "outcome": c.outcome,
            "knockout_criterion": c.criterion if c.outcome == "knockout" else None,
            "rejection_reason": None,
            "zone": answers["zone"],
            "locations": [],
            "claim": None,
            "control": "agent",
            "control_user_id": None,
            "control_user_name": None,
            "flags": [],
            "created_at": _iso(c.created_at),
            "updated_at": now,
            "closed_at": None,
            "time_in_stage_seconds": 0,
            "total_seconds": 0,
            "answers": answers,
            "answer_status": answer_status,
            "messages": messages,
            "events": list(c.events),
            "summary": {
                "structured": {
                    "answers": answers,
                    "stage": c.stage,
                    "outcome": c.outcome,
                    "language": language,
                    "durations_seconds": {"total": 0},
                    "nudges": c.nudges,
                    "escalations": 0,
                    "flags": [],
                },
                "prose": None,
                "written_at": now,
            },
            "files": [],
            "other_applications": [],
            "open_queue_items": [],
            "interview": None,
        }

    def client(self, base_url: str = "http://fake") -> httpx.AsyncClient:
        """An HTTP client wired to this app in-process. Every exchange lands in `self.log`."""

        async def record(response: httpx.Response) -> None:
            await response.aread()
            request = response.request
            try:
                req_json = json.loads(request.content) if request.content else None
                resp_json = response.json() if response.content else None
            except ValueError:
                req_json = resp_json = None
            self.log.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "query": dict(request.url.params),
                    "request": req_json,
                    "status": response.status_code,
                    "response": resp_json,
                }
            )

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url=base_url,
            event_hooks={"response": [record]},
        )

    def tool_calls(self, phone: str) -> list[str]:
        return list(self.by_phone[phone].tool_calls)

    def _build_app(self) -> FastAPI:
        app = FastAPI()
        fake = self

        def error(status: int, code: str, message: str, **headers: str) -> JSONResponse:
            return JSONResponse({"error": code, "message": message}, status, headers=headers)

        @app.post("/api/chat/conversations", status_code=201)
        async def start(payload: dict[str, Any]):
            country = payload.get("country")
            if country not in ("ES", "MX") or set(payload) - {
                "country",
                "phone",
                "locale",
                "client",
            }:
                return error(422, "validation_error", "bad request")
            phone = payload.get("phone")
            if phone and phone in fake.by_phone:
                c = fake.by_phone[phone]
            else:
                c = Conv(
                    id="wc_" + secrets.token_hex(16),
                    phone=phone,
                    country=country,
                    locale=payload.get("locale") or ("es-MX" if country == "MX" else "es-ES"),
                    created_at=fake.now(),
                    lang="en" if payload.get("locale") == "en" else "es",
                )
                fake.convs[c.id] = c
                if phone:
                    fake.by_phone[phone] = c
            cursor = max((m.id for m in c.messages), default=0)
            return {
                "conversation_id": c.id,
                "created_at": _iso(c.created_at),
                "cursor": cursor,
            }

        @app.get("/api/chat/conversations/{cid}")
        async def get_conv(cid: str):
            c = fake.convs.get(cid)
            if c is None:
                return error(404, "not_found", "Conversation not found.")
            return {
                "conversation_id": c.id,
                "created_at": _iso(c.created_at),
                "cursor": max((m.id for m in c.messages), default=0),
            }

        def store(c: Conv, payload: dict[str, Any], signature: tuple, build) -> JSONResponse | dict:
            message_id = payload["message_id"]
            seen = c.idempotency.get(message_id)
            if seen:
                if seen[1] != signature:
                    return error(
                        409,
                        "message_id_reused",
                        "This message_id was already used for a different message.",
                    )
                return JSONResponse(
                    {"message_id": message_id, "id": seen[0], "duplicate": True}, 202
                )
            msg = build()
            c.idempotency[message_id] = (msg.id, signature)
            return {"message_id": message_id, "id": msg.id, "duplicate": False}

        @app.post("/api/chat/conversations/{cid}/messages", status_code=202)
        async def send(cid: str, payload: dict[str, Any]):
            c = fake.convs.get(cid)
            if c is None:
                return error(404, "not_found", "Conversation not found.")
            allowed = {"message_id", "text", "media"}
            if (
                set(payload) - allowed
                or not isinstance(payload.get("message_id"), str)
                or not payload["message_id"]
                or not isinstance(payload.get("text"), str)
            ):
                return error(422, "validation_error", "message_id and text are required.")
            media = payload.get("media")

            def build() -> Msg:
                m = Msg(
                    id=fake._next_id(),
                    direction="inbound",
                    sender="candidate",
                    text=payload["text"],
                    created_at=fake.now(),
                    visible_at=0.0,
                    message_id=payload["message_id"],
                    content_type="media" if media else "text",
                    media_type=media["type"] if media else None,
                )
                c.messages.append(m)
                fake._agent_turn(c, payload["text"], None)
                return m

            return store(c, payload, ("msg", payload["text"], str(media)), build)

        @app.post("/api/chat/conversations/{cid}/replies", status_code=202)
        async def reply(cid: str, payload: dict[str, Any]):
            c = fake.convs.get(cid)
            if c is None:
                return error(404, "not_found", "Conversation not found.")
            keys = {"message_id", "in_reply_to", "option_id"}
            if set(payload) != keys or not isinstance(payload["in_reply_to"], int):
                return error(
                    422,
                    "validation_error",
                    "message_id, in_reply_to, option_id required.",
                )
            latest = next(
                (m for m in reversed(c.messages) if m.direction == "outbound" and m.interactive),
                None,
            )
            target = next((m for m in c.messages if m.id == payload["in_reply_to"]), None)
            if target is None or not target.interactive:
                return error(
                    422,
                    "validation_error",
                    "in_reply_to is not an interactive message.",
                )
            if latest is None or latest.id != target.id:
                return error(409, "option_expired", "Esas opciones ya no están disponibles.")
            option = next(
                (o for o in target.interactive["options"] if o["id"] == payload["option_id"]),
                None,
            )
            if option is None:
                return error(422, "validation_error", "unknown option_id.")

            def build() -> Msg:
                m = Msg(
                    id=fake._next_id(),
                    direction="inbound",
                    sender="candidate",
                    text=option["label"],
                    created_at=fake.now(),
                    visible_at=0.0,
                    message_id=payload["message_id"],
                    content_type="button_reply",
                    option_id=option["id"],
                    reply_to={"message_id": target.id, "option_id": option["id"]},
                )
                c.messages.append(m)
                fake._agent_turn(c, option["label"], option["id"])
                return m

            return store(
                c,
                payload,
                ("reply", payload["in_reply_to"], payload["option_id"]),
                build,
            )

        @app.get("/api/chat/conversations/{cid}/messages")
        async def read(cid: str, after: int = 0, wait: int = 0, limit: int = 100):
            c = fake.convs.get(cid)
            if c is None:
                return error(404, "not_found", "Conversation not found.")
            deadline = time.monotonic() + min(wait, 30)
            while True:
                now = time.monotonic()
                visible = [m for m in c.messages if m.id > after and m.visible_at <= now]
                typing = any(m.visible_at > now for m in c.messages if m.direction == "outbound")
                if visible or now >= deadline:
                    break
                await asyncio.sleep(0.01)
            page = visible[:limit]
            cursor = page[-1].id if page else after
            body: dict[str, Any] = {
                "cursor": cursor,
                "agent_typing": typing,
                "messages": [m.chat_json() for m in page],
            }
            if fake.run_finished:
                body["run_finished"] = not typing
            return body

        def clock_state(jobs: int = 0) -> dict[str, Any]:
            return {
                "now": _iso(fake.now()),
                "offset_seconds": int(fake.offset.total_seconds()),
                "due_jobs_run": jobs,
            }

        @app.get("/api/sim/clock")
        async def clock():
            return clock_state()

        @app.post("/api/sim/clock/advance")
        async def advance(payload: dict[str, Any]):
            if set(payload) not in ({"seconds"}, {"to"}):
                return error(422, "validation_error", "Exactly one of seconds or to.")
            if "seconds" in payload:
                seconds = payload["seconds"]
                if not isinstance(seconds, int) or not 1 <= seconds <= 7_776_000:
                    return error(422, "validation_error", "seconds out of range.")
                fake.offset += timedelta(seconds=seconds)
            else:
                target = datetime.fromisoformat(payload["to"].replace("Z", "+00:00"))
                if target < fake.now():
                    return error(422, "validation_error", "The clock never moves backwards.")
                fake.offset += target - fake.now()
            return clock_state(fake.run_jobs())

        @app.post("/api/sim/clock/reset")
        async def reset():
            fake.offset = timedelta(0)
            return clock_state()

        return app
