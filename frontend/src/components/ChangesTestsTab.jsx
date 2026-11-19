import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "../api.js";
import { Badge, Card, EmptyState, ErrorBanner, Spinner, formatAge, formatDate, formatNumber } from "./ui.jsx";

const POLL_MS = 500;

const STATUS_LABELS = {
  A: { label: "added", tone: "ok", symbol: "+" },
  M: { label: "modified", tone: "warn", symbol: "~" },
  D: { label: "deleted", tone: "danger", symbol: "−" },
  R: { label: "renamed", tone: "info", symbol: "→" },
  C: { label: "copied", tone: "info", symbol: "⧉" },
  U: { label: "unmerged", tone: "danger", symbol: "!" },
  "?": { label: "untracked", tone: "neutral", symbol: "?" },
  "!": { label: "ignored", tone: "neutral", symbol: "·" },
};

function fileTone(status) {
  const key = String(status ?? "").slice(0, 1);
  return STATUS_LABELS[key] ?? { label: status ?? "changed", tone: "neutral", symbol: "•" };
}

function ChangeRow({ file }) {
  const tone = fileTone(file.status);
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-1 py-2 last:border-b-0">
      <Badge tone={tone.tone} symbol={tone.symbol}>
        {tone.label}
      </Badge>
      <span className="min-w-0 flex-1 break-all font-mono text-sm text-ink" title={file.path}>
        {file.path}
      </span>
      <span className="shrink-0 font-mono text-sm">
        <span className="text-ok">+{formatNumber(file.insertions ?? 0)}</span>{" "}
        <span className="text-danger">−{formatNumber(file.deletions ?? 0)}</span>
      </span>
    </li>
  );
}

function commandOutcome(run) {
  if ((typeof run.exit_code === "number" && run.exit_code !== 0) || run.failed > 0) {
    return { label: "Failed", tone: "danger", symbol: "✗" };
  }
  if (run.exit_code === 0) return { label: "Passed", tone: "ok", symbol: "✓" };
  return { label: "Status unavailable", tone: "neutral", symbol: "?" };
}

function countLabels(run) {
  const kinds = ["passed", "failed", "skipped"];
  if (run.recognized === false || kinds.every((kind) => typeof run[kind] !== "number")) {
    return ["Test counts unavailable"];
  }
  return kinds.map((kind) =>
    typeof run[kind] === "number" ? `${run[kind]} ${kind}` : `${kind} count unavailable`
  );
}

