-- Criba Phase 2 data contract (SQLite).
--
-- Owner after wave 0: lane A (backend data). Frozen by spec 005.
-- Change rule: only lane A edits this file, in a separate commit, and lists
-- the change in its wave merge notes. Other lanes ask lane A. Lane A's first
-- migration is a verbatim copy of this file.
--
-- Conventions
--   * Every table is STRICT. Foreign keys are declared; the runtime must run
--     `PRAGMA foreign_keys = ON` on every connection (WAL mode is also a
--     runtime setting, not part of this file).
--   * Vocabulary follows CONTEXT.md. Answer columns follow
--     docs/case/phase-1-detailed-spec.md section 3 and hold canonical values,
--     never the Candidate's language.
--   * Timestamps are ISO 8601 UTC TEXT ('2026-10-09T10:00:00Z'). Dates are
--     'YYYY-MM-DD'. Local times are derived from the Zone/Location time zone.
--   * Closed sets are snake_case CHECK constraints. Display labels live in
--     the web app, not here.
--   * Sets (availability, schedule, platforms) are JSON arrays in TEXT,
--     checked with json_valid().
--   * Messages and events use INTEGER ids so they double as read cursors.
--
-- Stages (applications.stage)
--   Screening substages, in Phase 1 order:
--     greeting, name, zone, driver_license, availability,
--     preferred_schedule, experience, start_date, confirm
--   After Screening:
--     qualified        passed Screening, waiting for Approval
--     approved         Approval ended Approved; the Candidate may book
--     interview_booked an Interview is booked (the Application stays open)
--   Approval is the Recruiter decision on a qualified Application (a queue
--   item of kind 'approval'), not a stage of its own.
--   A closed Application keeps the stage it reached; `outcome` says how it
--   ended (Knockout, Rejection, Abandoned, Withdrawn).

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Clients, Locations, Zones, Service Areas
-- ---------------------------------------------------------------------------

CREATE TABLE clients (
    id          TEXT PRIMARY KEY,                 -- e.g. 'grupo_sazon'
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
) STRICT;

CREATE TABLE locations (
    id          TEXT PRIMARY KEY,                 -- e.g. 'loc_mad_chamberi'
    client_id   TEXT NOT NULL REFERENCES clients(id),
    name        TEXT NOT NULL,
    country     TEXT NOT NULL CHECK (country IN ('ES', 'MX')),
    city        TEXT NOT NULL,
    address     TEXT,
    timezone    TEXT NOT NULL,                    -- IANA, e.g. 'Europe/Madrid'
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at  TEXT NOT NULL
) STRICT;

CREATE TABLE zones (
    id          TEXT PRIMARY KEY,                 -- e.g. 'zone_es_madrid_chamberi'
    country     TEXT NOT NULL CHECK (country IN ('ES', 'MX')),
    city        TEXT NOT NULL,
    name        TEXT NOT NULL,                    -- neighbourhood or city name
    aliases     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(aliases)),  -- JSON array of strings
    timezone    TEXT NOT NULL
) STRICT;

-- One Service Area per Location (CONTEXT.md).
CREATE TABLE service_areas (
    id          TEXT PRIMARY KEY,
    location_id TEXT NOT NULL UNIQUE REFERENCES locations(id) ON DELETE CASCADE
) STRICT;

CREATE TABLE service_area_zones (
    service_area_id TEXT NOT NULL REFERENCES service_areas(id) ON DELETE CASCADE,
    zone_id         TEXT NOT NULL REFERENCES zones(id),
    PRIMARY KEY (service_area_id, zone_id)
) STRICT;

-- ---------------------------------------------------------------------------
-- Users (Recruiters and Operators), auth sessions
-- ---------------------------------------------------------------------------

CREATE TABLE users (
    id            TEXT PRIMARY KEY,
    client_id     TEXT REFERENCES clients(id),    -- NULL for Operators
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('recruiter', 'operator')),
    password_hash TEXT NOT NULL,                  -- argon2
    active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at    TEXT NOT NULL,
    CHECK (role = 'operator' OR client_id IS NOT NULL)
) STRICT;

