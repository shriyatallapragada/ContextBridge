import { useCallback, useEffect, useMemo, useState } from "react";
import { apiRequest, clearToken, isStorageAvailable, onAuthError, resolveToken, saveToken } from "./api.js";
import { useAsyncData } from "./hooks.js";
import { ErrorBanner, Spinner } from "./components/ui.jsx";
import OverviewTab from "./components/OverviewTab.jsx";
import TasksTab from "./components/TasksTab.jsx";
import ChangesTestsTab from "./components/ChangesTestsTab.jsx";
import HandoffTab from "./components/HandoffTab.jsx";
import ArchitectureTab from "./components/ArchitectureTab.jsx";
import ChatBridge from "./components/ChatBridge.jsx";
import SettingsTab from "./components/SettingsTab.jsx";
import TokenGate from "./components/TokenGate.jsx";

const VIEW_KEY = "cb_view";

const DESTINATIONS = [
  { id: "overview", label: "Overview", icon: "overview" },
  { id: "tasks", label: "Notes and tasks", icon: "tasks" },
  { id: "changes", label: "Changes and tests", icon: "changes" },
  { id: "handoff", label: "Hand off", icon: "handoff" },
  { id: "guide", label: "Project guide", icon: "guide" },
  { id: "chat", label: "Chat bridge", icon: "chat" },
  { id: "settings", label: "Settings", icon: "settings" },
];

const VIEW_IDS = DESTINATIONS.map((item) => item.id);

function readView() {
  let fromHash = null;
  try {
    fromHash = new URLSearchParams(window.location.hash.slice(1)).get("view");
  } catch {
    fromHash = null;
  }
  let fromStore = null;
  try {
    fromStore = window.localStorage.getItem(VIEW_KEY);
  } catch {
    fromStore = null;
  }
  const candidate = fromHash || fromStore;
  return VIEW_IDS.includes(candidate) ? candidate : "overview";
}

function writeView(view) {
  try {
    const params = new URLSearchParams(window.location.hash.slice(1));
    params.set("view", view);
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}#${params.toString()}`
    );
  } catch {
    // History unavailable — the localStorage copy below still keeps context.
  }
  try {
    window.localStorage.setItem(VIEW_KEY, view);
  } catch {
    // Storage unavailable.
  }
}

const ICON_PATHS = {
  overview: "M4 11.5 12 4l8 7.5M6 10.5V20h12v-9.5M10 20v-5h4v5",
  tasks: "M5 6h2m3 0h9M5 12h2m3 0h9M5 18h2m3 0h9M4 6l1 1 1.5-2M4 12l1 1 1.5-2M4 18l1 1 1.5-2",
  changes: "M7 4v10m0 0a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm10-6a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm0 6v1a4 4 0 0 1-4 4h-3",
  handoff: "M4 12h13m0 0-5-5m5 5-5 5M19 4v16",
  guide: "M4 6.5 9.5 4l5 2.5L20 4v13.5L14.5 20l-5-2.5L4 20V6.5Zm5.5-2.5v13.5m5-11v13.5",
  chat: "M5 6h14v10H9l-4 3V6Z",
  settings:
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm8-3-.1.9 1.6 1.4-1.5 2.6-2-.5a8 8 0 0 1-1.7 1l-.4 2.1h-3l-.4-2.1a8 8 0 0 1-1.7-1l-2 .5-1.5-2.6L7.1 12 7 11.1l.1-.9-1.6-1.4 1.5-2.6 2 .5a8 8 0 0 1 1.7-1L11 3.5h3l.4 2.2a8 8 0 0 1 1.7 1l2-.5 1.5 2.6L19.9 11l.1.9Z",
};

function Icon({ name }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-5 w-5 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={ICON_PATHS[name] ?? ICON_PATHS.overview} />
    </svg>
  );
}

function ProjectHeader({ project }) {
  const { data, loading, error, reload } = project;
  return (
    <div className="border-b border-on-dark/15 px-4 py-4">
      <div className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="flex h-8 w-8 items-center justify-center rounded-control bg-cardamom text-sm font-bold text-chai"
        >
          CB
        </span>
        <span className="text-base font-bold tracking-wide text-on-dark">ContextBridge</span>
      </div>

      <div className="mt-3">
        {loading && !data ? (
          <Spinner label="Loading project…" />
        ) : error && !data ? (
          <div className="space-y-2">
            <p className="text-sm font-semibold text-on-dark-muted">Project unknown</p>
            <button type="button" className="btn btn-on-dark btn-block" onClick={reload}>
              Retry project info
            </button>
          </div>
        ) : data ? (
          <>
            <p className="truncate text-base font-bold text-on-dark" title={data.project}>
              {data.project || "Untitled project"}
            </p>
            <p
              className="mt-0.5 break-all font-mono text-sm leading-snug text-on-dark-muted"
              title={data.root || ""}
            >
              {data.root || "Path unavailable"}
            </p>
          </>
        ) : null}
      </div>

      <p className="hint mt-3 rounded-control bg-black/15 px-2.5 py-2 text-on-dark-muted">
        <span aria-hidden="true" className="mr-1 font-bold text-cardamom">
          i
        </span>
        This dashboard shows one project at a time — the one <code className="kbd">cb serve</code>{" "}
        was started in. To switch projects, stop it and restart{" "}
        <code className="kbd">cb serve</code> in the other directory.
      </p>
    </div>
  );
}

