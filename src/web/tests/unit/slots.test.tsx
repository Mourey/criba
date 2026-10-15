import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { locations, puebla, recruiterMe, slot } from "./fixtures.ts";
import { error, mockApi, renderRoute } from "./helpers.tsx";

describe("Slot manager", () => {
  const base = {
    "GET /api/auth/me": recruiterMe,
    "GET /api/locations": { items: [locations[0]] },
  };

  it("lists a Location's slots with status and booking", async () => {
    mockApi({
      ...base,
      "GET /api/slots": {
        items: [
          slot,
          { ...slot, id: "slot_8x3", starts_at: "2026-10-14T17:00:00Z", ends_at: "2026-10-14T17:30:00Z", local_label: "mié 14 · 11:00", status: "booked", booking: { application_id: "app_1", candidate_name: "Luis Hernández", booked_via: "chat", booked_at: "2026-10-10T09:00:00Z" } },
        ],
      },
    });
    renderRoute("/slots");
    expect(await screen.findByText("mié 14 · 10:00")).toBeInTheDocument();
    expect(screen.getByText("2026-10-14")).toBeInTheDocument();
    expect(screen.getByText(/Luis Hernández \(booked in chat\)/)).toBeInTheDocument();
    const rows = screen.getAllByRole("listitem");
    expect(within(rows[0]).getByRole("button", { name: "Cancel slot" })).toBeInTheDocument();
    expect(within(rows[1]).queryByRole("button", { name: "Cancel slot" })).not.toBeInTheDocument();
  });

  it("publishes slots entered in the Location's time zone as UTC", async () => {
    const mock = mockApi({ ...base, "GET /api/slots": { items: [] }, "POST /api/slots": [201, { items: [slot] }] });
    renderRoute("/slots");
    await screen.findByText("No slots published yet.");

    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-10-14" } });
    fireEvent.change(screen.getByLabelText("Starts"), { target: { value: "10:00" } });
    expect(screen.getByLabelText("Ends")).toHaveValue("10:30");
    fireEvent.click(screen.getByRole("button", { name: "Add to batch" }));
    fireEvent.change(screen.getByLabelText("Starts"), { target: { value: "16:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Add to batch" }));
    fireEvent.click(screen.getByRole("button", { name: "Publish 2 slots" }));

    await waitFor(() => expect(mock.find("POST", "/api/slots")).toHaveLength(1));
    expect(mock.find("POST", "/api/slots")[0].body).toEqual({
      location_id: puebla.id,
      slots: [
        { starts_at: "2026-10-14T16:00:00Z", ends_at: "2026-10-14T16:30:00Z" },
        { starts_at: "2026-10-14T22:00:00Z", ends_at: "2026-10-14T22:30:00Z" },
      ],
    });
  });

  it("refuses a slot that ends before it starts", async () => {
    mockApi({ ...base, "GET /api/slots": { items: [] } });
    renderRoute("/slots");
    await screen.findByText("No slots published yet.");
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-10-14" } });
    fireEvent.change(screen.getByLabelText("Ends"), { target: { value: "09:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Add to batch" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A slot must end after it starts.");
  });

  it("cancels an open slot and reports a booked one the server refuses", async () => {
    const mock = mockApi({
      ...base,
      "GET /api/slots": { items: [slot] },
      "DELETE /api/slots/:id": error(409, "slot_booked", "Reject or reschedule the booking first."),
    });
    renderRoute("/slots");
    fireEvent.click(await screen.findByRole("button", { name: "Cancel slot" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Reject or reschedule the booking first.");
    expect(mock.find("DELETE", "/api/slots/slot_8x2")).toHaveLength(1);
  });

  it("moves an unbooked slot", async () => {
    const mock = mockApi({ ...base, "GET /api/slots": { items: [slot] }, "PATCH /api/slots/:id": slot });
    renderRoute("/slots");
    fireEvent.click(await screen.findByRole("button", { name: "Move" }));
    fireEvent.change(screen.getByLabelText("New start time"), { target: { value: "11:00" } });
    fireEvent.change(screen.getByLabelText("New end time"), { target: { value: "11:30" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mock.find("PATCH", "/api/slots/slot_8x2")).toHaveLength(1));
    expect(mock.find("PATCH", "/api/slots/slot_8x2")[0].body).toEqual({ starts_at: "2026-10-14T17:00:00Z", ends_at: "2026-10-14T17:30:00Z" });
  });
});

// Phase 4 (recruiter-pages): three states for the Locations and the slots queries, and actions tied to
// their row. Appended; the five tests above stay as they were.

describe("Slot manager states and layout", () => {
  const base = {
    "GET /api/auth/me": recruiterMe,
    "GET /api/locations": { items: [locations[0]] },
  };
  const forever = () => new Promise<object>(() => undefined);
  const skeletons = () => document.querySelectorAll(".ui-skeleton").length;

  it("shows a skeleton and no Loading text while the Locations load", async () => {
    mockApi({ ...base, "GET /api/locations": forever, "GET /api/slots": { items: [slot] } });
    renderRoute("/slots");
    await screen.findByRole("heading", { level: 1, name: "Interview slots" });
    await waitFor(() => expect(skeletons()).toBeGreaterThan(0));
    expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No Locations/)).not.toBeInTheDocument();
    expect(screen.queryAllByRole("status")).toEqual([]);
  });

  it("shows a skeleton and no Loading text while a Location's slots load", async () => {
    mockApi({ ...base, "GET /api/slots": forever });
    renderRoute("/slots");
    await screen.findByRole("option", { name: puebla.name });
    await waitFor(() => expect(skeletons()).toBeGreaterThan(0));
    expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
    expect(screen.queryByText("No slots published yet.")).not.toBeInTheDocument();
  });

  it("says there are no Locations to publish for when the list is empty", async () => {
    mockApi({ ...base, "GET /api/locations": { items: [] } });
    renderRoute("/slots");
    expect(await screen.findByText(/No Locations are available to you yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add to batch" })).toBeDisabled();
    expect(screen.queryByText("No slots published yet.")).not.toBeInTheDocument();
  });

  it("says no slots are published yet for a Location with none", async () => {
    mockApi({ ...base, "GET /api/slots": { items: [] } });
    renderRoute("/slots");
    expect(await screen.findAllByText("No slots published yet.")).toHaveLength(1);
    expect(skeletons()).toBe(0);
  });

  it("shows an error state for the Locations whose Retry asks once more", async () => {
    let failing = true;
    const mock = mockApi({
      ...base,
      "GET /api/locations": () => (failing ? error(500, "internal", "boom") : { items: [locations[0]] }),
      "GET /api/slots": { items: [slot] },
    });
    renderRoute("/slots");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load Locations. boom");
    const before = mock.find("GET", "/api/locations").length;
    failing = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("mié 14 · 10:00")).toBeInTheDocument();
    expect(mock.find("GET", "/api/locations")).toHaveLength(before + 1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an error state for the slots whose Retry asks once more", async () => {
    let failing = true;
    const mock = mockApi({
      ...base,
      "GET /api/slots": () => (failing ? error(500, "internal", "boom") : { items: [slot] }),
    });
    renderRoute("/slots");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load slots. boom");
    // The publish form stays usable.
    expect(screen.getByRole("button", { name: "Add to batch" })).toBeEnabled();
    const before = mock.find("GET", "/api/slots").length;
    failing = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("mié 14 · 10:00")).toBeInTheDocument();
    expect(mock.find("GET", "/api/slots")).toHaveLength(before + 1);
  });

  it("keeps the list and shows no skeleton while it refetches after a mutation", async () => {
    let gets = 0;
    const mock = mockApi({
      ...base,
      "GET /api/slots": () => (++gets === 1 ? { items: [slot] } : forever()),
      "DELETE /api/slots/:id": [204, undefined],
    });
    renderRoute("/slots");
    fireEvent.click(await screen.findByRole("button", { name: "Cancel slot" }));
    await waitFor(() => expect(mock.find("GET", "/api/slots")).toHaveLength(2));
    expect(screen.getByText("mié 14 · 10:00")).toBeInTheDocument();
    expect(skeletons()).toBe(0);
  });

  it("shows the skeleton, not the previous Location's slots, after a Location switch", async () => {
    mockApi({
      ...base,
      "GET /api/locations": { items: locations },
      "GET /api/slots": (call) => (call.query.get("location_id") === locations[1].id ? forever() : { items: [slot] }),
    });
    renderRoute("/slots");
    expect(await screen.findByText("mié 14 · 10:00")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Location"), { target: { value: locations[1].id } });
    await waitFor(() => expect(skeletons()).toBeGreaterThan(0));
    expect(screen.queryByText("mié 14 · 10:00")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel slot" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: `Slots at ${locations[1].name}` })).toBeInTheDocument();
  });

  it("points Move and Cancel slot at their slot, keeping the names", async () => {
    mockApi({ ...base, "GET /api/slots": { items: [slot] } });
    renderRoute("/slots");
    const row = (await screen.findAllByRole("listitem"))[0];
    for (const name of ["Move", "Cancel slot"]) {
      const button = within(row).getByRole("button", { name });
      const target = document.getElementById(button.getAttribute("aria-describedby") ?? "");
      expect(target, name).not.toBeNull();
      expect(row).toContainElement(target);
      expect(target).toHaveTextContent("mié 14 · 10:00");
    }
  });

  it("points Remove at its batch entry and keeps the Publish count", async () => {
    mockApi({ ...base, "GET /api/slots": { items: [] } });
    renderRoute("/slots");
    await screen.findByText("No slots published yet.");
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-10-14" } });
    fireEvent.click(screen.getByRole("button", { name: "Add to batch" }));
    const batch = screen.getByRole("list", { name: "Slots to publish" });
    const remove = within(batch).getByRole("button", { name: "Remove" });
    const target = document.getElementById(remove.getAttribute("aria-describedby") ?? "");
    expect(target).toHaveTextContent("2026-10-14 10:00-10:30");
    expect(screen.getByRole("button", { name: "Publish 1 slot" })).toBeInTheDocument();
    fireEvent.click(remove);
    expect(screen.queryByRole("list", { name: "Slots to publish" })).not.toBeInTheDocument();
  });

  it("groups slots under an h3 date and gives the status a Tag", async () => {
    mockApi({
      ...base,
      "GET /api/slots": {
        items: [
          slot,
          { ...slot, id: "slot_8x3", starts_at: "2026-10-15T17:00:00Z", ends_at: "2026-10-15T17:30:00Z", local_label: "jue 15 · 11:00", status: "booked", booking: { application_id: "app_1", candidate_name: "Luis Hernández", booked_via: "page", booked_at: "2026-10-10T09:00:00Z" } },
        ],
      },
    });
    renderRoute("/slots");
    expect(await screen.findByRole("heading", { level: 3, name: "2026-10-14" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "2026-10-15" })).toBeInTheDocument();
    const rows = screen.getAllByRole("listitem");
    expect(within(rows[0]).getByText("Open")).toHaveClass("ui-tag");
    expect(within(rows[1]).getByText("Booked")).toHaveClass("ui-tag");
    expect(rows[1]).toHaveTextContent("booked on the booking page");
    expect(screen.getByRole("region", { name: "Publish Interview slots" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: `Slots at ${puebla.name}` })).toBeInTheDocument();
  });

  it("opens the move form with its three named inputs and closes it again", async () => {
    mockApi({ ...base, "GET /api/slots": { items: [slot] } });
    renderRoute("/slots");
    fireEvent.click(await screen.findByRole("button", { name: "Move" }));
    for (const label of ["New date", "New start time", "New end time"]) expect(screen.getByLabelText(label)).toBeInTheDocument();
    expect(screen.getByLabelText("New start time")).toHaveValue("10:00");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("New date")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move" })).toBeInTheDocument();
  });
});
