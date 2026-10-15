# Grupo Sazón: driver screening, detailed specification (Phase 1)

Status: draft, 2026-10-09. Companion to the white paper [phase-1-process-design.md](phase-1-process-design.md); this file holds the field-level rules. All items, including the former proposals (personality, Stalled timings, Rejection reasons, tone), were approved by the owner on 2026-10-09. Everything follows the owner's decisions and ADRs 0012–0030 (`docs/decisions/`). Capitalised terms (Candidate, Application, Knockout, Recruiter, and so on) are defined in [`CONTEXT.md`](../../CONTEXT.md).

Grupo Sazón hires delivery drivers for 45 Locations in Spain and Mexico, from about 1,000 applications a week. The Candidate opens a WhatsApp chat from a link on the website and writes first, which starts their Application. The Screening Agent screens them in a few minutes, ends unqualified Applications within two or three messages with a Knockout, and hands Qualified Applications to Recruiters.

## 1. Conversation stages

| # | Stage | Agent does | Branches |
|---|---|---|---|
| 0 | **Greeting** | Greets, says it is an AI assistant, links to the privacy notice, asks for the name | Candidate asks a question first → answer briefly, then return here |
| 1 | **Name** | Collects full name | Invalid → re-ask (§4.2) |
| 2 | **Zone** | Collects the Zone and checks it against Service Areas | Outside every Service Area → **Knockout** (§5) · ambiguous place name → offer matching options |
| 3 | **Driver's license** | Asks yes/no | No → **Knockout** (§5) |
| 4 | **Availability** | Full-time / part-time / weekends | — |
| 5 | **Preferred schedule** | Morning / afternoon / evening / flexible | — |
| 6 | **Experience** | Years, and platform if more than 0 years | — |
| 7 | **Start date** | When they can begin | — |
| 8 | **Confirm** | Shows a one-message summary: "Is this right?" | Correction → update that field and re-validate (a Zone or license correction can cause a Knockout) |
| 9 | **Qualified** | Next steps (§5). Screening ends; the Application waits for a Recruiter's **Approval**, then the Candidate books an **Interview** | — |

At any stage, the Candidate can say they're no longer interested, which ends the Application as **Withdrawn**. They can also ask us to stop messaging them, which marks them **Opted out** (§4.2).

Rules:
- One question per message.
- If the Candidate answers several things at once ("Soy Ana, vivo en Puebla y tengo licencia"), the agent captures them all and skips ahead.
- The knock-out checks (2, 3) always run before details are collected.
- Every answer is saved to the Application as it arrives, so the conversation can resume from the last answered question.

## 2. Agent personality

- **Who it is:** the Screening Agent, presented as Grupo Sazón's hiring assistant under the Client's brand. The name is the client's choice (placeholder: "Sazón Empleo"). It never pretends to be human, and says it is an AI whenever asked.
- **Character:** warm, quick and practical. It sounds like a friendly shift manager, not an HR department, and respects the candidate's time.
- **Form of address:** *tú* in both countries.
- **Language variant:** follows the country. Spain uses "carnet de conducir", "moto"; Mexico uses "licencia de manejo", "motoneta".
- **Never:**
  - promises a job, pay or a start date;
  - discusses other Candidates;
  - gives legal advice;
  - asks about age, nationality, health, family or any other protected attribute;
  - comments on how the Candidate seems to feel;
  - names Criba or an Operator. Every human is "the Grupo Sazón team".

## 3. Data fields and validation

