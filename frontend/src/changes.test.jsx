import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ChangesTestsTab from "./components/ChangesTestsTab.jsx";
import { mockFetch, setToken } from "./test-utils.js";

function routes({ statusStates }) {
  let statusIndex = 0;
  return [
    {
      match: "/v1/changes",
      body: {
        branch: "phase-5-gui",
        files: [
          { path: "frontend/src/App.jsx", status: "M", insertions: 12, deletions: 3 },
        ],
        counts: { insertions: 12, deletions: 3, files: 1 },
      },
    },
    {
      match: "/v1/commits",
      body: {
        commits: [
          { short_sha: "abc1234", subject: "Step 6: diagrams", committed_at: "2026-11-18T09:00:00Z" },
        ],
      },
    },
    {
      match: "/v1/tests/detect",
      body: { command: "npm run test", detected: true, framework: "vitest" },
    },
    {
      match: "/v1/tests/run",
      method: "POST",
      body: { started: true },
    },
    {
      match: "/v1/tests/status",
      body: () => {
        const state = statusStates[Math.min(statusIndex, statusStates.length - 1)];
        statusIndex += 1;
        return state;
      },
    },
    { match: "/v1/tests/history", body: { runs: [], last_run_age_seconds: 900, stale: false } },
  ];
}

const RUNNING = {
  running: true,
  status: "running",
  output: "RUN v5.1.1 /project\n",
};

const FINISHED = {
  running: false,
  status: "finished",
  output: "Test Files  2 failed | 8 passed (10)\nTests  5 failed | 686 passed (691)\n",
  report: {
    passed: 686,
    failed: 5,
    skipped: 1,
    total: 692,
    exit_code: 1,
    failures: [
      { test: "goal save keeps the draft", file: "src/overview.test.jsx", message: "expected 400" },
      { test: "graph selector syncs", file: "src/guide.test.jsx", message: "timed out" },
    ],
  },
};

describe("test run polling", () => {
  it("polls a running command and renders the report counts when finished", async () => {
    const { calls } = mockFetch(routes({ statusStates: [RUNNING, FINISHED] }));
    setToken();
    render(<ChangesTestsTab />);

    // Prefilled from detection, but never started automatically.
    expect(await screen.findByDisplayValue("npm run test")).toBeInTheDocument();
    expect(calls.some((call) => call.url === "/v1/tests/run")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /Run tests/ }));

    const post = await waitFor(() => {
      const found = calls.find((call) => call.url === "/v1/tests/run" && call.method === "POST");
      expect(found).toBeTruthy();
      return found;
    });
    expect(post.body).toEqual({ command: "npm run test" });

    // Poll until finished — counts render as text badges (never colour alone).
    expect(await screen.findByText("686 passed", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText("5 failed")).toBeInTheDocument();
    expect(screen.getByText("1 skipped")).toBeInTheDocument();
    expect(screen.getByText("exit 1")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();

    // Failures listed with test name, file and message.
    expect(screen.getByText("goal save keeps the draft")).toBeInTheDocument();
    expect(screen.getByText("src/overview.test.jsx")).toBeInTheDocument();
    expect(screen.getByText("expected 400")).toBeInTheDocument();

    // Polling happened more than once (running → finished).
    const polls = calls.filter(
      (call) => call.url === "/v1/tests/status" && call.method === "GET"
    );
    expect(polls.length).toBeGreaterThanOrEqual(2);

    // Raw output lives in the command-output pane.
    expect(screen.getByText(/Test Files/)).toBeInTheDocument();
  });

  it("keeps the manual command editable and does not auto-run on refresh", async () => {
    const { calls } = mockFetch(routes({ statusStates: [FINISHED] }));
    setToken();
    render(<ChangesTestsTab />);

    await screen.findByDisplayValue("npm run test");
    fireEvent.change(screen.getByLabelText(/command/i), {
      target: { value: "npm test -- --watch=false" },
    });

    // Nothing ran on its own.
    expect(calls.some((call) => call.url === "/v1/tests/run")).toBe(false);
    expect(calls.some((call) => call.url === "/v1/tests/status")).toBe(false);

    // History context still renders ("recorded runs" badge).
    expect(screen.getByText(/recorded run/)).toBeInTheDocument();
    expect(screen.getByLabelText(/command/i)).toHaveValue("npm test -- --watch=false");
  });
});
