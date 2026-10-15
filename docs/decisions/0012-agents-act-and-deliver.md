# 0012. Agents act and deliver; no writes to external systems; significant decisions keep a human

- Status: Accepted, amended by 0016 (objective knockouts are automated, with safeguards)
- Date: 2026-10-09
- Supersedes: 0006

## Context
0006 limited agents to recommending. The owner rejected that. This is a client-facing product, and its agents must do the work themselves: answer and write back to candidates and employees, deliver files and data, and move a person through the workflow. GDPR Art. 22 still prohibits decisions based **solely** on automated processing that produce legal or similarly significant effects on a person, unless an exception applies with safeguards. The EU AI Act requires AI disclosure in conversations with people (from Aug 2026) and human oversight of high-risk recruiting and HR systems (deferred to Dec 2027 for standalone systems). See our industry research.

## Decision
- **Agents act directly.** They write back to candidates and employees on their channel, deliver files and data to them and to internal users, update the case record, and trigger the next step through events (0009). None of this needs approval.
- **No writes to external systems.** That means no ATS, HRIS, payroll or client calendar. Agents deliver data, for example an export file; a human or a later integration pushes it into those systems.
- **Significant decisions are the one exception.** Rejecting a candidate, making a hire or offer, terminating, and disciplinary actions are each recorded as a `decision_pending` event on the case record. A named human confirms it before the agent tells the person. Advancing a candidate, scheduling, answering questions and onboarding steps are not significant decisions.
- **Every conversation discloses AI use** at its start, and on request.
- **Hard rules in every role skill:** no protected attributes or obvious proxies for them, and no emotion inference.

## Alternatives considered
- **Recommend only (0006):** too passive for the product.
- **Fully autonomous, including rejections:** conflicts with GDPR Art. 22 unless the client relies on an exception (such as necessity for a contract, or explicit consent) with safeguards, and would rest the deployer's (Criba client's) legal exposure on our design. We can revisit per client if they document such a basis.

## Consequences
- Outbound messages need no approval gate (H3 is dropped). Message templates and content approved by the client still define what agents may say about pay, contracts and policy.
- Checkpoint H2 narrows to the significant-decision list above. The list is defined per case, in one place, and enforced in code: the router refuses to send an outcome message for a decision without a confirmation event.
- Every action an agent takes is an event on the case record, and that log serves as the audit trail.
