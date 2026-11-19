import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import HandoffTab from "./components/HandoffTab.jsx";
import { mockFetch, setToken } from "./test-utils.js";

function previewPayload() {
  return {
    payload: "# Goal\nKeep the dashboard honest\n\n# Next steps\n- polish the guide",
    method: "deterministic",
    written: false,
    source_agent: "codex",
    target_path: ".codex/handoffs/next.md",
  };
}

describe("handoff preview / copy / write", () => {
  it("previews, warns about same-agent handoff, copies and writes", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    const { calls } = mockFetch([
      { match: "/v1/tests/history", body: { runs: [], last_run_age_seconds: 900, stale: false } },
      {
        match: "/v1/handoffs/preview",
        body: () => ({
          ...previewPayload(),
          warnings: ["Warning — the source and destination agents are the same. Check --from and --to."],
        }),
      },
      {
        match: "/v1/handoffs",
        method: "POST",
        status: 201,
        body: {
          ...previewPayload(),
          written: true,
          test: { passed: 12, failed: 0, skipped: 1, exit_code: 0, age_seconds: 60, stale: false },
        },
      },
    ]);
    setToken();
    render(<HandoffTab />);

    // Same agent on both sides.
    fireEvent.change(screen.getByLabelText("Coming from"), { target: { value: "codex" } });
    fireEvent.change(screen.getByLabelText("Destination agent"), { target: { value: "codex" } });
    fireEvent.change(screen.getByLabelText("Token budget"), { target: { value: "1500" } });

    fireEvent.click(screen.getByRole("button", { name: "Preview" }));

    const previewCall = await waitFor(() => {
      const found = calls.find((call) => call.url === "/v1/handoffs/preview");
      expect(found).toBeTruthy();
      return found;
    });
    expect(previewCall.method).toBe("POST");
    expect(previewCall.body).toEqual({
      to_agent: "codex",
      from_agent: "codex",
      budget: 1500,
      polish: false, // the only model-assisted step stays OFF by default
      force: false,
    });

    // Server warnings render prominently, plus the client-side same-agent note.
    expect(
      await screen.findByText(/source and destination agents are the same/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/Source and destination are the same agent/)).toBeInTheDocument();
    expect(screen.getByText(/Keep the dashboard honest/)).toBeInTheDocument();

    // Copy
    fireEvent.click(screen.getByRole("button", { name: "Copy to clipboard" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText.mock.calls[0][0]).toContain("Keep the dashboard honest");
    expect(await screen.findByRole("button", { name: "Copied ✓" })).toBeInTheDocument();

    // Write
    fireEvent.click(screen.getByRole("button", { name: "Write handoff" }));
    const writeCall = await waitFor(() => {
      const found = calls.find((call) => call.url === "/v1/handoffs");
      expect(found).toBeTruthy();
      return found;
    });
    expect(writeCall.method).toBe("POST");
    expect(writeCall.body.polish).toBe(false);

    // Success + target path + recorded test result with age.
    expect(await screen.findByText(/The handoff was written/)).toBeInTheDocument();
    expect(screen.getByText(".codex/handoffs/next.md")).toBeInTheDocument();
    expect(screen.getByText("12 passed")).toBeInTheDocument();
    expect(screen.getByText(/1m ago/)).toBeInTheDocument();
    // The polish toggle carries its "only model-assisted step" note.
    expect(screen.getByText(/polishing is the/)).toBeInTheDocument();
  });

  it("keeps field drafts when the preview request fails", async () => {
    mockFetch([
      { match: "/v1/tests/history", body: { runs: [] } },
      { match: "/v1/handoffs/preview", status: 400, body: { detail: "Budget too small for these files." } },
    ]);
    setToken();
    render(<HandoffTab />);

    fireEvent.change(screen.getByLabelText("Token budget"), { target: { value: "130" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));

    expect(await screen.findByText("Budget too small for these files.")).toBeInTheDocument();
    expect(screen.getByLabelText("Token budget")).toHaveValue(130);
    expect(screen.getByRole("button", { name: "Preview" })).toBeEnabled();
  });

  it("never enables polishing by default", () => {
    mockFetch([{ match: "/v1/tests/history", body: { runs: [] } }]);
    setToken();
    render(<HandoffTab />);
    const polish = screen.getByRole("checkbox", { name: /Polish the handoff/ });
    expect(polish).not.toBeChecked();
    expect(screen.getByText(/polishing is the/)).toBeInTheDocument();
    expect(screen.getByText(/Everything except polishing is computed locally/)).toBeInTheDocument();
  });
});
