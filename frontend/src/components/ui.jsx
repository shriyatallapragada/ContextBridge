// Small shared presentational helpers, kept dependency-free.

import { useEffect } from "react";

export function Spinner({ label = "Loading…" }) {
  return (
    <div className="flex items-center gap-3 text-base text-ink-muted" role="status">
      <span
        aria-hidden="true"
        className="h-5 w-5 animate-spin rounded-full border-2 border-line border-t-accent"
      />
      {label}
    </div>
  );
}

export function ErrorBanner({ message, onRetry }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className="flex flex-wrap items-start justify-between gap-4 rounded-card border border-danger bg-danger-bg px-4 py-3 text-base text-danger"
    >
      <p className="min-w-0 flex-1">
        <span aria-hidden="true" className="mr-2 font-bold">
          !
        </span>
        {message}
      </p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn btn-secondary shrink-0">
          Retry
        </button>
      )}
    </div>
  );
}

export function Notice({ children, tone = "info" }) {
  if (!children) return null;
  const tones = {
    info: "border-line bg-surface-alt text-ink",
    warn: "border-warn bg-warn-bg text-warn",
    ok: "border-ok bg-ok-bg text-ok",
  };
  return (
    <div className={`rounded-card border px-4 py-3 text-base ${tones[tone] ?? tones.info}`}>
      {children}
    </div>
  );
}

export function EmptyState({ title, hint, action }) {
  return (
    <div className="rounded-card border border-dashed border-line bg-surface-alt/60 px-6 py-10 text-center">
      <p className="text-base font-semibold text-ink">{title}</p>
      {hint && <p className="mx-auto mt-2 max-w-xl text-base leading-relaxed text-ink-muted">{hint}</p>}
      {action && <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  );
}

// Badges always carry a word AND a symbol: colour is never the only signal.
export function Badge({ children, tone = "neutral", symbol, className = "" }) {
  return (
    <span className={`badge badge-${tone} ${className}`}>
      {symbol && (
        <span aria-hidden="true" className="font-bold">
          {symbol}
        </span>
      )}
      {children}
    </span>
  );
}

export function InfoDot({ label, children }) {
  return (
    <span
      className="info-dot"
      role="img"
      aria-label={label}
      title={label}
    >
      ?
      {children ? <span className="sr-only">{children}</span> : null}
    </span>
  );
}

export function Card({ title, subtitle, actions, children, className = "", tight = false }) {
  return (
    <section className={`card ${tight ? "card-tight" : ""} ${className}`}>
      {(title || actions) && (
        <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            {title && <h2 className="card-title">{title}</h2>}
            {subtitle && <p className="hint mt-1">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Modal({ title, onClose, children, footer, labelledBy }) {
  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-chai/40 px-4 py-8"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose?.();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={labelledBy ? undefined : typeof title === "string" ? title : undefined}
        aria-labelledby={labelledBy || undefined}
        className="card max-h-[90vh] w-full max-w-xl overflow-y-auto thin-scroll"
        style={{ boxShadow: "var(--shadow-pop)" }}
      >
        {title && <h2 className="card-title mb-3">{title}</h2>}
        <div className="text-base">{children}</div>
        {footer && <div className="mt-5 flex flex-wrap justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

// Permanent, irreversible action → explicit confirmation is mandatory.
export function ConfirmDialog({ title, message, confirmLabel = "Delete", onConfirm, onCancel, busy }) {
  return (
    <Modal
      title={title}
      onClose={busy ? undefined : onCancel}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn-danger" onClick={onConfirm} disabled={busy}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </>
      }
    >
      <p className="leading-relaxed">{message}</p>
    </Modal>
  );
}

export async function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through to the legacy path
    }
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

export function downloadText(filename, text) {
  try {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.setTimeout(() => URL.revokeObjectURL(url), 2000);
    return true;
  } catch {
    return false;
  }
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

// "45 seconds ago" / "3 minutes ago" — used for last test-run ages.
export function formatAge(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n)) return "unknown";
  if (n < 60) return `${Math.max(0, Math.round(n))}s ago`;
  if (n < 3600) return `${Math.round(n / 60)}m ago`;
  if (n < 86400) return `${Math.round(n / 3600)}h ago`;
  return `${Math.round(n / 86400)}d ago`;
}
