const state = {
  overview: null,
  users: null,
  workspaces: null,
  admin: null,
  activeTab: "overview",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function cookie(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}

function csrfToken() {
  return cookie("__Host-operly_csrf") || cookie("operly_csrf") || cookie("operly_preauth_csrf") || "";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function readResponse(response) {
  let body = null;
  try { body = await response.json(); } catch { /* no JSON */ }
  if (!response.ok) {
    const detail = body?.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message || detail?.code || body?.message || `Request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || "GET").toUpperCase();
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = csrfToken();
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  return readResponse(response);
}

function setVisible(selector, visible) {
  $(selector)?.classList.toggle("hidden", !visible);
}

function showLogin(message = "") {
  setVisible("#admin-loading", false);
  setVisible("#admin-app", false);
  setVisible("#admin-login", true);
  const error = $("#admin-login-error");
  if (error) {
    error.textContent = message;
    error.classList.toggle("hidden", !message);
  }
  window.setTimeout(() => $("#admin-email")?.focus(), 40);
}

function showApp() {
  setVisible("#admin-loading", false);
  setVisible("#admin-login", false);
  setVisible("#admin-app", true);
  if (state.admin?.user) {
    $("#admin-name").textContent = state.admin.user.display_name || "Admin";
    $("#admin-email-label").textContent = state.admin.user.email || "";
  }
}

function formatNumber(value) {
  const number = Number(value || 0);
  return new Intl.NumberFormat(undefined, { notation: number >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(number);
}

function formatDate(value, withTime = false) {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, withTime
    ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
    : { month: "short", day: "numeric", year: "numeric" }
  ).format(date);
}

function relativeTime(value) {
  if (!value) return "Never active";
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return "Unknown";
  const seconds = Math.max(0, Math.round((Date.now() - time) / 1000));
  if (seconds < 60) return "Just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 60) return `${days}d ago`;
  return formatDate(value);
}

function countryName(code) {
  if (!code) return "Unknown";
  try {
    const names = new Intl.DisplayNames([navigator.language || "en"], { type: "region" });
    return names.of(String(code).toUpperCase()) || String(code).toUpperCase();
  } catch {
    return String(code).toUpperCase();
  }
}

function countryFlag(code) {
  const value = String(code || "").toUpperCase();
  if (!/^[A-Z]{2}$/.test(value)) return "◌";
  return String.fromCodePoint(...[...value].map((letter) => 127397 + letter.charCodeAt(0)));
}

function setMetric(name, value) {
  $$(`[data-metric="${name}"]`).forEach((element) => { element.textContent = formatNumber(value); });
}

function renderMetrics(metrics) {
  Object.entries(metrics || {}).forEach(([name, value]) => setMetric(name, value));
  const users = Number(metrics?.users || 0);
  const verified = Number(metrics?.verified_users || 0);
  const percentage = users ? Math.min(100, (verified / users) * 100) : 0;
  $("#verified-meter").style.width = `${percentage.toFixed(1)}%`;
  $("#verified-copy").textContent = users
    ? `${verified} of ${users} customer accounts are email verified (${percentage.toFixed(1)}%).`
    : "No customer accounts yet.";
}

function renderActivityChart(rows) {
  const target = $("#activity-chart");
  const data = Array.isArray(rows) ? rows : [];
  if (!target || !data.length) return;

  const width = 820;
  const height = 230;
  const left = 34;
  const right = 14;
  const top = 14;
  const bottom = 28;
  const innerWidth = width - left - right;
  const innerHeight = height - top - bottom;
  const maxValue = Math.max(1, ...data.flatMap((row) => [Number(row.signups || 0), Number(row.signins || 0)]));
  const x = (index) => left + (data.length <= 1 ? 0 : (index / (data.length - 1)) * innerWidth);
  const y = (value) => top + innerHeight - (Number(value || 0) / maxValue) * innerHeight;
  const pathFor = (key) => data.map((row, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(row[key]).toFixed(2)}`).join(" ");
  const ticks = [0, .25, .5, .75, 1];
  const labelIndexes = [...new Set([0, 7, 14, 21, data.length - 1].filter((index) => index >= 0 && index < data.length))];

  const grid = ticks.map((fraction) => {
    const yy = top + innerHeight - fraction * innerHeight;
    const label = Math.round(maxValue * fraction);
    return `<line class="chart-grid-line" x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}"></line><text class="chart-axis-label" x="${left - 8}" y="${yy + 3}" text-anchor="end">${label}</text>`;
  }).join("");
  const labels = labelIndexes.map((index) => {
    const date = new Date(`${data[index].date}T00:00:00`);
    const label = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
    return `<text class="chart-axis-label" x="${x(index)}" y="${height - 5}" text-anchor="middle">${escapeHtml(label)}</text>`;
  }).join("");

  target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Signups and sign-ins over the last 30 days">${grid}${labels}<path class="chart-signups" d="${pathFor("signups")}"></path><path class="chart-signins" d="${pathFor("signins")}"></path></svg>`;
}

function renderGeography(geography) {
  const countries = Array.isArray(geography?.countries) ? geography.countries : [];
  const totalViews = Number(geography?.total_views || 0);
  const coverage = Number(geography?.coverage_percent || 0);
  $("#geo-coverage").textContent = totalViews ? `${coverage.toFixed(0)}% geo coverage` : "Collecting data";

  const cloud = $("#geo-cloud");
  const list = $("#country-list");
  if (!countries.length) {
    cloud.innerHTML = "";
    list.innerHTML = `<div class="empty-state">No country data yet. It begins filling as authenticated users open Operly after this deployment.</div>`;
  } else {
    cloud.innerHTML = countries.slice(0, 9).map((item) => `<span class="geo-bubble" title="${escapeHtml(countryName(item.country_code))}"><span>${countryFlag(item.country_code)}</span>${escapeHtml(item.country_code)}<strong>${formatNumber(item.unique_users)}</strong></span>`).join("");
    const maxVisits = Math.max(1, ...countries.map((item) => Number(item.visits || 0)));
    list.innerHTML = countries.slice(0, 10).map((item) => {
      const name = countryName(item.country_code);
      const width = Math.max(3, (Number(item.visits || 0) / maxVisits) * 100);
      return `<div class="country-row"><div class="country-main"><span class="country-flag">${countryFlag(item.country_code)}</span><div class="country-copy"><strong>${escapeHtml(name)}</strong><small>${formatNumber(item.unique_users)} unique users · ${formatNumber(item.visits)} page views</small><div class="country-bar"><i style="width:${width.toFixed(1)}%"></i></div></div></div><span class="country-count">${formatNumber(item.visits)}</span></div>`;
    }).join("");
  }

  const paths = Array.isArray(geography?.top_paths) ? geography.top_paths : [];
  $("#top-paths").innerHTML = paths.length
    ? paths.map((item) => `<div class="path-row"><div><strong title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</strong><small>Authenticated page views</small></div><span class="path-count">${formatNumber(item.views)}</span></div>`).join("")
    : `<div class="empty-state">No product page views recorded yet.</div>`;
}

function renderRecentUsers(users) {
  const target = $("#recent-users");
  const rows = Array.isArray(users) ? users : [];
  target.innerHTML = rows.length
    ? rows.map((user) => `<div class="recent-user"><strong title="${escapeHtml(user.display_name)}">${escapeHtml(user.display_name || "Unnamed user")}</strong><small title="${escapeHtml(user.email)}">${escapeHtml(user.email)}</small><small>${formatDate(user.created_at)}</small><span class="user-state">${user.verified ? "Verified" : "Unverified"}</span></div>`).join("")
    : `<div class="empty-state">No customer signups yet.</div>`;
}

function renderOverview() {
  if (!state.overview) return;
  renderMetrics(state.overview.metrics || {});
  renderActivityChart(state.overview.activity || []);
  renderGeography(state.overview.geography || {});
  renderRecentUsers(state.overview.recent_users || []);
  $("#generated-at").textContent = `Updated ${formatDate(state.overview.generated_at, true)}`;
}

function userMatches(user, query) {
  if (!query) return true;
  const country = countryName(user.country_code);
  const workspaces = (user.workspaces || []).map((workspace) => `${workspace.name} ${workspace.role}`).join(" ");
  return `${user.display_name} ${user.email} ${country} ${workspaces}`.toLowerCase().includes(query.toLowerCase());
}

function renderUsers() {
  const target = $("#users-table");
  if (!target) return;
  const query = $("#user-search")?.value?.trim() || "";
  const users = (state.users || []).filter((user) => userMatches(user, query));
  const header = `<div class="table-head"><span>User</span><span>Status</span><span>Country</span><span>Workspaces</span><span>Joined</span></div>`;
  if (!users.length) {
    target.innerHTML = `${header}<div class="empty-state">No users match this view.</div>`;
    return;
  }
  const rows = users.map((user) => {
    const badges = [
      user.is_admin ? `<span class="badge admin">Platform admin</span>` : "",
      user.verified ? `<span class="badge good">Verified</span>` : `<span class="badge">Unverified</span>`,
    ].filter(Boolean).join("");
    const workspaces = (user.workspaces || []).length
      ? (user.workspaces || []).slice(0, 3).map((workspace) => `<span class="badge" title="${escapeHtml(workspace.role)}">${escapeHtml(workspace.name)}</span>`).join("") + ((user.workspaces || []).length > 3 ? `<span class="badge">+${user.workspaces.length - 3}</span>` : "")
      : `<span class="badge">Personal only</span>`;
    const activeClass = user.active ? "active" : "";
    return `<div class="table-row"><div class="user-cell"><strong>${escapeHtml(user.display_name || "Unnamed user")}</strong><small>${escapeHtml(user.email)}</small><div class="badge-row" style="margin-top:6px">${badges}</div></div><div><span class="status-dot ${activeClass}"><i></i>${user.active ? "Active" : "Disabled"}</span><div class="user-cell"><small>${escapeHtml(relativeTime(user.last_active_at))}</small></div></div><div class="user-cell"><strong>${user.country_code ? `${countryFlag(user.country_code)} ${escapeHtml(countryName(user.country_code))}` : "Unknown"}</strong><small>${escapeHtml(user.country_code || "No geo yet")}</small></div><div class="badge-row">${workspaces}</div><div class="user-cell"><strong>${escapeHtml(formatDate(user.created_at))}</strong></div></div>`;
  }).join("");
  target.innerHTML = `${header}${rows}`;
}

function renderWorkspaces() {
  const target = $("#workspace-grid");
  if (!target) return;
  const workspaces = state.workspaces || [];
  if (!workspaces.length) {
    target.innerHTML = `<div class="panel empty-state">No workspaces exist yet.</div>`;
    return;
  }
  target.innerHTML = workspaces.map((workspace) => {
    const members = (workspace.members || []).slice(0, 8).map((member) => `<div class="member-row"><div><strong>${escapeHtml(member.display_name || "Unknown user")}</strong><small>${escapeHtml(member.email || "")}</small></div><span class="member-role">${escapeHtml(member.role)}</span></div>`).join("");
    const overflow = (workspace.members || []).length > 8 ? `<div class="member-row"><div><small>+${workspace.members.length - 8} more members</small></div></div>` : "";
    return `<article class="workspace-card"><div class="workspace-card-head"><div style="min-width:0"><h3 title="${escapeHtml(workspace.name)}">${escapeHtml(workspace.name)}</h3><div class="slug">/${escapeHtml(workspace.slug || workspace.id)}</div></div><span class="workspace-member-count">${formatNumber(workspace.member_count)} members</span></div><div class="workspace-meta"><span>${escapeHtml(workspace.timezone || "UTC")}</span><span>Created ${escapeHtml(formatDate(workspace.created_at))}</span></div><div class="member-list">${members || `<div class="empty-state">No members</div>`}${overflow}</div></article>`;
  }).join("");
}

async function loadOverview() {
  state.overview = await api("/api/admin/overview");
  renderOverview();
}

async function loadUsers(force = false) {
  if (!state.users || force) state.users = await api("/api/admin/users?limit=500");
  renderUsers();
}

async function loadWorkspaces(force = false) {
  if (!state.workspaces || force) state.workspaces = await api("/api/admin/workspaces?limit=500");
  renderWorkspaces();
}

async function activateTab(tab) {
  state.activeTab = tab;
  $$(".admin-tab").forEach((section) => section.classList.add("hidden"));
  $(`#admin-tab-${tab}`)?.classList.remove("hidden");
  $$('[data-admin-tab]').forEach((button) => button.classList.toggle("active", button.dataset.adminTab === tab && button.classList.contains("nav-item")));
  const titles = { overview: "Overview", users: "Users", workspaces: "Workspaces" };
  $("#admin-page-title").textContent = titles[tab] || "Platform admin";
  if (tab === "users") await loadUsers();
  if (tab === "workspaces") await loadWorkspaces();
}

async function refreshCurrent() {
  const button = $("#admin-refresh");
  if (button) { button.disabled = true; button.textContent = "Refreshing…"; }
  try {
    if (state.activeTab === "overview") await loadOverview();
    if (state.activeTab === "users") await loadUsers(true);
    if (state.activeTab === "workspaces") await loadWorkspaces(true);
  } finally {
    if (button) { button.disabled = false; button.textContent = "Refresh"; }
  }
}

async function adminLogin(email, password) {
  const bootstrap = await api("/api/auth/bootstrap");
  const response = await fetch("/api/auth/login", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": bootstrap.csrf_token,
    },
    body: JSON.stringify({ email, password }),
  });
  await readResponse(response);
  state.admin = await api("/api/admin/session");
}

