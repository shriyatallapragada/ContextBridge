// Fetch wrapper for the local ContextBridge API.
//
// Token resolution: #token=... in the URL fragment wins (it is the newest intent),
// then localStorage. Every /v1/* call carries `Authorization: Bearer <token>`.
// A 401 notifies subscribers so the app can ask for a fresh token.

const TOKEN_KEY = "cb_token";

const authListeners = new Set();

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function onAuthError(listener) {
  authListeners.add(listener);
  return () => {
    authListeners.delete(listener);
  };
}

function emitAuthError(detail) {
  const message =
    typeof detail === "string" && detail
      ? detail
      : "The session token was rejected. Enter a new one.";
  for (const listener of Array.from(authListeners)) {
    listener(message);
  }
}

function tokenFromHash() {
  const hash = window.location.hash;
  if (!hash || hash.length < 2) return null;
  try {
    const value = new URLSearchParams(hash.slice(1)).get("token");
    return value && value.trim() ? value.trim() : null;
  } catch {
    return null;
  }
}

function stripHash() {
  try {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  } catch {
    // History may be unavailable in embedded webviews; a stale fragment is harmless.
  }
}

export function resolveToken() {
  const fromUrl = tokenFromHash();
  if (fromUrl) {
    try {
      window.localStorage.setItem(TOKEN_KEY, fromUrl);
    } catch {
      // Storage unavailable: still usable for this page load.
    }
    stripHash();
    return fromUrl;
  }
  try {
    const stored = window.localStorage.getItem(TOKEN_KEY);
    if (stored && stored.trim()) return stored.trim();
  } catch {
    // Storage unavailable.
  }
  return null;
}

export function saveToken(token) {
  const clean = String(token ?? "").trim();
  try {
    window.localStorage.setItem(TOKEN_KEY, clean);
  } catch {
    // Storage unavailable.
  }
  return clean;
}

export function clearToken() {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage unavailable.
  }
}

function requireToken() {
  const token = resolveToken();
  if (!token) {
    emitAuthError("Enter the session token to continue.");
    throw new ApiError(401, "A session token is required.");
  }
  return token;
}

function detailOf(data) {
  return data && typeof data === "object" && typeof data.detail === "string"
    ? data.detail
    : null;
}

async function readJson(response) {
  try {
    const text = await response.text();
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  } catch {
    return null;
  }
}

async function send(path, { method = "GET", body } = {}) {
  const token = requireToken();
  const hasBody = body !== undefined;
  try {
    return await fetch(path, {
      method,
      headers: hasBody
        ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }
        : { Authorization: `Bearer ${token}` },
      body: hasBody ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Cannot reach the local API. Run `cb serve`, then reload.");
  }
}

export async function apiRequest(path, options) {
  const response = await send(path, options);
  const data = await readJson(response);
  if (response.status === 401) {
    const detail = detailOf(data);
    emitAuthError(detail);
    throw new ApiError(401, detail || "The session token was rejected.");
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      detailOf(data) || `Request failed with status ${response.status}.`
    );
  }
  return data;
}

// Plain-text responses (the Mermaid export).
export async function apiText(path) {
  const response = await send(path);
  if (response.status === 401) {
    emitAuthError(null);
    throw new ApiError(401, "The session token was rejected.");
  }
  if (!response.ok) {
    throw new ApiError(response.status, `Request failed with status ${response.status}.`);
  }
  return response.text();
}
