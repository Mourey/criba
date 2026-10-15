/// <reference types="node" />
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { vi } from "vitest";
import type { PublicBookingConfirmation, PublicBookingPage, PublicSlot } from "../../src/api/types.ts";
import { dayMonth, formatExpiry } from "../../src/lib/labels/booking.ts";
import { error, mockApi, renderRoute } from "./helpers.tsx";

// Read from disk: Vitest swaps every CSS import for an empty string.
const read = (file: string) => readFileSync(join(import.meta.dirname, "../../src", file), "utf8");

describe("Public booking page", () => {
  const page: PublicBookingPage = {
    first_name: "Luis",
    language: "es-MX",
    expires_at: "2026-10-16T10:00:00Z",
    current_booking: null,
    slots: [
      { slot_id: "slot_8x2", starts_at: "2026-10-14T16:00:00Z", ends_at: "2026-10-14T16:30:00Z", local_label: "mié 14 · 10:00", location_name: "Sazón Puebla Centro", address: "Av. Reforma 123, Centro, Puebla" },
      { slot_id: "slot_8x3", starts_at: "2026-10-15T17:00:00Z", ends_at: "2026-10-15T17:30:00Z", local_label: "mié 15 · 11:00", location_name: "Sazón Puebla Centro", address: null },
    ],
  };

  it("needs no login, lists open slots in the Candidate's language, and books one", async () => {
    const mock = mockApi({
      "GET /api/public/booking/:token": page,
      "POST /api/public/booking/:token": [
        201,
        {
          interview: { starts_at: page.slots[0].starts_at, ends_at: page.slots[0].ends_at, local_label: "mié 14 · 10:00", location_name: "Sazón Puebla Centro", address: "Av. Reforma 123, Centro, Puebla" },
          ics_url: "/api/public/booking/tok123/invite.ics",
          message: "¡Listo, Luis! Tu entrevista quedó para el martes 14 a las 10:00.",
        },
      ],
    });
    renderRoute("/book/tok123");
    expect(await screen.findByRole("heading", { name: "Elige el horario de tu entrevista" })).toBeInTheDocument();
    expect(await screen.findByText(/Hola, Luis/)).toBeInTheDocument();
    // Public: nothing but the booking page is requested, and there is no app shell.
    expect(mock.calls.map((c) => c.path)).toEqual(["/api/public/booking/tok123"]);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();

    const confirm = screen.getByRole("button", { name: "Confirmar entrevista" });
    expect(confirm).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/mié 14 · 10:00/));
    fireEvent.click(confirm);

    expect(await screen.findByText(/¡Listo, Luis!/)).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("lang", "es");
    expect(screen.getByRole("link", { name: "Añadir a mi calendario (.ics)" })).toHaveAttribute("href", "/api/public/booking/tok123/invite.ics");
    expect(mock.find("POST", "/api/public/booking/tok123")[0].body).toEqual({ slot_id: "slot_8x2" });
  });

  it("speaks English to an English-speaking Candidate", async () => {
    mockApi({ "GET /api/public/booking/:token": { ...page, language: "en" } });
    renderRoute("/book/tok123");
    expect(await screen.findByText(/Hi Luis/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Choose your interview time" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("lang", "en");
  });

  it("shows the server's message for an expired link", async () => {
    mockApi({ "GET /api/public/booking/:token": error(410, "link_expired", "Este enlace ya caducó. Escríbenos por el chat y te mandamos uno nuevo.") });
    renderRoute("/book/old");
    expect(await screen.findByRole("alert")).toHaveTextContent("Este enlace ya caducó.");
  });

  it("lets the Candidate pick again when the slot was just taken", async () => {
    let fetches = 0;
    mockApi({
      "GET /api/public/booking/:token": () => {
        fetches += 1;
        return { ...page, slots: fetches > 1 ? [page.slots[1]] : page.slots };
      },
      "POST /api/public/booking/:token": error(409, "slot_taken", "taken"),
    });
    renderRoute("/book/tok123");
    fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar entrevista" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Ese horario se acaba de ocupar");
    await waitFor(() => expect(screen.queryByLabelText(/mié 14 · 10:00/)).not.toBeInTheDocument());
    expect(screen.getByLabelText(/mié 15 · 11:00/)).toBeInTheDocument();
  });

  it("is reachable while logged out and never asks who is logged in", async () => {
    const mock = mockApi({ "GET /api/public/booking/:token": page });
    const { router } = renderRoute("/book/tok123");
    await screen.findByText(/Hola, Luis/);
    expect(router.state.location.pathname).toBe("/book/tok123");
    expect(mock.find("GET", "/api/auth/me")).toHaveLength(0);
  });

  const puebla = { location_name: "Sazón Puebla Centro", address: "Av. Reforma 123, Centro, Puebla" };
  const cholula = { location_name: "Sazón Cholula", address: "Calle 5 Norte 40, Cholula" };
  const slot = (id: string, local_label: string, where: { location_name: string; address: string | null }): PublicSlot => ({
    slot_id: id,
    starts_at: "2026-10-14T16:00:00Z",
    ends_at: "2026-10-14T16:30:00Z",
    local_label,
    ...where,
  });
  const confirmation: PublicBookingConfirmation = {
    interview: { ...page.slots[0], address: puebla.address },
    ics_url: "/api/public/booking/tok123/invite.ics",
    message: "¡Listo, Luis! Tu entrevista quedó para el martes 14 a las 10:00.",
  };
  const bookingPath = "/api/public/booking/tok123";
  // A reply the test settles by hand, to hold a request open.
  const hold = () => {
    const held: { release: (reply: object) => void } = { release: () => undefined };
    const reply = new Promise<object>((resolve) => {
      held.release = resolve;
    });
    return { held, reply };
  };

  describe("slot groups", () => {
    it("shows a Location once, in a fieldset with a legend, and keeps a radio for every slot", async () => {
      mockApi({
        "GET /api/public/booking/:token": { ...page, slots: [slot("s1", "mié 14 · 10:00", puebla), slot("s2", "mié 15 · 11:00", puebla), slot("s3", "jue 16 · 12:00", puebla)] },
      });
      renderRoute("/book/tok123");
      const group = await screen.findByRole("group", { name: /Sazón Puebla Centro/ });
      expect(group.tagName).toBe("FIELDSET");
      expect(group.querySelector("legend")).toHaveTextContent("Sazón Puebla Centro");
      expect(group.querySelector("legend")).toHaveTextContent("Av. Reforma 123, Centro, Puebla");
      expect(screen.getAllByRole("group")).toHaveLength(1);
      expect(screen.getAllByText(/Sazón Puebla Centro/)).toHaveLength(1);
      expect(screen.getAllByText(/Av\. Reforma 123/)).toHaveLength(1);
      expect(within(group).getAllByRole("radio")).toHaveLength(3);
      expect(screen.getAllByRole("radio")).toHaveLength(3);
    });

    it("groups by Location name and address, in the order of each Location's first slot", async () => {
      const otherAddress = { location_name: puebla.location_name, address: "Blvd. Atlixco 9, Puebla" };
      mockApi({
        "GET /api/public/booking/:token": {
          ...page,
          slots: [
            slot("a1", "mié 14 · 10:00", puebla),
            slot("b1", "mié 14 · 11:00", cholula),
            slot("a2", "mié 14 · 12:00", puebla),
            slot("c1", "mié 14 · 13:00", otherAddress),
            slot("d1", "mié 14 · 14:00", { location_name: "Sazón Sin Dirección", address: null }),
          ],
        },
      });
      renderRoute("/book/tok123");
      const groups = await screen.findAllByRole("group");
      expect(groups).toHaveLength(4);
      expect(groups[0]).toHaveAccessibleName(/Sazón Puebla Centro/);
      expect(groups[1]).toHaveAccessibleName(/Sazón Cholula/);
      expect(groups[2]).toHaveAccessibleName(/Blvd\. Atlixco 9/);
      expect(groups[3]).toHaveAccessibleName(/Sazón Sin Dirección/);
      expect(groups.map((g) => within(g).getAllByRole("radio").length)).toEqual([2, 1, 1, 1]);
      expect(screen.getAllByRole("radio")).toHaveLength(5);
      expect(screen.getAllByText(/Sazón Cholula/)).toHaveLength(1);
      // Picking in one group clears the other: one radio set, one choice.
      fireEvent.click(screen.getByLabelText(/mié 14 · 11:00/));
      fireEvent.click(screen.getByLabelText(/mié 14 · 13:00/));
      expect(screen.getByLabelText(/mié 14 · 11:00/)).not.toBeChecked();
      expect(screen.getByLabelText(/mié 14 · 13:00/)).toBeChecked();
    });

    it("tells a Candidate who already has an Interview, and what choosing again does", async () => {
      mockApi({ "GET /api/public/booking/:token": { ...page, current_booking: { ...page.slots[0], address: puebla.address } } });
      renderRoute("/book/tok123");
      expect(await screen.findByText(/Ya tienes una entrevista:/)).toHaveTextContent("mié 14 · 10:00");
      expect(screen.getByText(/Si eliges otro horario, cambiaremos tu entrevista/)).toBeInTheDocument();
    });
  });

  describe("loading, empty and refetch states", () => {
    it("shows a labelled skeleton and no loading text while the first load is pending", async () => {
      mockApi({ "GET /api/public/booking/:token": () => new Promise<object>(() => undefined) });
      const { container } = renderRoute("/book/tok123");
      expect(await screen.findByRole("status", { name: "Cargando…" })).toBeInTheDocument();
      expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
      expect(screen.queryByText(/Loading|Cargando/)).not.toBeInTheDocument();
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });

    it("says what will appear and how to get help when there are no slots, with no Confirm", async () => {
      mockApi({ "GET /api/public/booking/:token": { ...page, slots: [] } });
      renderRoute("/book/tok123");
      expect(await screen.findByText(/no hay horarios disponibles/)).toHaveTextContent("Escríbenos por WhatsApp");
      expect(screen.queryByRole("button", { name: "Confirmar entrevista" })).not.toBeInTheDocument();
      expect(screen.queryAllByRole("button")).toHaveLength(0);
      expect(screen.queryByRole("radio")).not.toBeInTheDocument();
      expect(screen.queryByRole("status", { name: "Cargando…" })).not.toBeInTheDocument();
    });

    it("keeps the list on screen, with no skeleton, while it refetches after a taken slot", async () => {
      const second = hold();
      let fetches = 0;
      mockApi({
        "GET /api/public/booking/:token": () => {
          fetches += 1;
          return fetches === 1 ? page : second.reply;
        },
        "POST /api/public/booking/:token": error(409, "slot_taken", "taken"),
      });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      fireEvent.click(screen.getByRole("button", { name: "Confirmar entrevista" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Ese horario se acaba de ocupar");
      await waitFor(() => expect(fetches).toBe(2));
      // The refetch is still open: the list stays and nothing says "loading".
      expect(screen.getByLabelText(/mié 14 · 10:00/)).toBeInTheDocument();
      expect(screen.getByLabelText(/mié 15 · 11:00/)).toBeInTheDocument();
      expect(screen.queryByRole("status", { name: "Cargando…" })).not.toBeInTheDocument();
      expect(document.querySelector('[aria-busy="true"]')).toBeNull();
      second.held.release({ ...page, slots: [page.slots[1]] });
      await waitFor(() => expect(screen.queryByLabelText(/mié 14 · 10:00/)).not.toBeInTheDocument());
    });
  });

  describe("errors", () => {
    it("retries a failed load once, with a Reintentar button that sends exactly one new request", async () => {
      let fetches = 0;
      const mock = mockApi({
        "GET /api/public/booking/:token": () => {
          fetches += 1;
          return fetches === 1 ? error(500, "internal", "database exploded") : page;
        },
      });
      renderRoute("/book/tok123");
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("No pudimos cargar esta página");
      expect(alert.textContent).not.toMatch(/We could not/);
      expect(document.body).not.toHaveTextContent("database exploded");
      expect(mock.find("GET", bookingPath)).toHaveLength(1);
      fireEvent.click(within(alert).getByRole("button", { name: "Reintentar" }));
      expect(await screen.findByText(/Hola, Luis/)).toBeInTheDocument();
      expect(mock.find("GET", bookingPath)).toHaveLength(2);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("shows a rejected request as generic copy, never the browser's own message", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          throw new TypeError("Failed to fetch");
        }),
      );
      renderRoute("/book/tok123");
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("No pudimos cargar esta página");
      expect(document.body).not.toHaveTextContent("Failed to fetch");
      expect(within(alert).getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
    });

    it("answers an unknown link in Spanish and English, each in its own lang, with no Reintentar", async () => {
      mockApi({ "GET /api/public/booking/:token": error(404, "not_found", "server says nope") });
      renderRoute("/book/bad");
      const alert = await screen.findByRole("alert");
      expect(alert.querySelector('[lang="es"]')).toHaveTextContent(/Este enlace no es válido\. Escríbenos por WhatsApp/);
      expect(alert.querySelector('[lang="en"]')).toHaveTextContent(/This link is not valid\. Message us on WhatsApp/);
      expect(document.body).not.toHaveTextContent("server says nope");
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      expect(screen.getAllByRole("alert")).toHaveLength(1);
    });

    it("shows the server's message for a link that is no longer valid, with no Reintentar", async () => {
      mockApi({ "GET /api/public/booking/:token": error(410, "link_expired", "Este enlace ya caducó. Escríbenos por el chat y te mandamos uno nuevo.") });
      renderRoute("/book/old");
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Este enlace ya caducó. Escríbenos por el chat y te mandamos uno nuevo.");
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      expect(screen.getAllByRole("alert")).toHaveLength(1);
    });

    it("moves to the same states when the link stops being valid between load and booking", async () => {
      mockApi({
        "GET /api/public/booking/:token": page,
        "POST /api/public/booking/:token": error(410, "link_expired", "Este enlace ya caducó. Pídenos uno nuevo."),
      });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      fireEvent.click(screen.getByRole("button", { name: "Confirmar entrevista" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Este enlace ya caducó. Pídenos uno nuevo.");
      expect(screen.queryByRole("radio")).not.toBeInTheDocument();
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });

    it("keeps the choice, re-enables Confirm and shows generic copy when booking fails", async () => {
      const mock = mockApi({
        "GET /api/public/booking/:token": page,
        "POST /api/public/booking/:token": error(500, "internal", "database exploded"),
      });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      fireEvent.click(screen.getByRole("button", { name: "Confirmar entrevista" }));
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("No pudimos completar la solicitud");
      expect(document.body).not.toHaveTextContent("database exploded");
      expect(mock.find("POST", bookingPath)).toHaveLength(1);
      expect(screen.getByLabelText(/mié 14 · 10:00/)).toBeChecked();
      expect(screen.getByRole("button", { name: "Confirmar entrevista" })).toBeEnabled();
      expect(screen.getAllByRole("alert")).toHaveLength(1);
    });

    it("speaks English on a failed booking when the Candidate's language is English", async () => {
      mockApi({
        "GET /api/public/booking/:token": { ...page, language: "en" },
        "POST /api/public/booking/:token": error(500, "internal", "database exploded"),
      });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      fireEvent.click(screen.getByRole("button", { name: "Confirm interview" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("We could not complete that");
    });

    it("sends one POST per press, and holds Confirm while it is pending", async () => {
      const pending = hold();
      const mock = mockApi({
        "GET /api/public/booking/:token": page,
        "POST /api/public/booking/:token": () => pending.reply,
      });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      const confirm = screen.getByRole("button", { name: "Confirmar entrevista" });
      fireEvent.click(confirm);
      fireEvent.click(confirm);
      await waitFor(() => expect(mock.find("POST", bookingPath)).toHaveLength(1));
      await waitFor(() => expect(confirm).toBeDisabled());
      fireEvent.click(confirm);
      expect(mock.find("POST", bookingPath)).toHaveLength(1);
      pending.held.release(error(500, "internal", "database exploded"));
      await waitFor(() => expect(screen.getByRole("button", { name: "Confirmar entrevista" })).toBeEnabled());
      expect(mock.find("POST", bookingPath)).toHaveLength(1);
    });
  });

  describe("expiry date and focus", () => {
    const expiry = "2026-10-16T12:00:00Z";

    it("formats the date for the page language", () => {
      expect(formatExpiry(expiry, "es-ES")).toMatch(/16 de octubre de 2026/);
      expect(formatExpiry(expiry, "es-MX")).toMatch(/16 de octubre de 2026/);
      expect(formatExpiry(expiry, "en")).toMatch(/October 16, 2026/);
      expect(formatExpiry("not a date", "en")).toBeNull();
    });

    it("shows it formatted in Spanish, never as a raw date", async () => {
      mockApi({ "GET /api/public/booking/:token": { ...page, expires_at: expiry } });
      renderRoute("/book/tok123");
      expect(await screen.findByText(/Este enlace es válido hasta el 16 de octubre de 2026/)).toBeInTheDocument();
      expect(document.body).not.toHaveTextContent("2026-10-16");
    });

    it("shows it formatted in English, without the word expire", async () => {
      mockApi({ "GET /api/public/booking/:token": { ...page, language: "en", expires_at: expiry } });
      renderRoute("/book/tok123");
      const line = await screen.findByText(/This link is valid until October 16, 2026/);
      expect(line.textContent).not.toMatch(/expire/i);
      expect(document.body).not.toHaveTextContent("2026-10-16");
    });

    it("moves focus to the confirmation when it replaces the picker", async () => {
      mockApi({ "GET /api/public/booking/:token": page, "POST /api/public/booking/:token": [201, confirmation] });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      fireEvent.click(screen.getByRole("button", { name: "Confirmar entrevista" }));
      await screen.findByText(/¡Listo, Luis!/);
      const status = screen.getByRole("status");
      expect(status.tagName).toBe("SECTION");
      expect(status).toHaveAttribute("tabindex", "-1");
      await waitFor(() => expect(status).toHaveFocus());
      expect(within(status).getByRole("link", { name: "Añadir a mi calendario (.ics)" })).toHaveAttribute("download");
    });
  });

  describe("days and time chips", () => {
    const days = [
      slot("d1", "mié 14 · 10:00", puebla),
      slot("d2", "mié 14 · 12:00", puebla),
      slot("d3", "jue 15 · 10:00", puebla),
      slot("d4", "jue 15 · 16:00", puebla),
    ];

    it("groups a Location's slots by day, keeps the API order, and hides none", async () => {
      mockApi({ "GET /api/public/booking/:token": { ...page, slots: days } });
      renderRoute("/book/tok123");
      const group = await screen.findByRole("group", { name: /Sazón Puebla Centro/ });
      expect(within(group).getAllByText("mié 14")).toHaveLength(1);
      expect(within(group).getAllByText("jue 15")).toHaveLength(1);
      const radios = within(group).getAllByRole("radio") as HTMLInputElement[];
      expect(radios.map((r) => r.value)).toEqual(["d1", "d2", "d3", "d4"]);
      expect(new Set(radios.map((r) => r.name)).size).toBe(1);
      // Still one Location heading for the group, and one choice across the chips.
      expect(screen.getAllByText(/Sazón Puebla Centro/)).toHaveLength(1);
      fireEvent.click(screen.getByLabelText(/mié 14 · 12:00/));
      fireEvent.click(screen.getByLabelText(/jue 15 · 16:00/));
      expect(screen.getByLabelText(/mié 14 · 12:00/)).not.toBeChecked();
      expect(screen.getByLabelText(/jue 15 · 16:00/)).toBeChecked();
    });

    it("shows the time as the strong text of each chip, with the day before it for a screen reader", async () => {
      mockApi({ "GET /api/public/booking/:token": { ...page, slots: days } });
      renderRoute("/book/tok123");
      const chip = (await screen.findByLabelText(/mié 14 · 12:00/)).closest("label") as HTMLElement;
      expect(chip.textContent).toBe("mié 14 · 12:00");
      expect(within(chip).getByText("12:00").tagName).toBe("STRONG");
      expect(chip.querySelector(".ui-sr-only")).toHaveTextContent("mié 14 ·");
    });

    it("falls back to the whole label when a slot has no day part", async () => {
      mockApi({ "GET /api/public/booking/:token": { ...page, slots: [slot("f1", "Mañana 10:00", puebla), slot("f2", "Tarde 16:00", puebla)] } });
      renderRoute("/book/tok123");
      const chip = (await screen.findByLabelText(/Mañana 10:00/)).closest("label") as HTMLElement;
      expect(within(chip).getByText("Mañana 10:00").tagName).toBe("STRONG");
      expect(screen.getAllByRole("radio")).toHaveLength(2);
    });
  });

  describe("the Confirm bar", () => {
    const barOf = (button: HTMLElement) => button.parentElement as HTMLElement;

    it("says to choose a time first, then names the chosen day, time and Location", async () => {
      mockApi({ "GET /api/public/booking/:token": page });
      renderRoute("/book/tok123");
      const confirm = await screen.findByRole("button", { name: "Confirmar entrevista" });
      const hint = within(barOf(confirm)).getByText("Elige un horario para continuar.");
      expect(hint).toHaveAttribute("aria-live", "polite");
      fireEvent.click(screen.getByLabelText(/mié 15 · 11:00/));
      expect(hint).toHaveTextContent("Tu horario: mié 15 · 11:00, Sazón Puebla Centro.");
      expect(screen.getAllByRole("button")).toHaveLength(1);
    });

    it("speaks English in the hint", async () => {
      mockApi({ "GET /api/public/booking/:token": { ...page, language: "en" } });
      renderRoute("/book/tok123");
      const confirm = await screen.findByRole("button", { name: "Confirm interview" });
      expect(within(barOf(confirm)).getByText("Choose a time to continue.")).toBeInTheDocument();
      fireEvent.click(screen.getByLabelText(/mié 14 · 10:00/));
      expect(within(barOf(confirm)).getByText("Your time: mié 14 · 10:00, Sazón Puebla Centro.")).toBeInTheDocument();
    });

    it("puts a taken-slot notice in the bar and names the lost time", async () => {
      mockApi({
        "GET /api/public/booking/:token": page,
        "POST /api/public/booking/:token": error(409, "slot_taken", "taken"),
      });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      const confirm = screen.getByRole("button", { name: "Confirmar entrevista" });
      fireEvent.click(confirm);
      const alert = await screen.findByRole("alert");
      expect(barOf(confirm)).toContainElement(alert);
      expect(alert).toHaveTextContent(/Ese horario se acaba de ocupar \(mié 14 · 10:00\)/);
      expect(screen.getAllByRole("alert")).toHaveLength(1);
    });

    it("puts a generic booking failure in the bar too", async () => {
      mockApi({
        "GET /api/public/booking/:token": page,
        "POST /api/public/booking/:token": error(500, "internal", "database exploded"),
      });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      const confirm = screen.getByRole("button", { name: "Confirmar entrevista" });
      fireEvent.click(confirm);
      const alert = await screen.findByRole("alert");
      expect(barOf(confirm)).toContainElement(alert);
      expect(alert).toHaveTextContent("No pudimos completar la solicitud");
    });
  });

  describe("the heading and the tab title follow the state", () => {
    it("names the picker, then the confirmation, in the page title as well", async () => {
      document.title = "Criba";
      mockApi({ "GET /api/public/booking/:token": page, "POST /api/public/booking/:token": [201, confirmation] });
      const { unmount } = renderRoute("/book/tok123");
      expect(await screen.findByRole("heading", { level: 1, name: "Elige el horario de tu entrevista" })).toBeInTheDocument();
      expect(document.title).toBe("Elige el horario de tu entrevista");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      fireEvent.click(screen.getByRole("button", { name: "Confirmar entrevista" }));
      expect(await screen.findByRole("heading", { level: 1, name: "Entrevista confirmada" })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Elige el horario de tu entrevista" })).not.toBeInTheDocument();
      expect(document.title).toBe("Entrevista confirmada");
      unmount();
      expect(document.title).toBe("Criba");
    });

    it("confirms in English for an English-speaking Candidate", async () => {
      mockApi({ "GET /api/public/booking/:token": { ...page, language: "en" }, "POST /api/public/booking/:token": [201, confirmation] });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      fireEvent.click(screen.getByRole("button", { name: "Confirm interview" }));
      expect(await screen.findByRole("heading", { level: 1, name: "Interview confirmed" })).toBeInTheDocument();
      expect(document.title).toBe("Interview confirmed");
    });

    it("stops inviting a choice on a link that cannot be used", async () => {
      mockApi({ "GET /api/public/booking/:token": error(404, "not_found", "nope") });
      renderRoute("/book/bad");
      await screen.findByRole("alert");
      const heading = screen.getByRole("heading", { level: 1 });
      expect(heading).toHaveTextContent("Enlace no disponible");
      expect(heading).toHaveTextContent("Link not available");
      expect(heading.querySelector('[lang="es"]')).not.toBeNull();
      expect(heading.querySelector('[lang="en"]')).not.toBeNull();
      expect(screen.queryByText(/Elige el horario/)).not.toBeInTheDocument();
      expect(document.title).toBe("Enlace no disponible · Link not available");
    });

    it("does the same for a link that is no longer valid", async () => {
      mockApi({ "GET /api/public/booking/:token": error(410, "link_expired", "Este enlace ya caducó.") });
      renderRoute("/book/old");
      await screen.findByRole("alert");
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Este enlace ya caducó");
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("This link is no longer active");
    });

    it("does not invite a choice when the page could not load", async () => {
      mockApi({ "GET /api/public/booking/:token": error(500, "internal", "boom") });
      renderRoute("/book/tok123");
      await screen.findByRole("alert");
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Algo salió mal");
      expect(screen.getByRole("heading", { level: 1 })).not.toHaveTextContent("Something went wrong");
    });
  });

  describe("round 3: month, compact bar, legend and chip states", () => {
    const noon = (day: number) => `2026-10-${String(day).padStart(2, "0")}T12:00:00Z`;
    const dated = (id: string, label: string, day: number) => ({ ...slot(id, label, puebla), starts_at: noon(day) });

    it("formats the month of a day for the page language, only when starts_at agrees with the label's day", () => {
      expect(dayMonth(noon(14), "mié 14", "es-MX")).toBe("octubre");
      expect(dayMonth(noon(14), "mié 14", "es-ES")).toBe("octubre");
      expect(dayMonth(noon(14), "Wed 14", "en")).toBe("October");
      expect(dayMonth(noon(16), "mié 14", "es-MX")).toBeNull();
      expect(dayMonth(noon(12), "mié 14", "es-MX")).toBeNull();
      expect(dayMonth(noon(14), "Mañana", "es-MX")).toBeNull();
      expect(dayMonth("not a date", "mié 14", "es-MX")).toBeNull();
    });

    it("adds the month to a day heading when it agrees, and leaves the heading alone when it does not", async () => {
      mockApi({
        "GET /api/public/booking/:token": {
          ...page,
          slots: [dated("m1", "mié 14 · 10:00", 14), dated("m2", "jue 15 · 10:00", 14), dated("m3", "lun 19 · 10:00", 23)],
        },
      });
      renderRoute("/book/tok123");
      const agreeing = (await screen.findAllByText("mié 14"))[0];
      expect(agreeing).toHaveTextContent("mié 14 octubre");
      expect(screen.getByText("jue 15")).toHaveTextContent("jue 15 octubre");
      // A label day more than one day from starts_at gets no month: the heading stays as the API wrote it.
      expect(screen.getByText("lun 19")).toHaveTextContent(/^lun 19$/);
      expect(screen.getByText("lun 19").querySelector(".booking__month")).toBeNull();
      // The chip's name keeps the label as the API wrote it.
      expect((await screen.findByLabelText(/mié 14 · 10:00/)).closest("label")?.textContent).toBe("mié 14 · 10:00");
    });

    it("takes the month from the UTC date and its neighbours, whatever the browser's time zone", () => {
      const zones = ["UTC", "Pacific/Kiritimati", "Pacific/Pago_Pago", "America/Mexico_City", "Europe/Madrid"];
      const before = process.env.TZ;
      try {
        for (const zone of zones) {
          process.env.TZ = zone;
          // 12:00Z on the 14th is already the 15th in Kiritimati and still the 14th everywhere else.
          expect(dayMonth(noon(14), "mié 14", "es-MX"), zone).toBe("octubre");
          expect(dayMonth(noon(14), "jue 15", "es-MX"), zone).toBe("octubre");
          expect(dayMonth(noon(14), "Wed 14", "en"), zone).toBe("October");
          // A Location behind UTC: 03:00Z on 1 November is still 31 October there.
          expect(dayMonth("2026-11-01T03:00:00Z", "sáb 31", "es-MX"), zone).toBe("octubre");
          expect(dayMonth("2026-11-01T03:00:00Z", "dom 1", "es-MX"), zone).toBe("noviembre");
        }
      } finally {
        if (before === undefined) delete process.env.TZ;
        else process.env.TZ = before;
      }
    });

    it("puts a month on every day heading, including a day the browser would date differently", async () => {
      mockApi({
        "GET /api/public/booking/:token": {
          ...page,
          slots: [dated("t1", "mié 14 · 10:00", 14), dated("t2", "jue 15 · 10:00", 14), dated("t3", "vie 16 · 10:00", 16)],
        },
      });
      renderRoute("/book/tok123");
      for (const day of ["mié 14", "jue 15", "vie 16"]) {
        expect(await screen.findByText(day)).toHaveTextContent(`${day} octubre`);
      }
    });

    it("names the lost time and the Location when a slot was just taken", async () => {
      mockApi({
        "GET /api/public/booking/:token": page,
        "POST /api/public/booking/:token": error(409, "slot_taken", "taken"),
      });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      fireEvent.click(screen.getByRole("button", { name: "Confirmar entrevista" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(/Ese horario se acaba de ocupar \(mié 14 · 10:00\) en Sazón Puebla Centro/);
    });

    it("names them in English too", async () => {
      mockApi({
        "GET /api/public/booking/:token": { ...page, language: "en" },
        "POST /api/public/booking/:token": error(409, "slot_taken", "taken"),
      });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      fireEvent.click(screen.getByRole("button", { name: "Confirm interview" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(/That time was just taken \(mié 14 · 10:00\) at Sazón Puebla Centro/);
    });

    it("shows a notice in the bar in place of the hint, so the bar stays short", async () => {
      mockApi({
        "GET /api/public/booking/:token": page,
        "POST /api/public/booking/:token": error(500, "internal", "database exploded"),
      });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      const confirm = screen.getByRole("button", { name: "Confirmar entrevista" });
      fireEvent.click(confirm);
      await screen.findByRole("alert");
      expect(within(confirm.parentElement as HTMLElement).queryByText(/Tu horario:/)).not.toBeInTheDocument();
    });
  });

  describe("round 3: way out, load failure and a link that is no longer active", () => {
    it("names WhatsApp as the way out in every state and language", async () => {
      mockApi({ "GET /api/public/booking/:token": { ...page, slots: [] } });
      const es = renderRoute("/book/tok123");
      expect(await screen.findByText(/no hay horarios disponibles/)).toHaveTextContent("Escríbenos por WhatsApp y te ayudamos");
      es.unmount();
      mockApi({ "GET /api/public/booking/:token": { ...page, language: "en", slots: [] } });
      renderRoute("/book/tok123");
      expect(await screen.findByText(/no times available/)).toHaveTextContent("Message us on WhatsApp and we will help");
    });

    it("names WhatsApp when booking fails", async () => {
      mockApi({
        "GET /api/public/booking/:token": page,
        "POST /api/public/booking/:token": error(500, "internal", "database exploded"),
      });
      renderRoute("/book/tok123");
      fireEvent.click(await screen.findByLabelText(/mié 14 · 10:00/));
      fireEvent.click(screen.getByRole("button", { name: "Confirmar entrevista" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("escríbenos por WhatsApp");
    });

    it("shows a failed load in Spanish only, in one alert, with a native Reintentar", async () => {
      mockApi({ "GET /api/public/booking/:token": error(500, "internal", "boom") });
      renderRoute("/book/tok123");
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("No pudimos cargar esta página.");
      expect(alert.querySelector("[lang]")).toBeNull();
      expect(screen.getAllByRole("alert")).toHaveLength(1);
      expect(screen.getAllByRole("button")).toHaveLength(1);
      expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
      expect(screen.getByRole("main")).toHaveAttribute("lang", "es");
      expect(screen.getByRole("heading", { level: 1 })).toHaveAccessibleName("Algo salió mal");
      expect(document.title).toBe("Algo salió mal");
    });

    it("gives a link that is no longer active its own heading and tab title, apart from an invalid link", async () => {
      mockApi({ "GET /api/public/booking/:token": error(410, "link_expired", "Este enlace ya caducó.") });
      renderRoute("/book/old");
      await screen.findByRole("alert");
      const heading = screen.getByRole("heading", { level: 1 });
      expect(heading.querySelector('[lang="es"]')).toHaveTextContent("Este enlace ya caducó");
      expect(heading.querySelector('[lang="en"]')).toHaveTextContent("This link is no longer active");
      expect(heading).not.toHaveTextContent("Enlace no disponible");
      expect(document.title).toBe("Este enlace ya caducó · This link is no longer active");
    });

    it("answers a link that is no longer active with its own words when the server sent none", async () => {
      mockApi({ "GET /api/public/booking/:token": [410, undefined] });
      renderRoute("/book/old");
      const alert = await screen.findByRole("alert");
      expect(alert.querySelector('[lang="es"]')).toHaveTextContent("Este enlace ya caducó. Escríbenos por WhatsApp y te enviamos uno nuevo.");
      expect(alert.querySelector('[lang="en"]')).toHaveTextContent("This link is no longer active. Message us on WhatsApp and we will send a new one.");
      expect(alert).not.toHaveTextContent("no es válido");
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });
  });

  describe("head", () => {
    const metaContent = (name: string) => document.head.querySelector(`meta[name="${name}"]`)?.getAttribute("content");

    it("asks for no referrer and no indexing while the page is mounted, and takes both away after", async () => {
      mockApi({ "GET /api/public/booking/:token": page });
      const { unmount } = renderRoute("/book/tok123");
      await screen.findByText(/Hola, Luis/);
      expect(metaContent("referrer")).toBe("no-referrer");
      expect(metaContent("robots")).toBe("noindex");
      unmount();
      expect(document.head.querySelector('meta[name="referrer"]')).toBeNull();
      expect(document.head.querySelector('meta[name="robots"]')).toBeNull();
    });

    it("keeps both on an invalid link", async () => {
      mockApi({ "GET /api/public/booking/:token": error(404, "not_found", "nope") });
      renderRoute("/book/bad");
      await screen.findByRole("alert");
      expect(metaContent("referrer")).toBe("no-referrer");
      expect(metaContent("robots")).toBe("noindex");
    });
  });

  describe("source", () => {
    const book = read("pages/Book.tsx");

    it("has no inline style, hex colour, legacy button class or raw table", () => {
      expect(book).not.toMatch(/style=/);
      expect(book).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(book).not.toMatch(/className="btn/);
      expect(book).not.toMatch(/<table/);
    });

    it("imports primitives by file and loads only its own surface stylesheet and ui.css", () => {
      expect(book).not.toMatch(/components\/ui(\/index(\.ts)?)?["']/);
      expect(book).not.toMatch(/MotionProvider|MotionConfig|Toast|ThemeToggle/);
      const stylesheets = [...book.matchAll(/import "([^"]+\.css)"/g)].map((m) => m[1]);
      expect(stylesheets.sort()).toEqual(["../components/ui/ui.css", "../styles/surfaces/booking.css"]);
    });

    it("keeps the word expired out of the page's copy and names the 410 state gone", () => {
      const copy = read("lib/labels/booking.ts");
      expect(copy).not.toMatch(/expire/i);
      expect(copy).toMatch(/"gone"/);
      expect(book).not.toMatch(/expire[ds]?\b/i);
    });
  });

  describe("stylesheet", () => {
    const css = read("styles/surfaces/booking.css");
    const rules = css.replace(/@media[^{]*/g, "@media ");

    it("is a mobile-first column that never hides overflow or fixes a width in pixels", () => {
      expect(css).not.toMatch(/overflow-x\s*:\s*hidden/);
      expect(rules).not.toMatch(/(^|[\s;{])(min-|max-)?width\s*:\s*[\d.]+px/);
      expect(css).not.toMatch(/@media[^{]*max-width/);
      expect(css).toMatch(/@media \(min-width:\s*900px\)/);
      expect(css).toMatch(/max-width\s*:/);
      expect(css).toMatch(/overflow-wrap\s*:\s*anywhere/);
    });

    it("sticks Confirm to the bottom with a safe-area inset, and keeps a focused slot clear of it", () => {
      expect(css).toMatch(/position\s*:\s*sticky/);
      expect(css).toMatch(/bottom\s*:\s*0/);
      expect(css).toMatch(/env\(safe-area-inset-bottom\)/);
      expect(css).toMatch(/scroll-padding-bottom/);
    });

    it("makes every target 44 px from tokens", () => {
      const target = /min-height\s*:\s*calc\(var\(--space-7\) \+ var\(--space-3\)\)/g;
      expect(css.match(target)?.length ?? 0).toBeGreaterThanOrEqual(3);
    });

    it("sets the Location legend above the times, and keeps the hint to two lines", () => {
      expect(css).toMatch(/\.booking__legend\s*\{[^}]*font-size\s*:\s*var\(--text-lg\)/);
      expect(css).toMatch(/\.booking__legend\s*\{[^}]*font-weight\s*:\s*var\(--weight-bold\)/);
      expect(css).toMatch(/\.booking__hint\s*\{[^}]*display\s*:\s*-webkit-box/);
      expect(css).toMatch(/\.booking__hint\s*\{[^}]*-webkit-line-clamp\s*:\s*2/);
      expect(css).toMatch(/\.booking__hint\s*\{[^}]*-webkit-box-orient\s*:\s*vertical/);
    });

    it("marks a chosen chip by more than colour, and rings a focused chip", () => {
      expect(css).toMatch(/\.booking__slot:has\(\.booking__radio:checked\)\s*\{[^}]*box-shadow\s*:\s*inset/);
      expect(css).toMatch(/\.booking__slot:has\(\.booking__radio:checked\) strong\s*\{[^}]*font-weight\s*:\s*var\(--weight-bold\)/);
      expect(css).toMatch(/\.booking__slot:has\(\.booking__radio:focus-visible\)\s*\{[^}]*outline\s*:\s*2px solid var\(--focus\)/);
    });

    it("lifts the legend out of the fieldset border and lets the chips fill the column", () => {
      expect(css).toMatch(/\.booking__legend\s*\{[^}]*float\s*:\s*left/);
      expect(css).toMatch(/\.booking__legend\s*\{[^}]*width\s*:\s*100%/);
      expect(css).toMatch(/\.booking__times\s*\{[^}]*grid-template-columns\s*:\s*repeat\(auto-fit,/);
    });

    it("loads nothing but tokens: no colour literal and no custom property of its own", () => {
      expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/);
      expect(css).not.toMatch(/(^|[\s;{])--[a-z]/);
    });
  });
});
