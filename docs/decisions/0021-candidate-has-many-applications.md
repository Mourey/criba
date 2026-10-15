# 0021. A Candidate has many Applications; an Application belongs to every covering Location

- Status: Accepted
- Date: 2026-10-09
- Refines: 0009 and 0014 (the "case record" becomes Candidate plus Application; see `CONTEXT.md`)

## Context
Earlier records used one "case record" per person. Scenarios break that model:
- a Candidate knocked out in March for having no license gets one and writes again in June;
- a Candidate moves from Madrid to Valencia;
- a Candidate in Mexico later applies in Spain.

Each outcome has to remain its own auditable record for GDPR Art. 22 and for funnel metrics, while the person's identity and contact details persist. A Zone can also be covered by more than one Location.

## Decision
- **Candidate** (the person: identity and channel identities) and **Application** (one attempt: answers, outcome, events) are separate.
- **A Candidate has at most one open Application.** A message within 30 days of a Knockout or Abandonment **reopens** that Application, resuming at the failed or pending question. Later than that, it starts a new Application, which may pre-fill answers that don't expire (such as the name) for the Candidate to confirm.
- **An Application belongs to every Location whose Service Area contains the Candidate's Zone.** Recruiters at each of those Locations see it.

## Alternatives considered
- **One Application per Candidate, reopened forever:** mixes outcomes, so a June hire would sit on top of a March knockout in the same record.
- **One Application per Candidate per country:** solves only the cross-country case.
- **Assign each Application to exactly one Location** (the nearest, or one the Candidate picks): needs more geography or another question, and hides the Candidate from neighbouring Locations that could use them.

## Consequences
- Funnel metrics count Applications, not Candidates. A Candidate's Applications are linked for history.
- If several Locations' Recruiters see the same Application, two of them could act on the Candidate at once. How a Recruiter claims an Application is an open question.
- Whether Grupo Sazón organises Recruiters per Location or per city still needs confirming with the client.
- Wording in earlier ADRs ("case record", "applicant", "disqualified") is kept as it was written. `CONTEXT.md` is the current vocabulary.
