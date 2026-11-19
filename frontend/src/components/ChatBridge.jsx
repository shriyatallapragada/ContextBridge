import { useEffect, useState } from "react";
import { apiRequest } from "../api.js";
import { Badge, Card, EmptyState, ErrorBanner, Notice, Spinner, copyText, downloadText } from "./ui.jsx";
import ChatImport from "./ChatImport.jsx";

const SECTION_ORDER_HINT = [
  "Goal",
  "Done",
  "Decisions",
  "Current state",
  "Open problems",
  "Next steps",
  "Files",
];

function normalizeSections(sections) {
  if (!sections) return [];
  if (Array.isArray(sections)) {
    return sections
      .map((entry) => ({
        title: entry.title ?? entry.name ?? "",
        lines: Array.isArray(entry.lines)
          ? entry.lines
          : Array.isArray(entry.items)
            ? entry.items
            : [String(entry.content ?? entry.text ?? "")],
      }))
      .filter((entry) => entry.title || entry.lines.length);
  }
  return Object.entries(sections).map(([title, lines]) => ({
    title,
    lines: Array.isArray(lines) ? lines.map(String) : [String(lines)],
  }));
}

// Operation 2: paste a conversation → deterministic condensed handoff text.
function ConversationHandoff({ incomingText }) {
  const [text, setText] = useState("");
  const [goal, setGoal] = useState("");
  const [pinned, setPinned] = useState("");
  const [budget, setBudget] = useState(2000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [edited, setEdited] = useState(null);
  const [copyState, setCopyState] = useState("idle");

  // A file imported below lands here.
  useEffect(() => {
    if (incomingText) setText(incomingText);
  }, [incomingText]);

  const preview = async () => {
    if (!text.trim() || busy) return; // no double submits
    setBusy(true);
    setError(null);
    setEdited(null);
    try {
      const body = { text };
      if (goal.trim()) body.goal = goal.trim();
      if (pinned.trim()) body.pinned = pinned.trim();
      const budgetNumber = Number(budget);
      if (!Number.isInteger(budgetNumber) || budgetNumber < 2000 || budgetNumber > 12000) {
        setError("Choose a budget between 2,000 and 12,000 tokens.");
        return;
      }
      body.budget = budgetNumber;
      setResult(await apiRequest("/v1/chat/handoff", { method: "POST", body }));
    } catch (err) {
      setError(err.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  const sections = normalizeSections(result?.sections);
  const finalText = edited ?? result?.text ?? "";
  const warnings = Array.isArray(result?.warnings) ? result.warnings : [];

  const doCopy = async () => {
    const ok = await copyText(finalText);
    setCopyState(ok ? "copied" : "error");
    window.setTimeout(() => setCopyState("idle"), 2500);
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="md:col-span-2">
          <label htmlFor="cb-conversation" className="label">
            Conversation to hand off
          </label>
          <textarea
            id="cb-conversation"
            className="field mt-1.5 font-mono text-base"
            rows={8}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Paste the transcript you want the next agent to pick up…"
          />
        </div>
        <div>
          <label htmlFor="cb-conversation-goal" className="label">
            Goal <span className="hint">(optional)</span>
          </label>
          <input
            id="cb-conversation-goal"
            className="field mt-1.5"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            placeholder="What this conversation is trying to achieve"
          />
        </div>
        <div>
          <label htmlFor="cb-conversation-budget" className="label">
            Budget (tokens)
          </label>
          <input
            id="cb-conversation-budget"
            type="number"
            min={2000}
            max={12000}
            step={100}
            className="field mt-1.5"
            value={budget}
            onChange={(event) => setBudget(event.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <label htmlFor="cb-conversation-pinned" className="label">
            Pinned facts <span className="hint">(optional — always included)</span>
          </label>
          <textarea
            id="cb-conversation-pinned"
            className="field mt-1.5"
            rows={3}
            value={pinned}
            onChange={(event) => setPinned(event.target.value)}
            placeholder="Decisions or constraints that must survive the handoff"
          />
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="btn btn-primary"
          onClick={preview}
          disabled={!text.trim() || busy}
        >
          {busy ? "Preparing…" : "Preview handoff"}
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={doCopy}
          disabled={!finalText || busy}
        >
          {copyState === "copied"
            ? "Copied ✓"
            : copyState === "error"
              ? "Copy failed"
              : "Copy"}
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => downloadText("conversation-handoff.md", finalText)}
          disabled={!finalText || busy}
        >
          Export .md
        </button>
      </div>

      <ErrorBanner message={error} />
      {busy && <Spinner label="Condensing the conversation (deterministic, no model)…" />}

      {warnings.length > 0 && (
        <div className="rounded-card border border-warn bg-warn-bg px-4 py-3 text-warn">
          <h3 className="text-base font-bold">
            <span aria-hidden="true" className="mr-1">
              ⚠
            </span>
            Warnings — review before sending
          </h3>
          <ul className="mt-1.5 list-disc space-y-1 pl-5 text-base">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      {result && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral" symbol="↗">
              {Number(result.source_chars ?? 0).toLocaleString()} source characters
            </Badge>
            {Number(result.omitted_chars ?? 0) > 0 && (
              <Badge tone="warn" symbol="✂">
                {Number(result.omitted_chars).toLocaleString()} omitted
              </Badge>
            )}
            <Badge tone="neutral">≈{Math.round(finalText.length / 4).toLocaleString()} tokens</Badge>
          </div>

          {sections.length > 0 && edited === null ? (
            <div className="space-y-3">
              {sections.map((section) => (
                <section
                  key={section.title}
                  className="rounded-card border border-line bg-surface-alt px-4 py-3"
                >
                  <h3 className="text-base font-bold" style={{ color: "var(--red-chai)" }}>
                    {section.title}
                  </h3>
                  <ul className="mt-1.5 space-y-1.5">
                    {section.lines.map((line, index) => (
                      <li key={index} className="whitespace-pre-wrap text-base leading-relaxed text-ink">
                        {line}
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
              <p className="hint">
                Expected order: {SECTION_ORDER_HINT.join(" · ")} (the server decides the actual
                content of each section).
              </p>
              <button type="button" className="btn btn-secondary" onClick={() => setEdited(result?.text ?? "")}>
                Edit as plain text
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              <label htmlFor="cb-handoff-edited" className="label">
                Preview (editable)
              </label>
              <textarea
                id="cb-handoff-edited"
                className="field font-mono text-base"
                rows={16}
                value={finalText}
                onChange={(event) => setEdited(event.target.value)}
              />
              {edited !== null && (
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setEdited(null)}
                >
                  Reset to server version
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {!result && !busy && (
        <EmptyState
          title="No handoff yet"
          hint="Paste a conversation above and press Preview handoff."
        />
      )}
    </div>
  );
}

// Operation 3: read a .txt / .md / .pdf file into the conversation handoff.
function FileImport({ onLoaded }) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const run = async () => {
    if (!path.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await apiRequest("/v1/chat/import-file", { method: "POST", body: { path: path.trim() } }));
    } catch (err) {
      setError(err.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <label htmlFor="cb-import-file" className="label">
          File path inside this project
        </label>
        <div className="mt-1.5 flex flex-wrap gap-2">
          <input
            id="cb-import-file"
            className="field min-w-0 flex-1 font-mono"
            value={path}
            onChange={(event) => setPath(event.target.value)}
            placeholder="docs/notes.txt · meeting.md · spec.pdf"
            spellCheck={false}
          />
          <button
            type="button"
            className="btn btn-primary"
            onClick={run}
            disabled={!path.trim() || busy}
          >
            {busy ? "Reading…" : "Import file"}
          </button>
        </div>
        <p className="hint mt-1.5">.txt, .md and .pdf files inside the project directory.</p>
      </div>

      <ErrorBanner message={error} />

      {result && (
        <div className="space-y-3">
          {Array.isArray(result.warnings) && result.warnings.length > 0 && (
            <Notice tone="warn">
              <ul className="list-disc space-y-1 pl-5">
                {result.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </Notice>
          )}
          <pre className="max-h-72 overflow-auto thin-scroll whitespace-pre-wrap break-words rounded-card border border-line bg-surface-alt p-4 text-base leading-relaxed text-ink">
            {result.text}
          </pre>
          <button type="button" className="btn btn-secondary" onClick={() => onLoaded?.(result.text ?? "")}>
            Use in conversation handoff ↑
          </button>
        </div>
      )}
    </div>
  );
}

// Two clearly separated operations: importing tasks and preparing a handoff.
export default function ChatBridge() {
  const [conversation, setConversation] = useState("");

  return (
    <div className="space-y-6">
      <Card
        title="1 · Import tasks from text"
        subtitle="Deterministic parsing of a pasted transcript into the local task list. No model, no git commits."
      >
        <ChatImport />
      </Card>

      <Card
        title="2 · Prepare a conversation handoff"
        subtitle="Condense this conversation into structured sections the next agent can act on."
      >
        <ConversationHandoff incomingText={conversation} />
      </Card>

      <Card title="Import from a file" subtitle="Read a text, Markdown or PDF file into the handoff box above.">
        <FileImport onLoaded={setConversation} />
        {conversation && (
          <p className="hint mt-2">
            {conversation.length.toLocaleString()} characters loaded into the conversation handoff
            box above.
          </p>
        )}
      </Card>
    </div>
  );
}
