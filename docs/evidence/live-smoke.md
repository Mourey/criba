# Live smoke run

Real conversations against the running product: the production Recruiter profile on anthropic/claude-haiku-5.5 through OpenRouter, no stub model and no simulated clock. Candidates write through the web chat API; the record is read back through the Operator API. Checks are on the record (stage, events, answers, queue items, attachments), not on the agent's exact words.

| Fact | Value |
|---|---|
| Date (UTC) | 2026-10-12 13:25:36 |
| Commit | 5b2897a |
| Stack | http://localhost:18300 |
| Turn model | anthropic/claude-haiku-5.5 |
| Summary model | anthropic/claude-sonnet-5.5 |
| Note | re-run on main at 5b2897a |

## Result

**20 of 20 flows passed.**

| Flow | Result | Conversations | Checks passed | Cost (USD) | Time (s) |
|---|---|---|---|---|---|
| Qualified: an English-speaking Candidate completes Screening | pass | 1 | 15 of 15 | 0.0138 | 49.2 |
| Knockout, then a Review request (no licence) | pass | 1 | 11 of 11 | 0.0075 | 24.4 |
| CDMX Zone list: Cuajimalpa gets the city's Zones, one is tapped | pass | 1 | 13 of 13 | 0.0033 | 19.8 |
| Demo chat: 'Ciudad de Mexico en Tepito' gets the city's Zones with the first reply | pass | 1 | 14 of 14 | 0.0029 | 17.1 |
| Demo chat: 'I live in Texas!' gets the cities list; None of these is the Zone Knockout | pass | 1 | 18 of 18 | 0.0023 | 14.5 |
| After the cities list a second place that matches no Zone, typed, is the Zone Knockout | pass | 1 | 17 of 17 | 0.0032 | 20.8 |
| A place with no Zone gets the cities list; a city leads to its Zones, one is tapped | pass | 1 | 16 of 16 | 0.0030 | 20.7 |
| Approval, slot offer, booked Interview with .ics | pass | 1 | 16 of 16 | 0.0129 | 50.7 |
| Otro horario: three taps each get the booking link, and a slot still books | pass | 1 | 25 of 25 | 0.0141 | 60.0 |
| STOP: opt-out confirmed, no Application created | pass | 2 | 8 of 8 | 0.0007 | 5.1 |
| Language: an English opener gets English, one Spanish word does not switch it | pass | 1 | 6 of 6 | 0.0011 | 9.2 |
| A frustrated Candidate asks for a person: tone-adjusted reply and an Escalation | pass | 1 | 7 of 7 | 0.0016 | 10.0 |
| No app: delivering on their own is [] on the first answer | pass | 3 | 34 of 34 | 0.0152 | 100.3 |
| Next Sunday: both Sundays as buttons, the tapped one recorded | pass | 2 | 23 of 23 | 0.0117 | 83.0 |
| Unambiguous start dates are recorded with no extra question | pass | 6 | 67 of 67 | 0.0324 | 227.8 |
| Demo chat: an MX chat living in Chamberí moves to Spain; tomorrow is recorded | pass | 1 | 13 of 13 | 0.0070 | 47.9 |
| Language request: "me preguntas en inglés porfi" gives English reply, buttons and record | pass | 1 | 10 of 10 | 0.0038 | 24.8 |
| Demo chat: three unreadable messages hand the chat to the Recruiters | pass | 1 | 6 of 6 | 0.0023 | 27.9 |
| Spain chat: the Zone question says barrio, never colonia | pass | 1 | 3 of 3 | 0.0014 | 8.9 |
| MX chat: the Zone question keeps colonia | pass | 1 | 2 of 2 | 0.0013 | 10.2 |

## Latency

Seconds from the Candidate's message (or tap, or Approval) to the agent's first reply, over 187 replies that a model call produced: **p50 4.1 s, p95 6.1 s**, slowest 9.6 s. 133 of 187 within 5 s (the first-reply target), 187 of 187 within 10 s (the target for later replies). Latency does not decide pass or fail.

| Flow | Replies | p50 (s) | p95 (s) | Slowest (s) |
|---|---|---|---|---|
| Qualified: an English-speaking Candidate completes Screening | 11 | 3.8 | 5.8 | 5.8 |
| Knockout, then a Review request (no licence) | 5 | 5.1 | 6.4 | 6.4 |
| CDMX Zone list: Cuajimalpa gets the city's Zones, one is tapped | 4 | 6.3 | 7.1 | 7.1 |
| Demo chat: 'Ciudad de Mexico en Tepito' gets the city's Zones with the first reply | 4 | 4.4 | 6.1 | 6.1 |
| Demo chat: 'I live in Texas!' gets the cities list; None of these is the Zone Knockout | 3 | 4.8 | 6.6 | 6.6 |
| After the cities list a second place that matches no Zone, typed, is the Zone Knockout | 4 | 5.4 | 5.9 | 5.9 |
| A place with no Zone gets the cities list; a city leads to its Zones, one is tapped | 5 | 3.8 | 5.6 | 5.6 |
| Approval, slot offer, booked Interview with .ics | 12 | 4.6 | 5.3 | 5.4 |
| Otro horario: three taps each get the booking link, and a slot still books | 14 | 3.6 | 5.3 | 5.9 |
| STOP: opt-out confirmed, no Application created | 1 | 5.1 | 5.1 | 5.1 |
| Language: an English opener gets English, one Spanish word does not switch it | 2 | 3.8 | 5.4 | 5.4 |
| A frustrated Candidate asks for a person: tone-adjusted reply and an Escalation | 1 | 5.6 | 5.6 | 5.6 |
| No app: delivering on their own is [] on the first answer | 25 | 3.8 | 5.1 | 6.1 |
| Next Sunday: both Sundays as buttons, the tapped one recorded | 20 | 4.1 | 5.3 | 6.1 |
| Unambiguous start dates are recorded with no extra question | 54 | 3.8 | 5.9 | 6.1 |
| Demo chat: an MX chat living in Chamberí moves to Spain; tomorrow is recorded | 9 | 4.3 | 9.6 | 9.6 |
| Language request: "me preguntas en inglés porfi" gives English reply, buttons and record | 6 | 3.6 | 6.1 | 6.1 |
| Demo chat: three unreadable messages hand the chat to the Recruiters | 3 | 5.4 | 6.1 | 6.1 |
| Spain chat: the Zone question says barrio, never colonia | 2 | 3.6 | 5.3 | 5.3 |
| MX chat: the Zone question keeps colonia | 2 | 3.6 | 6.6 | 6.6 |

## Cost

From the usage ledger, per conversation: OpenRouter's own charge where a call reports one (the summary), otherwise the call's tokens at OpenRouter's list prices (Hermes reports tokens, not dollars). The OpenRouter activity page is the bill.

| Flow | Agent runs | Input tokens | Cached | Output tokens | Cost (USD) |
|---|---|---|---|---|---|
| Qualified: an English-speaking Candidate completes Screening | 11 | 225573 | 179991 | 4485 | 0.0138 |
| Knockout, then a Review request (no licence) | 5 | 115672 | 95300 | 2449 | 0.0075 |
| CDMX Zone list: Cuajimalpa gets the city's Zones, one is tapped | 4 | 78591 | 65387 | 2101 | 0.0033 |
| Demo chat: 'Ciudad de Mexico en Tepito' gets the city's Zones with the first reply | 4 | 70097 | 57167 | 1766 | 0.0029 |
| Demo chat: 'I live in Texas!' gets the cities list; None of these is the Zone Knockout | 3 | 58417 | 49439 | 1387 | 0.0023 |
| After the cities list a second place that matches no Zone, typed, is the Zone Knockout | 4 | 87177 | 71951 | 2151 | 0.0032 |
| A place with no Zone gets the cities list; a city leads to its Zones, one is tapped | 5 | 84982 | 72054 | 1802 | 0.0030 |
| Approval, slot offer, booked Interview with .ics | 12 | 237382 | 197045 | 5249 | 0.0129 |
| Otro horario: three taps each get the booking link, and a slot still books | 15 | 270182 | 228057 | 6456 | 0.0141 |
| STOP: opt-out confirmed, no Application created | 1 | 17194 | 13823 | 644 | 0.0007 |
| Language: an English opener gets English, one Spanish word does not switch it | 2 | 34453 | 27876 | 861 | 0.0011 |
| A frustrated Candidate asks for a person: tone-adjusted reply and an Escalation | 2 | 42582 | 35046 | 1152 | 0.0016 |
| No app: delivering on their own is [] on the first answer | 25 | 501796 | 414395 | 8941 | 0.0152 |
| Next Sunday: both Sundays as buttons, the tapped one recorded | 20 | 404256 | 341577 | 7409 | 0.0117 |
| Unambiguous start dates are recorded with no extra question | 54 | 1133422 | 950407 | 20694 | 0.0324 |
| Demo chat: an MX chat living in Chamberí moves to Spain; tomorrow is recorded | 9 | 198715 | 165273 | 4788 | 0.0070 |
| Language request: "me preguntas en inglés porfi" gives English reply, buttons and record | 6 | 114044 | 95153 | 2213 | 0.0038 |
| Demo chat: three unreadable messages hand the chat to the Recruiters | 3 | 62831 | 49480 | 1723 | 0.0023 |
| Spain chat: the Zone question says barrio, never colonia | 2 | 42304 | 35111 | 792 | 0.0014 |
| MX chat: the Zone question keeps colonia | 2 | 42396 | 35317 | 838 | 0.0013 |
| **Total** |  |  |  |  | **0.1417** |

