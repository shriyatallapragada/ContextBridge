import { useCallback, useEffect, useState } from "react";
import { clearToken, onAuthError, resolveToken, saveToken } from "./api.js";
import OverviewTab from "./components/OverviewTab.jsx";
import TasksTab from "./components/TasksTab.jsx";
import ArchitectureTab from "./components/ArchitectureTab.jsx";
import TokenGate from "./components/TokenGate.jsx";

const TABS = [
  { id: "overview", label: "Overview & Timeline" },
  { id: "tasks", label: "To-Do & Tasks" },
  { id: "architecture", label: "Architecture Map" },
];

export default function App() {
  const [token, setToken] = useState(() => resolveToken());
  const [authMessage, setAuthMessage] = useState(null);
  const [tab, setTab] = useState("overview");

  // A 401 anywhere in the app drops the stale token and prompts for a new one.
  useEffect(
    () =>
      onAuthError((message) => {
        clearToken();
        setToken(null);
        setAuthMessage(message);
      }),
    []
  );

  const acceptToken = useCallback((value) => {
    const clean = saveToken(value);
    if (clean) {
      setToken(clean);
      setAuthMessage(null);
    }
  }, []);

  if (!token) {
    return <TokenGate message={authMessage} onAccept={acceptToken} />;
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-200">
      <header className="sticky top-0 z-20 border-b border-zinc-800 bg-zinc-950/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4 sm:px-6">
          <div className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="flex h-7 w-7 items-center justify-center rounded-lg bg-sky-600 text-sm font-bold text-white"
            >
              CB
            </span>
            <span className="text-sm font-semibold tracking-wide text-zinc-100">ContextBridge</span>
          </div>

          <nav className="flex flex-1 items-center gap-1 overflow-x-auto" aria-label="Dashboard tabs">
            {TABS.map((option) => (
              <button
                key={option.id}
                type="button"
                onClick={() => setTab(option.id)}
                aria-current={tab === option.id ? "page" : undefined}
                className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium transition sm:text-sm ${
                  tab === option.id
                    ? "bg-zinc-800 text-zinc-50"
                    : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
                }`}
              >
                {option.label}
              </button>
            ))}
          </nav>

          <button
            type="button"
            onClick={() => {
              clearToken();
              setToken(null);
              setAuthMessage("Enter a session token to switch sessions.");
            }}
            className="hidden shrink-0 rounded-lg border border-zinc-800 px-2.5 py-1 text-[11px] text-zinc-500 transition hover:border-zinc-600 hover:text-zinc-300 sm:block"
            title="Change session token"
          >
            Token
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6">
        {tab === "overview" && <OverviewTab onNavigate={setTab} />}
        {tab === "tasks" && <TasksTab />}
        {tab === "architecture" && <ArchitectureTab />}
      </main>

      <footer className="mx-auto max-w-6xl px-4 pb-8 text-center text-[11px] text-zinc-700 sm:px-6">
        Deterministic dashboard — parsing, git and AST work all happen on the Python side. 0 tokens.
      </footer>
    </div>
  );
}
