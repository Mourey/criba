import { useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useRef, useState } from "react";
import { api } from "../api/client.ts";
import type { ChatLocale } from "../api/types.ts";
import { useConversation, type Conversation } from "../api/useConversation.ts";
import { countryOfPhone, generatePhone, LOCALE_OF, type PhoneCountry } from "./phone.ts";
import type { Take } from "./takes.ts";
import { useApplicationLookup, type ApplicationLookup } from "./useApplicationLookup.ts";

/** What one Take on the demo stage holds. The player drives the phone through `conversation` only. */
export interface StageSession {
  /** The Candidate's phone for this Take, full length, generated before the conversation starts. */
  phone: string;
  country: PhoneCountry;
  locale: ChatLocale;
  /** The shared chat client (`storageKey: null`: nothing is kept across a reload). */
  conversation: Conversation;
  /** The Take's Application, found by the exact phone once the Candidate has written. */
  application: ApplicationLookup;
  /**
   * A conversation reset and a fresh phone (in `country`, else the current country), which it
   * returns. With a `locale` the session's locale is that one until the next Take (a persona's own);
   * without one it follows the country.
   */
  newTake: (country?: PhoneCountry, locale?: ChatLocale) => string;
}

/** What the playback control reads from the stage. Provided by StageScreen; absent anywhere else. */
export interface StagePlayerValue {
  session: StageSession;
  /** Sets the composer text without counting as the Operator's typing. */
  setDraft: (text: string) => void;
  /** How many times the Operator has changed the composer: a keystroke pauses the player. */
  composerEdits: number;
  /** The sim clock probe StageScreen already asks: `available` is false on a stack with no sim mode. */
  probe: {
    available: boolean;
    isLoading: boolean;
    /** The sim clock's time, when it answers: the Presenter console's window line reads it. */
    now?: Date | null;
  };
  /** Puts a stored Take back on the stage: its phone and its conversation. Not part of `StageSession`. */
  resumeTake?: (take: Take) => void;
}

/** What `useStageSession` adds to a `StageSession`; kept out of the interface so existing values still fit it. */
export interface StageResume {
  resumeTake: (take: Take) => void;
}

export const StagePlayerContext = createContext<StagePlayerValue | null>(null);

/** The part of the Application's detail the player reads. No cost, no Candidate text. */
export interface StageDetailState {
  stage: string;
  status: string;
  outcome: string | null;
}

/**
 * The Application's Stage, status and Outcome, live: the same query as RecordPane's (the stream
 * invalidates it), keeping only these three fields, so no `cost` reaches a component.
 */
export function useStageDetail(applicationId: string | null): StageDetailState | null {
  const query = useQuery({
    queryKey: ["stage-detail", applicationId],
    queryFn: () => api.getApplication(applicationId as string),
    enabled: applicationId !== null,
    retry: false,
    select: ({ stage, status, outcome }): StageDetailState => ({ stage, status, outcome: outcome ?? null }),
  });
  return query.data ?? null;
}

export function useStageSession(): StageSession & StageResume {
  const conversation = useConversation({ storageKey: null });
  const [phone, setPhone] = useState(() => generatePhone("MX"));
  // The phone `newTake` last made, so it can hand the new one back without waiting for a render.
  const phoneRef = useRef(phone);
  const [localeOverride, setLocaleOverride] = useState<ChatLocale | null>(null);
  const country = countryOfPhone(phone) ?? "MX";
  const { reset, resume } = conversation;
  // The lookup stays idle until the Candidate has written (a message sent, or one already in the thread).
  const written = conversation.sent > 0 || conversation.messages.some((m) => m.direction === "inbound");
  const application = useApplicationLookup({ phone, enabled: written });

  const newTake = useCallback(
    (next?: PhoneCountry, locale?: ChatLocale) => {
      reset();
      const current = phoneRef.current;
      const target = next ?? countryOfPhone(current) ?? "MX";
      // A Take never reuses the previous phone (the odds are nil; the loop makes it a rule).
      let fresh = generatePhone(target);
      while (fresh === current) fresh = generatePhone(target);
      phoneRef.current = fresh;
      setPhone(fresh);
      setLocaleOverride(locale ?? null);
      return fresh;
    },
    [reset],
  );

  const resumeTake = useCallback(
    (take: Take) => {
      phoneRef.current = take.phone;
      setPhone(take.phone);
      setLocaleOverride(take.locale);
      resume(take.conversationId);
    },
    [resume],
  );

  return { phone, country, locale: localeOverride ?? LOCALE_OF[country], conversation, application, newTake, resumeTake };
}
