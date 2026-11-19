import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../api.js";
import { Badge, Card, EmptyState, ErrorBanner, Spinner, formatDate } from "./ui.jsx";
import ChatBridge from "./ChatBridge.jsx";

const FILTERS = [
  { id: "", label: "All" },
  { id: "open", label: "Open" },
  { id: "resolved", label: "Resolved" },
  { id: "dismissed", label: "Dismissed" },
  { id: "superseded", label: "Superseded" },
];

const SOURCE_TONES = {
  manual: "border-sky-800 bg-sky-950/60 text-sky-300",
  chat: "border-violet-800 bg-violet-950/60 text-violet-300",
  mcp: "border-emerald-800 bg-emerald-950/60 text-emerald-300",
};

const STATUS_TONES = {
  resolved: "border-emerald-800 bg-emerald-950/60 text-emerald-300",
  dismissed: "border-zinc-700 bg-zinc-900 text-zinc-400",
  superseded: "border-amber-800 bg-amber-950/60 text-amber-300",
};

function TaskRow({ task, busy, onStatus, onDelete }) {
  const resolved = task.status === "resolved";
  const busyNote = busy === task.id ? "…" : "";

  return (
    <li className="flex items-start gap-3 rounded-xl border border-zinc-800 bg-zinc-900/40 px-4 py-3 transition hover:border-zinc-700">
      <input
        type="checkbox"
        checked={resolved}
        disabled={busy === task.id}
        onChange={() => onStatus(task, resolved ? "open" : "resolved")}
        title={resolved ? "Reopen" : "Mark resolved"}
        className="mt-0.5 h-4 w-4 shrink-0 rounded border-zinc-600 bg-zinc-950 accent-emerald-600 disabled:opacity-40"
      />
      <div className="min-w-0 flex-1">
        <p
          className={`text-sm leading-relaxed ${
            resolved ? "text-zinc-500 line-through" : "text-zinc-200"
          }`}
        >
          {task.content}
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-zinc-500">
          <Badge className={SOURCE_TONES[task.source] ?? SOURCE_TONES.manual}>
            {task.source ?? "manual"}
          </Badge>
          {task.status !== "open" && (
            <Badge className={STATUS_TONES[task.status] ?? STATUS_TONES.dismissed}>
              {task.status}
            </Badge>
          )}
          <span>{formatDate(task.created_at)}</span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        {task.status === "open" && (
          <button
            type="button"
            disabled={busy === task.id}
            onClick={() => onStatus(task, "dismissed")}
            className="rounded-md border border-zinc-700 px-2 py-1 text-[11px] text-zinc-400 transition hover:border-zinc-500 hover:text-zinc-200 disabled:opacity-40"
          >
            Dismiss
          </button>
        )}
        {task.status !== "open" && task.status !== "resolved" && (
          <button
            type="button"
            disabled={busy === task.id}
            onClick={() => onStatus(task, "open")}
            className="rounded-md border border-zinc-700 px-2 py-1 text-[11px] text-zinc-400 transition hover:border-zinc-500 hover:text-zinc-200 disabled:opacity-40"
          >
            Reopen
          </button>
        )}
        <button
          type="button"
          disabled={busy === task.id}
          onClick={() => onDelete(task)}
          aria-label="Delete task"
          className="rounded-md border border-zinc-700 px-2 py-1 text-[11px] text-zinc-500 transition hover:border-red-800 hover:text-red-400 disabled:opacity-40"
        >
          Delete{busyNote}
        </button>
      </div>
    </li>
  );
}

// Checklist plus the Chat Bridge import box (fixed server-side parsing rules).
export default function TasksTab() {
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

  const load = useCallback(async (status) => {
    setLoading(true);
    setError(null);
    try {
      const query = status ? `?status=${encodeURIComponent(status)}&limit=200` : "?limit=200";
      const result = await apiRequest(`/v1/tasks${query}`);
      setTasks(result.tasks ?? []);
      setCounts(result.counts ?? {});
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(filter);
  }, [load, filter]);

  const addTask = async (event) => {
    event.preventDefault();
    const clean = draft.trim();
    if (!clean || adding) return;
    setAdding(true);
    setAddError(null);
    try {
      await apiRequest("/v1/tasks", { method: "POST", body: { content: clean } });
      setDraft("");
      await load(filter);
    } catch (err) {
      setAddError(err.message);
    } finally {
      setAdding(false);
    }
  };

  const changeStatus = async (task, status) => {
    setBusy(task.id);
    setActionError(null);
    try {
      await apiRequest(`/v1/tasks/${encodeURIComponent(task.id)}`, {
        method: "PATCH",
        body: { status },
      });
      await load(filter);
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(null);
    }
  };

  const deleteTask = async (task) => {
    setBusy(task.id);
    setActionError(null);
    try {
      await apiRequest(`/v1/tasks/${encodeURIComponent(task.id)}`, { method: "DELETE" });
      await load(filter);
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      {/* Checklist */}
      <div className="space-y-4 lg:col-span-2">
        <div className="flex flex-wrap items-center gap-2">
          {FILTERS.map((option) => {
            const active = filter === option.id;
            const count =
              option.id === ""
                ? counts.total
                : counts[option.id];
            return (
              <button
                key={option.id || "all"}
                type="button"
                onClick={() => setFilter(option.id)}
                className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
                  active
                    ? "border-sky-600 bg-sky-600/15 text-sky-300"
                    : "border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200"
                }`}
              >
                {option.label}
                {typeof count === "number" && <span className="ml-1.5 tabular-nums opacity-70">{count}</span>}
              </button>
            );
          })}
        </div>

        <form onSubmit={addTask} className="flex gap-2">
          <input
            type="text"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={600}
            placeholder="Add a task…"
            className="min-w-0 flex-1 rounded-xl border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-200 outline-none transition focus:border-sky-600"
          />
          <button
            type="submit"
            disabled={!draft.trim() || adding}
            className="shrink-0 rounded-xl bg-sky-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40"
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
            hint="Add one above, or paste a chat transcript into the Chat Bridge on the right."
          />
        ) : (
          <ul className="space-y-2">
            {tasks.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                busy={busy}
                onStatus={changeStatus}
                onDelete={deleteTask}
              />
            ))}
          </ul>
        )}
      </div>

      {/* Chat Bridge */}
      <div>
        <Card title="Chat Bridge">
          <ChatBridge onImported={() => load(filter)} />
        </Card>
      </div>
    </div>
  );
}
