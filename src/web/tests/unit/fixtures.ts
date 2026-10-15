// Typed fixtures shaped like the examples in contracts/api.openapi.yaml. Because they are typed
// from the generated schema, a contract change that breaks them breaks the type-check.
import type {
  ApplicationDetail,
  ApplicationSummary,
  ClockState,
  EconomicsMetrics,
  Location,
  Me,
  ModelComparison,
  QueueItem,
  ResultsMetrics,
  Slot,
} from "../../src/api/types.ts";

// The users and Locations below match what the backend seeds (criba.db.seed): one Operator, and
// one Recruiter per city scoped to that city's Locations. Puebla has three.
export const puebla = { id: "loc_mx_puebla_centro", name: "Sazón Puebla Centro", country: "MX", city: "Puebla" } as const;
export const pueblaAngelopolis = { id: "loc_mx_puebla_angelopolis", name: "Sazón Puebla Angelópolis", country: "MX", city: "Puebla" } as const;
export const pueblaLaPaz = { id: "loc_mx_puebla_la_paz", name: "Sazón Puebla La Paz", country: "MX", city: "Puebla" } as const;

export const locations: Location[] = [
  { ...puebla, timezone: "America/Mexico_City", address: "(synthetic) Calle Ejemplo 2, Centro, Puebla" },
  {
    id: "loc_es_madrid_chamberi",
    name: "Sazón Madrid Chamberí",
    country: "ES",
    city: "Madrid",
    timezone: "Europe/Madrid",
    address: "(synthetic) Calle Ejemplo 1, Chamberí, Madrid",
  },
];

export const operatorMe: Me = {
  user: { id: "usr_operator", name: "Operator (synthetic)", email: "operator@criba.example", role: "operator" },
  locations: [],
};

export const recruiterMe: Me = {
  user: { id: "usr_rec_puebla", name: "Recruiter Puebla (synthetic)", email: "recruiter.puebla@sazon.example", role: "recruiter" },
  locations: [pueblaAngelopolis, puebla, pueblaLaPaz],
};

export function summary(overrides: Partial<ApplicationSummary> = {}): ApplicationSummary {
  return {
    id: "app_01JA2B3C4D5E6F7G8H9J0K",
    candidate: { id: "cand_01JA2B", full_name: "Luis Hernández", phone: "+525512345678", country: "MX", language: "es-MX", opted_out: false },
    channel: "webchat",
    status: "open",
    stage: "qualified",
    outcome: null,
    knockout_criterion: null,
    rejection_reason: null,
    zone: { id: "zone_mx_puebla_centro", name: "Centro", city: "Puebla", country: "MX" },
    locations: [puebla],
    claim: null,
    control: "agent",
    control_user_id: null,
    control_user_name: null,
    flags: [],
    created_at: "2026-10-09T10:00:00Z",
    updated_at: "2026-10-09T10:04:00Z",
    closed_at: null,
    time_in_stage_seconds: 60,
    total_seconds: 240,
    stage_durations_seconds: { greeting: 20, name: 15, zone: 30, driver_license: 10, availability: 25 },
    ...overrides,
  };
}

const detailAnswers: ApplicationDetail["answers"] = {
  full_name: "Luis Hernández",
  zone: { id: "zone_mx_puebla_centro", name: "Centro", city: "Puebla", country: "MX" },
  driver_license: true,
  availability: ["full_time"],
  preferred_schedule: ["morning", "afternoon"],
  experience_years: 2,
  experience_platforms: ["rappi", "didi_food"],
  start_date: "2026-10-12",
};

export function detail(overrides: Partial<ApplicationDetail> = {}): ApplicationDetail {
  return {
    ...summary(),
    answers: detailAnswers,
    // Like the backend: one entry per field, the Candidate's words only for zone and driver_license.
    answer_status: [
      { field: "full_name", status: "valid", raw_input: null, attempts: 1, recorded_at: "2026-10-09T10:00:30Z", note: null },
      { field: "zone", status: "valid", raw_input: "Vivo en Centro, Puebla", attempts: 1, recorded_at: "2026-10-09T10:00:50Z", note: null },
      { field: "driver_license", status: "valid", raw_input: "sí, tengo licencia", attempts: 1, recorded_at: "2026-10-09T10:01:10Z", note: null },
      { field: "availability", status: "valid", raw_input: null, attempts: 1, recorded_at: "2026-10-09T10:01:40Z", note: null },
      { field: "preferred_schedule", status: "valid", raw_input: null, attempts: 1, recorded_at: "2026-10-09T10:02:10Z", note: null },
      { field: "experience_years", status: "valid", raw_input: null, attempts: 1, recorded_at: "2026-10-09T10:02:40Z", note: null },
      { field: "experience_platforms", status: "valid", raw_input: null, attempts: 1, recorded_at: "2026-10-09T10:03:00Z", note: null },
      { field: "start_date", status: "flagged", raw_input: null, attempts: 2, recorded_at: "2026-10-09T10:03:30Z", note: "more than 60 days out" },
    ],
    messages: [
      { id: 1, direction: "inbound", sender: "candidate", sender_name: null, content_type: "text", body: "Hola, quiero trabajar de repartidor", interactive: null, created_at: "2026-10-09T10:00:00Z" },
      { id: 2, direction: "outbound", sender: "agent", sender_name: null, content_type: "text", body: "¡Hola! Soy el asistente virtual de Grupo Sazón", interactive: null, created_at: "2026-10-09T10:00:02Z" },
      {
        id: 9,
        direction: "outbound",
        sender: "agent",
        sender_name: null,
        content_type: "interactive",
        body: "¿Qué disponibilidad tienes?",
        interactive: { style: "buttons", options: [{ id: "full_time", label: "Tiempo completo" }, { id: "part_time", label: "Medio tiempo" }] },
        created_at: "2026-10-09T10:01:40Z",
      },
    ],
    events: [
      { id: 1, kind: "application_started", actor: "candidate", user_name: null, data: {}, created_at: "2026-10-09T10:00:00Z" },
      { id: 14, kind: "screening_finished", actor: "agent", user_name: null, data: { stage: "qualified" }, created_at: "2026-10-09T10:04:00Z" },
    ],
    summary: {
      structured: {
        answers: detailAnswers,
        stage: "qualified",
        outcome: null,
        language: "es-MX",
        durations_seconds: { total: 240 },
        nudges: 0,
        escalations: 0,
        flags: [],
      },
      prose: "Luis lives in central Puebla and holds a valid licence.",
      written_at: "2026-10-09T10:04:05Z",
    },
    files: [],
    other_applications: [{ id: "app_01J9ZZ", status: "closed", stage: "driver_license", outcome: "knockout", created_at: "2026-06-02T09:00:00Z" }],
    open_queue_items: [{ id: "qi_approval_1", kind: "approval", status: "open", reason: null, note: null, created_at: "2026-10-09T10:04:00Z" }],
    interview: null,
    cost: {
      est_cost_usd: 0.0031,
      reconciled_cost_usd: null,
      runs: 12,
      input_tokens: 86000,
      output_tokens: 2100,
      cache_read_tokens: 61000,
      cache_write_tokens: 7200,
      p50_latency_ms: 1700,
      models: ["anthropic/claude-haiku-5.5"],
    },
    ...overrides,
  };
}

