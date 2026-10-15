# A place that is not a Zone gets the city's Zones

The Candidate is a scripted, synthetic Candidate (the same script as the matching live smoke flow). Live smoke flow 3 runs the same script.

Source: the demo stack `criba-demo` (http://localhost:8080, real Claude Haiku 5.5 turns through OpenRouter, Sonnet 5.5 summaries), read on 2026-10-12 through the Operator API. The transcript, the events and the JSON all come from this one Application.

## What it shows

- "Cuajimalpa" is in Ciudad de México but is not one of our Zones. The read-only `search_zones` lookup matches the city, and code offers the city's Zones as a list ([0042](../decisions/0042-agent-interprets-code-verifies.md), [0062](../decisions/0062-a-city-only-zone-search-offers-the-citys-zones.md)).
- The tapped Zone is recorded with the Locations that cover it; the agent never invents a Zone id ([0037](../decisions/0037-buttons-from-the-backend-pending-choice.md), [0026](../decisions/0026-answers-recorded-through-python-tools.md)).

**What to notice.** The script stops at the licence question, so the Application is still open at `driver_license`: a drop-off. The scheduler's nudges and Stalled rules take it from here (`src/backend/criba/scheduler/`).

| Fact | Value |
|---|---|
| Application | `app_01M4RM7J262RACWCJKQJ79NYAT` |
| Candidate | Valeria Núñez, +525500000983 (synthetic) |
| Started (UTC) | 2026-10-12 17:58:08 |
| State when read | stage `driver_license`, status `open`, outcome `null` |
| Language, country | `es-MX`, `MX` |

## Transcript

```text
Candidate: Hola, me interesa el trabajo de repartidor
Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cuál es tu nombre y apellido?
Candidate: Valeria Núñez
Agent: Gracias, Valeria. ¿En qué colonia y ciudad vives?
Candidate: Ciudad de México, Cuajimalpa
Agent: Gracias. Tu colonia no aparece en la lista, así que elige la zona más cercana de las opciones que te aparecen abajo. [list: Centro | Condesa | Coyoacán | Del Valle | Iztapalapa | Milpa Alta | Narvarte | Polanco | Roma]
Candidate (tapped): Polanco
Agent: Gracias, Valeria. ¿Tienes licencia de manejo vigente? [buttons: Sí, tengo licencia | No]
```

## Events on the record

Ids, codes and canonical values only, never the Candidate's words ([0035](../decisions/0035-no-candidate-free-text-in-events.md)). Time is UTC.

```text
17:58:08  application_started  candidate                      {"channel": "webchat"}
17:58:15  answer_recorded      agent                          {"field": "full_name", "message_id": 37}
17:58:15  stage_changed        agent                          {"from": "greeting", "to": "zone"}
17:58:20  answer_ambiguous     agent                          {"field": "zone", "options": [{"id": "zone_mx_cdmx_centro", "label": "Centro"}, {"id": "zone_mx_cdmx_condesa", "label": "Condesa"}, {"id": "zone_mx_cdmx_coyoacan", "label": "Coyoacán"}, {"id": "zone_mx_cdmx_del_valle", "label": "Del Valle"}, {"id": "zone_mx_cdmx_iztapalapa", "label": "Iztapalapa"}, {"id": "zone_mx_cdmx_milpa_alta", "label": "Milpa Alta"}, {"id": "zone_mx_cdmx_narvarte", "label": "Narvarte"}, {"id": "zone_mx_cdmx_polanco", "label": "Polanco"}, {"id": "zone_mx_cdmx_roma", "label": "Roma"}], "message_id": 39}
17:58:23  answer_recorded      agent                          {"field": "zone", "value": "zone_mx_cdmx_polanco", "message_id": 41, "locations": ["loc_mx_cdmx_condesa", "loc_mx_cdmx_polanco"]}
17:58:23  stage_changed        agent                          {"from": "zone", "to": "driver_license"}
```

## Application JSON

From `GET /api/applications/app_01M4RM7J262RACWCJKQJ79NYAT/export`, unchanged.

```json
{
  "application_id": "app_01M4RM7J262RACWCJKQJ79NYAT",
  "candidate_id": "cand_01M4RM7J20FBH2150C5QZJY9S6",
  "channel": "webchat",
  "country": "MX",
  "language": "es-MX",
  "status": "open",
  "stage": "driver_license",
  "outcome": null,
  "knockout_criterion": null,
  "rejection_reason": null,
  "answers": {
    "full_name": "Valeria Núñez",
    "zone": {
      "id": "zone_mx_cdmx_polanco",
      "name": "Polanco",
      "city": "Ciudad de México",
      "country": "MX"
    },
    "driver_license": null,
    "availability": null,
    "preferred_schedule": null,
    "experience_years": null,
    "experience_platforms": null,
    "start_date": null
  },
  "start_date_far": false,
  "locations": [
    "loc_mx_cdmx_condesa",
    "loc_mx_cdmx_polanco"
  ],
  "created_at": "2026-10-12T17:58:08Z",
  "updated_at": "2026-10-12T17:58:25Z",
  "qualified_at": null,
  "approved_at": null,
  "closed_at": null,
  "interview": null,
  "summary": null
}
```
