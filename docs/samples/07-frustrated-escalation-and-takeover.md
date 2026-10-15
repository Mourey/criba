# A frustrated Candidate asks for a person: an Escalation, then a Takeover

The Candidate is a scripted, synthetic Candidate (the same script as the matching live smoke flow). Live smoke flow 12 runs the same script. The Takeover was done by the synthetic Operator in the web app, about 20 minutes later.

Source: the demo stack `criba-demo` (http://localhost:8080, real Claude Haiku 5.5 turns through OpenRouter, Sonnet 5.5 summaries), read on 2026-10-12 through the Operator API. The transcript, the events and the JSON all come from this one Application.

## What it shows

- Jev reads the message before the agent runs: frustrated, wants a person. Code stores a tone hint as codes and opens a `wants_human` Escalation; the reply apologises and keeps going ([0051](../decisions/0051-jev-router-decides-language-and-reads-tone.md)).
- The communication notes tell the Recruiter how to talk to this Candidate; nothing about the Candidate is decided ([0056](../decisions/0056-the-summary-is-prose-in-plain-words-and-the-notes-stay-on-the-api.md), [0012](../decisions/0012-agents-act-and-deliver.md)).
- An Operator takes over from the web app; from then on the agent does not answer ([0020](../decisions/0020-human-takeover-from-web-panel.md)).

**What to notice.** The Escalation is no longer an open queue item: taking over the chat answered it (`takeover_started` carries its `queue_item_id`).

| Fact | Value |
|---|---|
| Application | `app_01M4RMA9N2AJDZWPTGNAYJ35NP` |
| Candidate | (no name recorded), +525500000985 (synthetic) |
| Started (UTC) | 2026-10-12 17:59:37 |
| State when read | stage `greeting`, status `open`, outcome `null` |
| Language, country | `es-MX`, `MX` |

## Transcript

```text
Candidate: Hola, busco chamba de repartidor
Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
Candidate: ya te dije que cualquier dia!! quiero hablar con una persona
Agent: Perdón por la espera, ya avisé al equipo de Grupo Sazón para que te contacte por aquí. Mientras tanto, ¿me dices tu nombre y apellido para seguir con la solicitud?
System: Alguien del equipo de Grupo Sazón se unió a la conversación.
```

## Also on the record

From the Application detail API (`GET /api/applications/app_01M4RMA9N2AJDZWPTGNAYJ35NP`), the fields this sample is about. The communication notes are written by code from the Jev readings and live only on this API, not in the Summary ([0056](../decisions/0056-the-summary-is-prose-in-plain-words-and-the-notes-stay-on-the-api.md)).

```json
{
  "communication_notes": [
    {
      "kind": "writes_in",
      "text": "Escribe en español de México: funcionan las preguntas cortas y directas.",
      "stage": null,
      "at": "2026-10-12T17:59:37Z",
      "message_id": 74
    },
    {
      "kind": "asked_for_person",
      "text": "Pidió hablar con una persona al inicio del chat (11:59).",
      "stage": "greeting",
      "at": "2026-10-12T17:59:41Z",
      "message_id": 76
    },
    {
      "kind": "impatient",
      "text": "Mostró impaciencia al inicio del chat (11:59): funcionan las respuestas cortas y directas.",
      "stage": "greeting",
      "at": "2026-10-12T17:59:41Z",
      "message_id": 76
    }
  ],
  "message_signals": [
    {
      "message_id": 76,
      "stage": "greeting",
      "at": "2026-10-12T17:59:41Z",
      "fired": [
        "wants_human",
        "frustrated"
      ],
      "language_changed": false,
      "escalated": true
    }
  ]
}
```

## Events on the record

Ids, codes and canonical values only, never the Candidate's words ([0035](../decisions/0035-no-candidate-free-text-in-events.md)). Time is UTC.

```text
17:59:37  application_started  candidate                      {"channel": "webchat"}
17:59:41  tone_hint            system                         {"code": "escalated", "signals": ["wants_human", "frustrated"], "message_id": 76}
17:59:41  escalation_raised    agent                          {"queue_item_id": "qi_01M4RMADN84PFXTEY0DZNSG2N0", "reason": "wants_human"}
18:19:19  takeover_started     user (Operator (synthetic))    {"queue_item_id": "qi_01M4RMADN84PFXTEY0DZNSG2N0"}
```

## Application JSON

From `GET /api/applications/app_01M4RMA9N2AJDZWPTGNAYJ35NP/export`, unchanged.

```json
{
  "application_id": "app_01M4RMA9N2AJDZWPTGNAYJ35NP",
  "candidate_id": "cand_01M4RMA9MVQWQDEN11WKE0376D",
  "channel": "webchat",
  "country": "MX",
  "language": "es-MX",
  "status": "open",
  "stage": "greeting",
  "outcome": null,
  "knockout_criterion": null,
  "rejection_reason": null,
  "answers": {
    "full_name": null,
    "zone": null,
    "driver_license": null,
    "availability": null,
    "preferred_schedule": null,
    "experience_years": null,
    "experience_platforms": null,
    "start_date": null
  },
  "start_date_far": false,
  "locations": [],
  "created_at": "2026-10-12T17:59:37Z",
  "updated_at": "2026-10-12T18:19:19Z",
  "qualified_at": null,
  "approved_at": null,
  "closed_at": null,
  "interview": null,
  "summary": null
}
```