export default function App() {
  const [token, setToken] = useState(() => resolveToken());
  const [authMessage, setAuthMessage] = useState(null);
  const [view, setView] = useState(() => readView());
  const [navOpen, setNavOpen] = useState(false);

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

  const navigate = useCallback((next) => {
    setView(next);
    setNavOpen(false);
  }, []);

  useEffect(() => {
    writeView(view);
  }, [view]);

  // Keep in step with manual hash edits (back/forward buttons).
  useEffect(() => {
    const onHash = () => setView(readView());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const acceptToken = useCallback((value) => {
    const clean = saveToken(value);
    if (clean) {
      setToken(clean);
      setAuthMessage(null);
    }
  }, []);

  const project = useAsyncData(async () => (token ? apiRequest("/v1/dashboard") : null), [token]);

  const active = useMemo(
    () => DESTINATIONS.find((item) => item.id === view) ?? DESTINATIONS[0],
    [view]
  );

  if (!token) {
    return <TokenGate message={authMessage} onAccept={acceptToken} />;
  }

  const screenProps = { onNavigate: navigate };
  const storageWarning = !isStorageAvailable();

  return (
    <div className="min-h-screen bg-page lg:flex">
      {/* Small-screen top bar */}
      <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-sidebar px-3 py-2.5 text-on-dark lg:hidden">
        <button
          type="button"
          className="btn btn-on-dark"
          aria-label="Open navigation"
          aria-expanded={navOpen}
          onClick={() => setNavOpen(true)}
        >
          <span aria-hidden="true" className="text-lg leading-none">
            ☰
          </span>
        </button>
        <span className="min-w-0 flex-1 truncate text-base font-bold">{active.label}</span>
        <button
          type="button"
          className="btn btn-on-dark"
          onClick={() => {
            clearToken();
            setToken(null);
            setAuthMessage("Enter a session token to switch sessions.");
          }}
        >
          Token
        </button>
      </header>

      {navOpen && (
        <div
          className="fixed inset-0 z-30 bg-chai/40 lg:hidden"
          role="presentation"
          onClick={() => setNavOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`sidebar-scroll fixed inset-y-0 left-0 z-40 flex w-56 shrink-0 flex-col overflow-y-auto bg-sidebar transition-transform duration-200 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 ${
          navOpen ? "translate-x-0" : "-translate-x-full"
        }`}
        aria-label="Primary"
      >
        <ProjectHeader project={project} />

        <nav className="flex-1 space-y-1 px-3 py-4">
          {DESTINATIONS.map((item) => (
            <button
              key={item.id}
              type="button"
              className="nav-item"
              aria-current={view === item.id ? "page" : undefined}
              onClick={() => navigate(item.id)}
            >
              <Icon name={item.icon} />
              <span className="min-w-0 flex-1">{item.label}</span>
            </button>
          ))}
        </nav>

        <div className="space-y-3 border-t border-on-dark/15 px-4 py-4">
          <div>
            <p className="text-sm font-semibold text-on-dark">Session token</p>
            <p className="hint mt-0.5 text-on-dark-muted">
              Active in this browser. It is not the provider API key (that one stays on the
              server).
            </p>
          </div>
          {storageWarning && (
            <p className="hint rounded-control bg-black/20 px-2 py-1.5 text-cardamom">
              Browser storage is unavailable — the token lasts only for this tab.
            </p>
          )}
          <button
            type="button"
            className="btn btn-on-dark btn-block"
            onClick={() => {
              clearToken();
              setToken(null);
              setAuthMessage("Enter a session token to switch sessions.");
            }}
          >
            Change session token
          </button>
          <button type="button" className="btn btn-on-dark btn-block lg:hidden" onClick={() => setNavOpen(false)}>
            Close menu
          </button>
        </div>
      </aside>

      {/* Content */}
      <div className="min-w-0 flex-1">
        <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
          <ErrorBanner message={project.error && project.data ? project.error : null} onRetry={project.reload} />
          {view === "overview" && <OverviewTab {...screenProps} />}
          {view === "tasks" && <TasksTab {...screenProps} />}
          {view === "changes" && <ChangesTestsTab {...screenProps} />}
          {view === "handoff" && <HandoffTab {...screenProps} />}
          {view === "guide" && <ArchitectureTab {...screenProps} />}
          {view === "chat" && <ChatBridge {...screenProps} />}
          {view === "settings" && <SettingsTab {...screenProps} />}
        </main>

        <footer className="mx-auto w-full max-w-6xl px-4 pb-8 text-base text-ink-muted sm:px-6 lg:px-8">
          Local-first dashboard — parsing, git and AST work happen on the Python side. Views
          without an “Explain” action never call a model.
        </footer>
      </div>
    </div>
  );
}
