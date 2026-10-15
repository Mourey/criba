# Model comparison

Real-model run of 2026-10-10, on
OpenRouter, under the owner's hard **$5 cap on real spend**, counted by the spend guard
(`src/tests/sim/spend.py`, after `fix/spend-guard-pricing`) with the turn size trimmed by
`fix/turn-size-real`. Steps in the order of `demo.md` section 5: the ledger reconciled to the
bill, one real Sonnet 5.5 summary, the Haiku 5.5 comparison (stack `criba-real-haiku`, port
18110), the Sonnet 5.5 comparison (stack `criba-real-sonnet`, port 18111, turn-model override).
Both stacks were torn down with `down -v`.

**The run is complete.** All 9 usable personas ran on each model (the 4 that move the sim clock
cannot run on a real stack); the guard did not stop either comparison.

## Generated table (`sim.compare report`)

Dataset: real-3. 18 persona runs.

| Model (label) | Served | Screenings | Turns | Latency p50 (s) | Latency p95 (s) | Cost per screening (USD) | Cost per Qualified (USD) | Validation failures per screening | Escalation rate | Outcome accuracy | Not as expected | Errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | anthropic/claude-haiku-5.5, anthropic/claude-sonnet-5.5 | 9 | 136 | 2.81 | 4.595 | 0.02609 | 0.046961 | 0 | 0.222 | 0.222 | confirm_correction_mx, invalid_then_corrected_es, language_switch_mx, multi_answer_es, qualified_es, zone_ambiguous_mx, zone_freeform_mx | 0 |
| anthropic/claude-sonnet-5.5 | anthropic/claude-sonnet-5.5 | 9 | 91 | 3.837 | 6.66 | 0 | 0 | 0.667 | 0 | 0.444 | invalid_then_corrected_es, language_switch_mx, multi_answer_es, zone_ambiguous_mx, zone_freeform_mx | 0 |

Read two columns with care:

- **Cost** here is the backend's own estimate (`est_cost_usd`), not the bill. The backend has
  no Sonnet price row, so Sonnet shows 0; for Haiku it reads about 7 times the bill ($0.2504
  estimated for the Haiku turn calls against $0.0356 billed). The real costs are in the next
  table.
- **Outcome accuracy** is the full persona check (stage, answers, field order, events, tools,
  AI disclosure). "Final outcome" in the next table checks only where the Application ended.

## Per model, with the real bill

Latency is the time to the first agent reply per Candidate turn, measured by the driver. Cost
comes from OpenRouter: the turn calls' `total_cost` from their generation records
(`/api/v1/generation`, one per model call, 452 calls, the ids read from Hermes's
`agent.log`), and the Sonnet summaries' `usage.cost` as stored in the usage ledger
(`reconciled_cost_usd`). Both are the bill. The guard's own count is listed next to it.

| | Haiku 5.5 | Sonnet 5.5 |
|---|---|---|
| Screenings run (of 9 usable) | 9 | 9 |
| Candidate turns | 136 (61 in `zone_ambiguous_mx`, which hit the turn limit) | 91 |
| Model calls (turns) | 232 | 204 |
| Latency p50 / p95 (s) | 2.81 / 4.60 (3.07 / 4.61 without `zone_ambiguous_mx`) | 3.84 / 6.66 |
| Turn cost per screening (USD, bill) | 0.0036 | 0.0496 |
| Turn and summary cost per screening (USD, bill) | 0.0074 | 0.0535 |
| Turn cost per Qualified Application (USD, bill) | 0.0065 (5 Qualified) | 0.0892 (5 Qualified) |
| Turn and summary cost per Qualified Application (USD, bill) | 0.0132 | 0.0964 |
| Comparison bill: turns + summaries (USD) | 0.0326 + 0.0336 = **0.0662** | 0.4460 + 0.0358 = **0.4818** |
| Same step, guard prices (USD) | 0.2684 (the backend estimate, which is higher, wins) | 0.6323 (1.3 times the bill) |
| Validation failures (`answer_invalid`) | 0 in 9 screenings | 6 in 3 screenings (2 expected, 4 from the driver) |
| Escalations | 2 in 9 (`zone_ambiguous_mx`, `zone_freeform_mx`) | 0 in 9 |
| Final outcome as the persona expects (stage, status, outcome) | 6 of 9 | 6 of 9 |
| Full persona check (generated table) | 2 of 9 | 4 of 9 |

On the personas both models finished normally, Sonnet's turns cost 15 to 21 times Haiku's:
`confirm_correction_mx` $0.0697 against $0.0033, `qualified_es` $0.0429 against $0.0022,
`prompt_injection_es` $0.0471 against $0.0027, `no_license_mx` $0.0283 against $0.0019.

### Which misses are the model's and which are the driver's