| Field | Stored as | Valid when | If invalid or ambiguous |
|---|---|---|---|
| Full name | `string` | At least 2 words, letters only (accents, ñ and hyphens allowed) | "¿Me dices tu nombre y apellido?" |
| Zone | `zone_id`; country from the phone prefix (+34 / +52) | Inside at least one Service Area. The Application then belongs to every Location covering that Zone (ADR 0021) | Place name matches several Zones ("Centro") → up to 3 buttons. Outside every Service Area → **Knockout** (§5) |
| Driver's license | `bool`, plus the Candidate's own words | Clear yes or no | "Expired" or "getting it soon" → treated as **No**, with an invitation to come back once they have it. Vehicle and license class are open questions with the client. |
| Availability | set of `{full_time, part_time, weekends}` | At least one | Reply buttons |
| Preferred schedule | set of `{morning, afternoon, evening, flexible}` | At least one | List message |
| Experience | `years: number ≥ 0`; `platforms: [glovo, uber_eats, just_eat, rappi, didi_food, other]` | Years parsed ("6 meses" → 0.5); platform asked only if years > 0 | Informational only, never a filter |
| Start date | ISO date | Today or later ("mañana", "el lunes", "ya" → resolved in the Candidate's local time) | More than 60 days out → accepted, and flagged for Recruiters |

Answers are stored in canonical values, whatever language the Candidate used. Options with fixed values use WhatsApp's interactive messages: reply buttons for up to 3 options, list messages for up to 10. These tap-to-answer messages cut invalid answers and typing.

**Validation rules settled while building (wave 1, lane B; approved 2026-10-09), split by [ADR 0042](../decisions/0042-agent-interprets-code-verifies.md).** These refine the table above. The Screening Agent reads the Candidate's words and passes `record_answer` a structured value per field (`x-answer-values` in `contracts/tools.json`), or `null` when the answer maps to no value; code checks that value and decides every Knockout. Interpretation rules belong to the Screener skill (`src/skills/criba/screener/SKILL.md`, spec 006b); verification rules are code (`src/backend/criba/screening/verify.py`). Until spec 006a task 11 the old parsers in `src/backend/criba/screening/` still read the Candidate's words when the agent passes them instead of a value, and apply the interpretation rules below themselves.

*Interpretation (the agent, in the skill)*

- **Zone:** look the place up with `search_zones` and pass the ids it returns; for a city alone, every id it returns for that city (up to 10). At most one clarifying question per field without a call ("¿En qué colonia?"); a second answer that still can't be mapped is `null`. The phone country only breaks ties (the Application's country comes first in the search).
- **Driver's license:** a code, `yes`, `no`, `expired`, `suspended` or `pending`. "Getting it soon" is `pending`; "expires next month" is `yes`; "I don't know" is `null`, a miss, never No.
- **Full name:** pass the name cleaned of greetings and "me llamo".
- **Start date:** an ISO date resolved against `today` from `get_application`, in the Candidate's local calendar. A weekday means its next occurrence; a day and month already past this year rolls to next year.
- **Availability and experience:** "cualquiera" or "any" means all three availability options, and a negation in the same clause removes one ("pero no fines de semana"). An experience range keeps its lower bound, and months are divided by 12; a platform not on the list is `other`; no platforms is an empty list.

*Verification (code)*

- **An unknown place is not a Knockout.** A Zone Knockout fires only on a known Zone that no Service Area covers. A Zone id that doesn't exist is the agent's error (no miss); `null` is re-asked like any invalid answer, and the 3-miss Escalation applies (§4.2). Reason: we can't tell "a town we don't serve" from a typo, and a Knockout must rest on something we know. The Zone data therefore needs uncovered Zones for places Candidates often name.
- **One uncovered Zone found by the agent is confirmed first** ("¿Vives en Toluca?", Sí/No buttons). Sí is the Knockout; No counts no miss and the Zone is asked again. An uncovered Zone the Candidate tapped from buttons or a list is the Knockout directly, and after a Knockout a still-uncovered answer keeps the Application closed (ADR 0033).
- **Several Zones:** 2 or 3 are offered as buttons, covered or not; 4 to 10 in one city are that city's list (Q45), not a miss; 4 or more across several cities are a miss (`ambiguous_zone`), and the agent asks which city.
- **Driver's license:** `yes` is stored as true; each other code is a Knockout, stored as false, with its own template variant (no, expired, suspended, pending).
- **Full name:** 2–8 words of letters (accents, ñ, hyphens and apostrophes allowed, as in "O'Brien"), at least 4 letters, at most 80 characters; text typed all in lower or upper case is title-cased.
- **Start date:** a real date, today or later in the Candidate's local time (else `past_start_date`, a miss); more than 60 days out is accepted and flagged.
- **Codes and years:** option codes must be in their enums, kept in canonical order; experience years between 0 and 60 (else `out_of_range`, a miss); 0 years skips the platform question.
- **Misses** (§4.2) count only the Candidate's answers that fail a rule: `null`, or a value that fails verification. A value of the wrong shape for its field (`invalid_args`), an unknown Zone id, a city's list, a needs-choice and a No to the Zone confirmation count none. Events keep the canonical value and the inbound `message_id`, a miss keeps its `reason`, and the Candidate's own words for Zone and licence are copied from their message, never from the agent's value.

