# 0046. The Approval message skips quiet hours when the Candidate wrote in the last 60 minutes

- Status: Accepted
- Date: 2026-10-10
- Refines: 0038 (the scheduler alone holds actions for quiet hours) and 0039 (held actions)

## Context
In a real demo a Candidate in Mexico City (Zone Polanco) finished Screening at 07:36 local. A Recruiter approved at 07:38, and the `[system] approved` turn that offers the Interview slots sat in `held_actions` until 09:00 local, because 22:00–09:00 is quiet hours. The Candidate had written two minutes earlier, so they were plainly awake; waiting 80 minutes for an answer to a conversation in progress broke the instant experience that the Approval step is meant to deliver. Quiet hours exist so that we do not start a conversation at night; here the Candidate had started it.

The Owner decided to exempt this one message. (ADR 0045 is proposed informally in the ATS integration note and is not in the index, so this ADR takes 0046.)

## Decision
- **The `approved` action skips quiet hours when the Candidate's last inbound message on that Application (`applications.last_inbound_at`) is at most 60 minutes before the scheduler clock's now.** The window is the named constant `ACTIVE_CANDIDATE_WINDOW` (`timedelta(minutes=60)`) in `criba.scheduler.active`, the only place it and the test (`candidate_recently_active`) are written. The boundary is inclusive: exactly 60 minutes counts as active, 60 minutes and one second does not. Time comes from the injected `Clock`, so the sim clock drives it in sim mode.
- **Checked at submit time and again on every `run_due` pass.** `Scheduler.submit_action` dispatches an active Candidate's Approval at once instead of holding it. An `approved` row that is already held is dispatched by the next `run_due_jobs` once the Candidate has written within the window, without waiting for `not_before`. It is still dispatched once (the compare-and-set claim, 0039), dropped if the Application closed or the Candidate opted out, failed after 3 attempts, and the Takeover check in the router is unchanged.
- **The channel is told, not asked to copy the rule.** The router's `approved` handler passes `quiet_hours_exempt=candidate_recently_active(...)` to `Sender.check`, which then skips its `not_now` for quiet hours. The sender does not know the rule or the window; it only honours the flag, and the flag never lifts the opt-out.
- **Nothing else changes.** Stalled nudges, the 48-hour template and every other proactive message keep the strict rule in `criba.scheduler.quiet` (22:00–09:00 local: the Zone's time zone, else the country's, else UTC), whose single-source test stands. The WhatsApp 24-hour window logic is unchanged; an active Candidate is always inside it, so the Interview-offer template path never needs the exemption.

## Alternatives considered
- **Keep holding until 09:00:** the observed behaviour; wrong for a Candidate who is writing right now.
- **Exempt every reply-like or proactive message to an active Candidate:** a nudge to a Candidate who wrote 60 minutes ago is exactly what quiet hours prevent, and the rule was settled once (0038). Only the Approval answers a Candidate who is waiting for it.
- **A longer window (24 hours, WhatsApp's):** too long to mean "awake"; a Candidate who wrote at 21:00 is probably asleep at 02:00.
- **Pass the decision from the scheduler through `Dispatch`, `Channel.handle_event` and the event-action handlers:** deterministic, but it changes four signatures and every fake `Dispatch` for one flag. The router instead evaluates the same function on the same clock.
- **Lift `quiet_hours` in the sender for all `approved` sends:** would send at night to a Candidate who has been silent since the previous day.

## Consequences
- The router and the scheduler each evaluate `candidate_recently_active` for the same `approved` action. If the clock crosses the 60-minute edge between the two calls (milliseconds), the channel answers `not_now`, and the held row's retry path (0039) tries again; a submit that finds no held row raises as it would for a clock crossing 22:00 today.
- A Candidate who writes at night while an Approval is held now gets the offer within one scheduler pass (the loop's interval, 60 seconds by default).
- Tests that approve a Candidate during quiet hours must say when the Candidate last wrote; the existing scheduler tests now have the Candidate silent for two hours.
- No schema or contract change: `last_inbound_at` already exists, and the `held_actions` table is untouched.
