// Copy for the Operator's conversation list (/conversations) and its read-only view (/conversations/:id).
// Terms follow CONTEXT.md: Application, Stage, Outcome, Demo stage, Test chat.

/** The page title: the Operator's "Test chat" navigation item opens this list. */
export const title = "Test chat";
export const tableCaption = "Test chat and Demo stage conversations, newest first";
export const intro = "Conversations started from Test chat and the Demo stage, newest first.";

export const columns = {
  who: "Persona or phone",
  client: "Started from",
  stageOutcome: "Stage and Outcome",
  time: "Started",
} as const;

export const clientLabel: Record<"test_chat" | "demo", string> = {
  test_chat: "Test chat",
  demo: "Demo stage",
};

export const unknownPersona = "Unknown persona";
export const noPhone = "No phone";
/** A voice conversation (`channel` voice, ADR 0067): its messages are Spoken turns. */
export const spoken = "Spoken";
export const noApplication = "No Application yet";
export const lastMessage = (when: string) => `Last message ${when}`;
export const noMessage = "No message yet";

export const newConversation = "New conversation";
export const loadMore = "Load more";
export const empty = "No conversations yet.";
export const emptyHint = "Start one in Test chat or on the Demo stage.";
export const loadFailed = (message: string) => `Could not load the conversations. ${message}`;

// The view.
export const backToList = "All conversations";
export const startedAt = (when: string) => `Started ${when}`;
export const openApplication = "Open the Application";
export const resume = "Resume";
export const resumeHint = "Opens Test chat on this conversation.";
export const resumeFailed = "Could not resume: this browser did not let the page keep the conversation for Test chat. Allow this page in the browser settings and try again.";
export const spokenNoResume = "Spoken conversations cannot be resumed here.";
export const closed = "This conversation's Application is closed.";
export const sharedPhone = "Messages from every web-chat conversation with this phone.";
export const notFound = "Conversation not found.";
export const notFoundHint = "It does not exist, or it was started before conversations were recorded.";
export const viewLoadFailed = (message: string) => `Could not load the conversation. ${message}`;
