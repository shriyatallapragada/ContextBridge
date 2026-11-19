import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ChatBridge from "./components/ChatBridge.jsx";
import { mockFetch, setToken } from "./test-utils.js";

const TRANSCRIPT = "- [ ] ship the dashboard\n- [x] parse transcripts";

describe("chat bridge — import tasks", () => {
  it("Preview does not save; Save tasks saves without any git call", async () => {
    const { calls } = mockFetch([
      {
        match: "/v1/chat-import",
        body: (target, init) => {
          const body = JSON.parse(init.body);
          if (!body.commit) return { parsed: [{ kind: "checkbox", content: "ship the dashboard", status: "open", line: 1 }], committed: false };
          return {
            parsed: [{ kind: "checkbox", content: "ship the dashboard", status: "open", line: 1 }],
            created: [{ id: "t9", content: "ship the dashboard" }],
            duplicates: 0,
            committed: true,
            counts: { total: 1, open: 1 },
          };
        },
      },
    ]);
    setToken();
    render(<ChatBridge />);

    const textarea = screen.getByLabelText("Paste a chat transcript");
    fireEvent.change(textarea, { target: { value: TRANSCRIPT } });

    // Preview → commit:false, nothing saved.
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    const preview = await waitFor(() => {
      const found = calls.find((call) => call.url === "/v1/chat-import");
      expect(found).toBeTruthy();
      return found;
    });
    expect(preview.body).toEqual({ text: TRANSCRIPT, commit: false });
    expect(await screen.findByText(/Preview only — nothing saved/)).toBeInTheDocument();
    expect(calls).toHaveLength(1);

    // Save tasks → commit:true.
    fireEvent.click(screen.getByRole("button", { name: "Save tasks" }));
    await waitFor(() => {
      const saves = calls.filter(
        (call) => call.url === "/v1/chat-import" && call.body && call.body.commit === true
      );
      expect(saves).toHaveLength(1);
    });
    expect(await screen.findByText(/1 task saved/)).toBeInTheDocument();

    // No git endpoint of any kind was touched (the old label claimed a commit).
    expect(calls.every((call) => !call.url.includes("commits"))).toBe(true);
    expect(calls.every((call) => !call.url.includes("git"))).toBe(true);
    expect(screen.getByText(/never creates a git commit/)).toBeInTheDocument();
  });
});

describe("chat bridge — conversation handoff", () => {
  it("previews structured sections with warnings, and copies the text", async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    const { calls } = mockFetch([
      {
        match: "/v1/chat/handoff",
        method: "POST",
        body: {
          text: "# Goal\nship the local dashboard\n\n# Done\nparsed transcripts",
          warnings: ["Secret-looking values were redacted.", "Truncated 1,024 characters."],
          sections: {
            Goal: ["ship the local dashboard"],
            Done: ["parsed transcripts"],
            Decisions: ["keep parsing deterministic"],
            "Current state": ["dashboard is cream and burgundy"],
            "Open problems": ["explain coverage is partial"],
            "Next steps": ["wire the test runner"],
            Files: ["`frontend/src/App.jsx`"],
          },
          source_chars: 5000,
          omitted_chars: 1024,
        },
      },
    ]);
    setToken();
    render(<ChatBridge />);

    fireEvent.change(screen.getByLabelText("Conversation to hand off"), {
      target: { value: "You: let's ship it\nAssistant: parsing done" },
    });
    fireEvent.change(screen.getByLabelText(/^Goal/), { target: { value: "ship it" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview handoff" }));

    const call = await waitFor(() => {
      const found = calls.find((entry) => entry.url === "/v1/chat/handoff");
      expect(found).toBeTruthy();
      return found;
    });
    expect(call.body).toEqual({
      text: "You: let's ship it\nAssistant: parsing done",
      goal: "ship it",
      budget: 2000,
    });

    // All seven sections render, in the server's order, as complete statements.
    for (const title of [
      "Goal",
      "Done",
      "Decisions",
      "Current state",
      "Open problems",
      "Next steps",
      "Files",
    ]) {
      expect(await screen.findByRole("heading", { name: title })).toBeInTheDocument();
    }
    expect(screen.getByText("dashboard is cream and burgundy")).toBeInTheDocument();
    expect(screen.getByText("`frontend/src/App.jsx`")).toBeInTheDocument();

    // Warnings are prominent.
    expect(screen.getByText("Secret-looking values were redacted.")).toBeInTheDocument();
    expect(screen.getByText(/Truncated 1,024 characters/)).toBeInTheDocument();
    expect(screen.getByText(/5,000 source characters/)).toBeInTheDocument();

    // Copy exports the preview text.
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText.mock.calls[0][0]).toContain("ship the local dashboard");
  });

  it("imports a file and offers it to the handoff box", async () => {
    const { calls } = mockFetch([
      { match: "/v1/chat/import-file", method: "POST", body: { text: "notes from the meeting", warnings: ["PDF layout simplified."] } },
    ]);
    setToken();
    render(<ChatBridge />);

    fireEvent.change(screen.getByLabelText("File path inside this project"), {
      target: { value: "docs/notes.pdf" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import file" }));

    await waitFor(() =>
      expect(calls.some((call) => call.url === "/v1/chat/import-file")).toBe(true)
    );
    expect(await screen.findByText("notes from the meeting")).toBeInTheDocument();
    expect(screen.getByText("PDF layout simplified.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Use in conversation handoff ↑" }));
    expect(screen.getByLabelText("Conversation to hand off")).toHaveValue("notes from the meeting");
  });
});
