const state = { me: null, workflow: {}, linkToken: null, authBootstrap: null };

function csrfToken(path = "") {
  const cookies = Object.fromEntries(document.cookie.split(";").map((item) => {
    const [name, ...value] = item.trim().split("=");
    return [name, decodeURIComponent(value.join("="))];
  }).filter(([name]) => name));
  const preauthPath = [
    "/auth/signup", "/auth/login", "/auth/verify-email",
    "/auth/resend-verification", "/auth/forgot-password",
    "/auth/reset-password", "/auth/google"
  ].includes(path);
  if (preauthPath) return cookies.operly_preauth_csrf || cookies["__Host-operly_csrf"] || cookies.operly_csrf || "";
  return cookies["__Host-operly_csrf"] || cookies.operly_csrf || cookies.operly_preauth_csrf || "";
}

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function show(id) {
  $$(".screen").forEach((element) => element.classList.add("hidden"));
  $(id)?.classList.remove("hidden");
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = csrfToken(path);
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  const response = await fetch(`/api${path}`, { ...options, headers, credentials: "same-origin" });
  let body = null;
  try { body = await response.json(); } catch { /* response may not contain JSON */ }
  if (!response.ok) {
    const detail = body?.detail;
    const validation = detail?.validation;
    const items = [...(validation?.initial?.errors || []), ...(validation?.errors || [])]
      .map((item) => `${item.stage} ${item.path}: ${item.message}`)
      .join(" · ");
    const message = typeof detail === "string"
      ? detail
      : detail?.message || detail?.code || `Request failed (${response.status})`;
    const error = new Error(message + (items ? ` — ${items}` : ""));
    error.details = detail;
    throw error;
  }
  return body;
}
