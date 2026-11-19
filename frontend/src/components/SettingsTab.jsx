import { useEffect, useState } from "react";
import { apiRequest, isStorageAvailable, requestRelogin } from "../api.js";
import { useAsyncData } from "../hooks.js";
import { Badge, Card, ErrorBanner, Notice, Spinner } from "./ui.jsx";

const MODES = [
  {
    id: "offline",
    provider: "none",
    title: "Offline — no model",
    blurb:
      "Everything deterministic: summaries, handoffs, task parsing and the file map run locally. Nothing is sent anywhere. Explanations stay unavailable.",
  },
  {
    id: "ollama",
    provider: "ollama",
    title: "Ollama — local model",
    blurb: "A model running on this machine. Explanations and polishing stay on your hardware.",
  },
  {
    id: "hosted",
    provider: "openai-compatible",
    title: "Hosted-compatible API",
    blurb:
      "An OpenAI-compatible endpoint you have an account with. Saved keys stay in the system keychain and are never sent back to the browser.",
  },
];

function modeFromSettings(settings) {
  if (settings?.mode) return ({ off: "offline", local: "ollama", cloud: "hosted" })[settings.mode] ?? settings.mode;
  const provider = settings?.provider;
  if (!provider || provider === "none") return "offline";
  if (provider === "ollama") return "ollama";
  return "hosted";
}