Per conversation that called a model: 0.0071 USD on average over 20. Of the total, 0.0352 USD is OpenRouter's own charge.

`real-model spend: this run $0.1417, earlier runs $4.0528, total $4.1945 of the $4.40 cap (guard prices; the OpenRouter bill is the figure that counts)`

## Flows

### 1. Qualified: an English-speaking Candidate completes Screening: pass

- pass: conversation ran to its end (ended: completed)
- pass: stage qualified, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: reply language
- pass: summary prose written
- pass: summary prose complete (ends on a full stop)
- pass: summary written by anthropic/claude-sonnet-5.5
- pass: summary prose is plain words (no stage, field or flag codes, no ids)
- pass: summary prose carries no communication notes (ADR 0056)
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R4MHYAQA7YPNQFGQFEDGVR: stage qualified, status open, events application_started, answer_recorded x8, stage_changed x8, answer_ambiguous, screening_finished, approval_requested, summary_written
- Summary: Daniel Foster's application has passed screening and is waiting for your approval. He lives near Chamberí in Madrid, holds a driving licence (about six years), and has roughly 2.5 years of experience on Glovo and Uber Eats. He said he is free full time, Monday to Saturday, preferring afternoons but fine with evenings, and confirmed a start on Monday 19 October 2026. I found no contradictions in his answers. The screening took under a minute, so you may want to confirm the licence and his work eligibility directly.

- reply to opening: 5.3 s
- reply to answer:full_name: 5.4 s
- reply to answer:zone: 5.3 s
- reply to answer:driver_license: 3.8 s
- reply to answer:availability: 3.6 s
- reply to answer:preferred_schedule: 3.6 s
- reply to answer:experience_years: 3.3 s
- reply to answer:experience_platforms: 5.6 s
- reply to answer:start_date: 5.8 s
- reply to answer:start_date: 2.6 s
- reply to answer:confirm: 3.8 s

<details><summary>Transcript</summary>

    Candidate: Hi, I saw your ad for delivery riders in Madrid. I'd like to apply.
    Agent: Hi! I'm Grupo Sazón's virtual (AI) assistant 🛵 I'll help you apply as a delivery rider in just a few minutes. Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad. What's your full name?
    Candidate: My name is Daniel Foster
    Agent: Thanks, Daniel! Which neighbourhood and city do you live in?
    Candidate: I live in Madrid, near Chamberí
    Agent: Great, Chamberí in Madrid it is. Do you have a valid carnet de conducir? [buttons: Yes, I have one | No]
    Candidate: Yes, I've had my driving licence for about six years
    Agent: Perfect, thanks for confirming. Which days can you work: full time, part time, or weekends? [buttons: Full-time | Part-time | Weekends]
    Candidate: I'm free full time, Monday to Saturday
    Agent: Got it, full time works. What shift do you prefer: morning, afternoon, evening, or flexible? [list: Morning | Afternoon | Evening | Flexible]
    Candidate: Mostly afternoons, evenings are fine too
    Agent: Noted, afternoons and evenings. How many years of delivery experience do you have? If none, just say 0.
    Candidate: About two and a half years
    Agent: Thanks, two and a half years is great. Which delivery platforms have you worked with? For example Glovo, Uber Eats, Just Eat, Rappi, Didi Food, or another one. [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Other | No app]
    Candidate: Mainly Glovo, and some Uber Eats
    Agent: Glovo and Uber Eats, thanks! When could you start? You can give a date or a day like "el lunes".
    Candidate: I could start next Monday
    Agent: Which Monday works for you: 19 October or 26 October? [buttons: Monday 19 Oct | Monday 26 Oct]
    Candidate (tapped): Monday 19 Oct
    Agent: Here's your application summary, Daniel. Please check it: - Name: Daniel Foster - Zone: Chamberí, Madrid - Driving licence: yes - Availability: full time - Preferred shift: afternoons and evenings - Experience: 2.5 years, on Glovo and Uber Eats - Start date: Monday 19 October ...
    Candidate: Yes
    Agent: All set, Daniel! A Grupo Sazón recruiter will review your application and write to you here.

</details>

### 2. Knockout, then a Review request (no licence): pass

- pass: conversation ran to its end (ended: knockout)
- pass: stage driver_license, closed
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Knockout message in es-MX with the requirements link
- pass: Criba never named
- pass: one Review queue item open
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R4P2176ZFA3JJ9SFGQME9C: stage driver_license, status closed, events application_started, answer_recorded x3, stage_changed x2, knockout, screening_finished, summary_written, review_requested
- Review item qi_01M4R4PRDTPSA4C708MMJY77HV (status open)
- Requirements link in the Knockout message: https://empleo.gruposazon.example/requisitos.

- reply to opening: 4.3 s
- reply to answer:full_name: 6.4 s
- reply to answer:zone: 5.1 s
- reply to answer:driver_license: 5.3 s
- reply to interrupt:after_knockout: 3.3 s

<details><summary>Transcript</summary>

    Candidate: Hola, vi el anuncio para repartidores y quiero trabajar con ustedes
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Me llamo Luis Hernández
    Agent: Gracias, Luis. ¿En qué colonia y ciudad vives?
    Candidate: Vivo en Puebla, por el Centro
    Agent: Perfecto, Luis. ¿Tienes licencia de manejo vigente? [buttons: Sí, tengo licencia | No]
    Candidate: No, todavía no tengo licencia de manejo
    Agent: Gracias, Luis. Por ahora necesitamos licencia de manejo vigente: es un requisito legal y del seguro para repartir. Aquí están los requisitos: https://empleo.gruposazon.example/requisitos. Cuando la tengas, escríbenos y seguimos donde lo dejamos. Si crees que hay un error, resp...
    Candidate: Creo que hay un error, quisiera pedir una revisión
    Agent: Listo, Luis. Pedí la revisión y una persona del equipo de Grupo Sazón la verá y te escribirá por aquí. ¡Gracias por tu paciencia!

</details>

### 3. CDMX Zone list: Cuajimalpa gets the city's Zones, one is tapped: pass

- pass: conversation ran to its end (ended: stopped)
- pass: stage driver_license, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: a list of Zones was offered
- pass: a Zone was tapped
- pass: the list has several CDMX Zones, Polanco among them
- pass: the tapped Zone is the recorded Zone
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R4PSX4VCMKRMZD5G7GPF8Y: stage driver_license, status open, events application_started, answer_recorded x2, stage_changed x2, answer_ambiguous
- Zone list (9): Centro, Condesa, Coyoacán, Del Valle, Iztapalapa, Milpa Alta, Narvarte, Polanco, Roma
- Recorded Zone: Polanco (Ciudad de México)

- reply to opening: 3.8 s
- reply to answer:full_name: 6.3 s
- reply to answer:zone: 7.1 s
- reply to answer:zone: 2.6 s

<details><summary>Transcript</summary>

    Candidate: Hola, me interesa el trabajo de repartidor
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Valeria Núñez
    Agent: Gracias, Valeria. ¿En qué colonia y ciudad vives?
    Candidate: Ciudad de México, Cuajimalpa
    Agent: Gracias. Cuajimalpa no aparece en nuestra lista, así que elige de la lista la colonia más cercana a donde vives. [list: Centro | Condesa | Coyoacán | Del Valle | Iztapalapa | Milpa Alta | Narvarte | Polanco | Roma]
    Candidate (tapped): Polanco
    Agent: Perfecto, ya tengo tu zona. ¿Tienes licencia de manejo vigente? [buttons: Sí, tengo licencia | No]

</details>

### 4. Demo chat: 'Ciudad de Mexico en Tepito' gets the city's Zones with the first reply: pass

- pass: conversation ran to its end (ended: stopped)
- pass: stage driver_license, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: a list of Zones was offered
- pass: the first reply to the Zone answer carries the list
- pass: a Zone was tapped
- pass: the list has several CDMX Zones, Polanco among them
- pass: the tapped Zone is the recorded Zone
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R4QD9FSZQ252VBHFHNXCG7: stage driver_license, status open, events application_started, answer_recorded x2, stage_changed x2, answer_ambiguous
- Zone list (9): Centro, Condesa, Coyoacán, Del Valle, Iztapalapa, Milpa Alta, Narvarte, Polanco, Roma
- Recorded Zone: Centro (Ciudad de México)

