# 0052. Headless components and one motion library for the web app; the Demo stage is for Operators only

- Status: Accepted
- Date: 2026-10-10
- Refines: 0024 (the static React app, which now has browser dependencies for components and motion), 0036 (sim mode, which now adds the Demo stage's clock controls but no longer gates the stage) and 0019 (cost visible to Operators only, which the Demo stage's Recruiter's view keeps)

## Context
The frontend redesign needs dialogs that trap and restore focus, tabs and toasts that are accessible (master spec R4, R21: WCAG 2.2 AA), and motion that honours reduced motion (R7). The accepted intent allows "a headless component library (for example Radix) and a motion library ... within the `CONSTRAINTS.md` budgets and the pinned-version practice" (Q9). Sources: the accepted intent and the master spec of the frontend redesign.

The redesign also adds the Demo stage (`/stage`): a Candidate's WhatsApp chat beside the live Application, for demonstrations and recordings, with the Presenter console that plays personas as Takes. The original intent gated it by sim mode. The amended intent gates it by Operator login instead, because the recording runs on the real-model stack (Haiku 5.5), which has no sim mode and no sim clock (ADR 0036 pairs the stub model, the sim profile and `CRIBA_ENV=sim`).

## Decision
- **Components.** The headless `@radix-ui/react-*` packages that the design-system phase merged, with exact pins: `@radix-ui/react-dialog@1.1.23`, `@radix-ui/react-icons@1.3.2`, `@radix-ui/react-tabs@1.1.21` and `@radix-ui/react-toast@1.2.23`. Each is wrapped once in `src/web/src/components/ui/` (Dialog, Icon, Tabs, Toast). No CSS framework and no styled component library (R25); colour, spacing and component styling stay in `styles/tokens.css` and the primitives, as `src/scripts/check_web_tokens.sh` checks.
- **Motion.** `motion@13.4.4`, wrapped once in `MotionProvider` (`components/ui/MotionProvider.tsx`) with reduced motion set to "user" (R7). The motion vocabulary lives in `DESIGN.md`.
- **The Demo stage is for Operators only.** `/stage` renders only when the real role is `operator` and "Preview as Recruiter" is off (R13 as amended); anyone else gets NotFound. No navigation link points to it, and it is a lazy chunk. The Presenter console is hidden by default and opened by a keyboard shortcut. Its clock controls render only when `GET /api/sim/clock` answers 200 with a parseable body; anything else hides them.
- **The stage gate is a UI gate, not a security boundary.** The boundary is the backend: `/api/sim/*` answers 404 `sim_disabled` unless `CRIBA_ENV=sim` (`src/backend/criba/api/sim.py`, ADR 0036). The Demo stage holds no secret and calls only endpoints an Operator already has (master spec concern 2).

## Alternatives considered
- **A styled component library or a CSS framework:** it brings its own look and class names, which fight the tokens in `DESIGN.md` and the token check. R25 rules it out.
- **Hand-built dialogs, focus traps, tabs and toasts:** no new dependency, but focus handling and ARIA are where hand-built widgets most often miss WCAG 2.2 AA (R21), and each would need its own accessibility tests.
- **CSS-only motion:** no new dependency, but no single reduced-motion switch for every animation (R7), and each animation would carry its own `prefers-reduced-motion` rule.
- **Gating the stage by sim mode** (the original intent): the recording could not run on the real-model stack, which has no sim mode.
- **A separate demo app:** a second build and container that would duplicate the login, the API client and the Application view.

## Consequences
- The JavaScript bundle grows. Its size is recorded per pull request; `CONSTRAINTS.md` sets no budget (master spec concern 8).
- `osv-scanner` (in `check.sh full`) now covers the new packages through `src/web/package-lock.json`. Upgrades are by hand, because every runtime dependency is pinned exactly (R25).
- The persona fixture ships in `dist/`, inside the lazy stage chunk behind the Operator gate (master spec concern 4).
- On the real-model stack, persona playback sends persona text to the real model, which ADR 0036's pairing allows (master spec concern 14).
- A Recruiter, or an Operator under "Preview as Recruiter", who opens `/stage` gets NotFound; the Recruiter's view on the stage hides cost (0019).
- `CONTEXT.md` gains Today, Overview, Demo stage, Presenter console and Take, and the token check enforces their `_Avoid_` words in `src/web/src`.
