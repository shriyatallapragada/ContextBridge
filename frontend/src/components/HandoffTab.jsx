import { useEffect, useState } from "react";
import { apiRequest } from "../api.js";
import { Badge, Card, EmptyState, ErrorBanner, Notice, Spinner, copyText, formatAge, formatDate } from "./ui.jsx";

const DESTINATIONS = [
  { id: "codex", label: "Codex (codex.md / AGENTS.md)" },
  { id: "claude", label: "Claude (CLAUDE.md)" },
  { id: "cursor", label: "Cursor (.cursor/rules)" },
  { id: "kiro", label: "Kiro (.kiro/steering)" },
  { id: "generic", label: "Generic file (HANDOFF.md)" },
];

const SOURCES = [
  { id: "", label: "Detect automatically (recommended)" },
  { id: "codex", label: "codex" },
  { id: "claude", label: "claude" },
  { id: "cursor", label: "cursor" },
  { id: "kiro", label: "kiro" },
  { id: "generic", label: "generic" },
  { id: "unknown", label: "unknown" },
];

function handoffText(result) {
  if (!result) return "";
  if (typeof result === "string") return result;
  return result.text ?? result.payload ?? result.content ?? "";
}

function handoffTarget(result) {
  if (!result || typeof result !== "object") return null;
  return result.target_path ?? result.path ?? result.target ?? null;
}

function TestRecord({ test, history }) {
  const age =
    (test && typeof test === "object" && (test.age_seconds ?? test.last_run_age_seconds)) ??
    history?.last_run_age_seconds;
  const stale =
    (test && typeof test === "object" ? test.stale : undefined) ?? history?.stale;
  const passed = test && typeof test === "object" ? test.passed : undefined;
  const failed = test && typeof test === "object" ? test.failed : undefined;
  const skipped = test && typeof test === "object" ? test.skipped : undefined;
  const exitCode = test && typeof test === "object" ? test.exit_code : undefined;

  if (!test && !history) return null;

  return (
    <div className="rounded-card border border-line bg-surface-alt px-4 py-3">
      <h3 className="text-base font-bold text-ink">Recorded test result</h3>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {typeof test === "string" ? (
          <span className="text-base text-ink">{test}</span>
        ) : test ? (
          <>
            <Badge tone={failed > 0 ? "danger" : "ok"} symbol={failed > 0 ? "✗" : "✓"}>
              {failed > 0 ? "Failed" : "Passed"}
            </Badge>
            <Badge tone="ok" symbol="+">
              {passed ?? 0} passed
            </Badge>
            <Badge tone={failed > 0 ? "danger" : "neutral"} symbol="−">
              {failed ?? 0} failed
            </Badge>
            {typeof skipped === "number" && (
              <Badge tone="neutral" symbol="⊘">
                {skipped} skipped
              </Badge>
            )}
            {typeof exitCode === "number" && <Badge tone="neutral">exit {exitCode}</Badge>}
            {test.command && <span className="font-mono text-sm text-ink-muted">{test.command}</span>}
          </>
        ) : (
          <span className="hint">No recorded test result in the handoff response.</span>
        )}
        {typeof age === "number" && (
          <Badge tone={stale ? "warn" : "neutral"} symbol={stale ? "⚠" : "⏱"}>
            {formatAge(age)}
            {stale ? " · stale" : ""}
          </Badge>
        )}
      </div>
      {stale && (
        <p className="hint mt-1.5">
          Stale: this result predates the newest changes, so it may not describe the current code.
        </p>
      )}
      {!test && history && typeof history.last_run_age_seconds === "number" && (
        <p className="hint mt-1.5">
          Last recorded run {formatAge(history.last_run_age_seconds)} ·{" "}
          {history.runs?.length ?? 0} run{history.runs?.length === 1 ? "" : "s"} on file.
        </p>
      )}
    </div>
  );
}

