# Criba screening

The agents in this system screen frontline job candidates over chat on behalf of a Client, and hand qualified ones to the Client's Recruiters. The first Client is Grupo Sazón, which hires delivery drivers in Spain and Mexico.

## People and organisations

**Client**:
A company that uses the system to hire, such as Grupo Sazón.
_Avoid_: Customer, tenant

**Candidate**:
A person who might work for a Client. They exist from their first message, or from being imported, onward.
_Avoid_: Applicant, lead, driver (until hired)

**Not contacted**:
An imported Candidate whom the system hasn't messaged yet.

**Contacted**:
An imported Candidate who has been sent outreach but hasn't replied yet.

**Unresponsive**:
A Contacted Candidate who never replied after the outreach sequence ended. No Application was ever started.
_Avoid_: Abandoned (which needs a started Application)

**Opted out**:
A Candidate who asked not to be messaged. It blocks every follow-up and outreach message the system would send, for every Application. It is lifted when the Candidate writes to us again. A STOP with no open Application never creates or reopens one (Q42).
_Avoid_: Unsubscribed, blocked

**Erasure request**:
A Candidate's request to have their personal data deleted under GDPR or LFPDPPP. It is handled outside the screening chat. If the same person writes again later, they are a new Candidate with no link to the old one.
_Avoid_: Opt-out, deletion

**Recruiter**:
A human on the Client's staff who works Applications, handles Reviews and Escalations, and can take over chats.
_Avoid_: Hiring staff, HR (as a person)

**Operator**:
A member of Criba's own staff who runs the system for Clients. Candidates never see an Operator named; to them, every human is "the Client's team".
_Avoid_: Admin, Criba staff, support

**Screening Agent**:
The AI agent that holds the screening chat with a Candidate. It is presented to Candidates under a Client-branded persona name. A Voice call is held by its voice flow: its own profile and skill, the same tools and rules (ADR 0059).
_Avoid_: Recruiter agent, bot

**Voice call**:
A Screening held by speech in the browser, on its own `voice` channel, never inside a chat: the Candidate only hears the agent, so nothing points to a link, a button or a message. ElevenLabs Agents listens and speaks, and our backend answers each Spoken turn as its custom LLM; no audio is stored. A voice-only Candidate gets no written follow-up until a messaging channel exists (ADR 0059, 0065).
_Avoid_: Voice chat, voice mode, voice note (a voice note is an audio file sent in a chat)

**Spoken turn**:
One thing the Candidate said on a Voice call, as ElevenLabs transcribed it, and the agent's spoken reply. It is stored as a message like a typed one, but Jev reads it after the reply and never changes the language from it (ADR 0065).
_Avoid_: Voice message, voice note

## Hiring structure

**Location**:
A Client site that hires, such as one restaurant.
_Avoid_: Store, branch, site

**Service Area**:
The set of Zones a Location delivers to.
_Avoid_: Coverage, delivery area

**Zone**:
The city or neighbourhood a Candidate says they work from. It is matched against Service Areas. A place that matches no Zone at all (Texas) is not a Knockout until the Candidate answers the Zone funnel.
_Avoid_: City/zone, address

**Zone funnel**:
The one question asked when a Candidate's place matches no Zone: which of the cities that have a Service Area in their country they can work in, as a list ending with "None of these". A city leads to its Zones; "None of these", or a second place that still matches no Zone, is the Zone Knockout, with no Zone stored (ADR 0068). It does not depend on how the agent searched or whether it recorded anything: after a Zone question the agent left unhandled, code offers it (ADR 0076).
_Avoid_: Area question, Zone fallback

## Screening

**Application**:
One attempt by a Candidate to get hired, with its own screening answers, outcome and history. It starts with the Candidate's first message in that attempt. A Candidate can have many Applications over time.
_Avoid_: Case, case record, profile

**Claim**:
A Recruiter taking ownership of an Application that several Locations can see, so other Recruiters know it is being handled. It lasts until the Application ends or the Claim is released.
_Avoid_: Assignment, lock

**Claimed, idle**:
A Claim with no Recruiter action for 7 days. Any Recruiter at a covering Location may take it over.

**Knock-out Criterion**:
A screening requirement that, if unmet, ends the Application automatically. For Grupo Sazón: holding a driver's license, and a Zone inside some Service Area.
_Avoid_: Filter, disqualifier

**Knockout**:
The automated outcome of an Application that failed a Knock-out Criterion.
_Avoid_: Disqualification, rejection

**Rejection**:
A decision by a Recruiter to end an Application, at any stage and always with a reason. The Candidate is told once the Recruiter confirms.
_Avoid_: Knockout, disqualification

**Screening**:
The chat in which the Screening Agent collects and checks a Candidate's answers. It ends either with the Application Qualified, or with an outcome (Knockout, Abandoned, Withdrawn).
_Avoid_: Interview, assessment

**Qualified**:
The Application passed Screening and is waiting for Approval. It is not an outcome: the Application stays open, in the Recruiters' hands, until an outcome ends it.
_Avoid_: Hired, passed

