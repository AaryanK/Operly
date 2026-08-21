(() => {
  let mounted = false;
  let currentPage = "home";
  let workspaces = [];
  let me = null;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function esc(value = "") {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[ch]);
  }

  function initials(value = "") {
    const words = String(value).trim().split(/\s+/).filter(Boolean);
    return (words.length > 1 ? words.slice(0, 2).map(x => x[0]).join("") : (words[0] || "W").slice(0, 2)).toUpperCase();
  }

  function asArray(value) { return Array.isArray(value) ? value : []; }
  function asObject(value) { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
  function number(value) { const result = Number(value); return Number.isFinite(result) ? result : 0; }
  function titleCase(value = "") { return String(value).replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase()); }
  function formatTime(value) {
    if (!value) return "";
    try { return new Intl.DateTimeFormat(undefined, {month:"short", day:"numeric", hour:"numeric", minute:"2-digit"}).format(new Date(value)); }
    catch { return ""; }
  }
  function formatMoney(value, currency = "USD") {
    try { return new Intl.NumberFormat(undefined, {style:"currency", currency, maximumFractionDigits:0}).format(number(value)); }
    catch { return `${currency} ${number(value).toLocaleString()}`; }
  }

  function ensureStyles() {
    if (!document.querySelector('link[data-operly-workspace-shell]')) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "/static/workspace-shell.css?v=20260821-shell-v2";
      link.dataset.operlyWorkspaceShell = "1";
      document.head.append(link);
    }
    if (!document.querySelector('link[data-operly-frontend-overhaul]')) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = "/static/frontend-overhaul.css?v=20260821-command-center-v1";
      link.dataset.operlyFrontendOverhaul = "1";
      document.head.append(link);
    }
  }

  function hiddenClick(selector) {
    const node = $(selector);
    if (!node) return false;
    node.click();
    return true;
  }

  function setActive(page) {
    currentPage = page;
    $$(".operly-nav-item").forEach(button => button.classList.toggle("active", button.dataset.shellPage === page));
  }

  function setTitle(title) {
    const node = $("#page-title");
    if (node) node.textContent = title;
  }

  function content() { return $("#content"); }
  function workspaceById(id) { return workspaces.find(item => item.id === id); }
  function currentWorkspace() { return workspaces.find(item => item.current) || workspaces[0]; }

  function loading(label = "Loading workspace…") {
    const target = content();
    if (target) target.innerHTML = `<div class="operly-page-loading"><span></span><strong>${esc(label)}</strong></div>`;
  }

  function showPageError(error, retry) {
    const target = content();
    if (!target) return;
    target.innerHTML = `<section class="op-error-state"><div class="op-error-icon">!</div><h2>That page could not load</h2><p>${esc(error?.message || error || "Unknown error")}</p>${retry ? `<button class="operly-shell-button primary" id="op-page-retry">Try again</button>` : ""}</section>`;
    $("#op-page-retry")?.addEventListener("click", retry);
  }

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
    const rail = $("#operly-workspace-rail");
    if (!rail) return;
    rail.innerHTML = `
      <button class="operly-rail-logo" data-shell-page="operly" title="Ask Operly" aria-label="Ask Operly">O</button>
      <div class="operly-rail-separator"></div>
      ${workspaces.map(workspace => `<button class="operly-rail-item ${workspace.current ? "active" : ""}" data-workspace-id="${esc(workspace.id)}" title="${esc(workspace.name)} — ${esc(workspace.role)}">${esc(initials(workspace.name))}</button>`).join("")}
      <button class="operly-rail-add" id="operly-create-workspace" title="Create workspace" aria-label="Create workspace">+</button>
      <div class="operly-rail-spacer"></div>
      <button class="operly-rail-account" data-shell-page="account" title="My account">${esc(initials(me?.user?.display_name || me?.display_name || "Me"))}</button>`;
    $$('[data-workspace-id]', rail).forEach(button => button.addEventListener("click", () => switchWorkspace(button.dataset.workspaceId)));
    $$('[data-shell-page]', rail).forEach(button => button.addEventListener("click", () => navigateShell(button.dataset.shellPage)));
    $("#operly-create-workspace", rail)?.addEventListener("click", openCreateWorkspace);
  }

  function renderWorkspaceHeader() {
    const current = currentWorkspace();
    const name = current?.name || me?.tenant?.name || "Workspace";
    const role = current?.role || me?.role || "member";
    $("#operly-workspace-title")?.replaceChildren(document.createTextNode(name));
    $("#operly-workspace-role")?.replaceChildren(document.createTextNode(role));
    const userName = me?.user?.display_name || me?.display_name || "Operly user";
    $("#operly-user-name")?.replaceChildren(document.createTextNode(userName));
    $("#operly-user-role")?.replaceChildren(document.createTextNode(`${role} · ${name}`));
    const avatar = $("#operly-user-avatar");
    if (avatar) avatar.textContent = initials(userName);
  }

  function mountShell() {
    const dashboard = $("#dashboard");
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
    $$('[data-shell-page]', nav).forEach(button => button.addEventListener("click", () => navigateShell(button.dataset.shellPage)));
    refreshIdentity().catch(console.error);
  }

  function shellPage(title, eyebrow, subtitle, {actions = "", body = ""} = {}) {
    setTitle(title);
    const target = content();
    if (!target) return;
    target.innerHTML = `<div class="operly-shell-page"><section class="operly-shell-hero"><div><span class="operly-shell-eyebrow">${esc(eyebrow)}</span><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div>${actions ? `<div class="op-hero-actions">${actions}</div>` : ""}</section>${body}</div>`;
  }

  async function renderCRM() {
    setActive("crm");
    loading("Loading CRM…");
    try {
      const [contactsResult, leadsResult, quotesResult, ordersResult] = await Promise.allSettled([
        api("/business/contacts"), api("/business/leads"), api("/business/quotes"), api("/business/orders")
      ]);
      const contacts = contactsResult.status === "fulfilled" ? asArray(contactsResult.value) : [];
      const leads = leadsResult.status === "fulfilled" ? asArray(leadsResult.value) : [];
      const quotes = quotesResult.status === "fulfilled" ? asArray(quotesResult.value) : [];
      const orders = ordersResult.status === "fulfilled" ? asArray(ordersResult.value) : [];
      const openLeads = leads.filter(x => !["won", "lost"].includes(x.stage));
      const pipeline = openLeads.reduce((sum, lead) => sum + number(lead.value), 0);
      const currency = "USD";
      shellPage("CRM", "Business", "Customers, opportunities, quotes and orders—without leaving your workspace.", {
        actions: `<button class="operly-shell-button accent" id="crm-new-contact">+ Contact</button>`,
        body: `
          <section class="op-metric-grid op-metric-grid-four">
            <article class="op-metric"><span>Contacts</span><strong>${contacts.length}</strong><small>People in this workspace</small></article>
            <article class="op-metric"><span>Open leads</span><strong>${openLeads.length}</strong><small>${leads.length - openLeads.length} closed</small></article>
            <article class="op-metric"><span>Pipeline</span><strong>${esc(formatMoney(pipeline, currency))}</strong><small>Open opportunity value</small></article>
            <article class="op-metric"><span>Orders</span><strong>${orders.length}</strong><small>${quotes.length} quotes</small></article>
          </section>
          <section class="op-two-column">
            <article class="operly-shell-card op-section-card">
              <div class="op-section-title"><div><span class="operly-shell-eyebrow">Sales</span><h3>Lead pipeline</h3></div><span class="op-count-pill">${openLeads.length} open</span></div>
              <div class="operly-shell-list">${openLeads.length ? openLeads.slice(0, 14).map(lead => `<div class="operly-shell-row"><div class="op-row-main"><strong>${esc(lead.title || "Untitled lead")}</strong><small>${esc(lead.contact_name || lead.contact_email || "No contact")} · ${esc(titleCase(lead.stage || "new"))}</small></div><div class="operly-shell-actions"><span class="operly-status">${esc(titleCase(lead.stage || "new"))}</span>${number(lead.value) ? `<strong>${esc(formatMoney(lead.value, currency))}</strong>` : ""}</div></div>`).join("") : `<div class="op-zero-state compact"><strong>No open leads</strong><span>Add a contact, then create opportunities as conversations turn into business.</span></div>`}</div>
            </article>
            <article class="operly-shell-card op-section-card">
              <div class="op-section-title"><div><span class="operly-shell-eyebrow">Relationships</span><h3>Contacts</h3></div><span class="op-count-pill">${contacts.length}</span></div>
              <div class="operly-shell-list">${contacts.length ? contacts.slice(0, 14).map(person => `<div class="operly-shell-row"><div class="op-contact-avatar">${esc(initials(person.name || person.email || "?"))}</div><div class="op-row-main"><strong>${esc(person.name || person.email || "Unknown contact")}</strong><small>${esc(person.email || person.phone || person.company || "No contact details")}</small></div><span class="operly-status">${esc(person.status || "active")}</span></div>`).join("") : `<div class="op-zero-state compact"><strong>No contacts yet</strong><span>Start with the first customer, partner, or lead.</span></div>`}</div>
            </article>
          </section>`
      });
      $("#crm-new-contact")?.addEventListener("click", openContactModal);
    } catch (error) { showPageError(error, renderCRM); }
  }

  function openContactModal() {
    const dialog = modal("New contact", "Add a person to this workspace CRM.", `
      <form id="operly-contact-form" class="operly-shell-form">
        <label class="grow">Name<input class="operly-shell-input" name="name" required maxlength="200"></label>
        <label class="grow">Email<input class="operly-shell-input" name="email" type="email" maxlength="320"></label>
        <label class="grow">Company<input class="operly-shell-input" name="company" maxlength="200"></label>
      </form>`, "Add contact");
    $("form", dialog).addEventListener("submit", async event => {
      event.preventDefault();
      const submit = $("[data-modal-submit]", dialog); submit.disabled = true;
      try {
        await api("/business/contacts", {method:"POST", body:JSON.stringify(Object.fromEntries(new FormData(event.currentTarget)))});
        dialog.close(); dialog.remove(); await renderCRM();
      } catch (error) { showModalError(dialog, error); submit.disabled = false; }
    });
  }

  async function renderOperations() {
    setActive("operations");
    loading("Reading operational state…");
    try {
      const [snapshotResult, alertsResult] = await Promise.allSettled([api("/operations/snapshot"), api("/operations/alerts")]);
      if (snapshotResult.status !== "fulfilled") throw snapshotResult.reason;
      const snapshot = asObject(snapshotResult.value);
      const counts = asObject(snapshot.counts);
      const profile = asObject(snapshot.profile);
      const alerts = alertsResult.status === "fulfilled" ? asArray(alertsResult.value) : [];
      const activeAlerts = alerts.filter(item => !item.resolved && item.status !== "resolved");
      const recentMessages = asArray(snapshot.recent_messages);
      const memories = asArray(snapshot.memories);
      const currency = profile.currency || "USD";
      const attention = number(counts.overdue_tasks) + number(counts.stale_leads) + number(counts.low_stock) + number(counts.pending_approvals);
      const profileReady = profile.induction_status === "complete";

      shellPage("Operations", "Workspace intelligence", "A live business pulse: what is moving, what is blocked, and what deserves attention.", {
        actions: `<button class="operly-shell-button" id="operations-brief">Owner brief</button><button class="operly-shell-button primary" id="operations-scan">Run scan</button>`,
        body: `
          ${!profileReady ? `<section class="op-setup-banner"><div><span class="operly-shell-eyebrow">Setup</span><h3>Finish teaching Operly how this business works</h3><p>Your operational signals improve once the business profile is complete.</p></div><button class="operly-shell-button accent" id="operations-complete-profile">Complete profile</button></section>` : ""}
          <section class="op-metric-grid">
            <article class="op-metric ${attention ? "attention" : "healthy"}"><span>Needs attention</span><strong>${attention}</strong><small>${attention ? "Across tasks, leads, stock and approvals" : "No tracked exceptions"}</small></article>
            <article class="op-metric"><span>Pipeline</span><strong>${esc(formatMoney(snapshot.pipeline_value, currency))}</strong><small>${number(counts.stale_leads)} stalled leads</small></article>
            <article class="op-metric"><span>Open tasks</span><strong>${number(counts.open_tasks)}</strong><small>${number(counts.overdue_tasks)} overdue</small></article>
            <article class="op-metric"><span>Customers</span><strong>${number(counts.contacts)}</strong><small>${number(counts.upcoming_appointments)} upcoming appointments</small></article>
            <article class="op-metric"><span>Orders</span><strong>${number(counts.open_orders)}</strong><small>${number(counts.draft_quotes)} draft quotes</small></article>
            <article class="op-metric"><span>Approvals</span><strong>${number(counts.pending_approvals)}</strong><small>Waiting for a human decision</small></article>
          </section>

          <section id="operations-brief-card" class="op-brief-card hidden" aria-live="polite"></section>

          <section class="op-two-column op-operations-grid">
            <article class="operly-shell-card op-section-card op-attention-panel">
              <div class="op-section-title"><div><span class="operly-shell-eyebrow">Now</span><h3>Needs attention</h3></div><span class="op-count-pill ${activeAlerts.length ? "warn" : ""}">${activeAlerts.length}</span></div>
              <div class="op-alert-list">${activeAlerts.length ? activeAlerts.slice(0, 12).map(alert => `<div class="op-alert-row severity-${esc(alert.severity || "medium")}"><div class="op-alert-marker"></div><div class="op-row-main"><div class="op-alert-meta"><span>${esc(titleCase(alert.category || "Operations"))}</span><span>·</span><span>${esc(titleCase(alert.severity || "medium"))}</span></div><strong>${esc(alert.title || "Operational alert")}</strong><p>${esc(alert.description || alert.detail || "This item needs review.")}</p>${alert.recommended_action ? `<small><b>Next:</b> ${esc(alert.recommended_action)}</small>` : ""}</div>${alert.id ? `<button class="operly-shell-button" data-resolve-alert="${esc(alert.id)}">Resolve</button>` : ""}</div>`).join("") : `<div class="op-zero-state"><div class="op-zero-icon">✓</div><strong>Nothing urgent right now</strong><span>Run a scan whenever you want Operly to refresh the operational picture.</span></div>`}</div>
            </article>

            <article class="operly-shell-card op-section-card">
              <div class="op-section-title"><div><span class="operly-shell-eyebrow">Business pulse</span><h3>Across the workspace</h3></div></div>
              <div class="op-pulse-list">
                <div><span>Sales</span><strong>${number(counts.stale_leads) ? `${number(counts.stale_leads)} stale leads` : "Pipeline moving"}</strong><i class="${number(counts.stale_leads) ? "warn" : "ok"}"></i></div>
                <div><span>Execution</span><strong>${number(counts.overdue_tasks) ? `${number(counts.overdue_tasks)} overdue tasks` : `${number(counts.open_tasks)} open tasks`}</strong><i class="${number(counts.overdue_tasks) ? "warn" : "ok"}"></i></div>
                <div><span>Inventory</span><strong>${number(counts.low_stock) ? `${number(counts.low_stock)} low-stock items` : `${number(counts.catalog_items)} catalog items`}</strong><i class="${number(counts.low_stock) ? "warn" : "ok"}"></i></div>
                <div><span>Governance</span><strong>${number(counts.pending_approvals) ? `${number(counts.pending_approvals)} waiting` : "Clear"}</strong><i class="${number(counts.pending_approvals) ? "warn" : "ok"}"></i></div>
              </div>
            </article>
          </section>

          <section class="op-two-column">
            <article class="operly-shell-card op-section-card">
              <div class="op-section-title"><div><span class="operly-shell-eyebrow">Recent</span><h3>Conversations</h3></div></div>
              <div class="op-mini-list">${recentMessages.length ? recentMessages.slice(-8).reverse().map(item => `<div class="op-mini-row"><div class="op-contact-avatar">${esc(initials(item.author || "?"))}</div><div class="op-row-main"><strong>${esc(item.author || "Unknown")}</strong><p>${esc(item.content || "")}</p></div></div>`).join("") : `<div class="op-zero-state compact"><strong>No recent conversations</strong><span>Connected channel activity will appear here.</span></div>`}</div>
            </article>
            <article class="operly-shell-card op-section-card">
              <div class="op-section-title"><div><span class="operly-shell-eyebrow">Context</span><h3>Business memory</h3></div></div>
              <div class="op-mini-list">${memories.length ? memories.slice(0, 8).map(item => `<div class="op-memory-row"><span>${esc(titleCase(item.kind || "fact"))}</span><p>${esc(item.content || "")}</p></div>`).join("") : `<div class="op-zero-state compact"><strong>No stored business context</strong><span>Teach Operly durable facts and policies from Activity or conversation.</span></div>`}</div>
            </article>
          </section>`
      });

      $("#operations-scan")?.addEventListener("click", async event => {
        const button = event.currentTarget; button.disabled = true; button.textContent = "Scanning…";
        try { await api("/operations/scan", {method:"POST"}); await renderOperations(); }
        catch (error) { button.disabled = false; button.textContent = "Run scan"; alert(error.message); }
      });
      $("#operations-brief")?.addEventListener("click", async event => {
        const button = event.currentTarget; const card = $("#operations-brief-card");
        button.disabled = true; button.textContent = "Thinking…";
        card.classList.remove("hidden"); card.innerHTML = `<span class="operly-shell-eyebrow">Owner brief</span><p>Analyzing the workspace…</p>`;
        try {
          const result = await api("/operations/brief", {method:"POST"});
          card.innerHTML = `<div class="op-section-title"><div><span class="operly-shell-eyebrow">Owner brief</span><h3>What matters right now</h3></div></div><p>${esc(result.brief || "No brief was returned.")}</p>`;
        } catch (error) { card.innerHTML = `<span class="operly-shell-eyebrow">Owner brief</span><p>${esc(error.message)}</p>`; }
        finally { button.disabled = false; button.textContent = "Owner brief"; }
      });
      $("#operations-complete-profile")?.addEventListener("click", () => navigateShell("home"));
      $$('[data-resolve-alert]').forEach(button => button.addEventListener("click", async () => {
        button.disabled = true;
        try { await api(`/operations/alerts/${button.dataset.resolveAlert}/resolve`, {method:"PATCH"}); await renderOperations(); }
        catch (error) { button.disabled = false; alert(error.message); }
      }));
    } catch (error) { showPageError(error, renderOperations); }
  }

  async function renderWorkspaceAdmin() {
    setActive("workspace");
    loading("Loading members and roles…");
    try {
      const [membersResult, rolesResult] = await Promise.allSettled([api("/workspace/members"), api("/workspace/roles")]);
      if (membersResult.status !== "fulfilled") throw membersResult.reason;
      if (rolesResult.status !== "fulfilled") throw rolesResult.reason;
      const members = asArray(membersResult.value), roles = asArray(rolesResult.value);
      shellPage("Members & Roles", "Workspace", "Control who belongs here and what each role is allowed to do.", {
        actions: `<button class="operly-shell-button" id="workspace-new-role">+ Role</button><button class="operly-shell-button accent" id="workspace-add-member">+ Member</button>`,
        body: `
          <section class="op-two-column op-admin-grid">
            <article class="operly-shell-card op-section-card">
              <div class="op-section-title"><div><span class="operly-shell-eyebrow">People</span><h3>Members</h3></div><span class="op-count-pill">${members.length}</span></div>
              <div class="operly-shell-list">${members.length ? members.map(member => `<div class="operly-shell-row"><div class="op-contact-avatar">${esc(initials(member.display_name || member.email))}</div><div class="op-row-main"><strong>${esc(member.display_name || member.email)}</strong><small>${esc(member.email)}</small></div><select class="operly-shell-select op-role-select" data-member-role="${esc(member.user_id)}">${roles.map(role => `<option value="${esc(role.key)}" ${role.key === member.role ? "selected" : ""}>${esc(role.name)}</option>`).join("")}</select></div>`).join("") : `<div class="op-zero-state compact"><strong>No members</strong><span>Add another Operly user to collaborate.</span></div>`}</div>
            </article>
            <article class="operly-shell-card op-section-card">
              <div class="op-section-title"><div><span class="operly-shell-eyebrow">Security</span><h3>Roles & permissions</h3></div></div>
              <div class="operly-shell-list">${roles.length ? roles.map(role => `<div class="operly-shell-row op-role-row"><div class="op-row-main"><strong>${esc(role.name)}</strong><small>${esc(role.key)}${role.customized ? " · customized" : ""} · ${asArray(role.permissions).length} permissions</small><div class="operly-permissions">${asArray(role.permissions).slice(0, 7).map(p => `<span class="operly-permission">${esc(p)}</span>`).join("")}${asArray(role.permissions).length > 7 ? `<span class="operly-permission">+${asArray(role.permissions).length - 7}</span>` : ""}</div></div><button class="operly-shell-button" data-edit-role="${esc(role.key)}">Edit</button></div>`).join("") : `<div class="op-zero-state compact"><strong>No roles available</strong></div>`}</div>
            </article>
          </section>`
      });
      $$('[data-member-role]').forEach(select => select.addEventListener("change", async () => {
        select.disabled = true;
        try { await api(`/workspace/members/${select.dataset.memberRole}/role`, {method:"PATCH", body:JSON.stringify({role:select.value})}); }
        catch (error) { alert(error.message); await renderWorkspaceAdmin(); }
        finally { select.disabled = false; }
      }));
      $("#workspace-add-member")?.addEventListener("click", () => openMemberModal(roles));
      $("#workspace-new-role")?.addEventListener("click", () => openRoleModal(roles));
      $$('[data-edit-role]').forEach(button => button.addEventListener("click", () => openRolePermissionsModal(roles, button.dataset.editRole)));
    } catch (error) { showPageError(error, renderWorkspaceAdmin); }
  }

  function openMemberModal(roles) {
    const dialog = modal("Add workspace member", "The person must already have an Operly account.", `
      <form id="operly-member-form" class="operly-shell-form">
        <label class="grow">Email<input class="operly-shell-input" type="email" name="email" required></label>
        <label>Role<select class="operly-shell-select" name="role">${roles.map(role => `<option value="${esc(role.key)}">${esc(role.name)}</option>`).join("")}</select></label>
      </form>`, "Add member");
    $("form", dialog).addEventListener("submit", async event => {
      event.preventDefault(); const submit = $("[data-modal-submit]", dialog); submit.disabled = true;
      try { await api("/workspace/members", {method:"POST", body:JSON.stringify(Object.fromEntries(new FormData(event.currentTarget)))}); dialog.close(); dialog.remove(); await renderWorkspaceAdmin(); }
      catch (error) { showModalError(dialog, error); submit.disabled = false; }
    });
  }

  function openRoleModal(roles) {
    const knownPermissions = [...new Set(roles.flatMap(role => asArray(role.permissions)))].sort();
    const dialog = modal("Create role", "Choose a name and the permissions this role should receive.", `
      <form id="operly-role-form" class="operly-shell-form">
        <label class="grow">Role name<input class="operly-shell-input" name="name" required maxlength="120" placeholder="Travel Agent"></label>
        <label class="grow">Key<input class="operly-shell-input" name="key" maxlength="30" placeholder="travel-agent"></label>
        <fieldset class="op-permission-picker"><legend>Permissions</legend>${knownPermissions.map(permission => `<label><input type="checkbox" name="permission" value="${esc(permission)}"> <span>${esc(permission)}</span></label>`).join("") || `<small>No permission vocabulary is available yet.</small>`}</fieldset>
      </form>`, "Create role");
    $("form", dialog).addEventListener("submit", async event => {
      event.preventDefault(); const submit = $("[data-modal-submit]", dialog); submit.disabled = true;
      const formData = new FormData(event.currentTarget);
      const payload = {name:formData.get("name"), key:formData.get("key") || null, permissions:formData.getAll("permission")};
      try { await api("/workspace/roles", {method:"POST", body:JSON.stringify(payload)}); dialog.close(); dialog.remove(); await renderWorkspaceAdmin(); }
      catch (error) { showModalError(dialog, error); submit.disabled = false; }
    });
  }

  function openRolePermissionsModal(roles, roleKey) {
    const role = roles.find(item => item.key === roleKey);
    if (!role) return;
    const knownPermissions = [...new Set(roles.flatMap(item => asArray(item.permissions)))].sort();
    const selected = new Set(asArray(role.permissions));
    const dialog = modal(`Edit ${role.name}`, "Permission changes are enforced by the Operly harness before tools or data are exposed.", `
      <form id="operly-role-permissions-form" class="operly-shell-form">
        <fieldset class="op-permission-picker"><legend>Permissions</legend>${knownPermissions.map(permission => `<label><input type="checkbox" name="permission" value="${esc(permission)}" ${selected.has(permission) ? "checked" : ""}> <span>${esc(permission)}</span></label>`).join("")}</fieldset>
      </form>`, "Save permissions");
    $("form", dialog).addEventListener("submit", async event => {
      event.preventDefault(); const submit = $("[data-modal-submit]", dialog); submit.disabled = true;
      const permissions = new FormData(event.currentTarget).getAll("permission");
      try { await api(`/workspace/roles/${encodeURIComponent(role.key)}/permissions`, {method:"PUT", body:JSON.stringify({permissions})}); dialog.close(); dialog.remove(); await renderWorkspaceAdmin(); }
      catch (error) { showModalError(dialog, error); submit.disabled = false; }
    });
  }

  async function renderAccess() {
    setActive("access");
    loading("Loading AI access policy…");
    try {
      const [grantsResult, exposureResult] = await Promise.allSettled([api("/access/client-grants"), api("/access/tool-exposure")]);
      const grants = grantsResult.status === "fulfilled" ? asArray(grantsResult.value) : [];
      const exposures = exposureResult.status === "fulfilled" ? asArray(exposureResult.value) : [];
      shellPage("AI & MCP Access", "Security", "External AI clients receive explicit grants; exposed tools still obey workspace permissions.", {
        actions: `<button class="operly-shell-button" id="access-expose-tool">+ Tool policy</button><button class="operly-shell-button accent" id="access-new-client">+ Client grant</button>`,
        body: `
          <section class="op-two-column op-admin-grid">
            <article class="operly-shell-card op-section-card"><div class="op-section-title"><div><span class="operly-shell-eyebrow">Clients</span><h3>Client grants</h3></div><span class="op-count-pill">${grants.length}</span></div><p class="op-section-copy">ChatGPT, Claude, MCP and API clients never inherit blanket workspace access.</p><div class="operly-shell-list">${grants.length ? grants.map(grant => `<div class="operly-shell-row"><div class="op-row-main"><strong>${esc(grant.client_id || "Client")}</strong><small>${esc(asArray(grant.scopes).join(" · ") || "No scopes")}</small></div><div class="operly-shell-actions"><span class="operly-status">${esc(grant.status || "active")}</span>${grant.status === "active" ? `<button class="operly-shell-button danger" data-revoke-grant="${esc(grant.id)}">Revoke</button>` : ""}</div></div>`).join("") : `<div class="op-zero-state compact"><strong>No external client grants</strong><span>Nothing outside Operly can use workspace capabilities through this policy.</span></div>`}</div></article>
            <article class="operly-shell-card op-section-card"><div class="op-section-title"><div><span class="operly-shell-eyebrow">MCP</span><h3>Tool exposure</h3></div><span class="op-count-pill">${exposures.length}</span></div><p class="op-section-copy">A capability can exist internally without being published over MCP.</p><div class="operly-shell-list">${exposures.length ? exposures.map(item => `<div class="operly-shell-row"><div class="op-row-main"><strong>${esc(item.tool_id || "Tool")}</strong><small>${esc(item.surface || "mcp")} · ${esc(item.access_mode || "authenticated")}</small></div><span class="operly-status">${item.exposed ? "exposed" : "hidden"}</span></div>`).join("") : `<div class="op-zero-state compact"><strong>No explicit tool policies</strong><span>Tools remain governed by the default workspace policy.</span></div>`}</div></article>
          </section>`
      });
      $$('[data-revoke-grant]').forEach(button => button.addEventListener("click", async () => { button.disabled = true; try { await api(`/access/client-grants/${button.dataset.revokeGrant}`, {method:"DELETE"}); await renderAccess(); } catch (error) { button.disabled = false; alert(error.message); } }));
      $("#access-new-client")?.addEventListener("click", openClientGrantModal);
      $("#access-expose-tool")?.addEventListener("click", openToolExposureModal);
    } catch (error) { showPageError(error, renderAccess); }
  }

  function openClientGrantModal() {
    const dialog = modal("Create client grant", "Scopes are intersected with the signed-in user’s real workspace permissions.", `
      <form id="operly-client-form" class="operly-shell-form">
        <label class="grow">Client ID<input class="operly-shell-input" name="client_id" required placeholder="chatgpt"></label>
        <label class="grow">Scopes<input class="operly-shell-input" name="scopes" placeholder="crm:read, tasks:read"></label>
      </form>`, "Grant access");
    $("form", dialog).addEventListener("submit", async event => {
      event.preventDefault(); const submit = $("[data-modal-submit]", dialog); submit.disabled = true;
      const data = Object.fromEntries(new FormData(event.currentTarget));
      const scopes = String(data.scopes || "").split(",").map(x => x.trim()).filter(Boolean);
      try { await api("/access/client-grants", {method:"POST", body:JSON.stringify({client_id:data.client_id, scopes, workspace_only:true})}); dialog.close(); dialog.remove(); await renderAccess(); }
      catch (error) { showModalError(dialog, error); submit.disabled = false; }
    });
  }

  function openToolExposureModal() {
    const dialog = modal("MCP tool policy", "Exposure and authorization are separate. Exposing a tool never bypasses user permissions.", `
      <form id="operly-tool-policy-form" class="operly-shell-form">
        <label class="grow">Tool ID<input class="operly-shell-input" name="tool_id" required placeholder="crm.search_leads"></label>
        <label>Access<select class="operly-shell-select" name="access_mode"><option value="authenticated">Authenticated</option><option value="public">Public</option></select></label>
        <label>Exposure<select class="operly-shell-select" name="exposed"><option value="true">Exposed</option><option value="false">Hidden</option></select></label>
      </form>`, "Save policy");
    $("form", dialog).addEventListener("submit", async event => {
      event.preventDefault(); const submit = $("[data-modal-submit]", dialog); submit.disabled = true;
      const data = Object.fromEntries(new FormData(event.currentTarget));
      try { await api("/access/tool-exposure", {method:"PUT", body:JSON.stringify({tool_id:data.tool_id, surface:"mcp", access_mode:data.access_mode, exposed:data.exposed === "true"})}); dialog.close(); dialog.remove(); await renderAccess(); }
      catch (error) { showModalError(dialog, error); submit.disabled = false; }
    });
  }

  async function renderPlugins() {
    setActive("plugins");
    loading("Loading plugins…");
    const connectors = await api("/connectors").catch(() => []);
    const builtins = [
      ["CRM", "Customers, leads, quotes and orders", "Built in", "◎"],
      ["Website", "Website Studio, publishing and rollback", "Built in", "◇"],
      ["Tasks & approvals", "Human-controlled execution and follow-up", "Built in", "✓"],
      ["Operations", "Scans, business state, alerts and owner briefs", "Built in", "▣"],
      ["Operly Intelligence", "One governed intelligence layer across every surface", "Core", "✦"]
    ];
    shellPage("Plugins", "Extend Operly", "Everything Operly can do should arrive as a governed capability—not as a one-off special case.", {
      body: `<section class="op-plugin-grid">${builtins.map(([name, detail, status, icon]) => `<article class="op-plugin-card"><div class="op-plugin-icon">${esc(icon)}</div><div><span class="operly-status">${esc(status)}</span><h3>${esc(name)}</h3><p>${esc(detail)}</p></div></article>`).join("")}${asArray(connectors).map(item => `<article class="op-plugin-card"><div class="op-plugin-icon">↗</div><div><span class="operly-status">${esc(item.status || "connector")}</span><h3>${esc(item.display_name || item.provider || item.label || "Connector")}</h3><p>Workspace connector capability.</p></div></article>`).join("")}</section>`
    });
  }

  async function renderAccount() {
    setActive("account");
    shellPage("My Operly", "Direct identity", "Your Operly identity can participate in many independent workspaces.", {
      body: `<section class="operly-shell-card op-section-card"><div class="op-section-title"><div><span class="operly-shell-eyebrow">Workspaces</span><h3>Your spaces</h3></div></div><div class="operly-shell-list">${workspaces.map(item => `<div class="operly-shell-row"><div class="op-contact-avatar">${esc(initials(item.name))}</div><div class="op-row-main"><strong>${esc(item.name)}</strong><small>Your role: ${esc(item.role)}</small></div>${item.current ? `<span class="operly-status">current</span>` : `<button class="operly-shell-button" data-account-switch="${esc(item.id)}">Open</button>`}</div>`).join("")}</div></section>`
    });
    $$('[data-account-switch]').forEach(button => button.addEventListener("click", () => switchWorkspace(button.dataset.accountSwitch)));
  }

  async function navigateShell(page) {
    setActive(page);
    document.querySelector("#operly-chat-dock")?.classList.toggle("page-suppressed", page === "solutions" || page === "operly");
    try {
      if (page === "home") { if (!hiddenClick('[data-simple-page="home"]')) location.reload(); return; }
      if (page === "presence") { hiddenClick('[data-simple-page="presence"]'); return; }
      if (page === "activity") { hiddenClick('[data-simple-page="activity"]'); return; }
      if (page === "connections") { hiddenClick('[data-page="settings"]'); return; }
      if (page === "operly") { setTitle("Operly"); if (typeof window.renderOperlyAssistant === "function") await window.renderOperlyAssistant(); else hiddenClick("#simple-topbar-ask"); return; }
      if (page === "solutions") {
        setTitle("Solutions");
        if (typeof window.operlyStudio === "function") await window.operlyStudio(); else hiddenClick('[data-simple-page="home"]');
        return;
      }
      if (page === "crm") return renderCRM();
      if (page === "operations") return renderOperations();
      if (page === "workspace") return renderWorkspaceAdmin();
      if (page === "access") return renderAccess();
      if (page === "plugins") return renderPlugins();
      if (page === "account") return renderAccount();
    } catch (error) { showPageError(error, () => navigateShell(page)); }
  }

  function showModalError(dialog, error) {
    let node = $(".op-modal-error", dialog);
    if (!node) { node = document.createElement("div"); node.className = "op-modal-error"; $(".operly-shell-modal-actions", dialog)?.before(node); }
    node.textContent = error?.message || String(error);
  }

  function modal(title, subtitle, body, submitLabel) {
    const dialog = document.createElement("dialog");
    dialog.className = "operly-shell-modal";
    dialog.innerHTML = `<div class="operly-shell-modal-body"><div class="op-modal-heading"><h2>${esc(title)}</h2><button type="button" data-modal-cancel aria-label="Close">×</button></div><p>${esc(subtitle)}</p>${body}<div class="operly-shell-modal-actions"><button class="operly-shell-button" type="button" data-modal-cancel>Cancel</button><button class="operly-shell-button primary" type="button" data-modal-submit>${esc(submitLabel)}</button></div></div>`;
    document.body.append(dialog);
    const form = $("form", dialog);
    $("[data-modal-submit]", dialog)?.addEventListener("click", () => form?.requestSubmit());
    $$('[data-modal-cancel]', dialog).forEach(button => button.addEventListener("click", () => { dialog.close(); dialog.remove(); }));
    dialog.addEventListener("cancel", event => { event.preventDefault(); dialog.close(); dialog.remove(); });
    dialog.showModal();
    requestAnimationFrame(() => $("input,select,textarea", dialog)?.focus());
    return dialog;
  }

  function openCreateWorkspace() {
    const dialog = modal("Create workspace", "A workspace is an independent business or organization security boundary.", `
      <form id="operly-workspace-form" class="operly-shell-form">
        <label class="grow">Workspace name<input class="operly-shell-input" name="name" required maxlength="200" placeholder="NAYSCHOOL"></label>
        <label class="grow">Timezone<input class="operly-shell-input" name="timezone" value="${esc(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC")}" maxlength="100"></label>
      </form>`, "Create workspace");
    $("form", dialog).addEventListener("submit", async event => {
      event.preventDefault(); const submit = $("[data-modal-submit]", dialog); submit.disabled = true;
      try {
        const created = await api("/workspaces", {method:"POST", body:JSON.stringify(Object.fromEntries(new FormData(event.currentTarget)))});
        await api("/session/switch-workspace", {method:"POST", body:JSON.stringify({tenant_id:created.id})});
        location.reload();
      } catch (error) { showModalError(dialog, error); submit.disabled = false; }
    });
  }

  function observeDashboard() {
    ensureStyles();
    const dashboard = $("#dashboard");
    if (!dashboard) return;
    const attempt = () => { if (!dashboard.classList.contains("hidden")) mountShell(); };
    attempt();
    new MutationObserver(attempt).observe(dashboard, {attributes:true, attributeFilter:["class"]});
  }

  window.operlyWorkspaceShell = {navigate: navigateShell, renderOperations, renderCRM, renderWorkspaceAdmin, renderAccess};

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", observeDashboard, {once:true});
  else observeDashboard();
})();
