// The left navigation (operator console ui-shell, UI1 to UI3): the items per role, the collapse state in
// lib/navState.ts, and the nesting of the content area that the ui-detail child builds on. Written
// before components/LeftNav.tsx and lib/navState.ts exist; shell.test.tsx keeps the older Shell checks.
import { act, fireEvent, renderHook, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readNav, saveNav, useNavOpen } from "../../src/lib/navState.ts";
import { mockApi, renderRoute } from "./helpers.tsx";
import { locations, operatorMe, recruiterMe } from "./fixtures.ts";

const NAV_KEY = "criba.nav";
const WIDE_QUERY = "(min-width: 1440px)";

const common = {
  "GET /api/applications": { items: [], next_cursor: null, total: 0 },
  "GET /api/locations": { items: locations },
  "GET /api/slots": { items: [] },
};

const OPERATOR_ITEMS = ["Overview", "Applications", "Review queue", "Interview slots", "Results", "Test chat", "Test voice", "Economics"];
const RECRUITER_ITEMS = ["Today", "Applications", "Review queue", "Interview slots", "Results", "Test chat", "Test voice"];
const HREFS: Record<string, string> = {
  Today: "/today",
  Overview: "/overview",
  Applications: "/applications",
  "Review queue": "/queue",
  "Interview slots": "/slots",
  Results: "/results",
  "Test chat": "/conversations",
  "Test voice": "/voice",
  Economics: "/economics",
};

const previewOn = () => window.sessionStorage.setItem("criba.previewRecruiter", "1");

/** A `matchMedia` stub whose one MediaQueryList can be flipped by hand, like a window resize. */
function stubMedia(matches: boolean) {
  const listeners = new Set<(event: { matches: boolean }) => void>();
  const queries: string[] = [];
  const list = {
    matches,
    media: "",
    onchange: null,
    addEventListener: (_type: string, fn: (event: { matches: boolean }) => void) => listeners.add(fn),
    removeEventListener: (_type: string, fn: (event: { matches: boolean }) => void) => listeners.delete(fn),
    addListener: (fn: (event: { matches: boolean }) => void) => listeners.add(fn),
    removeListener: (fn: (event: { matches: boolean }) => void) => listeners.delete(fn),
    dispatchEvent: () => false,
  };
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => {
      queries.push(query);
      return list;
    }),
  );
  return {
    queries,
    change(next: boolean) {
      list.matches = next;
      act(() => listeners.forEach((fn) => fn({ matches: next })));
    },
  };
}

/** Make one method of `localStorage` (only that Storage, not sessionStorage) throw. */
function breakLocalStorage(method: "getItem" | "setItem") {
  const original = Storage.prototype[method] as (this: Storage, ...args: string[]) => unknown;
  vi.spyOn(Storage.prototype, method).mockImplementation(function (this: Storage, ...args: string[]) {
    if (this === window.localStorage) throw new DOMException("storage blocked", "SecurityError");
    return original.apply(this, args);
  } as never);
}

afterEach(() => {
  vi.restoreAllMocks();
});

async function renderShell(me: typeof operatorMe, path = "/applications") {
  mockApi({ ...common, "GET /api/auth/me": me });
  const view = renderRoute(path);
  const nav = await screen.findByRole("navigation", { name: "Main" });
  return { ...view, nav };
}

const linksIn = (nav: HTMLElement) => within(nav).getAllByRole("link");
const labelsIn = (nav: HTMLElement) => linksIn(nav).map((a) => a.textContent);
const toggleButton = () => screen.getByRole("button", { name: /^(Collapse|Expand) navigation$/ });

