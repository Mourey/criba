---
name: Criba
description: A calm working console for the people who decide, light first, on the Criba seed palette.
colors:
  ground: { light: "#f6f3ec", dark: "#161b12" }
  panel: { light: "#ffffff", dark: "#20271a" }
  seam: { light: "#dcd9cc", dark: "#343f2b" }
  seam-strong: { light: "#c9c6b6", dark: "#465538" }
  seam-soft: { light: "#eeece3", dark: "#2a3322" }
  ink: { light: "#343f2b", dark: "#e4ecdb" }
  ink-strong: { light: "#20271a", dark: "#f5ffee" }
  ink-muted: { light: "#6b7262", dark: "#a3ad97" }
  ink-faint: { light: "#9a9f90", dark: "#858e79" }
  live: { light: "#97ff48", dark: "#97ff48" }
  live-soft: { light: "#eefbe1", dark: "#2c3a22" }
  live-ink: { light: "#20271a", dark: "#20271a" }
  live-edge: { light: "#6fd12a", dark: "#6fd12a" }
  status-warn: { light: "#b4560f", dark: "#f0a064" }
  status-warn-soft: { light: "#fff7f0", dark: "#3a2418" }
  status-warn-edge: { light: "#e9c9ad", dark: "#6b4128" }
  status-info: { light: "#2f6f8f", dark: "#7ab8d6" }
  status-success: { light: "#3f9b3a", dark: "#6cc866" }
  danger: { light: "#b3261e", dark: "#ff9a92" }
  focus: { light: "#20271a", dark: "#97ff48" }
  client-accent: { light: "#c8462b", dark: "#e0664a" }
typography:
  display:
    fontFamily: "Inter, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Inter, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.3
  title:
    fontFamily: "Inter, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Inter, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  body-sm:
    fontFamily: "Inter, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.3
  label:
    fontFamily: "Azeret Mono, ui-monospace, monospace"
    fontSize: "11px"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "0.16em"
  figure:
    fontFamily: "Azeret Mono, ui-monospace, monospace"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1.3
    fontFeature: "tnum"
rounded:
  radius-1: "4px"
  radius-2: "6px"
  radius-3: "8px"
  radius-pill: "999px"
spacing:
  space-1: "4px"
  space-2: "8px"
  space-3: "12px"
  space-4: "16px"
  space-5: "20px"
  space-6: "24px"
  space-7: "32px"
  space-8: "48px"
  space-9: "64px"
components:
  button-primary:
    backgroundColor: "{colors.live}"
    textColor: "{colors.live-ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.radius-2}"
    padding: "4px 12px"
    height: "32px"
  button-primary-active:
    backgroundColor: "{colors.live-edge}"
    textColor: "{colors.live-ink}"
  button-secondary:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink-strong}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.radius-2}"
    padding: "4px 12px"
    height: "32px"
  button-secondary-hover:
    backgroundColor: "{colors.live-soft}"
    textColor: "{colors.ink-strong}"
  button-quiet:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.radius-2}"
    padding: "4px 12px"
  button-danger:
    textColor: "{colors.danger}"
    rounded: "{rounded.radius-2}"
    padding: "4px 12px"
  card:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.radius-3}"
    padding: "16px"
  card-title:
    textColor: "{colors.ink-strong}"
    typography: "{typography.label}"
    padding: "12px 16px"
  field-input:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink-strong}"
    typography: "{typography.body}"
    rounded: "{rounded.radius-2}"
    padding: "8px 12px"
    height: "32px"
  tag:
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.radius-pill}"
    padding: "4px 8px"
  tag-live:
    backgroundColor: "{colors.live-soft}"
    textColor: "{colors.ink-strong}"
  status-badge:
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.radius-1}"
    padding: "4px 8px"
  dialog:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.radius-3}"
    padding: "20px"
  toast:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.radius-3}"
    padding: "12px 12px 12px 16px"
---

# Design System: Criba

## Overview

**Creative North Star: "The Quiet Console"**

Criba is a calm, persistent working console for the people who decide. It refuses the category's KPI-card dashboard: nothing on screen that is not work or its state. White panels sit on a cream ground, divided by one-pixel olive seams instead of shadows. Olive ink carries the text. Lime is the single live accent: it marks what needs the Recruiter and the one primary action in reach. Figures, times, counts and panel headers are set in a tabular mono, panel headers in small caps. Destructive actions are outlined and set apart.

