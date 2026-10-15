/// <reference types="node" />
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { afterEach, vi } from "vitest";
import { THEME_STORAGE_KEY } from "../../src/components/ui/theme.ts";
import { error, mockApi, renderRoute } from "./helpers.tsx";
import { locations, operatorMe, recruiterMe } from "./fixtures.ts";

const common = {
  "GET /api/applications": { items: [], next_cursor: null, total: 0 },
  "GET /api/locations": { items: locations },
};

describe("login", () => {
  it("logs in and lands on Applications", async () => {
    let loggedIn = false;
    const mock = mockApi({
      ...common,
      "GET /api/auth/me": () => (loggedIn ? recruiterMe : error(401, "unauthorized", "Log in to continue.")),
      "POST /api/auth/login": () => {
        loggedIn = true;
        return recruiterMe;
      },
    });
    const { router } = renderRoute("/applications");

    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "recruiter.puebla@sazon.example" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "demo-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByRole("heading", { name: "Applications" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/applications");
    expect(mock.find("POST", "/api/auth/login")[0].body).toEqual({
      email: "recruiter.puebla@sazon.example",
      password: "demo-password",
    });
  });

  it("shows a message for wrong credentials", async () => {
    mockApi({
      "GET /api/auth/me": error(401, "unauthorized", "Log in to continue."),
      "POST /api/auth/login": error(401, "unauthorized", "Invalid email or password."),
    });
    renderRoute("/login");
    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "x@y.z" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
  });

  it("says when the login throttle has locked the email (also a 401)", async () => {
    mockApi({
      "GET /api/auth/me": error(401, "unauthorized", "Log in to continue."),
      "POST /api/auth/login": error(401, "unauthorized", "Too many attempts. Try again later."),
    });
    renderRoute("/login");
    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "x@y.z" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "right-this-time" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Try again later.");
  });

  it("falls back to a fixed text when a 401 has no body", async () => {
    mockApi({
      "GET /api/auth/me": error(401, "unauthorized", "Log in to continue."),
      "POST /api/auth/login": () => new Response("", { status: 401 }),
    });
    renderRoute("/login");
    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "x@y.z" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is not right.");
  });
});

