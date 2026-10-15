import { motion } from "motion/react";
import { useState, type ReactNode } from "react";
import {
  Button,
  Card,
  DURATION_BASE,
  DURATION_FAST,
  DURATION_SLOW,
  Dialog,
  EASE_IN_OUT,
  EASE_OUT,
  ErrorState,
  Field,
  Icon,
  MotionProvider,
  Skeleton,
  StatusBadge,
  THEME_STORAGE_KEY,
  Table,
  Tabs,
  Tag,
  ThemeToggle,
  Toast,
  type IconName,
  type StatusKind,
  type TableColumn,
  type ThemeChoice,
  type ToastTone,
} from "./index.ts";

// Dev-only page (specimen.html). It renders every export of index.ts so the system can be reviewed
// in light, dark and reduced motion: ?theme=light|dark starts it in a theme; reduced motion follows
// the operating system setting through MotionProvider.

type Row = { id: string; name: string; location: string; stage: StatusKind; age: string };

const ROWS: Row[] = [
  { id: "r1", name: "Diego Martínez", location: "Sazón Puebla Centro", stage: "qualified", age: "2h 10m" },
  { id: "r2", name: "Lucía Herrera", location: "Sazón Angelópolis", stage: "approved", age: "48m" },
  { id: "r3", name: "Andrés Soto", location: "Sazón La Paz", stage: "screening", age: "12m" },
  { id: "r4", name: "Paola Méndez", location: "Sazón Puebla Centro", stage: "interview_booked", age: "1h" },
  { id: "r5", name: "Tomás Aguirre", location: "Sazón Angelópolis", stage: "knockout", age: "3h" },
  { id: "r6", name: "Valeria Ortiz", location: "Sazón La Paz", stage: "rejection", age: "5h" },
  { id: "r7", name: "Emilio Ramos", location: "Sazón Puebla Centro", stage: "abandoned", age: "1d" },
  { id: "r8", name: "Sara Núñez", location: "Sazón Angelópolis", stage: "withdrawn", age: "2d" },
];

const COLUMNS: TableColumn<Row>[] = [
  { key: "name", header: "Candidate" },
  { key: "location", header: "Location" },
  { key: "stage", header: "Stage", render: (row) => <StatusBadge status={row.stage} /> },
  { key: "age", header: "Age", mono: true, align: "end" },
];

const STAGES: Record<StatusKind, string> = {
  screening: "The Screening Agent is asking its questions",
  qualified: "Every question answered, waiting for Approval",
  approved: "A Recruiter approved; the Candidate can book",
  interview_booked: "A slot is taken",
  knockout: "A criterion failed; can reopen on a valid new answer",
  rejection: "A named human ended the Application",
  abandoned: "The nudges ran out without a reply",
  withdrawn: "The Candidate withdrew",
};

const ICON_USE: Record<IconName, string> = {
  arrowRight: "Go to",
  bell: "Notifications",
  calendar: "Interview slots",
  chat: "Conversation",
  check: "Done",
  checkCircle: "Success",
  chevronDown: "Expand",
  chevronRight: "Next",
  clock: "Age, time left",
  cross: "Close, dismiss",
  externalLink: "Opens elsewhere",
  gear: "Settings",
  info: "Neutral notice",
  moon: "Dark theme",
  padlock: "Restricted",
  person: "Candidate, user",
  plus: "Add",
  reload: "Retry, refresh",
  search: "Find a Candidate",
  sun: "Light theme",
  system: "System theme",
  trash: "Delete",
  warning: "Needs attention",
};

// Token swatches. Each name has a rule in ui.css, so the specimen needs no inline style.
const SWATCHES: readonly { group: string; tokens: readonly string[] }[] = [
  { group: "Surface", tokens: ["ground", "panel", "live-soft"] },
  { group: "Seam", tokens: ["seam-soft", "seam", "seam-strong"] },
  { group: "Ink", tokens: ["ink-strong", "ink", "ink-muted", "ink-faint"] },
  { group: "Live", tokens: ["live", "live-edge", "live-ink"] },
  { group: "Status", tokens: ["status-warn", "status-info", "status-success", "danger", "focus"] },
];

const TYPE_SCALE: readonly { token: string; sample: string; mono?: boolean }[] = [
  { token: "xl", sample: "3 need you before lunch" },
  { token: "lg", sample: "Approvals waiting" },
  { token: "md", sample: "Diego Martínez, Sazón Puebla Centro" },
  { token: "base", sample: "Full-time, evenings, 2 years on a delivery platform" },
  { token: "sm", sample: "Booked via chat, window open" },
  { token: "xs", sample: "RECENTLY QUALIFIED  12:30  48M  8/8", mono: true },
];

