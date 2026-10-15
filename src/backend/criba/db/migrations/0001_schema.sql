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
    created_at   TEXT NOT NULL,
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
    created_at        TEXT NOT NULL,
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
                       'tone_hint', 'run_error', 'template_sent')),
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
                       'wants_human', 'frustrated', 'technical_error', 'other')),  -- escalations only
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
