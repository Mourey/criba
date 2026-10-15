import type { components } from "./schema";
import type { components as chatComponents } from "./chat-schema";

type S = components["schemas"];
type C = chatComponents["schemas"];

export type Role = S["Role"];
export type Country = S["Country"];
export type Language = S["Language"];
export type Stage = S["Stage"];
export type Outcome = S["Outcome"];
export type Flag = S["Flag"];
export type RejectionReason = S["RejectionReason"];
export type QueueKind = S["QueueKind"];
export type EscalationReason = S["EscalationReason"];
export type SlotStatus = S["SlotStatus"];

export type Me = S["Me"];
export type User = S["User"];
export type LocationRef = S["LocationRef"];
export type Location = S["Location"];
export type Claim = S["Claim"];
export type ApplicationSummary = S["ApplicationSummary"];
export type ApplicationList = S["ApplicationList"];
export type ApplicationDetail = S["ApplicationDetail"];
export type ApplicationExport = S["ApplicationExport"];
export type ApplicationCost = S["ApplicationCost"];
export type Answers = S["Answers"];
export type AnswerStatus = S["AnswerStatus"];
export type Message = S["Message"];
export type MessageSignal = S["MessageSignal"];
export type MessageLabel = S["MessageLabel"];
export type JevThresholds = S["JevThresholds"];
export type JevReading = S["JevReading"];
export type JevReadingEntry = S["JevReadingEntry"];
export type JevReadings = S["JevReadings"];
export type ToolCall = S["ToolCall"];
export type ToolCallRun = S["ToolCallRun"];
export type ToolCallTrace = S["ToolCallTrace"];
export type TraceJevCall = S["TraceJevCall"];
export type AppEvent = S["Event"];
export type Summary = S["Summary"];
export type Interview = S["Interview"];
export type QueueItem = S["QueueItem"];
export type QueueItemRef = S["QueueItemRef"];
export type QueueList = S["QueueList"];
export type ApprovalDecision = S["ApprovalDecision"];
export type ReviewDecision = S["ReviewDecision"];
export type EscalationDecision = S["EscalationDecision"];
export type RejectionRequest = S["RejectionRequest"];
export type TakeoverState = S["TakeoverState"];
export type StreamEvent = S["StreamEvent"];
export type Slot = S["Slot"];
export type SlotTimes = S["SlotTimes"];
export type BookingLink = S["BookingLink"];
export type PublicBookingPage = S["PublicBookingPage"];
export type PublicSlot = S["PublicSlot"];
export type PublicBookingConfirmation = S["PublicBookingConfirmation"];
export type ResultsMetrics = S["ResultsMetrics"];
export type EconomicsMetrics = S["EconomicsMetrics"];
export type ModelComparison = S["ModelComparison"];
export type ApiErrorBody = S["Error"];

export type ChatConversation = C["Conversation"];
export type ChatMessage = C["ChatMessage"];
export type ChatMessagePage = C["MessagePage"];
export type ChatStartRequest = C["StartConversationRequest"];
export type ChatLocale = C["Locale"];
export type ClockState = C["ClockState"];
export type AdvanceClockRequest = C["AdvanceClockRequest"];
export type VoiceSession = C["VoiceSession"];

export type ApplicationFilters = NonNullable<
  import("./schema").operations["listApplications"]["parameters"]["query"]
>;
