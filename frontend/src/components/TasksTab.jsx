import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "../api.js";
import { notifyDashboardStale } from "../events.js";
import { Badge, Card, ConfirmDialog, EmptyState, ErrorBanner, InfoDot, Spinner, formatDate } from "./ui.jsx";
import ChatImport from "./ChatImport.jsx";

const FILTERS = [
  { id: "", label: "All" },
  { id: "open", label: "Open", symbol: "○" },
  { id: "resolved", label: "Resolved", symbol: "✓" },
  { id: "dismissed", label: "Dismissed", symbol: "–" },
  { id: "superseded", label: "Superseded", symbol: "↻" },
];

const SUPERSEDED_HELP =
  "Superseded means a newer task or decision replaced this one. It was neither completed nor thrown away.";

const SOURCE_TONES = { manual: "neutral", chat: "info", mcp: "ok" };
const STATUS_TONES = { resolved: "ok", dismissed: "neutral", superseded: "warn" };
const STATUS_SYMBOLS = { resolved: "✓", dismissed: "–", superseded: "↻" };

const NOTE_TAGS = [
  { id: "", label: "All notes" },
  { id: "note", label: "Notes" },
  { id: "decision", label: "Decisions" },
  { id: "bug", label: "Bugs" },
  { id: "blocker", label: "Blockers" },
  { id: "architecture", label: "Architecture" },
];

function TaskRow({ task, busy, onStatus, onDelete }) {
  const resolved = task.status === "resolved";
  const busyHere = busy === task.id;

  return (
    <li className="flex flex-wrap items-start gap-3 rounded-card border border-line bg-surface px-4 py-3 transition hover:border-accent sm:flex-nowrap">
      <input
        type="checkbox"
        checked={resolved}
        disabled={busyHere}
        onChange={() => onStatus(task, resolved ? "open" : "resolved")}
        aria-label={resolved ? `Reopen task: ${task.content}` : `Mark resolved: ${task.content}`}
        title={resolved ? "Reopen" : "Mark resolved"}
        className="mt-1 h-5 w-5 shrink-0 rounded-control border-line accent-[var(--primary)] disabled:opacity-40"
      />
      <div className="min-w-0 flex-1">
        <p className={`text-base leading-relaxed ${resolved ? "text-ink-muted line-through" : "text-ink"}`}>
          {task.content}
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-ink-muted">
          <Badge tone={SOURCE_TONES[task.source] ?? "neutral"}>{task.source ?? "manual"}</Badge>
          {task.status !== "open" && (
            <span title={task.status === "superseded" ? SUPERSEDED_HELP : undefined}>
              <Badge tone={STATUS_TONES[task.status] ?? "neutral"} symbol={STATUS_SYMBOLS[task.status]}>
                {task.status}
              </Badge>
            </span>
          )}
          <span>{formatDate(task.created_at)}</span>
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-1.5">
        {task.status === "open" && (
          <button
            type="button"
            disabled={busyHere}
            onClick={() => onStatus(task, "dismissed")}
            className="btn btn-secondary"
          >
            Dismiss
          </button>
        )}
        {task.status !== "open" && task.status !== "resolved" && (
          <button
            type="button"
            disabled={busyHere}
            onClick={() => onStatus(task, "open")}
            className="btn btn-secondary"
          >
            Reopen
          </button>
        )}
        <button
          type="button"
          disabled={busyHere}
          onClick={() => onDelete(task)}
          aria-label={`Delete task: ${task.content}`}
          className="btn btn-danger"
        >
          {busyHere ? "Working…" : "Delete"}
        </button>
      </div>
    </li>
  );
}

