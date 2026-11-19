// Small shared presentational helpers, kept dependency-free.

export function Spinner({ label = "Loading…" }) {
  return (
    <div className="flex items-center gap-3 text-sm text-zinc-400" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-zinc-600 border-t-sky-400" />
      {label}
    </div>
  );
}

export function ErrorBanner({ message, onRetry }) {
  if (!message) return null;
  return (
    <div className="flex items-start justify-between gap-4 rounded-xl border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-200">
      <p>{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded-lg border border-red-800 px-3 py-1 text-xs font-medium text-red-100 transition hover:bg-red-900/60"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, hint, action }) {
  return (
    <div className="rounded-xl border border-dashed border-zinc-700/80 bg-zinc-900/30 px-6 py-10 text-center">
      <p className="text-sm font-medium text-zinc-300">{title}</p>
      {hint && <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-zinc-500">{hint}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

export function Badge({ children, className = "" }) {
  return (
    <span
      className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${className}`}
    >
      {children}
    </span>
  );
}

export function Card({ title, actions, children, className = "" }) {
  return (
    <section className={`rounded-2xl border border-zinc-800 bg-zinc-900/50 p-5 ${className}`}>
      {(title || actions) && (
        <header className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="text-sm font-semibold tracking-wide text-zinc-200">{title}</h2>}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatNumber(value) {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n.toLocaleString() : "0";
}
