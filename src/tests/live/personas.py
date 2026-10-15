"""The Candidates of the live smoke run, written the way people write to a recruiter.

These are ordinary `sim.personas.schema.Persona` objects played by the existing driver; only the
texts differ from the seed personas, which are tuned to the stub model. The real model interprets
the free text, so each persona asserts on the record with loose checks (`non_null`) wherever a
fair reading can vary (a schedule said as "afternoons, evenings too", a start date said as "next
Monday"). The `value` and `query` fields exist because the schema requires them for a typed
answer; only the stub model reads them, the real model never sees them.

Latency is not a pass or fail condition here (`max_reply_latency_s` is lifted): the report shows
it against the Phase 1 targets instead (first reply 5 s, later 10 s).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sim.personas.schema import Persona

NO_LATENCY_LIMIT = 3600.0
FIELD_ORDER = [
    "full_name",
    "zone",
    "driver_license",
    "availability",
    "preferred_schedule",
    "experience_years",
    "experience_platforms",
    "start_date",
]
QUALIFIED_FIELDS: dict[str, Any] = {
    "full_name": {"non_null": True},
    "zone": {"non_null": True},
    "driver_license": {"value": True},
    "availability": {"non_null": True},
    "preferred_schedule": {"non_null": True},
    "experience_years": {"non_null": True},
    "experience_platforms": {"non_null": True},
    "start_date": {"non_null": True},
}


def _qualified_expect(**overrides: Any) -> dict[str, Any]:
    expect: dict[str, Any] = {
        "stage": "qualified",
        "status": "open",
        "fields": dict(QUALIFIED_FIELDS),
        "field_order": list(FIELD_ORDER),
        "events": ["application_started", "answer_recorded", "screening_finished"],
        "forbidden_events": ["knockout", "escalation_raised", "withdrawn", "nudge_sent"],
        "tools": ["record_answer", "finish_screening"],
        "forbidden_tools": ["request_review", "raise_escalation", "record_backout"],
        "nudges": 0,
        "forbidden_agent_text": ["Criba"],
        "max_reply_latency_s": NO_LATENCY_LIMIT,
    }
    return expect | overrides


def qualified_english() -> Persona:
    """Flow 1: an English-speaking Candidate in Madrid completes Screening."""
    return Persona.model_validate(
        {
            "id": "live_qualified_en",
            "description": "English-speaking Candidate in Madrid, answers in full sentences.",
            "identity": {
                "name": "Daniel Foster",
                "phone": "+34600000901",
                "country": "ES",
                "locale": "en",
            },
            "opening": {
                "text": "Hi, I saw your ad for delivery riders in Madrid. I'd like to apply."
            },
            "answers": {
                "full_name": [{"text": "My name is Daniel Foster", "value": "Daniel Foster"}],
                "zone": [{"text": "I live in Madrid, near Chamberí", "query": "Madrid, Chamberí"}],
                "driver_license": [
                    {"text": "Yes, I've had my driving licence for about six years", "value": "yes"}
                ],
                "availability": [
                    {"text": "I'm free full time, Monday to Saturday", "value": ["full_time"]}
                ],
                "preferred_schedule": [
                    {
                        "text": "Mostly afternoons, evenings are fine too",
                        "value": ["afternoon", "evening"],
                    }
                ],
                "experience_years": [{"text": "About two and a half years", "value": 2.5}],
                "experience_platforms": [
                    {"text": "Mainly Glovo, and some Uber Eats", "value": ["glovo", "uber_eats"]}
                ],
                "start_date": [
                    {"text": "I could start next Monday", "value": "next:mon"},
                    # "next Monday" fits two Mondays within the week: the backend asks with
                    # both dates as buttons (ADR 0054), and the Candidate takes the first.
                    {"text": "This coming Monday", "pick_option": 0},
                ],
            },
            "expect": _qualified_expect(
                final_language="en",
                fields=QUALIFIED_FIELDS | {"full_name": {"value": "Daniel Foster"}},
            ),
            "seed_assumptions": ["Madrid, Chamberí is inside at least one ES Service Area."],
        }
    )


def knockout_then_review() -> Persona:
    """Flow 2: no driving licence, a Knockout, then the Candidate asks for a review."""
    return Persona.model_validate(
        {
            "id": "live_knockout_review_mx",
            "description": "Mexican Candidate without a licence who disputes the Knockout.",
            "quirks": ["disputes_knockout"],
            "identity": {
                "name": "Luis Hernández",
                "phone": "+525500000901",
                "country": "MX",
                "locale": "es-MX",
            },
            "opening": {
                "text": "Hola, vi el anuncio para repartidores y quiero trabajar con ustedes"
            },
            "answers": {
                "full_name": [{"text": "Me llamo Luis Hernández", "value": "Luis Hernández"}],
                "zone": [{"text": "Vivo en Puebla, por el Centro", "query": "Centro, Puebla"}],
                "driver_license": [
                    {"text": "No, todavía no tengo licencia de manejo", "value": "no"}
                ],
            },
            "zone_choice": "Centro",
            "interrupts": [
                {
                    "at": "after_knockout",
                    "say": {"text": "Creo que hay un error, quisiera pedir una revisión"},
                    "then": "stop",
                }
            ],
            "expect": {
                "stage": "driver_license",
                "status": "closed",
                "outcome": "knockout",
                "knockout_criterion": "driver_license",
                "knockout_template": "es-MX",
                "fields": {"full_name": {"value": "Luis Hernández"}, "zone": {"non_null": True}},
                "fields_unanswered": [
                    "availability",
                    "preferred_schedule",
                    "experience_years",
                    "start_date",
                ],
                "field_order": ["full_name", "zone", "driver_license"],
                "events": [
                    "application_started",
                    "answer_recorded",
                    "knockout",
                    "review_requested",
                ],
                "event_counts": [{"kind": "knockout", "count": 1}],
                "forbidden_events": ["screening_finished", "nudge_sent", "escalation_raised"],
                "tools": ["record_answer", "request_review"],
                "forbidden_tools": ["finish_screening"],
                "forbidden_agent_text": ["Criba"],
                "max_reply_latency_s": NO_LATENCY_LIMIT,
            },
            "seed_assumptions": [
                '"Puebla" names three MX Zones (Angelópolis, Centro, La Paz); the Candidate '
                "taps Centro."
            ],
        }
    )


def cdmx_zone_list() -> Persona:
    """Flow 3: a neighbourhood that is not a Zone gets the city's Zones as a list; one is tapped.

    The Candidate stops at the licence question: the flow tests the Zone step only.
    """
    return Persona.model_validate(
        {
            "id": "live_cdmx_zone_mx",
            "description": "Candidate in Cuajimalpa, Mexico City, which is not a seeded Zone.",
            "quirks": ["ambiguous_zone"],
            "identity": {
                "name": "Valeria Núñez",
                "phone": "+525500000902",
                "country": "MX",
                "locale": "es-MX",
            },
            "opening": {"text": "Hola, me interesa el trabajo de repartidor"},
            "answers": {
                "full_name": [{"text": "Valeria Núñez", "value": "Valeria Núñez"}],
                "zone": [{"text": "Ciudad de México, Cuajimalpa", "query": "Ciudad de México"}],
            },
            "zone_choice": "Polanco",
            "interrupts": [{"at": "driver_license", "then": "stop"}],
            "expect": {
                "stage": "driver_license",
                "status": "open",
                "fields": {"full_name": {"value": "Valeria Núñez"}, "zone": {"non_null": True}},
                "field_order": ["full_name", "zone"],
                "events": ["application_started", "answer_recorded"],
                "forbidden_events": ["knockout", "escalation_raised", "nudge_sent"],
                "tools": ["record_answer"],
                "forbidden_agent_text": ["Criba"],
                "max_reply_latency_s": NO_LATENCY_LIMIT,
            },
            "seed_assumptions": [
                '"Ciudad de México" ranks nine Zones of one city; Cuajimalpa is not a seeded Zone.'
            ],
        }
    )


def cdmx_unknown_place_list() -> Persona:
    """Flow 3b, the demo chat of 2026-10-11 (ADR 0062): "Ciudad de Mexico en Tepito".

    Tepito is not a seeded Zone, so the search matches the city only. The city's Zones must
    reach the Candidate as a list with the agent's first reply, whether or not the agent
    records the ids; one is tapped. The second Zone reply is the demo Candidate's own typed
    fallback, sent only when no list comes (the flow then fails its checks but still ends).
    The Candidate stops at the licence question: the flow tests the Zone step only.
    """
    return Persona.model_validate(
        {
            "id": "live_cdmx_unknown_place_mx",
            "description": "Candidate in Tepito, Mexico City, which is not a seeded Zone.",
            "quirks": ["ambiguous_zone"],
            "identity": {
                "name": "Fernando Fernández",
                "phone": "+525500000903",
                "country": "MX",
                "locale": "es-MX",
            },
            "opening": {"text": "Hola!"},
            "answers": {
                "full_name": [{"text": "Fernando Fernández", "value": "Fernando Fernández"}],
                "zone": [
                    {"text": "Ciudad de Mexico en Tepito", "query": "Tepito, Ciudad de México"},
                    {"text": "Centro, ciudad de México", "query": "Centro, Ciudad de México"},
                ],
            },
            "zone_choice": "Centro",
            "interrupts": [{"at": "driver_license", "then": "stop"}],
            "expect": {
                "stage": "driver_license",
                "status": "open",
                "fields": {
                    "full_name": {"value": "Fernando Fernández"},
                    "zone": {"non_null": True},
                },
                "field_order": ["full_name", "zone"],
                "events": ["application_started", "answer_recorded"],
                "forbidden_events": ["knockout", "escalation_raised", "nudge_sent"],
                "tools": ["record_answer"],
                "forbidden_agent_text": ["Criba"],
                "max_reply_latency_s": NO_LATENCY_LIMIT,
            },
            "seed_assumptions": [
                '"Ciudad de México" ranks nine Zones of one city; Tepito is not a seeded Zone.'
            ],
        }
    )


def qualified_for_booking() -> Persona:
    """Flow 4: a Spanish-speaking Candidate in Madrid qualifies; the flow approves and books."""
    return Persona.model_validate(
        {
            "id": "live_booking_es",
            "description": "Candidate in Madrid who qualifies, is Approved and books a slot.",
            "identity": {
                "name": "Marta Ruiz",
                "phone": "+34600000902",
                "country": "ES",
                "locale": "es-ES",
            },
            "opening": {"text": "Buenas, me interesa el puesto de repartidor que anunciáis"},
            "answers": {
                "full_name": [{"text": "Marta Ruiz Pérez", "value": "Marta Ruiz Pérez"}],
                "zone": [{"text": "Vivo en Madrid, en Retiro", "query": "Madrid, Retiro"}],
                "driver_license": [
                    {"text": "Sí, tengo el carnet B desde hace años", "value": "yes"}
                ],
                "availability": [
                    {"text": "Puedo trabajar a jornada completa", "value": ["full_time"]}
                ],
                "preferred_schedule": [
                    {"text": "Por las tardes me viene mejor", "value": ["afternoon"]}
                ],
                "experience_years": [{"text": "Llevo unos tres años", "value": 3}],
                "experience_platforms": [
                    {"text": "He trabajado con Glovo y Just Eat", "value": ["glovo", "just_eat"]}
                ],
                "start_date": [
                    {"text": "Podría empezar el lunes que viene", "value": "next:mon"},
                    # If the agent reads it as two Mondays, the backend asks (ADR 0054).
                    {"text": "Este lunes", "pick_option": 0},
                ],
            },
            "expect": _qualified_expect(
                fields=QUALIFIED_FIELDS | {"full_name": {"value": "Marta Ruiz Pérez"}},
                # Spain asks for the barrio; colonia is the Mexican word (the Screener skill).
                forbidden_agent_text=["Criba", "colonia"],
            ),
            "seed_assumptions": ["Madrid, Retiro is inside at least one ES Service Area."],
        }
    )


# ---- demo chat 2026-10-10: "no app" and "el próximo domingo" (ADRs 0053, 0054) -------------

# Per language: the opening and the answers before the field under test, one at a time (an
# opening with three fields made Haiku call `record_answer(zone, null)` in parallel with the
# search, which re-asked the Zone later; that is not what these flows test). `query` and
# `value` are for the schema only.
_SHORT_OPENINGS: dict[str, dict[str, Any]] = {
    "es-MX": {
        "identity": {"name": "Ramiro Rodríguez", "country": "MX", "locale": "es-MX"},
        "opening": {"text": "Hola, quiero trabajar de repartidor"},
        "before": {
            "zone": [{"text": "Vivo en el Centro de Puebla", "query": "Centro, Puebla"}],
            "driver_license": [{"text": "Sí, tengo licencia", "value": "yes"}],
            "availability": [{"text": "Medio tiempo", "value": ["part_time"]}],
            "preferred_schedule": [{"text": "En la tarde", "value": ["afternoon"]}],
        },
    },
    "es-ES": {
        "identity": {"name": "Lucía Romero", "country": "ES", "locale": "es-ES"},
        "opening": {"text": "Hola, busco trabajo de repartidora"},
        "before": {
            "zone": [{"text": "En Madrid, en Chamberí", "query": "Madrid, Chamberí"}],
            "driver_license": [{"text": "Sí, tengo carnet", "value": "yes"}],
            "availability": [{"text": "Media jornada", "value": ["part_time"]}],
            "preferred_schedule": [{"text": "Por las tardes", "value": ["afternoon"]}],
        },
    },
    "en": {
        "identity": {"name": "Sam Carter", "country": "ES", "locale": "en"},
        "opening": {"text": "Hi, I'd like to apply as a delivery rider"},
        "before": {
            "zone": [{"text": "Madrid, in Chamberí", "query": "Madrid, Chamberí"}],
            "driver_license": [{"text": "Yes, I have one", "value": "yes"}],
            "availability": [{"text": "Part-time", "value": ["part_time"]}],
            "preferred_schedule": [{"text": "Afternoons", "value": ["afternoon"]}],
        },
    },
}

# What the Candidate says about the apps after years of delivery on their own (ADR 0053).
NO_APP_ANSWERS: dict[str, tuple[str, str]] = {
    "es-MX": ("tengo 10 años repartiendo como independiente", "Por mi cuenta"),
    "es-ES": ("Llevo cuatro años repartiendo para restaurantes", "Ninguna, siempre por libre"),
    "en": ("About five years delivering for a local pizzeria", "None, I never used an app"),
}


def _short_screening(
    pid: str, language: str, answers: dict[str, Any], *, stop_at: str, expect: dict[str, Any]
) -> Persona:
    base = _SHORT_OPENINGS[language]
    return Persona.model_validate(
        {
            "id": pid,
            "description": f"Short Screening in {language}, stopped at {stop_at}.",
            # The phone is the schema's; `LiveStack.play` uses a fresh one for every run.
            "identity": {
                **base["identity"],
                "phone": "+525500000999" if base["identity"]["country"] == "MX" else "+34600000999",
            },
            "opening": base["opening"],
            "answers": {
                "full_name": [
                    {"text": base["identity"]["name"], "value": base["identity"]["name"]}
                ],
                **base["before"],
                **answers,
            },
            "interrupts": [{"at": stop_at, "then": "stop"}],
            "expect": {
                "status": "open",
                "events": ["application_started", "answer_recorded"],
                "forbidden_events": ["knockout", "escalation_raised", "nudge_sent"],
                "tools": ["record_answer"],
                "forbidden_agent_text": ["Criba"],
                "max_reply_latency_s": NO_LATENCY_LIMIT,
                **expect,
            },
        }
    )


def no_app(language: str) -> Persona:
    """Years of delivery on their own, then "no app" for the platforms: [] at once."""
    years, platforms = NO_APP_ANSWERS[language]
    return _short_screening(
        f"live_no_app_{language.replace('-', '_').lower()}",
        language,
        {
            "experience_years": [{"text": years, "value": 5}],
            "experience_platforms": [{"text": platforms, "value": []}],
        },
        stop_at="start_date",
        expect={"stage": "start_date", "fields": {"experience_platforms": {"value": []}}},
    )


@dataclass(frozen=True)
class DateReading:
    """A start-date phrase and how the backend should treat it (ADR 0054).

    `weekday` and `weeks` give the expected date: the next occurrence of the weekday after
    today, plus `weeks` weeks; or `days` from today. With `ambiguous`, both readings come back
    as buttons and the Candidate taps `pick` (0: the earlier, 1: the later).
    """

    key: str
    language: str
    text: str
    ambiguous: bool = False
    weekday: int | None = None  # Monday 0
    weeks: int = 0
    days: int | None = None
    month_day: tuple[int, int] | None = None
    pick: int = 0
    pick_text: str = ""


DATE_READINGS: tuple[DateReading, ...] = (
    DateReading(
        "proximo_domingo_mx",
        "es-MX",
        "El próximo domingo me queda bien",
        ambiguous=True,
        weekday=6,
        pick=1,
        pick_text="El de la otra semana",
    ),
    DateReading(
        "next_sunday_en",
        "en",
        "Next Sunday works for me",
        ambiguous=True,
        weekday=6,
        pick=0,
        pick_text="This coming Sunday",
    ),
    DateReading("este_domingo_es", "es-ES", "Este domingo", weekday=6),
    DateReading(
        "domingo_semana_mx", "es-MX", "El domingo de la próxima semana", weekday=6, weeks=1
    ),
    DateReading("manana_es", "es-ES", "Mañana", days=1),
    # The demo chat of 2026-10-10 asked for "the exact date" after this, with no tool call.
    DateReading("tomorrow_en", "en", "Tomorrow works for me", days=1),
    DateReading("el_lunes_mx", "es-MX", "El lunes", weekday=0),
    DateReading("veinte_octubre_es", "es-ES", "El 20 de octubre", month_day=(10, 20)),
)


def start_date_reading(reading: DateReading) -> Persona:
    """Everything up to the start date, the phrase, and a tap if the backend asks."""
    replies: list[dict[str, Any]] = [{"text": reading.text, "value": "+1d"}]
    if reading.ambiguous:
        replies.append({"text": reading.pick_text, "pick_option": reading.pick})
    return _short_screening(
        f"live_date_{reading.key}",
        reading.language,
        {
            "experience_years": [{"text": "2", "value": 2}],
            "experience_platforms": [{"text": "Glovo", "value": ["glovo"]}],
            "start_date": replies,
        },
        stop_at="confirm",
        expect={"stage": "confirm", "fields": {"start_date": {"non_null": True}}},
    )


def demo_zone_country() -> Persona:
    """The demo chat of 2026-10-10: English on a web chat opened as Mexico, living in Chamberí
    (Madrid), "tomorrow works for me". The Application moves to Spain and stays English
    (ADR 0057); tomorrow is recorded with no question."""
    return Persona.model_validate(
        {
            "id": "live_demo_zone_country",
            "description": "English on an MX chat, lives in Chamberí, starts tomorrow.",
            # The phone is the schema's; `LiveStack.play` uses a fresh one for every run.
            "identity": {
                "name": "Ramiro Salcedo",
                "phone": "+525500000998",
                "country": "MX",
                "locale": "en",
            },
            "opening": {"text": "Hey!"},
            "answers": {
                "full_name": [{"text": "Ramiro Salcedo", "value": "Ramiro Salcedo"}],
                "zone": [{"text": "I live in Chamberi", "query": "Chamberí"}],
                "driver_license": [{"text": "Yes, I have one", "value": "yes"}],
                "availability": [{"text": "Full time", "value": ["full_time"]}],
                "preferred_schedule": [{"text": "Evenings", "value": ["evening"]}],
                "experience_years": [{"text": "2", "value": 2}],
                "experience_platforms": [{"text": "Uber Eats", "value": ["uber_eats"]}],
                "start_date": [{"text": "tomorrow works for me", "value": "+1d"}],
            },
            "interrupts": [{"at": "confirm", "then": "stop"}],
            "expect": {
                "stage": "confirm",
                "status": "open",
                "final_language": "en",
                "final_country": "ES",
                "fields": {"zone": {"value": "Chamberí"}, "start_date": {"non_null": True}},
                "events": ["application_started", "answer_recorded"],
                "forbidden_events": ["knockout", "escalation_raised", "nudge_sent"],
                "tools": ["record_answer"],
                "forbidden_agent_text": ["Criba"],
                "max_reply_latency_s": NO_LATENCY_LIMIT,
            },
            "seed_assumptions": ['"Chamberí" names one Zone, in Madrid, inside a Service Area.'],
        }
    )


def texas_funnel_none() -> Persona:
    """The demo chat of 2026-10-12 (ADR 0068): an English Candidate on an MX chat opens with his
    name and lives in Texas, which matches no Zone. The backend asks once which of our cities
    he can work in; he taps "None of these": the Zone Knockout, sent at once."""
    return Persona.model_validate(
        {
            "id": "live_texas_funnel_none",
            "description": "English on an MX chat, opens with his name, lives in Texas.",
            "identity": {
                "name": "Cody Walker",
                "phone": "+525500000904",
                "country": "MX",
                "locale": "en",
            },
            "opening": {"text": "Hi, I'm Cody Walker, I saw your delivery job ad"},
            "answers": {
                "full_name": [{"text": "Cody Walker", "value": "Cody Walker"}],
                "zone": [{"text": "I live in Texas!", "query": "Texas"}],
            },
            "zone_choice": "None of these",
            "expect": {
                "stage": "zone",
                "status": "closed",
                "outcome": "knockout",
                "knockout_criterion": "zone",
                "knockout_template": "en",
                "fields": {"full_name": {"non_null": True}},
                "fields_unanswered": ["zone", "driver_license"],
                "field_order": ["full_name"],
                "events": [
                    "application_started",
                    "answer_recorded",
                    "answer_ambiguous",
                    "knockout",
                    "screening_finished",
                ],
                "event_counts": [
                    {"kind": "knockout", "count": 1},
                    {"kind": "answer_ambiguous", "count": 1},
                    {"kind": "answer_invalid", "count": 0},
                ],
                "forbidden_events": ["reopened", "escalation_raised", "nudge_sent"],
                "tools": ["record_answer"],
                "forbidden_tools": ["raise_escalation", "request_review", "finish_screening"],
                "forbidden_agent_text": ["Criba"],
                "max_reply_latency_s": NO_LATENCY_LIMIT,
            },
            "seed_assumptions": [
                '"Texas" is no seeded Zone, alias or city, so search_zones finds nothing.',
                "Mexico's cities with a Service Area are Ciudad de México, Guadalajara, "
                "Monterrey and Puebla.",
            ],
        }
    )


def texas_funnel_typed() -> Persona:
    """ADR 0068, the second answer: after the cities list the Candidate does not tap but types
    another place that matches no Zone ("I live in the united states", as in the demo chat). That
    second out-of-area answer is the Zone Knockout, with no Zone stored."""
    persona = texas_funnel_none().model_dump(mode="json", exclude_unset=True)
    persona["id"] = "live_texas_funnel_typed"
    persona["identity"]["phone"] = "+525500000906"
    persona["description"] = "English on an MX chat, Texas, then the united states, typed."
    persona.pop("zone_choice")
    persona["answers"]["zone"] = [
        {"text": "I live in Texas!", "query": "Texas"},
        # `pick_option` names no option, so the Candidate types it instead of tapping a row.
        {"text": "I live in the united states", "query": "united states", "pick_option": "typed"},
    ]
    return Persona.model_validate(persona)


def dallas_funnel_city() -> Persona:
    """ADR 0068, the other answer: a place that matches no Zone (Dallas) is asked as the cities
    list; the Candidate taps Monterrey, gets Monterrey's Zones and taps Cumbres. No Knockout.
    The Candidate stops at the licence question: the flow tests the Zone step only."""
    return Persona.model_validate(
        {
            "id": "live_dallas_funnel_city",
            "description": "English on an MX chat, lives in Dallas, can work in Monterrey.",
            "quirks": ["ambiguous_zone"],
            "identity": {
                "name": "Dana Whitfield",
                "phone": "+525500000905",
                "country": "MX",
                "locale": "en",
            },
            "opening": {"text": "Hello, I'd like to apply as a delivery driver"},
            "answers": {
                "full_name": [{"text": "Dana Whitfield", "value": "Dana Whitfield"}],
                "zone": [
                    {"text": "I live in Dallas", "query": "Dallas"},
                    {"text": "Monterrey", "pick_option": "Monterrey"},
                    {"text": "Cumbres", "pick_option": "Cumbres"},
                ],
            },
            "interrupts": [{"at": "driver_license", "then": "stop"}],
            "expect": {
                "stage": "driver_license",
                "status": "open",
                "fields": {
                    "full_name": {"non_null": True},
                    "zone": {"non_null": True},
                },
                "field_order": ["full_name", "zone"],
                "events": ["application_started", "answer_recorded", "answer_ambiguous"],
                "event_counts": [{"kind": "answer_invalid", "count": 0}],
                "forbidden_events": ["knockout", "escalation_raised", "nudge_sent"],
                "tools": ["record_answer"],
                "forbidden_agent_text": ["Criba"],
                "max_reply_latency_s": NO_LATENCY_LIMIT,
            },
            "seed_assumptions": [
                '"Dallas" is no seeded Zone, alias or city, so search_zones finds nothing.',
                "Monterrey has covered Zones including Cumbres.",
            ],
        }
    )
