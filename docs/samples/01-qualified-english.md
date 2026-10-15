# Qualified, in English, in Madrid

The Candidate is a scripted, synthetic Candidate (the same script as the matching live smoke flow). Live smoke flow 1 runs the same script.

Source: the demo stack `criba-demo` (http://localhost:8080, real Claude Haiku 5.5 turns through OpenRouter, Sonnet 5.5 summaries), read on 2026-10-12 through the Operator API. The transcript, the events and the JSON all come from this one Application.

## What it shows

- The AI disclosure and the privacy notice link are in the first message, guaranteed by code ([0044](../decisions/0044-code-guarantees-the-ai-disclosure.md), [0068](../decisions/0068-a-place-with-no-zone-is-asked-once-as-a-list-of-our-cities.md)).
- The agent turns free text ("About two and a half years", "Mainly Glovo, and some Uber Eats") into values; code checks and records each one, and the buttons come from the backend ([0042](../decisions/0042-agent-interprets-code-verifies.md), [0026](../decisions/0026-answers-recorded-through-python-tools.md), [0037](../decisions/0037-buttons-from-the-backend-pending-choice.md)).
- "Next Monday" fits two dates, so code asks with two date buttons; then the read-back, the Qualified close, an Approval request and a Summary written by Sonnet 5.5 in plain words ([0054](../decisions/0054-a-start-date-that-fits-two-days-is-asked-with-buttons.md), [0058](../decisions/0058-weekday-dates-come-from-the-backend.md), [0025](../decisions/0025-model-choice-haiku-turns-sonnet-summary.md), [0056](../decisions/0056-the-summary-is-prose-in-plain-words-and-the-notes-stay-on-the-api.md)).

**What to notice.** The script sent the Zone answer twice. The agent kept the recorded Zone and asked the licence question again. The Summary tells the Recruiter about it, and that the Candidate never said his licence is valid: the kind of thing a Recruiter should check before the Approval.

| Fact | Value |
|---|---|
| Application | `app_01M4RM53GRSV81EYF55W43CAGQ` |
| Candidate | Daniel Foster, +34600000982 (synthetic) |
| Started (UTC) | 2026-10-12 17:56:47 |
| State when read | stage `qualified`, status `open`, outcome `null` |
| Language, country | `en`, `ES` |

## Transcript

```text
Candidate: Hi, I saw your ad for delivery riders in Madrid. I'd like to apply.
Agent: Hi! I'm Grupo Sazón's virtual (AI) assistant 🛵 I'll help you apply as a delivery rider in just a few minutes. Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad. What's your full name?
Candidate: My name is Daniel Foster
Agent: Thanks, Daniel! Which neighbourhood and city do you live in?
Candidate: I live in Madrid, near Chamberí
Agent: Got it, you're in Chamberí, Madrid. Do you have a driving licence (carnet de conducir)? If so, is it valid, expired, suspended or still pending? [buttons: Yes, I have one | No]
Candidate: I live in Madrid, near Chamberí
Agent: Thanks, Daniel, your Chamberí zone is saved. Do you have a driving licence (carnet de conducir)? Let me know if it's valid, expired, suspended or still pending. [buttons: Yes, I have one | No]
Candidate: Yes, I've had my driving licence for about six years
Agent: Thanks, Daniel, that's great. Which days can you work: full time, part time, or weekends only? [buttons: Full-time | Part-time | Weekends]
Candidate: I'm free full time, Monday to Saturday
Agent: Got it, Daniel: full time, Monday to Saturday. What time of day do you prefer to work: morning, afternoon, evening, or flexible? [list: Morning | Afternoon | Evening | Flexible]
Candidate: Mostly afternoons, evenings are fine too
Agent: Thanks, Daniel: afternoons and evenings. How many years of delivery experience do you have? If none, just say 0.
Candidate: About two and a half years
Agent: Thanks, Daniel: two and a half years is solid. Which delivery platforms have you worked for? For example Glovo, Uber Eats, Just Eat, Rappi, DiDi Food, another app, or none on your own. [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Other | No app]
Candidate: Mainly Glovo, and some Uber Eats
Agent: Thanks, Daniel: Glovo and Uber Eats noted. When could you start?
Candidate: I could start next Monday
Agent: Thanks, Daniel. Which Monday works better for you: 19 October or 26 October? [buttons: Monday 19 Oct | Monday 26 Oct]
Candidate (tapped): Monday 19 Oct
Agent: Thanks, Daniel. Here's your summary, please check it:
    Name: Daniel Foster
    Zone: Chamberí, Madrid
    Driving licence: yes, about six years
    Availability: full time, Monday to Saturday
    Preferred schedule: afternoons and evenings
    Experience: 2.5 years, on Glovo and Uber Eats
    Start date: Monday 19 October

    Is everything correct?
Candidate: Yes
Agent: All set, Daniel! A Grupo Sazón recruiter will review your application and write to you here.
```

## Also on the record

From the Application detail API (`GET /api/applications/app_01M4RM53GRSV81EYF55W43CAGQ`), the fields this sample is about. The communication notes are written by code from the Jev readings and live only on this API, not in the Summary ([0056](../decisions/0056-the-summary-is-prose-in-plain-words-and-the-notes-stay-on-the-api.md)).

```json
{
  "communication_notes": [
    {
      "kind": "writes_in",
      "text": "Writes in English: short, direct questions work.",
      "stage": null,
      "at": "2026-10-12T17:56:47Z",
      "message_id": 1
    }
  ],
  "open_queue_items": [
    {
      "id": "qi_01M4RM6W6K734G5JHRYB5QJM4D",
      "kind": "approval",
      "status": "open",
      "reason": null,
      "note": null,
      "created_at": "2026-10-12T17:57:45Z"
    }
  ]
}
```

## Events on the record

Ids, codes and canonical values only, never the Candidate's words ([0035](../decisions/0035-no-candidate-free-text-in-events.md)). Time is UTC.

```text
17:56:47  application_started  candidate                      {"channel": "webchat"}
17:56:57  answer_recorded      agent                          {"field": "full_name", "message_id": 3}
17:56:57  stage_changed        agent                          {"from": "greeting", "to": "zone"}
17:57:03  answer_recorded      agent                          {"field": "zone", "value": "zone_es_madrid_chamberi", "message_id": 5, "locations": ["loc_es_madrid_chamberi", "loc_es_madrid_salamanca"]}
17:57:03  stage_changed        agent                          {"from": "zone", "to": "driver_license"}
17:57:12  answer_recorded      agent                          {"field": "driver_license", "value": true, "message_id": 9}
17:57:12  stage_changed        agent                          {"from": "driver_license", "to": "availability"}
17:57:17  answer_recorded      agent                          {"field": "availability", "value": ["full_time"], "message_id": 11}
17:57:17  stage_changed        agent                          {"from": "availability", "to": "preferred_schedule"}
17:57:22  answer_recorded      agent                          {"field": "preferred_schedule", "value": ["afternoon", "evening"], "message_id": 13}
17:57:22  stage_changed        agent                          {"from": "preferred_schedule", "to": "experience"}
17:57:26  answer_recorded      agent                          {"field": "experience_years", "value": 2.5, "message_id": 15}
17:57:31  answer_recorded      agent                          {"field": "experience_platforms", "value": ["glovo", "uber_eats"], "message_id": 17}
17:57:31  stage_changed        agent                          {"from": "experience", "to": "start_date"}
17:57:38  answer_ambiguous     agent                          {"field": "start_date", "options": [{"id": "2026-10-19", "label": "Monday 19 Oct"}, {"id": "2026-10-26", "label": "Monday 26 Oct"}], "message_id": 19}
17:57:39  answer_recorded      agent                          {"field": "start_date", "value": "2026-10-19", "message_id": 21}
17:57:39  stage_changed        agent                          {"from": "start_date", "to": "confirm"}
17:57:45  stage_changed        agent                          {"from": "confirm", "to": "qualified"}
17:57:45  screening_finished   agent                          {"result": "qualified"}
17:57:45  approval_requested   agent                          {"queue_item_id": "qi_01M4RM6W6K734G5JHRYB5QJM4D", "reason": null}
17:57:45  summary_written      system
```

## Application JSON

From `GET /api/applications/app_01M4RM53GRSV81EYF55W43CAGQ/export`, unchanged.

```json
{
  "application_id": "app_01M4RM53GRSV81EYF55W43CAGQ",
  "candidate_id": "cand_01M4RM53GJ8RHHC6T8S6MYD7SN",
  "channel": "webchat",
  "country": "ES",
  "language": "en",
  "status": "open",
  "stage": "qualified",
  "outcome": null,
  "knockout_criterion": null,
  "rejection_reason": null,
  "answers": {
    "full_name": "Daniel Foster",
    "zone": {
      "id": "zone_es_madrid_chamberi",
      "name": "Chamberí",
      "city": "Madrid",
      "country": "ES"
    },
    "driver_license": true,
    "availability": [
      "full_time"
    ],
    "preferred_schedule": [
      "afternoon",
      "evening"
    ],
    "experience_years": 2.5,
    "experience_platforms": [
      "glovo",
      "uber_eats"
    ],
    "start_date": "2026-10-19"
  },
  "start_date_far": false,
  "locations": [
    "loc_es_madrid_chamberi",
    "loc_es_madrid_salamanca"
  ],
  "created_at": "2026-10-12T17:56:47Z",
  "updated_at": "2026-10-12T17:57:45Z",
  "qualified_at": "2026-10-12T17:57:45Z",
  "approved_at": null,
  "closed_at": null,
  "interview": null,
  "summary": {
    "structured": {
      "answers": {
        "full_name": "Daniel Foster",
        "zone": {
          "id": "zone_es_madrid_chamberi",
          "name": "Chamberí",
          "city": "Madrid",
          "country": "ES"
        },
        "driver_license": true,
        "availability": [
          "full_time"
        ],
        "preferred_schedule": [
          "afternoon",
          "evening"
        ],
        "experience_years": 2.5,
        "experience_platforms": [
          "glovo",
          "uber_eats"
        ],
        "start_date": "2026-10-19"
      },
      "stage": "qualified",
      "outcome": null,
      "language": "en",
      "durations_seconds": {
        "total": 58,
        "by_stage": {
          "greeting": 10,
          "zone": 6,
          "driver_license": 9,
          "availability": 5,
          "preferred_schedule": 5,
          "experience": 9,
          "start_date": 8,
          "confirm": 6,
          "qualified": 0
        }
      },
      "nudges": 0,
      "escalations": 0,
      "flags": [],
      "knockout_criterion": null,
      "reopened_count": 0
    },
    "prose": "Daniel Foster's application is qualified and waiting for your approval after a smooth screening of about a minute. He confirmed the final summary, and nothing was escalated. When asked about his licence, he first repeated his zone answer, then said he has held a licence for about six years; he never stated whether it is currently valid, so you may want to confirm that. He described his availability as full time, Monday to Saturday, and chose Monday 19 October as his start date.",
    "written_at": "2026-10-12T17:57:45Z"
  }
}
```
