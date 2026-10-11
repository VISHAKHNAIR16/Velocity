/**
 * API client: token storage, fetch wrapper, automatic token refresh.
 * Requires config.js (API_BASE_URL) to be loaded first.
 */
const TIMEOUT_MS = 70000; // Render's free tier can take ~1 minute to wake up

/** Token storage (localStorage, as per the project spec). */
const Auth = {
  ACCESS: "velocity_access",
  REFRESH: "velocity_refresh",
  save(tokens) {
    if (tokens.access) localStorage.setItem(this.ACCESS, tokens.access);
    if (tokens.refresh) localStorage.setItem(this.REFRESH, tokens.refresh);
  },
  clear() {
    localStorage.removeItem(this.ACCESS);
    localStorage.removeItem(this.REFRESH);
  },
  get access() { return localStorage.getItem(this.ACCESS); },
  get refresh() { return localStorage.getItem(this.REFRESH); },
  isLoggedIn() { return Boolean(this.refresh); },
};

/** Error thrown for every failed API call (matches the backend error format). */
class ApiError extends Error {
    constructor(status, code, message, errors = {}, retryAfter = null, details = null) {
      super(message);
      this.status = status;
      this.code = code;
      this.errors = errors; // {field: ["message", ...]} for validation errors
      // Seconds the server asked us to wait (decision 24). `null` when it did not
      // say - the caller must not assume "retry immediately".
      this.retryAfter = retryAfter;
      this.isThrottled = status === 429;
      // `details` is the structured, per-row payload some endpoints attach
      // alongside `message` - notably 2.1.4's INSUFFICIENT_STOCK, which lists
      // which items are short and by how much. It is deliberately separate from
      // `errors`: `errors` maps to form fields, while `details` describes rows
      // the user has to look at on the invoice, and folding one into the other
      // loses the distinction the UI needs to highlight the right lines.
      this.details = Array.isArray(details) ? details : null;
    }
  }

/** One raw fetch with timeout. Returns {response, data}; throws ApiError if unreachable. */
async function send(path, method, body, token) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const headers = {};
    const isForm = body instanceof FormData; // file uploads: the browser sets the Content-Type itself
    if (body !== undefined && !isForm) headers["Content-Type"] = "application/json";
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
      signal: controller.signal,
    });
    const data = await response.json().catch(() => ({})); // tolerate empty / non-JSON replies
    return { response, data };
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Cannot reach the server. Check your connection and try again.");
  } finally {
    clearTimeout(timer);
  }
}

/** Exchange the refresh token for a new access token. Shared so parallel calls refresh once. */
let refreshing = null;
function refreshAccessToken() {
  if (!refreshing) {
    refreshing = (async () => {
      const { response, data } = await send("/auth/refresh/", "POST", { refresh: Auth.refresh });
      if (!response.ok) return false;
      Auth.save(data); // the backend rotates refresh tokens, so save both
      return true;
    })().finally(() => { refreshing = null; });
  }
  return refreshing;
}

/**
 * Call the API. Returns parsed JSON, or throws ApiError.
 * options: { method = "GET", body, auth = true }
 */
async function apiRequest(path, { method = "GET", body, auth = true } = {}) {
  let result = await send(path, method, body, auth ? Auth.access : null);

  // Access token expired? Refresh once and retry.
  if (auth && result.response.status === 401 && Auth.refresh) {
    if (await refreshAccessToken()) {
      result = await send(path, method, body, Auth.access);
    }
  }

  // Still unauthorised: the session is over.
  if (auth && result.response.status === 401) {
    Auth.clear();
    window.location.href = "/auth/login.html";
    throw new ApiError(401, "SESSION_EXPIRED", "Your session expired. Please log in again.");
  }

  if (!result.response.ok) {
    const d = result.data;
    // 429 carries `retry_after` in the body and `Retry-After` in the header
    // (decision 24). Read the body first, fall back to the header, and cap it so
    // a hostile or buggy value cannot hang the UI for an hour.
    let retryAfter = null;
    if (result.response.status === 429) {
      const raw = d.retry_after ?? result.response.headers?.get?.("Retry-After");
      const seconds = Number.parseInt(raw, 10);
      retryAfter = Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, 120) : null;
    }
throw new ApiError(
        result.response.status,
        d.error || "ERROR",
        d.message || "Something went wrong.",
        d.errors || {},
        retryAfter,
        d.details ?? null,
      );
  }
  return result.data;
}

/**
 * Retry a call once the throttle window expires (decision 24).
 *
 * Without this the billing screen's only option on a 429 is to show an error,
 * and because `/preview/` drives the totals, the user is left staring at
 * STALE figures that look authoritative. Waiting and retrying keeps the
 * numbers honest.
 *
 * Safe because it re-sends the *same* request. Retrying a non-idempotent call
 * after a 429 is fine specifically because the server refused it before doing
 * any work, so nothing was written.
 *
 * @param {number} attempts how many extra tries (default 1)
 */
async function withRetryOnThrottle(fn, attempts = 1) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await fn();
    } catch (err) {
      if (!err.isThrottled || attempt >= attempts) throw err;
      const waitMs = (err.retryAfter ?? 5) * 1000 + 250; // small jitter margin
      if (typeof console !== "undefined") {
        console.warn(`Rate limited; retrying in ${Math.round(waitMs / 1000)}s`);
      }
      await new Promise((resolve) => setTimeout(resolve, waitMs));
    }
  }
}