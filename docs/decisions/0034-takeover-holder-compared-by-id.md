# 0034. The Takeover holder is identified by user id, not by name

- Status: Accepted
- Date: 2026-10-09
- Refines: 0020 (the `control` field)

## Context
Under 0020 an Application's `control` is `agent` or `human:<user>`. The frozen API contract exposed the holder to the web app as a name only (`control_user_name`), so the Takeover panel decided "is this me?" by comparing names. Two users with the same display name (two "Ana Torres" Recruiters at different Locations) would each see the other's Takeover as their own and could type into it. This was settled before wave 2 as decision Q37.

## Decision
- **`ApplicationSummary.control_user_id`** is added to `contracts/api.openapi.yaml`: the holder's user id, set exactly when `control` is `human`, otherwise null. Its description says: "Compare this, not the name."
- **The web app compares ids.** `control_user_name` stays for display only.
- The `control` stream event already carried `user_id`; the summary now matches it.

## Alternatives considered
- **Make display names unique:** a rule on data the Client owns, and it breaks on the first rename.
- **Ask the backend "do I hold control?" per view:** an extra request for a fact the summary can carry.

## Consequences
- An additive, optional contract field; web types were regenerated, with a regression test for two users of the same name.
- Any new check of "who holds the Takeover" uses the id.
