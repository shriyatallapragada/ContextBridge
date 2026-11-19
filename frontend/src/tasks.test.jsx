import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import TasksTab from "./components/TasksTab.jsx";
import { mockFetch, setToken } from "./test-utils.js";

const TASK = {
  id: "t1",
  content: "Ship the notes panel",
  status: "open",
  source: "chat",
  created_at: "2026-11-18T10:00:00Z",
};

describe("task mutations refresh counts", () => {
  it("resolving a task updates the filter counts", async () => {
    let tasks = [TASK];
    let counts = { open: 1, resolved: 0, dismissed: 0, superseded: 0, total: 1 };

    const { calls } = mockFetch([
      {
        match: "/v1/tasks",
        body: () => ({ tasks: tasks.map((task) => ({ ...task })), counts: { ...counts } }),
      },
      {
        match: "/v1/tasks/t1",
        method: "PATCH",
        body: (target, init) => {
          const patch = JSON.parse(init.body);
          tasks = tasks.map((task) => (task.id === "t1" ? { ...task, status: patch.status } : task));
          counts = { open: 0, resolved: 1, dismissed: 0, superseded: 0, total: 1 };
          return { id: "t1", status: patch.status };
        },
      },
      { match: "/v1/notes", body: { notes: [], counts: { total: 0 } } },
      { match: "/v1/chat-import", body: { parsed: [], committed: false } },
    ]);
    setToken();
    render(<TasksTab />);

    expect(await screen.findByText("Ship the notes panel")).toBeInTheDocument();
    const openChip = screen.getByRole("button", { name: /Open/ });
    expect(openChip).toHaveTextContent("· 1");

    // Resolve it: PATCH must carry the status, then counts re-render.
    const checkbox = screen.getByRole("checkbox", {
      name: "Mark resolved: Ship the notes panel",
    });
    fireEvent.click(checkbox);

    const patch = await waitFor(() => {
      const found = calls.find((call) => call.method === "PATCH");
      expect(found).toBeTruthy();
      return found;
    });
    expect(patch.url).toBe("/v1/tasks/t1");
    expect(patch.body).toEqual({ status: "resolved" });

    expect(await screen.findByRole("button", { name: /Resolved/ })).toHaveTextContent("· 1");
    expect(screen.getByRole("button", { name: /Open/ })).toHaveTextContent("· 0");
    // No git endpoint was ever touched.
    expect(calls.every((call) => !call.url.includes("commits"))).toBe(true);
  });

  it("creates a task with a double-submit guard", async () => {
    const { calls } = mockFetch([
      {
        match: "/v1/tasks",
        body: { tasks: [TASK], counts: { open: 1, resolved: 0, dismissed: 0, superseded: 0, total: 1 } },
      },
      {
        match: "/v1/notes",
        body: { notes: [{ id: "n1", content: "Decided: keep it simple", tag: "decision", created_at: "2026-11-18T11:00:00Z" }], counts: { total: 1, decision: 1 } },
      },
      { match: "/v1/chat-import", body: { parsed: [], committed: false } },
    ]);
    setToken();
    render(<TasksTab />);

    await screen.findByText("Ship the notes panel");
    const input = screen.getByLabelText("New task");
    fireEvent.change(input, { target: { value: "Add the notes filter" } });
    const submit = screen.getByRole("button", { name: "Add" });
    fireEvent.click(submit);
    fireEvent.click(submit); // second click while in flight must be ignored

    await waitFor(() => {
      const posts = calls.filter((call) => call.url === "/v1/tasks" && call.method === "POST");
      expect(posts).toHaveLength(1);
    });

    // Notes panel rendered from /v1/notes with tag filters.
    expect(screen.getByText("Decided: keep it simple")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Decisions/ })).toHaveTextContent("· 1");
  });

  it("asks for confirmation before deleting a note", async () => {
    let notes = [{ id: "n9", content: "Temporary note", tag: "note", created_at: "2026-11-18T11:00:00Z" }];
    const { calls } = mockFetch([
      {
        match: "/v1/tasks",
        body: { tasks: [], counts: { open: 0, resolved: 0, dismissed: 0, superseded: 0, total: 0 } },
      },
      {
        match: "/v1/notes",
        body: () => ({
          notes: notes.map((note) => ({ ...note })),
          counts: { total: notes.length, note: notes.length },
        }),
      },
      { match: "/v1/chat-import", body: { parsed: [], committed: false } },
      {
        match: "/v1/notes/n9",
        method: "DELETE",
        body: () => {
          notes = [];
          return { id: "n9", deleted: true };
        },
      },
    ]);
    setToken();
    render(<TasksTab />);

    await screen.findByText("Temporary note");
    fireEvent.click(screen.getByRole("button", { name: /^Delete note/ }));
    // Permanent action → confirmation dialog appears first, nothing sent yet.
    expect(await screen.findByText("Delete this note permanently?")).toBeInTheDocument();
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
    await waitFor(() => {
      const deleted = calls.find((call) => call.method === "DELETE");
      expect(deleted).toBeTruthy();
      expect(deleted.url).toBe("/v1/notes/n9");
    });
    expect(await screen.findByText("No notes yet")).toBeInTheDocument();
  });
});