- reply to opening: 4.4 s
- reply to answer:full_name: 4.1 s
- reply to answer:zone: 6.1 s
- reply to answer:zone: 2.5 s

<details><summary>Transcript</summary>

    Candidate: Hola!
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Fernando Fernández
    Agent: Gracias, Fernando. ¿En qué colonia y ciudad vives?
    Candidate: Ciudad de Mexico en Tepito
    Agent: Gracias. Para tu zona, elige de la lista la que más te quede cerca de Tepito. [list: Centro | Condesa | Coyoacán | Del Valle | Iztapalapa | Milpa Alta | Narvarte | Polanco | Roma]
    Candidate (tapped): Centro
    Agent: Perfecto, ya tengo tu zona. ¿Tienes licencia de manejo vigente? [buttons: Sí, tengo licencia | No]

</details>

### 5. Demo chat: 'I live in Texas!' gets the cities list; None of these is the Zone Knockout: pass

- pass: conversation ran to its end (ended: knockout)
- pass: stage zone, closed
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Knockout message in en with the requirements link
- pass: Criba never named
- pass: the first agent message links the privacy notice
- pass: the cities were offered once, as a list ending with none of these
- pass: the first reply to the Zone answer is the cities question
- pass: the list names every city with a Service Area and the question says them
- pass: the agent named no other area
- pass: None of these was tapped
- pass: the Zone is the failed check, no Zone stored, the Candidate's words kept
- pass: the Knockout message was the last reply and asked nothing
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R4QY0BNCKWCX9ZFK9PW829: stage zone, status closed, events application_started, answer_recorded, stage_changed, answer_ambiguous, knockout, screening_finished
- Cities question: We hire delivery drivers in these cities: Ciudad de México, Guadalajara, Monterrey, Puebla. Can you work in one of them?

- reply to opening: 6.6 s
- reply to answer:zone: 4.8 s
- reply to answer:zone: 3.1 s

<details><summary>Transcript</summary>

    Candidate: Hi, I'm Cody Walker, I saw your delivery job ad
    Agent: Hi Cody, nice to meet you! I'm Grupo Sazón's virtual (AI) assistant 🛵 I'll help you apply as a delivery driver in a few minutes. Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad Which neighbourhood and city do you live in?
    Candidate: I live in Texas!
    Agent: We hire delivery drivers in these cities: Ciudad de México, Guadalajara, Monterrey, Puebla. Can you work in one of them? [list: Ciudad de México | Guadalajara | Monterrey | Puebla | None of these]
    Candidate (tapped): None of these
    Agent: Thank you, Cody. Right now none of our locations deliver in your area, and drivers need to work inside a location's delivery area. Here are the requirements: https://empleo.gruposazon.example/requisitos. If you move or we open nearby, write to us and we'll pick up where we lef...

</details>

### 6. After the cities list a second place that matches no Zone, typed, is the Zone Knockout: pass

- pass: conversation ran to its end (ended: knockout)
- pass: stage zone, closed
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Knockout message in en with the requirements link
- pass: Criba never named
- pass: the first agent message links the privacy notice
- pass: the cities were offered once, as a list ending with none of these
- pass: the first reply to the Zone answer is the cities question
- pass: the list names every city with a Service Area and the question says them
- pass: the agent named no other area
- pass: nothing was tapped, the second place was typed
- pass: the Zone is the failed check, no Zone stored, the second place kept
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R4RC5Y56DDRTQ5TJ0JADM3: stage zone, status closed, events application_started, answer_recorded, stage_changed, answer_ambiguous, knockout, screening_finished
- Cities question: We hire delivery drivers in these cities: Ciudad de México, Guadalajara, Monterrey, Puebla. Can you work in one of them?

- reply to opening: 5.4 s
- reply to answer:full_name: 5.1 s
- reply to answer:zone: 4.3 s
- reply to answer:zone: 5.9 s

<details><summary>Transcript</summary>

    Candidate: Hi, I'm Cody Walker, I saw your delivery job ad
    Agent: Hi! I'm Grupo Sazón's virtual (AI) assistant, and I'll help you apply for a delivery driver role in a few minutes. Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad. What's your full name?
    Candidate: Cody Walker
    Agent: Thanks, Cody! Which neighbourhood and city do you live in?
    Candidate: I live in Texas!
    Agent: We hire delivery drivers in these cities: Ciudad de México, Guadalajara, Monterrey, Puebla. Can you work in one of them? [list: Ciudad de México | Guadalajara | Monterrey | Puebla | None of these]
    Candidate: I live in the united states
    Agent: Thank you, Cody. Right now none of our locations deliver in your area, and drivers need to work inside a location's delivery area. Here are the requirements: https://empleo.gruposazon.example/requisitos. If you move or we open nearby, write to us and we'll pick up where we lef...

</details>

### 7. A place with no Zone gets the cities list; a city leads to its Zones, one is tapped: pass

- pass: conversation ran to its end (ended: stopped)
- pass: stage driver_license, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: the first agent message links the privacy notice
- pass: the cities were offered once, as a list ending with none of these
- pass: the first reply to the Zone answer is the cities question
- pass: the list names every city with a Service Area and the question says them
- pass: the agent named no other area
- pass: a city was tapped, then one of its Zones
- pass: the tapped Zone is the recorded Zone
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R4S0H49W29BB8MFS062F6R: stage driver_license, status open, events application_started, answer_recorded x2, stage_changed x2, answer_ambiguous x2
- Cities question: We hire delivery drivers in these cities: Ciudad de México, Guadalajara, Monterrey, Puebla. Can you work in one of them?
- Recorded Zone: Cumbres (Monterrey)

- reply to opening: 3.8 s
- reply to answer:full_name: 5.6 s
- reply to answer:zone: 4.6 s
- reply to answer:zone: 2.8 s
- reply to answer:zone: 3.8 s

<details><summary>Transcript</summary>

    Candidate: Hello, I'd like to apply as a delivery driver
    Agent: Hi! I'm Grupo Sazón's virtual (AI) assistant 🛵 I'll help you apply as a delivery driver in a few minutes. Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad. What's your full name?
    Candidate: Dana Whitfield
    Agent: Thanks, Dana! Which neighborhood and city do you live in?
    Candidate: I live in Dallas
    Agent: We hire delivery drivers in these cities: Ciudad de México, Guadalajara, Monterrey, Puebla. Can you work in one of them? [list: Ciudad de México | Guadalajara | Monterrey | Puebla | None of these]
    Candidate (tapped): Monterrey
    Agent: Thanks, Dana. Which neighborhood in Monterrey do you live in? Please pick one from the list. [list: Apodaca | Centro | Cumbres | Guadalupe | San Pedro]
    Candidate (tapped): Cumbres
    Agent: Thanks, Dana. Do you have a valid driver's license (licencia de manejo)? [buttons: Yes, I have one | No]

</details>

### 8. Approval, slot offer, booked Interview with .ics: pass

- pass: conversation ran to its end (ended: completed)
- pass: stage qualified, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: one Approval queue item open
- pass: Approval accepted by the Operator API
- pass: slot offer arrives with buttons
- pass: booking confirmation carries the .ics
- pass: the .ics downloads as a calendar event
- pass: Interview booked on the record
- pass: approved, slots offered and booked, in that order
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R4SMQASPK95R3W9PBMZ184: stage qualified, status open, events application_started, answer_recorded x8, stage_changed x8, screening_finished, approval_requested
- Slots offered: mar 13 · 10:00 | mar 13 · 12:00 | mar 13 · 16:00
- Interview: {'starts_at': '2026-10-13T08:00:00Z', 'ends_at': '2026-10-13T08:30:00Z', 'local_label': 'mar 13 · 10:00', 'location_name': 'Sazón Madrid Centro', 'address': '(synthetic) Calle Ejemplo 4, Centro, Madrid'}

- reply to opening: 3.3 s
- reply to answer:full_name: 4.6 s
- reply to answer:zone: 5.3 s
- reply to answer:driver_license: 4.6 s
- reply to answer:availability: 4.1 s
- reply to answer:preferred_schedule: 3.3 s
- reply to answer:experience_years: 3.6 s
- reply to answer:experience_platforms: 5.4 s
- reply to answer:start_date: 4.9 s
- reply to answer:confirm: 3.8 s
- Approval to slot offer: 5.1 s
- slot tap to confirmation: 2.8 s