function TestReport({ status, history }) {
  const report = status?.report ?? null;
  const running = Boolean(status?.running);
  const outcome = report ? commandOutcome(report) : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {running ? (
          <Badge tone="info" symbol="▶">
            Running…
          </Badge>
        ) : report ? (
          <>
            <Badge tone={outcome.tone} symbol={outcome.symbol}>
              {outcome.label}
            </Badge>
            {countLabels(report).map((label) => (
              <Badge key={label} tone="neutral">{label}</Badge>
            ))}
            {report.runner && <Badge tone="neutral">{report.runner}</Badge>}
            {typeof report.exit_code === "number" && (
              <Badge tone={report.exit_code === 0 ? "ok" : "danger"} symbol={report.exit_code === 0 ? "✓" : "✗"}>
                exit {report.exit_code}
              </Badge>
            )}
          </>
        ) : null}

        {history && typeof history.last_run_age_seconds === "number" && (
          <Badge tone={history.stale ? "warn" : "neutral"} symbol={history.stale ? "⚠" : "⏱"}>
            Last run {formatAge(history.last_run_age_seconds)}
            {history.stale ? " · stale" : ""}
          </Badge>
        )}
        {history?.stale && (
          <span className="hint">
            Stale: the recorded run predates the newest changes, so the result may not describe the
            current code.
          </span>
        )}
      </div>

      {report && report.recognized === false && (
        <p className="hint rounded-card border border-warn bg-warn-bg px-3 py-2 text-warn">
          <span aria-hidden="true" className="mr-1 font-bold">
            ⚠
          </span>
          The command was not recognised as a known test runner — the raw output below is shown as
          it came back.
        </p>
      )}

      {report && Array.isArray(report.failures) && report.failures.length > 0 && (
        <div className="rounded-card border border-danger bg-danger-bg px-4 py-3">
          <h3 className="text-base font-bold text-danger">
            <span aria-hidden="true" className="mr-1">
              ✗
            </span>
            Failure details
          </h3>
          <ul className="mt-2 list-disc space-y-1.5 pl-5 text-base text-danger">
            {report.failures.map((failure, index) => (
              <li key={index}>
                {typeof failure === "string" ? (
                  failure
                ) : (
                  <>
                    <span className="font-semibold">{failure.test ?? failure.title ?? failure.name}</span>
                    {failure.file && <span className="ml-2 font-mono text-sm">{failure.file}</span>}
                    {failure.message && <span className="ml-2">{failure.message}</span>}
                    {!failure.test && !failure.title && !failure.name && JSON.stringify(failure)}
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {history && Array.isArray(history.runs) && history.runs.length > 0 && (
        <details className="rounded-card border border-line bg-surface-alt px-4 py-2.5">
          <summary className="cursor-pointer text-base font-semibold text-ink">
            {history.runs.length} recorded run{history.runs.length === 1 ? "" : "s"}
          </summary>
          <ul className="mt-2 space-y-1 text-base text-ink-muted">
            {history.runs.slice(0, 10).map((run, index) => {
              const outcome = commandOutcome(run);
              const timestamp = run.ran_at ?? run.started_at ?? run.finished_at;
              return (
                <li key={run.id ?? index} className="flex flex-wrap gap-2">
                  <span className="font-mono text-sm">
                    {timestamp ? formatDate(timestamp) : "Time unavailable"}
                  </span>
                  <span>
                    {outcome.symbol} {outcome.label} · {countLabels(run).join(" · ")}
                  </span>
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </div>
  );
}

// Working-tree changes, recorded commits and the manual test runner.
// Nothing here runs automatically: a test run starts only on an explicit click.
export default function ChangesTestsTab() {
  const [changes, setChanges] = useState(null);
  const [changesError, setChangesError] = useState(null);
  const [changesLoading, setChangesLoading] = useState(true);

  const [commits, setCommits] = useState([]);
  const [commitsError, setCommitsError] = useState(null);

  const [command, setCommand] = useState("");
  const [detectNote, setDetectNote] = useState(null);

  const [history, setHistory] = useState(null);
  const [status, setStatus] = useState(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState(null);

  const changesSeqRef = useRef(0);
  const pollSeqRef = useRef(0);
  const timerRef = useRef(null);

  const loadChanges = useCallback(async () => {
    setChangesLoading(true);
    setChangesError(null);
    const mySeq = (changesSeqRef.current += 1);
    try {
      const data = await apiRequest("/v1/changes");
      if (mySeq === changesSeqRef.current) setChanges(data);
    } catch (err) {
      if (mySeq === changesSeqRef.current) setChangesError(err.message);
    } finally {
      if (mySeq === changesSeqRef.current) setChangesLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await apiRequest("/v1/tests/history"));
    } catch {
      // History is optional context; the report above still renders.
    }
  }, []);

  const loadCommits = useCallback(async () => {
    try {
      const data = await apiRequest("/v1/commits?limit=10");
      setCommits(data?.commits ?? []);
      setCommitsError(null);
    } catch (err) {
      setCommitsError(err.message);
    }
  }, []);

  // Prefill the command from the detected runner — but never start a run here.
  useEffect(() => {
    let cancelled = false;
    apiRequest("/v1/tests/detect")
      .then((data) => {
        if (cancelled) return;
        const detected = data && typeof data === "object" ? data.command : null;
        if (detected) {
          setCommand((current) => (current ? current : detected));
          setDetectNote(`Detected: ${detected}`);
        } else {
          setDetectNote("No test command detected — type the command your project uses.");
        }
      })
      .catch((err) => {
        if (!cancelled) setDetectNote(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    loadChanges();
    loadCommits();
    loadHistory();
    return () => {
      pollSeqRef.current += 1;
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [loadChanges, loadCommits, loadHistory]);

  const pollStatus = useCallback(async () => {
    const mySeq = pollSeqRef.current;
    try {
      const data = await apiRequest("/v1/tests/status");
      if (mySeq !== pollSeqRef.current) return;
      setStatus(data);
      if (data?.running) {
        timerRef.current = window.setTimeout(() => {
          if (mySeq === pollSeqRef.current) pollStatus();
        }, POLL_MS);
      } else {
        setRunning(false);
        loadHistory();
      }
    } catch (err) {
      if (mySeq !== pollSeqRef.current) return;
      setRunError(err.message);
      setRunning(false);
    }
  }, [loadHistory]);

  const runTests = async () => {
    const clean = command.trim();
    if (!clean || running) return;
    setRunning(true);
    setRunError(null);
    setStatus({ running: true, output: "" });
    pollSeqRef.current += 1;
    const mySeq = pollSeqRef.current;
    try {
      await apiRequest("/v1/tests/run", { method: "POST", body: { command: clean } });
      if (mySeq === pollSeqRef.current) await pollStatus();
    } catch (err) {
      if (mySeq === pollSeqRef.current) {
        setRunError(err.message);
        setRunning(false);
      }
    }
  };

  const counts = changes?.counts ?? {};
  const files = changes?.files ?? [];
  const output = status?.output ?? "";

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Test runner */}
        <div className="space-y-6 lg:col-span-2">
          <Card
            title="Tests"
            subtitle="Runs are manual and recorded — opening this screen never starts anything."
            actions={
              history && Array.isArray(history.runs) ? (
                <Badge tone="neutral" symbol="▤">
                  {history.runs.length} recorded run{history.runs.length === 1 ? "" : "s"}
                </Badge>
              ) : null
            }
          >
            <div className="space-y-3">
              <div>
                <label className="label" htmlFor="cb-test-command">
                  Test command
                </label>
                <div className="mt-1.5 flex flex-wrap gap-2">
                  <input
                    id="cb-test-command"
                    className="field min-w-0 flex-1 font-mono"
                    value={command}
                    onChange={(event) => setCommand(event.target.value)}
                    placeholder="npm test / pytest / dart test …"
                    spellCheck={false}
                  />
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={runTests}
                    disabled={!command.trim() || running}
                  >
                    {running ? "Running…" : "Run tests"}
                  </button>
                </div>
                {detectNote && <p className="hint mt-1.5">{detectNote}</p>}
              </div>

              <ErrorBanner message={runError} />

              <TestReport status={status} history={history} />

              <div>
                <h3 className="label mb-1.5">Output</h3>
                <pre className="output-pane" aria-live="polite" aria-label="Test output">
                  {output
                    ? output
                    : running
                      ? "Waiting for the first output…"
                      : "No test run yet. Press “Run tests” to start one."}
                </pre>
              </div>
            </div>
          </Card>

          {/* Recorded commits (deliberately compact) */}
          <Card
            title="Recorded commits"
            subtitle="From the local history window — full stats live in Changes."
            actions={
              <button type="button" className="btn btn-secondary" onClick={loadCommits}>
                Refresh
              </button>
            }
          >
            <ErrorBanner message={commitsError} />
            {commits.length === 0 ? (
              <EmptyState title="No commits recorded yet" hint="Commit once, then refresh." />
            ) : (
              <ul className="space-y-1.5">
                {commits.slice(0, 8).map((commit) => (
                  <li key={commit.sha} className="flex flex-wrap items-baseline gap-2 text-base">
                    <code className="rounded-control bg-surface-alt px-2 py-0.5 font-mono text-sm text-primary">
                      {commit.short_sha}
                    </code>
                    <span className="min-w-0 flex-1 text-ink">{commit.subject}</span>
                    <span className="hint">{formatDate(commit.committed_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        {/* Working tree */}
        <div>
          <Card
            title="Working tree"
            subtitle={changes?.branch ? `Branch ${changes.branch}` : "Uncommitted changes"}
            actions={
              <button type="button" className="btn btn-secondary" onClick={loadChanges}>
                Refresh
              </button>
            }
          >
            <ErrorBanner message={changesError} />
            {changesLoading && !changes ? (
              <Spinner label="Reading the working tree…" />
            ) : files.length === 0 ? (
              <EmptyState
                title="Clean working tree"
                hint="Edit a project file, then press Refresh to see the change."
              />
            ) : (
              <>
                <div className="mb-3 flex flex-wrap gap-2">
                  <Badge tone="neutral" symbol="▤">
                    {files.length} file{files.length === 1 ? "" : "s"}
                  </Badge>
                  <Badge tone="ok" symbol="+">
                    {formatNumber(counts.insertions ?? 0)} inserted
                  </Badge>
                  <Badge tone="danger" symbol="−">
                    {formatNumber(counts.deletions ?? 0)} deleted
                  </Badge>
                </div>
                <ul className="max-h-[420px] overflow-y-auto thin-scroll pr-1">
                  {files.map((file) => (
                    <ChangeRow key={file.path} file={file} />
                  ))}
                </ul>
              </>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