-- Recruiter scope. Operators see every Location and need no rows here.
CREATE TABLE user_locations (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, location_id)
) STRICT;

CREATE TABLE auth_sessions (
    id          TEXT PRIMARY KEY,                 -- hash of the session cookie value
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    revoked_at  TEXT
) STRICT;

-- ---------------------------------------------------------------------------
-- Candidates
-- ---------------------------------------------------------------------------

CREATE TABLE candidates (
    id                    TEXT PRIMARY KEY,       -- e.g. 'cand_01J...'
    client_id             TEXT NOT NULL REFERENCES clients(id),
    full_name             TEXT,                   -- last confirmed name, for display
    phone                 TEXT,                   -- E.164; drives country (+34 ES, +52 MX)
    country               TEXT CHECK (country IN ('ES', 'MX')),
    language              TEXT CHECK (language IN ('es-ES', 'es-MX', 'en')),  -- of the last exchange
    source                TEXT NOT NULL DEFAULT 'inbound' CHECK (source IN ('inbound', 'import')),
    -- Imported Candidates only; NULL for inbound ones.
    import_state          TEXT CHECK (import_state IN ('not_contacted', 'contacted', 'unresponsive')),
    opted_out             INTEGER NOT NULL DEFAULT 0 CHECK (opted_out IN (0, 1)),
    opted_out_at          TEXT,
    erasure_requested_at  TEXT,                   -- handled outside the chat
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    CHECK (source = 'import' OR import_state IS NULL),
    CHECK ((opted_out = 1) = (opted_out_at IS NOT NULL))
) STRICT;

-- Channel identities (web chat conversation, WhatsApp number).
CREATE TABLE candidate_identities (
    candidate_id TEXT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    channel      TEXT NOT NULL CHECK (channel IN ('webchat', 'whatsapp', 'voice')),
    external_id  TEXT NOT NULL,                   -- conversation_id or wa_id
    created_at   TEXT NOT NULL, client TEXT CHECK (client IN ('test_chat', 'simulator', 'demo')), persona_id TEXT CHECK (persona_id IS NULL OR (length(persona_id) BETWEEN 1 AND 64 AND persona_id NOT GLOB '*[^a-z0-9_]*')),
    PRIMARY KEY (channel, external_id)
) STRICT;

-- ---------------------------------------------------------------------------
-- Applications
-- ---------------------------------------------------------------------------

