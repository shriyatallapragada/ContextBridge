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
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-4">
      <form
        onSubmit={submit}
        className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6 shadow-2xl"
      >
        <h1 className="text-base font-semibold text-zinc-100">ContextBridge dashboard</h1>
        <p className="mt-2 text-sm leading-relaxed text-zinc-400">
          Enter the session token printed by <code className="text-zinc-300">cb serve</code>. The
          dashboard URL also carries it as <code className="text-zinc-300">#token=…</code> — opening
          that URL is enough. The token stays in this browser&apos;s local storage.
        </p>

        {message && (
          <p className="mt-3 rounded-lg border border-amber-800/60 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
            {message}
          </p>
        )}
        {error && (
          <p className="mt-3 rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-xs text-red-200">
            {error}
          </p>
        )}

        <label htmlFor="cb-token" className="mt-4 block text-xs font-medium text-zinc-400">
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
          className="mt-1.5 w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-sm text-zinc-200 outline-none transition focus:border-sky-600"
        />
        <button
          type="submit"
          className="mt-4 w-full rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-sky-500"
        >
          Open dashboard
        </button>
      </form>
    </div>
  );
}