**A driver gap hits both models (3 Sonnet runs, 1 Haiku run).** After the years of experience,
both models asked for the platforms in words the driver does not recognise: "¿Con qué apps has
trabajado? Por ejemplo Glovo, ..." (the question has no "plataforma" and the examples come
after the question mark) or "¿En qué plataformas has trabajado (Glovo, Uber Eats, Just Eat,
etc.)?" (the "etc." period cuts the last question down to ")?"). `sim.detect.detect_prompt`
returns nothing, and the driver sends the years answer again until the turn ends. That is
`language_switch_mx` on both models, and `multi_answer_es` and `zone_ambiguous_mx` on Sonnet:
each stopped at `experience` with the platforms unanswered, and on Sonnet the repeats drew two
`answer_invalid` (`experience_platforms`, reason `unresolved`) each. Both models handled the
repeats well (re-asked, then offered the platform buttons). These four runs say nothing about
the model; the detector needs "apps" and a period-tolerant last question. Not fixed here.

**Haiku's own misses:**

- **`zone_ambiguous_mx`, the Zone tap (real).** "Puebla" gave three Zone buttons
  (`answer_ambiguous`). The Candidate tapped Centro; Haiku did not record it ("no necesito
  códigos: elige tu zona tocando una de las opciones"), asked again three times, raised an
  Escalation (`repeated_invalid_answers`) and then answered the driver's repeated tap until the
  60-turn limit. No `record_answer` reached the backend for the tap. In
  `confirm_correction_mx` the same tap on the same list was recorded, so it is not systematic,
  but it happened once in two.
- **`zone_freeform_mx`, a free-form Zone (real).** "Ciudad de México, Cuajimalpa" was not
  resolved ("No encuentro Cuajimalpa en mi lista de zonas"), two `answer_ambiguous`, then an
  Escalation. Sonnet resolved the same answer and reached Qualified.
- **Field order (cosmetic).** In `qualified_es` and `multi_answer_es` the Candidate gave name,
  Zone and license in one message; Haiku recorded the license before the Zone. All three were
  recorded and the Application qualified.
- **AI disclosure (`qualified_es`).** The greeting said "Soy el asistente virtual de Grupo
  Sazón" without "(IA)"; the other Haiku greetings and every Sonnet greeting carried it. The
  persona check fails on it.
- **Invalid names not recorded (`invalid_then_corrected_es`).** Haiku handled "Ana" and
  "ana123" in prose (asked for the surname, then for the full name) and recorded only "Ana
  Pérez": 0 `answer_invalid` where the persona expects 3. The outcome is right; the attempts
  counter is not moved. Sonnet recorded 2 of the 3.
- **`confirm_correction_mx`**: "Centro, Puebla" got the Zone buttons (`answer_ambiguous`, which
  the persona forbids) instead of being resolved directly. The outcome is right.

**Sonnet's own misses:** `invalid_then_corrected_es` (2 `answer_invalid` of 3 expected) and
`zone_freeform_mx` (an `answer_ambiguous` where the persona expected an `answer_invalid`: the
designed Toluca confirmation of ADR 0042, so the persona now expects that flow); both ended
Qualified as expected.

So, the driver aside: Haiku ended 6 of 8 screenings where they should end and Sonnet 6 of 6.
Haiku's two wrong endings are both at the Zone step, both ended in an Escalation (a human
picks it up, nothing wrong is recorded), and neither recorded a wrong answer or a wrong
Knockout. `no_license_mx` was knocked out correctly on both models.

## Spend

Every spend line printed, in order:

```
reconcile: spend ledger /tmp/criba-real-spend.json reconciled: was $4.9156, now $0.1500 (adjustment -4.7656): replace the old guard's 33x over-count with OpenRouter's billed usage (fix/spend-guard-pricing)
           ledger reconciled: was $4.9156, now $0.1500 (adjustment -4.7656): replace the old guard's 33x over-count with OpenRouter's billed usage (fix/spend-guard-pricing)
           real-model spend recorded in the ledger (/tmp/criba-real-spend.json): $0.1500 of the $5.00 cap
summary:   real-model spend: this run $0.0233, earlier runs $0.1500, total $0.1733 of the $5.00 cap (guard prices; the OpenRouter bill is the figure that counts)
Haiku:     real-model spend: this run $0.2684, earlier runs $0.1733, total $0.4417 of the $5.00 cap (guard prices; the OpenRouter bill is the figure that counts)
Sonnet:    real-model spend: this run $0.6323, earlier runs $0.4417, total $1.0740 of the $5.00 cap (guard prices; the OpenRouter bill is the figure that counts)
spend:     real-model spend recorded in the ledger (/tmp/criba-real-spend.json): $1.0740 of the $5.00 cap
```

