# A correction after a Knockout reopens the Application

The Candidate lines were typed by hand, as a test Candidate, on the web app's Demo stage; the typos are left as typed. The agent replies are real Haiku 5.5 output.

Source: the demo stack `criba-demo` (http://localhost:8080, real Claude Haiku 5.5 turns through OpenRouter, Sonnet 5.5 summaries), read on 2026-10-12 through the Operator API. The transcript, the events and the JSON all come from this one Application.

## What it shows

- A tapped licence "No" is a Knockout. A valid new answer to the failed criterion reopens the Application where it stopped, and Screening goes on to Qualified ([0033](../decisions/0033-knockout-reopens-on-a-valid-answer.md)).
- "0" years of experience skips the platforms question, and "tomorrow" is resolved against the Candidate's local date, given to the model by code each turn ([0055](../decisions/0055-every-model-call-knows-the-candidates-local-date.md), [0058](../decisions/0058-weekday-dates-come-from-the-backend.md)).
- The chat started as a Mexico chat ("colonia") and moved to Spain when the Zone was recorded ([0057](../decisions/0057-the-applications-country-follows-the-recorded-zone.md)). The Summary flags the reversal for the Recruiter to check, and `reopened_count` is 1 ([0056](../decisions/0056-the-summary-is-prose-in-plain-words-and-the-notes-stay-on-the-api.md)).

**What to notice.** Code does not believe or doubt the correction: it records the new valid answer and the Summary tells the Recruiter the licence is worth verifying. The decision stays with a person ([0012](../decisions/0012-agents-act-and-deliver.md)).

| Fact | Value |
|---|---|
| Application | `app_01M4RN4PMFN1Z14P6EGC50NR10` |
| Candidate | Ramiro Salcedo, +525500000982 (synthetic) |
| Started (UTC) | 2026-10-12 18:14:03 |
| State when read | stage `qualified`, status `open`, outcome `null` |
| Language, country | `en`, `ES` |

## Transcript

```text
Candidate: Hello
Agent: Hi! I'm Grupo Sazón's virtual (AI) assistant 🛵 I'll help you apply as a delivery driver in a few minutes. Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad. What's your full name?
Candidate: Ramiro Salcedo
Agent: Thanks, Ramiro! Which colonia and city do you live in?
Candidate: Madrid, Retiro
Agent: Perfect, Retiro in Madrid it is. Do you have a carnet de conducir that's valid? [buttons: Yes, I have one | No]
Candidate (tapped): No
Agent: Thank you, Ramiro. For now we need a valid driving licence: it's a legal and insurance requirement for delivery. Here are the requirements: https://empleo.gruposazon.example/requisitos. Once you have one, write to us and we'll pick up where we left off. If you think this is a mistake, reply "review" and a person will look at it.
Candidate: Actually I just cheked my wallet and i found it! It's still active and workingI have type B license
Agent: Good news, Ramiro, welcome back! Your application is open again. Which days are you available to work: full time, part time, or weekends? [buttons: Full-time | Part-time | Weekends]
Candidate (tapped): Part-time
Agent: Got it. What time of day works best for you: morning, afternoon, evening, or flexible? [list: Morning | Afternoon | Evening | Flexible]
Candidate (tapped): Afternoon
Agent: How many years of delivery or driving experience do you have? If none, just say 0.
Candidate: 0
Agent: When could you start? You can give me a specific date or a day of the week.
Candidate: tomorrow
Agent: Here's your summary, Ramiro:
    Name: Ramiro Salcedo
    Zone: Retiro, Madrid
    Driving licence: yes
    Availability: part time
    Preferred schedule: afternoon
    Experience: 0 years
    Start date: 2026-10-13

    Is everything correct?
Candidate: Correct!
Agent: All set, Ramiro! A Grupo Sazón recruiter will review your application and write to you here.
```

## Also on the record

From the Application detail API (`GET /api/applications/app_01M4RN4PMFN1Z14P6EGC50NR10`), the fields this sample is about. The communication notes are written by code from the Jev readings and live only on this API, not in the Summary ([0056](../decisions/0056-the-summary-is-prose-in-plain-words-and-the-notes-stay-on-the-api.md)).

```json
{
  "communication_notes": [
    {
      "kind": "writes_in",
      "text": "Writes in English: short, direct questions work.",
      "stage": null,
      "at": "2026-10-12T18:14:03Z",
      "message_id": 102
    }
  ],
  "open_queue_items": [
    {
      "id": "qi_01M4RNAPQMZTMMABEZ0DC4XWJY",
      "kind": "approval",
      "status": "open",
      "reason": null,
      "note": null,
      "created_at": "2026-10-12T18:17:19Z"
    }
  ]
}
```

## Events on the record

Ids, codes and canonical values only, never the Candidate's words ([0035](../decisions/0035-no-candidate-free-text-in-events.md)). Time is UTC.

```text
18:14:03  application_started  candidate                      {"channel": "webchat"}
18:14:19  answer_recorded      agent                          {"field": "full_name", "message_id": 104}
18:14:19  stage_changed        agent                          {"from": "greeting", "to": "zone"}
18:14:31  answer_recorded      agent                          {"field": "zone", "value": "zone_es_madrid_retiro", "message_id": 106, "locations": ["loc_es_madrid_centro", "loc_es_madrid_retiro", "loc_es_madrid_salamanca"], "country_changed": {"from": "MX", "to": "ES"}}
18:14:31  stage_changed        agent                          {"from": "zone", "to": "driver_license"}
18:14:38  answer_recorded      agent                          {"field": "driver_license", "value": false, "message_id": 108}
18:14:38  knockout             agent                          {"reason": "no", "template": "knockout_driver_license.en", "criterion": "driver_license", "stage": "driver_license", "message_id": 109}
18:14:38  screening_finished   agent                          {"result": "knockout"}
18:15:17  reopened             candidate                      {"previous_outcome": "knockout", "via": "candidate", "criterion": "driver_license", "previous_value": false}
18:15:17  answer_recorded      agent                          {"field": "driver_license", "value": true, "message_id": 110}
18:15:17  stage_changed        agent                          {"from": "driver_license", "to": "availability"}
18:15:17  summary_written      system
18:16:12  answer_recorded      agent                          {"field": "availability", "value": ["part_time"], "message_id": 112}
18:16:12  stage_changed        agent                          {"from": "availability", "to": "preferred_schedule"}
18:16:46  answer_recorded      agent                          {"field": "preferred_schedule", "value": ["afternoon"], "message_id": 114}
18:16:46  stage_changed        agent                          {"from": "preferred_schedule", "to": "experience"}
18:16:56  answer_recorded      agent                          {"field": "experience_years", "value": 0.0, "message_id": 116}
18:16:56  stage_changed        agent                          {"from": "experience", "to": "start_date"}
18:17:06  answer_recorded      agent                          {"field": "start_date", "value": "2026-10-13", "message_id": 118}
18:17:06  stage_changed        agent                          {"from": "start_date", "to": "confirm"}
18:17:19  stage_changed        agent                          {"from": "confirm", "to": "qualified"}
18:17:19  screening_finished   agent                          {"result": "qualified"}
18:17:19  approval_requested   agent                          {"queue_item_id": "qi_01M4RNAPQMZTMMABEZ0DC4XWJY", "reason": null}
18:17:19  summary_written      system
```

## Application JSON

From `GET /api/applications/app_01M4RN4PMFN1Z14P6EGC50NR10/export`, unchanged.

```json
{
  "application_id": "app_01M4RN4PMFN1Z14P6EGC50NR10",
  "candidate_id": "cand_01M4RN4K9RHW7GFSXH151W8D92",
  "channel": "webchat",
  "country": "ES",
  "language": "en",
  "status": "open",
  "stage": "qualified",
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
      "part_time"
    ],
    "preferred_schedule": [
      "afternoon"
    ],
    "experience_years": 0.0,
    "experience_platforms": null,
    "start_date": "2026-10-13"
  },
  "start_date_far": false,
  "locations": [
    "loc_es_madrid_centro",
    "loc_es_madrid_retiro",
    "loc_es_madrid_salamanca"
  ],
  "created_at": "2026-10-12T18:14:03Z",
  "updated_at": "2026-10-12T18:17:19Z",
  "qualified_at": "2026-10-12T18:17:19Z",
  "approved_at": null,
  "closed_at": null,
  "interview": null,
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
          "part_time"
        ],
        "preferred_schedule": [
          "afternoon"
        ],
        "experience_years": 0.0,
        "experience_platforms": null,
        "start_date": "2026-10-13"
      },
      "stage": "qualified",
      "outcome": null,
      "language": "en",
      "durations_seconds": {
        "total": 157,
        "by_stage": {
          "greeting": 16,
          "zone": 12,
          "driver_license": 7,
          "availability": 55,
          "preferred_schedule": 34,
          "experience": 10,
          "start_date": 10,
          "confirm": 13,
          "qualified": 0
        }
      },
      "nudges": 0,
      "escalations": 0,
      "flags": [],
      "knockout_criterion": null,
      "reopened_count": 1
    },
    "prose": "Ramiro Salcedo completed Screening and the application is qualified, now waiting for your approval. He first said he had no valid driving licence, then reversed himself a moment later, saying he found his type B licence in his wallet and that it is still active, so the licence has not been checked and is worth verifying. He has no delivery or driving experience, wants part-time afternoon work in Retiro, Madrid, and gave \"tomorrow\" as his start date, which was recorded as 13 October 2026. It is unclear whether that date matches what he meant.",
    "written_at": "2026-10-12T18:17:19Z"
  }
}
```
