import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App.jsx";
import { DASHBOARD, mockFetch, setToken } from "./test-utils.js";

function overviewRoutes({ intentStatus = 200, intentBody } = {}) {
  return [
    { match: "/v1/dashboard", body: DASHBOARD },
    {
      match: "/v1/commits",
      body: { commits: [], momentum: DASHBOARD.momentum },
    },
    {
      match: "/v1/intent",
      method: "PUT",
      status: intentStatus,
      body: intentBody ?? ((url, init) => ({ north_star: JSON.parse(init.body).north_star, updated_at: "2026-11-19T10:00:00Z" })),
    },
  ];
}

describe("project goal (North Star)", () => {
  it("saves the goal, then the handoff preview request carries it through", async () => {
    let savedGoal = null;
    const { calls } = mockFetch([
      ...overviewRoutes(),
      {
        match: "/v1/handoffs/preview",
        body: () => ({
          payload: `# Goal\n${savedGoal ?? ""}\n\n# Done\n- shipped things`,
          method: "deterministic",
          written: false,
        }),
      },
    ]);
    setToken();
    render(<App />);

    // Relabelled: "Project goal" with North Star as supporting wording.
    expect(await screen.findByText("Project goal")).toBeInTheDocument();
    expect(screen.getByText(/North Star/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Write the goal" }));
    fireEvent.change(screen.getByLabelText("Project goal"), {
      target: { value: "Ship the local-first dashboard" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save goal" }));

    await waitFor(() =>
      expect(
        calls.find((call) => call.url === "/v1/intent" && call.method === "PUT")
      ).toBeTruthy()
    );
    const intentCall = calls.find((call) => call.url === "/v1/intent");
    expect(intentCall.body).toEqual({ north_star: "Ship the local-first dashboard" });
    savedGoal = intentCall.body.north_star;

    await screen.findByText("Ship the local-first dashboard");

    // Go to Hand off and preview.
    fireEvent.click(screen.getByRole("button", { name: "Hand off" }));
    fireEvent.click(await screen.findByRole("button", { name: "Preview" }));

    const preview = await waitFor(() => {
      const found = calls.find((call) => call.url === "/v1/handoffs/preview");
      expect(found).toBeTruthy();
      return found;
    });
    expect(preview.method).toBe("POST");
    expect(preview.body).toEqual({
      to_agent: "codex",
      from_agent: null,
      budget: 2000,
      polish: false,
      force: false,
    });

    // The goal saved above shows up in the rendered preview.
    expect(await screen.findByText(/Ship the local-first dashboard/)).toBeInTheDocument();
  });

  it("keeps the draft and shows an error when the save fails", async () => {
    mockFetch(overviewRoutes({ intentStatus: 400, intentBody: { detail: "Goal could not be saved." } }));
    setToken();
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Write the goal" }));
    const textarea = screen.getByLabelText("Project goal");
    fireEvent.change(textarea, { target: { value: "A goal that will fail" } });
    fireEvent.click(screen.getByRole("button", { name: "Save goal" }));

    expect(await screen.findByText("Goal could not be saved.")).toBeInTheDocument();
    // Draft preserved, still editing, retryable.
    expect(screen.getByLabelText("Project goal")).toHaveValue("A goal that will fail");
    expect(screen.getByRole("button", { name: "Save goal" })).toBeEnabled();
  });
});
