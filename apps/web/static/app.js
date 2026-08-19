const state = { me: null, page: "overview", workflow: {}, linkToken: null, authBootstrap: null };

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
  $$(".screen").forEach((el) => el.classList.add("hidden"));
  $(id).classList.remove("hidden");
}

function setMobileNavigation(open) {
  const sidebar = $("#sidebar"), toggle = $("#mobile-nav-toggle"), backdrop = $("#mobile-nav-backdrop");
  if (!sidebar || !toggle || !backdrop) return;
  sidebar.classList.toggle("open", open); backdrop.classList.toggle("open", open);
  toggle.setAttribute("aria-expanded", String(open));
  toggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
  document.body.classList.toggle("mobile-nav-open", open);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = csrfToken(path);
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  const response = await fetch(`/api${path}`, {
    ...options,
    headers,
    credentials: "same-origin"
  });
  if (response.status === 401 && state.me) navigate("/login");
  let body = null;
  try { body = await response.json(); } catch {}
  if (!response.ok) { const detail=body?.detail; const validation=detail?.validation; const items=[...(validation?.initial?.errors||[]),...(validation?.errors||[])].map(item=>{const r=item.resolution;const resolved=r?` [child: ${r.child}; supplied parent: ${r.suppliedParent}; matched page: ${r.matchedPage}; page root found: ${r.pageRootFound}; synthesis attempted: ${r.synthesisAttempted}]`:"";return `${item.stage} ${item.path}: ${item.message}${resolved}`}).join(" · "); const error=new Error((typeof detail==="string"?detail:detail?.message||detail?.code||`Request failed (${response.status})`)+(items?` — ${items}`:"")); error.details=detail; throw error; }
  return body;
}

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
  }).format(new Date(value));
}

function esc(value = "") {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function empty(text) { return `<div class="empty">${esc(text)}</div>`; }

async function enterDashboard() {
  state.me = await api("/me");
  $("#workspace-name").textContent = state.me.tenant.name;
  $("#workspace-avatar").textContent = state.me.tenant.name.slice(0, 1).toUpperCase();
  $("#workspace-role").textContent = state.me.role;
  $("#tenant-kicker").textContent = state.me.tenant.name;
  show("#dashboard");
  if (location.pathname !== "/app") history.replaceState({}, "", "/app");
  await loadWorkspaces();
  await renderPage("overview");
}

async function loadWorkspaces() {
  const rows = await api("/session/workspaces");
  const select = $("#workspace-switch");
  select.replaceChildren(...rows.map((row) => {
    const option = document.createElement("option");
    option.value = row.id; option.textContent = row.name; option.selected = row.current;
    return option;
  }));
}

async function renderPage(page) {
  state.page = page;
  const dock = document.querySelector("#operly-chat-dock");
  if (dock) dock.classList.toggle("page-suppressed", page === "studio" || page === "assistant");
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  const title = {
    overview: "Overview", inbox: "Inbox", tasks: "Tasks", memory: "Business brain",
    approvals: "Approvals", integrations: "Integrations", settings: "Connectors"
  }[page];
  $("#page-title").textContent = title;
  const renderers = { overview, inbox, tasks, memory, approvals, integrations, settings };
  await renderers[page]();
}

async function overview() {
  const data = await api("/dashboard");
  const messages = data.recent_messages.map((m) => `
    <div class="row">
      <div class="avatar ${m.is_bot ? "bot" : ""}">${m.is_bot ? "O" : esc(m.author_name.slice(0,1))}</div>
      <div><strong>${esc(m.author_name)}</strong><p>${esc(m.content)}</p></div>
      <time>${formatDate(m.created_at)}</time>
    </div>`).join("") || empty("Messages from Discord will appear here.");

  $("#content").innerHTML = `
    <section class="welcome">
      <span class="kicker">Today in your business</span>
      <h2>Everything that needs your attention, in one place.</h2>
      <p>OPERLY is turning tenant-scoped conversations into usable business operations.</p>
    </section>
    <section class="stats">
      <article class="stat"><b>${data.stats.messages}</b><span>Messages captured</span></article>
      <article class="stat"><b>${data.stats.open_tasks}</b><span>Open tasks</span></article>
      <article class="stat"><b>${data.stats.memories}</b><span>Business facts</span></article>
      <article class="stat"><b>${data.stats.pending_approvals}</b><span>Pending approvals</span></article>
    </section>
    <section class="grid">
      <div class="panel"><div class="panel-header"><h3>Recent conversations</h3></div><div class="list">${messages}</div></div>
      <div class="panel"><div class="panel-header"><h3>OPERLY control loop</h3></div>
        <div class="flow"><p><b>1</b> Listen to business activity</p><p><b>2</b> Understand isolated context</p><p><b>3</b> Execute approved tools</p><p><b>4</b> Report what matters</p></div>
      </div>
    </section>`;
}

async function inbox(search = "") {
  const rows = await api(`/messages${search ? `?search=${encodeURIComponent(search)}` : ""}`);
  $("#content").innerHTML = `
    <div class="page-head"><div><span class="kicker green">Unified communication</span><h2>Every business conversation</h2></div>
      <input id="message-search" class="search" placeholder="Search messages" value="${esc(search)}"></div>
    <div class="panel"><div class="list">${rows.map((m) => `
      <div class="row"><div class="avatar ${m.is_bot ? "bot" : ""}">${m.is_bot ? "O" : esc(m.author_name.slice(0,1))}</div>
      <div><strong>${esc(m.author_name)}</strong><p>${esc(m.content)}</p><small>Channel ${esc(m.channel_id)}</small></div><time>${formatDate(m.created_at)}</time></div>`).join("") || empty("No messages found.")}</div></div>`;
  let timer;
  $("#message-search").addEventListener("input", (e) => {
    clearTimeout(timer); timer = setTimeout(() => inbox(e.target.value), 250);
  });
}

async function tasks() {
  const rows = await api("/tasks");
  $("#content").innerHTML = `
    <div class="page-head"><div><span class="kicker green">Execution</span><h2>Tasks that move the business</h2></div></div>
    <form id="task-form" class="create"><input id="task-title" placeholder="Create a task…" required><button class="button primary">Add task</button></form>
    <div class="panel"><div class="list">${rows.map((t) => `
      <div class="row task ${t.status === "completed" ? "done" : ""}">
        <button class="check" data-complete="${t.id}" ${t.status === "completed" ? "disabled" : ""}>${t.status === "completed" ? "✓" : ""}</button>
        <div><strong>${esc(t.title)}</strong><p>${t.due_at ? `Due ${formatDate(t.due_at)}` : "No deadline"}</p></div>
        <span class="pill">${esc(t.status)}</span></div>`).join("") || empty("No tasks yet.")}</div></div>`;
  $("#task-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/tasks", { method: "POST", body: JSON.stringify({ title: $("#task-title").value }) });
    await tasks();
  });
  $$("[data-complete]").forEach((button) => button.addEventListener("click", async () => {
    await api(`/tasks/${button.dataset.complete}/complete`, { method: "PATCH" });
    await tasks();
  }));
}