CREATE TABLE applications (
    id                    TEXT PRIMARY KEY,       -- also the Hermes session_id (ADR 0018)
    candidate_id          TEXT NOT NULL REFERENCES candidates(id),
    client_id             TEXT NOT NULL REFERENCES clients(id),
    channel               TEXT NOT NULL CHECK (channel IN ('webchat', 'whatsapp', 'voice')),
    country               TEXT CHECK (country IN ('ES', 'MX')),
    language              TEXT CHECK (language IN ('es-ES', 'es-MX', 'en')),

    status                TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    stage                 TEXT NOT NULL DEFAULT 'greeting' CHECK (stage IN (
                              'greeting', 'name', 'zone', 'driver_license', 'availability',
                              'preferred_schedule', 'experience', 'start_date', 'confirm',
                              'qualified', 'approved', 'interview_booked')),
    outcome               TEXT CHECK (outcome IN ('knockout', 'rejection', 'abandoned', 'withdrawn')),
    knockout_criterion    TEXT CHECK (knockout_criterion IN ('driver_license', 'zone')),
    rejection_reason      TEXT CHECK (rejection_reason IN ('no_show', 'failed_call', 'role_filled', 'other')),
    rejection_note        TEXT,

    -- Takeover (ADR 0020): who holds the chat.
    control               TEXT NOT NULL DEFAULT 'agent' CHECK (control IN ('agent', 'human')),
    control_user_id       TEXT REFERENCES users(id),
    control_since         TEXT,

    -- Claim (CONTEXT.md): at most one Recruiter at a time.
    claim_user_id         TEXT REFERENCES users(id),
    claimed_at            TEXT,
    claim_last_action_at  TEXT,

    -- Answers (Phase 1 section 3), canonical values.
    full_name             TEXT,
    zone_id               TEXT REFERENCES zones(id),
    zone_input            TEXT,                   -- the Candidate's own words
    driver_license        INTEGER CHECK (driver_license IN (0, 1)),
    driver_license_input  TEXT,                   -- the Candidate's own words
    availability          TEXT CHECK (availability IS NULL OR json_valid(availability)),          -- ["full_time","part_time","weekends"]
    preferred_schedule    TEXT CHECK (preferred_schedule IS NULL OR json_valid(preferred_schedule)), -- ["morning","afternoon","evening","flexible"]
    experience_years      REAL CHECK (experience_years IS NULL OR experience_years >= 0),
    experience_platforms  TEXT CHECK (experience_platforms IS NULL OR json_valid(experience_platforms)), -- ["glovo","uber_eats","just_eat","rappi","didi_food","other"]
    start_date            TEXT,                   -- YYYY-MM-DD, Candidate's local date
    start_date_far        INTEGER NOT NULL DEFAULT 0 CHECK (start_date_far IN (0, 1)), -- > 60 days out, flagged for Recruiters
    invalid_attempts      INTEGER NOT NULL DEFAULT 0 CHECK (invalid_attempts >= 0),     -- misses on the pending question

    -- Flags maintained by the scheduler.
    stalled_since         TEXT,
    nudges_sent           INTEGER NOT NULL DEFAULT 0 CHECK (nudges_sent BETWEEN 0 AND 3),
    qualified_waiting     INTEGER NOT NULL DEFAULT 0 CHECK (qualified_waiting IN (0, 1)),
    claimed_idle          INTEGER NOT NULL DEFAULT 0 CHECK (claimed_idle IN (0, 1)),

    -- Summary (structured part by code, prose by Sonnet).
    summary_structured    TEXT CHECK (summary_structured IS NULL OR json_valid(summary_structured)),
    summary_prose         TEXT,
    summary_at            TEXT,

    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    last_inbound_at       TEXT,
    last_outbound_at      TEXT,
    qualified_at          TEXT,
    approved_at           TEXT,
    approved_by           TEXT REFERENCES users(id),
    closed_at             TEXT,
    reopened_count        INTEGER NOT NULL DEFAULT 0 CHECK (reopened_count >= 0),
    last_reopened_at      TEXT,

    CHECK ((status = 'closed') = (outcome IS NOT NULL)),
    CHECK ((status = 'closed') = (closed_at IS NOT NULL)),
    CHECK ((outcome = 'knockout') = (knockout_criterion IS NOT NULL)),
    CHECK ((outcome = 'rejection') = (rejection_reason IS NOT NULL)),
    CHECK (rejection_reason IS NOT 'other' OR rejection_note IS NOT NULL),
    CHECK ((control = 'human') = (control_user_id IS NOT NULL)),
    CHECK ((claim_user_id IS NULL) = (claimed_at IS NULL))
) STRICT;

-- A Candidate has at most one open Application.
CREATE UNIQUE INDEX applications_one_open_per_candidate
    ON applications (candidate_id) WHERE status = 'open';
CREATE INDEX applications_by_stage ON applications (status, stage, updated_at);
CREATE INDEX applications_by_outcome ON applications (outcome, closed_at);
CREATE INDEX applications_by_claim ON applications (claim_user_id) WHERE claim_user_id IS NOT NULL;
CREATE INDEX applications_by_candidate ON applications (candidate_id, created_at);

-- An Application belongs to every Location whose Service Area covers its Zone (ADR 0021).
CREATE TABLE application_locations (
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    location_id    TEXT NOT NULL REFERENCES locations(id),
    PRIMARY KEY (application_id, location_id)
) STRICT;
CREATE INDEX application_locations_by_location ON application_locations (location_id, application_id);

-- ---------------------------------------------------------------------------
-- Messages (verbatim) and events (timeline)
-- ---------------------------------------------------------------------------