function NotesPanel() {
  const [tag, setTag] = useState("");
  const [notes, setNotes] = useState([]);
  const [counts, setCounts] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [text, setText] = useState("");
  const [newTag, setNewTag] = useState("note");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState(null);

  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState(null);
  const seqRef = useRef(0);

  // Newest request wins: a stale response can never overwrite a newer tag filter.
  const load = useCallback(async () => {
    const mySeq = (seqRef.current += 1);
    setLoading(true);
    setError(null);
    try {
      const query = `?limit=200${tag ? `&tag=${encodeURIComponent(tag)}` : ""}`;
      const result = await apiRequest(`/v1/notes${query}`);
      if (mySeq !== seqRef.current) return;
      setNotes(result?.notes ?? []);
      setCounts(result?.counts ?? {});
    } catch (err) {
      if (mySeq === seqRef.current) setError(err.message);
    } finally {
      if (mySeq === seqRef.current) setLoading(false);
    }
  }, [tag]);

  useEffect(() => {
    load();
  }, [load]);

  const addNote = async (event) => {
    event.preventDefault();
    const clean = text.trim();
    if (!clean || adding) return;
    setAdding(true);
    setAddError(null);
    try {
      await apiRequest("/v1/notes", { method: "POST", body: { text: clean, tag: newTag } });
      setText("");
      notifyDashboardStale();
      await load();
    } catch (err) {
      setAddError(err.message);
    } finally {
      setAdding(false);
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete || deleting) return;
    setDeleting(true);
    setActionError(null);
    try {
      await apiRequest(`/v1/notes/${encodeURIComponent(pendingDelete.id)}`, { method: "DELETE" });
      setPendingDelete(null);
      notifyDashboardStale();
      await load();
    } catch (err) {
      setActionError(err.message);
      setPendingDelete(null);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Card
      title="Notes and decisions"
      subtitle="Decisions, bugs, blockers and architecture notes stored in the local project database."
    >
      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter notes by tag">
        {NOTE_TAGS.map((option) => {
          const count = option.id === "" ? counts.total : counts[option.id];
          return (
            <button
              key={option.id || "all"}
              type="button"
              className="chip"
              aria-pressed={tag === option.id}
              onClick={() => setTag(option.id)}
            >
              {option.label}
              {typeof count === "number" && <span className="tabular-nums">· {count}</span>}
            </button>
          );
        })}
      </div>

      <form onSubmit={addNote} className="mt-4 space-y-2">
        <label className="label" htmlFor="cb-note-text">
          New note
        </label>
        <textarea
          id="cb-note-text"
          className="field"
          rows={3}
          value={text}
          maxLength={2000}
          onChange={(event) => setText(event.target.value)}
          placeholder="Decided to keep the explainer cache keyed by content hash…"
        />
        <div className="flex flex-wrap items-center gap-2">
          <label className="label mb-0" htmlFor="cb-note-tag">
            Tag
          </label>
          <select
            id="cb-note-tag"
            className="field w-auto"
            value={newTag}
            onChange={(event) => setNewTag(event.target.value)}
          >
            {NOTE_TAGS.filter((option) => option.id).map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
          <button type="submit" className="btn btn-primary ml-auto" disabled={!text.trim() || adding}>
            {adding ? "Adding…" : "Add note"}
          </button>
        </div>
      </form>

      <ErrorBanner message={addError} />
      <ErrorBanner message={actionError} />

      <div className="mt-4 space-y-2">
        {loading ? (
          <Spinner label="Loading notes…" />
        ) : error ? (
          <ErrorBanner message={error} onRetry={load} />
        ) : notes.length === 0 ? (
          <EmptyState
            title={tag ? `No ${tag} notes yet` : "No notes yet"}
            hint="Capture a decision, bug, blocker or architecture fact above — handoffs read these."
          />
        ) : (
          <ul className="space-y-2">
            {notes.map((note) => (
              <li key={note.id} className="rounded-card border border-line bg-surface-alt px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="accent" symbol="#">
                    {note.tag ?? "note"}
                  </Badge>
                  <span className="hint">{formatDate(note.created_at)}</span>
                  <button
                    type="button"
                    className="btn btn-danger ml-auto"
                    onClick={() => setPendingDelete(note)}
                    aria-label={`Delete note: ${note.content.slice(0, 60)}`}
                  >
                    Delete
                  </button>
                </div>
                <p className="mt-1.5 whitespace-pre-wrap text-base leading-relaxed text-ink">
                  {note.content}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this note permanently?"
          message={
            <>
              This removes the note from the local database. There is no undo and no backup copy:{" "}
              <strong>{pendingDelete.content.slice(0, 160)}</strong>
              {pendingDelete.content.length > 160 ? "…" : ""}
            </>
          }
          confirmLabel="Delete permanently"
          busy={deleting}
          onCancel={() => setPendingDelete(null)}
          onConfirm={confirmDelete}
        />
      )}
    </Card>
  );
}

// Task checklist plus notes/decisions and the chat-import box.
export default function TasksTab({ onNavigate }) {
  const [filter, setFilter] = useState("");
  const [tasks, setTasks] = useState([]);
  const [counts, setCounts] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState(null);

  const [busy, setBusy] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);
  const seqRef = useRef(0);

  // Newest request wins: switching filters fast cannot show an old list.
  const load = useCallback(async (status) => {
    const mySeq = (seqRef.current += 1);
    setLoading(true);
    setError(null);
    try {
      const query = status ? `?status=${encodeURIComponent(status)}&limit=200` : "?limit=200";
      const result = await apiRequest(`/v1/tasks${query}`);
      if (mySeq !== seqRef.current) return;
      setTasks(result?.tasks ?? []);
      setCounts(result?.counts ?? {});
    } catch (err) {
      if (mySeq === seqRef.current) setError(err.message);
    } finally {
      if (mySeq === seqRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(filter);
  }, [load, filter]);

  const afterMutation = async () => {
    notifyDashboardStale();
    await load(filter);
  };

  const addTask = async (event) => {
    event.preventDefault();
    const clean = draft.trim();
    if (!clean || adding) return;
    setAdding(true);
    setAddError(null);
    try {
      await apiRequest("/v1/tasks", { method: "POST", body: { content: clean } });
      setDraft("");
      await afterMutation();
    } catch (err) {
      setAddError(err.message);
    } finally {
      setAdding(false);
    }
  };

  const changeStatus = async (task, status) => {
    if (busy) return;
    setBusy(task.id);
    setActionError(null);
    try {
      await apiRequest(`/v1/tasks/${encodeURIComponent(task.id)}`, {
        method: "PATCH",
        body: { status },
      });
      await afterMutation();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(null);
    }
  };

  const confirmDelete = async () => {
    const task = pendingDelete;
    if (!task || busy) return;
    setBusy(task.id);
    setActionError(null);
    setPendingDelete(null);
    try {
      await apiRequest(`/v1/tasks/${encodeURIComponent(task.id)}`, { method: "DELETE" });
      await afterMutation();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Checklist */}
        <div className="space-y-4 lg:col-span-2">
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter tasks">
            {FILTERS.map((option) => {
              const active = filter === option.id;
              const count = option.id === "" ? counts.total : counts[option.id];
              return (
                <span key={option.id || "all"} className="inline-flex items-center gap-1">
                  <button
                    type="button"
                    className="chip"
                    aria-pressed={active}
                    onClick={() => setFilter(option.id)}
                  >
                    {option.symbol && (
                      <span aria-hidden="true" className="font-bold">
                        {option.symbol}
                      </span>
                    )}
                    {option.label}
                    {typeof count === "number" && <span className="tabular-nums">· {count}</span>}
                  </button>
                  {option.id === "superseded" && <InfoDot label={SUPERSEDED_HELP} />}
                </span>
              );
            })}
          </div>
          <p className="hint">
            <strong>Superseded</strong> means a newer task replaced this one — it was neither
            completed nor discarded.
          </p>

          <form onSubmit={addTask} className="flex gap-2">
            <input
              type="text"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              maxLength={600}
              placeholder="Add a task…"
              aria-label="New task"
              className="field min-w-0 flex-1"
            />
            <button
              type="submit"
              disabled={!draft.trim() || adding}
              className="btn btn-primary shrink-0"
            >
              {adding ? "Adding…" : "Add"}
            </button>
          </form>

          <ErrorBanner message={addError} />
          <ErrorBanner message={actionError} />

          {loading ? (
            <Spinner label="Loading tasks…" />
          ) : error ? (
            <ErrorBanner message={error} onRetry={() => load(filter)} />
          ) : tasks.length === 0 ? (
            <EmptyState
              title={filter ? `No ${filter} tasks` : "No tasks yet"}
              hint="Add one above, or paste a chat transcript into “Import tasks from text” on the right."
            />
          ) : (
            <ul className="space-y-2">
              {tasks.map((task) => (
                <TaskRow
                  key={task.id}
                  task={task}
                  busy={busy}
                  onStatus={changeStatus}
                  onDelete={(item) => setPendingDelete(item)}
                />
              ))}
            </ul>
          )}
        </div>

        {/* Notes + import */}
        <div className="space-y-6">
          <NotesPanel />

          <Card
            title="Import tasks from text"
            actions={
              <button type="button" className="btn btn-secondary" onClick={() => onNavigate?.("chat")}>
                Chat bridge →
              </button>
            }
          >
            <ChatImport onSaved={() => load(filter)} />
          </Card>
        </div>
      </div>

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this task permanently?"
          message={
            <>
              The task is removed from the local list — no undo:{" "}
              <strong>{pendingDelete.content}</strong>
            </>
          }
          confirmLabel="Delete task"
          busy={Boolean(busy)}
          onCancel={() => setPendingDelete(null)}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  );
}
