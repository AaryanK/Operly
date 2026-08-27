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

async function renderPersonalDiscordConnection() {
  const pane = $("#account-settings-pane");
  if (!pane || !pane.querySelector(".connector-setting") || pane.querySelector("[data-personal-discord-card]")) return;

  let discordIdentity = null;
  try {
    const identities = await api("/identities");
    discordIdentity = Array.isArray(identities) ? identities.find((item) => item.provider === "discord") : null;
  } catch {
    return;
  }

  const googleCard = pane.querySelector(".connector-setting");
  if (!googleCard || pane.querySelector("[data-personal-discord-card]")) return;

  const card = document.createElement("div");
  card.className = "settings-card connector-setting";
  card.dataset.personalDiscordCard = "true";
  card.innerHTML = `
    <div class="connector-icon discord">D</div>
    <div class="connector-copy">
      <h4>Discord</h4>
      <p>${discordIdentity ? (discordIdentity.display_name || "Discord account connected") : "Connect your Discord identity to Personal Operly."}</p>
      <small>${discordIdentity ? "Your Discord DMs can resolve to this same Operly user. Workspace access is still checked separately." : "Used for personal identity resolution in Discord; it does not grant access to servers or Operly workspaces by itself."}</small>
    </div>
    <div class="connector-actions">
      ${discordIdentity
        ? `<span class="connection-status">connected</span><button class="shell-button danger-subtle" data-personal-discord-disconnect="${discordIdentity.id}">Disconnect</button>`
        : `<button class="shell-button primary" data-connect-discord>Connect Discord</button>`}
    </div>`;
  googleCard.insertAdjacentElement("afterend", card);

  card.querySelector("[data-connect-discord]")?.addEventListener("click", (event) => {
    event.currentTarget.disabled = true;
    location.href = "/api/identities/discord/sign-in";
  });

  card.querySelector("[data-personal-discord-disconnect]")?.addEventListener("click", async (event) => {
    if (!confirm("Disconnect this Discord account from your Operly user? Discord server/workspace bindings remain separate.")) return;
    event.currentTarget.disabled = true;
    try {
      await api(`/identities/${event.currentTarget.dataset.personalDiscordDisconnect}`, { method: "DELETE", body: "{}" });
      if (window.operlyPersonal?.openAccountSettings) await window.operlyPersonal.openAccountSettings("connections");
    } catch (error) {
      alert(error.message);
      event.currentTarget.disabled = false;
    }
  });
}

function installPersonalConnectionObserver() {
  const observer = new MutationObserver(() => {
    renderPersonalDiscordConnection().catch(() => {});
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  renderPersonalDiscordConnection().catch(() => {});
}

installDiscordSignIn();
installPersonalConnectionObserver();
