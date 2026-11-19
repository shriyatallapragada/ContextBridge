import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../api.js";
import {
  Badge,
  Card,
  EmptyState,
  ErrorBanner,
  InfoDot,
  Spinner,
  formatDate,
  formatNumber,
} from "./ui.jsx";

const TASK_STATUSES = [
  { id: "open", label: "Open", tone: "primary", symbol: "○" },
  { id: "resolved", label: "Resolved", tone: "ok", symbol: "✓" },
  { id: "dismissed", label: "Dismissed", tone: "neutral", symbol: "–" },
  {
    id: "superseded",
    label: "Superseded",
    tone: "warn",
    symbol: "↻",
    explain:
      "Superseded means a newer task or decision replaced this one, so it was neither completed nor thrown away.",
  },
];

function CommitRow({ commit }) {
  const [expanded, setExpanded] = useState(false);
  const files = commit.files ?? [];

  return (
    <li className="relative pl-8">
      <span
        aria-hidden="true"
        className="absolute left-[7px] top-6 h-3 w-3 rounded-full border-2 border-accent bg-surface"
      />
      <div className="rounded-card border border-line bg-surface px-4 py-3 transition hover:border-accent">
        <div className="flex flex-wrap items-center gap-2">
          <code className="rounded-control bg-surface-alt px-2 py-0.5 font-mono text-sm text-primary">
            {commit.short_sha}
          </code>
          <span className="min-w-0 flex-1 text-base font-semibold text-ink">{commit.subject}</span>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-muted">
          <span>{commit.author}</span>
          <span>{formatDate(commit.committed_at)}</span>
          <span className="font-mono text-ok">+{formatNumber(commit.insertions)}</span>
          <span className="font-mono text-danger">−{formatNumber(commit.deletions)}</span>
          {files.length > 0 && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              className="btn btn-secondary"
              aria-expanded={expanded}
            >
              {expanded ? "Hide" : "Show"} {files.length} file{files.length === 1 ? "" : "s"}
            </button>
          )}
        </div>
        {expanded && files.length > 0 && (
          <ul className="mt-3 space-y-1 border-t border-line pt-3">
            {files.map((file) => (
              <li key={file.path} className="flex items-baseline justify-between gap-3 text-sm">
                <span className="min-w-0 truncate font-mono text-ink-muted" title={file.path}>
                  {file.path}
                </span>
                <span className="shrink-0 font-mono text-ink-muted">{file.lines}L</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

// Project goal editor, momentum strip, git timeline and task counts.
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
  const [seq, setSeq] = useState(0);

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
    if (!clean || saving) return; // saving also guards against double submits
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await apiRequest("/v1/intent", {
        method: "PUT",
        body: { north_star: clean },
      });
      // Optimistic-but-safe: only commit the new goal after the server confirms it.
      setData((current) =>
        current
          ? {
              ...current,
              north_star: updated.north_star ?? clean,
              north_star_updated_at: updated.updated_at ?? current.north_star_updated_at,
            }
          : current
      );
      setEditing(false);
    } catch (err) {
      // The draft stays in the editor so nothing typed is lost.
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
    { label: "Added", value: `+${formatNumber(momentum.insertions ?? 0)}`, tone: "text-ok" },
    { label: "Removed", value: `−${formatNumber(momentum.deletions ?? 0)}`, tone: "text-danger" },
    { label: "Files touched", value: formatNumber(momentum.files_touched ?? 0) },
    { label: "Window", value: `${momentum.window ?? commits.length} commits` },
  ];

  return (
    <div className="space-y-6">
      <ErrorBanner message={error} onRetry={load} />

      <div className="flex flex-wrap gap-2" aria-label="Quick actions">
        <button type="button" className="btn btn-primary" onClick={() => onNavigate("handoff")}>
          Prepare a handoff →
        </button>
        <button type="button" className="btn btn-secondary" onClick={() => onNavigate("guide")}>
          Open project guide
        </button>
        <button type="button" className="btn btn-secondary" onClick={() => onNavigate("changes")}>
          Changes and tests
        </button>
      </div>

      {/* Project goal (formerly “North Star”) */}
      <Card
        title="Project goal"
        subtitle="The one-sentence North Star for this project — shown to every agent that receives a handoff."
        actions={
          !editing && (
            <button
              type="button"
              onClick={() => {
                setDraft(data.north_star ?? "");
                setSaveError(null);
                setEditing(true);
              }}
              className="btn btn-secondary"
            >
              Edit goal
            </button>
          )
        }
      >
        {editing ? (
          <form onSubmit={saveIntent} className="space-y-3">
            <label className="label" htmlFor="cb-goal">
              Project goal
            </label>
            <textarea
              id="cb-goal"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              rows={4}
              maxLength={4000}
              autoFocus
              placeholder="One sentence describing what this project is trying to achieve…"
              className="field"
            />
            <ErrorBanner message={saveError} />
            <div className="flex flex-wrap items-center gap-2">
              <button type="submit" disabled={!draft.trim() || saving} className="btn btn-primary">
                {saving ? "Saving…" : "Save goal"}
              </button>
              <button
                type="button"
                onClick={() => setEditing(false)}
                className="btn btn-secondary"
                disabled={saving}
              >
                Cancel
              </button>
              {saveError && <span className="hint">Your draft is still here — retry when ready.</span>}
            </div>
          </form>
        ) : (data.north_star || data.project_goal) ? (
          <div className="space-y-2">
            <p className="whitespace-pre-wrap text-lg leading-relaxed text-ink">{data.north_star || data.project_goal}</p>
            <p className="hint">{data.north_star ? `Last updated ${formatDate(data.north_star_updated_at)}` : "From a Goal note or project description. Set an explicit goal to override it."}</p>
          </div>
        ) : (
          <EmptyState
            title="No project goal yet"
            hint="Choose Set project goal to describe what this project is trying to achieve."
            action={
              <button
                type="button"
                onClick={() => {
                  setDraft("");
                  setEditing(true);
                }}
                className="btn btn-primary"
              >
                Write the goal
              </button>
            }
          />
        )}
      </Card>

      {/* Momentum strip */}
      <Card title="Momentum" actions={<Badge tone="neutral" symbol="⎇">{data.branch}</Badge>}>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {momentumStats.map((stat) => (
            <div
              key={stat.label}
              className="rounded-card border border-line bg-surface-alt px-3 py-3 text-center"
            >
              <div className={`text-xl font-bold tabular-nums ${stat.tone ?? "text-ink"}`}>
                {stat.value}
              </div>
              <div className="mt-0.5 text-sm text-ink-muted">{stat.label}</div>
            </div>
          ))}
        </div>
        {(momentum.latest_at || momentum.oldest_at) && (
          <p className="hint mt-3">
            {formatDate(momentum.oldest_at)} → {formatDate(momentum.latest_at)}
          </p>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Git timeline */}
        <div className="lg:col-span-2">
          <Card
            title="Git timeline"
            subtitle="Recent commits — expand a row to see its files."
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={refreshCommits}
                  disabled={refreshing}
                  className="btn btn-secondary"
                >
                  {refreshing ? "Refreshing…" : "Refresh from git"}
                </button>
              </div>
            }
          >
            <ErrorBanner message={refreshError} />
            {commits.length === 0 ? (
              <EmptyState
                title="No commits in the window yet"
                hint="Commit once, then use “Refresh from git” to pull the latest history."
              />
            ) : (
              <ol className="relative mt-3 space-y-4 before:absolute before:bottom-2 before:left-[12px] before:top-2 before:w-px before:bg-line">
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
                className="btn btn-secondary"
              >
                Open checklist →
              </button>
            }
          >
            {!counts.total && <p className="hint mb-3">No tasks yet. Open the checklist to add your first one.</p>}
            <div className="space-y-2">
              {TASK_STATUSES.map((status) => (
                <button
                  key={status.id}
                  type="button"
                  onClick={() => onNavigate("tasks")}
                  className="flex w-full items-center justify-between gap-2 rounded-control border border-line bg-surface-alt px-3 py-2.5 text-left transition hover:border-accent"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <Badge tone={status.tone} symbol={status.symbol}>
                      {status.label}
                    </Badge>
                    {status.explain && <InfoDot label={status.explain} />}
                  </span>
                  <span className="text-lg font-bold tabular-nums text-ink">
                    {counts[status.id] ?? 0}
                  </span>
                </button>
              ))}
              <div className="flex items-center justify-between border-t border-line pt-2 text-base text-ink-muted">
                <span>Total</span>
                <span className="font-bold tabular-nums text-ink">{counts.total ?? 0}</span>
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
