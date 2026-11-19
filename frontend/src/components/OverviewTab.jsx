import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../api.js";
import {
  Badge,
  Card,
  EmptyState,
  ErrorBanner,
  Spinner,
  formatDate,
  formatNumber,
} from "./ui.jsx";

const TASK_STATUSES = [
  { id: "open", label: "Open", tone: "border-sky-800 bg-sky-950/60 text-sky-300" },
  { id: "resolved", label: "Resolved", tone: "border-emerald-800 bg-emerald-950/60 text-emerald-300" },
  { id: "dismissed", label: "Dismissed", tone: "border-zinc-700 bg-zinc-900 text-zinc-400" },
  { id: "superseded", label: "Superseded", tone: "border-amber-800 bg-amber-950/60 text-amber-300" },
];

function CommitRow({ commit }) {
  const [expanded, setExpanded] = useState(false);
  const files = commit.files ?? [];

  return (
    <li className="relative pl-8">
      <span
        aria-hidden="true"
        className="absolute left-[7px] top-6 h-3 w-3 rounded-full border-2 border-sky-500 bg-zinc-950"
      />
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 transition hover:border-zinc-700">
        <div className="flex flex-wrap items-center gap-2">
          <code className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-xs text-sky-400">
            {commit.short_sha}
          </code>
          <span className="min-w-0 flex-1 text-sm font-medium text-zinc-100">{commit.subject}</span>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-500">
          <span>{commit.author}</span>
          <span>{formatDate(commit.committed_at)}</span>
          <span className="font-mono text-emerald-400">+{formatNumber(commit.insertions)}</span>
          <span className="font-mono text-red-400">−{formatNumber(commit.deletions)}</span>
          {files.length > 0 && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              className="rounded-md border border-zinc-700 px-2 py-0.5 text-zinc-400 transition hover:border-zinc-500 hover:text-zinc-200"
            >
              {expanded ? "Hide" : "Show"} {files.length} file{files.length === 1 ? "" : "s"}
            </button>
          )}
        </div>
        {expanded && files.length > 0 && (
          <ul className="mt-3 space-y-1 border-t border-zinc-800 pt-3">
            {files.map((file) => (
              <li key={file.path} className="flex items-baseline justify-between gap-3 text-xs">
                <span className="min-w-0 truncate font-mono text-zinc-400" title={file.path}>
                  {file.path}
                </span>
                <span className="shrink-0 font-mono text-zinc-600">{file.lines}L</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

// North Star editor, momentum strip, git timeline and task counts in one tab.
export default function OverviewTab({ onNavigate }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await apiRequest("/v1/dashboard"));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const saveIntent = async (event) => {
    event.preventDefault();
    const clean = draft.trim();
    if (!clean || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await apiRequest("/v1/intent", {
        method: "PUT",
        body: { north_star: clean },
      });
      setData((current) =>
        current
          ? {
              ...current,
              north_star: updated.north_star,
              north_star_updated_at: updated.updated_at,
            }
          : current
      );
      setEditing(false);
    } catch (err) {
      setSaveError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const refreshCommits = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshError(null);
    try {
      const result = await apiRequest("/v1/commits?limit=15&refresh=1");
      setData((current) =>
        current
          ? { ...current, commits: result.commits ?? [], momentum: result.momentum ?? current.momentum }
          : current
      );
    } catch (err) {
      setRefreshError(err.message);
    } finally {
      setRefreshing(false);
    }
  };

  if (loading && !data) {
    return <Spinner label="Loading dashboard…" />;
  }

  if (error && !data) {
    return <ErrorBanner message={error} onRetry={load} />;
  }

  if (!data) {
    return <EmptyState title="No dashboard data yet" hint="Start the API with `cb serve` and reload." />;
  }

  const momentum = data.momentum ?? {};
  const counts = data.tasks ?? {};
  const commits = data.commits ?? [];

  const momentumStats = [
    { label: "Commits", value: formatNumber(momentum.commits ?? 0) },
    { label: "Authors", value: (momentum.authors ?? []).length },
    { label: "Insertions", value: `+${formatNumber(momentum.insertions ?? 0)}`, tone: "text-emerald-400" },
    { label: "Deletions", value: `−${formatNumber(momentum.deletions ?? 0)}`, tone: "text-red-400" },
    { label: "Files touched", value: formatNumber(momentum.files_touched ?? 0) },
    { label: "Window", value: `${momentum.window ?? commits.length} commits` },
  ];

  return (
    <div className="space-y-6">
      <ErrorBanner message={error} onRetry={load} />

      {/* North Star */}
      <Card
        title="North Star"
        actions={
          !editing && (
            <button
              type="button"
              onClick={() => {
                setDraft(data.north_star ?? "");
                setSaveError(null);
                setEditing(true);
              }}
              className="rounded-lg border border-zinc-700 px-3 py-1 text-xs font-medium text-zinc-300 transition hover:border-zinc-500 hover:text-zinc-100"
            >
              Edit
            </button>
          )
        }
      >
        {editing ? (
          <form onSubmit={saveIntent} className="space-y-3">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              rows={4}
              maxLength={4000}
              autoFocus
              placeholder="One sentence describing what this project is trying to achieve…"
              className="w-full resize-y rounded-xl border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm leading-relaxed text-zinc-200 outline-none transition focus:border-sky-600"
            />
            <ErrorBanner message={saveError} />
            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={!draft.trim() || saving}
                className="rounded-lg bg-sky-600 px-4 py-1.5 text-sm font-medium text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {saving ? "Saving…" : "Save"}
              </button>
              <button
                type="button"
                onClick={() => setEditing(false)}
                className="rounded-lg border border-zinc-700 px-4 py-1.5 text-sm text-zinc-300 transition hover:border-zinc-500"
              >
                Cancel
              </button>
            </div>
          </form>
        ) : data.north_star ? (
          <div className="space-y-2">
            <p className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-200">
              {data.north_star}
            </p>
            <p className="text-xs text-zinc-500">
              Updated {formatDate(data.north_star_updated_at)}
            </p>
          </div>
        ) : (
          <EmptyState
            title="No North Star yet"
            hint="Describe, in one sentence, what this project is trying to achieve. It persists in SQLite across sessions."
            action={
              <button
                type="button"
                onClick={() => {
                  setDraft("");
                  setEditing(true);
                }}
                className="rounded-lg bg-sky-600 px-4 py-1.5 text-sm font-medium text-white transition hover:bg-sky-500"
              >
                Write it
              </button>
            }
          />
        )}
      </Card>

      {/* Momentum strip */}
      <Card title="Momentum" actions={<span className="text-xs text-zinc-500">{data.branch}</span>}>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {momentumStats.map((stat) => (
            <div
              key={stat.label}
              className="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-3 text-center"
            >
              <div className={`text-lg font-semibold tabular-nums ${stat.tone ?? "text-zinc-100"}`}>
                {stat.value}
              </div>
              <div className="mt-0.5 text-[11px] uppercase tracking-wide text-zinc-500">
                {stat.label}
              </div>
            </div>
          ))}
        </div>
        {(momentum.latest_at || momentum.oldest_at) && (
          <p className="mt-3 text-xs text-zinc-600">
            {formatDate(momentum.oldest_at)} → {formatDate(momentum.latest_at)}
          </p>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Git timeline */}
        <div className="lg:col-span-2">
          <Card
            title="Git timeline"
            actions={
              <div className="flex items-center gap-3">
                <ErrorBanner message={refreshError} />
                <button
                  type="button"
                  onClick={refreshCommits}
                  disabled={refreshing}
                  className="rounded-lg border border-zinc-700 px-3 py-1 text-xs font-medium text-zinc-300 transition hover:border-zinc-500 hover:text-zinc-100 disabled:opacity-50"
                >
                  {refreshing ? "Refreshing…" : "Refresh from git"}
                </button>
              </div>
            }
          >
            {commits.length === 0 ? (
              <EmptyState
                title="No commits in the window yet"
                hint="Commit once, then use “Refresh from git” to pull the latest history."
              />
            ) : (
              <ol className="relative space-y-4 before:absolute before:bottom-2 before:left-[12px] before:top-2 before:w-px before:bg-zinc-800">
                {commits.map((commit) => (
                  <CommitRow key={commit.sha} commit={commit} />
                ))}
              </ol>
            )}
          </Card>
        </div>

        {/* Task counts */}
        <div>
          <Card
            title="Tasks"
            actions={
              <button
                type="button"
                onClick={() => onNavigate("tasks")}
                className="text-xs font-medium text-sky-400 transition hover:text-sky-300"
              >
                Open checklist →
              </button>
            }
          >
            <div className="space-y-2">
              {TASK_STATUSES.map((status) => (
                <button
                  key={status.id}
                  type="button"
                  onClick={() => onNavigate("tasks")}
                  className="flex w-full items-center justify-between rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-left transition hover:border-zinc-600"
                >
                  <Badge className={status.tone}>{status.label}</Badge>
                  <span className="text-sm font-semibold tabular-nums text-zinc-200">
                    {counts[status.id] ?? 0}
                  </span>
                </button>
              ))}
              <div className="flex items-center justify-between border-t border-zinc-800 pt-2 text-xs text-zinc-500">
                <span>Total</span>
                <span className="font-semibold tabular-nums text-zinc-300">{counts.total ?? 0}</span>
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
