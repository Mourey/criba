// Typed client for contracts/api.openapi.yaml and contracts/webchat.openapi.yaml.
// Types come from the generated schema files; regenerate them with
//   npx openapi-typescript contracts/api.openapi.yaml -o src/web/src/api/schema.d.ts
//   npx openapi-typescript contracts/webchat.openapi.yaml -o src/web/src/api/chat-schema.d.ts
import type { operations } from "./schema";
import type {
  AdvanceClockRequest,
  ApplicationDetail,
  ApplicationExport,
  ApplicationFilters,
  ApplicationList,
  ApplicationSummary,
  ApprovalDecision,
  BookingLink,
  ChatConversation,
  ChatMessagePage,
  ChatStartRequest,
  Claim,
  ClockState,
  EconomicsMetrics,
  EscalationDecision,
  JevReadings,
  Location,
  Me,
  Message,
  ModelComparison,
  PublicBookingConfirmation,
  PublicBookingPage,
  QueueItem,
  QueueKind,
  QueueList,
  RejectionRequest,
  ResultsMetrics,
  ReviewDecision,
  Slot,
  SlotStatus,
  SlotTimes,
  TakeoverState,
  ToolCallTrace,
  VoiceSession,
} from "./types.ts";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: { field: string; message: string }[];

  constructor(status: number, code: string, message: string, details: { field: string; message: string }[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

type Query = Record<string, string | number | boolean | readonly (string | number)[] | null | undefined>;

interface RequestOptions {
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
  /** Do not treat a 401 as "session expired" (login, me, public routes). */
  quiet401?: boolean;
}

let unauthorizedHandler: (() => void) | undefined;

/** Called when any request other than login/me answers 401, so the app can return to the login page. */
export function setUnauthorizedHandler(handler: (() => void) | undefined): void {
  unauthorizedHandler = handler;
}

export function buildQuery(query: Query | undefined): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
    } else {
      params.append(key, String(value));
    }
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: string | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  const response = await fetch(`${path}${buildQuery(options.query)}`, {
    method,
    headers,
    body,
    credentials: "same-origin",
    signal: options.signal,
  });
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = undefined;
    }
  }
  if (!response.ok) {
    if (response.status === 401 && !options.quiet401) unauthorizedHandler?.();
    const err = (parsed ?? {}) as Partial<{
      error: string;
      message: string;
      details: { field: string; message: string }[];
    }>;
    throw new ApiError(
      response.status,
      err.error ?? "internal",
      err.message ?? `Request failed (${response.status}).`,
      err.details ?? [],
    );
  }
  return parsed as T;
}

const enc = encodeURIComponent;

type ListSlotsQuery = NonNullable<operations["listSlots"]["parameters"]["query"]>;
type ResultsQuery = NonNullable<operations["getResults"]["parameters"]["query"]>;
type EconomicsQuery = NonNullable<operations["getEconomics"]["parameters"]["query"]>;

/** A page of Test chat and Demo stage conversations (Operators only). Each id is a bearer secret. */
export type ConversationList = operations["listConversations"]["responses"][200]["content"]["application/json"];
export type ConversationItem = ConversationList["items"][number];
/** One conversation with the Candidate's messages on its channel, oldest first. */
export type ConversationDetail = operations["getConversation"]["responses"][200]["content"]["application/json"];