CREATE TABLE messages (
    id                INTEGER PRIMARY KEY,        -- monotonic; the web chat read cursor
    application_id    TEXT REFERENCES applications(id),  -- NULL only for outreach before an Application exists
    candidate_id      TEXT NOT NULL REFERENCES candidates(id),
    channel           TEXT NOT NULL CHECK (channel IN ('webchat', 'whatsapp', 'voice')),
    direction         TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    sender            TEXT NOT NULL CHECK (sender IN ('candidate', 'agent', 'human', 'system')),
    sender_user_id    TEXT REFERENCES users(id),  -- set when sender = 'human' (Takeover)
    client_message_id TEXT,                       -- inbound idempotency key (web chat message_id / WhatsApp wamid)
    content_type      TEXT NOT NULL DEFAULT 'text' CHECK (content_type IN ('text', 'interactive', 'button_reply', 'media', 'template')),
    body              TEXT NOT NULL,              -- verbatim; '' allowed for media-only
    interactive       TEXT CHECK (interactive IS NULL OR json_valid(interactive)), -- {"style":"buttons"|"list","options":[{"id","label"}]}
    reply_option_id   TEXT,                       -- for button_reply
    template_name     TEXT,                       -- for template
    media_type        TEXT CHECK (media_type IN ('audio', 'image', 'video', 'document', 'sticker')),
    run_id            TEXT,                       -- Hermes run that produced an agent message
    created_at        TEXT NOT NULL, modality TEXT NOT NULL DEFAULT 'typed' CHECK (modality IN ('typed','spoken')),
    CHECK ((sender = 'human') = (sender_user_id IS NOT NULL)),
    CHECK ((direction = 'inbound') = (sender = 'candidate'))
) STRICT;

CREATE UNIQUE INDEX messages_idempotency
    ON messages (channel, client_message_id) WHERE client_message_id IS NOT NULL;
CREATE INDEX messages_by_application ON messages (application_id, created_at, id);
CREATE INDEX messages_by_candidate ON messages (candidate_id, id);

CREATE TABLE events (
    id             INTEGER PRIMARY KEY,
    application_id TEXT REFERENCES applications(id),  -- NULL for Candidate-level events (outreach, opt-out lifted)
    candidate_id   TEXT NOT NULL REFERENCES candidates(id),
    kind           TEXT NOT NULL CHECK (kind IN (
                       -- lifecycle
                       'application_started', 'stage_changed', 'reopened',
                       'answer_recorded', 'answer_invalid', 'answer_ambiguous',
                       'screening_finished', 'summary_written',
                       -- Outcomes
                       'knockout', 'rejection', 'abandoned', 'withdrawn',
                       -- Review, Escalation, Approval
                       'review_requested', 'review_upheld', 'review_overturned',
                       'escalation_raised', 'escalation_dismissed',
                       'approval_requested', 'approved',
                       -- Claim
                       'claim_taken', 'claim_released', 'claim_taken_over',
                       -- Takeover
                       'takeover_started', 'takeover_handed_back',
                       -- Stalled and follow-ups
                       'stalled', 'nudge_sent', 'flag_qualified_waiting', 'flag_claimed_idle',
                       -- Interview
                       'slots_offered', 'booking_link_sent', 'interview_booked',
                       'interview_rescheduled', 'interview_slot_released',
                       -- Candidate-level
                       'outreach_sent', 'import_unresponsive', 'opted_out', 'opt_out_lifted',
                       'erasure_requested',
                       -- Runtime
                       'tone_hint', 'run_error', 'template_sent', 'system_turn')),
    actor          TEXT NOT NULL CHECK (actor IN ('agent', 'system', 'candidate', 'user')),
    user_id        TEXT REFERENCES users(id),
    data           TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(data)),
    created_at     TEXT NOT NULL,
    CHECK ((actor = 'user') = (user_id IS NOT NULL))
) STRICT;

CREATE INDEX events_by_application ON events (application_id, created_at, id);
CREATE INDEX events_by_kind ON events (kind, created_at);

-- ---------------------------------------------------------------------------
-- Review queue: Approvals, Reviews, Escalations
-- ---------------------------------------------------------------------------