<details><summary>Transcript</summary>

    Candidate: Buenas, me interesa el puesto de repartidor que anunciáis
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestra política: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Marta Ruiz Pérez
    Agent: Gracias, Marta. ¿En qué barrio y ciudad vives?
    Candidate: Vivo en Madrid, en Retiro
    Agent: ¡Perfecto, Marta! ¿Tienes carnet de conducir vigente? [buttons: Sí, tengo carnet | No]
    Candidate: Sí, tengo el carnet B desde hace años
    Agent: ¡Genial, Marta! ¿Qué disponibilidad tienes: jornada completa, media jornada o fines de semana? [buttons: Jornada completa | Media jornada | Fines de semana]
    Candidate: Puedo trabajar a jornada completa
    Agent: Anotado, Marta. ¿Qué horario prefieres: mañana, tarde, noche o flexible? [list: Mañana | Tarde | Noche | Flexible]
    Candidate: Por las tardes me viene mejor
    Agent: ¿Cuántos años de experiencia tienes como repartidor?
    Candidate: Llevo unos tres años
    Agent: ¿Con qué plataformas has trabajado: Glovo, Uber Eats, Just Eat, Rappi, Didi Food, otra o ninguna? [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Otra | Ninguna app]
    Candidate: He trabajado con Glovo y Just Eat
    Agent: Gracias, Marta. ¿Cuándo podrías empezar?
    Candidate: Podría empezar el lunes que viene
    Agent: Perfecto, Marta. Este es tu resumen: Nombre: Marta Ruiz Pérez Zona: Retiro, Madrid Carnet de conducir: sí Disponibilidad: jornada completa Horario preferido: tardes Experiencia: 3 años (Glovo y Just Eat) Inicio: lunes 19 de octubre de 2026 ¿Está todo correcto?
    Candidate: Sí
    Agent: ¡Listo, Marta! Un reclutador de Grupo Sazón revisará tu solicitud y te escribirá por aquí.
    Agent: ¡Enhorabuena, Marta! Elige una de estas entrevistas para el martes 13 de octubre. [list: mar 13 · 10:00 | mar 13 · 12:00 | mar 13 · 16:00 | Otro horario]
    Candidate (tapped): mar 13 · 10:00
    Agent: Tu entrevista está reservada, Marta: martes 13 de octubre a las 10:00 en Sazón Madrid Centro, Calle Ejemplo 4, Centro, Madrid. Te llegará una invitación con los detalles. [attachment ics entrevista.ics]

</details>

### 9. Otro horario: three taps each get the booking link, and a slot still books: pass

- pass: conversation ran to its end (ended: completed)
- pass: stage qualified, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: one Approval queue item open
- pass: Approval accepted by the Operator API
- pass: slot offer arrives with buttons
- pass: tap 1: the buttons include the other-time option
- pass: tap 1: the reply carries the booking link
- pass: tap 1: the booking page accepts the link and lists slots
- pass: tap 1: the slot buttons are still offered
- pass: tap 2: the buttons include the other-time option
- pass: tap 2: the reply carries the booking link
- pass: tap 2: the booking page accepts the link and lists slots
- pass: tap 2: the slot buttons are still offered
- pass: tap 3: the buttons include the other-time option
- pass: tap 3: the reply carries the booking link
- pass: tap 3: the booking page accepts the link and lists slots
- pass: tap 3: the slot buttons are still offered
- pass: a slot tapped after the link is booked
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R4V6820R6Y36SST8E7EX2X: stage qualified, status open, events application_started, answer_recorded x8, stage_changed x8, screening_finished, approval_requested
- Slots offered: mar 13 · 10:00 | mar 13 · 12:00 | mar 13 · 16:00

- reply to opening: 3.6 s
- reply to answer:full_name: 4.1 s
- reply to answer:zone: 5.9 s
- reply to answer:driver_license: 3.6 s
- reply to answer:availability: 3.6 s
- reply to answer:preferred_schedule: 3.3 s
- reply to answer:experience_years: 3.3 s
- reply to answer:experience_platforms: 4.6 s
- reply to answer:start_date: 5.1 s
- reply to answer:confirm: 3.6 s
- Approval to slot offer: 5.3 s
- other-time tap 1 to reply: 3.1 s
- other-time tap 2 to reply: 3.6 s
- other-time tap 3 to reply: 4.1 s

<details><summary>Transcript</summary>

    Candidate: Buenas, me interesa el puesto de repartidor que anunciáis
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestra política: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Marta Ruiz Pérez
    Agent: Gracias, Marta. ¿En qué barrio y ciudad vives?
    Candidate: Vivo en Madrid, en Retiro
    Agent: ¡Genial, Marta! ¿Tienes carnet de conducir vigente? [buttons: Sí, tengo carnet | No]
    Candidate: Sí, tengo el carnet B desde hace años
    Agent: ¿Qué disponibilidad tienes: jornada completa, parcial o fines de semana? [buttons: Jornada completa | Media jornada | Fines de semana]
    Candidate: Puedo trabajar a jornada completa
    Agent: ¿En qué franja horaria prefieres trabajar: mañana, tarde, noche o es flexible? [list: Mañana | Tarde | Noche | Flexible]
    Candidate: Por las tardes me viene mejor
    Agent: ¿Cuántos años de experiencia tienes como repartidor?
    Candidate: Llevo unos tres años
    Agent: ¿Con qué apps de reparto has trabajado? Por ejemplo Glovo, Uber Eats, Just Eat, Rappi, DiDi Food u otra. [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Otra | Ninguna app]
    Candidate: He trabajado con Glovo y Just Eat
    Agent: Perfecto, Marta, tengo Glovo y Just Eat anotados. ¿Desde qué día podrías empezar?
    Candidate: Podría empezar el lunes que viene
    Agent: Marta, anoto esto: Marta Ruiz Pérez, barrio Retiro en Madrid, carnet B vigente, jornada completa por las tardes, 3 años con Glovo y Just Eat, y empezar el lunes 19 de octubre. ¿Es correcto?
    Candidate: Sí
    Agent: ¡Listo, Marta! Un reclutador de Grupo Sazón revisará tu solicitud y te escribirá por aquí.
    Agent: ¡Enhorabuena, Marta! Puedes elegir una de estas entrevistas, o pedirme otro horario. [list: mar 13 · 10:00 | mar 13 · 12:00 | mar 13 · 16:00 | Otro horario]
    Candidate (tapped): other time
    Agent: Aquí tienes los horarios libres para elegir el que mejor te venga: http://localhost:18300/book/eyJhcHAiOiJhcHBfMDFNNFI0VjY4MjBSNlkzNlNTVDhFN0VYMlgiLCJleHAiOjE3OTI0MTY2MDB9.qhBeut8EeauLW4Tjh05Y9a_vR1iUSmSATwfj1wh570w [list: mar 13 · 10:00 | mar 13 · 12:00 | mar 13 · 16:00 | Otro horario]
    Candidate (tapped): other time
    Agent: Puedes elegir tu horario en este enlace: http://localhost:18300/book/eyJhcHAiOiJhcHBfMDFNNFI0VjY4MjBSNlkzNlNTVDhFN0VYMlgiLCJleHAiOjE3OTI0MTY2MDN9.kYNVdDwZ41Jkh3fXQcxcIdRExGqo5CbnRI8RxrkdF-A Cuando reserves, te confirmo los detalles por aquí. [list: mar 13 · 10:00 | mar 13 · 12:00 | mar 13 · 16:00 | Otro horario]
    Candidate (tapped): other time
    Agent: Puedes elegir tu horario en este enlace: http://localhost:18300/book/eyJhcHAiOiJhcHBfMDFNNFI0VjY4MjBSNlkzNlNTVDhFN0VYMlgiLCJleHAiOjE3OTI0MTY2MDZ9.44lz31Gg4wkcjPshV1HdnEuTQFNiNw9gF7qQgY2dq3o Cuando reserves, te confirmo los detalles por aquí. [list: mar 13 · 10:00 | mar 13 · 12:00 | mar 13 · 16:00 | Otro horario]
    Candidate (tapped): mar 13 · 10:00
    Agent: ¡Entrevista reservada, Marta! Es el martes 13 de octubre a las 10:00 en Sazón Madrid Retiro, Calle Ejemplo 3, Retiro, Madrid. Te he enviado una invitación al calendario. [attachment ics entrevista.ics]

</details>

### 10. STOP: opt-out confirmed, no Application created: pass

- pass: the agent greeted and disclosed it is an AI
- pass: STOP confirmed once, in the fixed copy
- pass: Candidate marked Opted out
- pass: the open Application stays as it is (not withdrawn, no nudge)
- pass: STOP created no second Application
- pass: a first-message STOP is confirmed
- pass: a first-message STOP created no Application
- pass: turns served by anthropic/claude-haiku-5.5

- reply to the opening: 5.1 s
- reply to STOP: 0.0 s (written by code, no model)
- reply to a first-message STOP: 0.0 s (written by code, no model)

<details><summary>Transcript</summary>

    Candidate: Hola, vi vuestro anuncio de repartidor
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestra política: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: STOP
    Agent: Hecho, no te enviaremos más mensajes. Si cambias de idea, escríbenos cuando quieras.
    Candidate (new conversation): STOP
    Agent: Listo, ya no te enviaremos más mensajes. Si cambias de opinión, escríbenos cuando quieras.

</details>

### 11. Language: an English opener gets English, one Spanish word does not switch it: pass

- pass: the reply to an English opener is in English
- pass: the Application language is en
- pass: the Jev router served the call (a jev row in the ledger)
- pass: one Spanish word does not switch the reply language
- pass: the Application language is still en
- pass: turns served by anthropic/claude-haiku-5.5

