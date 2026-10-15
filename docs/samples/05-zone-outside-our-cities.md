# Texas: the cities list, then a Zone Knockout

The Candidate is a scripted, synthetic Candidate (the same script as the matching live smoke flow). Live smoke flow 5 runs the same script.

Source: the demo stack `criba-demo` (http://localhost:8080, real Claude Haiku 5.5 turns through OpenRouter, Sonnet 5.5 summaries), read on 2026-10-12 through the Operator API. The transcript, the events and the JSON all come from this one Application.

## What it shows

- "I live in Texas!" matches no Zone, so code asks once, with a fixed question, which of our cities the Candidate can work in ([0068](../decisions/0068-a-place-with-no-zone-is-asked-once-as-a-list-of-our-cities.md)).
- "None of these" is a Zone Knockout with reason `outside_service_areas`, sent as a fixed template with the requirements link and a way back ([0016](../decisions/0016-knockouts-explained-with-a-way-back.md), [0068](../decisions/0068-a-place-with-no-zone-is-asked-once-as-a-list-of-our-cities.md)).
- The Candidate gave his name in the opener, so the agent went straight to the Zone question.

**What to notice.** No Summary is written for this Knockout (`summary` is null): nothing past the name was recorded.

| Fact | Value |
|---|---|
| Application | `app_01M4RM83K8RATSRJ75TDPQHHAY` |
| Candidate | Cody Walker, +525500000984 (synthetic) |
| Started (UTC) | 2026-10-12 17:58:26 |
| State when read | stage `zone`, status `closed`, outcome `"knockout"` |
| Language, country | `en`, `MX` |

## Transcript

```text
Candidate: Hi, I'm Cody Walker, I saw your delivery job ad
Agent: Hi Cody! I'm Grupo Sazón's virtual (AI) assistant 🛵 Glad you saw the ad. Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad

    Which colonia and city do you live in?
Candidate: I live in Texas!
Agent: We hire delivery drivers in these cities: Ciudad de México, Guadalajara, Monterrey, Puebla. Can you work in one of them? [list: Ciudad de México | Guadalajara | Monterrey | Puebla | None of these]
Candidate (tapped): None of these
Agent: Thank you, Cody. Right now none of our locations deliver in your area, and drivers need to work inside a location's delivery area. Here are the requirements: https://empleo.gruposazon.example/requisitos. If you move or we open nearby, write to us and we'll pick up where we left off. If you think this is a mistake, reply "review" and a person will look at it.
```

## Events on the record

Ids, codes and canonical values only, never the Candidate's words ([0035](../decisions/0035-no-candidate-free-text-in-events.md)). Time is UTC.

```text
17:58:26  application_started  candidate                      {"channel": "webchat"}
17:58:30  answer_recorded      agent                          {"field": "full_name", "message_id": 43}
17:58:30  stage_changed        agent                          {"from": "greeting", "to": "zone"}
17:58:35  answer_ambiguous     agent                          {"field": "zone", "options": [{"id": "city_mx_ciudad_de_mexico", "label": "Ciudad de México"}, {"id": "city_mx_guadalajara", "label": "Guadalajara"}, {"id": "city_mx_monterrey", "label": "Monterrey"}, {"id": "city_mx_puebla", "label": "Puebla"}, {"id": "city_none", "label": "None of these"}], "funnel": true, "message_id": 45}
17:58:37  knockout             agent                          {"reason": "outside_service_areas", "template": "knockout_zone.en", "criterion": "zone", "stage": "zone", "message_id": 48}
17:58:37  screening_finished   agent                          {"result": "knockout"}
```

## Application JSON

From `GET /api/applications/app_01M4RM83K8RATSRJ75TDPQHHAY/export`, unchanged.

```json
{
  "application_id": "app_01M4RM83K8RATSRJ75TDPQHHAY",
  "candidate_id": "cand_01M4RM83K2N528TR8XCJ814X6C",
  "channel": "webchat",
  "country": "MX",
  "language": "en",
  "status": "closed",
  "stage": "zone",
  "outcome": "knockout",
  "knockout_criterion": "zone",
  "rejection_reason": null,
  "answers": {
    "full_name": "Cody Walker",
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
  "created_at": "2026-10-12T17:58:26Z",
  "updated_at": "2026-10-12T17:58:39Z",
  "qualified_at": null,
  "approved_at": null,
  "closed_at": "2026-10-12T17:58:37Z",
  "interview": null,
  "summary": null
}
```
