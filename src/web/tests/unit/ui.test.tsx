/// <reference types="node" />
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MotionConfig } from "motion/react";
import {
  Button,
  Card,
  Dialog,
  ErrorState,
  Field,
  Icon,
  MotionProvider,
  Skeleton,
  StatusBadge,
  Table,
  Tabs,
  Tag,
  ThemeToggle,
  Toast,
  THEME_STORAGE_KEY,
  DURATION_BASE,
  DURATION_FAST,
  DURATION_SLOW,
  EASE_IN_OUT,
  EASE_OUT,
  applyTheme,
  readStoredTheme,
  saveTheme,
  type StatusKind,
} from "../../src/components/ui";
import * as ui from "../../src/components/ui";
import { Specimen } from "../../src/components/ui/Specimen";

// The real `motion` keeps working; only MotionConfig is a spy that still renders its children.
vi.mock("motion/react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("motion/react")>()),
  MotionConfig: vi.fn(({ children }: { children?: ReactNode }) => <>{children}</>),
}));

// Vitest swaps CSS imports for an empty string, so CSS is read from disk.
const readStyle = (file: string) => readFileSync(join(import.meta.dirname, "../../src", file), "utf8");

// The primitives: capitalised function exports of index.ts. THEME_STORAGE_KEY, DURATION_* and the like are not.
const COMPONENT_NAMES = Object.entries(ui)
  .filter(([name, value]) => /^[A-Z][a-z]/.test(name) && typeof value === "function")
  .map(([name]) => name);

function stubMatchMedia(matches = false) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(() => false),
  }));
}

