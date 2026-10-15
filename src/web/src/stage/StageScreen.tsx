import { useState, type ReactNode } from "react";
import "../components/ui/ui.css";
import { stage } from "../lib/labels.ts";
import { useSimClock } from "../lib/useSimClock.ts";
import "../styles/surfaces/stage.css";
import { ClockStrip } from "./ClockStrip.tsx";
import { PhonePane } from "./PhonePane.tsx";
import { RecordPane } from "./RecordPane.tsx";
import { StagePlayerContext, useStageSession } from "./useStageSession.ts";

/**
 * The two panes of the demo stage: the phone on the left, the live Application on the right, with the
 * sim clock above the record. `console` is the one slot for the Presenter console and the playback
 * control; it renders nothing when it is not passed. The composer text lives here, so the player can
 * type into it, and the context gives the player the session and the sim probe this screen already has.
 */
export function StageScreen({ console: consoleSlot }: { console?: ReactNode }) {
  const session = useStageSession();
  // The same query as the strip's, so one request: the record drops the date on the clock's day.
  const { now, available, isLoading } = useSimClock();
  const [draft, setDraft] = useState("");
  const [composerEdits, setComposerEdits] = useState(0);
  const player = { session, setDraft, composerEdits, probe: { available, isLoading, now }, resumeTake: session.resumeTake };
  return (
    <StagePlayerContext.Provider value={player}>
      <div className="stage-screen">
        <h1 className="ui-sr-only">{stage.heading}</h1>
        <PhonePane
          conversation={session.conversation}
          phone={session.phone}
          onCountryChange={(country) => session.newTake(country)}
          draft={draft}
          onDraftChange={(text) => {
            setDraft(text);
            setComposerEdits((n) => n + 1);
          }}
        />
        <div className="stage-side">
          <ClockStrip country={session.country} />
          {consoleSlot ? (
            <div className="stage-console" data-testid="stage-console">
              {consoleSlot}
            </div>
          ) : null}
          <RecordPane lookup={session.application} now={now} />
        </div>
      </div>
    </StagePlayerContext.Provider>
  );
}