- reply to the English opener: 3.8 s
- reply to one Spanish word: 5.4 s

<details><summary>Transcript</summary>

    Candidate: Hello, I'm looking for a delivery job
    Agent: Hi! I'm Grupo Sazón's virtual (AI) assistant 🛵 I'll help you apply as a delivery driver in a few minutes. Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad. What's your full name?
    Candidate: Mañana
    Agent: Thanks! Before we start the questions, could you tell me your full name (first and last name)?

</details>

### 12. A frustrated Candidate asks for a person: tone-adjusted reply and an Escalation: pass

- pass: the reply answers the request (the team, a person, an apology)
- pass: an Escalation is open for a person (reason wants_human)
- pass: a tone hint is on the record with codes only
- pass: the message is flagged for the Recruiter, as a flag with no score
- pass: the communication notes say a person was asked for
- pass: nothing about the Candidate was decided (still open, no Knockout or Rejection)
- pass: turns served by anthropic/claude-haiku-5.5

- Note: Escribe en español de México: funcionan las preguntas cortas y directas.
- Note: Pidió hablar con una persona al inicio del chat (07:30).
- Note: Mostró impaciencia al inicio del chat (07:30): funcionan las respuestas cortas y directas.

- reply to a frustrated request for a person: 5.6 s

<details><summary>Transcript</summary>

    Candidate: Hola, busco chamba de repartidor
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: ya te dije que cualquier dia!! quiero hablar con una persona
    Agent: Entendido, ya avisé al equipo de Grupo Sazón para que te contacte por aquí. Mientras tanto, ¿me dices tu nombre y apellido?

</details>

### 13. No app: delivering on their own is [] on the first answer: pass

- pass: conversation ran to its end (ended: stopped)
- pass: stage start_date, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: es-MX: no app is recorded as []
- pass: es-MX: no miss on the platforms
- pass: es-MX: the platforms question offered the no-app row
- pass: conversation ran to its end (ended: stopped)
- pass: stage start_date, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: es-ES: no app is recorded as []
- pass: es-ES: no miss on the platforms
- pass: es-ES: the platforms question offered the no-app row
- pass: conversation ran to its end (ended: stopped)
- pass: stage start_date, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: en: no app is recorded as []
- pass: en: no miss on the platforms
- pass: en: the platforms question offered the no-app row
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R4XRPS3PBNQJ7EZW9E9TW8: stage start_date, status open, events application_started, answer_recorded x7, stage_changed x6
- Application app_01M4R4YQM546S3969H997ENWDG: stage start_date, status open, events application_started, answer_recorded x7, stage_changed x6
- Application app_01M4R4ZNHDN8W06Z48FM9WGWA3: stage start_date, status open, events application_started, answer_recorded x7, stage_changed x6

- reply to opening: 4.0 s
- reply to answer:full_name: 6.1 s
- reply to answer:zone: 4.8 s
- reply to answer:driver_license: 3.3 s
- reply to answer:availability: 3.3 s
- reply to answer:preferred_schedule: 3.5 s
- reply to answer:experience_years: 3.3 s
- reply to answer:experience_platforms: 3.3 s
- reply to opening: 3.8 s
- reply to answer:full_name: 4.3 s
- reply to answer:zone: 4.3 s
- reply to answer:driver_license: 3.3 s
- reply to answer:availability: 3.3 s
- reply to answer:preferred_schedule: 4.1 s
- reply to answer:experience_years: 3.3 s
- reply to answer:experience_platforms: 4.1 s
- reply to opening: 3.8 s
- reply to answer:full_name: 5.1 s
- reply to answer:zone: 4.9 s
- reply to answer:zone: 4.1 s
- reply to answer:driver_license: 4.4 s
- reply to answer:availability: 3.6 s
- reply to answer:preferred_schedule: 3.5 s
- reply to answer:experience_years: 4.8 s
- reply to answer:experience_platforms: 3.8 s

<details><summary>Transcript</summary>

    Candidate: Hola, quiero trabajar de repartidor
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Ramiro Rodríguez
    Agent: Gracias, Ramiro. ¿En qué colonia y ciudad vives?
    Candidate: Vivo en el Centro de Puebla
    Agent: ¿Tienes licencia de manejo vigente? [buttons: Sí, tengo licencia | No]
    Candidate: Sí, tengo licencia
    Agent: ¿Qué disponibilidad tienes: tiempo completo, medio tiempo, o fines de semana? [buttons: Tiempo completo | Medio tiempo | Fines de semana]
    Candidate: Medio tiempo
    Agent: ¿En qué horario prefieres trabajar: mañana, tarde, noche o flexible? [list: Mañana | Tarde | Noche | Flexible]
    Candidate: En la tarde
    Agent: ¿Cuántos años de experiencia tienes como repartidor? Si nunca lo has hecho, puedes decir 0.
    Candidate: tengo 10 años repartiendo como independiente
    Agent: ¿Con qué plataformas has repartido: Glovo, Uber Eats, Just Eat, Rappi, DiDi Food, otra, o por tu cuenta? [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Otra | Ninguna app]
    Candidate: Por mi cuenta
    Agent: ¿Cuándo podrías empezar? Dime un día o una fecha.
    Candidate: Hola, busco trabajo de repartidora
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestra política: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Lucía Romero
    Agent: Gracias, Lucía. ¿En qué barrio y ciudad vives?
    Candidate: En Madrid, en Chamberí
    Agent: Perfecto, Chamberí. ¿Tienes carnet de conducir vigente? [buttons: Sí, tengo carnet | No]
    Candidate: Sí, tengo carnet
    Agent: ¿Qué disponibilidad tienes: jornada completa, parcial o fines de semana? [buttons: Jornada completa | Media jornada | Fines de semana]
    Candidate: Media jornada
    Agent: ¿Qué horario prefieres: mañana, tarde, noche o flexible? [list: Mañana | Tarde | Noche | Flexible]
    Candidate: Por las tardes
    Agent: ¿Cuántos años de experiencia tienes como repartidora?
    Candidate: Llevo cuatro años repartiendo para restaurantes
    Agent: ¿Con qué plataformas has repartido: Glovo, Uber Eats, Just Eat, Rappi, DiDi Food u otra? [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Otra | Ninguna app]
    Candidate: Ninguna, siempre por libre
    Agent: ¿Desde qué fecha podrías empezar?
    Candidate: Hi, I'd like to apply as a delivery rider
    Agent: Hi! I'm Grupo Sazón's virtual (AI) assistant 🛵 I'll help you apply as a delivery rider in a few minutes. Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad. What's your full name?
    Candidate: Sam Carter
    Agent: Thanks, Sam! Which neighbourhood and city do you live in?
    Candidate: Madrid, in Chamberí
    Agent: Got it, Chamberí in Madrid. Do you have a driving licence (carnet de conducir)? Is it valid, expired, suspended, or still pending? [buttons: Yes, I have one | No]
    Candidate: Madrid, in Chamberí
    Agent: Thanks, Sam. Could you tell me the status of your driving licence (carnet de conducir): valid, expired, suspended, or pending? [buttons: Yes, I have one | No]
    Candidate: Yes, I have one
    Agent: Thanks, Sam. What availability do you have: full time, part time, or weekends? [buttons: Full-time | Part-time | Weekends]
    Candidate: Part-time
    Agent: Thanks, Sam. What time of day do you prefer: morning, afternoon, evening, or flexible? [list: Morning | Afternoon | Evening | Flexible]
    Candidate: Afternoons
    Agent: Got it, afternoons. How many years of delivery experience do you have? If none, just say 0.
    Candidate: About five years delivering for a local pizzeria
    Agent: Five years is great, Sam. Which delivery apps have you worked with: Glovo, Uber Eats, Just Eat, Rappi, DiDi Food, or another one? If you worked on your own, just say so. [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Other | No app]
    Candidate: None, I never used an app
    Agent: Thanks, Sam. When could you start? You can give me a specific date or a day of the week.

</details>

### 14. Next Sunday: both Sundays as buttons, the tapped one recorded: pass

- pass: conversation ran to its end (ended: stopped)
- pass: stage confirm, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: proximo_domingo_mx: no miss on the start date
- pass: proximo_domingo_mx: both dates offered as buttons
- pass: proximo_domingo_mx: the tapped date is recorded
- pass: conversation ran to its end (ended: stopped)
- pass: stage confirm, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: next_sunday_en: no miss on the start date
- pass: next_sunday_en: both dates offered as buttons
- pass: next_sunday_en: the tapped date is recorded
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R50TMGSRXQT31K1Q1TWRCT: stage confirm, status open, events application_started, answer_recorded x8, stage_changed x7, answer_ambiguous
- proximo_domingo_mx (2026-10-12): recorded 2026-10-25
- Application app_01M4R5225AMXECBHXJMD34FKEW: stage confirm, status open, events application_started, answer_recorded x8, stage_changed x7, answer_ambiguous
- next_sunday_en (2026-10-12): recorded 2026-10-18

