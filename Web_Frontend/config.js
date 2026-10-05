/**
 * Central frontend configuration.
 * Uses the local backend when the page is opened from localhost,
 * and the Render backend everywhere else.
 */
const IS_LOCAL = ["localhost", "127.0.0.1"].includes(window.location.hostname);

const API_BASE_URL = IS_LOCAL
  ? "http://127.0.0.1:8000/api/v1"
  : "https://velocity-eis7.onrender.com/api/v1";