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

function installDiscordSignIn() {
  [
    ["#login-form", "#google-login-button", "Sign in with Discord"],
    ["#signup-form", "#google-signup-button", "Continue with Discord"]
  ].forEach(([formSelector, anchorSelector, label]) => {
    const form = $(formSelector);
    const anchor = $(anchorSelector);
    if (!form || !anchor || form.querySelector("[data-discord-sign-in]")) return;
    const link = document.createElement("a");
    link.href = "/api/identities/discord/sign-in";
    link.dataset.discordSignIn = "true";
    link.className = "button secondary large";
    link.textContent = label;
    link.setAttribute("role", "button");
    link.style.width = "100%";
    link.style.justifyContent = "center";
    link.style.marginBottom = "12px";
    anchor.insertAdjacentElement("beforebegin", link);
  });

  const discordError = new URLSearchParams(location.search).get("discord_error");
  if (discordError) {
    const target = $("#login-error");
    if (target) {
      target.textContent = discordError;
      target.className = "error";
    }
    history.replaceState(history.state || {}, "", "/login");
  }
}

installDiscordSignIn();