async function start() {
  try {
    state.admin = await api("/api/admin/session");
    showApp();
    await loadOverview();
  } catch (error) {
    if (error.status === 401) {
      showLogin();
      return;
    }
    if (error.status === 403) {
      showLogin("The signed-in Operly account is not the configured platform administrator. Sign in below with ADMIN_EMAIL.");
      return;
    }
    showLogin(error.message || "The admin console could not be opened.");
  }
}

$("#admin-login-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = $("#admin-login-submit");
  const errorBox = $("#admin-login-error");
  errorBox.classList.add("hidden");
  submit.disabled = true;
  submit.textContent = "Signing in…";
  try {
    await adminLogin($("#admin-email").value.trim(), $("#admin-password").value);
    $("#admin-password").value = "";
    showApp();
    await loadOverview();
  } catch (error) {
    errorBox.textContent = error.status === 403
      ? "Those credentials belong to an Operly account, but it is not the ADMIN_EMAIL account."
      : error.message || "Admin sign-in failed.";
    errorBox.classList.remove("hidden");
  } finally {
    submit.disabled = false;
    submit.textContent = "Open admin dashboard";
  }
});

$("#admin-logout")?.addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST", body: "{}" }); } catch { /* reload clears the view even if the session already expired */ }
  window.location.reload();
});

$("#admin-refresh")?.addEventListener("click", () => refreshCurrent().catch((error) => showLogin(error.message)));
$("#user-search")?.addEventListener("input", renderUsers);

$$('[data-admin-tab]').forEach((button) => {
  button.addEventListener("click", () => activateTab(button.dataset.adminTab).catch((error) => showLogin(error.message)));
});

start();
