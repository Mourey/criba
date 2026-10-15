# 0016. Knockouts are sent at once, with an explanation and a way back

- Status: Accepted
- Date: 2026-10-09
- Amends: 0012 (its list of significant decisions)

## Context
A candidate without a driver's license, or outside the service areas, can't be hired. The owner wants them told at once, with a link to the minimum hiring requirements (an FAQ), the reason behind the requirement, and an invitation to come back. Under 0012 a rejection needs a human to confirm it, and with about 1,000 applications a week that gets in the way of speed. GDPR Art. 22 allows automated decisions where they are necessary for entering a contract, provided there are safeguards: the person can get human intervention, express their view, and contest the decision.

## Decision
- **Objective knockouts are automated.** These are the knock-out criteria the client defines in the screening list. For Grupo Sazón: no driver's license, or a zone outside the service areas.
- **The knockout message is sent in the same conversation and includes:**
  - the reason, in plain words;
  - why the requirement exists (for example, legal and insurance rules for driving);
  - a link to the minimum hiring requirements page or FAQ;
  - an invitation to write again when circumstances change (they get a license, or the service area grows);
  - a way to ask for a person to review the decision.
- **Every knockout is an event** on the case record (`knocked_out`, carrying the criterion and the message sent). Review requests go to recruiters in the web app.
- **All other rejections, hires, offers and terminations** still need a person to confirm them (0012).

## Alternatives considered
- **A person confirms every knockout:** slow, and the recruiters' time is the scarce resource the case is about.

## Consequences
- **Grupo Sazón must document its legal basis** (Art. 22(2)(a) necessity, plus these safeguards) and supply the requirements and FAQ content. Mexico (LFPDPPP) is checked separately.
- Knockout messages come from templates per country and language that the client approves, not from wording the agent improvises.
- A candidate who comes back is matched to their existing record (0014), and screening resumes from the failed criterion.