The system is light first (the owner's call, for recordings) and keeps the seed palette from the Phase 1 process-design page: cream `ground`, olive `ink`, lime `live`. Dark mode is the same console on olive graphite, not a different world. Density is a working tool's density: 14px body, 13px controls, 32px control height on a fine pointer.

The page material in the approved comp is a one-pixel dot grid over the cream ground. That ground belongs to the app shell, which owns the page background; primitives never paint it. The token `--ground-dot` exists for it in `tokens.css`, but no primitive uses it, and the specimen deliberately shows a plain `--ground`.

**Key Characteristics:**
- Cream ground, white panels, one-pixel seams, no shadows.
- Lime only for what needs the Recruiter: the primary action, a live count, a live tag, the active tab.
- Inter for text; Azeret Mono for figures, counts, times, badges, tags and panel headers.
- Danger is outlined, never filled, and kept away from a primary.
- Square-shouldered corners (4, 6, 8px), pills only for tags.
- Short, ease-out motion, nothing longer than a third of a second; under reduced motion nothing moves, and only a `motion` opacity fade remains.

## Colors

A warm neutral console with one electric accent: cream and white surfaces, olive ink and seams, lime for live work, and muted signal colours for stage and outcome.

Every colour in `src/web/src/styles/tokens.css` is declared once as `light-dark(light, dark)`, and that file is the only place custom properties are defined. The theme switches through `color-scheme`: `:root` sets `color-scheme: light dark` (follow the system), and `:root[data-theme="light"]` or `:root[data-theme="dark"]` pins one. Components reference token names, never literals. The frontmatter above lists both values of every colour; the prose below names roles only.

**Browser floor.** `light-dark()` needs Chrome and Edge 123, Firefox 120, Safari 17.5 or later. There is no fallback below that floor.

### Primary
- **Signal Lime** (`--live`): the single live accent. The primary Button fill, the live count on a Card, the underline of the active tab, the `live` Tag tone. Same value in both themes, always with `--live-ink` text on it.
- **Lime Edge** (`--live-edge`): the border of lime surfaces, the pressed primary, the dot on the Approved badge.
- **Lime Wash** (`--live-soft`): hover wash for secondary buttons and the theme toggle; the fill of a `live` Tag.
- **Ink on Lime** (`--live-ink`): text on lime, dark olive in both themes.

### Neutral
- **Cream Ground** (`--ground`): the page. In dark, olive graphite.
- **Panel White** (`--panel`): cards, dialogs, toasts, fields, table headers.
- **Seam** (`--seam`): the one-pixel divider between and around panels; card and table header rules.
- **Strong Seam** (`--seam-strong`): borders of controls (quiet button, field, tag, badge, dialog, toast).
- **Soft Seam** (`--seam-soft`): row dividers inside a table; skeleton shimmer base.
- **Olive Ink** (`--ink`): running text.
- **Deep Olive** (`--ink-strong`): headings, labels, field values, secondary button text and border.
- **Muted Olive** (`--ink-muted`): hints, descriptions, table column headers, mono figures in tables.
- **Faint Olive** (`--ink-faint`): the quietest text; shown in the specimen, unused by any primitive yet.

### Signal (stage, outcome and feedback)
- **Ember** (`--status-warn`, with `--status-warn-soft` and `--status-warn-edge`): the `warn` Tag and Toast, the Knockout badge dot.
- **Slate Blue** (`--status-info`): the `info` Tag, the Interview badge dot.
- **Field Green** (`--status-success`): the `success` Tag and Toast, the Qualified badge dot.
- **Brick** (`--danger`): the danger Button, field errors, ErrorState, the Rejection badge dot.
- **Focus** (`--focus`): the 2px focus outline. Deep olive in light, lime in dark.

### Client accent
- **Sazón Red** (`--client-accent`): the Client's own colour, approved by the owner in the comp, where it marks the Client name in the rail. The dark value is lightened to meet 3:1 on the dark `--ground` and `--panel`. It is for the Client mark only. No primitive uses it; the app shell will.

### Other tokens in tokens.css
- **Phone skin** (`--phone-*`): WhatsApp-faithful colours for the Candidate chat view, kept from the current chat.
- **Chart** (`--series-1..3`, `--ramp-1..5`, `--chart-grid`, `--chart-axis`, `--chart-label`): validated categorical and sequential slots for charts.
- **Legacy names** (`--bg`, `--surface`, `--surface-2`, `--muted`, `--line`, `--accent`, `--accent-ink`, `--accent-soft`, `--warn`, `--warn-ink`, `--bubble-*`, `--chat-bg`, `--radius`, `--radius-lg`, `--font`, `--mono`): the values `app.css` carried before `tokens.css` existed, kept so current screens render unchanged. `--ink`, `--ink-strong`, `--danger`, `--focus`, `--font` and `--mono` are shared with the new set. New work uses the console names, not `--bg`, `--surface`, `--line` or `--accent`.
- **Translucent tokens** (`--ground-dot`, `--rail`, `--muted`): rgba values, left out of the frontmatter. `--rail` is the translucent cream of a navigation rail.

### Named Rules
**The One Live Rule.** Lime means "this needs you" or "this is the action". If nothing on screen needs the Recruiter, nothing on screen is lime.

**The Client Mark Rule.** `--client-accent` marks the Client and nothing else. It is never an action colour, never a status, never a fill behind a control.

## Typography

**Body Font:** Inter (with system-ui, -apple-system, Segoe UI, sans-serif), self-hosted at 400, 500, 600 and 700.
**Label/Mono Font:** Azeret Mono (with ui-monospace, monospace), self-hosted at 500.

**Character:** A plain, legible grotesque for names and sentences, and a wide mono for anything you scan or count: figures, ages, times, counts, badges, tags and the small-caps panel headers.

### Hierarchy
- **Display** (600, 30px, 1.1, -0.02em): the one headline of a view ("3 need you before lunch"). The scale stops here.
- **Headline** (600, 20px, 1.3): a section or page subhead.
- **Title** (600, 15px, 1.3, -0.02em): dialog titles; names in a row.
- **Body** (400, 14px, 1.45): running text, table cells, tab panels, dialog descriptions.
- **Body small** (400 to 600, 13px, 1.3): buttons, field labels, hints, errors, toasts, tab labels.
- **Label** (Azeret Mono 500, 11px, uppercase): panel headers and table captions at 0.16em tracking; badges, tags, table column headers, the theme toggle and card actions at 0.04em.
- **Figure** (Azeret Mono 500, 13px, tabular numerals): ages, times, counts in table cells.

### Named Rules
**The Mono Means Data Rule.** Azeret Mono is for figures and for the small uppercase labels that frame them. Sentences, names and Locations are Inter.

## Layout

Spacing runs on a 4px scale, `--space-1` (4px) to `--space-9` (64px). Panels pad at 16px; panel headers at 12px by 16px; table cells at 12px by 16px; dialogs at 20px. Controls are 32px tall (`--space-7`) on a fine pointer, and every button, toggle, field, tab and icon button grows to a 44px target under `@media (pointer: coarse)`.

Primitives carry no page grid. The approved comp's frame is a left rail and a grid of persistent panels at 1440 (Approvals waiting widest, top left); building it belongs to the app shell and the surfaces. Primitives set `min-width: 0` so they shrink inside grid tracks, and the Table scrolls inside its wrapper with a sticky header rather than pushing the page wide.

The specimen at `/specimen.html` uses a 14rem rail and a main column capped at 76rem, and collapses to one column with a horizontally scrolling contents strip below 900px.

## Elevation & Depth

Flat. No primitive has a drop shadow. Depth comes from tone and line: white panels on the cream ground, edged by a one-pixel seam. Overlays separate themselves the same way: the Dialog sits on a scrim of the ground at 80% (`color-mix` with transparent) and carries a `--seam-strong` border; the Toast is a panel with a `--seam-strong` border. Hover is tonal, never lifted: a lime wash on buttons, a 4% ink tint on table rows.

The only `box-shadow` in the system is the active tab's 2px inset lime underline, which is a rule, not elevation.

### Named Rules
**The Seam Not Shadow Rule.** Separate surfaces with a one-pixel `--seam` border. If a surface seems to need a shadow, it needs a seam or a stronger seam.

## Shapes

Square-shouldered. Badges, card counts and swatches round at 4px (`--radius-1`); buttons, fields, icon buttons and the theme toggle at 6px (`--radius-2`); cards, dialogs, toasts and ErrorState at 8px (`--radius-3`). Only Tags (and the badge dot, spinner and circle skeleton) are pills (`--radius-pill`). Borders are always 1px; the focus outline is 2px at a 2px offset.

The legacy `--radius` (16px) and `--radius-lg` (24px) are older screens' values; new surfaces do not use them.

## Components

Every primitive is exported from `src/web/src/components/ui/index.ts` and styled in `ui.css`, which imports only tokens. Fourteen components: Button, Card, Dialog, ErrorState, Field, Icon, MotionProvider, Skeleton, StatusBadge, Table, Tabs, Tag, ThemeToggle, Toast. Dialog, Tabs and Toast are built on Radix primitives.

### Button
Tactile and plain. `variant`: `primary`, `secondary` (default), `quiet`, `danger`; `loading` sets `aria-busy`, disables the button and shows a spinner while the label stays.
- **Shape:** 6px corners, 32px tall, 4px by 12px padding, 13px semibold Inter.
- **Primary:** lime fill, lime-edge border, ink-on-lime text. Hover mixes toward the edge; pressed is the edge colour. The one lime action in reach.
- **Secondary:** white panel, deep olive border and text; hover is the lime wash.
- **Quiet:** strong-seam border, olive ink text.
- **Danger:** brick outline and text on transparent, an 8% brick wash on hover. Never filled.
- **Disabled:** 50% opacity.

### Card
A white panel with a mono small-caps header. Props: `title`, `count`, `live`, `actions`, `level` (2 to 4), `flush`.
- With a `title` it is a `section` named by its heading; without one it is a padded plain container.
- The `count` sits in a small 4px box; `live` turns it lime, and only when the count is what needs the Recruiter.
- `actions` render in mono at the header's end (links such as "All →").
- `flush` drops the body padding for edge-to-edge tables.

### Dialog
Radix. Needs `title` and `description` (its accessible name and description); takes an optional `trigger` (focus returns to it on close) or a controlled `open` and `onOpenChange`. A panel up to 32rem wide on the ground scrim, with a close icon button. Reserve it for work that needs protected focus, such as confirming a Rejection.

### ErrorState
A `message` and `onRetry`. A panel with a brick-tinted border, the warning icon in brick, the message, and a secondary Retry button. It has `role="alert"`.

### Field
A labelled text input. Props: `label`, `hint`, `error`, plus input attributes. Hint and error are wired through `aria-describedby`; an error sets `aria-invalid`, turns the border brick, and shows a warning icon with the message, announced as an alert. Strong-seam border, darkening to muted ink on hover. Disabled is 50% opacity.

### Icon
The one icon set is `@radix-ui/react-icons`, curated by name in `Icon.tsx`: arrowRight, bell, calendar, chat, check, checkCircle, chevronDown, chevronRight, clock, cross, externalLink, gear, info, moon, padlock, person, plus, reload, search, sun, system, trash, warning. Sizes `sm` (13px), `md` (15px, default), `lg` (20px). Decorative and hidden from assistive technology by default; a `label` makes it `role="img"` with that name. Add a name to the map before using a new glyph.

### MotionProvider
Mount once near the root. It wraps children in the `motion` library's `MotionConfig` with `reducedMotion="user"`, so every `motion` animation below it follows the user's setting (transform animation is skipped; opacity still fades).

### Skeleton
A loading placeholder, `shape` `line` (12px), `block` (64px) or `circle` (32px), with `role="status"` and a `label`. A soft-seam to seam shimmer that stops under reduced motion.

### StatusBadge
One variant per stage and outcome, labelled in CONTEXT.md terms. A 4px-cornered mono label with a coloured dot:

| `status` | Label | Dot |
|---|---|---|
| `screening` | Screening | muted ink |
| `qualified` | Qualified | `--status-success` |
| `approved` | Approved | `--live-edge` |
| `interview_booked` | Interview | `--status-info` |
| `knockout` | Knockout | `--status-warn` |
| `rejection` | Rejection | `--danger` |
| `abandoned` | Abandoned | hollow, muted ring |
| `withdrawn` | Withdrawn | hollow, muted ring |

Takeover and Stalled are flags on an Application, not stages, so they render as Tag, never as StatusBadge.

### Table
Props: `caption`, `hideCaption`, `columns` (`key`, `header`, `mono`, `align`, `render`), `rows`, `rowKey`, `empty`. A real caption (visually hidden when the Card title already names it), a sticky header in mono uppercase muted ink, soft-seam row dividers, a 4% ink hover, `mono` columns in tabular Azeret Mono, and one spanning row with the `empty` content when there are no rows. It scrolls inside its wrapper; set it `flush` inside a Card.

### Tabs
Radix. `label` (the list's accessible name), `tabs` (`value`, `label`, `content`), `defaultValue`. Muted labels on a seam rule; the active tab goes deep olive, semibold, with a 2px lime underline. Arrow keys, Home and End move between tabs.

### Tag
A flag or attribute on a row: Takeover, Stalled, "Full-time". A pill in mono uppercase. `tone`: `neutral` (default), `live`, `warn`, `info`, `success`. Stages and outcomes are StatusBadge.

### ThemeToggle
A mono uppercase button with a sun, moon or desktop icon. It cycles light → dark → system, names the current mode in its label ("Theme: light. Switch to dark."), and saves the choice when storage allows. Takes an optional `initialMode`.

### Toast
Radix, bringing its own provider and viewport (bottom right, up to 24rem), so one Toast works anywhere. Props: `open`, `onOpenChange`, `title`, `description`, `tone` (`neutral`, `success`, `warn`, each with its own icon), `duration` (default 5000ms). Swipe right or the close button dismisses it.

### Theme storage interface
Exported from `theme.ts` and shared with the shell's pre-paint script:
- `THEME_STORAGE_KEY = "criba.theme"`. Stored values are `light` or `dark`; no key means follow the system.
- `applyTheme(value)` writes `data-theme` on the root element only for `light` or `dark`; anything else, including a stale or tampered stored value, removes the attribute.
- `readStoredTheme()` returns the stored choice, or `system` when nothing valid is stored.
- `saveTheme(choice)` stores the choice (`system` clears the key) and returns false when storage refuses.
- Every storage access is wrapped in try/catch, so the theme switch never depends on storage.

### Motion vocabulary
Short, ease-out, nothing longer than a third of a second. CSS tokens and their JS mirrors in `motion.ts` (a test keeps them equal):

| CSS token | Value | `motion.ts` export |
|---|---|---|
| `--motion-fast` | 120ms | `DURATION_FAST` (0.12) |
| `--motion-base` | 200ms | `DURATION_BASE` (0.2) |
| `--motion-slow` | 320ms | `DURATION_SLOW` (0.32) |
| `--ease-out` | cubic-bezier(0.22, 1, 0.36, 1) | `EASE_OUT` |
| `--ease-in-out` | cubic-bezier(0.65, 0, 0.35, 1) | `EASE_IN_OUT` |

What moves: colour and border transitions on hover at `--motion-fast` ease-out; the Dialog and its scrim enter at `--motion-base` ease-out (fade and a 2% rise) and leave at `--motion-fast` ease-in-out; the Toast slides in 24px from the right at `--motion-base` and fades out at `--motion-fast`; the button spinner and Skeleton shimmer loop. What never moves: layout, panels, rows, numbers and the page. Nothing bounces, nothing scales.

**Reduced motion.** MotionProvider passes `reducedMotion="user"` to `motion`. In CSS, `ui.css` carries a global `@media (prefers-reduced-motion: reduce)` rule that sets animation and transition durations and delays to 0s on every element and pseudo-element, limits animation iteration to one (stopping the spinner and shimmer loops) and turns off smooth scrolling, app-wide. A second rule removes the primitives' keyframes and transitions outright.

### Specimen
`src/web/specimen.html` (served at `/specimen.html` by the Vite dev server) renders every export of `index.ts` with synthetic data, in light, dark and reduced motion; `?theme=light` or `?theme=dark` starts it in a theme. It is dev only and not part of the production build.

## Do's and Don'ts

### Do:
- **Do** take every colour, space, radius, type size and duration from `tokens.css` by name; declare new colours there as `light-dark(light, dark)`.
- **Do** keep lime for what needs the Recruiter and for one primary action per row or panel.
- **Do** separate surfaces with a 1px seam and no shadow.
- **Do** set figures, ages, times and counts in Azeret Mono with tabular numerals.
- **Do** keep danger outlined and set apart from the primary.
- **Do** use CONTEXT.md terms in labels: StatusBadge for stages and outcomes, Tag for Takeover and Stalled.
- **Do** mount MotionProvider once near the root and use the `motion.ts` presets for any `motion` animation.
- **Do** let the app shell paint the dot-grid ground; primitives stay on `--panel`.

### Don't:
- **Don't** fill a danger button or put Reject next to Approve without space between them.
- **Don't** use `--client-accent` for actions, stages, outcomes or focus.
- **Don't** add drop shadows to panels, dialogs or toasts.
- **Don't** import a second icon set or an icon font; add a name to `Icon.tsx` instead.
- **Don't** animate longer than `--motion-slow` (320ms) or let anything move under reduced motion; an opacity fade from `motion` is the only thing that remains.
- **Don't** build KPI-card dashboards: nothing on screen that is not work or its state.
- **Don't** use the legacy `--radius` (16px) or `--radius-lg` (24px) on new surfaces.
