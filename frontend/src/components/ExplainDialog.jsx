import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "../api.js";
import { Badge, ErrorBanner, Modal, Spinner } from "./ui.jsx";

const POLL_MS = 700;

function noticeQuery(target) {
  const params = new URLSearchParams();
  if (target.scope === "file") {
    params.set("scope", "file");
    if (target.path) params.set("path", target.path);
  } else {
    params.set("scope", target.scope);
    if (target.component) params.set("component", target.component);
  }
  return params.toString();
}

function explainBody(target, budgets) {
  const body = { confirm: true, scope: target.scope };
  if (target.path) body.path = target.path;
  if (target.component) body.component = target.component;
  if (budgets.requests) body.max_requests = Number(budgets.requests);
  if (budgets.tokens) body.token_budget = Number(budgets.tokens);
  return body;
}

// The ONLY place the dashboard may start a model call — and only after the
// privacy notice is read and explicitly confirmed by the user.
//
// flow: notice → privacy confirmation (budgets) → POST /v1/explain {confirm}
//       → poll /v1/explain/status → progress / stopped / completed / failed
//       → Resume/Retry re-POSTs (the server resumes from its cache).
export default function ExplainDialog({ target, onClose, onFinished, onOpenSettings }) {
  const [phase, setPhase] = useState("notice"); // notice | confirm | running | done | stopped | failed
  const [notice, setNotice] = useState(null);
  const [noticeError, setNoticeError] = useState(null);
  const [requests, setRequests] = useState("");
  const [tokens, setTokens] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);
  const [status, setStatus] = useState(null);
  const [countdown, setCountdown] = useState(0);

  const pollRef = useRef(0);
  const timerRef = useRef(null);
  const tickRef = useRef(null);

  const stopTimers = useCallback(() => {
    pollRef.current += 1;
    if (timerRef.current) window.clearTimeout(timerRef.current);
    if (tickRef.current) window.clearInterval(tickRef.current);
    timerRef.current = null;
    tickRef.current = null;
  }, []);

  useEffect(() => () => stopTimers(), [stopTimers]);

  const loadNotice = useCallback(async () => {
    setNoticeError(null);
    setPhase("notice");
    try {
      const data = await apiRequest(`/v1/explain/notice?${noticeQuery(target)}`);
      setNotice(data);
      setRequests(data?.requests ?? "");
      setTokens(data?.tokens ?? "");
      setPhase("confirm");
    } catch (err) {
      setNoticeError(err.message);
      setPhase("failed");
    }
  }, [target]);

  useEffect(() => {
    loadNotice();
  }, [loadNotice]);

  const applyStatus = useCallback(
    (data) => {
      setStatus(data);
      if (!data) return;
      if (data.running) {
        setPhase("running");
        if (!tickRef.current && Number(data.retry_in_seconds) > 0) {
          // 1s ticker for a rate-limit countdown, only while one is shown.
          tickRef.current = window.setInterval(() => {
            setCountdown((value) => {
              const next = Math.max(0, value - 1);
              if (next === 0 && tickRef.current) {
                window.clearInterval(tickRef.current);
                tickRef.current = null;
              }
              return next;
            });
          }, 1000);
        }
        return;
      }
      if (Number(data.retry_in_seconds) > 0 || /^Stopped\b/i.test(data.summary ?? "")) {
        setPhase("stopped");
        setCountdown(Number(data.retry_in_seconds));
        if (!tickRef.current) {
          tickRef.current = window.setInterval(() => {
            setCountdown((value) => {
              const next = Math.max(0, value - 1);
              if (next === 0 && tickRef.current) {
                window.clearInterval(tickRef.current);
                tickRef.current = null;
              }
              return next;
            });
          }, 1000);
        }
      } else if (data.error) {
        setPhase("failed");
      } else {
        setPhase("done");
        onFinished?.();
      }
    },
    [onFinished]
  );

  const poll = useCallback(async () => {
    const mySeq = pollRef.current;
    try {
      const data = await apiRequest("/v1/explain/status");
      if (mySeq !== pollRef.current) return;
      applyStatus(data);
      if (data?.running) {
        timerRef.current = window.setTimeout(() => {
          if (mySeq === pollRef.current) poll();
        }, POLL_MS);
      }
    } catch (err) {
      if (mySeq !== pollRef.current) return;
      setStartError(err.message);
      setPhase("failed");
    }
  }, [applyStatus]);

  const start = async () => {
    if (starting) return;
    setStarting(true);
    setStartError(null);
    pollRef.current += 1;
    if (timerRef.current) window.clearTimeout(timerRef.current);
    setPhase("running");
    try {
      const data = await apiRequest("/v1/explain", {
        method: "POST",
        body: explainBody(target, { requests, tokens }),
      });
      // 202 with a status payload, 409 when another run is already active,
      // or an empty body — in every case the status endpoint is the truth.
      if (data && typeof data === "object" && "running" in data) applyStatus(data);
      await poll();
    } catch (err) {
      if (err.status === 409) {
        // Already running — follow it instead of failing.
        await poll();
      } else {
        setStartError(err.message);
        setPhase(notice ? "confirm" : "failed");
      }
    } finally {
      setStarting(false);
    }
  };

  const title = `Explain ${target.label ?? target.path ?? "the project"}`;
  const running = phase === "running" || starting;
  const messages = status?.messages ?? [];
  const warnings = status?.warnings ?? [];

  return (
    <Modal title={title} onClose={running ? undefined : onClose}>
      <div className="space-y-4">
        {phase === "notice" && <Spinner label="Preparing the privacy notice…" />}

        {(phase === "failed" && noticeError && !notice) || noticeError ? (
          <div className="space-y-3">
            <ErrorBanner message={noticeError} />
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn btn-secondary" onClick={loadNotice}>Retry</button>
              <button type="button" className="btn btn-secondary" onClick={onOpenSettings}>Open Settings</button>
              <button type="button" className="btn btn-secondary" onClick={onClose}>Close</button>
            </div>
          </div>
        ) : null}

        {notice && (phase === "confirm" || phase === "stopped" || phase === "failed") && (
          <div className="space-y-4">
            <div className="rounded-card border border-warn bg-warn-bg px-4 py-3 text-warn">
              <h3 className="text-base font-bold">
                <span aria-hidden="true" className="mr-1">
                  ⚠
                </span>
                A model request is about to be sent — confirm to continue
              </h3>
              <p className="mt-1.5 leading-relaxed">{notice.text}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Badge tone="warn" symbol="↗">
                  up to {notice.requests ?? "?"} requests
                </Badge>
                {notice.tokens != null && (
                  <Badge tone="warn" symbol="∑">
                    up to {Number(notice.tokens).toLocaleString()} reserved tokens
                  </Badge>
                )}
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="cb-explain-requests">
                  Max requests
                </label>
                <input
                  id="cb-explain-requests"
                  type="number"
                  min={1}
                  className="field mt-1.5"
                  value={requests}
                  onChange={(event) => setRequests(event.target.value)}
                />
              </div>
              <div>
                <label className="label" htmlFor="cb-explain-tokens">
                  Token budget
                </label>
                <input
                  id="cb-explain-tokens"
                  type="number"
                  min={500}
                  step={500}
                  className="field mt-1.5"
                  value={tokens}
                  onChange={(event) => setTokens(event.target.value)}
                />
              </div>
            </div>

            <p className="hint">
              Until you press the button below, no model is contacted — opening or refreshing the
              graph makes zero explain calls.
            </p>

            <ErrorBanner message={startError} />

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="btn btn-primary"
                onClick={start}
                disabled={starting || (phase === "stopped" && countdown > 0)}
              >
                {phase === "stopped"
                  ? countdown > 0
                    ? `Resume in ${countdown}s`
                    : "Resume from cache"
                  : starting
                    ? "Starting…"
                    : "Yes, explain it"}
              </button>
              <button type="button" className="btn btn-secondary" onClick={onClose} disabled={starting}>
                Cancel — do not send
              </button>
            </div>

            {phase === "stopped" && (
              <p className="hint">
                The provider rate-limited us, so the run stopped early. Progress so far is cached —
                Resuming repeats only the missing requests.
              </p>
            )}
          </div>
        )}

        {(phase === "running" || phase === "done") && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              {phase === "running" ? (
                <Badge tone="info" symbol="▶">
                  Explaining…
                </Badge>
              ) : (
                <Badge tone="ok" symbol="✓">
                  Completed
                </Badge>
              )}
              {typeof status?.requests_used === "number" && (
                <Badge tone="neutral">
                  {status.requests_used}
                  {status.requests_budget ? ` / ${status.requests_budget}` : ""} requests
                </Badge>
              )}
              {typeof status?.cache_hits === "number" && status.cache_hits > 0 && (
                <Badge tone="accent" symbol="♻">
                  {status.cache_hits} cache hit{status.cache_hits === 1 ? "" : "s"} reused
                </Badge>
              )}
              {typeof status?.tokens_reserved === "number" && (
                <Badge tone="neutral">≈{Number(status.tokens_reserved).toLocaleString()} tokens reserved</Badge>
              )}
            </div>

            {phase === "running" && <Spinner label="Working — progress appears below…" />}

            {status?.summary && (
              <p className="rounded-card border border-ok bg-ok-bg px-4 py-3 text-base text-ok">
                {status.summary}
              </p>
            )}

            {messages.length > 0 && (
              <ul className="max-h-56 space-y-1 overflow-y-auto thin-scroll rounded-card border border-line bg-surface-alt px-4 py-3">
                {messages.map((message, index) => (
                  <li key={index} className="flex gap-2 text-base text-ink">
                    <span className="shrink-0 font-mono text-sm text-ink-muted">{message.at ?? ""}</span>
                    <span>{message.text ?? String(message)}</span>
                  </li>
                ))}
              </ul>
            )}

            {warnings.length > 0 && (
              <ul className="list-disc space-y-1 pl-5 text-base text-warn">
                {warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}

            <div className="flex flex-wrap gap-2">
              {phase === "running" ? (
                <button type="button" className="btn btn-secondary" onClick={onClose}>
                  Keep running in background
                </button>
              ) : (
                <>
                  <button type="button" className="btn btn-primary" onClick={onClose}>
                    Done
                  </button>
                  <button type="button" className="btn btn-secondary" onClick={start}>
                    Refresh explanations
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {phase === "failed" && notice && (
          <div className="space-y-3">
            <ErrorBanner message={status?.error || startError || "The explain run failed."} />
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn btn-primary" onClick={start} disabled={starting}>
                Retry (resumes from cache)
              </button>
              <button type="button" className="btn btn-secondary" onClick={onClose}>
                Close
              </button>
            </div>
            {status?.messages?.length > 0 && (
              <ul className="max-h-40 space-y-1 overflow-y-auto thin-scroll text-base text-ink-muted">
                {status.messages.map((message, index) => (
                  <li key={index}>{message.text ?? String(message)}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