**The reconcile.** Before the run, the key endpoint (`GET /api/v1/key`, read-only) reported
`usage_daily` $0.148423 for 2026-10-10 (all of it the earlier partial run) and lifetime `usage`
$0.459469 (earlier days included). The ledger held the old guard's $4.9156. It was reconciled
once, to $0.15 (the daily figure rounded up).

**Total real spend.** The guard's final figure is **$1.0740 of the $5.00 cap**: $0.15 billed
for the earlier run plus $0.9240 at guard prices for this one. The bill is lower: after the
run the key reported `usage_daily` $0.707258, so this run billed **$0.5588** and the day
(both runs) **$0.7073**. By call: $0.4816 for the 452 turn calls (Haiku $0.0356 over 248,
Sonnet $0.4460 over 204) and $0.0771 for the 13 Sonnet summaries, $0.5587 together, which
matches the key's figure.

The guard now prices close to the bill on Sonnet (1.3 times) and on the summary step (2.2
times). On Haiku it is about 4 times the bill because the backend's own estimate for Haiku
(`criba.channel.run_client`) is higher than the guard's token pricing and the guard takes the
higher of the two; the backend estimate looks like it still counts cached tokens at the full
input rate. That is safe for the cap and not fixed here.

## Recommendation

On this data, keep ADR 0025 as it is: Haiku 5.5 for screening turns, Sonnet 5.5 for the one
summary per Application. Fix the Zone step on Haiku before treating its quality as settled.

- **Cost:** Sonnet turns cost 15 to 21 times Haiku's on the same persona, and $0.0892 against
  $0.0065 per Qualified Application (turns only). At about 1,000 Applications a week, Haiku's
  turns are a few dollars a week; Sonnet's would be about $50.
- **Latency:** Haiku is faster and inside the 5-second target at p95 (4.6 s). Sonnet's p95,
  6.7 s, is over it.
- **Quality:** with the driver gap set aside, Sonnet ended every screening where it should
  (6 of 6) and Haiku 6 of 8. Both of Haiku's misses are at the Zone step (a tapped Zone not
  recorded once, a free-form Zone not resolved once) and both ended in an Escalation, not a
  wrong record. That is a recoverable failure, but it costs an Operator's time. It points at
  the Zone handling in the Screener skill and the `search_zones` result trimmed by
  `fix/turn-size-real`, not at a model swap: Sonnet would cost 14 times more to fix two
  Escalations in nine. One run per persona is thin; re-run the Zone personas a few times on
  Haiku after a fix.
- **Summaries on Sonnet:** good, specific prose at about half a cent each ($0.0042 to $0.0082,
  13 calls). Keep it, but two summaries in 13 were cut off by the 500-token limit (below).

## Criterion 5: a real Sonnet 5.5 summary

Run of 2026-10-10 on branch `evidence/real-summary`, a real stack (project `criba-real-summary`,
port 18113): Haiku 5.5 turns, one Sonnet 5.5 summary per persona, via `sim.compare summary`.
Both runs printed `models served: anthropic/claude-haiku-5.5, anthropic/claude-sonnet-5.5` and
`summary prose by anthropic/claude-sonnet-5.5:`.

**The truncation fix as first merged does not work against the real API** (corrected afterwards
by `fix/summary-reasoning-param`, below). `summary.py` sends
`"reasoning": {"enabled": false}`. OpenRouter answers HTTP 400 `Reasoning is mandatory for this
endpoint and cannot be disabled.` for `anthropic/claude-sonnet-5.5` (reproduced with a direct
call). The backend caught it, stored the summary with `prose = null` (the structured part was
kept), and wrote a `usage` row with `status = error`, `error = HTTPStatusError`. The first
`qualified_es` run therefore produced **no prose**; `sim.compare summary` exited 6. The unit
tests did not see it because they use a fake HTTP client.

The two samples below come from the same code with one change, made on the working copy for
this run and not committed then (it has since been merged as `fix/summary-reasoning-param`):
`"reasoning": {"effort": "low"}` in place of `{"enabled": false}`. The same request with `effort` `low`, `minimal` or `reasoning.max_tokens`
returns 200 and `reasoning_tokens = 0`. Everything else (100-word instruction, 600-token budget,
retry on a cut-off) is the merged code. A temporary log line recorded `finish_reason` and
`reasoning_tokens` on every call, because the merged code logs them only when a call is cut off;
it was removed too.

### Sample 1: `qualified_es` (es-ES)

> Ana García ha completado el cribado y queda cualificada para Chamberí (Madrid), con carnet de
> conducir, disponibilidad a tiempo completo y preferencia por la tarde. No hay contradicciones
> ni incidencias, y confirmó el resumen final sin cambios. Dijo solo "el lunes" como fecha de
> inicio y el sistema la fijó en el 12 de octubre de 2026, así que conviene confirmarla con
> ella. Declara dos años de experiencia con Glovo y Uber Eats, sin aportar más detalles que
> verificar.