const SECTIONS: readonly string[] = [
  "Colour",
  "Type scale",
  "Button",
  "Card",
  "Dialog",
  "ErrorState",
  "Field",
  "Icon",
  "MotionProvider",
  "Skeleton",
  "StatusBadge",
  "Table",
  "Tabs",
  "Tag",
  "ThemeToggle",
  "Toast",
  "Theme and motion",
];

const slug = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, "-");

function themeFromQuery(): ThemeChoice | undefined {
  const value = new URLSearchParams(window.location.search).get("theme");
  return value === "light" || value === "dark" ? value : undefined;
}

function Section({ name, note, children }: { name: string; note: string; children: ReactNode }) {
  return (
    <section id={slug(name)} className="specimen__section" aria-labelledby={`${slug(name)}-name`}>
      <div className="specimen__intro">
        <h2 id={`${slug(name)}-name`} className="specimen__name">
          {name}
        </h2>
        <p className="specimen__note">{note}</p>
      </div>
      <div className="specimen__stage">{children}</div>
    </section>
  );
}

function Demo({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="specimen__demo">
      <p className="specimen__label">{label}</p>
      {children}
    </div>
  );
}

type ToastState = { id: number; open: boolean; tone: ToastTone; title: string; description: string };

export function Specimen() {
  const [retries, setRetries] = useState(0);
  const [replays, setReplays] = useState(0);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<ToastState>({ id: 0, open: false, tone: "neutral", title: "", description: "" });

  const showToast = (tone: ToastTone, title: string, description: string) =>
    setToast((current) => ({ id: current.id + 1, open: true, tone, title, description }));

  return (
    <MotionProvider>
      <div className="specimen">
        <aside className="specimen__rail" aria-label="Specimen sections">
          <p className="specimen__brand">
            <span className="specimen__mark" aria-hidden="true" />
            <span>CRIBA</span>
          </p>
          <nav>
            <ul className="specimen__toc">
              {SECTIONS.map((name) => (
                <li key={name}>
                  <a href={`#${slug(name)}`}>{name}</a>
                </li>
              ))}
            </ul>
          </nav>
        </aside>

        <main className="specimen__main">
          <header className="specimen__header">
            <div className="specimen__title">
              <h1>Criba primitives</h1>
              <p>
                Every export of components/ui on one page, in the Quiet Console: cream ground, white panels, one-pixel seams, no shadows, lime only for what
                needs the Recruiter.
              </p>
              <p className="specimen__note">
                Light and dark switch with the toggle, or start with ?theme=light or ?theme=dark. Reduced motion follows your operating system setting:
                MotionProvider passes reducedMotion=&quot;user&quot; and ui.css honours prefers-reduced-motion. Emulate it in the browser tools to review it.
              </p>
            </div>
            <ThemeToggle initialMode={themeFromQuery()} />
          </header>

          <Section name="Colour" note="Named tokens from tokens.css. Every value is declared once as light-dark(light, dark); components use the name, never a literal.">
            <div className="specimen__swatches">
              {SWATCHES.map(({ group, tokens }) => (
                <div key={group} className="specimen__swatchgroup">
                  <p className="specimen__label">{group}</p>
                  <ul className="specimen__swatchlist">
                    {tokens.map((token) => (
                      <li key={token} className="specimen__swatchitem">
                        <span className={`specimen__swatch specimen__swatch--${token}`} aria-hidden="true" />
                        <code>--{token}</code>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </Section>

          <Section name="Type scale" note="Inter for text, Azeret Mono for figures, counts, times, badges and panel headers. The scale stops at 30px.">
            <ul className="specimen__types">
              {TYPE_SCALE.map(({ token, sample, mono }) => (
                <li key={token} className="specimen__type">
                  <code>--text-{token}</code>
                  <span className={`specimen__sample specimen__sample--${token}${mono ? " specimen__sample--mono" : ""}`}>{sample}</span>
                </li>
              ))}
            </ul>
          </Section>

          <Section name="Button" note="Primary is the one lime action in reach. Danger is outlined and never fills; keep it away from a primary.">
            <Demo label="Variants">
              <div className="specimen__row">
                <Button variant="primary">Approve</Button>
                <Button variant="secondary">Open</Button>
                <Button variant="quiet">Cancel</Button>
              </div>
            </Demo>
            <Demo label="Danger, set apart">
              <div className="specimen__row">
                <Button variant="danger">Reject</Button>
              </div>
            </Demo>
            <Demo label="Loading and disabled">
              <div className="specimen__row">
                <Button
                  variant="primary"
                  loading={loading}
                  onClick={() => {
                    setLoading(true);
                    window.setTimeout(() => setLoading(false), 1600);
                  }}
                >
                  {loading ? "Approving" : "Approve and wait"}
                </Button>
                <Button variant="primary" loading>
                  Approving
                </Button>
                <Button variant="secondary" disabled>
                  Open
                </Button>
                <Button variant="danger" disabled>
                  Reject
                </Button>
              </div>
            </Demo>
          </Section>

          <Section name="Card" note="A white panel on the ground with a mono small-caps header. A title makes it a region; the count goes live when something needs a person.">
            <div className="specimen__cards">
              <Card title="Approvals waiting" count={3} live actions={<a href="#table">Review queue →</a>} level={3}>
                <p className="specimen__flush">Diego Martínez, Lucía Herrera and Andrés Soto are qualified and waiting for a Recruiter.</p>
              </Card>
              <Card title="Takeovers on me" count={1} actions={<a href="#tag">All →</a>} level={3}>
                <p className="specimen__flush">Rocío Paredes asked about night shifts.</p>
              </Card>
              <Card>
                <p className="specimen__flush">A panel without a title is a plain container.</p>
              </Card>
            </div>
          </Section>

          <Section name="Dialog" note="Radix. Traps focus, returns it to the trigger on close, and needs a title and a description. Reserve it for work that needs protected focus.">
            <Dialog
              trigger={<Button variant="secondary">Open dialog</Button>}
              title="Reject Lucía Herrera"
              description="This ends the Application with a Rejection. A Recruiter's name goes on the record."
            >
              <div className="specimen__actions">
                <Button variant="quiet">Keep reviewing</Button>
                <Button variant="danger">Reject</Button>
              </div>
            </Dialog>
          </Section>

          <Section name="ErrorState" note="A message that names the problem, and Retry. It is an alert, so it is announced.">
            <ErrorState message="Could not load Applications. Check the connection and try again." onRetry={() => setRetries((count) => count + 1)} />
            <p className="specimen__result" aria-live="polite">
              Retry pressed <span className="specimen__figure">{retries}</span> {retries === 1 ? "time" : "times"}
            </p>
          </Section>

          <Section name="Field" note="A label, a hint and an error, wired through aria-describedby. The error sets aria-invalid and names the way out.">
            <div className="specimen__fields">
              <Field label="Phone" placeholder="+52 55 1234 5678" />
              <Field label="Location" hint="As it appears on the Client's list" placeholder="Sazón Puebla Centro" />
              <Field label="Slot time" defaultValue="25:00" error="Enter a time between 00:00 and 23:59." />
              <Field label="Candidate" defaultValue="Locked while a Takeover is open" disabled />
            </div>
          </Section>

          <Section name="Icon" note="One set, @radix-ui/react-icons, curated by name. Decorative by default; give it a label when it stands alone.">
            <Demo label="Sizes and a labelled icon">
              <div className="specimen__row">
                <Icon name="check" size="sm" />
                <Icon name="check" size="md" />
                <Icon name="check" size="lg" />
                <Icon name="checkCircle" size="lg" label="Approved" />
              </div>
            </Demo>
            <Demo label="The set">
              <ul className="specimen__icons">
                {(Object.keys(ICON_USE) as IconName[]).map((name) => (
                  <li key={name} className="specimen__icon">
                    <Icon name={name} size="lg" />
                    <code>{name}</code>
                    <span>{ICON_USE[name]}</span>
                  </li>
                ))}
              </ul>
            </Demo>
          </Section>

          <Section name="MotionProvider" note="Wraps the page in MotionConfig with reducedMotion=&quot;user&quot;. With the system setting on, transform animation is skipped and opacity still fades.">
            <Demo label="Replay">
              <div className="specimen__row">
                <Button variant="secondary" onClick={() => setReplays((count) => count + 1)}>
                  Replay
                </Button>
                <motion.span
                  key={replays}
                  className="specimen__motion"
                  initial={{ opacity: 0, x: 24 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: DURATION_BASE, ease: EASE_OUT }}
                >
                  Slides in 0.2s, ease-out
                </motion.span>
              </div>
            </Demo>
          </Section>

          <Section name="Skeleton" note="A placeholder while data loads. The shimmer stops under reduced motion.">
            <div className="specimen__skeletons">
              <Skeleton shape="circle" label="Loading avatar" />
              <div className="specimen__skeletonlines">
                <Skeleton />
                <Skeleton />
              </div>
              <Skeleton shape="block" label="Loading panel" />
            </div>
          </Section>

          <Section name="StatusBadge" note="One variant per stage and outcome, in CONTEXT.md terms. Takeover and Stalled are flags, so they are Tags.">
            <ul className="specimen__stages">
              {(Object.keys(STAGES) as StatusKind[]).map((status) => (
                <li key={status} className="specimen__stagerow">
                  <StatusBadge status={status} />
                  <span>{STAGES[status]}</span>
                </li>
              ))}
            </ul>
          </Section>

          <Section name="Table" note="Caption, sticky header, hover rows, mono figures, and an empty row that spans the columns.">
            <div className="specimen__scroll">
              <p className="specimen__label">Scrolls inside the panel, the header stays put</p>
              <Card title="Recently qualified" level={3} flush>
                <Table caption="Recent Applications" hideCaption columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} empty="No Applications yet." />
              </Card>
            </div>
            <Card title="Takeovers on me" level={3} flush>
              <Table caption="Takeovers" hideCaption columns={COLUMNS} rows={[]} rowKey={(row) => row.id} empty="No Takeovers are open." />
            </Card>
          </Section>

          <Section name="Tabs" note="Radix. Arrow keys move between tabs, Home and End jump to the ends.">
            <Tabs
              label="Application sections"
              tabs={[
                { value: "summary", label: "Summary", content: <p className="specimen__flush">Eight of eight answers valid. Qualified 48 minutes ago.</p> },
                { value: "transcript", label: "Transcript", content: <p className="specimen__flush">The conversation with the Candidate, message by message.</p> },
                { value: "events", label: "Events", content: <p className="specimen__flush">Ids, codes and canonical values only.</p> },
              ]}
            />
          </Section>

          <Section name="Tag" note="A flag or attribute on a row. Takeover and Stalled are flags on an Application, not stages.">
            <div className="specimen__row">
              <Tag>Full-time</Tag>
              <Tag tone="live">Needs you</Tag>
              <Tag tone="warn">Takeover</Tag>
              <Tag tone="info">Stalled</Tag>
              <Tag tone="success">Window open</Tag>
            </div>
          </Section>

          <Section name="ThemeToggle" note="The button in the header. It cycles light, dark, system, names the current mode, and saves the choice when storage allows. The switch works without storage.">
            <p className="specimen__flush">
              Light, then dark, then system. The choice is stored under <code>{THEME_STORAGE_KEY}</code>; no key means system.
            </p>
          </Section>

          <Section name="Toast" note="Radix. Dismisses itself after its duration, or from its own button.">
            <div className="specimen__row">
              <Button variant="secondary" onClick={() => showToast("success", "Approved", "Lucía Herrera can now book an Interview.")}>
                Show success toast
              </Button>
              <Button variant="secondary" onClick={() => showToast("warn", "Takeover open", "Rocío Paredes asked about night shifts.")}>
                Show warning toast
              </Button>
              <Button variant="quiet" onClick={() => showToast("neutral", "Saved", "Your filters are kept for this session.")}>
                Show neutral toast
              </Button>
            </div>
            <Toast
              key={toast.id}
              open={toast.open}
              onOpenChange={(open) => setToast((current) => ({ ...current, open }))}
              tone={toast.tone}
              title={toast.title}
              description={toast.description}
            />
          </Section>

          <Section name="Theme and motion" note="The non-component exports of index.ts. shell-app's pre-paint script uses the same key and values.">
            <dl className="specimen__defs">
              <dt>
                <code>THEME_STORAGE_KEY</code>
              </dt>
              <dd>
                <code>&quot;{THEME_STORAGE_KEY}&quot;</code> holds <code>light</code> or <code>dark</code>; no key means system
              </dd>
              <dt>
                <code>applyTheme</code>
              </dt>
              <dd>Writes data-theme for light or dark; removes it for anything else</dd>
              <dt>
                <code>readStoredTheme</code>
              </dt>
              <dd>The stored choice, or system when nothing valid is stored or storage throws</dd>
              <dt>
                <code>saveTheme</code>
              </dt>
              <dd>Saves the choice; returns false when storage refuses</dd>
              <dt>
                <code>DURATION_FAST</code>
              </dt>
              <dd className="specimen__figure">{DURATION_FAST}s</dd>
              <dt>
                <code>DURATION_BASE</code>
              </dt>
              <dd className="specimen__figure">{DURATION_BASE}s</dd>
              <dt>
                <code>DURATION_SLOW</code>
              </dt>
              <dd className="specimen__figure">{DURATION_SLOW}s</dd>
              <dt>
                <code>EASE_OUT</code>
              </dt>
              <dd className="specimen__figure">cubic-bezier({EASE_OUT.join(", ")})</dd>
              <dt>
                <code>EASE_IN_OUT</code>
              </dt>
              <dd className="specimen__figure">cubic-bezier({EASE_IN_OUT.join(", ")})</dd>
            </dl>
          </Section>

          <footer className="specimen__footer">
            <span>SYNTHETIC DATA</span>
            <span>DEV ONLY · NOT IN THE BUILD</span>
          </footer>
        </main>
      </div>
    </MotionProvider>
  );
}
