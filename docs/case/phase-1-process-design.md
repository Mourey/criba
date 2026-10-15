# Conversational Screening for High-Volume Driver Hiring

**A Phase 1 design for Grupo Sazón** · Draft for owner review · 9 October 2026\
Defined terms: [`CONTEXT.md`](../../CONTEXT.md). Decisions: ADRs 0012–0030. Working detail: [phase-1-detailed-spec.md](phase-1-detailed-spec.md). All items approved by the owner on 9 October 2026.

## 1. Problem and approach

Grupo Sazón hires delivery drivers for 45 Locations in Spain and Mexico from about 1,000 applications a week. Thirteen Recruiters phone-screen about 975 candidates a week: 60% don't answer, and 80% of Recruiter time goes to candidates who don't qualify. The constraint is reach and filtering, not judgement. We replace the first call with a **Screening Agent on WhatsApp**. The Candidate taps a link and writes first; the agent ends ineligible Applications within two or three messages and hands Recruiters a structured, Qualified Application. People keep every significant decision (ADR 0012).

## 2. Conversation stages: order and branching

| #   | Stage       | The agent                                                                                                                    | Branches                                                                |
| --- | ----------- | ---------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| 0   | Greeting    | Says it is an AI assistant, links the privacy notice, asks for the name                                                      | A question first → answer briefly, then return                          |
| 1   | Name        | Collects the full name                                                                                                       | Invalid → re-ask (§5.2)                                                 |
| 2   | **Zone**    | Matches the Zone against Service Areas                                                                                       | Outside all → **Knockout** · ambiguous → offer options                  |
| 3   | **License** | Asks yes/no                                                                                                                  | No → **Knockout**                                                       |
| 4–7 | Details     | Availability, preferred schedule, experience, start date                                                                     | —                                                                       |
| 8   | Confirm     | One-message summary: "¿Es correcto?"                                                                                         | Correction → update and re-check; a Zone or license change can Knockout |
| 9   | Qualified   | Next steps (§6); the Application waits for a Recruiter's **Approval**, then the Candidate books an **Interview** in the chat | —                                                                       |

- **One question per message.** If a Candidate answers several at once ("Soy Ana, vivo en Puebla y tengo licencia"), the agent captures all of them and skips ahead.
- **Knock-out checks (2, 3) always run before the details**, so ineligible Applications end early.
- **Every answer is saved as it arrives**, so the chat resumes at the last unanswered question.
- At any stage the Candidate can withdraw (**Withdrawn**) or ask us to stop messaging (**Opted out**) (§5.4).

## 3. Agent personality

Grupo Sazón's hiring assistant, under a client-chosen name (placeholder "Sazón Empleo"). Warm, quick and practical: **a friendly shift manager, not an HR department**, respectful of the Candidate's time. It uses _tú_ in both countries and the country's vocabulary: Spain "carnet de conducir", "moto"; Mexico "licencia de manejo", "motoneta". It always says it is an AI when asked. **It never** promises a job, pay or a start date; discusses other Candidates; gives legal advice; asks about age, nationality, health, family or any protected attribute; comments on how the Candidate seems to feel; or names Criba or an Operator (every human is "the Grupo Sazón team").

## 4. Data fields and validation

| Field              | Stored as                                                                 | Valid when                                                               | If invalid or ambiguous                                            |
| ------------------ | ------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------ |
| Full name          | text                                                                      | ≥ 2 words; letters, accents, ñ, hyphens                                  | "¿Me dices tu nombre y apellido?"                                  |
| Zone               | `zone_id`; country from +34 / +52                                         | Inside ≥ 1 Service Area; the Application goes to every covering Location | "Centro" matches several → up to 3 buttons; outside all → Knockout |
| Driver's license   | yes/no + own words                                                        | A clear yes or no                                                        | "Expired", "getting it soon" → No, invited back once they have it  |
| Availability       | {full-time, part-time, weekends}                                          | ≥ 1                                                                      | Reply buttons                                                      |
| Preferred schedule | {morning, afternoon, evening, flexible}                                   | ≥ 1                                                                      | List message                                                       |
| Experience         | years ≥ 0; platform (Glovo, Uber Eats, Just Eat, Rappi, DiDi Food, other) | "6 meses" → 0.5; platform asked only if > 0                              | Informational, never a filter                                      |
| Start date         | ISO date                                                                  | Today or later; "mañana", "ya" resolved in local time                    | > 60 days out → accepted, flagged for Recruiters                   |

Answers are stored as canonical values whatever the language. Fixed choices use WhatsApp interactive messages (reply buttons up to 3 options, lists up to 10), which reduce invalid answers and typing.

## 5. Edge cases

**5.1 The Candidate stops responding**. WhatsApp allows free-form messages for 24 hours after the Candidate's last message, and only Meta-approved templates after that. The Application is **Stalled** while nudges run:

| Silent for | 1 hour                               | 20 hours                                      | 48 hours                                         | 7 days                    |
| ---------- | ------------------------------------ | --------------------------------------------- | ------------------------------------------------ | ------------------------- |
| Action     | Nudge repeating the pending question | Last free-form nudge: "te guardamos el sitio" | Approved template: "¿Seguimos donde lo dejaste?" | **Abandoned**, no message |