describe("UI1 items per role", () => {
  it("lists the Operator's items with Overview first and Economics last", async () => {
    const { nav } = await renderShell(operatorMe);
    expect(labelsIn(nav)).toEqual(OPERATOR_ITEMS);
  });

  it("lists the Recruiter's items with Today first and no Economics", async () => {
    const { nav } = await renderShell(recruiterMe);
    expect(labelsIn(nav)).toEqual(RECRUITER_ITEMS);
  });

  it("lists the Recruiter's items for an Operator previewing the Recruiter view", async () => {
    previewOn();
    const { nav } = await renderShell(operatorMe);
    expect(labelsIn(nav)).toEqual(RECRUITER_ITEMS);
  });

  it("switches the list when the Operator starts and ends the preview", async () => {
    const { nav } = await renderShell(operatorMe);
    expect(labelsIn(nav)).toEqual(OPERATOR_ITEMS);
    fireEvent.click(screen.getByRole("button", { name: "Preview as Recruiter" }));
    expect(labelsIn(await screen.findByRole("navigation", { name: "Main" }))).toEqual(RECRUITER_ITEMS);
    fireEvent.click(screen.getByRole("button", { name: "Back to Operator view" }));
    expect(labelsIn(await screen.findByRole("navigation", { name: "Main" }))).toEqual(OPERATOR_ITEMS);
  });

  it("links each item to its page and never to /stage", async () => {
    const { nav } = await renderShell(operatorMe);
    for (const link of linksIn(nav)) expect(link).toHaveAttribute("href", HREFS[link.textContent ?? ""]);
    expect(linksIn(nav).map((a) => a.getAttribute("href"))).not.toContain("/stage");
  });

  it("links the Recruiter's Test chat to /test-chat, label and icon unchanged", async () => {
    const { nav } = await renderShell(recruiterMe);
    const link = within(nav).getByRole("link", { name: "Test chat" });
    expect(link).toHaveAttribute("href", "/test-chat");
    expect(link.querySelector("svg")).not.toBeNull();
  });

  it("links Test chat to /test-chat for an Operator previewing the Recruiter view, label and icon unchanged", async () => {
    previewOn();
    const { nav } = await renderShell(operatorMe);
    const link = within(nav).getByRole("link", { name: "Test chat" });
    expect(link).toHaveAttribute("href", "/test-chat");
    expect(link.querySelector("svg")).not.toBeNull();
  });

  it("gives every link exactly its label as text and no differing aria-label", async () => {
    for (const me of [operatorMe, recruiterMe]) {
      const { nav, unmount } = await renderShell(me);
      for (const link of linksIn(nav)) {
        const label = link.textContent ?? "";
        expect(label).toBe(label.trim());
        expect(Object.keys(HREFS)).toContain(label);
        const aria = link.getAttribute("aria-label");
        expect(aria === null || aria === label).toBe(true);
        expect(screen.getAllByRole("link", { name: label }).length).toBeGreaterThan(0);
      }
      unmount();
    }
  });

  it("has no top bar", async () => {
    const { container } = await renderShell(operatorMe);
    expect(document.querySelector("header.topbar")).toBeNull();
    expect(container.querySelector(".topbar")).toBeNull();
  });

  it("marks the current page's link with aria-current and no other", async () => {
    const { nav } = await renderShell(operatorMe, "/applications");
    const current = linksIn(nav).filter((a) => a.getAttribute("aria-current") === "page");
    expect(current.map((a) => a.textContent)).toEqual(["Applications"]);
  });

  it("moves aria-current with the route", async () => {
    const { nav } = await renderShell(recruiterMe, "/slots");
    const current = linksIn(nav).filter((a) => a.getAttribute("aria-current") === "page");
    expect(current.map((a) => a.textContent)).toEqual(["Interview slots"]);
  });

  it("keeps the brand link outside the Main navigation, in the column", async () => {
    const { nav, container } = await renderShell(operatorMe);
    const brand = screen.getByRole("link", { name: "Criba" });
    expect(brand).toHaveAttribute("href", "/");
    expect(nav).not.toContainElement(brand);
    expect(container.querySelector(".shell-frame")?.children[0]).toContainElement(brand);
  });

  it("keeps the Client name outside the Main navigation for a Recruiter and under preview", async () => {
    const recruiter = await renderShell(recruiterMe);
    const name = screen.getByText("Grupo Sazón");
    expect(recruiter.nav).not.toContainElement(name);
    expect(recruiter.container.querySelector(".shell-frame")?.children[0]).toContainElement(name);
    recruiter.unmount();

    previewOn();
    const preview = await renderShell(operatorMe);
    expect(preview.nav).not.toContainElement(screen.getByText("Grupo Sazón"));
  });

  it("puts only page links in the Main navigation", async () => {
    const { nav } = await renderShell(operatorMe);
    expect(within(nav).queryAllByRole("button")).toHaveLength(0);
    expect(within(nav).queryByTestId("role-badge")).toBeNull();
    expect(within(nav).queryByRole("link", { name: "Criba" })).toBeNull();
  });

  it("keeps the role badge, theme toggle, preview and log out in the column, outside Main", async () => {
    const { nav, container } = await renderShell(operatorMe);
    const column = container.querySelector(".shell-frame")?.children[0] as HTMLElement;
    for (const element of [
      screen.getByTestId("role-badge"),
      screen.getByRole("button", { name: /^Theme: / }),
      screen.getByRole("button", { name: "Preview as Recruiter" }),
      screen.getByRole("button", { name: "Log out" }),
    ]) {
      expect(column).toContainElement(element);
      expect(nav).not.toContainElement(element);
    }
  });

  it("has no role=status in the Shell outside the page, until the preview banner appears", async () => {
    const shellStatuses = (container: HTMLElement) =>
      [...container.querySelectorAll('.shell-frame [role="status"]')].filter((el) => !el.closest("main.page"));
    const { container } = await renderShell(operatorMe);
    expect(shellStatuses(container)).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Preview as Recruiter" }));
    const banner = await screen.findByText(/Previewing the Recruiter view/);
    expect(shellStatuses(container)).toHaveLength(1);
    expect(shellStatuses(container)[0]).toBe(banner.closest('[role="status"]'));
    expect(shellStatuses(container)[0]).toHaveTextContent(
      "Previewing the Recruiter view. Economics views and cost fields are hidden.",
    );
    expect(within(shellStatuses(container)[0] as HTMLElement).getByRole("button", { name: "Back to Operator view" })).toBeInTheDocument();
  });
});

