# Switching to English mid-Screening, then Approval and booking

The Candidate lines were typed by hand, as a test Candidate, in the web app's Test chat; the typos are left as typed. The agent replies are real Haiku 5.5 output.

Source: the demo stack `criba-demo` (http://localhost:8080, real Claude Haiku 5.5 turns through OpenRouter, Sonnet 5.5 summaries), read on 2026-10-12 through the Operator API. The transcript, the events and the JSON all come from this one Application.

## What it shows

- The Candidate starts in Spanish and moves to English. The first English message does not switch the language; on the second, Jev reads a switch and code stores `en` as the Application's language ([0051](../decisions/0051-jev-router-decides-language-and-reads-tone.md), [0047](../decisions/0047-language-stays-in-step-one-word-never-switches.md)).
- One message, "3 years of experience with Rappi", answers two questions; both are recorded ([0042](../decisions/0042-agent-interprets-code-verifies.md)).
- After the Approval, the agent's message and the slot list (with "Another time") are in English, and the tapped slot books the Interview ([0038](../decisions/0038-after-approval-a-system-turn-or-a-template.md), [0040](../decisions/0040-single-booking-path.md), [0064](../decisions/0064-other-time-sends-the-booking-link-from-code.md)).

**What to notice.** Not every reply followed the switch. Jev read it on "Next monday works fine for me!" and code stored `en`, so that turn's buttons came in English. But the agent's text on that turn, the read-back, the close and the booking confirmation stayed in Spanish. That is a gap, not the design: ADR 0051 has code decide the reply language. A cleaner run of the same capability, an explicit request, is the live smoke flow at the end.

| Fact | Value |
|---|---|
| Application | `app_01M4RMXZ07G8850XY3W3JHSYGT` |
| Candidate | Ramiro Salcedo, +34600000981 (synthetic) |
| Started (UTC) | 2026-10-12 18:10:22 |
| State when read | stage `interview_booked`, status `open`, outcome `null` |
| Language, country | `en`, `ES` |

## Transcript

```text
Candidate: Hola!
Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestra política: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
Candidate: Ramiro Salcedo
Agent: Gracias, Ramiro. ¿En qué barrio y ciudad vives?
Candidate: Madrid, Retiro
Agent: ¡Perfecto, Ramiro! ¿Tienes carnet de conducir vigente? [buttons: Sí, tengo carnet | No]
Candidate (tapped): Sí, tengo carnet
Agent: Genial, ya tienes el carnet registrado. ¿Qué disponibilidad tienes: tiempo completo, medio tiempo, fines de semana, o cualquiera de estas? [buttons: Jornada completa | Media jornada | Fines de semana]
Candidate (tapped): Jornada completa
Agent: Perfecto, Ramiro. ¿En qué horario prefieres trabajar: por la mañana, por la tarde, por la noche o te da igual? [list: Mañana | Tarde | Noche | Flexible]
Candidate (tapped): Flexible
Agent: ¿Cuántos años de experiencia tienes repartiendo a domicilio? Si nunca lo has hecho, dime 0.
Candidate: 3 years of experience with Rappi
Agent: ¡Qué bien, Ramiro! ¿Desde qué fecha podrías empezar?
Candidate: Next monday works fine for me!
Agent: Para no equivocarnos, ¿cuál lunes te viene mejor: el 19 u el 26 de octubre? [buttons: Monday 19 Oct | Monday 26 Oct]
Candidate (tapped): Monday 26 Oct
Agent: Ramiro, este es el resumen de tu solicitud:
    - Nombre: Ramiro Salcedo
    - Zona: Retiro, Madrid
    - Carnet de conducir: sí
    - Disponibilidad: tiempo completo
    - Horario: flexible
    - Experiencia: 3 años con Rappi
    - Inicio: lunes 26 de octubre

    ¿Es correcto?
Candidate: Everytthing looks correct now!
Agent: ¡Todo listo, Ramiro! Un reclutador de Grupo Sazón revisará tu solicitud y te escribirá por aquí con el siguiente paso.
Agent: Great news, Ramiro: you've been approved! Pick a slot for your interview below, or tap "Another time" to see other options. [list: Tue 13 · 10:00 | Tue 13 · 12:00 | Tue 13 · 16:00 | Another time]
Candidate (tapped): Tue 13 · 10:00
Agent: ¡Entrevista confirmada, Ramiro! Será el martes 13 de octubre a las 10:00 en Sazón Madrid Retiro, Calle Ejemplo 3, Retiro, Madrid. Te adjunto la invitación en este mensaje.
```

## Also on the record

From the Application detail API (`GET /api/applications/app_01M4RMXZ07G8850XY3W3JHSYGT`), the fields this sample is about. The communication notes are written by code from the Jev readings and live only on this API, not in the Summary ([0056](../decisions/0056-the-summary-is-prose-in-plain-words-and-the-notes-stay-on-the-api.md)).

```json
{
  "communication_notes": [
    {
      "kind": "writes_in",
      "text": "Writes in English: short, direct questions work.",
      "stage": null,
      "at": "2026-10-12T18:10:22Z",
      "message_id": 82
    },
    {
      "kind": "language_switch",
      "text": "Switched to English at the start date question (20:11): reply in English.",
      "stage": "start_date",
      "at": "2026-10-12T18:11:42Z",
      "message_id": 96
    }
  ],
  "message_signals": [
    {
      "message_id": 96,
      "stage": "start_date",
      "at": "2026-10-12T18:11:42Z",
      "fired": [],
      "language_changed": true,
      "escalated": false
    }
  ]
}
```

## The same capability in the live smoke run

A second, separate run, for comparison: flow 17 of [`live-smoke.md`](../evidence/live-smoke.md) (its own stack on
port 18300, 2026-10-12, real Haiku 5.5), not `criba-demo`. A scripted Candidate asks for English, in
Spanish. The transcript is copied from that file. The JSON above is not from this run: the live smoke records its checks on
the record (listed in `live-smoke.md`: the language is `en` after the request, the tapped availability is recorded), not an
export.

```text
Candidate: Hola, busco trabajo de repartidor
Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestra política: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
Candidate: Me llamo Ramiro Salcedo
Agent: Gracias, Ramiro. ¿En qué barrio y ciudad vives?
Candidate: Vivo en Chamberí, Madrid
Agent: Perfecto, Chamberí en Madrid. ¿Tienes carnet de conducir vigente? [buttons: Sí, tengo carnet | No]
Candidate: Sí, tengo carnet
Agent: Genial. ¿Qué disponibilidad tienes: tiempo completo, parcial o fines de semana? [buttons: Jornada completa | Media jornada | Fines de semana]
Candidate: me preguntas en inglés porfi
Agent: Sure, we can continue in English. Which availability works for you: full time, part time, or weekends? [buttons: Full-time | Part-time | Weekends]
Candidate (tapped): Full-time
Agent: Thanks, availability is saved. What time of day do you prefer to work: morning, afternoon, evening, or flexible? [list: Morning | Afternoon | Evening | Flexible]
```

An explicit request for a language decides the stored language, whatever language it is written in ([0061](../decisions/0061-an-explicit-request-for-a-language-decides-the-language.md)).

## Events on the record

Ids, codes and canonical values only, never the Candidate's words ([0035](../decisions/0035-no-candidate-free-text-in-events.md)). Time is UTC.

```text
18:10:22  application_started  candidate                      {"channel": "webchat"}
18:10:37  answer_recorded      agent                          {"field": "full_name", "message_id": 84}
18:10:37  stage_changed        agent                          {"from": "greeting", "to": "zone"}
18:10:48  answer_recorded      agent                          {"field": "zone", "value": "zone_es_madrid_retiro", "message_id": 86, "locations": ["loc_es_madrid_centro", "loc_es_madrid_retiro", "loc_es_madrid_salamanca"]}
18:10:48  stage_changed        agent                          {"from": "zone", "to": "driver_license"}
18:10:53  answer_recorded      agent                          {"field": "driver_license", "value": true, "message_id": 88}
18:10:53  stage_changed        agent                          {"from": "driver_license", "to": "availability"}
18:10:58  answer_recorded      agent                          {"field": "availability", "value": ["full_time"], "message_id": 90}
18:10:58  stage_changed        agent                          {"from": "availability", "to": "preferred_schedule"}
18:11:06  answer_recorded      agent                          {"field": "preferred_schedule", "value": ["flexible"], "message_id": 92}
18:11:06  stage_changed        agent                          {"from": "preferred_schedule", "to": "experience"}
18:11:25  answer_recorded      agent                          {"field": "experience_years", "value": 3.0, "message_id": 94}
18:11:27  answer_recorded      agent                          {"field": "experience_platforms", "value": ["rappi"], "message_id": 94}
18:11:27  stage_changed        agent                          {"from": "experience", "to": "start_date"}
18:11:45  answer_ambiguous     agent                          {"field": "start_date", "options": [{"id": "2026-10-19", "label": "Monday 19 Oct"}, {"id": "2026-10-26", "label": "Monday 26 Oct"}], "message_id": 96}
18:11:52  answer_recorded      agent                          {"field": "start_date", "value": "2026-10-26", "message_id": 98}
18:11:52  stage_changed        agent                          {"from": "start_date", "to": "confirm"}
18:12:05  stage_changed        agent                          {"from": "confirm", "to": "qualified"}
18:12:05  screening_finished   agent                          {"result": "qualified"}
18:12:05  approval_requested   agent                          {"queue_item_id": "qi_01M4RN1473QPNGN98X9ET9BV2B", "reason": null}
18:12:05  summary_written      system
18:18:24  claim_taken          user (Operator (synthetic))
18:18:25  approved             user (Operator (synthetic))
18:18:25  system_turn          system                         {"turn": "[system] approved"}
18:18:27  slots_offered        agent                          {"slot_refs": ["so_zsPD2ehGQOsK", "so_mqnYzLEnZ6Bp", "so_m-jZTYBjb96e"], "slot_ids": ["slot_es_madrid_retiro_20261013_1000", "slot_es_madrid_centro_20261013_1200", "slot_es_madrid_centro_20261013_1600"], "reschedule": false, "expires_at": "2026-10-14T18:18:27Z"}
18:18:50  stage_changed        agent                          {"from": "approved", "to": "interview_booked"}
18:18:50  interview_booked     agent                          {"booking_id": "bk_01M4RNDF3Y0FCTVGAXET1VS103", "slot_id": "slot_es_madrid_retiro_20261013_1000", "location_id": "loc_es_madrid_retiro", "starts_at": "2026-10-13T08:00:00Z", "via": "chat", "ics_uid": "bk_01M4RNDF3Y0FCTVGAXET1VS103@criba.local", "invite_after_message": 123}
```

## Application JSON

From `GET /api/applications/app_01M4RMXZ07G8850XY3W3JHSYGT/export`, unchanged.

```json
{
  "application_id": "app_01M4RMXZ07G8850XY3W3JHSYGT",
  "candidate_id": "cand_01M4RMXQ1B90BHV9H66JF4NFVD",
  "channel": "webchat",
  "country": "ES",
  "language": "en",
  "status": "open",
  "stage": "interview_booked",
  "outcome": null,
  "knockout_criterion": null,
  "rejection_reason": null,
  "answers": {
    "full_name": "Ramiro Salcedo",
    "zone": {
      "id": "zone_es_madrid_retiro",
      "name": "Retiro",
      "city": "Madrid",
      "country": "ES"
    },
    "driver_license": true,
    "availability": [
      "full_time"
    ],
    "preferred_schedule": [
      "flexible"
    ],
    "experience_years": 3.0,
    "experience_platforms": [
      "rappi"
    ],
    "start_date": "2026-10-26"
  },
  "start_date_far": false,
  "locations": [
    "loc_es_madrid_centro",
    "loc_es_madrid_retiro",
    "loc_es_madrid_salamanca"
  ],
  "created_at": "2026-10-12T18:10:22Z",
  "updated_at": "2026-10-12T18:18:53Z",
  "qualified_at": "2026-10-12T18:12:05Z",
  "approved_at": "2026-10-12T18:18:25Z",
  "closed_at": null,
  "interview": {
    "starts_at": "2026-10-13T08:00:00Z",
    "ends_at": "2026-10-13T08:30:00Z",
    "local_label": "Tue 13 · 10:00",
    "location_name": "Sazón Madrid Retiro",
    "address": "(synthetic) Calle Ejemplo 3, Retiro, Madrid"
  },
  "summary": {
    "structured": {
      "answers": {
        "full_name": "Ramiro Salcedo",
        "zone": {
          "id": "zone_es_madrid_retiro",
          "name": "Retiro",
          "city": "Madrid",
          "country": "ES"
        },
        "driver_license": true,
        "availability": [
          "full_time"
        ],
        "preferred_schedule": [
          "flexible"
        ],
        "experience_years": 3.0,
        "experience_platforms": [
          "rappi"
        ],
        "start_date": "2026-10-26"
      },
      "stage": "qualified",
      "outcome": null,
      "language": "en",
      "durations_seconds": {
        "total": 103,
        "by_stage": {
          "greeting": 15,
          "zone": 11,
          "driver_license": 5,
          "availability": 5,
          "preferred_schedule": 8,
          "experience": 21,
          "start_date": 25,
          "confirm": 13,
          "qualified": 0
        }
      },
      "nudges": 0,
      "escalations": 0,
      "flags": [],
      "knockout_criterion": null,
      "reopened_count": 0
    },
    "prose": "Ramiro Salcedo completed the screening and the application is qualified, now waiting for your approval. He lives in Retiro, Madrid, holds a driving licence, and reports three years of delivery experience with Rappi, which is his own statement and not verified. When his start date was unclear (\"next Monday\"), he confirmed Monday 26 October after being offered two options. He wrote in English after the chat opened in Spanish, so you may want to use English when you contact him. No contradictions or other open questions came up.",
    "written_at": "2026-10-12T18:12:05Z"
  }
}
```
