import { lazy, Suspense } from "react";
import { useAuth } from "../auth/context.ts";
// Primitives by file, not through ui/index.ts (see Shell.tsx).
import { Skeleton } from "../components/ui/Skeleton.tsx";
import "../components/ui/ui.css";
import { NotFound } from "../pages/NotFound.tsx";
import "../styles/surfaces/stage.css";

// The panes are a separate chunk: only a session that passes the check below ever asks for it. Nothing
// else may import StageScreen statically.
const StageScreen = lazy(() => import("./StageScreen.tsx").then((module) => ({ default: module.StageScreen })));
// The Presenter console host is its own chunk, loaded only here, after the check below: persona text never
// reaches a session that fails it, and the phone and the record never wait for it. The host is mounted
// while the panel is closed (it holds the player and the two chords) and renders nothing until opened.
const StageConsole = lazy(() => import("./PresenterConsole.tsx").then((module) => ({ default: module.StageConsole })));

/**
 * Renders the demo stage for a real Operator who is not previewing the Recruiter view, and Page not
 * found for everyone else (a Recruiter, an Operator under "Preview as Recruiter", a logged-out visitor).
 * The check reads the real role, never the previewed one. While the first answer from the server is
 * pending it shows a skeleton: neither Page not found nor the stage may flash.
 */
export function StageGate() {
  const { realRole, isPreviewingRecruiter, loading } = useAuth();
  if (loading) {
    return (
      <main className="stage-state">
        <Skeleton shape="block" />
      </main>
    );
  }
  if (realRole !== "operator" || isPreviewingRecruiter) {
    return (
      <main className="stage-state">
        <NotFound />
      </main>
    );
  }
  return (
    <main className="stage">
      <Suspense fallback={<Skeleton shape="block" />}>
        <StageScreen
          console={
            <Suspense fallback={null}>
              <StageConsole />
            </Suspense>
          }
        />
      </Suspense>
    </main>
  );
}
