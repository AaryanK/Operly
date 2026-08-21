(() => {
  let mounted = false;
  let currentPage = "home";
  let workspaces = [];
  let me = null;

  function esc(value = "") {
    return String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[ch]);
  }

  function initials(value = "") {
    const words = String(value).trim().split(/\s+/).filter(Boolean);
    return (words.length > 1 ? words.slice(0,2).map(x => x[0]).join("") : (words[0] || "W").slice(0,2)).toUpperCase();
  }

  function ensureStyles() {
    if (document.querySelector('link[data-operly-workspace-shell]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/static/workspace-shell.css?v=20260821-shell-v1";
    link.dataset.operlyWorkspaceShell = "1";
    document.head.append(link);
  }

  function hiddenClick(selector) {
    const node = document.querySelector(selector);
    if (!node) return false;
    node.click();
    return true;
  }

  function setActive(page) {
    currentPage = page;
    document.querySelectorAll(".operly-nav-item").forEach(button => {
      button.classList.toggle("active", button.dataset.shellPage === page);
    });
  }

  function setTitle(title) {
    const node = document.querySelector("#page-title");
    if (node) node.textContent = title;
  }

  function content() { return document.querySelector("#content"); }

  function workspaceById(id) { return workspaces.find(item => item.id === id); }
  function currentWorkspace() { return workspaces.find(item => item.current) || workspaces[0]; }

  async function refreshIdentity() {
    [me, workspaces] = await Promise.all([api("/me"), api("/session/workspaces")]);
    renderRail();
    renderWorkspaceHeader();
  }

  async function switchWorkspace(id) {
    const workspace = workspaceById(id);
    if (!workspace || workspace.current) return;
    await api("/session/switch-workspace", {method:"POST", body:JSON.stringify({tenant_id:id})});
    location.reload();
  }

  function renderRail() {
    const rail = document.querySelector("#operly-workspace-rail");
    if (!rail) return;
    rail.innerHTML = `
      <button class="operly-rail-logo" data-shell-page="operly" title="Direct message Operly">O</button>
      <div class="operly-rail-separator"></div>
      ${workspaces.map(workspace => `<button class="operly-rail-item ${workspace.current ? "active" : ""}" data-workspace-id="${esc(workspace.id)}" title="${esc(workspace.name)} — ${esc(workspace.role)}">${esc(initials(workspace.name))}</button>`).join("")}
      <button class="operly-rail-add" id="operly-create-workspace" title="Create workspace">+</button>
      <div class="operly-rail-spacer"></div>
      <button class="operly-rail-account" data-shell-page="account" title="My account">${esc(initials(me?.user?.display_name || me?.display_name || "Me"))}</button>`;
    rail.querySelectorAll("[data-workspace-id]").forEach(button => button.addEventListener("click", () => switchWorkspace(button.dataset.workspaceId)));
    rail.querySelectorAll("[data-shell-page]").forEach(button => button.addEventListener("click", () => navigateShell(button.dataset.shellPage)));
    rail.querySelector("#operly-create-workspace")?.addEventListener("click", openCreateWorkspace);
  }

  function renderWorkspaceHeader() {
    const current = currentWorkspace();
    const name = current?.name || me?.tenant?.name || "Workspace";
    const role = current?.role || me?.role || "member";
    document.querySelector("#operly-workspace-title")?.replaceChildren(document.createTextNode(name));
    document.querySelector("#operly-workspace-role")?.replaceChildren(document.createTextNode(role));
    const userName = me?.user?.display_name || me?.display_name || "Operly user";
    document.querySelector("#operly-user-name")?.replaceChildren(document.createTextNode(userName));
    document.querySelector("#operly-user-role")?.replaceChildren(document.createTextNode(`${role} · ${name}`));
    const avatar = document.querySelector("#operly-user-avatar");
    if (avatar) avatar.textContent = initials(userName);
  }

  function mountShell() {
    const dashboard = document.querySelector("#dashboard");
    const appMain = dashboard?.querySelector(".app-main");
    if (!dashboard || !appMain || mounted) return;
    mounted = true;
    ensureStyles();
    dashboard.classList.add("workspace-shell-ready");

    const rail = document.createElement("aside");
    rail.id = "operly-workspace-rail";
    rail.className = "operly-workspace-rail";

    const nav = document.createElement("aside");
    nav.className = "operly-section-nav";
    nav.innerHTML = `
      <div class="operly-workspace-head">
        <button data-shell-page="workspace">
          <strong id="operly-workspace-title">Workspace</strong>
          <small id="operly-workspace-role">member</small>
        </button>
        <span class="operly-head-caret">⌄</span>
      </div>
      <div class="operly-nav-scroll">
        <button class="operly-nav-item active" data-shell-page="home"><span class="operly-nav-icon">⌂</span>Home</button>
        <button class="operly-nav-item" data-shell-page="operly"><span class="operly-nav-icon">✦</span>Operly <span class="operly-nav-pill">AI</span></button>

        <div class="operly-nav-group">Business</div>
        <button class="operly-nav-item" data-shell-page="crm"><span class="operly-nav-icon">◎</span>CRM</button>
        <button class="operly-nav-item" data-shell-page="operations"><span class="operly-nav-icon">▣</span>Operations</button>
        <button class="operly-nav-item" data-shell-page="activity"><span class="operly-nav-icon">◉</span>Activity</button>

        <div class="operly-nav-group">Digital presence</div>
        <button class="operly-nav-item" data-shell-page="presence"><span class="operly-nav-icon">◇</span>Presence</button>
        <button class="operly-nav-item" data-shell-page="solutions"><span class="operly-nav-icon">◈</span>Solutions</button>

        <div class="operly-nav-group">Extend</div>
        <button class="operly-nav-item" data-shell-page="connections"><span class="operly-nav-icon">↗</span>Connections</button>
        <button class="operly-nav-item" data-shell-page="plugins"><span class="operly-nav-icon">⊞</span>Plugins</button>

        <div class="operly-nav-group">Workspace</div>
        <button class="operly-nav-item" data-shell-page="workspace"><span class="operly-nav-icon">♟</span>Members & Roles</button>
        <button class="operly-nav-item" data-shell-page="access"><span class="operly-nav-icon">⌾</span>AI & MCP Access</button>
      </div>
      <div class="operly-nav-footer">
        <div class="operly-user-avatar" id="operly-user-avatar">U</div>
        <div class="operly-user-meta"><strong id="operly-user-name">Operly user</strong><small id="operly-user-role">member</small></div>
        <button class="operly-user-menu" data-shell-page="account" aria-label="Account">⋯</button>
      </div>`;

    dashboard.insertBefore(rail, appMain);
    dashboard.insertBefore(nav, appMain);
    nav.querySelectorAll("[data-shell-page]").forEach(button => button.addEventListener("click", () => navigateShell(button.dataset.shellPage)));
    refreshIdentity().catch(console.error);
  }

  function shellPage(title, eyebrow, subtitle, body, actions = "") {
    setTitle(title);
    const target = content();
    if (!target) return;
    target.innerHTML = `<div class="operly-shell-page"><section class="operly-shell-hero"><div><span class="operly-shell-eyebrow">${esc(eyebrow)}</span><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div>${actions}</section>${body}</div>`;
  }

  async function renderCRM() {
    setActive("crm");
    shellPage("CRM", "Business", "Contacts, leads, quotes, and orders inside this workspace.", `<button class="operly-shell-button accent" id="crm-new-contact">+ Contact</button>`);
    const target = content();
    target.insertAdjacentHTML("beforeend", `<div class="operly-shell-card">Loading CRM…</div>`);
    const [contactsResult, leadsResult, quotesResult, ordersResult] = await Promise.allSettled([
      api("/business/contacts"), api("/business/leads"), api("/business/quotes"), api("/business/orders")
    ]);
    const contacts = contactsResult.status === "fulfilled" ? contactsResult.value : [];
    const leads = leadsResult.status === "fulfilled" ? leadsResult.value : [];
    const quotes = quotesResult.status === "fulfilled" ? quotesResult.value : [];
    const orders = ordersResult.status === "fulfilled" ? ordersResult.value : [];
    target.querySelector(".operly-shell-card")?.remove();
    target.insertAdjacentHTML("beforeend", `
      <section class="operly-shell-grid">
        <article class="operly-shell-card"><span class="operly-shell-eyebrow">Contacts</span><h3>${contacts.length} people</h3><p>Customer and partner relationships.</p></article>
        <article class="operly-shell-card"><span class="operly-shell-eyebrow">Pipeline</span><h3>${leads.length} leads</h3><p>${leads.filter(x=>!["won","lost"].includes(x.stage)).length} currently open.</p></article>
        <article class="operly-shell-card"><span class="operly-shell-eyebrow">Quotes</span><h3>${quotes.length} quotes</h3><p>Workspace proposals and estimates.</p></article>
        <article class="operly-shell-card"><span class="operly-shell-eyebrow">Orders</span><h3>${orders.length} orders</h3><p>Structured business orders.</p></article>
      </section>
      <section class="operly-shell-card"><h3>Lead pipeline</h3><div class="operly-shell-list">${leads.length ? leads.slice(0,12).map(lead => `<div class="operly-shell-row"><div><strong>${esc(lead.title)}</strong><small>${esc(lead.contact_name || lead.contact_email || "No contact")} · ${esc(lead.stage)}</small></div><div class="operly-shell-actions"><span class="operly-status">${esc(lead.stage)}</span>${lead.value ? `<strong>$${Number(lead.value).toLocaleString()}</strong>` : ""}</div></div>`).join("") : `<small>No leads yet.</small>`}</div></section>
      <section class="operly-shell-card"><h3>Contacts</h3><div class="operly-shell-list">${contacts.length ? contacts.slice(0,12).map(person => `<div class="operly-shell-row"><div><strong>${esc(person.name)}</strong><small>${esc(person.email || person.phone || person.company || "No contact details")}</small></div><span class="operly-status">${esc(person.status || "active")}</span></div>`).join("") : `<small>No contacts yet.</small>`}</div></section>`);
    document.querySelector("#crm-new-contact")?.addEventListener("click", openContactModal);
  }

  function openContactModal() {
    const dialog = modal("New contact", "Add a person to this workspace CRM.", `
      <form id="operly-contact-form" class="operly-shell-form">
        <label class="grow">Name<input class="operly-shell-input" name="name" required maxlength="200"></label>
        <label class="grow">Email<input class="operly-shell-input" name="email" type="email" maxlength="320"></label>
        <label class="grow">Company<input class="operly-shell-input" name="company" maxlength="200"></label>
      </form>`, "Add contact");
    dialog.querySelector("form").addEventListener("submit", async event => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(event.currentTarget));
      await api("/business/contacts", {method:"POST", body:JSON.stringify(data)});
      dialog.close(); dialog.remove(); await renderCRM();
    });
  }

  async function renderOperations() {
    setActive("operations");
    shellPage("Operations", "Workspace intelligence", "Operational health, alerts, and the current business snapshot.", `<button class="operly-shell-button primary" id="operations-scan">Run scan</button>`);
    const target = content();
    const [snapshotResult, alertsResult] = await Promise.allSettled([api("/operations/snapshot"), api("/operations/alerts")]);
    const snapshot = snapshotResult.status === "fulfilled" ? snapshotResult.value : {};
    const alerts = alertsResult.status === "fulfilled" && Array.isArray(alertsResult.value) ? alertsResult.value : [];
    target.insertAdjacentHTML("beforeend", `
      <section class="operly-shell-grid">
        ${Object.entries(snapshot || {}).slice(0,8).map(([key,value]) => `<article class="operly-shell-card"><span class="operly-shell-eyebrow">${esc(key.replaceAll("_"," "))}</span><h3>${esc(typeof value === "object" ? JSON.stringify(value) : value)}</h3></article>`).join("") || `<article class="operly-shell-card"><h3>No operational snapshot yet</h3><p>Run a scan to inspect the workspace.</p></article>`}
      </section>
      <section class="operly-shell-card"><h3>Alerts</h3><div class="operly-shell-list">${alerts.length ? alerts.map(alert => `<div class="operly-shell-row"><div><strong>${esc(alert.title || alert.message || "Operational alert")}</strong><small>${esc(alert.detail || alert.severity || "Needs attention")}</small></div>${alert.resolved ? `<span class="operly-status">resolved</span>` : `<button class="operly-shell-button" data-resolve-alert="${esc(alert.id)}">Resolve</button>`}</div>`).join("") : `<small>No active alerts.</small>`}</div></section>`);
    document.querySelector("#operations-scan")?.addEventListener("click", async () => { await api("/operations/scan", {method:"POST"}); await renderOperations(); });
    document.querySelectorAll("[data-resolve-alert]").forEach(button => button.addEventListener("click", async () => { await api(`/operations/alerts/${button.dataset.resolveAlert}/resolve`, {method:"PATCH"}); await renderOperations(); }));
  }

  async function renderWorkspaceAdmin() {
    setActive("workspace");
    shellPage("Members & Roles", "Workspace", "Manage who belongs here and what each role can do.", `<button class="operly-shell-button accent" id="workspace-add-member">+ Add member</button>`);
    const target = content();
    const [membersResult, rolesResult] = await Promise.allSettled([api("/workspace/members"), api("/workspace/roles")]);
    const members = membersResult.status === "fulfilled" ? membersResult.value : [];
    const roles = rolesResult.status === "fulfilled" ? rolesResult.value : [];
    target.insertAdjacentHTML("beforeend", `
      <section class="operly-shell-card"><h3>Members</h3><div class="operly-shell-list">${members.map(member => `<div class="operly-shell-row"><div><strong>${esc(member.display_name || member.email)}</strong><small>${esc(member.email)}</small></div><select class="operly-shell-select" data-member-role="${esc(member.user_id)}">${roles.map(role => `<option value="${esc(role.key)}" ${role.key === member.role ? "selected" : ""}>${esc(role.name)}</option>`).join("")}</select></div>`).join("") || `<small>No members.</small>`}</div></section>
      <section class="operly-shell-card"><div class="operly-shell-row"><div><h3>Roles & permissions</h3><small>These permissions are enforced by the Operly harness before the model sees tools or data.</small></div><button class="operly-shell-button" id="workspace-new-role">+ Role</button></div><div class="operly-shell-list" style="margin-top:12px">${roles.map(role => `<div class="operly-shell-row"><div><strong>${esc(role.name)}</strong><small>${esc(role.key)}${role.customized ? " · customized" : ""}</small><div class="operly-permissions">${role.permissions.slice(0,14).map(p => `<span class="operly-permission">${esc(p)}</span>`).join("")}${role.permissions.length > 14 ? `<span class="operly-permission">+${role.permissions.length-14}</span>` : ""}</div></div><span class="operly-status">${role.permissions.length} permissions</span></div>`).join("")}</div></section>`);
    document.querySelectorAll("[data-member-role]").forEach(select => select.addEventListener("change", async () => {
      try { await api(`/workspace/members/${select.dataset.memberRole}/role`, {method:"PATCH", body:JSON.stringify({role:select.value})}); }
      catch (error) { alert(error.message); await renderWorkspaceAdmin(); }
    }));
    document.querySelector("#workspace-add-member")?.addEventListener("click", () => openMemberModal(roles));
    document.querySelector("#workspace-new-role")?.addEventListener("click", openRoleModal);
  }

  function openMemberModal(roles) {
    const dialog = modal("Add workspace member", "The person must already have an Operly account for now.", `
      <form id="operly-member-form" class="operly-shell-form">
        <label class="grow">Email<input class="operly-shell-input" type="email" name="email" required></label>
        <label>Role<select class="operly-shell-select" name="role">${roles.map(role => `<option value="${esc(role.key)}">${esc(role.name)}</option>`).join("")}</select></label>
      </form>`, "Add member");
    dialog.querySelector("form").addEventListener("submit", async event => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(event.currentTarget));
      try { await api("/workspace/members", {method:"POST", body:JSON.stringify(data)}); dialog.close(); dialog.remove(); await renderWorkspaceAdmin(); }
      catch (error) { alert(error.message); }
    });
  }

  function openRoleModal() {
    const dialog = modal("Create role", "Start with a role name. Fine-grained permission editing will use the workspace permission matrix.", `
      <form id="operly-role-form" class="operly-shell-form">
        <label class="grow">Role name<input class="operly-shell-input" name="name" required maxlength="120" placeholder="Travel Agent"></label>
        <label class="grow">Key<input class="operly-shell-input" name="key" maxlength="30" placeholder="travel-agent"></label>
      </form>`, "Create role");
    dialog.querySelector("form").addEventListener("submit", async event => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(event.currentTarget));
      data.permissions = [];
      try { await api("/workspace/roles", {method:"POST", body:JSON.stringify(data)}); dialog.close(); dialog.remove(); await renderWorkspaceAdmin(); }
      catch (error) { alert(error.message); }
    });
  }

  async function renderAccess() {
    setActive("access");
    shellPage("AI & MCP Access", "Security", "Control which external AI clients can use which workspace capabilities.");
    const target = content();
    const [grantsResult, exposureResult] = await Promise.allSettled([api("/access/client-grants"), api("/access/tool-exposure")]);
    const grants = grantsResult.status === "fulfilled" ? grantsResult.value : [];
    const exposures = exposureResult.status === "fulfilled" ? exposureResult.value : [];
    target.insertAdjacentHTML("beforeend", `
      <section class="operly-shell-card"><div class="operly-shell-row"><div><h3>Client grants</h3><small>ChatGPT, Claude, MCP and API clients get a separate grant from your own user permissions.</small></div><button class="operly-shell-button" id="access-new-client">+ Client grant</button></div><div class="operly-shell-list" style="margin-top:12px">${grants.length ? grants.map(grant => `<div class="operly-shell-row"><div><strong>${esc(grant.client_id)}</strong><small>${esc((grant.scopes || []).join(" · ") || "No scopes")}</small></div><div class="operly-shell-actions"><span class="operly-status">${esc(grant.status)}</span>${grant.status === "active" ? `<button class="operly-shell-button danger" data-revoke-grant="${esc(grant.id)}">Revoke</button>` : ""}</div></div>`).join("") : `<small>No external AI clients have workspace grants.</small>`}</div></section>
      <section class="operly-shell-card"><div class="operly-shell-row"><div><h3>MCP tool exposure</h3><small>A tool can exist inside Operly without being exposed over MCP.</small></div><button class="operly-shell-button" id="access-expose-tool">+ Tool policy</button></div><div class="operly-shell-list" style="margin-top:12px">${exposures.length ? exposures.map(item => `<div class="operly-shell-row"><div><strong>${esc(item.tool_id)}</strong><small>${esc(item.surface)} · ${esc(item.access_mode)}</small></div><span class="operly-status">${item.exposed ? "exposed" : "hidden"}</span></div>`).join("") : `<small>No explicit MCP tool exposure policies yet.</small>`}</div></section>`);
    document.querySelectorAll("[data-revoke-grant]").forEach(button => button.addEventListener("click", async () => { await api(`/access/client-grants/${button.dataset.revokeGrant}`, {method:"DELETE"}); await renderAccess(); }));
    document.querySelector("#access-new-client")?.addEventListener("click", openClientGrantModal);
    document.querySelector("#access-expose-tool")?.addEventListener("click", openToolExposureModal);
  }

  function openClientGrantModal() {
    const dialog = modal("Create client grant", "Scopes are intersected with your real workspace permissions.", `
      <form class="operly-shell-form">
        <label class="grow">Client ID<input class="operly-shell-input" name="client_id" required placeholder="chatgpt"></label>
        <label class="grow">Scopes<input class="operly-shell-input" name="scopes" placeholder="crm:read, tasks:read"></label>
      </form>`, "Grant access");
    dialog.querySelector("form").addEventListener("submit", async event => {
      event.preventDefault(); const data=Object.fromEntries(new FormData(event.currentTarget));
      const scopes=String(data.scopes||"").split(",").map(x=>x.trim()).filter(Boolean);
      await api("/access/client-grants", {method:"POST", body:JSON.stringify({client_id:data.client_id, scopes, workspace_only:true})});
      dialog.close();dialog.remove();await renderAccess();
    });
  }

  function openToolExposureModal() {
    const dialog = modal("MCP tool policy", "Exposure and authorization are separate. Exposing a tool never bypasses user permissions.", `
      <form class="operly-shell-form">
        <label class="grow">Tool ID<input class="operly-shell-input" name="tool_id" required placeholder="crm.search_leads"></label>
        <label>Access<select class="operly-shell-select" name="access_mode"><option value="authenticated">Authenticated</option><option value="public">Public</option></select></label>
        <label>Exposure<select class="operly-shell-select" name="exposed"><option value="true">Exposed</option><option value="false">Hidden</option></select></label>
      </form>`, "Save policy");
    dialog.querySelector("form").addEventListener("submit", async event => {
      event.preventDefault(); const data=Object.fromEntries(new FormData(event.currentTarget));
      await api("/access/tool-exposure", {method:"PUT", body:JSON.stringify({tool_id:data.tool_id, surface:"mcp", access_mode:data.access_mode, exposed:data.exposed==="true"})});
      dialog.close();dialog.remove();await renderAccess();
    });
  }

  async function renderPlugins() {
    setActive("plugins");
    shellPage("Plugins", "Extend Operly", "Capabilities belong to Operly plugins; channels and models use the same governed tools.");
    const target = content();
    const connectors = await api("/connectors").catch(()=>[]);
    const builtins = [
      ["CRM", "Customers, leads, quotes and orders", "Built in"],
      ["Website", "Website Studio and publishing", "Built in"],
      ["Tasks", "Workspace tasks and approvals", "Built in"],
      ["Operations", "Operational scans, plans and business state", "Built in"],
      ["Operly Intelligence", "One governed intelligence layer across every surface", "Core"]
    ];
    target.insertAdjacentHTML("beforeend", `<section class="operly-shell-grid">${builtins.map(([name,detail,status]) => `<article class="operly-shell-card"><span class="operly-status">${esc(status)}</span><h3 style="margin-top:12px">${esc(name)}</h3><p>${esc(detail)}</p></article>`).join("")}${(connectors||[]).map(item => `<article class="operly-shell-card"><span class="operly-status">${esc(item.status || "connector")}</span><h3 style="margin-top:12px">${esc(item.provider || item.label || "Connector")}</h3><p>Workspace connector plugin.</p></article>`).join("")}</section>`);
  }

  async function renderAccount() {
    setActive("account");
    shellPage("My Operly", "Direct identity", "Your Operly identity can participate in many independent workspaces.");
    const target = content();
    target.insertAdjacentHTML("beforeend", `<section class="operly-shell-card"><h3>Workspaces</h3><div class="operly-shell-list">${workspaces.map(item => `<div class="operly-shell-row"><div><strong>${esc(item.name)}</strong><small>Your role: ${esc(item.role)}</small></div>${item.current ? `<span class="operly-status">current</span>` : `<button class="operly-shell-button" data-account-switch="${esc(item.id)}">Open</button>`}</div>`).join("")}</div></section>`);
    document.querySelectorAll("[data-account-switch]").forEach(button=>button.addEventListener("click",()=>switchWorkspace(button.dataset.accountSwitch)));
  }

  async function navigateShell(page) {
    setActive(page);
    if (page === "home") { if (!hiddenClick('[data-simple-page="home"]')) location.reload(); return; }
    if (page === "presence") { hiddenClick('[data-simple-page="presence"]'); return; }
    if (page === "activity") { hiddenClick('[data-simple-page="activity"]'); return; }
    if (page === "connections") { hiddenClick('[data-page="settings"]'); return; }
    if (page === "operly") { if (!hiddenClick("#simple-topbar-ask")) hiddenClick("#dock-toggle"); return; }
    if (page === "solutions") {
      setTitle("Solutions");
      document.querySelector("#operly-chat-dock")?.classList.add("page-suppressed");
      if (typeof window.operlyStudio === "function") await window.operlyStudio();
      else hiddenClick('[data-simple-page="home"]');
      return;
    }
    if (page === "crm") return renderCRM();
    if (page === "operations") return renderOperations();
    if (page === "workspace") return renderWorkspaceAdmin();
    if (page === "access") return renderAccess();
    if (page === "plugins") return renderPlugins();
    if (page === "account") return renderAccount();
  }

  function modal(title, subtitle, body, submitLabel) {
    const dialog = document.createElement("dialog");
    dialog.className = "operly-shell-modal";
    dialog.innerHTML = `<div class="operly-shell-modal-body"><h2>${esc(title)}</h2><p>${esc(subtitle)}</p>${body}<div class="operly-shell-modal-actions"><button class="operly-shell-button" type="button" data-modal-cancel>Cancel</button><button class="operly-shell-button primary" type="submit" form="${dialog.querySelector?.('form')?.id || ''}" data-modal-submit>${esc(submitLabel)}</button></div></div>`;
    document.body.append(dialog);
    const form = dialog.querySelector("form");
    const submit = dialog.querySelector("[data-modal-submit]");
    if (form) submit.addEventListener("click", () => form.requestSubmit());
    dialog.querySelector("[data-modal-cancel]")?.addEventListener("click", () => {dialog.close();dialog.remove();});
    dialog.addEventListener("cancel", () => dialog.remove());
    dialog.showModal();
    return dialog;
  }

  function openCreateWorkspace() {
    const dialog = modal("Create workspace", "A workspace is an independent business or organization boundary, like a Discord server for your digital presence.", `
      <form id="operly-workspace-form" class="operly-shell-form">
        <label class="grow">Workspace name<input class="operly-shell-input" name="name" required maxlength="200" placeholder="NAYSCHOOL"></label>
        <label class="grow">Timezone<input class="operly-shell-input" name="timezone" value="${esc(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC")}" maxlength="100"></label>
      </form>`, "Create workspace");
    dialog.querySelector("form").addEventListener("submit", async event => {
      event.preventDefault();
      const data=Object.fromEntries(new FormData(event.currentTarget));
      try {
        const created=await api("/workspaces", {method:"POST", body:JSON.stringify(data)});
        dialog.close();dialog.remove();
        await api("/session/switch-workspace", {method:"POST", body:JSON.stringify({tenant_id:created.id})});
        location.reload();
      } catch(error) { alert(error.message); }
    });
  }

  function observeDashboard() {
    const dashboard = document.querySelector("#dashboard");
    if (!dashboard) return;
    const attempt = () => {
      if (!dashboard.classList.contains("hidden")) mountShell();
    };
    attempt();
    new MutationObserver(attempt).observe(dashboard, {attributes:true, attributeFilter:["class"]});
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", observeDashboard);
  else observeDashboard();
})();
