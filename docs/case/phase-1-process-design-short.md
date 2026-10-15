# Conversational Screening for Driver Hiring: Process Design (short)

**Grupo Sazón · Phase 1 · two-page version of [phase-1-process-design.md](phase-1-process-design.md)**\
Defined terms: [`CONTEXT.md`](../../CONTEXT.md). Full detail: [phase-1-detailed-spec.md](phase-1-detailed-spec.md).

A **Screening Agent on WhatsApp** replaces the first phone call. The Candidate taps a link and writes first. The agent ends ineligible Applications within two or three messages and hands Recruiters a structured, Qualified Application. People make every significant decision.

## 1. Conversation stages

| #   | Stage       | The agent                                                               | Branches                                                         |
| --- | ----------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------- |
| 0   | Greeting    | Says it is an AI assistant, links the privacy notice, asks for the name | A question first: answer briefly, then return                    |
| 1   | Name        | Collects the full name                                                  | Invalid: re-ask                                                  |
| 2   | **Zone**    | Matches the Zone against Service Areas                                  | Outside all: **Knockout**. Ambiguous: offer options              |
| 3   | **License** | Asks yes/no                                                             | No: **Knockout**                                                 |
| 4-7 | Details     | Availability, preferred schedule, experience, start date                | None                                                             |
| 8   | Confirm     | One-message summary, "¿Es correcto?"                                    | Correction: update and re-check; a Zone or license change can Knockout |
| 9   | Qualified   | Explains next steps                                                     | A Recruiter's **Approval**, then the Candidate books an **Interview** in the chat |

- One question per message. If the Candidate answers several at once, the agent captures all and skips ahead.
- Knockout checks (2, 3) run before the details, so ineligible Applications end early.
- Every answer is saved as it arrives, so the chat resumes at the last unanswered question.
- At any stage the Candidate can withdraw (**Withdrawn**) or ask us to stop messaging (**Opted out**).

## 2. Agent personality

Grupo Sazón's hiring assistant under a client-chosen name. Warm, quick and practical: **a friendly shift manager, not an HR department**. It uses _tú_ and local vocabulary (Spain "carnet de conducir"; Mexico "licencia de manejo"), and says it is an AI whenever asked. **It never** promises a job, pay or start date; gives legal advice; asks about age, nationality, health, family or any protected attribute; or names Criba (every human is "the Grupo Sazón team").

## 3. Data fields and validation

| Field              | Stored as                          | Valid when                              | If invalid or ambiguous                                    |
| ------------------ | ---------------------------------- | --------------------------------------- | ---------------------------------------------------------- |
| Full name          | text                               | 2+ words, letters, accents, hyphens     | "¿Me dices tu nombre y apellido?"                          |
| Zone               | `zone_id`; country from +34 / +52  | Inside at least one Service Area        | "Centro" matches several: up to 3 buttons. Outside all: Knockout |
| Driver's license   | yes/no                             | A clear yes or no                       | "Expired", "getting it soon" count as No, invited back     |
| Availability       | full-time, part-time, weekends     | At least one                            | Reply buttons                                              |
| Preferred schedule | morning, afternoon, evening, flexible | At least one                         | List message                                               |
| Experience         | years >= 0; platform if > 0        | "6 meses" becomes 0.5                   | Informational, never a filter                              |
| Start date         | ISO date                           | Today or later; "mañana" resolved locally | More than 60 days out: accepted, flagged                 |

Answers are stored as canonical values in either language. Fixed choices use WhatsApp buttons (up to 3) and lists (up to 10), which cut invalid answers.

## 4. Edge cases

**Candidate stops responding.** Free-form messages are allowed for 24 hours after the Candidate's last message, then only approved templates. The Application is **Stalled** while nudges run, at most 3, none in quiet hours (22:00-09:00 local) or to an Opted-out Candidate:

| Silent for | 1 hour                            | 20 hours                    | 48 hours                       | 7 days                    |
| ---------- | --------------------------------- | --------------------------- | ------------------------------ | ------------------------- |
| Action     | Nudge repeating the pending question | Last free-form nudge     | Approved template              | **Abandoned**, no message |

A reply within 30 days reopens the Application at the pending question; later, a new Application starts.

**Invalid or ambiguous answers.** (1) Re-ask with an example, without blame. (2) Switch to buttons or a list. (3) Raise an **Escalation** so a Recruiter can take over, while the agent stays friendly. Off-topic questions get one line from client-approved FAQ content, then the pending question; with no approved answer, the agent escalates. Voice notes and images: v1 asks for text.

**Spanish <-> English.** The agent replies in the language of the Candidate's latest message. Switching never restarts the flow, and answers already given are kept. Any other language gets a Spanish reply with one English line offering both. Templates exist in es-ES, es-MX and en.

**Exits.** "No longer interested": confirm once, **Withdrawn**. "Stop messaging me": **Opted out**. "Delete my data": log an Erasure request, handled outside the chat.

## 5. Qualified and disqualified paths

- **Qualified** (the Application stays open). The agent says a Recruiter will check it shortly. The first Recruiter to act **Claims** it.
- **Approved.** The agent writes at once and offers 3 Interview slots as buttons; "none work" links to a booking page. A booked slot sends an `.ics` invite to both sides.
- **Not approved.** A Rejection with a reason, sent once a Recruiter confirms.
- **Knockout** (no license, or Zone outside every Service Area). Sent at once from an approved template: the reason in plain words, why it is required, a link to the requirements, an invitation to come back, and how to ask for a person to **Review** it. A new valid answer to the failed check within 30 days reopens it.
- **Abandoned** and **Withdrawn** as in section 4. A Recruiter can make a **Takeover** at any stage; the agent resumes on hand-back.

## 6. Message tone and length

This is chat, not email.

- 1-2 sentences, under 300 characters, never more than 3 short lines. One question per message.
- Split rather than stack: never more than 2 messages in a row.
- Spoken and plain: no corporate phrasing, no bullets except the confirmation summary.
- At most one emoji, none in Knockout or Rejection messages.
- Fast replies: first within 5 seconds, later within 10.

> **Opening (es-ES):** ¡Hola! Soy el asistente virtual de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. ¿Cómo te llamas (nombre y apellido)?
>
> **Nudge at 1 hour (es-ES):** ¿Sigues por ahí, Ana? Solo me falta saber una cosa: ¿tienes carnet de conducir?
>
> **Knockout (es-MX):** Gracias, Luis. Por ahora necesitamos licencia de manejo vigente: es un requisito legal y del seguro para repartir. Cuando la tengas, escríbenos y seguimos donde lo dejamos. Si crees que hay un error, responde "revisión" y una persona lo verá.
