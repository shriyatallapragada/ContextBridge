import { useState } from "react";
import { apiRequest } from "../api.js";
import { notifyDashboardStale } from "../events.js";
import { Badge, EmptyState, ErrorBanner, Spinner } from "./ui.jsx";

const KIND_LABELS = {
  checkbox: "checklist",
  marker: "TODO",
  section: "bullet",
};

const KIND_TONES = {
  checkbox: "info",
  marker: "warn",
  section: "accent",
};

// Paste a transcript → deterministic server-side parse (no model involved).
// "Preview" never writes anything; "Save tasks" writes to SQLite only — it does
// not create git commits.
export default function ChatImport({ onSaved }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(null); // "preview" | "save"
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const run = async (commit) => {
    if (!text.trim() || busy) return; // guards against double submits
    setBusy(commit ? "save" : "preview");
    setError(null);
    try {
      const data = await apiRequest("/v1/chat-import", {
        method: "POST",
        body: { text, commit },
      });
      setResult(data);
      if (data && data.committed) {
        notifyDashboardStale();
        onSaved?.();
      }
    } catch (err) {
      setError(err.message);
      setResult(null);
    } finally {
      setBusy(null);
    }
  };

  const parsed = result?.parsed ?? [];
  const createdCount = result?.created?.length ?? 0;
  const duplicateCount = result?.duplicates ?? 0;
  const saved = Boolean(result?.committed);

  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="cb-transcript" className="label">
          Paste a chat transcript
        </label>
        <textarea
          id="cb-transcript"
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={7}
          spellCheck={false}
          placeholder={
            "- [ ] ship the dashboard\n- [x] parse transcripts\nTODO: review git timeline\n\n## Next steps\n- refresh momentum on boot"
          }
          className="field mt-1.5 font-mono text-base"
        />
        <p className="hint mt-1.5">
          Fixed parsing rules on the server: markdown checkboxes, TODO/ACTION marker lines and
          bullets under a <em>Next steps</em> heading. Same transcript, same tasks — no model is
          used.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => run(false)}
          disabled={!text.trim() || Boolean(busy)}
          className="btn btn-secondary"
        >
          {busy === "preview" ? "Previewing…" : "Preview"}
        </button>
        <button
          type="button"
          onClick={() => run(true)}
          disabled={!text.trim() || Boolean(busy)}
          className="btn btn-primary"
        >
          {busy === "save" ? "Saving…" : "Save tasks"}
        </button>
      </div>
      <p className="hint">
        Saving writes to the local task list (SQLite) only — it never creates a git commit.
      </p>

      <ErrorBanner message={error} />
      {busy && <Spinner label="Parsing transcript…" />}

      {!busy && result && (
        <div className="space-y-3 rounded-card border border-line bg-surface-alt p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={saved ? "ok" : "neutral"} symbol={saved ? "✓" : "👀"}>
              {saved ? `${createdCount} task${createdCount === 1 ? "" : "s"} saved` : "Preview only — nothing saved"}
            </Badge>
            <Badge tone={duplicateCount > 0 ? "warn" : "neutral"} symbol="⧉">
              {duplicateCount} duplicate{duplicateCount === 1 ? "" : "s"}
            </Badge>
            {result.counts && (
              <span className="text-base text-ink-muted">
                total: {result.counts.total} ({result.counts.open} open)
              </span>
            )}
          </div>
          {duplicateCount > 0 && (
            <p className="hint">
              Duplicates already exist in the task list, so they were not created again.
            </p>
          )}

          {parsed.length === 0 ? (
            <EmptyState
              title="Nothing parsed from this text"
              hint="Try a checklist with - [ ] items, TODO:/ACTION: lines, or bullets under a Next steps heading."
            />
          ) : (
            <ul className="space-y-1.5">
              {parsed.map((item, index) => (
                <li
                  key={`${item.line}-${index}`}
                  className="flex items-start gap-2 rounded-control border border-line bg-surface px-3 py-2"
                >
                  <Badge tone={KIND_TONES[item.kind] ?? "accent"}>
                    {KIND_LABELS[item.kind] ?? item.kind}
                  </Badge>
                  <span
                    className={`min-w-0 flex-1 text-base leading-relaxed ${
                      item.status === "resolved" ? "text-ink-muted line-through" : "text-ink"
                    }`}
                  >
                    {item.content}
                  </span>
                  <span className="shrink-0 font-mono text-sm text-ink-muted">L{item.line}</span>
                </li>
              ))}
            </ul>
          )}

          {saved && (
            <p className="text-base text-ok">
              <span aria-hidden="true" className="mr-1 font-bold">
                ✓
              </span>
              Saved — the task checklist has been refreshed.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
