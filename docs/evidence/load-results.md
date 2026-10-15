# Load results

## Ramp

| Step | Offered, Candidates a week | Messages/s (x ADR peak) | p95 (s) | Error rate | Bottleneck |
|---|---|---|---|---|---|
| Step 1: speed-up 1 | 10,000 | 0.435 (0.6x) | 0.529 | 0 | hermes (headroom) |
| Step 2: speed-up 10 | 100,000 | 4.359 (6.2x) | 0.525 | 0 | hermes (headroom) |
| Step 3: speed-up 30 | 300,000 | 12.778 (18.3x) | 1.525 | 0 | hermes (saturated) |

## Runs

### Step 1: speed-up 1

Plan: 10,000/week x peak 3 over 15 min of sim time at speed-up 1 (15.0 min wall, about 45 Candidates). Ran 887.5 s.

| Metric | Value |
|---|---|
| Candidates scheduled / started / finished | 57 / 57 / 57 |
| Ended | completed 55, knockout 2 |
| Most Candidates in conversation at once | 3 |
| Candidate messages per second | 0.435 |
| Agent runs per second | 0.719 |
| Offered rate, as Candidates a week (same peak shape) | 10,000 |
| Message rate, as a multiple of ADR 0028's peak at this weekly rate | 0.6x |
| Reply latency p50 / p95 / p99 (s) | 0.517 / 0.529 / 0.537 |
| First reply p95 (s); first replies over 5 s | 0.534; 0 |
| Turns over 10 s, of all turns | 0 of 386 |
| Agent run latency, median of per-Application medians (ms) | 275 |
| Error rate (Candidates) | 0  |
| Applications as the persona expects | 57 of 57 |
| Cost: total / per Candidate / per week at this rate (USD) | 0 / 0 / 0 |
| Models served | deterministic-summary, stub-model |

| Service | CPU p90 (% of one core) | CPU max | Memory max (MiB) |
|---|---|---|---|
| backend | 8.4 | 18.0 | 91.4 |
| hermes | 15.4 | 32.5 | 505.5 |
| stub-llm | 1.8 | 20.6 | 90.1 |
| web | 0.2 | 1.2 | 11.6 |

**Bottleneck (not saturated):** hermes. no limit broken; busiest is hermes at p90 CPU 15% of one core; at linear scaling it reaches 85% near 2.4 messages/s (6x this run).

### Step 2: speed-up 10

Plan: 10,000/week x peak 3 over 15 min of sim time at speed-up 10 (1.5 min wall, about 45 Candidates). Ran 99.3 s.

| Metric | Value |
|---|---|
| Candidates scheduled / started / finished | 41 / 41 / 41 |
| Ended | completed 36, knockout 5 |
| Most Candidates in conversation at once | 8 |
| Candidate messages per second | 4.359 |
| Agent runs per second | 4.359 |
| Offered rate, as Candidates a week (same peak shape) | 100,000 |
| Message rate, as a multiple of ADR 0028's peak at this weekly rate | 6.2x |
| Reply latency p50 / p95 / p99 (s) | 0.513 / 0.525 / 0.546 |
| First reply p95 (s); first replies over 5 s | 0.521; 0 |
| Turns over 10 s, of all turns | 0 of 433 |
| Agent run latency, median of per-Application medians (ms) | 272 |
| Error rate (Candidates) | 0  |
| Applications as the persona expects | 41 of 41 |
| Cost: total / per Candidate / per week at this rate (USD) | 0 / 0 / 0 |
| Models served | deterministic-summary, stub-model |

| Service | CPU p90 (% of one core) | CPU max | Memory max (MiB) |
|---|---|---|---|
| backend | 16.2 | 21.2 | 81.7 |
| hermes | 44.9 | 76.3 | 516.7 |
| stub-llm | 5.7 | 16.8 | 82.2 |
| web | 0.3 | 0.4 | 11.4 |

**Bottleneck (not saturated):** hermes. no limit broken; busiest is hermes at p90 CPU 45% of one core; at linear scaling it reaches 85% near 8.3 messages/s (2x this run).

### Step 3: speed-up 30

Plan: 10,000/week x peak 3 over 15 min of sim time at speed-up 30 (0.5 min wall, about 45 Candidates). Ran 47.2 s.

| Metric | Value |
|---|---|
| Candidates scheduled / started / finished | 58 / 58 / 58 |
| Ended | completed 55, knockout 3 |
| Most Candidates in conversation at once | 25 |
| Candidate messages per second | 12.778 |
| Agent runs per second | 12.778 |
| Offered rate, as Candidates a week (same peak shape) | 300,000 |
| Message rate, as a multiple of ADR 0028's peak at this weekly rate | 18.3x |
| Reply latency p50 / p95 / p99 (s) | 1.011 / 1.525 / 1.781 |
| First reply p95 (s); first replies over 5 s | 1.524; 0 |
| Turns over 10 s, of all turns | 0 of 603 |
| Agent run latency, median of per-Application medians (ms) | 820 |
| Error rate (Candidates) | 0  |
| Applications as the persona expects | 58 of 58 |
| Cost: total / per Candidate / per week at this rate (USD) | 0 / 0 / 0 |
| Models served | deterministic-summary, stub-model |

| Service | CPU p90 (% of one core) | CPU max | Memory max (MiB) |
|---|---|---|---|
| backend | 32.9 | 40.0 | 97.3 |
| hermes | 129.0 | 130.9 | 528.9 |
| stub-llm | 7.2 | 7.3 | 81.5 |
| web | 0.5 | 0.6 | 11.6 |

**Bottleneck (saturated):** hermes. hermes p90 CPU 129% of one core (max 131%).

## Next bottleneck and its fix

First seen in **Step 3: speed-up 30**: **hermes**. hermes p90 CPU 129% of one core (max 131%).

Hermes's API server runs every session in one process. Add Hermes replicas and route each Application's session to one of them (the session id is stable), or raise its run concurrency.