async function memory() {
  const rows = await api("/memories");
  $("#content").innerHTML = `
    <div class="page-head"><div><span class="kicker green">Persistent context</span><h2>Your business brain</h2></div></div>
    <form id="memory-form" class="memory-compose"><h3>Teach OPERLY something important</h3>
      <p>Facts remain inside this business workspace.</p><textarea id="memory-content" placeholder="Refunds over $100 require manager approval." required></textarea>
      <button class="button lime">Store memory</button></form>
    <div class="memory-grid">${rows.map((m) => `<article class="card"><span class="pill">${esc(m.kind)}</span><p>${esc(m.content)}</p><small>${formatDate(m.created_at)}</small></article>`).join("") || empty("No stored business facts yet.")}</div>`;
  $("#memory-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/memories", { method: "POST", body: JSON.stringify({ kind: "fact", content: $("#memory-content").value }) });
    await memory();
  });
}

async function approvals() {
  const rows = await api("/approvals");
  $("#content").innerHTML = `
    <div class="page-head"><div><span class="kicker green">Human-controlled autonomy</span><h2>Review consequential actions</h2></div></div>
    ${rows.map((a) => `<article class="card approval"><div><span class="pill status ${esc(a.status)}">${esc(a.status)}</span><h3>${esc(a.action)}</h3><p>${esc(Object.values(a.details || {}).join(" · ") || "No details")}</p><small>${formatDate(a.created_at)}</small></div>
      ${a.status === "pending" ? `<div class="approval-actions"><button class="button secondary" data-decision="rejected" data-id="${a.id}">Reject</button><button class="button primary" data-decision="approved" data-id="${a.id}">Approve</button></div>` : ""}</article>`).join("") || `<div class="panel">${empty("No approvals waiting.")}</div>`}`;
  $$("[data-decision]").forEach((button) => button.addEventListener("click", async () => {
    await api(`/approvals/${button.dataset.id}`, { method: "PATCH", body: JSON.stringify({ status: button.dataset.decision }) });
    await approvals();
  }));
}

async function integrations() {
  const rows = await api("/integrations");
  $("#content").innerHTML = `
    <div class="page-head"><div><span class="kicker green">One communication layer</span><h2>Connect where customers find you</h2></div></div>
    <div class="integration-grid">${rows.map((i) => `<article class="card"><span class="pill status ${esc(i.status)}">${esc(i.status.replace("_"," "))}</span><h3>${esc(i.label)}</h3><p>${esc(i.detail || (i.status === "coming_soon" ? "Planned after MVP" : "Not connected"))}</p></article>`).join("")}</div>`;
}

