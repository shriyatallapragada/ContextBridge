import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App.jsx";
import { DASHBOARD, mockFetch, setToken } from "./test-utils.js";

describe("token gate and auth", () => {
  it("changes projects without restarting the dashboard", async () => {
    let current = DASHBOARD;
    mockFetch([
      { match: "/v1/dashboard", body: () => current },
      { match: "/v1/project", method: "POST", body: () => {
        current = { ...DASHBOARD, project: "Other project", root: "/Users/dev/Other" };
        return { root: current.root, project: current.project };
      } },
    ]);
    setToken();
    render(<App />);
    await screen.findByText("Demo project");
    fireEvent.click(screen.getByRole("button", { name: "Change project" }));
    fireEvent.change(screen.getByLabelText("Project folder"), { target: { value: "/Users/dev/Other" } });
    fireEvent.click(screen.getByRole("button", { name: "Open project" }));
    expect(await screen.findByText("Other project")).toBeInTheDocument();
    expect(screen.getByText("/Users/dev/Other")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps the current project when switching fails", async () => {
    mockFetch([
      { match: "/v1/dashboard", body: DASHBOARD },
      { match: "/v1/project", method: "POST", status: 409, body: { detail: "Wait for the current run to finish." } },
    ]);
    setToken();
    render(<App />);
    await screen.findByText("Demo project");
    fireEvent.click(screen.getByRole("button", { name: "Change project" }));
    fireEvent.click(screen.getByRole("button", { name: "Open project" }));
    expect(await screen.findByText(/Wait for the current run/)).toBeInTheDocument();
    expect(screen.getByText("Demo project")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("asks for a token when none is stored", () => {
    mockFetch([{ match: "/v1/dashboard", body: DASHBOARD }]);
    render(<App />);
    expect(screen.getByText("Session token")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open dashboard" })).toBeInTheDocument();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("sends the bearer header on every /v1 call after the token is accepted", async () => {
    const { calls } = mockFetch([
      { match: "/v1/dashboard", body: DASHBOARD },
      { match: "/v1/settings", body: { mode: "offline", provider: "none", polish_handoffs: false } },
    ]);
    render(<App />);

    fireEvent.change(screen.getByLabelText("Session token"), {
      target: { value: "abc123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Open dashboard" }));

    await waitFor(() => expect(screen.getByText("/Users/dev/ContextBridge")).toBeInTheDocument());

    expect(calls.length).toBeGreaterThan(0);
    for (const call of calls) {
      expect(call.url.startsWith("/")).toBe(true); // origin-absolute, never under /ui/
      expect(call.headers.Authorization).toBe("Bearer abc123");
    }
    // All seven destinations are present and discoverable in the sidebar nav.
    const nav = screen.getByRole("navigation");
    for (const label of [
      "Overview",
      "Notes and tasks",
      "Changes and tests",
      "Hand off",
      "Project guide",
      "Chat bridge",
      "Settings",
    ]) {
      expect(within(nav).getByRole("button", { name: label })).toBeInTheDocument();
    }
    // Project header: name + path + honest switch-project note.
    expect(screen.getByText("Demo project")).toBeInTheDocument();
    expect(screen.getByText("/Users/dev/ContextBridge")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change project" })).toBeInTheDocument();
  });

  it("returns to the gate with an explanation when the API rejects the token", async () => {
    mockFetch([
      { match: "/v1/dashboard", status: 401, body: { detail: "A valid session bearer token is required." } },
    ]);
    setToken("stale-token");
    render(<App />);

    await waitFor(() =>
      expect(screen.getByText(/valid session bearer token/i)).toBeInTheDocument()
    );
    expect(screen.getByText("Session token")).toBeInTheDocument();
    expect(window.localStorage.getItem("cb_token")).toBeNull();
  });

  it("keeps the selected destination across a re-render (hash/localStorage)", async () => {
    mockFetch([
      { match: "/v1/dashboard", body: DASHBOARD },
      { match: "/v1/settings", body: { mode: "offline", provider: "none", polish_handoffs: false } },
    ]);
    setToken();
    render(<App />);

    await waitFor(() => expect(screen.getByText("/Users/dev/ContextBridge")).toBeInTheDocument());
    const nav = screen.getByRole("navigation");
    fireEvent.click(within(nav).getByRole("button", { name: "Settings" }));

    expect(window.localStorage.getItem("cb_view")).toBe("settings");
    expect(window.location.hash).toContain("view=settings");
    expect(await screen.findByLabelText("Exact model name")).toBeInTheDocument();
  });
});