- reply to opening: 4.3 s
- reply to answer:full_name: 5.3 s
- reply to answer:zone: 4.8 s
- reply to answer:driver_license: 3.3 s
- reply to answer:availability: 3.3 s
- reply to answer:preferred_schedule: 3.1 s
- reply to answer:experience_years: 3.6 s
- reply to answer:experience_platforms: 4.6 s
- reply to answer:start_date: 5.1 s
- reply to answer:start_date: 3.1 s
- reply to opening: 3.6 s
- reply to answer:full_name: 6.1 s
- reply to answer:zone: 4.8 s
- reply to answer:driver_license: 4.1 s
- reply to answer:availability: 3.6 s
- reply to answer:preferred_schedule: 4.1 s
- reply to answer:experience_years: 3.6 s
- reply to answer:experience_platforms: 4.8 s
- reply to answer:start_date: 5.1 s
- reply to answer:start_date: 2.8 s

<details><summary>Transcript</summary>

    Candidate: Hola, quiero trabajar de repartidor
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Ramiro Rodríguez
    Agent: Gracias, Ramiro. ¿En qué colonia y ciudad vives?
    Candidate: Vivo en el Centro de Puebla
    Agent: ¡Perfecto, Ramiro! ¿Tienes licencia de manejo vigente? [buttons: Sí, tengo licencia | No]
    Candidate: Sí, tengo licencia
    Agent: ¡Genial! ¿Qué disponibilidad tienes: tiempo completo, medio tiempo o fines de semana? [buttons: Tiempo completo | Medio tiempo | Fines de semana]
    Candidate: Medio tiempo
    Agent: ¡Anotado, medio tiempo! ¿En qué horario prefieres trabajar: mañana, tarde, noche o es flexible? [list: Mañana | Tarde | Noche | Flexible]
    Candidate: En la tarde
    Agent: ¿Cuántos años de experiencia tienes como repartidor?
    Candidate: 2
    Agent: ¿Con qué apps has trabajado: Glovo, Uber Eats, Just Eat, Rappi, DiDi Food, otra o ninguna? [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Otra | Ninguna app]
    Candidate: Glovo
    Agent: ¡Gracias! ¿Desde qué día podrías empezar?
    Candidate: El próximo domingo me queda bien
    Agent: Perfecto, Ramiro. ¿Cuál domingo te queda mejor: el 18 u el 25 de octubre? [buttons: Domingo 18 oct | Domingo 25 oct]
    Candidate (tapped): Domingo 25 oct
    Agent: Ramiro, esto es lo que tengo: nombre Ramiro Rodríguez, colonia Centro en Puebla, con licencia de manejo vigente, medio tiempo por la tarde, 2 años de experiencia con Glovo y inicio el domingo 25 de octubre. ¿Está todo correcto?
    Candidate: Hi, I'd like to apply as a delivery rider
    Agent: Hi! I'm Grupo Sazón's virtual (AI) assistant 🛵 I'll help you apply as a delivery rider in a few minutes. Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad. What's your full name?
    Candidate: Sam Carter
    Agent: Thanks, Sam! Which neighbourhood and city do you live in?
    Candidate: Madrid, in Chamberí
    Agent: Got it, Chamberí in Madrid. Do you have a driver's licence (carnet de conducir)? Let me know if it's valid, expired, suspended, or pending. [buttons: Yes, I have one | No]
    Candidate: Yes, I have one
    Agent: Thanks, Sam. Which days can you work: full time, part time, or weekends? [buttons: Full-time | Part-time | Weekends]
    Candidate: Part-time
    Agent: Thanks! What schedule works best for you: morning, afternoon, evening, or flexible? [list: Morning | Afternoon | Evening | Flexible]
    Candidate: Afternoons
    Agent: Got it, afternoons. How many years of delivery experience do you have? Just a number is fine, or 0 if you're new to it.
    Candidate: 2
    Agent: Thanks! Which delivery platforms have you worked with? Glovo, Uber Eats, Just Eat, Rappi, Didi Food, another app, or none of these? [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Other | No app]
    Candidate: Glovo
    Agent: Thanks, Glovo noted! When could you start? Give me a day or date.
    Candidate: Next Sunday works for me
    Agent: Which Sunday could you start: 18 October or 25 October? [buttons: Sunday 18 Oct | Sunday 25 Oct]
    Candidate (tapped): Sunday 18 Oct
    Agent: Here's your summary, Sam: - Name: Sam Carter - Zone: Chamberí, Madrid - Driver's licence: yes - Availability: part time - Preferred schedule: afternoons - Experience: 2 years, on Glovo - Start date: Sunday 18 October 2026 Is everything correct?

</details>

### 15. Unambiguous start dates are recorded with no extra question: pass

- pass: conversation ran to its end (ended: stopped)
- pass: stage confirm, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: este_domingo_es: no miss on the start date
- pass: este_domingo_es: no date choice asked
- pass: este_domingo_es: recorded 2026-10-18
- pass: conversation ran to its end (ended: stopped)
- pass: stage confirm, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: domingo_semana_mx: no miss on the start date
- pass: domingo_semana_mx: no date choice asked
- pass: domingo_semana_mx: recorded 2026-10-25
- pass: conversation ran to its end (ended: stopped)
- pass: stage confirm, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: manana_es: no miss on the start date
- pass: manana_es: no date choice asked
- pass: manana_es: recorded 2026-10-13
- pass: conversation ran to its end (ended: stopped)
- pass: stage confirm, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: tomorrow_en: no miss on the start date
- pass: tomorrow_en: no date choice asked
- pass: tomorrow_en: recorded 2026-10-13
- pass: conversation ran to its end (ended: stopped)
- pass: stage confirm, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: el_lunes_mx: no miss on the start date
- pass: el_lunes_mx: no date choice asked
- pass: el_lunes_mx: recorded 2026-10-19
- pass: conversation ran to its end (ended: stopped)
- pass: stage confirm, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: veinte_octubre_es: no miss on the start date
- pass: veinte_octubre_es: no date choice asked
- pass: veinte_octubre_es: recorded 2026-10-20
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R53BQ6PH9EQTGCZR8M2SH0: stage confirm, status open, events application_started, answer_recorded x8, stage_changed x7
- este_domingo_es (2026-10-12): recorded 2026-10-18
- Application app_01M4R54DDW0CRPQD8XY6RNKE4A: stage confirm, status open, events application_started, answer_recorded x8, stage_changed x7
- domingo_semana_mx (2026-10-12): recorded 2026-10-25
- Application app_01M4R55JRW9XHFBNX86CGW2R56: stage confirm, status open, events application_started, answer_recorded x8, stage_changed x7
- manana_es (2026-10-12): recorded 2026-10-13
- Application app_01M4R56P6ZPYEP6TA37CACSBWZ: stage confirm, status open, events application_started, answer_recorded x8, stage_changed x7
- tomorrow_en (2026-10-12): recorded 2026-10-13
- Application app_01M4R57WNR2BH44K5XRGVNEW80: stage confirm, status open, events application_started, answer_recorded x8, stage_changed x7
- el_lunes_mx (2026-10-12): recorded 2026-10-19
- Application app_01M4R594PN3002QBSVMVHY1ZRP: stage confirm, status open, events application_started, answer_recorded x8, stage_changed x7
- veinte_octubre_es (2026-10-12): recorded 2026-10-20

- reply to opening: 3.3 s
- reply to answer:full_name: 5.1 s
- reply to answer:zone: 4.6 s
- reply to answer:driver_license: 3.8 s
- reply to answer:availability: 3.8 s
- reply to answer:preferred_schedule: 3.5 s
- reply to answer:experience_years: 3.3 s
- reply to answer:experience_platforms: 3.6 s
- reply to answer:start_date: 3.6 s
- reply to opening: 5.9 s
- reply to answer:full_name: 5.9 s
- reply to answer:zone: 4.8 s
- reply to answer:driver_license: 3.3 s
- reply to answer:availability: 3.3 s
- reply to answer:preferred_schedule: 4.1 s
- reply to answer:experience_years: 3.3 s
- reply to answer:experience_platforms: 3.3 s
- reply to answer:start_date: 4.3 s
- reply to opening: 3.5 s
- reply to answer:full_name: 5.1 s
- reply to answer:zone: 4.6 s
- reply to answer:driver_license: 3.5 s
- reply to answer:availability: 3.3 s
- reply to answer:preferred_schedule: 3.1 s
- reply to answer:experience_years: 3.5 s
- reply to answer:experience_platforms: 3.8 s
- reply to answer:start_date: 5.8 s
- reply to opening: 3.6 s
- reply to answer:full_name: 6.1 s
- reply to answer:zone: 6.1 s
- reply to answer:driver_license: 5.3 s
- reply to answer:availability: 3.3 s
- reply to answer:preferred_schedule: 3.8 s
- reply to answer:experience_years: 4.1 s
- reply to answer:experience_platforms: 3.3 s
- reply to answer:start_date: 3.8 s
- reply to opening: 4.8 s
- reply to answer:full_name: 5.6 s
- reply to answer:zone: 4.8 s
- reply to answer:driver_license: 4.3 s
- reply to answer:availability: 3.6 s
- reply to answer:preferred_schedule: 4.6 s
- reply to answer:experience_years: 3.8 s
- reply to answer:experience_platforms: 5.6 s
- reply to answer:start_date: 3.8 s
- reply to opening: 3.5 s
- reply to answer:full_name: 5.3 s
- reply to answer:zone: 5.3 s
- reply to answer:driver_license: 3.8 s
- reply to answer:availability: 3.3 s
- reply to answer:preferred_schedule: 4.3 s
- reply to answer:experience_years: 3.3 s
- reply to answer:experience_platforms: 3.8 s
- reply to answer:start_date: 5.6 s