export default function SettingsTab({ onNavigate }) {
  const load = useAsyncData(() => apiRequest("/v1/settings"), []);

  const [mode, setMode] = useState("offline");
  const [model, setModel] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [polish, setPolish] = useState(false);
  const [initialised, setInitialised] = useState(false);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [saved, setSaved] = useState(false);

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testError, setTestError] = useState(null);

  const settings = load.data;

  useEffect(() => {
    if (!settings || initialised) return;
    setMode(modeFromSettings(settings));
    setModel(settings.model ?? "");
    setEndpoint(settings.endpoint ?? "");
    setPolish(Boolean(settings.polish_handoffs));
    setInitialised(true);
  }, [settings, initialised]);

  const payload = () => {
    const info = MODES.find((item) => item.id === mode) ?? MODES[0];
    return {
      mode: { offline: "off", ollama: "local", hosted: "cloud" }[mode],
      provider: info.provider,
      model: model.trim(),
      endpoint: endpoint.trim(),
      polish_handoffs: polish,
      ...(mode === "hosted" && apiKey.trim() ? { api_key: apiKey.trim() } : {}),
    };
  };

  const save = async (event) => {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      await apiRequest("/v1/settings", { method: "POST", body: payload() });
      setApiKey("");
      load.reload();
      setSaved(true);
    } catch (err) {
      setSaveError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    if (testing) return;
    setTesting(true);
    setTestResult(null);
    setTestError(null);
    try {
      const data = await apiRequest("/v1/settings/test", { method: "POST", body: payload() });
      setTestResult(data ?? { ok: false, message: "The server returned no result." });
    } catch (err) {
      setTestError(err.message);
    } finally {
      setTesting(false);
    }
  };

  if (load.loading && !settings) return <Spinner label="Loading settings…" />;
  if (load.error && !settings) return <ErrorBanner message={load.error} onRetry={load.reload} />;

  const activeMode = MODES.find((item) => item.id === mode) ?? MODES[0];

  return (
    <div className="space-y-6">
      <ErrorBanner message={load.error} onRetry={load.reload} />

      <Card
        title="Works without a model"
        subtitle="First things first: ContextBridge is useful before any provider is configured."
      >
        <ul className="space-y-1.5 text-base text-ink">
          <li>· Goal, tasks, notes, git timeline, changes and test records — always local.</li>
          <li>· Handoffs and chat imports are deterministic — no tokens, no network.</li>
          <li>· File/component explanations and optional polishing need a model — that is all.</li>
        </ul>
      </Card>

      <form onSubmit={save} className="space-y-6">
        <Card
          title="Model mode"
          subtitle="Choose where model requests go. Saved keys are never sent back to the browser."
        >
          <fieldset>
            <legend className="label mb-2">Mode</legend>
            <div className="space-y-2">
              {MODES.map((item) => (
                <label
                  key={item.id}
                  className={`flex cursor-pointer items-start gap-3 rounded-card border px-4 py-3 transition ${
                    mode === item.id
                      ? "border-accent bg-surface-alt"
                      : "border-line bg-surface hover:border-accent"
                  }`}
                >
                  <input
                    type="radio"
                    name="cb-mode"
                    value={item.id}
                    checked={mode === item.id}
                    onChange={() => setMode(item.id)}
                    className="mt-1.5 h-5 w-5 accent-[var(--primary)]"
                  />
                  <span>
                    <span className="block text-base font-bold text-ink">{item.title}</span>
                    <span className="hint mt-0.5 block">{item.blurb}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <div>
              <label className="label" htmlFor="cb-model">
                Exact model name
              </label>
              <input
                id="cb-model"
                className="field mt-1.5 font-mono"
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder={
                  mode === "offline" ? "not used offline" : mode === "ollama" ? "llama3.2" : "gpt-4o-mini"
                }
                spellCheck={false}
                disabled={mode === "offline"}
              />
            </div>
            <div>
              <label className="label" htmlFor="cb-endpoint">
                Endpoint {mode === "ollama" ? "(local Ollama host)" : mode === "hosted" ? "(API base URL)" : ""}
              </label>
              <input
                id="cb-endpoint"
                className="field mt-1.5 font-mono"
                value={endpoint}
                onChange={(event) => setEndpoint(event.target.value)}
                placeholder={
                  mode === "ollama"
                    ? "http://localhost:11434"
                    : mode === "hosted"
                      ? "https://api.your-provider.com/v1"
                      : "not used offline"
                }
                spellCheck={false}
                disabled={mode === "offline"}
              />
            </div>
          </div>

          <div className="mt-4 rounded-card border border-line bg-surface-alt px-4 py-3">
            {mode === "hosted" && (
              <label className="label mb-3 block">
                Provider API key (leave blank to keep the saved key)
                <input type="password" className="field mt-1" autoComplete="off"
                  value={apiKey} onChange={(event) => setApiKey(event.target.value)} />
              </label>
            )}
            <p className="text-base">
              <span className="font-bold">Provider API key: </span>
              {settings?.has_api_key ? (
                <Badge tone="ok" symbol="✓">
                  configured on this machine
                </Badge>
              ) : (
                <Badge tone="neutral" symbol="–">
                  not configured
                </Badge>
              )}
            </p>
            <p className="hint mt-1">
              Saved keys are never sent back to the browser or stored in browser storage.
              A newly typed key passes through this form to reach the system keychain.
            </p>
          </div>

          <label className="mt-4 flex cursor-pointer select-none items-start gap-3 text-base text-ink">
            <input
              type="checkbox"
              checked={polish}
              onChange={(event) => setPolish(event.target.checked)}
              className="mt-1 h-5 w-5 rounded-control border-line accent-[var(--primary)]"
            />
            <span>
              Polish handoffs with the model
              <span className="hint mt-0.5 block">
                Off by default. This is the only feature that would send handoff text to the model —
                every other step stays deterministic.
              </span>
            </span>
          </label>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? "Saving…" : "Save settings"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={testConnection}
              disabled={testing || saving}
            >
              {testing ? "Testing…" : "Test connection"}
            </button>
            {saved && (
              <span className="text-base text-ok">
                <span aria-hidden="true" className="mr-1 font-bold">
                  ✓
                </span>
                Saved.
              </span>
            )}
          </div>

          <div className="mt-3 space-y-3">
            <ErrorBanner message={saveError} />
            <ErrorBanner message={testError} />
            {testResult && (
              <Notice tone={testResult.ok ? "ok" : "warn"}>
                <span className="font-bold">{testResult.ok ? "✓ Connected" : "✗ Not connected"}:</span>{" "}
                {testResult.message}
                {!testResult.ok && (
                  <span className="hint mt-1 block">
                    Common causes: the model name is missing or misspelled, the endpoint is wrong or
                    unreachable, or the server returned an empty response.
                  </span>
                )}
              </Notice>
            )}
          </div>
        </Card>
      </form>

      <Card title="Session token vs provider key" subtitle="Two different secrets — do not mix them up.">
        <div className="space-y-3 text-base text-ink">
          <div className="rounded-card border border-line bg-surface-alt px-4 py-3">
            <p className="font-bold">Session token (this dashboard)</p>
            <p className="hint mt-1">
              Proves this browser may talk to your local <code className="kbd">cb serve</code> API.
              Stored in localStorage{isStorageAvailable() ? "" : " — unavailable here, so it lives in memory for this tab only"}.
              If it expires, every screen asks for a new one — paste the token from the terminal, or
              reopen the <code className="kbd">…/ui/#token=…</code> URL printed by{" "}
              <code className="kbd">cb serve</code>.
            </p>
          </div>
          <div className="rounded-card border border-line bg-surface-alt px-4 py-3">
            <p className="font-bold">Provider API key (the model)</p>
            <p className="hint mt-1">
              Lets the <em>server</em> call Ollama or a hosted API. Saved keys are held in the system
              keychain; new keys are submitted through the hosted API form above.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => requestRelogin("Enter a session token to switch sessions.")}
            >
              Change session token now
            </button>
            <button type="button" className="btn btn-quiet" onClick={() => onNavigate?.("overview")}>
              Back to overview
            </button>
          </div>
        </div>
      </Card>
    </div>
  );
}
