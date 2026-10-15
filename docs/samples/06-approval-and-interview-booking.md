# Approval, then an Interview booked in the chat, with an .ics

The Candidate is a scripted, synthetic Candidate (the same script as the matching live smoke flow). Live smoke flow 8 runs the same script. The Approval was given by the synthetic Operator through the web app.

Source: the demo stack `criba-demo` (http://localhost:8080, real Claude Haiku 5.5 turns through OpenRouter, Sonnet 5.5 summaries), read on 2026-10-12 through the Operator API. The transcript, the events and the JSON all come from this one Application.

## What it shows

- Qualified in Spain's Spanish ("barrio", "carnet de conducir"). After a person's Approval, the router sends the agent a `[system] approved` turn, which the Candidate never sees ([0012](../decisions/0012-agents-act-and-deliver.md), [0038](../decisions/0038-after-approval-a-system-turn-or-a-template.md)).
- The slot list comes from the backend, with "Otro horario" for other times; a tap books through `book_slot`, the one booking path ([0037](../decisions/0037-buttons-from-the-backend-pending-choice.md), [0040](../decisions/0040-single-booking-path.md), [0064](../decisions/0064-other-time-sends-the-booking-link-from-code.md)).
- The confirmation carries a calendar invite (.ics) for the Interview ([0030](../decisions/0030-approval-then-interview-booking-in-chat.md)).

**What to notice.** The `.ics` is attached after the tapped slot (`invite_after_message` on the `interview_booked` event), so it is not a message body in the transcript. It is shown below as the backend serves it.

| Fact | Value |
|---|---|
| Application | `app_01M4RM8GVCABKHDAZDRKYD44YA` |
| Candidate | Marta Ruiz Pérez, +34600000983 (synthetic) |
| Started (UTC) | 2026-10-12 17:58:39 |
| State when read | stage `interview_booked`, status `open`, outcome `null` |
| Language, country | `es-ES`, `ES` |

## Transcript

```text
Candidate: Buenas, me interesa el puesto de repartidor que anunciáis
Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestra política: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
Candidate: Marta Ruiz Pérez
Agent: Gracias, Marta. ¿En qué barrio y ciudad vives?
Candidate: Vivo en Madrid, en Retiro
Agent: Perfecto, Marta. ¿Tienes carnet de conducir vigente? [buttons: Sí, tengo carnet | No]
Candidate: Sí, tengo el carnet B desde hace años
Agent: Genial, Marta. ¿Qué disponibilidad tienes: jornada completa, parcial o fines de semana? [buttons: Jornada completa | Media jornada | Fines de semana]
Candidate: Puedo trabajar a jornada completa
Agent: ¡Perfecto! ¿Qué franja horaria prefieres: mañana, tarde, noche o flexible? [list: Mañana | Tarde | Noche | Flexible]
Candidate: Por las tardes me viene mejor
Agent: ¡Genial! ¿Cuántos años de experiencia tienes como repartidor?
Candidate: Llevo unos tres años
Agent: ¡Qué bien, Marta! ¿Con qué plataformas de reparto has trabajado: Glovo, Uber Eats, Just Eat, Rappi, DiDi Food u otra? [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Otra | Ninguna app]
Candidate: He trabajado con Glovo y Just Eat
Agent: ¡Gracias, Marta! ¿Desde qué día podrías empezar?
Candidate: Podría empezar el lunes que viene
Agent: Entendido. ¿Te refieres al lunes 19 u 26 de octubre? Elige una opción. [buttons: Lunes 19 oct | Lunes 26 oct]
Candidate (tapped): Lunes 19 oct
Agent: Marta, revisa tu solicitud:
    Nombre: Marta Ruiz Pérez
    Barrio y ciudad: Retiro, Madrid
    Carnet de conducir: sí
    Disponibilidad: jornada completa
    Franja preferida: tardes
    Experiencia: 3 años en Glovo y Just Eat
    Inicio: lunes 19 de octubre

    ¿Está todo correcto?
Candidate: Sí
Agent: ¡Listo, Marta! Un reclutador de Grupo Sazón revisará tu solicitud y te escribirá por aquí.
Agent: ¡Enhorabuena, Marta! Has pasado la preselección. Elige uno de estos huecos para tu entrevista. [list: mar 13 · 10:00 | mar 13 · 12:00 | mar 13 · 16:00 | Otro horario]
Candidate (tapped): mar 13 · 10:00
Agent: Tu entrevista está confirmada: martes 13 de octubre a las 10:00 en Sazón Madrid Centro, Calle Ejemplo 4, Centro, Madrid. Te he enviado una invitación en el mensaje de confirmación.
```

## The calendar invite

From `GET /api/applications/app_01M4RM8GVCABKHDAZDRKYD44YA/booking/invite.ics`, unchanged. The address is part of the synthetic seed.

```text
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Criba//Screening//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
BEGIN:VEVENT
UID:bk_01M4RMA6W0PBT4V3CFYHC876D6@criba.local
DTSTAMP:20261012T175935Z
DTSTART:20261013T080000Z
DTEND:20261013T083000Z
SUMMARY:Entrevista con Grupo Sazón · Sazón Madrid Centro
DESCRIPTION:Entrevista para repartidor en Sazón Madrid Centro.
LOCATION:(synthetic) Calle Ejemplo 4\, Centro\, Madrid
STATUS:CONFIRMED
SEQUENCE:0
END:VEVENT
END:VCALENDAR
```

## Events on the record

Ids, codes and canonical values only, never the Candidate's words ([0035](../decisions/0035-no-candidate-free-text-in-events.md)). Time is UTC.

```text
17:58:39  application_started  candidate                      {"channel": "webchat"}
17:58:47  answer_recorded      agent                          {"field": "full_name", "message_id": 51}
17:58:47  stage_changed        agent                          {"from": "greeting", "to": "zone"}
17:58:53  answer_recorded      agent                          {"field": "zone", "value": "zone_es_madrid_retiro", "message_id": 53, "locations": ["loc_es_madrid_centro", "loc_es_madrid_retiro", "loc_es_madrid_salamanca"]}
17:58:53  stage_changed        agent                          {"from": "zone", "to": "driver_license"}
17:58:59  answer_recorded      agent                          {"field": "driver_license", "value": true, "message_id": 55}
17:58:59  stage_changed        agent                          {"from": "driver_license", "to": "availability"}
17:59:04  answer_recorded      agent                          {"field": "availability", "value": ["full_time"], "message_id": 57}
17:59:04  stage_changed        agent                          {"from": "availability", "to": "preferred_schedule"}
17:59:08  answer_recorded      agent                          {"field": "preferred_schedule", "value": ["afternoon"], "message_id": 59}
17:59:08  stage_changed        agent                          {"from": "preferred_schedule", "to": "experience"}
17:59:11  answer_recorded      agent                          {"field": "experience_years", "value": 3.0, "message_id": 61}
17:59:16  answer_recorded      agent                          {"field": "experience_platforms", "value": ["glovo", "just_eat"], "message_id": 63}
17:59:16  stage_changed        agent                          {"from": "experience", "to": "start_date"}
17:59:22  answer_ambiguous     agent                          {"field": "start_date", "options": [{"id": "2026-10-19", "label": "Lunes 19 oct"}, {"id": "2026-10-26", "label": "Lunes 26 oct"}], "message_id": 65}
17:59:24  answer_recorded      agent                          {"field": "start_date", "value": "2026-10-19", "message_id": 67}
17:59:24  stage_changed        agent                          {"from": "start_date", "to": "confirm"}
17:59:29  stage_changed        agent                          {"from": "confirm", "to": "qualified"}
17:59:29  screening_finished   agent                          {"result": "qualified"}
17:59:29  approval_requested   agent                          {"queue_item_id": "qi_01M4RMA179J2D28NA9G2X4KEWR", "reason": null}
17:59:30  approved             user (Operator (synthetic))
17:59:30  system_turn          system                         {"turn": "[system] approved"}
17:59:29  summary_written      system
17:59:33  slots_offered        agent                          {"slot_refs": ["so_q5EE4Ag3nFma", "so_iZlc2XoCWbzX", "so_GcS7mOFQyWzf"], "slot_ids": ["slot_es_madrid_centro_20261013_1000", "slot_es_madrid_centro_20261013_1200", "slot_es_madrid_centro_20261013_1600"], "reschedule": false, "expires_at": "2026-10-14T17:59:33Z"}
17:59:35  stage_changed        agent                          {"from": "approved", "to": "interview_booked"}
17:59:35  interview_booked     agent                          {"booking_id": "bk_01M4RMA6W0PBT4V3CFYHC876D6", "slot_id": "slot_es_madrid_centro_20261013_1000", "location_id": "loc_es_madrid_centro", "starts_at": "2026-10-13T08:00:00Z", "via": "chat", "ics_uid": "bk_01M4RMA6W0PBT4V3CFYHC876D6@criba.local", "invite_after_message": 72}
```

## Application JSON

From `GET /api/applications/app_01M4RM8GVCABKHDAZDRKYD44YA/export`, unchanged.

```json
{
  "application_id": "app_01M4RM8GVCABKHDAZDRKYD44YA",
  "candidate_id": "cand_01M4RM8GV68N4E4XY3Y820AAPX",
  "channel": "webchat",
  "country": "ES",
  "language": "es-ES",
  "status": "open",
  "stage": "interview_booked",
  "outcome": null,
  "knockout_criterion": null,
  "rejection_reason": null,
  "answers": {
    "full_name": "Marta Ruiz Pérez",
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
      "afternoon"
    ],
    "experience_years": 3.0,
    "experience_platforms": [
      "glovo",
      "just_eat"
    ],
    "start_date": "2026-10-19"
  },
  "start_date_far": false,
  "locations": [
    "loc_es_madrid_centro",
    "loc_es_madrid_retiro",
    "loc_es_madrid_salamanca"
  ],
  "created_at": "2026-10-12T17:58:39Z",
  "updated_at": "2026-10-12T17:59:37Z",
  "qualified_at": "2026-10-12T17:59:29Z",
  "approved_at": "2026-10-12T17:59:30Z",
  "closed_at": null,
  "interview": {
    "starts_at": "2026-10-13T08:00:00Z",
    "ends_at": "2026-10-13T08:30:00Z",
    "local_label": "mar 13 · 10:00",
    "location_name": "Sazón Madrid Centro",
    "address": "(synthetic) Calle Ejemplo 4, Centro, Madrid"
  },
  "summary": {
    "structured": {
      "answers": {
        "full_name": "Marta Ruiz Pérez",
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
          "afternoon"
        ],
        "experience_years": 3.0,
        "experience_platforms": [
          "glovo",
          "just_eat"
        ],
        "start_date": "2026-10-19"
      },
      "stage": "qualified",
      "outcome": null,
      "language": "es-ES",
      "durations_seconds": {
        "total": 50,
        "by_stage": {
          "greeting": 8,
          "zone": 6,
          "driver_license": 6,
          "availability": 5,
          "preferred_schedule": 4,
          "experience": 8,
          "start_date": 8,
          "confirm": 5,
          "qualified": 0
        }
      },
      "nudges": 0,
      "escalations": 0,
      "flags": [],
      "knockout_criterion": null,
      "reopened_count": 0
    },
    "prose": "Marta Ruiz Pérez ha completado el cribado y su solicitud está calificada, pendiente de que apruebes. La conversación fue fluida y breve, sin contradicciones, avisos ni escalados. Dijo que podría empezar \"el lunes que viene\" y, tras aclararlo, confirmó el lunes 19 de octubre. Indicó unos tres años de experiencia en Glovo y Just Eat, y dice tener el carnet B desde hace años, algo que no se ha comprobado con documentación. Queda abierto verificar el carnet y la experiencia declarada.",
    "written_at": "2026-10-12T17:59:29Z"
  }
}
```