export const claimByAna = {
  user_id: "usr_rec_puebla",
  user_name: "Ana Torres",
  claimed_at: "2026-10-09T10:00:00Z",
  last_action_at: "2026-10-09T10:00:00Z",
  idle: false,
};

export function queueItem(overrides: Partial<QueueItem> & Pick<QueueItem, "id" | "kind">): QueueItem {
  return {
    status: "open",
    reason: null,
    note: null,
    created_at: "2026-10-09T10:05:00Z",
    application: summary(),
    ...overrides,
  };
}

export const slot: Slot = {
  id: "slot_8x2",
  location_id: puebla.id,
  location_name: puebla.name,
  starts_at: "2026-10-14T16:00:00Z",
  ends_at: "2026-10-14T16:30:00Z",
  local_label: "mié 14 · 10:00",
  status: "open",
  booking: null,
};

export const results: ResultsMetrics = {
  from: "2026-10-01",
  to: "2026-10-09",
  applications: 1000,
  funnel: {
    open_by_stage: { greeting: 12, name: 8, zone: 6, driver_license: 4, availability: 3, preferred_schedule: 2, experience: 2, start_date: 1, confirm: 1, qualified: 40, approved: 15, interview_booked: 22 },
    outcomes: { knockout: 420, rejection: 30, abandoned: 380, withdrawn: 54 },
  },
  time_to_qualify_seconds: { p50: 240, p95: 900 },
  knockouts_by_criterion: { zone: 160, driver_license: 260 },
  reviews: { requested: 21, upheld: 15, overturned: 6, overturn_rate: 0.2857 },
  dropoff_by_stage: { greeting: 120, name: 60, zone: 50, driver_license: 40, availability: 30, preferred_schedule: 25, experience: 20, start_date: 20, confirm: 15 },
  qualified_waiting: 3,
  takeovers: 18,
  escalations: 25,
  by_location: [{ location_id: puebla.id, applications: 300, qualified: 90, knockouts: 120 }],
};

export const economics: EconomicsMetrics = {
  from: "2026-10-01",
  to: "2026-10-09",
  conversations: 1000,
  cost_usd: { total: 3.1, per_conversation: 0.0031, per_qualified: 0.0402, per_interview: 0.1409 },
  tokens: { input: 7200000, output: 410000, cache_read: 5100000, cache_write: 900000, per_turn_input: 7223, cache_hit_rate: 0.71 },
  latency_ms: { p50: 1700, p95: 3900 },
  model_mix: [
    { model: "anthropic/claude-haiku-5.5", kind: "run", calls: 9800, share: 0.95, cost_usd: 2.7 },
    { model: "anthropic/claude-sonnet-5.5", kind: "auxiliary", calls: 420, share: 0.04, cost_usd: 0.35 },
  ],
  errors: { count: 12, rate: 0.0012, retries: 9 },
  spend_by_environment: { prod: 3.1, sim: 1.4, test: 0 },
};

export const comparison: ModelComparison = {
  dataset: "compare-2026-10-12",
  personas: 50,
  models: [
    { model: "anthropic/claude-haiku-5.5", runs: 50, latency_ms: { p50: 1700, p95: 3900 }, cost_per_screening_usd: 0.003, validation_failures: 4, escalations: 2, outcome_accuracy: 0.96 },
    { model: "anthropic/claude-sonnet-5.5", runs: 50, latency_ms: { p50: 3100, p95: 6800 }, cost_per_screening_usd: 0.011, validation_failures: 2, escalations: 1, outcome_accuracy: 0.98 },
  ],
};

// GET /api/sim/clock on the sim stack (contracts/webchat.openapi.yaml, example).
export const clockState: ClockState = { now: "2026-10-09T10:00:00Z", offset_seconds: 0, due_jobs_run: 0 };