export const api = {
  // auth
  login: (email: string, password: string) =>
    request<Me>("POST", "/api/auth/login", { body: { email, password }, quiet401: true }),
  logout: () => request<void>("POST", "/api/auth/logout", { quiet401: true }),
  me: () => request<Me>("GET", "/api/auth/me", { quiet401: true }),
  listLocations: (country?: "ES" | "MX") =>
    request<{ items: Location[] }>("GET", "/api/locations", { query: { country } }),

  // applications
  listApplications: (filters: ApplicationFilters, signal?: AbortSignal) =>
    request<ApplicationList>("GET", "/api/applications", { query: filters as Query, signal }),
  getApplication: (id: string) => request<ApplicationDetail>("GET", `/api/applications/${enc(id)}`),
  getJevReadings: (id: string) => request<JevReadings>("GET", `/api/applications/${enc(id)}/jev-readings`),
  getToolCalls: (id: string) => request<ToolCallTrace>("GET", `/api/applications/${enc(id)}/tool-calls`),
  exportUrl: (id: string) => `/api/applications/${enc(id)}/export`,
  inviteUrl: (id: string) => `/api/applications/${enc(id)}/booking/invite.ics`,
  exportApplication: (id: string) => request<ApplicationExport>("GET", `/api/applications/${enc(id)}/export`),

  // claims
  claim: (id: string) => request<Claim>("POST", `/api/applications/${enc(id)}/claim`),
  releaseClaim: (id: string) => request<void>("DELETE", `/api/applications/${enc(id)}/claim`),
  takeOverIdleClaim: (id: string) => request<Claim>("POST", `/api/applications/${enc(id)}/claim/takeover`),

  // rejection and queue
  reject: (id: string, body: RejectionRequest) =>
    request<ApplicationSummary>("POST", `/api/applications/${enc(id)}/rejection`, { body }),
  listQueue: (query: { kind?: QueueKind[]; status?: "open" | "resolved"; location_id?: string; limit?: number } = {}) =>
    request<QueueList>("GET", "/api/queue", { query }),
  resolveApproval: (itemId: string, body: ApprovalDecision) =>
    request<QueueItem>("POST", `/api/queue/approvals/${enc(itemId)}`, { body }),
  resolveReview: (itemId: string, body: ReviewDecision) =>
    request<QueueItem>("POST", `/api/queue/reviews/${enc(itemId)}`, { body }),
  resolveEscalation: (itemId: string, body: EscalationDecision) =>
    request<QueueItem>("POST", `/api/queue/escalations/${enc(itemId)}`, { body }),
  queueStreamUrl: () => "/api/queue/stream",

  // takeover
  startTakeover: (id: string) => request<TakeoverState>("POST", `/api/applications/${enc(id)}/takeover`),
  sendTakeoverMessage: (id: string, text: string) =>
    request<Message>("POST", `/api/applications/${enc(id)}/takeover/messages`, { body: { text } }),
  handBack: (id: string, summary?: string | null) =>
    request<TakeoverState>("POST", `/api/applications/${enc(id)}/takeover/handback`, {
      body: summary ? { summary } : {},
    }),
  applicationStreamUrl: (id: string) => `/api/applications/${enc(id)}/stream`,

  // slots and booking
  createBookingLink: (id: string) => request<BookingLink>("POST", `/api/applications/${enc(id)}/booking-link`),
  listSlots: (query: ListSlotsQuery & { status?: SlotStatus } = {}) =>
    request<{ items: Slot[] }>("GET", "/api/slots", { query }),
  createSlots: (location_id: string, slots: SlotTimes[]) =>
    request<{ items: Slot[] }>("POST", "/api/slots", { body: { location_id, slots } }),
  updateSlot: (slotId: string, body: SlotTimes) => request<Slot>("PATCH", `/api/slots/${enc(slotId)}`, { body }),
  cancelSlot: (slotId: string) => request<void>("DELETE", `/api/slots/${enc(slotId)}`),
  getBookingPage: (token: string) =>
    request<PublicBookingPage>("GET", `/api/public/booking/${enc(token)}`, { quiet401: true }),
  bookFromPage: (token: string, slotId: string) =>
    request<PublicBookingConfirmation>("POST", `/api/public/booking/${enc(token)}`, {
      body: { slot_id: slotId },
      quiet401: true,
    }),

  // conversations (Operators only): the id goes in the path, never in a query string
  listConversations: (cursor?: string) =>
    request<ConversationList>("GET", "/api/conversations", { query: { cursor } }),
  getConversation: (id: string) => request<ConversationDetail>("GET", `/api/conversations/${enc(id)}`),

  // metrics
  getResults: (query: ResultsQuery = {}) => request<ResultsMetrics>("GET", "/api/metrics/results", { query }),
  getEconomics: (query: EconomicsQuery = {}) => request<EconomicsMetrics>("GET", "/api/metrics/economics", { query }),
  getModelComparison: (dataset?: string) =>
    request<ModelComparison>("GET", "/api/metrics/comparison", { query: { dataset } }),

  // sim clock (only mounted when CRIBA_ENV=sim; any other stack answers 404 sim_disabled)
  getSimClock: () => request<ClockState>("GET", "/api/sim/clock", { quiet401: true }),
  advanceSimClock: (body: AdvanceClockRequest) =>
    request<ClockState>("POST", "/api/sim/clock/advance", { body, quiet401: true }),
  resetSimClock: () => request<ClockState>("POST", "/api/sim/clock/reset", { quiet401: true }),
};

export const chatApi = {
  start: (body: ChatStartRequest) =>
    request<ChatConversation>("POST", "/api/chat/conversations", { body, quiet401: true }),
  send: (conversationId: string, messageId: string, text: string) =>
    request<{ message_id: string; id: number; duplicate: boolean }>(
      "POST",
      `/api/chat/conversations/${enc(conversationId)}/messages`,
      { body: { message_id: messageId, text }, quiet401: true },
    ),
  reply: (conversationId: string, messageId: string, inReplyTo: number, optionId: string) =>
    request<{ message_id: string; id: number; duplicate: boolean }>(
      "POST",
      `/api/chat/conversations/${enc(conversationId)}/replies`,
      { body: { message_id: messageId, in_reply_to: inReplyTo, option_id: optionId }, quiet401: true },
    ),
  /** `seen` is the run state of the last page: the server compares against it, so a run that ended between two polls returns at once. */
  read: (
    conversationId: string,
    after: number,
    wait: number,
    signal?: AbortSignal,
    seen?: { typing: boolean; finished: boolean | undefined } | null,
  ) =>
    request<ChatMessagePage>("GET", `/api/chat/conversations/${enc(conversationId)}/messages`, {
      query:
        seen && seen.finished !== undefined
          ? { after, wait, agent_typing: seen.typing, run_finished: seen.finished }
          : { after, wait },
      signal,
      quiet401: true,
    }),
  /** Mint a voice call for this conversation (startVoiceSession). The page sends no body. */
  voiceSession: (conversationId: string) =>
    request<VoiceSession>("POST", `/api/chat/conversations/${enc(conversationId)}/voice-session`, { quiet401: true }),
};