### Sample 2: `confirm_correction_mx` (es-MX)

> Diego Martínez completó la evaluación y quedó calificado, sin alertas ni escalamientos.
> Primero dijo vivir en Centro, Puebla, y al revisar el resumen corrigió su zona a La Paz, que
> es la que quedó registrada; conviene confirmar cuál es su zona real de reparto. Solo dijo "el
> lunes" como fecha de inicio y el sistema lo registró como el 12 de octubre de 2026, así que
> vale la pena verificar que esa sea la fecha que quiere. Terminó todo en unos 35 segundos, con
> respuestas muy breves.

### Measurements

| | `qualified_es` | `confirm_correction_mx` |
|---|---|---|
| Ends on a complete sentence | yes (full stop) | yes (full stop) |
| `finish_reason` | `stop` | `stop` |
| `reasoning_tokens` | 0 | 0 |
| Output tokens (characters) | 192 (470) | 202 (483) |
| Input tokens | 1,379 | 1,708 |
| Calls to the summary model, retry | 1, no retry | 1, no retry |
| Latency | 2.6 s | 2.5 s |
| Billed (`usage.cost`) | $0.004678 | $0.005436 |
| Spend line | `this run $0.0195, earlier runs $1.0896, total $1.1091 of the $5.00 cap` | `this run $0.0281, earlier runs $1.1091, total $1.1372 of the $5.00 cap` |

The failed first attempt (400, no prose) printed `this run $0.0156, earlier runs $1.0740, total
$1.0896 of the $5.00 cap`; that is Haiku turns only. The "this run" figures are guard prices
(an upper bound) and include the Haiku turns; the summary call's own billed charge is the
`usage.cost` row above. Neither sample was cut off, and neither needed the retry. At 192 and 202
output tokens against a 600-token budget there is room to spare. Length: 79 and 87 words, both
inside the 100-word instruction, 4 sentences each.

The `summary` step's progress line marked `qualified_es` "NOT as expected": the Application
qualified, but Haiku recorded the license before the Zone (the field-order check).
`confirm_correction_mx` was "ok".

### History: the truncated sample (earlier run, 500-token limit)

The first criterion 5 sample (`qualified_es`, Haiku stack) was cut off mid-sentence, after "la
fijó en el lunes 12 de octubre de 2026": the call used all 500 output tokens (`max_tokens`)
for 376 characters of prose, because most of the budget went to hidden thinking tokens. A
second summary was cut off the same way (Sonnet stack, `zone_freeform_mx`: 500 output tokens,
556 characters, ending "...horario de mañana o noche; no"), and a third came close (473
output tokens for 515 characters, `multi_answer_es`). So 2 of 13 summaries were truncated.
`fix/summary-truncation` turned reasoning off, asked for at most 100 words and added one retry;
the run above shows that the "reasoning off" part is rejected by the API for this model (see
above), and that `effort: low` gives complete prose with no reasoning tokens in these two
samples. Two samples do not prove truncation cannot recur; the retry and the discard of a
still-cut-off text stay as the safety net.

## Real tokens per model call

From OpenRouter's generation records (`native_tokens_prompt`, the real tokenizer), every turn
call of this run, including the `summary` step's 16 Haiku calls:

| | Calls | p50 | p90 | max | Over 10,000 | First call of a session |
|---|---|---|---|---|---|---|
| Haiku 5.5 | 248 | 8,182 | 11,312 | 12,793 (`zone_ambiguous_mx`) | 52 | 6,555 to 6,588 |
| Sonnet 5.5 | 204 | 8,392 | 10,473 | 12,504 (`invalid_then_corrected_es`) | 30 | 6,555 to 6,588 |

**Haiku turns are not all under 10,000.** The 52 Haiku calls over the limit are all in the
two Zone runs that went wrong: 47 in `zone_ambiguous_mx` (61 Candidate turns, against 7 to 14
for a normal screening) and 5 in `zone_freeform_mx` (max 10,872). In the other seven
personas, and in the `summary` step, every Haiku call is under 10,000 (max 9,625,
`confirm_correction_mx`). Sonnet's 30 calls over the limit are spread over five personas. About
98% of the prompt was read from the cache on both models. Before and after are in
`turn-size.md`, "Real usage".

## Earlier partial run

The first real run (branch `evidence/real-model-2`) was stopped early by the old guard, which
counted $4.9156 for a bill of about $0.147 (about 33 times; it counted the cached tokens twice
and priced Haiku turns at Sonnet's rate). Haiku ran 6 personas and Sonnet 1. Its write-up is in
this file's history: `git log -p -- docs/evidence/model-comparison.md`.