beforeEach(() => {
  stubMatchMedia();
});

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("Button", () => {
  it.each(["primary", "secondary", "quiet", "danger"] as const)("renders the %s variant", (variant) => {
    render(<Button variant={variant}>Approve</Button>);
    const button = screen.getByRole("button", { name: "Approve" });
    expect(button).toHaveClass("ui-btn", `ui-btn--${variant}`);
    expect(button).toHaveAttribute("type", "button");
  });

  it("defaults to the secondary variant and is not busy", () => {
    render(<Button>Open</Button>);
    const button = screen.getByRole("button", { name: "Open" });
    expect(button).toHaveClass("ui-btn--secondary");
    expect(button).not.toHaveAttribute("aria-busy");
    expect(button).toBeEnabled();
  });

  it("calls onClick", () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Open</Button>);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("is busy and disabled while loading, and keeps its name", () => {
    const onClick = vi.fn();
    render(
      <Button variant="primary" loading onClick={onClick}>
        Approve
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Approve" });
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe("Field", () => {
  it("labels the input", () => {
    render(<Field label="Phone" />);
    const input = screen.getByLabelText("Phone");
    expect(input.tagName).toBe("INPUT");
    expect(input).not.toHaveAttribute("aria-describedby");
    expect(input).not.toHaveAttribute("aria-invalid", "true");
  });

  it("links the hint and the error through aria-describedby", () => {
    render(<Field label="Phone" hint="Include the country code" error="Enter a valid number" />);
    const input = screen.getByLabelText("Phone");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("Include the country code Enter a valid number");
    const ids = (input.getAttribute("aria-describedby") ?? "").split(" ");
    expect(ids).toHaveLength(2);
    expect(document.getElementById(ids[0] ?? "")).toHaveTextContent("Include the country code");
    expect(document.getElementById(ids[1] ?? "")).toHaveTextContent("Enter a valid number");
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a valid number");
  });

  it("describes the input by the hint alone when there is no error", () => {
    render(<Field label="Phone" hint="Include the country code" />);
    expect(screen.getByLabelText("Phone")).toHaveAccessibleDescription("Include the country code");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("passes input props through", () => {
    const onChange = vi.fn();
    render(<Field label="City" placeholder="Puebla" onChange={onChange} />);
    const input = screen.getByLabelText("City");
    expect(input).toHaveAttribute("placeholder", "Puebla");
    fireEvent.change(input, { target: { value: "La Paz" } });
    expect(onChange).toHaveBeenCalledTimes(1);
  });
});

describe("Table", () => {
  type Row = { id: string; name: string; age: string };
  const columns = [
    { key: "name", header: "Candidate" },
    { key: "age", header: "Age", mono: true },
  ];
  const rows: Row[] = [
    { id: "1", name: "Lucía Herrera", age: "48m" },
    { id: "2", name: "Andrés Soto", age: "12m" },
  ];

  it("names the table by its caption and renders headers and rows", () => {
    render(<Table caption="Approvals waiting" columns={columns} rows={rows} rowKey={(r: Row) => r.id} empty="Nothing waiting" />);
    const table = screen.getByRole("table", { name: "Approvals waiting" });
    expect(table.querySelector("caption")).toHaveTextContent("Approvals waiting");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Candidate", "Age"]);
    expect(within(table).getByRole("cell", { name: "Lucía Herrera" })).toBeInTheDocument();
    expect(within(table).getAllByRole("row")).toHaveLength(3);
  });

  it("marks the header sticky", () => {
    render(<Table caption="Approvals waiting" columns={columns} rows={rows} rowKey={(r: Row) => r.id} empty="Nothing waiting" />);
    expect(screen.getByRole("table").querySelector("thead")).toHaveClass("ui-table__head--sticky");
  });

  it("renders one empty row spanning every column", () => {
    render(<Table caption="Approvals waiting" columns={columns} rows={[]} rowKey={(r: Row) => r.id} empty="Nothing waiting" />);
    const cell = screen.getByRole("cell", { name: "Nothing waiting" });
    expect(cell).toHaveAttribute("colspan", "2");
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
  });

  it("can hide the caption visually and still name the table", () => {
    render(<Table caption="Approvals waiting" hideCaption columns={columns} rows={rows} rowKey={(r: Row) => r.id} empty="Nothing waiting" />);
    expect(screen.getByRole("table", { name: "Approvals waiting" }).querySelector("caption")).toHaveClass("ui-sr-only");
  });

  it("renders a column with a custom render function", () => {
    render(
      <Table
        caption="Approvals waiting"
        columns={[{ key: "name", header: "Candidate", render: (r: Row) => <strong>{r.name.toUpperCase()}</strong> }]}
        rows={rows}
        rowKey={(r: Row) => r.id}
        empty="Nothing waiting"
      />,
    );
    expect(screen.getByText("ANDRÉS SOTO").tagName).toBe("STRONG");
  });
});

describe("Tag", () => {
  it("renders its children with a neutral tone by default", () => {
    render(<Tag>Full-time</Tag>);
    expect(screen.getByText("Full-time")).toHaveClass("ui-tag", "ui-tag--neutral");
  });

  it("renders the Takeover and Stalled flags as tags", () => {
    render(
      <>
        <Tag tone="warn">Takeover</Tag>
        <Tag tone="info">Stalled</Tag>
      </>,
    );
    expect(screen.getByText("Takeover")).toHaveClass("ui-tag--warn");
    expect(screen.getByText("Stalled")).toHaveClass("ui-tag--info");
  });
});

describe("StatusBadge", () => {
  // CONTEXT.md terms. Takeover and Stalled are flags (Tag), not stages or outcomes.
  const LABELS: readonly (readonly [StatusKind, string])[] = [
    ["screening", "Screening"],
    ["qualified", "Qualified"],
    ["approved", "Approved"],
    ["interview_booked", "Interview"],
    ["knockout", "Knockout"],
    ["rejection", "Rejection"],
    ["abandoned", "Abandoned"],
    ["withdrawn", "Withdrawn"],
  ];
  const AVOID = ["Hired", "passed", "Result", "status", "cancelled", "expired", "Inactive"];

  it.each(LABELS)("labels %s as %s", (kind, label) => {
    const { container } = render(<StatusBadge status={kind} />);
    const badge = container.firstElementChild as HTMLElement;
    expect(badge).toHaveClass("ui-badge", `ui-badge--${kind}`);
    expect(badge.textContent).toBe(label);
  });

  it("covers exactly the eight stages and outcomes", () => {
    expect(LABELS.map(([kind]) => kind)).toHaveLength(8);
    expect(new Set(LABELS.map(([, label]) => label)).size).toBe(8);
  });

  it("uses no avoided word in any label", () => {
    for (const [kind] of LABELS) {
      const { container, unmount } = render(<StatusBadge status={kind} />);
      for (const word of AVOID) expect(container.textContent?.toLowerCase()).not.toContain(word.toLowerCase());
      unmount();
    }
  });

  it("hides the status dot from assistive technology", () => {
    const { container } = render(<StatusBadge status="qualified" />);
    expect(container.querySelector(".ui-badge__dot")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("Card", () => {
  it("is a region named by its title, with actions and a count", () => {
    render(
      <Card title="Approvals waiting" count={3} actions={<a href="#queue">Review queue</a>}>
        <p>Rows</p>
      </Card>,
    );
    const region = screen.getByRole("region", { name: "Approvals waiting" });
    expect(within(region).getByRole("heading", { name: /Approvals waiting/ })).toBeInTheDocument();
    expect(within(region).getByText("3")).toBeInTheDocument();
    expect(within(region).getByRole("link", { name: "Review queue" })).toBeInTheDocument();
    expect(within(region).getByText("Rows")).toBeInTheDocument();
  });

  it("renders a plain panel without a title", () => {
    render(
      <Card>
        <p>Body</p>
      </Card>,
    );
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    expect(screen.getByText("Body").parentElement).toHaveClass("ui-card");
  });
});

describe("Skeleton", () => {
  it("is a status named Loading by default", () => {
    render(<Skeleton />);
    expect(screen.getByRole("status", { name: "Loading" })).toHaveClass("ui-skeleton");
  });

  it("takes a custom label and a shape", () => {
    render(<Skeleton label="Loading Applications" shape="block" />);
    expect(screen.getByRole("status", { name: "Loading Applications" })).toHaveClass("ui-skeleton--block");
  });

  it("stops its shimmer under reduced motion", () => {
    const css = readStyle("components/ui/ui.css");
    const block = css.slice(css.indexOf("prefers-reduced-motion: reduce"));
    expect(block).toContain(".ui-skeleton");
    expect(block).toContain("animation: none");
  });
});

describe("Dialog", () => {
  function Demo() {
    return (
      <Dialog
        trigger={<Button>Open dialog</Button>}
        title="Reject application"
        description="This ends the Application with a Rejection."
      >
        <Button variant="danger">Reject</Button>
      </Dialog>
    );
  }

  it("shows a title and description once opened", async () => {
    render(<Demo />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open dialog" }));
    const dialog = await screen.findByRole("dialog", { name: "Reject application" });
    expect(dialog).toHaveAccessibleDescription("This ends the Application with a Rejection.");
  });

  it("traps focus inside the dialog", async () => {
    render(<Demo />);
    fireEvent.click(screen.getByRole("button", { name: "Open dialog" }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    const buttons = within(dialog).getAllByRole("button");
    const first = buttons[0] as HTMLElement;
    const last = buttons[buttons.length - 1] as HTMLElement;
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
  });

  it("restores focus to the trigger on close", async () => {
    render(<Demo />);
    const trigger = screen.getByRole("button", { name: "Open dialog" });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("closes from its own close button", async () => {
    render(<Demo />);
    fireEvent.click(screen.getByRole("button", { name: "Open dialog" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("Toast", () => {
  function Demo({ onOpenChange }: { onOpenChange?: (open: boolean) => void }) {
    const [open, setOpen] = useState(false);
    return (
      <>
        <Button onClick={() => setOpen(true)}>Show toast</Button>
        <Toast
          open={open}
          onOpenChange={(next) => {
            setOpen(next);
            onOpenChange?.(next);
          }}
          title="Approved"
          description="Diego Martínez can now book."
          duration={4000}
        />
      </>
    );
  }

  it("shows, then dismisses itself after its duration", () => {
    vi.useFakeTimers();
    render(<Demo />);
    expect(screen.queryByText("Approved")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show toast" }));
    expect(screen.getAllByText("Approved").length).toBeGreaterThan(0);
    act(() => {
      vi.advanceTimersByTime(4500);
    });
    expect(screen.queryByText("Approved")).not.toBeInTheDocument();
  });

  it("dismisses from its own button", () => {
    const onOpenChange = vi.fn();
    render(<Demo onOpenChange={onOpenChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Show toast" }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onOpenChange).toHaveBeenLastCalledWith(false);
    expect(screen.queryByText("Approved")).not.toBeInTheDocument();
  });
});

describe("Tabs", () => {
  const tabs = [
    { value: "overview", label: "Overview", content: <p>Overview body</p> },
    { value: "transcript", label: "Transcript", content: <p>Transcript body</p> },
    { value: "events", label: "Events", content: <p>Events body</p> },
  ];

  it("selects the first tab and shows its panel", () => {
    render(<Tabs label="Application sections" tabs={tabs} />);
    expect(screen.getByRole("tablist", { name: "Application sections" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Overview body");
  });

  it("moves with the arrow keys, wrapping at the ends", async () => {
    render(<Tabs label="Application sections" tabs={tabs} />);
    const [overview, transcript, events] = screen.getAllByRole("tab");
    overview?.focus();
    fireEvent.keyDown(overview as HTMLElement, { key: "ArrowRight" });
    await waitFor(() => expect(transcript).toHaveFocus());
    await waitFor(() => expect(transcript).toHaveAttribute("aria-selected", "true"));
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Transcript body");
    fireEvent.keyDown(transcript as HTMLElement, { key: "ArrowLeft" });
    await waitFor(() => expect(overview).toHaveFocus());
    fireEvent.keyDown(overview as HTMLElement, { key: "ArrowLeft" });
    await waitFor(() => expect(events).toHaveFocus());
  });

  it("starts on defaultValue", () => {
    render(<Tabs label="Application sections" tabs={tabs} defaultValue="events" />);
    expect(screen.getByRole("tab", { name: "Events" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("ErrorState", () => {
  it("shows the message and calls onRetry once", () => {
    const onRetry = vi.fn();
    render(<ErrorState message="Could not load Applications." onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load Applications.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("Icon", () => {
  it("is hidden from assistive technology by default", () => {
    const { container } = render(<Icon name="check" />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("is an image with a name when given a label", () => {
    render(<Icon name="check" label="Done" />);
    expect(screen.getByRole("img", { name: "Done" })).toBeInTheDocument();
  });
});

describe("theme", () => {
  it("pins the storage key", () => {
    expect(THEME_STORAGE_KEY).toBe("criba.theme");
  });

  it("applyTheme writes data-theme only for light and dark", () => {
    const root = document.documentElement;
    applyTheme("light");
    expect(root).toHaveAttribute("data-theme", "light");
    applyTheme("dark");
    expect(root).toHaveAttribute("data-theme", "dark");
    applyTheme("system");
    expect(root).not.toHaveAttribute("data-theme");
  });

  it.each(["bogus", "", "Light", "dark ", "<script>", null, undefined])("applyTheme(%j) removes the attribute", (value) => {
    document.documentElement.setAttribute("data-theme", "dark");
    applyTheme(value);
    expect(document.documentElement).not.toHaveAttribute("data-theme");
  });

  it("readStoredTheme returns light, dark or system", () => {
    expect(readStoredTheme()).toBe("system");
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    expect(readStoredTheme()).toBe("light");
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    expect(readStoredTheme()).toBe("dark");
    window.localStorage.setItem(THEME_STORAGE_KEY, "bogus");
    expect(readStoredTheme()).toBe("system");
  });

  it("saveTheme stores light and dark and clears the key for system", () => {
    expect(saveTheme("dark")).toBe(true);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(saveTheme("system")).toBe(true);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it("reads and saves safely when localStorage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(readStoredTheme()).toBe("system");
    expect(saveTheme("dark")).toBe(false);
    expect(saveTheme("system")).toBe(false);
  });
});

describe("ThemeToggle", () => {
  it("states the current mode in its name and cycles light, dark, system", () => {
    render(<ThemeToggle />);
    const root = document.documentElement;
    const button = () => screen.getByRole("button", { name: /^Theme:/ });
    expect(button()).toHaveAccessibleName(/Theme: system/);
    expect(root).not.toHaveAttribute("data-theme");

    fireEvent.click(button());
    expect(button()).toHaveAccessibleName(/Theme: light/);
    expect(root).toHaveAttribute("data-theme", "light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");

    fireEvent.click(button());
    expect(button()).toHaveAccessibleName(/Theme: dark/);
    expect(root).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    fireEvent.click(button());
    expect(button()).toHaveAccessibleName(/Theme: system/);
    expect(root).not.toHaveAttribute("data-theme");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it("starts from the stored value and applies it", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    render(<ThemeToggle />);
    expect(screen.getByRole("button")).toHaveAccessibleName(/Theme: dark/);
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  it("starts from initialMode without writing storage", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    render(<ThemeToggle initialMode="dark" />);
    expect(screen.getByRole("button")).toHaveAccessibleName(/Theme: dark/);
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });

  it("still switches data-theme when localStorage throws", () => {
    for (const method of ["getItem", "setItem", "removeItem"] as const) {
      vi.spyOn(Storage.prototype, method).mockImplementation(() => {
        throw new Error("denied");
      });
    }
    render(<ThemeToggle />);
    const root = document.documentElement;
    fireEvent.click(screen.getByRole("button"));
    expect(root).toHaveAttribute("data-theme", "light");
    fireEvent.click(screen.getByRole("button"));
    expect(root).toHaveAttribute("data-theme", "dark");
    fireEvent.click(screen.getByRole("button"));
    expect(root).not.toHaveAttribute("data-theme");
  });
});

describe("MotionProvider", () => {
  it("renders its children inside a MotionConfig with reducedMotion user", () => {
    vi.mocked(MotionConfig).mockClear();
    render(
      <MotionProvider>
        <p>Inside</p>
      </MotionProvider>,
    );
    expect(screen.getByText("Inside")).toBeInTheDocument();
    expect(vi.mocked(MotionConfig)).toHaveBeenCalled();
    expect(vi.mocked(MotionConfig).mock.calls[0]?.[0]).toMatchObject({ reducedMotion: "user" });
  });
});

describe("motion presets", () => {
  const tokens = readStyle("styles/tokens.css");
  const token = (name: string) => new RegExp(`${name}:\\s*([^;]+);`).exec(tokens)?.[1]?.trim();

  it.each([
    ["--motion-fast", DURATION_FAST],
    ["--motion-base", DURATION_BASE],
    ["--motion-slow", DURATION_SLOW],
  ] as const)("%s matches its duration in seconds", (name, seconds) => {
    expect(token(name)).toBe(`${Math.round(seconds * 1000)}ms`);
  });

  it.each([
    ["--ease-out", EASE_OUT],
    ["--ease-in-out", EASE_IN_OUT],
  ] as const)("%s matches its curve", (name, curve) => {
    expect(token(name)).toBe(`cubic-bezier(${curve.join(", ")})`);
  });

  it("keeps every duration under a third of a second", () => {
    for (const seconds of [DURATION_FAST, DURATION_BASE, DURATION_SLOW]) expect(seconds).toBeLessThanOrEqual(0.32);
  });

  it("zeroes every duration and stops loops app-wide under reduced motion", () => {
    const css = readStyle("components/ui/ui.css");
    const global = /@media \(prefers-reduced-motion: reduce\) \{\s*\*,\s*\*::before,\s*\*::after \{([^}]+)\}/.exec(css)?.[1] ?? "";
    for (const rule of [
      "animation-duration: 0s !important",
      "animation-iteration-count: 1 !important",
      "transition-duration: 0s !important",
    ]) {
      expect(global).toContain(rule);
    }
  });
});

describe("Client accent token", () => {
  const tokens = readStyle("styles/tokens.css");
  const luminance = (hex: string) => {
    const [r, g, b] = [1, 3, 5].map((i) => {
      const c = parseInt(hex.slice(i, i + 2), 16) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const contrast = (a: string, b: string) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const pair = (name: string) => {
    const match = new RegExp(`${name}:\\s*light-dark\\((#[0-9a-f]{6}),\\s*(#[0-9a-f]{6})\\)`, "i").exec(tokens);
    return { light: match?.[1]?.toLowerCase() ?? "", dark: match?.[2]?.toLowerCase() ?? "" };
  };

  it("is the approved Grupo Sazón colour in light", () => {
    expect(pair("--client-accent").light).toBe("#c8462b");
  });

  it("meets 3:1 on the dark ground and panel", () => {
    const { dark } = pair("--client-accent");
    expect(contrast(dark, pair("--ground").dark)).toBeGreaterThanOrEqual(3);
    expect(contrast(dark, pair("--panel").dark)).toBeGreaterThanOrEqual(3);
  });
});

describe("Specimen", () => {
  const exported = Object.entries(ui);
  const components = COMPONENT_NAMES;
  const others = exported.map(([name]) => name).filter((name) => !components.includes(name));

  it("exports the fourteen primitives", () => {
    expect(components.toSorted()).toEqual(
      ["Button", "Card", "Dialog", "ErrorState", "Field", "Icon", "MotionProvider", "Skeleton", "StatusBadge", "Table", "Tabs", "Tag", "ThemeToggle", "Toast"].toSorted(),
    );
  });

  it("renders every component under a section heading with its name", () => {
    render(<Specimen />);
    for (const name of components) {
      expect(screen.getByRole("heading", { level: 2, name }), name).toBeInTheDocument();
    }
  });

  it("names every other export on the page", () => {
    render(<Specimen />);
    const text = document.body.textContent ?? "";
    for (const name of others) expect(text, name).toContain(name);
  });

  it("links every section in its contents to a heading on the page", () => {
    render(<Specimen />);
    const links = within(screen.getByRole("navigation")).getAllByRole("link");
    expect(links.length).toBeGreaterThan(components.length);
    for (const link of links) {
      const target = document.getElementById((link.getAttribute("href") ?? "").slice(1));
      expect(target, link.textContent ?? "").not.toBeNull();
      expect(within(target as HTMLElement).getByRole("heading", { level: 2, name: link.textContent ?? "" })).toBeInTheDocument();
    }
  });

  it("shows every StatusBadge variant and the Takeover and Stalled flags as tags", () => {
    render(<Specimen />);
    const section = document.getElementById("statusbadge") as HTMLElement;
    const badges = Array.from(section.querySelectorAll(".ui-badge"), (badge) => badge.textContent);
    expect(badges).toEqual(["Screening", "Qualified", "Approved", "Interview", "Knockout", "Rejection", "Abandoned", "Withdrawn"]);
    expect(screen.getByText("Takeover", { selector: ".ui-tag" })).toBeInTheDocument();
    expect(screen.getByText("Stalled", { selector: ".ui-tag" })).toBeInTheDocument();
  });

  it("starts in the theme named by ?theme= and switches with the header toggle", () => {
    window.history.replaceState(null, "", "/specimen.html?theme=dark");
    try {
      render(<Specimen />);
      expect(document.documentElement).toHaveAttribute("data-theme", "dark");
      fireEvent.click(screen.getByRole("button", { name: /^Theme: dark/ }));
      expect(document.documentElement).not.toHaveAttribute("data-theme");
    } finally {
      window.history.replaceState(null, "", "/");
    }
  });

  it("opens its dialog and shows a toast from the triggers", async () => {
    render(<Specimen />);
    fireEvent.click(screen.getByRole("button", { name: "Open dialog" }));
    expect(await screen.findByRole("dialog", { name: "Reject Lucía Herrera" })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Show success toast" }));
    expect(screen.getAllByText("Approved").length).toBeGreaterThan(0);
  });
});

// DESIGN.md is written from the built system; these checks keep it equal to tokens.css and complete.
describe("DESIGN.md", () => {
  const designMd = readFileSync(join(import.meta.dirname, "../../DESIGN.md"), "utf8");
  const tokensCss = readStyle("styles/tokens.css");
  const HEADINGS = ["Overview", "Colors", "Typography", "Layout", "Elevation & Depth", "Shapes", "Components", "Do's and Don'ts"];

  function split(text: string): { frontmatter: string; body: string } {
    const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n([\s\S]*)$/.exec(text);
    return { frontmatter: match?.[1] ?? "", body: match?.[2] ?? "" };
  }

  // `  ground: { light: "#f6f3ec", dark: "#161b12" }` lines under `colors:`.
  function frontmatterColours(frontmatter: string): Map<string, { light: string; dark: string }> {
    const colours = new Map<string, { light: string; dark: string }>();
    let inColours = false;
    for (const line of frontmatter.split(/\r?\n/)) {
      if (/^\S/.test(line)) inColours = line.trim() === "colors:";
      else if (inColours) {
        const m = /^\s+([\w-]+):\s*\{\s*light:\s*"(#[0-9a-fA-F]+)"\s*,\s*dark:\s*"(#[0-9a-fA-F]+)"\s*\}\s*$/.exec(line);
        if (m) colours.set(m[1] as string, { light: (m[2] as string).toLowerCase(), dark: (m[3] as string).toLowerCase() });
      }
    }
    return colours;
  }

  // `--ground: light-dark(#f6f3ec, #161b12);` with hex values only (rgba tokens are not colours here).
  function tokenColours(css: string): Map<string, { light: string; dark: string }> {
    const tokens = new Map<string, { light: string; dark: string }>();
    for (const m of css.matchAll(/--([\w-]+):\s*light-dark\(\s*(#[0-9a-fA-F]+)\s*,\s*(#[0-9a-fA-F]+)\s*\)\s*;/g)) {
      tokens.set(m[1] as string, { light: (m[2] as string).toLowerCase(), dark: (m[3] as string).toLowerCase() });
    }
    return tokens;
  }

  // Problems with the frontmatter colours against tokens.css; empty when they agree.
  function colourProblems(colours: Map<string, { light: string; dark: string }>, tokens: Map<string, { light: string; dark: string }>): string[] {
    const problems: string[] = [];
    if (colours.size < 19) problems.push(`only ${colours.size} frontmatter colours, expected at least 19`);
    for (const [name, value] of colours) {
      const token = tokens.get(name);
      if (!token) problems.push(`colour "${name}" has no light-dark() token --${name} in tokens.css`);
      else {
        if (token.light !== value.light) problems.push(`colour "${name}" light is ${value.light}, tokens.css --${name} is ${token.light}`);
        if (token.dark !== value.dark) problems.push(`colour "${name}" dark is ${value.dark}, tokens.css --${name} is ${token.dark}`);
      }
    }
    return problems;
  }

  const SEEDS = ["#f6f3ec", "#343f2b", "#97ff48"];
  const seedProblems = (colours: Map<string, { light: string; dark: string }>): string[] =>
    SEEDS.filter((seed) => !Array.from(colours.values()).some((value) => value.light === seed)).map(
      (seed) => `no named colour has light value ${seed} (a seed colour)`,
    );

  const headingsOf = (body: string): string[] =>
    Array.from(body.replace(/^```[\s\S]*?^```/gm, "").matchAll(/^## (.+?)\s*$/gm), (m) => m[1] as string);

  const missingTerms = (body: string, terms: readonly string[]): string[] => terms.filter((term) => !body.includes(term)).map((term) => `body never mentions ${term}`);

  const { frontmatter, body } = split(designMd);
  const colours = frontmatterColours(frontmatter);
  const tokens = tokenColours(tokensCss);
  const motionNames = Object.keys(ui).filter((name) => /^(DURATION|EASE)_/.test(name));
  const REQUIRED_TERMS = [
    "criba.theme",
    "light-dark(",
    ...motionNames,
    "@radix-ui/react-icons",
    "--client-accent",
    "prefers-reduced-motion",
    ...COMPONENT_NAMES,
  ];

  it("has a frontmatter block and a body", () => {
    expect(frontmatter, "DESIGN.md must start with a --- frontmatter block").toContain("colors:");
    expect(body.length, "DESIGN.md has no body after the frontmatter").toBeGreaterThan(0);
  });

  it("derives the motion names it must mention from the exports", () => {
    expect(motionNames.toSorted()).toEqual(["DURATION_BASE", "DURATION_FAST", "DURATION_SLOW", "EASE_IN_OUT", "EASE_OUT"]);
  });

  it("holds every colour at its tokens.css value, light and dark, with at least 19 colours", () => {
    expect(colourProblems(colours, tokens)).toEqual([]);
  });

  it("names the three seed colours", () => {
    expect(seedProblems(colours)).toEqual([]);
  });

  it("has exactly the eight headings, in order", () => {
    expect(headingsOf(body)).toEqual(HEADINGS);
  });

  it("mentions the theme key, light-dark(), every motion and component name, the icon package, the client accent and reduced motion", () => {
    expect(missingTerms(body, REQUIRED_TERMS)).toEqual([]);
  });

  it("reports a wrong, missing or surplus value with a message that names it", () => {
    const wrongLight = new Map(colours).set("ground", { light: "#ffffff", dark: "#161b12" });
    expect(colourProblems(wrongLight, tokens)).toEqual(['colour "ground" light is #ffffff, tokens.css --ground is #f6f3ec']);
    const wrongDark = new Map(colours).set("panel", { light: "#ffffff", dark: "#000000" });
    expect(colourProblems(wrongDark, tokens)).toEqual(['colour "panel" dark is #000000, tokens.css --panel is #20271a']);
    const unknown = new Map(colours).set("invented", { light: "#123456", dark: "#654321" });
    expect(colourProblems(unknown, tokens)).toEqual(['colour "invented" has no light-dark() token --invented in tokens.css']);
    expect(colourProblems(new Map(Array.from(colours).slice(0, 5)), tokens)).toEqual(["only 5 frontmatter colours, expected at least 19"]);
    const noSeed = new Map(colours);
    noSeed.delete("ground");
    expect(seedProblems(noSeed)).toEqual(["no named colour has light value #f6f3ec (a seed colour)"]);
    expect(headingsOf("## Overview\n## Shapes\n## Colors\n")).not.toEqual(HEADINGS);
    expect(missingTerms(body.replaceAll("DURATION_FAST", "x"), REQUIRED_TERMS)).toEqual(["body never mentions DURATION_FAST"]);
  });
});