**Approval**:
A Recruiter's final check of a Qualified Application. It ends **Approved**, which lets the Candidate book an Interview, or in a Rejection.
_Avoid_: Review (which is only for a disputed Knockout), sign-off

**Interview**:
A meeting between an Approved Candidate and the Client, booked by the Candidate into a slot the Recruiters published.
_Avoid_: Call, appointment

**Outcome**:
How an Application ends. Every closed Application has exactly one: Knockout, Rejection, Abandoned or Withdrawn. Outcomes from hiring come in a later part of the case.
_Avoid_: Result, status

**Qualified, waiting**:
A Qualified Application that has gone unclaimed for 14 days without an Approval.

**Withdrawn**:
The outcome of an Application the Candidate called off themselves ("I found another job").
_Avoid_: Abandoned, cancelled

**Stalled**:
An open Application whose Candidate has stopped replying, while follow-up nudges are still running.
_Avoid_: Inactive, paused

**Abandoned**:
The outcome of a Stalled Application once the nudge sequence ends without a reply.
_Avoid_: Stalled closed, dropped, expired

**Reopen**:
Continuing a closed Application instead of starting a new one, within 30 days of its end. An Abandoned Application reopens on any message from the Candidate. A Knockout reopens only on a new valid answer to the failed criterion (ADR 0033); any other message, such as a dispute, reaches the Screening Agent with the Application still closed. An Overturned Review also reopens a Knockout, with no time limit.

## Human involvement

**Review**:
A human check of a Knockout, asked for by the Candidate. It ends **Upheld** (the Knockout stands) or **Overturned** (the Application reopens and screening continues).
_Avoid_: Appeal, dispute

**Escalation**:
The Screening Agent asking for human attention, for example after repeated invalid answers, abuse, or a question it can't answer. The backend raises one too when a message clearly asks for a person or is abusive (ADR 0051), and after three **Unreadable messages** in a row (ADR 0063). It is resolved by a Takeover or by being dismissed.
_Avoid_: Flag, alert, needs_human

**Takeover**:
A Recruiter or Operator holding a Candidate's chat in place of the Screening Agent, until they hand it back.
_Avoid_: Handoff

**System alarm**:
One open notice per cause that the shared model dependency (OpenRouter) is failing: `credits`, `rate_limited`, `key_rejected` or `model_unavailable`. It opens when failed runs of several Applications show the cause, or when the hourly check of the key's balance is low or rejected, and clears when a run succeeds and the key is fine. It is for the Operator's monitor (`GET /health/alarms`), is not a queue item, and does not replace the **Escalation** each affected Application still gets (ADR 0078).
_Avoid_: Alert, outage Escalation

**Owed turn**:
A typed Candidate message we still owe a reply to: recorded when the message is accepted, gone once the reply is sent. A turn that failed or was cut off by a restart stays owed and is run again, in order and under the same Idempotency-Key; a Takeover drops it. While a reply is owed the Candidate is waiting on us, so the Stalled clock does not run. Spoken turns are never owed (ADR 0073).
_Avoid_: Pending message, retry queue, lost turn

**Holding message**:
The fixed "dame un momento" reply sent when a turn fails, at most once per outage, while the owed turn is retried (ADR 0073).
_Avoid_: fallback message, fallback reply

## Conversation signals

**Tone hint**:
A one-turn instruction the backend gives the Screening Agent when a Candidate's latest typed message or Spoken turn reads as frustrated, confused, asking for a person or abusive: apologise, use simpler words, offer a person. It lasts until the agent replies and holds codes only; on a Spoken turn it is read after the reply, so it applies to the next turn. It is never a property of the Candidate and never an input to a Knockout, Rejection or any decision (ADR 0029, 0051, 0065).
_Avoid_: Sentiment score, mood, emotion

**Message signals**:
What the Jev router read from one typed message or Spoken turn: the language and the tone, as probabilities, stored with the message and the stage it arrived at, with no text, and deleted after 180 days. A typed message also gets an **Intent reading** and a **Manipulation score**. They describe a message, not the person (ADR 0051, 0065, 0071).
_Avoid_: Candidate score, profile

**Intent reading**:
What Jev read one typed message as mainly trying to do: answering, asking, off-topic, wants to stop, trying to steer the agent, or other (greetings, thanks, small talk), with the confidence of that choice. One of the **Message signals**, stored with the message and kept NULL on a Spoken turn. The confidence is shown to Operators only; Recruiters see the choice as one of the **Message labels**, except trying to steer the agent (ADR 0075). Display only: it has no threshold, raises no Escalation and is never an input to a Knockout, Rejection or Approval (ADR 0071).
_Avoid_: malice, malicious, threat score

**Manipulation score**:
The probability Jev gave that one typed message tries to manipulate the assistant: to override or reveal its instructions, pose as the system or the team, or claim an approval it does not have. One of the **Message signals**, stored with the message and kept NULL on a Spoken turn (the column and the question are named `malice`). Shown to Operators only and display only: no threshold, no Escalation, never an input to a Knockout, Rejection or Approval. It describes a message, not the person (ADR 0071, 0051).
_Avoid_: malice, malicious, threat score