<details><summary>Transcript</summary>

    Candidate: Hola, busco trabajo de repartidora
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidora en unos minutos. Tus datos se tratan según nuestra política: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Lucía Romero
    Agent: ¡Gracias, Lucía! ¿En qué barrio y ciudad vives?
    Candidate: En Madrid, en Chamberí
    Agent: ¡Perfecto, Lucía! ¿Tienes carnet de conducir vigente? [buttons: Sí, tengo carnet | No]
    Candidate: Sí, tengo carnet
    Agent: ¡Genial, gracias! ¿Qué disponibilidad tienes: jornada completa, parcial o fines de semana? [buttons: Jornada completa | Media jornada | Fines de semana]
    Candidate: Media jornada
    Agent: ¡Anotado, media jornada! ¿Prefieres mañana, tarde, noche o te es indiferente? [list: Mañana | Tarde | Noche | Flexible]
    Candidate: Por las tardes
    Agent: ¡Perfecto! ¿Cuántos años de experiencia tienes como repartidora? Si nunca lo has hecho, puedes decirlo sin problema.
    Candidate: 2
    Agent: ¡Gracias! ¿Con qué plataformas de reparto has trabajado: Glovo, Uber Eats, Just Eat, Rappi, DiDi Food u otra? [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Otra | Ninguna app]
    Candidate: Glovo
    Agent: ¡Anotado, Glovo! ¿Cuándo podrías empezar?
    Candidate: Este domingo
    Agent: Lucía, revisemos tus datos: te llamas Lucía Romero, vives en Chamberí (Madrid), tienes carnet de conducir, buscas media jornada por las tardes, tienes 2 años de experiencia con Glovo y podrías empezar el domingo 18 de octubre de 2026. ¿Es correcto?
    Candidate: Hola, quiero trabajar de repartidor
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Ramiro Rodríguez
    Agent: Gracias, Ramiro. ¿En qué colonia y ciudad vives?
    Candidate: Vivo en el Centro de Puebla
    Agent: ¿Tienes licencia de manejo vigente? [buttons: Sí, tengo licencia | No]
    Candidate: Sí, tengo licencia
    Agent: ¿Qué disponibilidad tienes: tiempo completo, medio tiempo o fines de semana? [buttons: Tiempo completo | Medio tiempo | Fines de semana]
    Candidate: Medio tiempo
    Agent: ¿En qué horario prefieres trabajar: mañana, tarde, noche o flexible? [list: Mañana | Tarde | Noche | Flexible]
    Candidate: En la tarde
    Agent: ¿Cuántos años de experiencia tienes como repartidor? Si nunca lo has hecho, dime "0".
    Candidate: 2
    Agent: ¿Con qué apps has trabajado? Por ejemplo Glovo, Uber Eats, Rappi, DiDi Food, Just Eat u otra. [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Otra | Ninguna app]
    Candidate: Glovo
    Agent: ¿Cuándo podrías empezar?
    Candidate: El domingo de la próxima semana
    Agent: Perfecto, Ramiro. Confirmo tus datos: nombre Ramiro Rodríguez, zona Centro en Puebla, con licencia de manejo vigente, medio tiempo por la tarde, 2 años de experiencia con Glovo y empezando el domingo 25 de octubre de 2026. ¿Es correcto?
    Candidate: Hola, busco trabajo de repartidora
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidora en unos minutos. Tus datos se tratan según nuestra política: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Lucía Romero
    Agent: Gracias, Lucía. ¿En qué barrio y ciudad vives?
    Candidate: En Madrid, en Chamberí
    Agent: Perfecto, Lucía. ¿Tienes carnet de conducir vigente? [buttons: Sí, tengo carnet | No]
    Candidate: Sí, tengo carnet
    Agent: Genial, Lucía. ¿Qué disponibilidad tienes: jornada completa, media jornada o fines de semana? [buttons: Jornada completa | Media jornada | Fines de semana]
    Candidate: Media jornada
    Agent: ¿En qué horario prefieres trabajar: mañana, tarde, noche o flexible? [list: Mañana | Tarde | Noche | Flexible]
    Candidate: Por las tardes
    Agent: ¿Cuántos años de experiencia tienes como repartidora?
    Candidate: 2
    Agent: ¿Con qué apps has repartido? Por ejemplo Glovo, Uber Eats, Just Eat, Rappi, DiDi Food u otra. [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Otra | Ninguna app]
    Candidate: Glovo
    Agent: ¡Anotado, Glovo! ¿Desde qué fecha podrías empezar?
    Candidate: Mañana
    Agent: Perfecto, Lucía. Resumen: te llamas Lucía Romero, vives en Chamberí (Madrid), tienes carnet de conducir, buscas media jornada por las tardes, tienes 2 años de experiencia con Glovo y podrías empezar el martes 13 de octubre de 2026. ¿Es correcto todo?
    Candidate: Hi, I'd like to apply as a delivery rider
    Agent: Hi! I'm Grupo Sazón's virtual (AI) assistant. I'll help you apply as a delivery rider in a few minutes. Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad. What's your full name?
    Candidate: Sam Carter
    Agent: Thanks, Sam! Which neighbourhood and city do you live in?
    Candidate: Madrid, in Chamberí
    Agent: Got it, Chamberí in Madrid. Do you have a driver's licence (carnet de conducir)? Let me know if it's valid, expired, suspended or pending. [buttons: Yes, I have one | No]
    Candidate: Yes, I have one
    Agent: Thanks, Sam. Which days can you work: full time, part time, or weekends? [buttons: Full-time | Part-time | Weekends]
    Candidate: Part-time
    Agent: Thanks, Sam. What time of day suits you best: morning, afternoon, evening, or flexible? [list: Morning | Afternoon | Evening | Flexible]
    Candidate: Afternoons
    Agent: Got it, afternoons. How many years of delivery experience do you have? If none, just say 0.
    Candidate: 2
    Agent: Thanks, Sam. Which delivery apps have you worked with? For example Glovo, Uber Eats, Just Eat, Rappi, DiDi Food, another app, or none on your own. [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Other | No app]
    Candidate: Glovo
    Agent: Thanks, Sam. When could you start? You can give me a day, like "el lunes" or a specific date.
    Candidate: Tomorrow works for me
    Agent: Here's your summary, Sam: Name: Sam Carter Area: Chamberí, Madrid Driver's licence: yes Availability: part-time Preferred time: afternoons Experience: 2 years, on Glovo Start date: 13 October 2026 Is everything correct?
    Candidate: Hola, quiero trabajar de repartidor
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Ramiro Rodríguez
    Agent: Gracias, Ramiro. ¿En qué colonia y ciudad vives?
    Candidate: Vivo en el Centro de Puebla
    Agent: ¿Tienes licencia de manejo vigente? [buttons: Sí, tengo licencia | No]
    Candidate: Sí, tengo licencia
    Agent: ¿Qué disponibilidad tienes: tiempo completo, medio tiempo o fines de semana? [buttons: Tiempo completo | Medio tiempo | Fines de semana]
    Candidate: Medio tiempo
    Agent: ¿En qué horario prefieres trabajar: mañana, tarde, noche o flexible? [list: Mañana | Tarde | Noche | Flexible]
    Candidate: En la tarde
    Agent: ¿Cuántos años de experiencia tienes como repartidor? Si nunca lo has hecho, dime 0.
    Candidate: 2
    Agent: ¿Con qué apps has trabajado? Por ejemplo Glovo, Uber Eats, Rappi, DiDi Food, Just Eat u otra. [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Otra | Ninguna app]
    Candidate: Glovo
    Agent: ¡Gracias, Ramiro! ¿Desde qué fecha podrías empezar?
    Candidate: El lunes
    Agent: Perfecto, Ramiro. Confirmo tus datos: te llamas Ramiro Rodríguez, vives en el Centro de Puebla, tienes licencia de manejo vigente, buscas medio tiempo por la tarde, tienes 2 años de experiencia con Glovo y podrías empezar el lunes 19 de octubre de 2026. ¿Es correcto?
    Candidate: Hola, busco trabajo de repartidora
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidora en unos minutos. Tus datos se tratan según nuestra política: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Lucía Romero
    Agent: Gracias, Lucía. ¿En qué barrio y ciudad vives?
    Candidate: En Madrid, en Chamberí
    Agent: Perfecto, Lucía. ¿Tienes carnet de conducir vigente? [buttons: Sí, tengo carnet | No]
    Candidate: Sí, tengo carnet
    Agent: ¿Qué disponibilidad tienes: jornada completa, media jornada o fines de semana? [buttons: Jornada completa | Media jornada | Fines de semana]
    Candidate: Media jornada
    Agent: ¿En qué horario prefieres trabajar: mañana, tarde, noche o es flexible? [list: Mañana | Tarde | Noche | Flexible]
    Candidate: Por las tardes
    Agent: ¿Cuántos años de experiencia tienes como repartidora?
    Candidate: 2
    Agent: ¿Con qué apps has trabajado: Glovo, Uber Eats, Just Eat, Rappi, DiDi Food u otra? [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Otra | Ninguna app]
    Candidate: Glovo
    Agent: ¡Genial, Lucía! ¿Desde qué fecha podrías empezar?
    Candidate: El 20 de octubre
    Agent: Gracias, Lucía. Revisa si está bien: te llamas Lucía Romero, vives en Chamberí (Madrid), tienes carnet de conducir, buscas media jornada por las tardes, tienes 2 años de experiencia con Glovo y podrías empezar el 20 de octubre de 2026. ¿Es correcto?

