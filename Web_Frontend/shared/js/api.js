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
  constructor(status, code, message, errors = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.errors = errors; // {field: ["message", ...]} for validation errors
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
    throw new ApiError(result.response.status, d.error || "ERROR", d.message || "Something went wrong.", d.errors || {});
  }
  return result.data;
}