CREATE TABLE queue_items (
    id             TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    kind           TEXT NOT NULL CHECK (kind IN ('approval', 'review', 'escalation')),
    status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    reason         TEXT CHECK (reason IN (
                       'repeated_invalid_answers', 'abuse', 'unanswered_question',
                       'wants_human', 'frustrated', 'technical_error', 'other',
                       'unreadable')),  -- escalations only
    note           TEXT,                          -- agent's or Candidate's words
    resolution     TEXT CHECK (resolution IN (
                       'approved', 'rejected',    -- approval
                       'upheld', 'overturned',    -- review
                       'dismissed', 'takeover')), -- escalation
    resolution_note TEXT,
    resolved_by    TEXT REFERENCES users(id),
    created_at     TEXT NOT NULL,
    resolved_at    TEXT,
    CHECK ((status = 'resolved') = (resolution IS NOT NULL)),
    CHECK ((status = 'resolved') = (resolved_at IS NOT NULL)),
    CHECK ((kind = 'escalation') = (reason IS NOT NULL)),
    CHECK (resolution IS NULL
           OR (kind = 'approval'   AND resolution IN ('approved', 'rejected'))
           OR (kind = 'review'     AND resolution IN ('upheld', 'overturned'))
           OR (kind = 'escalation' AND resolution IN ('dismissed', 'takeover')))
) STRICT;

-- At most one open item of each kind per Application.
CREATE UNIQUE INDEX queue_items_one_open
    ON queue_items (application_id, kind) WHERE status = 'open';
CREATE INDEX queue_items_open ON queue_items (status, kind, created_at);

-- ---------------------------------------------------------------------------
-- Interview slots and bookings (ADR 0030)
-- ---------------------------------------------------------------------------

CREATE TABLE interview_slots (
    id          TEXT PRIMARY KEY,
    location_id TEXT NOT NULL REFERENCES locations(id),
    starts_at   TEXT NOT NULL,
    ends_at     TEXT NOT NULL,
    created_by  TEXT REFERENCES users(id),        -- NULL for seeded slots
    created_at  TEXT NOT NULL,
    cancelled_at TEXT,
    CHECK (ends_at > starts_at)
) STRICT;
CREATE INDEX interview_slots_by_location ON interview_slots (location_id, starts_at);

-- Slots offered in the chat; book_interview accepts only a slot offered to that Application.
CREATE TABLE slot_offers (
    ref            TEXT PRIMARY KEY,              -- opaque, returned to the agent as slot_ref
    application_id TEXT NOT NULL REFERENCES applications(id),
    slot_id        TEXT NOT NULL REFERENCES interview_slots(id),
    offered_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL
) STRICT;
CREATE INDEX slot_offers_by_application ON slot_offers (application_id, offered_at);

CREATE TABLE bookings (
    id             TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    slot_id        TEXT NOT NULL REFERENCES interview_slots(id),
    status         TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'released', 'cancelled')),
    booked_via     TEXT NOT NULL CHECK (booked_via IN ('chat', 'booking_page')),
    ics_uid        TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    released_at    TEXT,
    CHECK ((status = 'booked') = (released_at IS NULL))
) STRICT;

-- The slot lock: one active booking per slot, and one per Application.
CREATE UNIQUE INDEX bookings_one_per_slot ON bookings (slot_id) WHERE status = 'booked';
CREATE UNIQUE INDEX bookings_one_per_application ON bookings (application_id) WHERE status = 'booked';

-- ---------------------------------------------------------------------------
-- Usage and cost ledger (ADR 0019, 0029, 0031)
-- ---------------------------------------------------------------------------