At most 3 nudges, none in quiet hours (22:00–09:00 local) and none to an Opted-out Candidate. A reply within 30 days **reopens** the Application at the pending question; later, a new Application starts. Every nudge is logged, so drop-off shows by stage.

**5.2 Invalid or ambiguous answers.** (1) Re-ask with an example, without blame ("Por ejemplo: Madrid, Chamberí"). (2) Switch to buttons or a list. (3) Raise an **Escalation** to Recruiters, who resolve it by Takeover or dismiss it, while the agent stays friendly. Off-topic questions (pay, vehicle, shifts) get one line from client-approved FAQ content, then the pending question; with no approved answer, the agent escalates ("alguien del equipo te lo confirma"). Voice notes and images: v1 asks for text. Abuse or attempts to redirect the agent: one polite redirect, then an Escalation.

**5.3 Spanish ↔ English.** The agent replies in the language of the Candidate's **latest** message. Switching never restarts the flow, and answers already given are kept. Spanish follows the country variant; English is plain and international. Any other language gets a Spanish reply with one English line offering both. Templates sent outside the chat (nudges, Knockouts) use the language of the last exchange and exist in es-ES, es-MX and en.

**5.4 Exits.** "No longer interested": confirm once, thank them, **Withdrawn**. "Stop messaging me": **Opted out**, with no nudges or outreach until they write again. "Delete my data": log an **Erasure request**, handled outside the chat.

## 6. Qualified and disqualified paths

**Qualified** (the Application stays open; it is not an Outcome). The agent thanks the Candidate and says a Recruiter will check their application shortly. The Application appears for every covering Location; the first Recruiter to act **Claims** it. Flags: unclaimed for 14 days, Claim idle for 7.

**Approval, then Interview booking** (ADR 0030). A Recruiter makes one final check of the answers; Phase 2 collects no documents. **Approved:** the agent writes at once and offers **3 Interview slots as buttons**, taken from availability the Recruiters publish. Outside WhatsApp's 24-hour window the offer goes out as an approved template. "None of these work" gets a link to a booking page with every open slot. A booked slot sends a `.ics` invite to the Candidate and to the Recruiter. **Not approved:** a Rejection with a reason. The Application stays open after booking, until a hiring Outcome from a later part of the case, a Rejection or a Withdrawal.

**Knockout** (no license, or Zone outside every Service Area). Sent at once from a client-approved template with: the reason in plain words, why the requirement exists, a link to the minimum requirements, an invitation to come back, and how to ask for a person to **Review** it (Upheld or Overturned, in which case screening resumes). A reply within 30 days reopens it at the failed check (refined by ADR 0033: only a new valid answer to that check does). Automating this relies on GDPR Art. 22(2)(a) with these safeguards; Grupo Sazón documents the basis (ADR 0016), and Mexico is assessed under the LFPDPPP.

**Rejection**: a Recruiter's decision at any stage, always with a reason (default list: no-show, failed call, role filled, other with a note; Grupo Sazón can add or rename reasons), sent by template once confirmed. **Abandoned** and **Withdrawn** as in §5. A Recruiter or Operator can make a **Takeover** at any stage; the Candidate is told "someone from the Grupo Sazón team" has joined, and the agent resumes on hand-back.

## 7. Message tone and length

This is chat, not email. The examples are approved reference copy; the final templates still need Grupo Sazón's approval.

- **1–2 sentences, under 300 characters**; never more than 3 short lines. One question per message.
- **Split, don't stack:** two short messages beat one long one, and never more than 2 in a row.
- **Spoken and plain:** no corporate phrasing, no bullet lists except the confirmation summary.
- **At most one emoji**, none in Knockout or Rejection messages. Use the first name now and then, not every time.
- **Speed is tone:** first reply within 5 seconds, later replies within 10.

> **Opening (es-ES):** ¡Hola! Soy el asistente virtual de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestra política: [link] · ¿Cómo te llamas (nombre y apellido)?
>
> **Nudge at 1 hour (es-ES):** ¿Sigues por ahí, Ana? Solo me falta saber una cosa: ¿tienes carnet de conducir?
>
> **Qualified (es-MX):** ¡Listo, Luis! Ya tenemos todo. Un reclutador de Grupo Sazón revisará tu solicitud y te escribirá por aquí con el siguiente paso.
>
> **Approved, Interview offer (es-MX):** ¡Buenas noticias, Luis! Tu solicitud está aprobada. ¿Cuándo te viene bien la entrevista? [mar 14 · 10:00] [mar 14 · 16:00] [mié 15 · 11:00] [Otro horario]
>
> **Knockout (es-MX):** Gracias, Luis. Por ahora necesitamos licencia de manejo vigente: es un requisito legal y del seguro para repartir. Aquí están los requisitos: [link]. Cuando la tengas, escríbenos y seguimos donde lo dejamos. Si crees que hay un error, responde "revisión" y una persona lo verá.

## 8. Assurance and dependencies

Every message, nudge, Knockout, Review, Approval, Interview booking and Takeover is an event on the Application, which serves as the audit trail. Every chat discloses AI use (EU AI Act). Before launch, simulated candidate personas exercise every path above (ADR 0017). **Needed from Grupo Sazón:** DPO sign-off on the legal basis; vehicle types and license classes; Service Area data for the 45 Locations; approved FAQ and templates in es-ES, es-MX and en; the persona name and any additions to the Rejection reasons; Interview availability for each Location.