## 4. Edge cases

### 4.1 The Candidate stops replying: Stalled, then Abandoned

| Silent for | Action |
|---|---|
| 1 hour | Nudge that repeats the pending question (free-form) |
| 20 hours | Last nudge inside WhatsApp's 24-hour window: "We'll keep your place" |
| 48 hours | WhatsApp template (approved by Meta): "Want to pick up where you left off?" |
| 7 days | The Application ends as **Abandoned**, with no message. A reply within 30 days **reopens** it at the pending question; after that, it starts a new Application |

Limits:
- at most 3 nudges;
- none during quiet hours (22:00–09:00 in the Candidate's local time); the one exception is the message after a Recruiter's Approval, which is sent at once if the Candidate wrote in the last 60 minutes (ADR 0046);
- none to an Opted-out Candidate;
- every nudge is logged, so the dashboard shows drop-off by stage.

### 4.2 Invalid or ambiguous answers

1. **First miss:** re-ask with an example or options, without blaming the Candidate ("Por ejemplo: Madrid, Chamberí").
2. **Second miss:** switch to buttons or a list where possible.
3. **Third miss:** raise an **Escalation** to the Recruiters' queue, and keep the conversation friendly while waiting. A Recruiter resolves it by a Takeover or by dismissing it.

Other cases:
- **Off-topic question** (pay, vehicle, shifts): answer in one line from client-approved FAQ content, then return to the pending question. If there's no approved answer, raise an Escalation and tell the Candidate "someone from the team will confirm that."
- **Correction later in the conversation:** update the field and run its check again.
- **Voice note or image:** v1 asks for text. Transcribing voice notes is a later option.
- **Abuse, or attempts to redirect the agent:** one polite redirect to the task. If it continues, raise an Escalation.
- **"I'm no longer interested"** (found another job, changed plans): confirm once, thank them, and end the Application as **Withdrawn**. No further messages.
- **"Stop messaging me":** confirm, and mark the Candidate **Opted out**. No nudges or outreach on any Application until they write again.
  - A STOP with no open Application (a new Candidate, or one whose Applications are all closed, including an Abandoned or Knockout one inside the 30-day window) never creates or reopens one (Q42): the Candidate is marked, the confirmation goes out, and nothing else changes.
- **"Delete my data":** acknowledge it, log an **Erasure request** for the team, and end the conversation. The request is handled outside the chat.

### 4.3 The Candidate switches language (Spanish ↔ English)

- The agent replies in the language of the Candidate's **latest** message. Switching doesn't restart the flow, and answers already given are kept.
- Spanish follows the country variant (§2). English is plain international English.
- A one-word or very short answer in another language ("Mañana", "Sí", "OK", a place name, a number) does not switch. Only a full sentence in the other language, a request to switch, or a greeting that opens the chat does. The reply and the stored language always agree.
- In another language, the agent replies in Spanish with an English line offering both.
- Templates sent from outside the chat (knockout, stalled) use the language of the conversation's last exchange. Each exists in es-ES, es-MX and en.

## 5. How Screening ends, and Outcomes

Screening ends in one of two ways (`CONTEXT.md`):
- **Qualified:** the Application passed Screening and **stays open**, in the Recruiters' hands.
- **An Outcome** ends the Application during Screening: Knockout, Abandoned or Withdrawn.

Every closed Application has exactly one Outcome: **Knockout**, **Rejection**, **Abandoned** or **Withdrawn**. Outcomes from hiring come with the next part of the case.

### Qualified (not an Outcome)

- The Screening Agent thanks the Candidate and says a Recruiter will check their application shortly, and how to reach the team.
- The Application appears in the Recruiters' web panel for every covering Location. The first Recruiter to act takes a **Claim** on it, so the others see it is handled. An unclaimed Qualified Application is flagged **Qualified, waiting** after 14 days, and a Claim with no action is flagged **Claimed, idle** after 7 days.
- It waits for **Approval** (below). It stays open until a Recruiter's **Rejection**, the Candidate's **Withdrawal**, or a hiring Outcome from a later part of the case.

### Approval and Interview booking (ADR 0030)

- **Approval:** a Recruiter's final check of a Qualified Application, from the review queue. Phase 2 checks the **answers only**; collecting documents such as a licence or ID photo comes later. It ends **Approved** or in a **Rejection** with a reason. It is not a Review, which is only for a disputed Knockout.
- **On Approved**, the Screening Agent writes at once and offers **3 Interview slots** as reply buttons. The slots come from availability Recruiters publish for each Location in the tracker. Outside WhatsApp's 24-hour window, the offer goes out as an approved template.
- **"None of these work"** (or no reply to the buttons) gets a link to a booking page in the web app with every open slot for the covering Locations.
- **Booking:** the slot is locked so no two Candidates can take it. It is recorded as an event on the Application, and a `.ics` invite goes to the Candidate and to the Recruiter. Nothing is written to Grupo Sazón's calendars.
- **Rescheduling:** the Candidate can ask in the chat. The agent offers new slots and releases the old one.
- **The Application stays open after booking**, until a hiring Outcome (a later part of the case), a Rejection or a Withdrawal.

### Outcomes


**Knockout**: during Screening (Zone outside every Service Area, or no license; ADR 0016). One message, sent at once, from a template Grupo Sazón approves. It contains:
- the reason, plainly;
- why the requirement exists;
- a link to the minimum hiring requirements or FAQ;
- an invitation to write again when circumstances change;
- how to ask for a person to review the decision.

The Application ends as a Knockout, with the criterion recorded. If the Candidate asks, a **Review** goes to the Recruiters' queue. It is either **Upheld** (the Knockout stands) or **Overturned** (the Application reopens and screening continues). A Candidate who writes again within 30 days **reopens** the Application at the failed check. (Refined by ADR 0033: only a new valid answer to the failed check reopens it; any other message, such as "revisión", leaves it closed so a Review can be requested.)

**Rejection**: a Recruiter can end an Application at any stage, during Screening or after Qualified (at Approval, or later, for example after an Interview no-show), always with a reason (default list: no-show, failed call, role filled, other with a note; Grupo Sazón can add or rename reasons for each Location). The Candidate is told by a client-approved template once the Recruiter confirms.

**Abandoned**: during Screening, the Candidate went silent through the whole nudge sequence (§4.1). It can Reopen within 30 days, on any message (a Knockout reopens only on a valid new answer; ADR 0033).

**Withdrawn**: during Screening or after Qualified, the Candidate called it off themselves (§4.2).

At any stage, a Recruiter or Operator can make a **Takeover** (ADR 0020). The Candidate is told "someone from the Grupo Sazón team" has joined. When the person hands back, the Screening Agent continues from the Application.

## 6. Message tone and length

- **Short messages.** Usually 1–2 sentences and under 300 characters. Never more than 3 short lines. **One question per message.**
- **Split rather than stack.** If there's more to say, send two short messages rather than one long one. Never more than 2 messages in a row.
- **Plain, spoken language.** No corporate phrasing and no bullet lists in candidate messages, except the confirmation summary (§1, stage 8).
- **Emoji:** at most one per message, and none in Knockout or Rejection messages.
- **Name:** use the Candidate's first name occasionally, not in every message.
- **Speed is part of the tone.** Target the first reply within 5 seconds, and every later reply within 10.

The examples below are approved reference copy. The final templates still need Grupo Sazón's approval.

Example opening (es-ES):
> ¡Hola! Soy el asistente virtual de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestra política: [link]
> ¿Cómo te llamas (nombre y apellido)?

Example nudge at 1 hour (es-ES):
> ¿Sigues por ahí, Ana? Solo me falta saber una cosa: ¿tienes carnet de conducir?

Example Qualified close (es-MX):
> ¡Listo, Luis! Ya tenemos todo. Un reclutador de Grupo Sazón revisará tu solicitud y te escribirá por aquí con el siguiente paso.

Example Interview offer after Approval (es-MX):
> ¡Buenas noticias, Luis! Tu solicitud está aprobada. ¿Cuándo te viene bien la entrevista?
> [mar 14 · 10:00] [mar 14 · 16:00] [mié 15 · 11:00] [Otro horario]

Example Knockout (es-MX):
> Gracias, Luis. Por ahora necesitamos licencia de manejo vigente: es un requisito legal y del seguro para repartir. Aquí están los requisitos: [link]. Cuando la tengas, escríbenos y seguimos donde lo dejamos. Si crees que hay un error, responde "revisión" y una persona lo verá.
