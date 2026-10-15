# Knockout on the licence, then a Review request

The Candidate is a scripted, synthetic Candidate (the same script as the matching live smoke flow). Live smoke flow 2 runs the same script.

Source: the demo stack `criba-demo` (http://localhost:8080, real Claude Haiku 5.5 turns through OpenRouter, Sonnet 5.5 summaries), read on 2026-10-12 through the Operator API. The transcript, the events and the JSON all come from this one Application.

## What it shows

- A licence "no" is a Knockout decided in code, not by the model. The message is a fixed template (`knockout_driver_license.es-MX`) with the reason, the requirements link and a way back ([0016](../decisions/0016-knockouts-explained-with-a-way-back.md), [0026](../decisions/0026-answers-recorded-through-python-tools.md)).
- "Revisión" opens a Review for a person. The agent decides nothing; a Recruiter does ([0012](../decisions/0012-agents-act-and-deliver.md)).
- Mexican Spanish throughout: "colonia", "licencia de manejo" ([0057](../decisions/0057-the-applications-country-follows-the-recorded-zone.md)).

**What to notice.** The Review item's note is the agent's own short line for the Recruiter. It lives on the queue item, not in the events, which hold codes only ([0035](../decisions/0035-no-candidate-free-text-in-events.md)).

| Fact | Value |
|---|---|
| Application | `app_01M4RM6YHJ4K8R46PRB1DQ5CQJ` |
| Candidate | Luis Hernández, +525500000981 (synthetic) |
| Started (UTC) | 2026-10-12 17:57:48 |
| State when read | stage `driver_license`, status `closed`, outcome `"knockout"` |
| Language, country | `es-MX`, `MX` |

## Transcript

```text
Candidate: Hola, vi el anuncio para repartidores y quiero trabajar con ustedes
Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
Candidate: Me llamo Luis Hernández
Agent: Gracias, Luis. ¿En qué colonia y ciudad vives?
Candidate: Vivo en Puebla, por el Centro
Agent: Perfecto, Luis. ¿Tienes licencia de manejo vigente? [buttons: Sí, tengo licencia | No]
Candidate: No, todavía no tengo licencia de manejo
Agent: Gracias, Luis. Por ahora necesitamos licencia de manejo vigente: es un requisito legal y del seguro para repartir. Aquí están los requisitos: https://empleo.gruposazon.example/requisitos. Cuando la tengas, escríbenos y seguimos donde lo dejamos. Si crees que hay un error, responde "revisión" y una persona lo verá.
Candidate: Creo que hay un error, quisiera pedir una revisión
Agent: Listo, Luis. Pedí la revisión y una persona del equipo de Grupo Sazón la revisará y te escribirá por aquí.
```

## Also on the record

From the Application detail API (`GET /api/applications/app_01M4RM6YHJ4K8R46PRB1DQ5CQJ`), the fields this sample is about. The communication notes are written by code from the Jev readings and live only on this API, not in the Summary ([0056](../decisions/0056-the-summary-is-prose-in-plain-words-and-the-notes-stay-on-the-api.md)).

```json
{
  "communication_notes": [
    {
      "kind": "writes_in",
      "text": "Escribe en español de México: funcionan las preguntas cortas y directas.",
      "stage": null,
      "at": "2026-10-12T17:57:48Z",
      "message_id": 25
    }
  ],
  "open_queue_items": [
    {
      "id": "qi_01M4RM7GN02MBCN1G3TCM5PAYT",
      "kind": "review",
      "status": "open",
      "reason": null,
      "note": "Candidate says there is an error regarding the driver licence answer; he does not currently hold a driver's licence.",
      "created_at": "2026-10-12T17:58:06Z"
    }
  ]
}
```

## Events on the record

Ids, codes and canonical values only, never the Candidate's words ([0035](../decisions/0035-no-candidate-free-text-in-events.md)). Time is UTC.

```text
17:57:48  application_started  candidate                      {"channel": "webchat"}
17:57:55  answer_recorded      agent                          {"field": "full_name", "message_id": 27}
17:57:55  stage_changed        agent                          {"from": "greeting", "to": "zone"}
17:57:59  answer_recorded      agent                          {"field": "zone", "value": "zone_mx_puebla_centro", "message_id": 29, "locations": ["loc_mx_puebla_angelopolis", "loc_mx_puebla_centro", "loc_mx_puebla_la_paz"]}
17:57:59  stage_changed        agent                          {"from": "zone", "to": "driver_license"}
17:58:02  answer_recorded      agent                          {"field": "driver_license", "value": false, "message_id": 31}
17:58:02  knockout             agent                          {"reason": "no", "template": "knockout_driver_license.es-MX", "criterion": "driver_license", "stage": "driver_license", "message_id": 32}
17:58:02  screening_finished   agent                          {"result": "knockout"}
17:58:02  summary_written      system
17:58:06  review_requested     candidate                      {"queue_item_id": "qi_01M4RM7GN02MBCN1G3TCM5PAYT", "reason": null}
```

## Application JSON

From `GET /api/applications/app_01M4RM6YHJ4K8R46PRB1DQ5CQJ/export`, unchanged.

```json
{
  "application_id": "app_01M4RM6YHJ4K8R46PRB1DQ5CQJ",
  "candidate_id": "cand_01M4RM6YHCRT869SHJPEYNCV37",
  "channel": "webchat",
  "country": "MX",
  "language": "es-MX",
  "status": "closed",
  "stage": "driver_license",
  "outcome": "knockout",
  "knockout_criterion": "driver_license",
  "rejection_reason": null,
  "answers": {
    "full_name": "Luis Hernández",
    "zone": {
      "id": "zone_mx_puebla_centro",
      "name": "Centro",
      "city": "Puebla",
      "country": "MX"
    },
    "driver_license": false,
    "availability": null,
    "preferred_schedule": null,
    "experience_years": null,
    "experience_platforms": null,
    "start_date": null
  },
  "start_date_far": false,
  "locations": [
    "loc_mx_puebla_angelopolis",
    "loc_mx_puebla_centro",
    "loc_mx_puebla_la_paz"
  ],
  "created_at": "2026-10-12T17:57:48Z",
  "updated_at": "2026-10-12T17:58:08Z",
  "qualified_at": null,
  "approved_at": null,
  "closed_at": "2026-10-12T17:58:02Z",
  "interview": null,
  "summary": {
    "structured": {
      "answers": {
        "full_name": "Luis Hernández",
        "zone": {
          "id": "zone_mx_puebla_centro",
          "name": "Centro",
          "city": "Puebla",
          "country": "MX"
        },
        "driver_license": false,
        "availability": null,
        "preferred_schedule": null,
        "experience_years": null,
        "experience_platforms": null,
        "start_date": null
      },
      "stage": "driver_license",
      "outcome": "knockout",
      "language": "es-MX",
      "durations_seconds": {
        "total": 14,
        "by_stage": {
          "greeting": 7,
          "zone": 4,
          "driver_license": 3
        }
      },
      "nudges": 0,
      "escalations": 0,
      "flags": [],
      "knockout_criterion": "driver_license",
      "reopened_count": 0
    },
    "prose": "La solicitud de Luis Hernández terminó de forma anticipada porque no cuenta con licencia de manejo vigente, requisito eliminatorio. Él mismo dijo que \"todavía no\" la tiene, así que podría volver a postularse si la obtiene. No respondió disponibilidad, horario, experiencia ni fecha de inicio, pues la conversación duró solo 14 segundos. Queda por confirmar si la zona Centro se refiere al centro de Puebla, como él mencionó. La decisión final queda en sus manos.",
    "written_at": "2026-10-12T17:58:02Z"
  }
}
```
