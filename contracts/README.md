# Contracts

The interfaces between the build lanes. Every lane builds and mocks against these files. Vocabulary: [`CONTEXT.md`](../CONTEXT.md). Answer fields and canonical values: [Phase 1 detailed spec §3](../docs/case/phase-1-detailed-spec.md).

| File | What it fixes | Owner after wave 0 |
|---|---|---|
| `schema.sql` | SQLite tables, constraints and indexes for every Phase 2 entity. Lane A's first migration is a copy of it. | Lane A (backend data) |
| `tools.json` | Draft 2020-12 JSON Schema for each Screening Agent tool's input and output, and the `POST /internal/tools/{name}` envelope `{tool, args, session_id}` → `{ok: true, value} \| {ok: false, error, hint}`. No tool input has `application_id` or `candidate_id`. | Lane A (agent tools, wave 2) |
| `api.openapi.yaml` | OpenAPI 3.1 for the web app's REST API under `/api` (auth, Applications, Claims, queue, Takeover and its event stream, slots and booking, metrics). Roles in `x-roles`; Operator-only cost fields carry `x-roles: [operator]`. | Lane D (web app and REST API) |
| `webchat.openapi.yaml` | OpenAPI 3.1 for the web chat channel under `/api/chat`, used by the test chat page, the simulator and the demo, plus the sim-only clock at `/api/sim/clock*` (`CRIBA_ENV=sim`). | Lane B (channel); the clock endpoint is implemented by lane D |
| `voice.openapi.yaml` | OpenAPI 3.1 for the two endpoints ElevenLabs calls: the custom-LLM turn `POST /api/voice/v1/chat/completions` (request, `chat.completion.chunk` stream and the `end_call` frame carrying the Takeover marker) and the post-call webhook `POST /api/voice/webhooks/post-call`, each with its own security scheme. Built from the probe captures in `src/tests/fixtures/spikes/005-elevenlabs-custom-llm/captures/`. | Lane B (channel) |

## Change rule

The contracts are frozen after wave 0. Only a contract's owner changes it, in a separate commit, and lists the change in the wave merge notes. Any other lane that needs a change asks the owner (in its merge notes, or directly). Contracts are versioned only through git; there is no version field to bump.

## Check

```sh
src/scripts/check_contracts.sh
```

It loads `schema.sql` into an in-memory SQLite database, checks `tools.json` against the JSON Schema metaschema, and validates all three OpenAPI files (`api.openapi.yaml`, `webchat.openapi.yaml` and `voice.openapi.yaml`). It needs `sqlite3`, `uv` (for `uvx`) and network access the first time `uvx` fetches its tools.

## Mock servers

Lanes D (web app) and E (simulator) develop against Prism mocks generated from the OpenAPI files. Prism answers with each operation's examples.

```sh
# Web app REST API on :4010
npx @stoplight/prism-cli mock contracts/api.openapi.yaml -p 4010

# Web chat channel and sim clock on :4011
npx @stoplight/prism-cli mock contracts/webchat.openapi.yaml -p 4011
```

For example:

```sh
curl -s -H 'Cookie: criba_session=mock' localhost:4010/api/applications
curl -s -X POST localhost:4011/api/chat/conversations \
  -H 'content-type: application/json' -d '{"country":"MX"}'
```

Notes:
- Prism checks request bodies against the schemas, so a malformed request gets a 422 from the mock as it will from the backend.
- Add `-d` (dynamic) to get generated data instead of the examples.
- Prism enforces the security scheme: `/api` routes need any `criba_session` cookie (without one they return the 401 example). It doesn't check the value, so role scoping and cost-field removal only behave for real against the backend. The mock returns the Operator view, cost fields included.
- To pick a named example, send `Prefer: example=<name>`; to force an error response, `Prefer: code=409`.
- Server-sent event endpoints return their example as one static body.