CREATE TABLE usage (
    id                  INTEGER PRIMARY KEY,
    ts                  TEXT NOT NULL,
    environment         TEXT NOT NULL CHECK (environment IN ('prod', 'sim', 'test')),
    source              TEXT NOT NULL DEFAULT 'live' CHECK (source IN ('live', 'replay')),
    dataset             TEXT,                     -- comparison dataset name when source = 'replay'
    client_id           TEXT REFERENCES clients(id),
    location_id         TEXT REFERENCES locations(id),
    candidate_id        TEXT REFERENCES candidates(id),
    application_id      TEXT REFERENCES applications(id),
    stage               TEXT,                     -- applications.stage at the time of the call
    profile             TEXT,                     -- Hermes profile, e.g. 'criba-recruiter'
    run_id              TEXT,
    session_id          TEXT,
    kind                TEXT NOT NULL CHECK (kind IN ('run', 'subagent', 'auxiliary', 'jev', 'speech')),
    provider            TEXT NOT NULL,            -- 'openrouter', 'typesafe', 'openai', 'stub'
    requested_model     TEXT,
    model               TEXT NOT NULL,            -- the model that actually served the call
    input_tokens        INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens       INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0 CHECK (cache_read_tokens >= 0),
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0 CHECK (cache_write_tokens >= 0),
    audio_seconds       REAL CHECK (audio_seconds IS NULL OR audio_seconds >= 0),   -- speech rows
    est_cost_usd        REAL NOT NULL DEFAULT 0 CHECK (est_cost_usd >= 0),
    reconciled_cost_usd REAL CHECK (reconciled_cost_usd IS NULL OR reconciled_cost_usd >= 0),
    latency_ms          INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    status              TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'error', 'timeout')),
    error               TEXT,
    CHECK (source = 'live' OR dataset IS NOT NULL)
) STRICT;

CREATE INDEX usage_by_ts ON usage (environment, ts);
CREATE INDEX usage_by_application ON usage (application_id, ts);
CREATE INDEX usage_by_kind_model ON usage (kind, model, ts);
CREATE UNIQUE INDEX usage_one_row_per_run
    ON usage (run_id, kind) WHERE run_id IS NOT NULL AND kind = 'run';

-- ---------------------------------------------------------------------------
-- Held actions: router actions held for quiet hours (Q41), so a restart keeps them
-- ---------------------------------------------------------------------------

CREATE TABLE held_actions (
    id             INTEGER PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    action         TEXT NOT NULL CHECK (action IN ('approved')),  -- the router action to run; one value today
    not_before     TEXT NOT NULL,                 -- UTC; when quiet hours end for the Candidate
    attempts       INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    status         TEXT NOT NULL DEFAULT 'held' CHECK (status IN ('held', 'dispatched', 'dropped', 'failed')),
    last_error     TEXT,                          -- the last failed attempt's error
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
) STRICT;

-- At most one held row per action per Application; finished rows stay as history.
CREATE UNIQUE INDEX held_actions_one_held
    ON held_actions (application_id, action) WHERE status = 'held';
CREATE INDEX held_actions_due ON held_actions (status, not_before);

-- ---------------------------------------------------------------------------
-- Hand-backs: the recap a Takeover leaves for the agent's next turn. It quotes the
-- transcript (or is the team member's own text), so it lives here, not in events.data:
-- events hold only ids, codes and canonical values, and an Erasure request deletes these
-- rows with the Candidate's messages. The takeover_handed_back event references the row.
-- ---------------------------------------------------------------------------

CREATE TABLE handbacks (
    id             INTEGER PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    user_id        TEXT REFERENCES users(id),     -- who handed the chat back
    summary        TEXT NOT NULL,                 -- free text: the team member's, or a recap generated from the transcript
    generated      INTEGER NOT NULL DEFAULT 0 CHECK (generated IN (0, 1)),
    created_at     TEXT NOT NULL
) STRICT;

CREATE INDEX handbacks_by_application ON handbacks (application_id, id);

-- ---------------------------------------------------------------------------
-- Message signals: what the Jev router read from one typed Candidate message (ADR 0051).
-- Language, switch and tone probabilities, tied to the message, never to the Candidate. No
-- Candidate text is stored here. Purged after a fixed retention period (180 days by default)
-- by the scheduler, and deleted with the message on an Erasure request. Never read by a
-- Knockout, Rejection, ranking or any other decision. `fired`, `hint` and `escalation` are
-- the codes the policy derived at the time (`policy_version`), so a later change of
-- thresholds does not rewrite history. `asks`, `asks_confidence` and `language_reason` (ADR
-- 0061) were added by ALTER TABLE in migration 0010, and `unreadable` (ADR 0063) in migration
-- 0013, which is why their text follows the created_at line: this is the text sqlite_master
-- stores. `unreadable` is read back only by the router's own step, to count a run of
-- unreadable messages before an Escalation (ADR 0063). `intent`, `intent_confidence` and
-- `malice` (ADR 0071) came in migration 0014 the same way: what the message is trying to do,
-- the Choice's confidence, and the probability that it tries to manipulate the assistant (the
-- product label is Manipulation). NULL on a failed call, on a spoken turn and on rows written
-- before the migration. Display only, for Operators: no decision reads them.
-- ---------------------------------------------------------------------------