describe("UI2 navState", () => {
  it("reads null with nothing stored", () => {
    expect(readNav()).toBeNull();
  });

  it.each(["open", "collapsed"] as const)("reads %s once it is stored", (value) => {
    window.localStorage.setItem(NAV_KEY, value);
    expect(readNav()).toBe(value);
  });

  it.each(["", "Open", "COLLAPSED", "true", "1", "open ", '"open"', "rail"])("ignores the stored value %j", (value) => {
    window.localStorage.setItem(NAV_KEY, value);
    expect(readNav()).toBeNull();
  });

  it("reads null when localStorage throws on read", () => {
    window.localStorage.setItem(NAV_KEY, "open");
    breakLocalStorage("getItem");
    expect(readNav()).toBeNull();
  });

  it.each(["open", "collapsed"] as const)("stores exactly %s under criba.nav", (value) => {
    saveNav(value);
    expect(window.localStorage.getItem(NAV_KEY)).toBe(value);
  });

  it("does not throw when localStorage throws on write", () => {
    breakLocalStorage("setItem");
    expect(() => saveNav("open")).not.toThrow();
  });

  it("does not touch the theme or preview keys", () => {
    saveNav("collapsed");
    expect(window.localStorage.length).toBe(1);
    expect(window.sessionStorage.length).toBe(0);
  });
});

describe("UI2 useNavOpen", () => {
  it("defaults to open when (min-width: 1440px) matches", () => {
    const media = stubMedia(true);
    const { result } = renderHook(() => useNavOpen());
    expect(result.current[0]).toBe(true);
    expect(media.queries).toContain(WIDE_QUERY);
  });

  it("defaults to collapsed when it does not match", () => {
    stubMedia(false);
    const { result } = renderHook(() => useNavOpen());
    expect(result.current[0]).toBe(false);
  });

  it("defaults to collapsed when matchMedia is missing", () => {
    vi.stubGlobal("matchMedia", undefined);
    const { result } = renderHook(() => useNavOpen());
    expect(result.current[0]).toBe(false);
  });

  it("lets a stored value win over matchMedia, both ways", () => {
    stubMedia(true);
    window.localStorage.setItem(NAV_KEY, "collapsed");
    expect(renderHook(() => useNavOpen()).result.current[0]).toBe(false);

    stubMedia(false);
    window.localStorage.setItem(NAV_KEY, "open");
    expect(renderHook(() => useNavOpen()).result.current[0]).toBe(true);
  });

  it("ignores a bad stored value and uses the default", () => {
    stubMedia(true);
    window.localStorage.setItem(NAV_KEY, "sideways");
    expect(renderHook(() => useNavOpen()).result.current[0]).toBe(true);
  });

  it("uses the default when localStorage throws on read", () => {
    stubMedia(true);
    breakLocalStorage("getItem");
    expect(renderHook(() => useNavOpen()).result.current[0]).toBe(true);
  });

  it("follows change events until the first toggle", () => {
    const media = stubMedia(false);
    const { result } = renderHook(() => useNavOpen());
    expect(result.current[0]).toBe(false);
    media.change(true);
    expect(result.current[0]).toBe(true);
    media.change(false);
    expect(result.current[0]).toBe(false);
  });

  it("stops following change events after a toggle", () => {
    const media = stubMedia(false);
    const { result } = renderHook(() => useNavOpen());
    act(() => result.current[1]());
    expect(result.current[0]).toBe(true);
    media.change(true);
    expect(result.current[0]).toBe(true);
    media.change(false);
    expect(result.current[0]).toBe(true);
  });

  it("flips on each toggle and stores exactly open or collapsed", () => {
    stubMedia(true);
    const { result } = renderHook(() => useNavOpen());
    act(() => result.current[1]());
    expect(result.current[0]).toBe(false);
    expect(window.localStorage.getItem(NAV_KEY)).toBe("collapsed");
    act(() => result.current[1]());
    expect(result.current[0]).toBe(true);
    expect(window.localStorage.getItem(NAV_KEY)).toBe("open");
  });

  it("still flips when localStorage throws on write", () => {
    stubMedia(true);
    breakLocalStorage("setItem");
    const { result } = renderHook(() => useNavOpen());
    act(() => result.current[1]());
    expect(result.current[0]).toBe(false);
    act(() => result.current[1]());
    expect(result.current[0]).toBe(true);
  });

  it("removes its change listener on unmount", () => {
    const media = stubMedia(false);
    const { result, unmount } = renderHook(() => useNavOpen());
    unmount();
    expect(() => media.change(true)).not.toThrow();
    expect(result.current[0]).toBe(false);
  });
});

