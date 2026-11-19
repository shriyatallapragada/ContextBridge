import { useState } from "react";
import { apiRequest } from "../api.js";
import { Badge, EmptyState, ErrorBanner, Spinner } from "./ui.jsx";

const KIND_LABELS = {
  checkbox: "checkbox",
  marker: "TODO",
  section: "bullet",
};

const KIND_TONES = {
  checkbox: "border-sky-800 bg-sky-950/60 text-sky-300",
  marker: "border-amber-800 bg-amber-950/60 text-amber-300",
  section: "border-violet-800 bg-violet-950/60 text-violet-300",
};

// Paste a transcript; the server parses it with fixed deterministic rules and
// (optionally) stores the results as tasks. The browser never parses or calls a model.
export default function ChatBridge({ onImported }) {
  const [text, setText] = useState("");
  const [commit, setCommit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const data = await apiRequest("/v1/chat-import", {
        method: "POST",
        body: { text, commit },
      });
      setResult(data);
      if (data && data.committed) onImported?.();
    } catch (err) {
      setError(err.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const createdCount = result?.created?.length ?? 0;
  const duplicateCount = result?.duplicates ?? 0;
  const parsed = result?.parsed ?? [];

  return (
    <form onSubmit={submit} className="space-y-4">
      <div>
        <label htmlFor="cb-transcript" className="block text-xs font-medium text-zinc-400">
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
          className="mt-1.5 w-full resize-y rounded-xl border border-zinc-700 bg-zinc-950 px-3 py-2.5 font-mono text-xs leading-relaxed text-zinc-200 outline-none transition focus:border-sky-600"
        />
        <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-500">
          Fixed parsing rules on the server: markdown checkboxes, TODO/ACTION marker lines, and
          bullets under a <em>Next steps</em> heading. Same transcript, same tasks.
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex cursor-pointer select-none items-center gap-2 text-xs text-zinc-300">
          <input
            type="checkbox"
            checked={commit}
            onChange={(event) => setCommit(event.target.checked)}
            className="h-4 w-4 rounded border-zinc-600 bg-zinc-950 accent-sky-600"
          />
          Commit to git
        </label>
        <button
          type="submit"
          disabled={!text.trim() || busy}
          className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? "Importing…" : "Import"}
        </button>
      </div>

      <ErrorBanner message={error} />

      {busy && <Spinner label="Parsing transcript…" />}

      {!busy && result && (
        <div className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-950/60 p-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge className="border-emerald-800 bg-emerald-950/60 text-emerald-300">
              {result.committed ? `${createdCount} created` : "preview only"}
            </Badge>
            <Badge className="border-zinc-700 bg-zinc-900 text-zinc-400">
              {duplicateCount} duplicate{duplicateCount === 1 ? "" : "s"}
            </Badge>
            {result.counts && (
              <span className="text-zinc-500">
                total: {result.counts.total} ({result.counts.open} open)
              </span>
            )}
          </div>

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
                  className="flex items-start gap-2 rounded-lg border border-zinc-800/80 bg-zinc-900/40 px-3 py-2"
                >
                  <Badge className={`mt-0.5 shrink-0 ${KIND_TONES[item.kind] ?? KIND_TONES.section}`}>
                    {KIND_LABELS[item.kind] ?? item.kind}
                  </Badge>
                  <span
                    className={`min-w-0 flex-1 text-xs leading-relaxed ${
                      item.status === "resolved" ? "text-zinc-500 line-through" : "text-zinc-300"
                    }`}
                  >
                    {item.content}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-zinc-600">L{item.line}</span>
                </li>
              ))}
            </ul>
          )}

          {result.committed && (
            <p className="text-[11px] text-emerald-400">
              Saved — the task checklist has been refreshed.
            </p>
          )}
        </div>
      )}
    </form>
  );
}