describe("app shell and the role switch", () => {
  it("shows the economics views to Operators", async () => {
    mockApi({ ...common, "GET /api/auth/me": operatorMe });
    renderRoute("/applications");
    expect(await screen.findByRole("link", { name: "Economics" })).toBeInTheDocument();
    expect(screen.getByTestId("role-badge")).toHaveTextContent("Operator");
  });

  it("hides the economics views from Recruiters", async () => {
    mockApi({ ...common, "GET /api/auth/me": recruiterMe });
    renderRoute("/applications");
    await screen.findByRole("heading", { name: "Applications" });
    expect(screen.queryByRole("link", { name: "Economics" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Preview as Recruiter" })).not.toBeInTheDocument();
    expect(screen.getByTestId("role-badge")).toHaveTextContent("Recruiter");
  });

  it("lets an Operator preview the Recruiter view, which hides economics", async () => {
    mockApi({ ...common, "GET /api/auth/me": operatorMe });
    renderRoute("/applications");
    fireEvent.click(await screen.findByRole("button", { name: "Preview as Recruiter" }));
    await waitFor(() => expect(screen.queryByRole("link", { name: "Economics" })).not.toBeInTheDocument());
    expect(screen.getByTestId("role-badge")).toHaveTextContent("Recruiter");
    expect(screen.getByRole("status")).toHaveTextContent("Previewing the Recruiter view");

    fireEvent.click(screen.getByRole("button", { name: "Back to Operator view" }));
    expect(await screen.findByRole("link", { name: "Economics" })).toBeInTheDocument();
  });

  it("blocks the economics route for a Recruiter and never requests the data", async () => {
    const mock = mockApi({ ...common, "GET /api/auth/me": recruiterMe });
    renderRoute("/economics");
    expect(await screen.findByText("The economics views are for Operators only.")).toBeInTheDocument();
    expect(mock.calls.some((c) => c.path.startsWith("/api/metrics/economics") || c.path.startsWith("/api/metrics/comparison"))).toBe(false);
  });

  it("returns to login when the session expires", async () => {
    let expired = false;
    mockApi({
      "GET /api/auth/me": () => (expired ? error(401, "unauthorized", "Log in to continue.") : operatorMe),
      "GET /api/locations": { items: locations },
      "GET /api/applications": () => {
        expired = true;
        return error(401, "unauthorized", "Log in to continue.");
      },
    });
    const { router } = renderRoute("/applications");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
  });

  it("logs out", async () => {
    const mock = mockApi({ ...common, "GET /api/auth/me": operatorMe, "POST /api/auth/logout": [204, undefined] });
    const { router } = renderRoute("/applications");
    fireEvent.click(await screen.findByRole("button", { name: "Log out" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(mock.find("POST", "/api/auth/logout")).toHaveLength(1);
  });
});

const loggedOut = { "GET /api/auth/me": error(401, "unauthorized", "Log in to continue.") };
const previewOn = () => window.sessionStorage.setItem("criba.previewRecruiter", "1");
const mainLinks = () => within(screen.getByRole("navigation", { name: "Main" })).getAllByRole("link").map((a) => a.textContent);

describe("branding", () => {
  it("shows the Criba wordmark linking to the start page", async () => {
    mockApi({ ...common, "GET /api/auth/me": operatorMe });
    renderRoute("/applications");
    expect(await screen.findByRole("link", { name: "Criba" })).toHaveAttribute("href", "/");
  });

  it("shows the Client name and accent to a Recruiter, with no image", async () => {
    mockApi({ ...common, "GET /api/auth/me": recruiterMe });
    const { container } = renderRoute("/applications");
    expect(await screen.findByText("Grupo Sazón")).toBeInTheDocument();
    expect(container.querySelector(".client-accent")).not.toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows neither to an Operator, and both under Preview as Recruiter", async () => {
    mockApi({ ...common, "GET /api/auth/me": operatorMe });
    const { container } = renderRoute("/applications");
    fireEvent.click(await screen.findByRole("button", { name: "Preview as Recruiter" }));
    expect(await screen.findByText("Grupo Sazón")).toBeInTheDocument();
    expect(container.querySelector(".client-accent")).not.toBeNull();
    expect(container.querySelector("img")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Back to Operator view" }));
    await waitFor(() => expect(screen.queryByText("Grupo Sazón")).not.toBeInTheDocument());
    expect(container.querySelector(".client-accent")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("landing", () => {
  it("sends an Operator from / to /overview, with Overview first in Main", async () => {
    mockApi({ ...common, "GET /api/auth/me": operatorMe });
    const { router } = renderRoute("/");
    await waitFor(() => expect(router.state.location.pathname).toBe("/overview"));
    await screen.findByRole("heading", { level: 1 });
    expect(mainLinks()[0]).toBe("Overview");
    expect(mainLinks()).not.toContain("Today");
  });

  it("sends a Recruiter from / to /today, with Today first in Main", async () => {
    mockApi({ ...common, "GET /api/auth/me": recruiterMe });
    const { router } = renderRoute("/");
    await waitFor(() => expect(router.state.location.pathname).toBe("/today"));
    await screen.findByRole("heading", { level: 1 });
    expect(mainLinks()[0]).toBe("Today");
    expect(mainLinks()).not.toContain("Overview");
  });

  it("keeps the real role's landing under preview while Main starts with Today", async () => {
    previewOn();
    mockApi({ ...common, "GET /api/auth/me": operatorMe });
    const { router } = renderRoute("/");
    await waitFor(() => expect(router.state.location.pathname).toBe("/overview"));
    await screen.findByRole("heading", { level: 1 });
    expect(mainLinks()[0]).toBe("Today");
  });

  it("lands a login with no from on the role landing", async () => {
    let loggedIn = false;
    mockApi({
      ...common,
      "GET /api/auth/me": () => (loggedIn ? recruiterMe : error(401, "unauthorized", "Log in to continue.")),
      "POST /api/auth/login": () => {
        loggedIn = true;
        return recruiterMe;
      },
    });
    const { router } = renderRoute("/login");
    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "recruiter.puebla@sazon.example" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "demo-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/today"));
  });

  it("links Page not found back to the start page", async () => {
    mockApi({ ...common, "GET /api/auth/me": operatorMe });
    renderRoute("/no-such-page");
    const card = (await screen.findByRole("heading", { name: "Page not found" })).parentElement as HTMLElement;
    expect(within(card).getByRole("link")).toHaveAttribute("href", "/");
  });
});

describe("route stubs", () => {
  it.each([
    ["/today", recruiterMe],
    ["/overview", operatorMe],
    ["/stage", operatorMe],
  ] as const)("renders a heading at %s", async (path, me) => {
    mockApi({ ...common, "GET /api/auth/me": me });
    const { router } = renderRoute(path);
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(path);
    expect(screen.queryByRole("heading", { name: "Page not found" })).not.toBeInTheDocument();
  });

  it("shows the demo stage to an Operator without the top bar", async () => {
    mockApi({ ...common, "GET /api/auth/me": operatorMe });
    renderRoute("/stage");
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Page not found" })).not.toBeInTheDocument();
  });

  it("shows Page not found at /stage to a Recruiter", async () => {
    mockApi({ ...common, "GET /api/auth/me": recruiterMe });
    renderRoute("/stage");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

  it("shows Page not found at /stage to an Operator under Preview as Recruiter", async () => {
    previewOn();
    mockApi({ ...common, "GET /api/auth/me": operatorMe });
    renderRoute("/stage");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

  it("has no navigation link to /stage", async () => {
    mockApi({ ...common, "GET /api/auth/me": operatorMe });
    renderRoute("/applications");
    const links = within(await screen.findByRole("navigation", { name: "Main" })).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).not.toContain("/stage");
  });

  it.each(["/today", "/overview", "/stage"])("sends a logged-out visitor at %s to /login", async (path) => {
    mockApi(loggedOut);
    const { router } = renderRoute(path);
    expect(await screen.findByRole("button", { name: "Log in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
  });
});

describe("states on the shell's pages", () => {
  it("shows a skeleton, not Loading text, while me is pending", async () => {
    mockApi({ "GET /api/auth/me": () => new Promise<object>(() => undefined) });
    renderRoute("/applications");
    expect(await screen.findByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
  });

  it("shows an error state on a 500 whose Retry asks me once more", async () => {
    let failing = true;
    const mock = mockApi({
      ...common,
      "GET /api/auth/me": () => (failing ? error(500, "internal", "boom") : operatorMe),
    });
    const { router } = renderRoute("/applications");
    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/applications");
    expect(mock.find("GET", "/api/auth/me")).toHaveLength(1);

    failing = false;
    fireEvent.click(retry);
    expect(await screen.findByTestId("role-badge")).toHaveTextContent("Operator");
    expect(mock.find("GET", "/api/auth/me")).toHaveLength(2);
  });

  it("keeps the Shell and shows no skeleton while me refetches", async () => {
    let calls = 0;
    const mock = mockApi({
      ...common,
      "GET /api/auth/me": () => (++calls === 1 ? operatorMe : new Promise<object>(() => undefined)),
    });
    const { client } = renderRoute("/applications");
    await screen.findByTestId("role-badge");
    void client.invalidateQueries({ queryKey: ["me"] });
    await waitFor(() => expect(mock.find("GET", "/api/auth/me")).toHaveLength(2));
    expect(screen.getByTestId("role-badge")).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "Loading" })).not.toBeInTheDocument();
  });

  it("still sends a 401 to /login", async () => {
    mockApi(loggedOut);
    const { router } = renderRoute("/applications");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
  });
});

describe("theme", () => {
  afterEach(() => vi.restoreAllMocks());

  const toggle = () => screen.findByRole("button", { name: /^Theme:/ });

  it("toggles data-theme and stores the choice", async () => {
    mockApi({ ...common, "GET /api/auth/me": operatorMe });
    renderRoute("/applications");
    fireEvent.click(await toggle());
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    fireEvent.click(await toggle());
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("renders and toggles when storage throws", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    mockApi({ ...common, "GET /api/auth/me": operatorMe });
    renderRoute("/applications");
    fireEvent.click(await toggle());
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  describe("pre-paint script in index.html", () => {
    const html = readFileSync(join(import.meta.dirname, "../../index.html"), "utf8");
    const inline = /<script>([\s\S]*?)<\/script>/.exec(html);
    const run = () => new Function((inline as RegExpExecArray)[1])();

    it("is a classic inline script before the module script", () => {
      expect(inline).not.toBeNull();
      expect(html.indexOf("<script>")).toBeLessThan(html.indexOf('<script type="module"'));
    });

    it("uses the same key as components/ui/theme.ts", () => {
      expect(/localStorage\.getItem\(["']([^"']+)["']\)/.exec(html)?.[1]).toBe(THEME_STORAGE_KEY);
    });

    it("sets dark for a stored dark", () => {
      window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
      run();
      expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    });

    it.each([["blue"], [null]])("leaves data-theme absent for %s", (stored) => {
      if (stored !== null) window.localStorage.setItem(THEME_STORAGE_KEY, stored);
      run();
      expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    });

    it("does not throw when storage throws", () => {
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("blocked");
      });
      expect(run).not.toThrow();
      expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    });
  });
});