</details>

### 16. Demo chat: an MX chat living in Chamberí moves to Spain; tomorrow is recorded: pass

- pass: conversation ran to its end (ended: stopped)
- pass: stage confirm, open
- pass: answers recorded
- pass: fields collected in order
- pass: events on the record
- pass: tools called (read from events)
- pass: AI disclosure in the first message
- pass: Criba never named
- pass: reply language
- pass: tomorrow recorded (2026-10-13)
- pass: the start date was given once
- pass: no miss on the start date
- pass: turns served by anthropic/claude-haiku-5.5

- Application app_01M4R5AA7Z3KJXN4WN8MYM0RNG: stage confirm, status open, events application_started, answer_recorded x8, stage_changed x7
- country ES, start date 2026-10-13 (2026-10-12 in Madrid)

- reply to opening: 4.3 s
- reply to answer:full_name: 6.9 s
- reply to answer:zone: 9.6 s
- reply to answer:driver_license: 5.9 s
- reply to answer:availability: 4.3 s
- reply to answer:preferred_schedule: 3.8 s
- reply to answer:experience_years: 5.4 s
- reply to answer:experience_platforms: 3.9 s
- reply to answer:start_date: 3.9 s

<details><summary>Transcript</summary>

    Candidate: Hey!
    Agent: Hi! I'm Grupo Sazón's virtual (AI) assistant 🛵 I'll help you apply as a delivery driver in a few minutes. Your data is handled under our privacy notice: https://empleo.gruposazon.example/privacidad. What's your full name?
    Candidate: Ramiro Salcedo
    Agent: Thanks, Ramiro! Which colonia and city do you live in?
    Candidate: I live in Chamberi
    Agent: Got it, Chamberí in Madrid. Do you have a valid driver's licence? [buttons: Yes, I have one | No]
    Candidate: Yes, I have one
    Agent: Thanks, Ramiro. Which days can you work: full time, part time, weekends, or a mix? [buttons: Full-time | Part-time | Weekends]
    Candidate: Full time
    Agent: Full time noted, Ramiro. What time of day do you prefer to work: morning, afternoon, evening, or flexible? [list: Morning | Afternoon | Evening | Flexible]
    Candidate: Evenings
    Agent: Evenings it is. How many years of delivery experience do you have? Write 0 if you have none.
    Candidate: 2
    Agent: Two years, great. Which delivery apps have you worked with? Glovo, Uber Eats, Just Eat, Rappi, Didi Food, or another one? [list: Glovo | Uber Eats | Just Eat | Rappi | DiDi Food | Other | No app]
    Candidate: Uber Eats
    Agent: Uber Eats noted. When could you start? You can give me a day or a date.
    Candidate: tomorrow works for me
    Agent: Here's your summary, Ramiro: Name: Ramiro Salcedo Area: Chamberí, Madrid Driver's licence: yes Availability: full time Preferred schedule: evenings Experience: 2 years, Uber Eats Start date: 13 October 2026 Is everything correct?

</details>

### 17. Language request: "me preguntas en inglés porfi" gives English reply, buttons and record: pass

- pass: the availability question comes with Spanish buttons first
- pass: the Application language is es-ES before the request
- pass: the reply to the request is in English
- pass: the Application language is en (the request, not the language it is written in)
- pass: the buttons on that turn are English, as the reply and the record
- pass: the Jev router served the calls (jev rows in the ledger)
- pass: the next question is in English
- pass: the Application language is still en
- pass: the tapped availability is recorded
- pass: turns served by anthropic/claude-haiku-5.5

- reply to the Spanish opener: 3.6 s
- reply before the availability question: 6.1 s
- reply before the availability question: 5.1 s
- reply before the availability question: 3.6 s
- reply to the language request: 3.6 s
- tap on an English button: 2.8 s

<details><summary>Transcript</summary>

    Candidate: Hola, busco trabajo de repartidor
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestra política: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Me llamo Ramiro Salcedo
    Agent: Gracias, Ramiro. ¿En qué barrio y ciudad vives?
    Candidate: Vivo en Chamberí, Madrid
    Agent: Perfecto, Chamberí en Madrid. ¿Tienes carnet de conducir vigente? [buttons: Sí, tengo carnet | No]
    Candidate: Sí, tengo carnet
    Agent: Genial. ¿Qué disponibilidad tienes: tiempo completo, parcial o fines de semana? [buttons: Jornada completa | Media jornada | Fines de semana]
    Candidate: me preguntas en inglés porfi
    Agent: Sure, we can continue in English. Which availability works for you: full time, part time, or weekends? [buttons: Full-time | Part-time | Weekends]
    Candidate (tapped): Full-time
    Agent: Thanks, availability is saved. What time of day do you prefer to work: morning, afternoon, evening, or flexible? [list: Morning | Afternoon | Evening | Flexible]

</details>

### 18. Demo chat: three unreadable messages hand the chat to the Recruiters: pass

- pass: the third unreadable message gets the fixed message, and nothing from the agent
- pass: an unreadable Escalation is open
- pass: each unreadable message counted once as a miss
- pass: a message while the Recruiters have the chat gets no reply
- pass: the Application stays open with nothing decided or recorded
- pass: turns served by anthropic/claude-haiku-5.5

- reply to an unreadable message: 5.4 s
- reply to an unreadable message: 6.1 s
- reply to an unreadable message: 0.5 s

<details><summary>Transcript</summary>

    Candidate: Hola
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Blok loka ksks aaa
    Agent: Gracias, pero no alcancé a entender tu nombre. ¿Me compartes tu nombre y apellido, por ejemplo "Ana García López"?
    Candidate: asd as sssskkk
    Agent: Disculpa, no logré leer tu nombre. ¿Podrías escribirlo de nuevo con tu nombre y apellido, por ejemplo "Juan Pérez"?
    Candidate: aksda asdas asassjqw weedyas
    Agent: No logro entender tus mensajes. Una persona del equipo de Grupo Sazón te escribirá por aquí.
    Candidate: hola?? me entiendes

</details>

### 19. Spain chat: the Zone question says barrio, never colonia: pass

- pass: the ES Zone question says barrio
- pass: the Spain Zone question never says colonia
- pass: turns served by anthropic/claude-haiku-5.5

- ES Zone question: Gracias, Lucía. ¿En qué barrio y ciudad vives?

- reply to the opening: 3.6 s
- reply to the name: 5.3 s

<details><summary>Transcript</summary>

    Candidate: Hola, quiero trabajar de repartidor
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestra política: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Lucía Romero
    Agent: Gracias, Lucía. ¿En qué barrio y ciudad vives?

</details>

### 20. MX chat: the Zone question keeps colonia: pass

- pass: the MX Zone question says colonia
- pass: turns served by anthropic/claude-haiku-5.5

- MX Zone question: Gracias, Ramiro. ¿En qué colonia y ciudad vives?

- reply to the opening: 3.6 s
- reply to the name: 6.6 s

<details><summary>Transcript</summary>

    Candidate: Hola, quiero trabajar de repartidor
    Agent: ¡Hola! Soy el asistente virtual (IA) de Grupo Sazón 🛵 Te ayudo a postularte como repartidor en unos minutos. Tus datos se tratan según nuestro aviso de privacidad: https://empleo.gruposazon.example/privacidad. ¿Cómo te llamas (nombre y apellido)?
    Candidate: Ramiro Rodríguez
    Agent: Gracias, Ramiro. ¿En qué colonia y ciudad vives?

</details>