describe("UI2 the collapse button in the Shell", () => {
  it("is open and named Collapse navigation when the window is wide", async () => {
    stubMedia(true);
    await renderShell(operatorMe);
    expect(toggleButton()).toHaveAccessibleName("Collapse navigation");
    expect(toggleButton()).toHaveAttribute("aria-expanded", "true");
  });

  it("is collapsed and named Expand navigation when the window is narrow", async () => {
    stubMedia(false);
    await renderShell(operatorMe);
    expect(toggleButton()).toHaveAccessibleName("Expand navigation");
    expect(toggleButton()).toHaveAttribute("aria-expanded", "false");
  });

  it("is collapsed when matchMedia is missing", async () => {
    vi.stubGlobal("matchMedia", undefined);
    await renderShell(operatorMe);
    expect(toggleButton()).toHaveAttribute("aria-expanded", "false");
  });

  it("controls the navigation column, which holds the button and the Main navigation", async () => {
    stubMedia(true);
    const { nav, container } = await renderShell(operatorMe);
    const id = toggleButton().getAttribute("aria-controls");
    expect(id).toBeTruthy();
    const column = document.getElementById(id as string);
    expect(column).not.toBeNull();
    expect(column).toBe(container.querySelector(".shell-frame")?.children[0]);
    expect(column).toContainElement(toggleButton());
    expect(column).toContainElement(nav);
  });

  it("flips aria-expanded and the name on a click, and stores the choice", async () => {
    stubMedia(true);
    await renderShell(operatorMe);
    fireEvent.click(toggleButton());
    expect(toggleButton()).toHaveAccessibleName("Expand navigation");
    expect(toggleButton()).toHaveAttribute("aria-expanded", "false");
    expect(window.localStorage.getItem(NAV_KEY)).toBe("collapsed");

    fireEvent.click(toggleButton());
    expect(toggleButton()).toHaveAccessibleName("Collapse navigation");
    expect(toggleButton()).toHaveAttribute("aria-expanded", "true");
    expect(window.localStorage.getItem(NAV_KEY)).toBe("open");
  });

  it("starts from the stored choice, not from the window width", async () => {
    stubMedia(true);
    window.localStorage.setItem(NAV_KEY, "collapsed");
    await renderShell(operatorMe);
    expect(toggleButton()).toHaveAttribute("aria-expanded", "false");
  });

  it("ignores a bad stored value", async () => {
    stubMedia(true);
    window.localStorage.setItem(NAV_KEY, "wide");
    await renderShell(operatorMe);
    expect(toggleButton()).toHaveAttribute("aria-expanded", "true");
  });

  it("renders with the default when localStorage throws on read", async () => {
    stubMedia(true);
    breakLocalStorage("getItem");
    const { nav } = await renderShell(operatorMe);
    expect(labelsIn(nav)).toEqual(OPERATOR_ITEMS);
    expect(toggleButton()).toHaveAttribute("aria-expanded", "true");
  });

  it("still flips when localStorage throws on write", async () => {
    stubMedia(true);
    breakLocalStorage("setItem");
    await renderShell(operatorMe);
    fireEvent.click(toggleButton());
    expect(toggleButton()).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggleButton());
    expect(toggleButton()).toHaveAttribute("aria-expanded", "true");
  });

  it("follows a window-width change until the viewer chooses", async () => {
    const media = stubMedia(false);
    await renderShell(operatorMe);
    media.change(true);
    expect(toggleButton()).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggleButton());
    media.change(true);
    expect(toggleButton()).toHaveAttribute("aria-expanded", "false");
  });

  it("gives each collapsed link, Preview as Recruiter and Log out a title equal to its text", async () => {
    stubMedia(false);
    const { nav } = await renderShell(operatorMe);
    for (const link of linksIn(nav)) expect(link).toHaveAttribute("title", link.textContent ?? "");
    expect(screen.getByRole("button", { name: "Preview as Recruiter" })).toHaveAttribute("title", "Preview as Recruiter");
    expect(screen.getByRole("button", { name: "Log out" })).toHaveAttribute("title", "Log out");
  });

  it("gives the collapsed theme toggle a title through its wrapper", async () => {
    stubMedia(false);
    await renderShell(operatorMe);
    const wrapper = screen.getByRole("button", { name: /^Theme: / }).closest("[title]");
    expect(wrapper).not.toBeNull();
    expect(wrapper?.getAttribute("title")).toBeTruthy();
  });

  it("keeps every label in the DOM when collapsed: the role badge, the Client name and the link text", async () => {
    stubMedia(false);
    const { nav } = await renderShell(recruiterMe);
    expect(screen.getByTestId("role-badge")).toHaveTextContent("Recruiter");
    expect(screen.getByText("Grupo Sazón")).toBeInTheDocument();
    expect(labelsIn(nav)).toEqual(RECRUITER_ITEMS);
    expect(screen.getByRole("button", { name: "Log out" })).toHaveTextContent("Log out");
  });

  it("keeps the same items and controls when open as when collapsed", async () => {
    stubMedia(true);
    const { nav } = await renderShell(operatorMe);
    expect(labelsIn(nav)).toEqual(OPERATOR_ITEMS);
    expect(screen.getByTestId("role-badge")).toHaveTextContent("Operator");
    fireEvent.click(toggleButton());
    expect(labelsIn(screen.getByRole("navigation", { name: "Main" }))).toEqual(OPERATOR_ITEMS);
    expect(screen.getByTestId("role-badge")).toHaveTextContent("Operator");
  });
});

