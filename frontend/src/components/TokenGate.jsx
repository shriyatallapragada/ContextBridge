import { useState } from "react";

// Shown whenever no session token is available (or the API rejected the current one).
export default function TokenGate({ message, onAccept }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState(null);

  const submit = (event) => {
    event.preventDefault();
    const clean = value.trim();
    if (!clean) {
      setError("Paste the session token to continue.");
      return;
    }
    setError(null);
    onAccept(clean);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-page px-4 py-10">
      <form onSubmit={submit} className="card w-full max-w-md" style={{ boxShadow: "var(--shadow-pop)" }}>
        <div className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="flex h-9 w-9 items-center justify-center rounded-control bg-sidebar text-sm font-bold text-on-dark"
          >
            CB
          </span>
          <h1 className="text-lg font-bold" style={{ color: "var(--red-chai)" }}>
            ContextBridge dashboard
          </h1>
        </div>

        <p className="mt-3 text-base leading-relaxed text-ink">
          This is the <strong>session token</strong> printed by <code className="kbd">cb serve</code>{" "}
          in your terminal — it only unlocks the local API on this machine. The dashboard URL also
          carries it as <code className="kbd">#token=…</code>, so opening that link is enough.
        </p>
        <p className="hint mt-2">
          Not to be confused with a provider API key: keys for Ollama or hosted APIs live in the
          operating system keyring and never enter this browser.
        </p>

        {message && (
          <p className="mt-3 rounded-control border border-warn bg-warn-bg px-3 py-2 text-base text-warn">
            <span aria-hidden="true" className="mr-1 font-bold">
              ⚠
            </span>
            {message}
          </p>
        )}
        {error && (
          <p className="mt-3 rounded-control border border-danger bg-danger-bg px-3 py-2 text-base text-danger">
            <span aria-hidden="true" className="mr-1 font-bold">
              !
            </span>
            {error}
          </p>
        )}

        <label htmlFor="cb-token" className="label mt-4">
          Session token
        </label>
        <input
          id="cb-token"
          type="text"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="paste token…"
          autoComplete="off"
          spellCheck={false}
          className="field mt-1.5 font-mono"
        />
        <button type="submit" className="btn btn-primary btn-block mt-4">
          Open dashboard
        </button>
        <p className="hint mt-3">
          Expired or rejected? Stop <code className="kbd">cb serve</code>, start it again and use the
          new <code className="kbd">#token=…</code> URL it prints.
        </p>
      </form>
    </div>
  );
}
