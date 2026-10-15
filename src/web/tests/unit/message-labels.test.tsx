import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MessageLabel } from "../../src/api/types.ts";
import { MessageLabelList, MessageLabelStrip } from "../../src/components/MessageLabels.tsx";
import { messageLabelWords } from "../../src/lib/messageLabels.ts";
import { detail, operatorMe, recruiterMe } from "./fixtures.ts";
import { mockApi, renderRoute } from "./helpers.tsx";

// Message labels (ADR 0075): Jev's labels under each typed Candidate message, the same for every role, never a number.

const ID = "app_01JA2B3C4D5E6F7G8H9J0K";

class SilentEventSource {
  addEventListener() {}
  removeEventListener() {}
  close() {}
}

beforeEach(() => {
  vi.stubGlobal("EventSource", SilentEventSource);
});

const LABEL: MessageLabel = {
  message_id: 1,
  stage: "greeting",
  at: "2026-10-09T10:00:00Z",
  language: "es",
  fired: ["frustrated", "wants_human"],
  intent: "asking",
  tone_hint: "offer_person",
  language_set: "en",
  language_reason: "request",
};

const labelled = (label: MessageLabel = LABEL) => detail({ message_labels: [label] });

const strip = async () => {
  const transcript = await screen.findByRole("log", { name: "Transcript" });
  return within(transcript).getByTestId("message-labels");
};

describe("Message labels: words", () => {
  it("turns each code into words, in a fixed order", () => {
    expect(messageLabelWords(LABEL)).toEqual([
      "Spanish",
      "Frustrated",
      "Wants a person",
      "Intent: Asking a question",
      "Tone hint: offer a person",
      "Asked for English",
    ]);
  });

  it("says a switch, and says nothing about the language a first message set", () => {
    expect(messageLabelWords({ ...LABEL, fired: [], intent: null, tone_hint: null, language_reason: "switch" })).toEqual(["Spanish", "Switched to English"]);
    expect(messageLabelWords({ ...LABEL, fired: [], intent: null, tone_hint: null, language_set: "es-MX", language_reason: "first_message" })).toEqual(["Spanish"]);
  });

  it("never turns an unknown code or a number into text, even if a server sent one", () => {
    const odd = { ...LABEL, fired: ["abusive", 0.97], intent: "steering", tone_hint: 0.5, language: "xx" } as unknown as MessageLabel;
    expect(messageLabelWords(odd)).toEqual(["Asked for English"]);
  });

  it("renders the strip as plain text with no digit, and nothing for a label with no words", () => {
    const { container, rerender } = render(<MessageLabelStrip label={LABEL} />);
    expect(container.textContent).not.toMatch(/\d/);
    expect(container.querySelector(".ui-tag")).toBeNull();
    rerender(<MessageLabelStrip label={{ ...LABEL, language: "xx", fired: [], intent: null, tone_hint: null, language_set: null } as unknown as MessageLabel} />);
    expect(container.textContent).toBe("");
  });

  it("lists every message's labels with the fixed line, and says when there are none", () => {
    const { rerender } = render(<MessageLabelList labels={[LABEL]} />);
    expect(screen.getByRole("list", { name: "Message labels" })).toHaveTextContent("Wants a person");
    expect(screen.getByText("Communication context from the conversation, not an assessment of the Candidate.")).toBeInTheDocument();
    rerender(<MessageLabelList labels={[]} />);
    expect(screen.getByText("Jev has read no typed message on this Application yet.")).toBeInTheDocument();
  });
});

describe("Message labels in the Transcript", () => {
  it("shows a Recruiter the language, the signals, the intent and the tone hint as words, with no number", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: labelled() });
    renderRoute(`/applications/${ID}`);
    const labels = await strip();
    for (const word of ["Spanish", "Frustrated", "Wants a person", "Intent: Asking a question", "Tone hint: offer a person", "Asked for English"]) {
      expect(labels).toHaveTextContent(word);
    }
    expect(labels.textContent).not.toMatch(/\d/);
    // Under the Candidate's own bubble, and the fixed line says it is not an assessment.
    expect(labels.closest("[data-message-id]")).toHaveAttribute("data-message-id", "1");
    expect(screen.getByText("Communication context from the conversation, not an assessment of the Candidate.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Operator panel" })).not.toBeInTheDocument();
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });

  it("shows an Operator under Preview as Recruiter the same strip and no scores", async () => {
    mockApi({
      "GET /api/auth/me": operatorMe,
      [`GET /api/applications/:id`]: labelled(),
      "GET /api/applications/:id/jev-readings": [500, { error: "internal", message: "no" }],
      "GET /api/applications/:id/tool-calls": [500, { error: "internal", message: "no" }],
    });
    renderRoute(`/applications/${ID}`);
    await screen.findByRole("region", { name: "Operator panel" });
    fireEvent.click(screen.getByRole("button", { name: "Preview as Recruiter" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Operator panel" })).not.toBeInTheDocument());
    const labels = await strip();
    expect(labels).toHaveTextContent("Wants a person");
    expect(labels.textContent).not.toMatch(/\d/);
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });

  it("puts no strip on a message without a label", async () => {
    mockApi({ "GET /api/auth/me": recruiterMe, [`GET /api/applications/:id`]: labelled() });
    renderRoute(`/applications/${ID}`);
    await strip();
    expect(screen.getAllByTestId("message-labels")).toHaveLength(1);
  });
});