**Message labels**:
What Jev read from one typed message, as words for every role under the message in the Transcript: the language, the signals that fired (frustrated, confused, wants a person), the intent and the tone hint the agent was given. Never a probability, a confidence, a threshold or a **Manipulation score**, and never the abusive signal or the steering intent, which would read as a judgement of the person. Context for a person who decides, never an input to a Knockout, Rejection or Approval (ADR 0075).
_Avoid_: Jev score, tone rating

**Unreadable message**:
A typed Candidate message that is not a word, an answer or a sentence in any language: keyboard mashing, random letters, made-up words. Typos, slang, names, numbers, emoji and other languages are not. Each counts as a miss on the pending question; the third in a row raises an **Escalation** that hands the conversation to the Recruiters, and the Screening Agent stops replying until it is dismissed or a **Takeover** ends (ADR 0063).
_Avoid_: Gibberish, spam, invalid message

**Communication notes**:
Short sentences for the Recruiter about how the conversation went and how to approach the Candidate ("Asked for a person at the start date question (17:13)"). Built from the message signals by templates. Context for a person who decides, never an assessment of the Candidate or of fitness for the job (ADR 0051). Shown on the Application detail only, never inside the Application summary, which is prose in plain words (ADR 0056).
_Avoid_: Sentiment report, personality, rating

## Web app

**Today**:
The Recruiter's landing page in the web app (`/today`): Approvals waiting, Takeovers waiting on this Recruiter, Interviews today, and recently Qualified Applications.
_Avoid_: Home page, Inbox

**Overview**:
The Operator's landing page in the web app (`/overview`): open Applications by screening step and closed ones by Outcome, Escalations, Takeovers, Qualified, waiting Applications and cost per conversation, with Economics and Comparison underneath. It is not the Results dashboard, the Client's own view of its Applications by Outcome.
_Avoid_: Control panel

**Demo stage**:
The Operator-only page (`/stage`) that shows a Candidate's WhatsApp chat beside the live Application, for demonstrations and recordings. It is reached by URL, never from the navigation, and an Operator under "Preview as Recruiter" does not get it. It is not the Application's screening step, which the UI labels "Stage".
_Avoid_: Demo page, Demo mode

**Agent activity**:
The Operator's timeline on the **Demo stage**, under the record: one block per Candidate turn, from stored records only (the message in, Jev's reading, each agent run and tool call with what code decided, code's events, the reply out). Hidden when the stage is viewed as a Recruiter (ADR 0075).
_Avoid_: activity log

**Presenter console**:
The hidden panel on the Demo stage, opened by a keyboard shortcut: persona playback, New take, Prepare take, Clear (client-side: empties the Takes list and the phone, ADR 0075), the list of Takes, the Mexico City time against the 09:00–22:00 recording window (quiet hours are 22:00–09:00), and, only when `/api/sim/clock` answers, the clock controls.
_Avoid_: Admin panel, Debug panel

**Take**:
One conversation played on the Demo stage: a fresh phone number, a persona, and the one Candidate and Application it creates. A finished Take can be opened again with its history. It is not a Takeover.
_Avoid_: Demo run

## Relationships

- A **Client** has many **Locations**. Each **Location** has one **Service Area**.
- A **Candidate** has many **Applications**, but at most one open at a time.
- An **Application** belongs to every **Location** whose **Service Area** contains the Candidate's **Zone**.
- An **Application** ends in exactly one **Outcome**: **Knockout**, **Rejection**, **Abandoned** or **Withdrawn**.
- **Screening** either makes an **Application** **Qualified**, which keeps it open, or ends it with **Knockout**, **Abandoned** or **Withdrawn**.
- A **Qualified** **Application** goes through **Approval**. **Approved** lets the Candidate book an **Interview**; otherwise it ends in **Rejection**. It can still end in **Withdrawn** at any point. Only **Knockout** and **Abandoned** can **Reopen**: **Abandoned** on any message, **Knockout** only on a valid new answer to the failed criterion or an Overturned **Review**.
- A **Review** (Candidate disputes a **Knockout**) and an **Approval** (a Recruiter checks a **Qualified** Application) are different things.
- An imported **Candidate** goes from **Not contacted** to **Contacted**, and then either starts an **Application** by replying or becomes **Unresponsive**.
- A **Review** applies only to a **Knockout**, which stays closed while the Candidate disputes it. An **Escalation** can come from any open **Application**.
- A **Takeover** can happen at any stage of an open **Application**, and is how most **Escalations** are resolved.
- **Message signals** come from the Candidate's typed messages and Spoken turns. They give the Screening Agent a **Tone hint**, can raise an **Escalation**, and become **Communication notes** for the Recruiter. They never reach a **Knockout**, **Rejection** or **Approval**.
- At most one **Recruiter** holds a **Claim** on an **Application** at a time.
- A **Recruiter** starts at **Today** and an **Operator** at **Overview**.
- The **Demo stage** shows one **Take** at a time, and the **Presenter console** controls it.
- A **Take** starts one **Candidate** and one **Application** (ADR 0021).