describe("UI3 the content-area contract", () => {
  const only = (list: HTMLCollection | Element[]) => [...list];

  it("holds one .shell-frame in .shell", async () => {
    const { container } = await renderShell(operatorMe);
    const shell = container.querySelector(".shell") as HTMLElement;
    expect(only(shell.children)).toHaveLength(1);
    expect(shell.children[0]).toHaveClass("shell-frame");
  });

  it("puts the navigation column and .shell-main in .shell-frame, in that order", async () => {
    const { container, nav } = await renderShell(operatorMe);
    const frame = container.querySelector(".shell-frame") as HTMLElement;
    expect(only(frame.children)).toHaveLength(2);
    expect(frame.children[0]).toContainElement(nav);
    expect(frame.children[0]).not.toHaveClass("shell-main");
    expect(frame.children[1]).toHaveClass("shell-main");
  });

  it("puts main.page in .shell-main, with .shell-route inside it holding the routed page", async () => {
    const { container } = await renderShell(operatorMe);
    const shellMain = container.querySelector(".shell-frame > .shell-main") as HTMLElement;
    const page = shellMain.querySelector(":scope > main.page") as HTMLElement;
    expect(page).not.toBeNull();
    expect(only(shellMain.children)).toEqual([page]);
    const route = page.querySelector(":scope > .shell-route") as HTMLElement;
    expect(route).not.toBeNull();
    expect(only(page.children)).toEqual([route]);
    expect(within(route).getByRole("heading", { name: "Applications" })).toBeInTheDocument();
  });

  it("puts the preview banner in .shell-main before main.page, and nowhere else", async () => {
    previewOn();
    const { container } = await renderShell(operatorMe);
    const shellMain = container.querySelector(".shell-frame > .shell-main") as HTMLElement;
    const children = only(shellMain.children);
    expect(children).toHaveLength(2);
    expect(children[0]).toHaveClass("preview-banner");
    expect(children[0]).toHaveAttribute("role", "status");
    expect(children[1].matches("main.page")).toBe(true);
    expect(container.querySelectorAll(".preview-banner")).toHaveLength(1);
    expect(container.querySelector("main .preview-banner")).toBeNull();
  });

  it("keeps the nesting when the navigation is collapsed and when it is open", async () => {
    stubMedia(true);
    const { container } = await renderShell(recruiterMe);
    fireEvent.click(toggleButton());
    const frame = container.querySelector(".shell-frame") as HTMLElement;
    expect(frame.querySelector(":scope > .shell-main > main.page > .shell-route")).not.toBeNull();
    fireEvent.click(toggleButton());
    expect(frame.querySelector(":scope > .shell-main > main.page > .shell-route")).not.toBeNull();
  });
});