// Destination picker → Preview (readable text) → Copy / Write.
export default function HandoffTab() {
  const [toAgent, setToAgent] = useState("codex");
  const [fromAgent, setFromAgent] = useState("");
  const [budget, setBudget] = useState(2000);
  const [polish, setPolish] = useState(false); // the only model-assisted step, OFF by default
  const [force, setForce] = useState(false);

  const [preview, setPreview] = useState(null);
  const [previewConfig, setPreviewConfig] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState(null);

  const [writing, setWriting] = useState(false);
  const [writeResult, setWriteResult] = useState(null);
  const [writeError, setWriteError] = useState(null);

  const [copyState, setCopyState] = useState("idle");
  const [history, setHistory] = useState(null);

  useEffect(() => {
    let cancelled = false;
    apiRequest("/v1/tests/history")
      .then((data) => {
        if (!cancelled) setHistory(data);
      })
      .catch(() => {
        // Optional context only.
      });
    apiRequest("/v1/settings")
      .then((data) => {
        if (!cancelled && data?.polish_handoffs) setPolish(true);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const body = () => {
    const budgetNumber = Math.min(12000, Math.max(120, Number(budget) || 2000));
    return {
      to_agent: toAgent,
      from_agent: fromAgent || null,
      budget: budgetNumber,
      polish,
      force,
    };
  };

  const doPreview = async () => {
    if (previewing || writing) return;
    setPreviewing(true);
    setPreviewError(null);
    setWriteResult(null);
    setWriteError(null);
    setPreviewConfig(null);
    const config = body();
    try {
      setPreview(await apiRequest("/v1/handoffs/preview", { method: "POST", body: config }));
      setPreviewConfig(JSON.stringify({ ...config, force: false }));
    } catch (err) {
      // Drafts (fields above) are preserved on failure.
      setPreviewError(err.message);
      if (err.status === 409) setForce(true);
    } finally {
      setPreviewing(false);
    }
  };

  const canWrite = previewConfig === JSON.stringify({ ...body(), force: false });
  const doWrite = async () => {
    if (!canWrite || writing || previewing) return;
    setWriting(true);
    setWriteError(null);
    setWriteResult(null);
    try {
      const result = await apiRequest("/v1/handoffs", {
        method: "POST",
        body: { ...body(), ...(preview.preview_id ? { preview_id: preview.preview_id } : {}) },
      });
      setWriteResult(result);
      setPreview((current) => current ?? result);
      setPreviewConfig(null);
      setForce(false);
    } catch (err) {
      setWriteError(err.message);
      if (err.status === 409) setForce(true);
    } finally {
      setWriting(false);
    }
  };

  const doCopy = async () => {
    const ok = await copyText(handoffText(preview));
    setCopyState(ok ? "copied" : "error");
    window.setTimeout(() => setCopyState("idle"), 2500);
  };

  const warnings = [
    ...(Array.isArray(preview?.warnings) ? preview.warnings : []),
    ...(Array.isArray(writeResult?.warnings) ? writeResult.warnings : []),
  ];
  const dedupedWarnings = [...new Set(warnings)];
  const sameAgent =
    (fromAgent && fromAgent === toAgent) ||
    (preview?.source_agent && preview.source_agent === toAgent) ||
    (preview?.source_label && preview.source_label === toAgent);
  const text = handoffText(preview);
  const target = handoffTarget(writeResult) ?? handoffTarget(preview);

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        <Card
          title="Hand off to another agent"
          subtitle="A bounded summary of this project — goal, current state, decisions and prioritised files — written into the destination agent's file."
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="label" htmlFor="cb-handoff-to">
                Destination agent
              </label>
              <select
                id="cb-handoff-to"
                className="field mt-1.5"
                value={toAgent}
                onChange={(event) => setToAgent(event.target.value)}
              >
                {DESTINATIONS.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="label" htmlFor="cb-handoff-from">
                Coming from
              </label>
              <select
                id="cb-handoff-from"
                className="field mt-1.5"
                value={fromAgent}
                onChange={(event) => setFromAgent(event.target.value)}
              >
                {SOURCES.map((option) => (
                  <option key={option.id || "auto"} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
              <p className="hint mt-1.5">Where the conversation is coming from (optional).</p>
            </div>

            <div>
              <label className="label" htmlFor="cb-handoff-budget">
                Token budget
              </label>
              <input
                id="cb-handoff-budget"
                type="number"
                min={120}
                max={12000}
                step={100}
                className="field mt-1.5"
                value={budget}
                onChange={(event) => setBudget(event.target.value)}
              />
              <p className="hint mt-1.5">Default 2000 tokens (120–12 000).</p>
            </div>

            <div className="flex items-start">
              <label className="mt-6 flex cursor-pointer select-none items-start gap-2.5 text-base text-ink">
                <input
                  type="checkbox"
                  checked={polish}
                  onChange={(event) => setPolish(event.target.checked)}
                  className="mt-1 h-5 w-5 rounded-control border-line accent-[var(--primary)]"
                />
                <span>
                  Polish the handoff with a model
                  <span className="hint mt-0.5 block">
                    Off by default — polishing is the <strong>only</strong> step in this flow that
                    sends text to a model. Without it everything is deterministic.
                  </span>
                </span>
              </label>
            </div>
          </div>

          {force && (
            <Notice tone="warn">
              The last write needs <code className="kbd">force</code> (the target already exists in
              a state the server will not overwrite by default). Press “Write with force” to
              proceed.
            </Notice>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="btn btn-primary"
              onClick={doPreview}
              disabled={previewing || writing}
            >
              {previewing ? "Preparing preview…" : "Preview"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={doWrite}
              disabled={!canWrite || writing || previewing}
            >
              {writing ? "Writing…" : force ? "Write with force" : "Write handoff"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={doCopy}
              disabled={!text || previewing}
            >
              {copyState === "copied"
                ? "Copied ✓"
                : copyState === "error"
                  ? "Copy failed"
                  : "Copy to clipboard"}
            </button>
          </div>

          <div className="mt-3 space-y-3">
            <ErrorBanner message={previewError} />
            <ErrorBanner message={writeError} />
          </div>
        </Card>

        <Card
          title="Preview"
          actions={
            preview?.method ? (
              <Badge tone="neutral" symbol="⚙">
                {preview.method}
              </Badge>
            ) : null
          }
        >
          {previewing ? (
            <Spinner label="Building the handoff from local context…" />
          ) : dedupedWarnings.length > 0 ? (
            <div className="space-y-3">
              <div className="rounded-card border border-warn bg-warn-bg px-4 py-3 text-warn">
                <h3 className="text-base font-bold">
                  <span aria-hidden="true" className="mr-1">
                    ⚠
                  </span>
                  Check before using this handoff
                </h3>
                <ul className="mt-1.5 list-disc space-y-1 pl-5 text-base">
                  {dedupedWarnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
              {text && (
                <pre className="whitespace-pre-wrap break-words rounded-card border border-line bg-surface-alt p-4 text-base leading-relaxed text-ink">
                  {text}
                </pre>
              )}
            </div>
          ) : text ? (
            <pre className="whitespace-pre-wrap break-words rounded-card border border-line bg-surface-alt p-4 text-base leading-relaxed text-ink">
              {text}
            </pre>
          ) : (
            <EmptyState
              title="No preview yet"
              hint="Choose a destination above and press Preview."
            />
          )}

          {sameAgent && (
            <p className="hint mt-3 rounded-card border border-warn bg-warn-bg px-3 py-2 text-warn">
              <span aria-hidden="true" className="mr-1 font-bold">
                ⚠
              </span>
              Source and destination are the same agent — a handoff usually goes to a{" "}
              <em>different</em> agent.
            </p>
          )}
        </Card>

        {writeResult && (
          <Card title="Written">
            <div className="space-y-2">
              <p className="text-base text-ok">
                <span aria-hidden="true" className="mr-1 font-bold">
                  ✓
                </span>
                The handoff was written
                {target ? (
                  <>
                    {" "}
                    to <code className="kbd">{target}</code>
                  </>
                ) : null}
                .
              </p>
              {writeResult.updated_at && (
                <p className="hint">Recorded {formatDate(writeResult.updated_at)}.</p>
              )}
              <TestRecord test={writeResult.test} history={history} />
            </div>
          </Card>
        )}
      </div>

      {/* Side panel */}
      <div className="space-y-6">
        <Card title="What goes into a handoff">
          <ul className="space-y-2 text-base text-ink">
            <li>· Project goal (the North Star you set on Overview)</li>
            <li>· Current state: tasks, notes, decisions, last commits</li>
            <li>· Eight prioritised source files with purposes</li>
            <li>· Open problems and next steps</li>
          </ul>
          <p className="hint mt-3">
            Everything except polishing is computed locally — no model call, no tokens spent.
          </p>
        </Card>
      </div>
    </div>
  );
}
