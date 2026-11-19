// Fetch wrapper for the local ContextBridge API.
//
// Token resolution: #token=... in the URL fragment wins (it is the newest intent),
// then localStorage (falling back to memory when storage is unavailable). Every
// /v1/* call carries `Authorization: Bearer <token>`.
// A 401 notifies subscribers so the app can ask for a fresh token.
//
// All request paths are origin-absolute ("/v1/..."): with `base: "/ui/"` the app
// can be served from /ui/ in production and /ui/ on the dev server, and absolute
// paths keep API calls out of that base path.

const TOKEN_KEY = "cb_token";

const authListeners = new Set();

// localStorage can be unavailable (private mode, embedded webviews, blocked
// cookies). Keys still work for the page lifetime via this in-memory fallback.
const memoryStore = new Map();
let storageAvailable = null;

function storage() {
  try {
    const probe = "__cb_probe__";
    window.localStorage.setItem(probe, "1");
    window.localStorage.removeItem(probe);
    storageAvailable = true;
    return window.localStorage;
  } catch {
    storageAvailable = false;
    return null;
  }
}

export function isStorageAvailable() {
  if (storageAvailable === null) storage();
  return storageAvailable;
}

function storeGet(key) {
  const local = storage();
  if (local) {
    try {
      return local.getItem(key);
    } catch {
      storageAvailable = false;
    }
  }
  return memoryStore.has(key) ? memoryStore.get(key) : null;
}

function storeSet(key, value) {
  const local = storage();
  if (local) {
    try {
      local.setItem(key, value);
      return;
    } catch {
      storageAvailable = false;
    }
  }
  memoryStore.set(key, value);
}

function storeRemove(key) {
  const local = storage();
  if (local) {
    try {
      local.removeItem(key);
    } catch {
      storageAvailable = false;
    }
  }
  memoryStore.delete(key);
}

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

function hashParams() {
  const hash = window.location.hash;
  if (!hash || hash.length < 2) return new URLSearchParams();
  try {
    return new URLSearchParams(hash.slice(1));
  } catch {
    return new URLSearchParams();
  }
}

function tokenFromHash() {
  const value = hashParams().get("token");
  return value && value.trim() ? value.trim() : null;
}

// Remove the (secret) token from the URL but keep any other fragment state,
// such as #view=..., so navigation survives a refresh.
function stripTokenFromHash() {
  try {
    const params = hashParams();
    params.delete("token");
    const rest = params.toString();
    window.history.replaceState(
      null,
      "",
      window.location.pathname + window.location.search + (rest ? `#${rest}` : "")
    );
  } catch {
    // History may be unavailable in embedded webviews; a stale fragment is harmless.
  }
}

export function resolveToken() {
  const fromUrl = tokenFromHash();
  if (fromUrl) {
    storeSet(TOKEN_KEY, fromUrl);
    stripTokenFromHash();
    return fromUrl;
  }
  const stored = storeGet(TOKEN_KEY);
  return stored && stored.trim() ? stored.trim() : null;
}

export function saveToken(token) {
  const clean = String(token ?? "").trim();
  storeSet(TOKEN_KEY, clean);
  return clean;
}

export function clearToken() {
  storeRemove(TOKEN_KEY);
}

// Drop the current session token and ask for a new one (Settings screen).
export function requestRelogin(message) {
  emitAuthError(message ?? "Enter a session token to continue.");
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

// Always origin-absolute: never let a relative path resolve under /ui/.
function absolute(path) {
  const value = String(path ?? "");
  return value.startsWith("/") ? value : `/${value}`;
}

async function send(path, { method = "GET", body, signal } = {}) {
  const token = requireToken();
  const hasBody = body !== undefined;
  try {
    return await fetch(absolute(path), {
      method,
      headers: hasBody
        ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }
        : { Authorization: `Bearer ${token}` },
      body: hasBody ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (err) {
    if (err && err.name === "AbortError") throw err;
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