CREATE TABLE message_signals (
    id             INTEGER PRIMARY KEY,
    message_id     INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    application_id TEXT NOT NULL REFERENCES applications(id),
    stage          TEXT,                          -- applications.stage when the message arrived
    status         TEXT NOT NULL CHECK (status IN ('ok', 'error', 'timeout')),
    model          TEXT,                          -- the model that served the call
    policy_version TEXT,
    lang           TEXT CHECK (lang IN ('es', 'en', 'other', 'unclear')),
    lang_confidence REAL CHECK (lang_confidence IS NULL OR lang_confidence BETWEEN 0 AND 1),
    switch         REAL CHECK (switch IS NULL OR switch BETWEEN 0 AND 1),
    frustrated     REAL CHECK (frustrated IS NULL OR frustrated BETWEEN 0 AND 1),
    confused       REAL CHECK (confused IS NULL OR confused BETWEEN 0 AND 1),
    wants_human    REAL CHECK (wants_human IS NULL OR wants_human BETWEEN 0 AND 1),
    abusive        REAL CHECK (abusive IS NULL OR abusive BETWEEN 0 AND 1),
    first_message  INTEGER NOT NULL DEFAULT 0 CHECK (first_message IN (0, 1)),
    language_set   TEXT CHECK (language_set IN ('es-ES', 'es-MX', 'en')),  -- the language this message stored, if it changed one
    fired          TEXT NOT NULL DEFAULT '[]',    -- JSON array of the signals above their thresholds
    hint           TEXT,                          -- the tone hint code given to the agent, if any
    escalation     TEXT CHECK (escalation IN ('wants_human', 'abuse')),  -- the Escalation this message raised, if any
    created_at     TEXT NOT NULL
, asks TEXT CHECK (asks IN ('es', 'en', 'none')), asks_confidence REAL CHECK (asks_confidence IS NULL OR asks_confidence BETWEEN 0 AND 1), language_reason TEXT CHECK (language_reason IN ('first_message', 'switch', 'request', 'variant')), unreadable REAL CHECK (unreadable IS NULL OR unreadable BETWEEN 0 AND 1), intent TEXT CHECK (intent IN ('answering', 'asking', 'off_topic', 'wants_to_stop', 'steering', 'other')), intent_confidence REAL CHECK (intent_confidence IS NULL OR intent_confidence BETWEEN 0 AND 1), malice REAL CHECK (malice IS NULL OR malice BETWEEN 0 AND 1)) STRICT;

CREATE INDEX message_signals_by_application ON message_signals (application_id, id);
CREATE INDEX message_signals_by_ts ON message_signals (created_at);

-- ---------------------------------------------------------------------------
-- Voice sessions: one row per voice call on the web chat (voice agent, parent R7).
-- Who called, in which agent country, when and for how long. No text, no audio and nothing
-- derived from audio is stored here; the call's turns are messages rows with
-- modality = 'spoken'. elevenlabs_conversation_id joins the post-call webhook and is written
-- once. messages.modality above was added by ALTER TABLE in migration 0008, which is why its
-- text sits on the created_at line: this is the text sqlite_master stores.
-- ---------------------------------------------------------------------------

CREATE TABLE voice_sessions (
    id                         TEXT PRIMARY KEY,           -- 'vs_<32 hex>'
    conversation_id            TEXT NOT NULL,              -- candidate_identities.external_id of the web chat conversation
    candidate_id               TEXT NOT NULL REFERENCES candidates(id),
    country                    TEXT NOT NULL CHECK (country IN ('ES', 'MX')),  -- the agent's country
    elevenlabs_conversation_id TEXT,                       -- the post-call webhook's join key (R19); written once
    started_at                 TEXT NOT NULL,
    ended_at                   TEXT,
    seconds                    REAL CHECK (seconds IS NULL OR seconds >= 0)
) STRICT;

