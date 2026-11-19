// Shared fetch mocking for the frontend tests: no real network, no real model.
import { vi } from "vitest";

export function json(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => (body === undefined || body === null ? "" : JSON.stringify(body)),
    json: async () => body,
  };
}

// routes: [{ match: string | (url) => boolean, method?, status?, body }]
// String matches are EXACT on the path (query strings ignored), an optional
// method must match too, first match wins; unmatched calls 404 with a detail.
export function mockFetch(routes) {
  const calls = [];
  const fn = vi.fn(async (url, init = {}) => {
    const target = String(url);
    const method = init.method ?? "GET";
    calls.push({
      url: target,
      method,
      body: init.body ? JSON.parse(init.body) : null,
      headers: init.headers ?? {},
    });
    const path = target.split("?")[0];
    const route = routes.find((entry) => {
      const pathOk =
        typeof entry.match === "function" ? entry.match(target) : path === entry.match;
      const methodOk = !entry.method || entry.method === method;
      return pathOk && methodOk;
    });
    if (!route) return json(404, { detail: `no mock for ${target}` });
    const body = typeof route.body === "function" ? route.body(target, init) : route.body;
    return json(route.status ?? 200, body);
  });
  globalThis.fetch = fn;
  return { calls, fn };
}

export function urls(calls) {
  return calls.map((call) => call.url);
}

export function setToken(token = "test-session-token") {
  window.localStorage.setItem("cb_token", token);
}

export function callsTo(calls, fragment) {
  return calls.filter((call) => call.url.includes(fragment));
}

export const DASHBOARD = {
  project: "Demo project",
  root: "/Users/dev/ContextBridge",
  branch: "main",
  initialized: true,
  north_star: null,
  north_star_updated_at: null,
  tasks: { open: 1, resolved: 2, dismissed: 1, superseded: 1, total: 5 },
  commits: [],
  momentum: { commits: 3, authors: 1, insertions: 10, deletions: 2, files_touched: 4, window: 3 },
};