async function settings() {
  const connected = await api("/connectors");
  const google = connected.find(item => item.provider === "google");
  const discord = connected.find(item => item.provider === "discord");
  const connectors = [google || {provider:"google",display_name:"Google Workspace",status:"disconnected",health_status:"unknown",capabilities:[]}, discord || {provider:"discord",display_name:"Discord",status:"disconnected",health_status:"unknown",capabilities:[]}];
  const connectorCards = connectors.map((item) => `
    <article class="connector-card">
      <span class="pill status ${esc(item.status)}">${esc(item.status.replaceAll("_", " "))}</span>
      <h3>${esc(item.display_name)}</h3><p>${esc(item.account || "Not connected")}</p>
      <div class="connector-boundary"><strong>Health: ${esc(item.health_status)}</strong><span>${esc((item.capabilities || []).join(" · ") || "No granted capabilities")}</span></div>
      <div class="approval-actions">${item.status === "connected" ? `<button class="button secondary" data-test-connector="${item.id}">Test</button><button class="button secondary" data-disable-connector="${item.id}">Disable</button><button class="button secondary" data-disconnect-connector="${item.id}">Disconnect</button>` : item.provider === "google" ? `<button class="button primary" id="connect-google">Connect Gmail & Calendar</button>` : ""}</div>
    </article>`).join("");
  $("#content").innerHTML = `
    <div class="page-head"><div><span class="kicker green">Business nervous system</span><h2>Connectors & workspace</h2><p>Connectors listen for events and run approved backend actions. They may publish controlled updates into a Solution, but they do not directly redesign or freely mutate its frontend.</p></div></div>
    <section class="connector-grid">${connectorCards}</section>
    <form id="settings-form" class="panel settings">
      <h3>Workspace identity</h3>
      <label>Business name<input id="tenant-name" value="${esc(state.me.tenant.name)}" required></label>
      <label>Timezone<input id="tenant-timezone" value="${esc(state.me.tenant.timezone)}" placeholder="Asia/Kathmandu"></label>
      <p><span class="pill status connected">Tenant isolation active</span></p>
      <button class="button primary">Save settings</button>
    </form>`;
  $("#connect-google")?.addEventListener("click",async()=>{const result=await api("/connectors/google/connect",{method:"POST"});location.href=result.authorization_url});
  $$('[data-test-connector]').forEach(b=>b.addEventListener('click',async()=>{await api(`/connectors/${b.dataset.testConnector}/test`,{method:'POST'});await settings()}));
  $$('[data-disable-connector]').forEach(b=>b.addEventListener('click',async()=>{await api(`/connectors/${b.dataset.disableConnector}/disable`,{method:'POST'});await settings()}));
  $$('[data-disconnect-connector]').forEach(b=>b.addEventListener('click',async()=>{await api(`/connectors/${b.dataset.disconnectConnector}`,{method:'DELETE'});await settings()}));
  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const tenant = await api("/settings/tenant", {
      method: "PATCH",
      body: JSON.stringify({ name: $("#tenant-name").value, timezone: $("#tenant-timezone").value })
    });
    state.me.tenant = tenant;
    $("#workspace-name").textContent = tenant.name;
    $("#tenant-kicker").textContent = tenant.name;
    await settings();
  });
}

// Authentication navigation is initialized by auth.js.
$$("[data-home]").forEach((b) => b.addEventListener("click", () => show("#landing")));
$("#nav").addEventListener("click", (e) => {
  const button = e.target.closest("[data-page]");
  if (button) { setMobileNavigation(false); renderPage(button.dataset.page); }
});
$("#mobile-nav-toggle").addEventListener("click", () => setMobileNavigation(!$("#sidebar").classList.contains("open")));
$("#mobile-nav-backdrop").addEventListener("click", () => setMobileNavigation(false));
document.addEventListener("click", (event) => { if (event.target.closest("#nav [data-page]")) setMobileNavigation(false); }, true);
window.addEventListener("keydown", (event) => { if (event.key === "Escape") setMobileNavigation(false); });
window.addEventListener("resize", () => { if (window.innerWidth > 700) setMobileNavigation(false); });
$("#workspace-switch").addEventListener("change", async (event) => {
  event.target.disabled = true;
  try {
    await api("/session/switch-workspace", { method: "POST", body: JSON.stringify({ tenant_id: event.target.value }) });
    await enterDashboard();
  } catch (error) { alert(error.message); await loadWorkspaces(); }
  finally { event.target.disabled = false; }
});
// Public authentication flows and initialization live in auth.js.