CREATE INDEX voice_sessions_by_conversation ON voice_sessions (conversation_id, started_at);
CREATE INDEX voice_sessions_live ON voice_sessions (started_at) WHERE ended_at IS NULL;
CREATE UNIQUE INDEX voice_sessions_by_elevenlabs
    ON voice_sessions (elevenlabs_conversation_id) WHERE elevenlabs_conversation_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Tool calls: one row per agent tool call through /internal/tools (the Operator's trace, ADR 0069).
-- Times, outcome and error code, the argument names with canonical values only (never free
-- text, ADR 0035), and the run that made the call once the run returned or failed. Retained
-- as long as message_signals; a row goes with its Application.
-- ---------------------------------------------------------------------------

CREATE TABLE tool_calls (
    id             INTEGER PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    run_id         TEXT,                          -- the run that made the call; set when the run returns or fails
    run_closed     INTEGER NOT NULL DEFAULT 0 CHECK (run_closed IN (0, 1)),  -- 1: a failed run without an id closed this call, so no later run claims it
    tool           TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    duration_ms    INTEGER NOT NULL CHECK (duration_ms >= 0),
    outcome        TEXT NOT NULL CHECK (outcome IN ('ok', 'invalid', 'knockout', 'error')),
    error          TEXT,                          -- one of the contracts/tools.json error codes, null when ok
    args           TEXT NOT NULL DEFAULT '{}',    -- JSON: argument names with canonical values or null, never free text (ADR 0035, ADR 0069)
    created_at     TEXT NOT NULL
) STRICT;

CREATE INDEX tool_calls_by_application ON tool_calls (application_id, id);
CREATE INDEX tool_calls_by_run ON tool_calls (run_id);
CREATE INDEX tool_calls_by_ts ON tool_calls (created_at);

-- ---------------------------------------------------------------------------
-- Owed turns: one row per inbound Candidate message we still owe a reply to (ADR 0073), so a
-- failed or interrupted turn is retried, and resumed after a restart. Written when the router
-- accepts a message that starts a run, deleted when the turn is answered, dropped under a
-- Takeover. Holds the run input (Candidate text): a row goes with its message.
-- ---------------------------------------------------------------------------

CREATE TABLE owed_turns (
    message_id      INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,  -- the inbound Candidate message we owe a reply to
    application_id  TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    channel         TEXT NOT NULL CHECK (channel IN ('webchat', 'whatsapp')),  -- the reply's channel; spoken turns are never owed (ADR 0073)
    turn_input      TEXT NOT NULL,                -- the agent's input as built at accept time (the message as run_input gives it, or the tap result)
    run_input       TEXT,                         -- the exact input first POSTed to Hermes; null until then. A re-POST under the same key must be byte-identical
    idempotency_key TEXT NOT NULL,                -- the key the next attempt POSTs with: the message_id, or a new one after Hermes reported the run failed
    after_event_id  INTEGER,                      -- TurnRequest.after_event_id: the last event before a tap was recorded
    booking_url     TEXT,                         -- an "other time" tap's link (ADR 0064)
    booking_message TEXT,
    typed           INTEGER NOT NULL DEFAULT 0 CHECK (typed IN (0, 1)),  -- 1: a typed text message, read by Jev before its first run
    attempts        INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),    -- failed attempts so far
    status          TEXT NOT NULL DEFAULT 'owed' CHECK (status IN ('owed', 'exhausted')),
    holding_sent    INTEGER NOT NULL DEFAULT 0 CHECK (holding_sent IN (0, 1)),  -- the holding message went out in this outage
    next_attempt_at TEXT NOT NULL,                -- UTC; when the recovery may run it (a lease while the first attempt runs)
    last_error      TEXT CHECK (last_error IS NULL OR last_error IN ('error', 'timeout')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
) STRICT;

CREATE INDEX owed_turns_by_application ON owed_turns (application_id, message_id);
CREATE INDEX owed_turns_due ON owed_turns (status, next_attempt_at);
