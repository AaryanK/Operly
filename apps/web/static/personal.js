(() => {
  const $p = (q, r = document) => r.querySelector(q);
  const $$p = (q, r = document) => [...r.querySelectorAll(q)];
  const escp = (v = "") => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const initialPath = location.pathname;
  const initialWorkspace = initialPath.match(/^\/channels\/([^/]+)$/)?.[1] || null;
  const initialWantsPersonal = initialPath === "/channels/@me" || initialPath.startsWith("/channels/@me/");
  const shell = {
    conversationId: null,
    busy: false,
    transition: false,
    workspaces: [],
    me: null,
    initialSyncDone: false,
    observer: null,
  };

  function authenticatedScreen() {
    const personal = $p("#personal");
    const dashboard = $p("#dashboard");
    if (personal && !personal.classList.contains("hidden")) return "personal";
    if (dashboard && !dashboard.classList.contains("hidden")) return "workspace";
    return null;
  }

  function initials(name = "") {
    const parts = String(name).trim().split(/\s+/).filter(Boolean);
    return (parts.slice(0, 2).map(part => part[0]).join("") || "O").toUpperCase();
  }

  function setCanonicalPath() {
    if (shell.transition) return;
    const screen = authenticatedScreen();
    if (screen === "personal") {
      if (location.pathname !== "/channels/@me") history.replaceState(history.state || {}, "", "/channels/@me");
      document.title = "Operly · @me";
      return;
    }
    if (screen === "workspace") {
      const current = shell.workspaces.find(item => item.current) || shell.workspaces.find(item => item.id === shell.me?.current_workspace_id);
      if (current && location.pathname !== `/channels/${current.id}`) history.replaceState(history.state || {}, "", `/channels/${current.id}`);
      if (current) document.title = `${current.name} · Operly`;
    }
  }

  async function refreshScopeData() {
    const [workspaces, me] = await Promise.all([
      api("/personal-agent/workspaces"),
      api("/personal-agent/me"),
    ]);
    shell.workspaces = Array.isArray(workspaces) ? workspaces : [];
    shell.me = me || null;
    return {workspaces: shell.workspaces, me: shell.me};
  }

  function workspaceMark(workspace) {
    if (workspace.logo_url) {
      return `<img src="${escp(workspace.logo_url)}" alt="" loading="lazy"><span class="scope-fallback">${escp(initials(workspace.name))}</span>`;
    }
    return `<span class="scope-fallback">${escp(initials(workspace.name))}</span>`;
  }

  function ensureRail() {
    let rail = $p("#operly-scope-rail");
    if (!rail) {
      rail = document.createElement("nav");
      rail.id = "operly-scope-rail";
      rail.className = "operly-scope-rail";
      rail.setAttribute("aria-label", "Operly spaces");
      document.body.appendChild(rail);
    }
    return rail;
  }

  function renderRail() {
    const screen = authenticatedScreen();
    const rail = ensureRail();
    if (!screen) {
      document.body.classList.remove("operly-authenticated-shell");
      rail.classList.add("hidden");
      return;
    }
    document.body.classList.add("operly-authenticated-shell");
    rail.classList.remove("hidden");
    const currentId = shell.workspaces.find(item => item.current)?.id || shell.me?.current_workspace_id;
    rail.innerHTML = `
      <div class="scope-rail-top">
        <button class="scope-button scope-home ${screen === "personal" ? "active" : ""}" data-scope-personal aria-label="Personal Operly" title="Personal Operly">
          <img src="/static/operly-logo.png" alt="">
        </button>
        <span class="scope-divider" aria-hidden="true"></span>
        <div class="scope-workspaces" aria-label="Workspaces">
          ${shell.workspaces.map(workspace => `
            <button class="scope-button workspace-scope ${screen === "workspace" && workspace.id === currentId ? "active" : ""}" data-scope-workspace="${escp(workspace.id)}" aria-label="${escp(workspace.name)}" title="${escp(workspace.name)}">
              ${workspaceMark(workspace)}
            </button>`).join("")}
        </div>
        <button class="scope-button scope-add" data-create-workspace aria-label="Create workspace" title="Create workspace">+</button>
      </div>
      <div class="scope-rail-bottom">
        <button class="scope-button scope-account" data-account-settings aria-label="Account settings" title="Account settings">${escp(initials(shell.me?.display_name || shell.me?.email || "Me"))}</button>
      </div>`;

    $$p(".scope-button img", rail).forEach(image => image.addEventListener("error", () => image.classList.add("image-failed"), {once:true}));
    $p("[data-scope-personal]", rail)?.addEventListener("click", () => goPersonal());
    $$p("[data-scope-workspace]", rail).forEach(button => button.addEventListener("click", () => goWorkspace(button.dataset.scopeWorkspace)));
    $p("[data-create-workspace]", rail)?.addEventListener("click", () => openCreateWorkspace());
    $p("[data-account-settings]", rail)?.addEventListener("click", () => openAccountSettings("account"));
  }

  async function refreshShell({canonical = true} = {}) {
    if (!authenticatedScreen()) {
      renderRail();
      return;
    }
    try {
      await refreshScopeData();
      renderRail();
      decoratePersonal();
      decorateWorkspace();
      if (canonical) setCanonicalPath();
    } catch (error) {
      console.warn("Operly scope shell refresh failed", error);
    }
  }

  async function goPersonal() {
    if (shell.transition) return;
    shell.transition = true;
    try {
      await refreshScopeData().catch(() => null);
      if (shell.me?.current_workspace_id) {
        await api("/auth/personal-scope", {method:"POST", body:"{}"});
      }
      if (typeof window.enterAuthenticatedPersonal === "function") await window.enterAuthenticatedPersonal();
      else location.assign("/channels/@me");
      await refreshShell({canonical:false});
      history.replaceState(history.state || {}, "", "/channels/@me");
      document.title = "Operly · @me";
    } catch (error) {
      showToast(error.message || "Could not open Personal Operly", "error");
    } finally {
      shell.transition = false;
    }
  }

  async function goWorkspace(workspaceId) {
    if (!workspaceId || shell.transition) return;
    shell.transition = true;
    try {
      await refreshScopeData().catch(() => null);
      if (shell.me?.current_workspace_id !== workspaceId) {
        await api("/auth/switch-workspace", {method:"POST", body:JSON.stringify({tenant_id:workspaceId})});
      }
      if (typeof window.enterAuthenticatedWorkspace === "function") await window.enterAuthenticatedWorkspace();
      else location.assign(`/channels/${workspaceId}`);
      await refreshShell({canonical:false});
      const workspace = shell.workspaces.find(item => item.id === workspaceId);
      history.replaceState(history.state || {}, "", `/channels/${workspaceId}`);
      document.title = `${workspace?.name || "Workspace"} · Operly`;
    } catch (error) {
      showToast(error.message || "Could not open workspace", "error");
    } finally {
      shell.transition = false;
    }
  }

  function add(role, text, meta = "") {
    const host = $p("#personal-messages");
    if (!host) return;
    host.insertAdjacentHTML("beforeend", `
      <article class="personal-message ${role}">
        <div class="personal-message-avatar">${role === "user" ? escp(initials(shell.me?.display_name || "You")) : "✦"}</div>
        <div class="personal-message-body"><strong>${role === "user" ? escp(shell.me?.display_name || "You") : "Operly"}</strong><p>${escp(text)}</p>${meta ? `<small>${escp(meta)}</small>` : ""}</div>
      </article>`);
    host.scrollTop = host.scrollHeight;
  }

  async function loadConversation(conversationId) {
    const host = $p("#personal-messages");
    if (!host) return;
    host.innerHTML = "";
    shell.conversationId = conversationId || null;
    if (!conversationId) {
      add("assistant", "I’m your private Operly. Ask me to work across your account, connected tools, or any workspace you’re authorized to use.", "Private account scope");
      return;
    }
    try {
      const rows = await api(`/personal-agent/conversations/${encodeURIComponent(conversationId)}/messages`);
      rows.forEach(row => add(row.role === "user" ? "user" : "assistant", row.content));
    } catch (error) {
      add("assistant", error.message, "Conversation unavailable");
    }
  }

  async function loadConversations() {
    const list = $p("#personal-conversation-list");
    if (!list) return;
    try {
      const rows = await api("/personal-agent/conversations");
      list.innerHTML = rows.length ? rows.slice(0, 12).map(row => `
        <button class="dm-history ${row.id === shell.conversationId ? "active" : ""}" data-conversation-id="${escp(row.id)}">
          <span>✦</span><span><b>${escp(row.title || "Operly")}</b><small>${row.updated_at ? escp(new Date(row.updated_at).toLocaleDateString()) : ""}</small></span>
        </button>`).join("") : `<p class="dm-empty">Your conversations with Operly will appear here.</p>`;
      $$p("[data-conversation-id]", list).forEach(button => button.addEventListener("click", async () => {
        await loadConversation(button.dataset.conversationId);
        await loadConversations();
      }));
      if (!shell.conversationId && rows[0]) {
        await loadConversation(rows[0].id);
        await loadConversations();
      }
    } catch (error) {
      list.innerHTML = `<p class="dm-empty">${escp(error.message)}</p>`;
    }
  }

  async function loadWorkspaces() {
    const select = $p("#personal-workspace-select");
    if (!select) return;
    const rows = shell.workspaces.length ? shell.workspaces : await api("/personal-agent/workspaces");
    shell.workspaces = rows;
    select.innerHTML = '<option value="">Let Operly resolve the right scope</option>' + rows.map(row => `<option value="${escp(row.id)}">${escp(row.name)} · ${escp(row.role)}</option>`).join("");
  }

  async function send() {
    const input = $p("#personal-input"), button = $p("#personal-send");
    const text = input?.value.trim();
    if (!text || shell.busy) return;
    shell.busy = true;
    if (button) button.disabled = true;
    input.value = "";
    add("user", text);
    const typing = document.createElement("div");
    typing.className = "personal-typing";
    typing.textContent = "Operly is working…";
    $p("#personal-messages")?.appendChild(typing);
    try {
      const result = await api("/personal-agent/chat", {
        method:"POST",
        body:JSON.stringify({
          message:text,
          conversation_id:shell.conversationId,
          selected_workspace_id:$p("#personal-workspace-select")?.value || null,
        }),
      });
      typing.remove();
      shell.conversationId = result.conversation_id || shell.conversationId;
      add("assistant", result.message, result.selected_workspace_id ? "Private request · authorized workspace context" : "Private account scope");
      loadConversations().catch(() => {});
    } catch (error) {
      typing.remove();
      add("assistant", error.message, "No unverified action was claimed");
    } finally {
      shell.busy = false;
      if (button) button.disabled = false;
      input?.focus();
    }
  }

  function decoratePersonal() {
    const root = $p("#personal");
    if (!root || root.classList.contains("hidden")) return;
    $p(".personal-top", root)?.classList.add("legacy-personal-top");
    const side = $p(".personal-side", root);
    if (side && !side.dataset.shellReady) {
      side.dataset.shellReady = "1";
      side.innerHTML = `
        <div class="dm-sidebar-head"><div><small>YOUR SPACE</small><b>Direct Messages</b></div><button data-new-personal-chat aria-label="New conversation" title="New conversation">+</button></div>
        <button class="dm-primary active"><span class="dm-operly-mark">✦</span><span><b>Operly</b><small>Personal AI</small></span></button>
        <div id="personal-conversation-list" class="personal-conversation-list"></div>
        <div class="dm-nav-section"><small>ACCOUNT</small>
          <button data-account-tab="connections">⌁ <span>Connected accounts</span></button>
          <button data-account-tab="security">⌾ <span>Password & security</span></button>
          <button data-account-tab="account">⚙ <span>My account</span></button>
        </div>`;
      $p("[data-new-personal-chat]", side)?.addEventListener("click", async () => {
        shell.conversationId = null;
        await loadConversation(null);
        loadConversations().catch(() => {});
        $p("#personal-input")?.focus();
      });
      $$p("[data-account-tab]", side).forEach(button => button.addEventListener("click", () => openAccountSettings(button.dataset.accountTab)));
    }
    const main = $p(".personal-panel:not(.personal-side)", root);
    const header = main?.querySelector(":scope > header");
    if (header && !header.dataset.shellReady) {
      header.dataset.shellReady = "1";
      header.innerHTML = `<div class="personal-channel-title"><span class="dm-operly-mark large">✦</span><div><small>@me</small><h1>Operly</h1><p>Your private AI can coordinate your account and act through the permissions you already have.</p></div><span class="private-badge">Private</span></div>`;
    }
    const compose = $p(".personal-compose", root);
    if (compose && !compose.dataset.shellReady) {
      compose.dataset.shellReady = "1";
      const label = compose.querySelector('label[for="personal-workspace-select"]');
      if (label) label.textContent = "Optional focus";
      $p("#personal-input")?.setAttribute("placeholder", "Message Operly — ask across your account or name a workspace…");
      compose.insertAdjacentHTML("afterbegin", `<div class="compose-context-note"><span>✦</span><span>Operly resolves permissions and connectors at execution time.</span></div>`);
    }
  }

  function decorateWorkspace() {
    const root = $p("#dashboard");
    if (!root || root.classList.contains("hidden")) return;
    const sidebar = $p("#sidebar", root);
    const current = shell.workspaces.find(item => item.current) || shell.workspaces.find(item => item.id === shell.me?.current_workspace_id);
    if (!sidebar || !current) return;
    let title = $p(".workspace-shell-title", sidebar);
    if (!title) {
      title = document.createElement("div");
      title.className = "workspace-shell-title";
      sidebar.querySelector(".brand")?.insertAdjacentElement("afterend", title);
    }
    title.innerHTML = `<button class="workspace-title-button" data-workspace-settings><span class="workspace-title-mark">${workspaceMark(current)}</span><span><b>${escp(current.name)}</b><small>${escp(current.role)}</small></span><span class="workspace-title-caret">⌄</span></button>`;
    title.querySelector("img")?.addEventListener("error", event => event.currentTarget.classList.add("image-failed"), {once:true});
    $p("[data-workspace-settings]", title)?.addEventListener("click", () => openWorkspaceSettings(current.id, "general"));
    const switcher = $p(".workspace-switch-label", sidebar);
    if (switcher) switcher.classList.add("scope-rail-replaced");
  }

  function ensureModal() {
    let host = $p("#operly-shell-modal");
    if (!host) {
      host = document.createElement("div");
      host.id = "operly-shell-modal";
      host.className = "operly-shell-modal hidden";
      host.innerHTML = `<button class="shell-modal-backdrop" aria-label="Close settings"></button><section class="shell-modal-card" role="dialog" aria-modal="true"><div id="shell-modal-content"></div></section>`;
      document.body.appendChild(host);
      $p(".shell-modal-backdrop", host).addEventListener("click", closeModal);
    }
    return host;
  }

  function closeModal() {
    const host = ensureModal();
    host.classList.add("hidden");
    document.body.classList.remove("shell-modal-open");
  }

  function openModal(content) {
    const host = ensureModal();
    $p("#shell-modal-content", host).innerHTML = content;
    host.classList.remove("hidden");
    document.body.classList.add("shell-modal-open");
    $p("[data-close-modal]", host)?.addEventListener("click", closeModal);
  }

  async function openCreateWorkspace() {
    openModal(`
      <header class="shell-modal-head"><div><small>NEW SPACE</small><h2>Create a workspace</h2><p>Start a shared Operly space for a team, business, community, project, or anything else you operate.</p></div><button data-close-modal aria-label="Close">×</button></header>
      <form id="shell-create-workspace" class="shell-form">
        <label>Workspace name<input id="shell-workspace-name" maxlength="200" placeholder="ORB Eats" autocomplete="off" required></label>
        <label>Timezone<input id="shell-workspace-timezone" maxlength="100" placeholder="America/Chicago" value="${escp(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC")}"></label>
        <div class="shell-form-actions"><button type="button" class="shell-button secondary" data-close-modal>Cancel</button><button class="shell-button primary">Create workspace</button></div>
      </form>`);
    $$p("[data-close-modal]", ensureModal()).forEach(button => button.addEventListener("click", closeModal));
    $p("#shell-create-workspace")?.addEventListener("submit", async event => {
      event.preventDefault();
      const submit = event.currentTarget.querySelector('button[type="submit"],button:not([type])');
      if (submit) submit.disabled = true;
      try {
        const result = await api("/auth/workspaces", {method:"POST", body:JSON.stringify({name:$p("#shell-workspace-name").value})});
        closeModal();
        await goWorkspace(result.workspace.id);
        showToast(`${result.workspace.name} is ready`);
      } catch (error) {
        showToast(error.message, "error");
        if (submit) submit.disabled = false;
      }
    });
  }

  function accountTabs(active) {
    return `
      <nav class="settings-tabs" aria-label="Account settings">
        <button data-account-switch="account" class="${active === "account" ? "active" : ""}">My account</button>
        <button data-account-switch="connections" class="${active === "connections" ? "active" : ""}">Connections</button>
        <button data-account-switch="security" class="${active === "security" ? "active" : ""}">Security</button>
        <button data-account-switch="workspaces" class="${active === "workspaces" ? "active" : ""}">Workspaces</button>
      </nav>`;
  }

  async function openAccountSettings(tab = "account") {
    await refreshScopeData().catch(() => {});
    openModal(`
      <div class="settings-shell">
        <aside><div class="settings-brand"><img src="/static/operly-logo.png" alt=""><span><small>OPERLY</small><b>User Settings</b></span></div>${accountTabs(tab)}</aside>
        <main id="account-settings-pane"><header class="shell-modal-head compact"><div><small>PERSONAL</small><h2>User settings</h2></div><button data-close-modal aria-label="Close">×</button></header><div class="settings-loading">Loading…</div></main>
      </div>`);
    $p("[data-close-modal]", ensureModal())?.addEventListener("click", closeModal);
    $$p("[data-account-switch]", ensureModal()).forEach(button => button.addEventListener("click", () => openAccountSettings(button.dataset.accountSwitch)));
    await renderAccountPane(tab);
  }

  async function renderAccountPane(tab) {
    const pane = $p("#account-settings-pane");
    if (!pane) return;
    const close = `<header class="shell-modal-head compact"><div><small>PERSONAL</small><h2>${tab === "connections" ? "Connected accounts" : tab === "security" ? "Password & security" : tab === "workspaces" ? "Your workspaces" : "My account"}</h2></div><button data-close-modal aria-label="Close">×</button></header>`;
    if (tab === "account") {
      const me = await api("/personal-agent/me");
      pane.innerHTML = `${close}<form id="account-profile-form" class="settings-section shell-form"><div class="profile-hero"><span>${escp(initials(me.display_name || me.email))}</span><div><h3>${escp(me.display_name)}</h3><p>${escp(me.email)}</p></div></div><label>Display name<input id="account-display-name" maxlength="200" value="${escp(me.display_name)}" required></label><label>Email<input value="${escp(me.email)}" disabled><small>Email changes require a verified identity flow.</small></label><button class="shell-button primary">Save profile</button></form>`;
      $p("#account-profile-form")?.addEventListener("submit", async event => {
        event.preventDefault();
        try {
          await api("/personal-agent/me", {method:"PATCH", body:JSON.stringify({display_name:$p("#account-display-name").value})});
          showToast("Profile updated");
          await refreshShell();
          await openAccountSettings("account");
        } catch (error) { showToast(error.message, "error"); }
      });
    } else if (tab === "connections") {
      const connectors = await api("/personal-connectors");
      const google = connectors.find(item => item.provider === "google");
      pane.innerHTML = `${close}<section class="settings-section"><div class="section-heading"><div><h3>Your tools, wherever you go</h3><p>Personal connectors belong to you, not to a workspace. Operly exposes only the capabilities allowed by their OAuth scopes.</p></div></div><div class="settings-card connector-setting"><div class="connector-icon google">G</div><div class="connector-copy"><h4>Google</h4><p>${google ? escp(google.account || "Connected") : "Gmail and Calendar for your Personal AI."}</p>${google ? `<small>${escp((google.capabilities || []).slice(0,6).join(" · ") || google.healthStatus || "Connected")}</small>` : ""}</div><div class="connector-actions">${google ? `<span class="connection-status">${escp(google.status)}</span><button class="shell-button secondary" data-personal-test="${escp(google.id)}">Test</button><button class="shell-button danger-subtle" data-personal-disconnect="${escp(google.id)}">Disconnect</button>` : `<button class="shell-button primary" data-connect-google>Connect Google</button>`}</div></div></section>`;
      $p("[data-connect-google]")?.addEventListener("click", async event => {
        event.currentTarget.disabled = true;
        try {
          const result = await api("/personal-connectors/google/connect?tier=assistant", {method:"POST", body:"{}"});
          location.href = result.authorization_url;
        } catch (error) { showToast(error.message, "error"); event.currentTarget.disabled = false; }
      });
      $p("[data-personal-test]")?.addEventListener("click", async event => {
        try { await api(`/personal-connectors/${event.currentTarget.dataset.personalTest}/test`, {method:"POST", body:"{}"}); showToast("Connection checked"); await openAccountSettings("connections"); } catch (error) { showToast(error.message, "error"); }
      });
      $p("[data-personal-disconnect]")?.addEventListener("click", async event => {
        if (!confirm("Disconnect this personal Google account? Workspace-owned connectors are unaffected.")) return;
        try { await api(`/personal-connectors/${event.currentTarget.dataset.personalDisconnect}`, {method:"DELETE"}); showToast("Personal Google disconnected"); await openAccountSettings("connections"); } catch (error) { showToast(error.message, "error"); }
      });
    } else if (tab === "security") {
      pane.innerHTML = `${close}<section class="settings-section"><div class="section-heading"><h3>Password</h3><p>Your password values go directly to Operly authentication; they are never sent through the Personal AI conversation.</p></div><form id="account-password-form" class="shell-form narrow"><label>Current password<input id="account-current-password" type="password" autocomplete="current-password"></label><label>New password<input id="account-new-password" type="password" minlength="12" autocomplete="new-password" required><small>Use at least 12 characters.</small></label><label>Confirm new password<input id="account-confirm-password" type="password" minlength="12" autocomplete="new-password" required></label><button class="shell-button primary">Change password</button></form></section>`;
      $p("#account-password-form")?.addEventListener("submit", async event => {
        event.preventDefault();
        const next = $p("#account-new-password").value;
        if (next !== $p("#account-confirm-password").value) return showToast("New passwords do not match", "error");
        try {
          await api("/auth/change-password", {method:"POST", body:JSON.stringify({current_password:$p("#account-current-password").value || null,new_password:next})});
          showToast("Password changed");
          closeModal();
        } catch (error) { showToast(error.message, "error"); }
      });
    } else {
      const workspaces = await api("/personal-agent/workspaces");
      pane.innerHTML = `${close}<section class="settings-section"><div class="section-heading split"><div><h3>Spaces you belong to</h3><p>Workspaces are shared boundaries. Your Personal AI remains private above them.</p></div><button class="shell-button primary" data-create-workspace-inline>Create workspace</button></div><div class="workspace-settings-list">${workspaces.map(workspace => `<button data-settings-workspace="${escp(workspace.id)}"><span class="mini-workspace-mark">${workspaceMark(workspace)}</span><span><b>${escp(workspace.name)}</b><small>${escp(workspace.role)} · ${escp(workspace.timezone || "UTC")}</small></span><span>›</span></button>`).join("") || `<p>No workspaces yet.</p>`}</div></section>`;
      $p("[data-create-workspace-inline]")?.addEventListener("click", openCreateWorkspace);
      $$p("[data-settings-workspace]").forEach(button => button.addEventListener("click", () => openWorkspaceSettings(button.dataset.settingsWorkspace, "general")));
    }
    $p("[data-close-modal]", pane)?.addEventListener("click", closeModal);
  }

  function workspaceTabs(active) {
    return `<nav class="settings-tabs" aria-label="Workspace settings"><button data-workspace-tab="general" class="${active === "general" ? "active" : ""}">Overview</button><button data-workspace-tab="members" class="${active === "members" ? "active" : ""}">Members & roles</button><button data-workspace-tab="connections" class="${active === "connections" ? "active" : ""}">Integrations</button><button data-workspace-tab="danger" class="danger-tab ${active === "danger" ? "active" : ""}">Danger zone</button></nav>`;
  }

  async function openWorkspaceSettings(workspaceId, tab = "general") {
    await refreshScopeData().catch(() => {});
    const workspace = shell.workspaces.find(item => item.id === workspaceId);
    if (!workspace) return showToast("Workspace not found", "error");
    if (tab === "members" || tab === "connections") {
      if (shell.me?.current_workspace_id !== workspaceId) await goWorkspace(workspaceId);
      await refreshScopeData().catch(() => {});
    }
    openModal(`<div class="settings-shell workspace-settings-shell"><aside><div class="settings-brand workspace"><span class="mini-workspace-mark large">${workspaceMark(workspace)}</span><span><small>WORKSPACE</small><b>${escp(workspace.name)}</b></span></div>${workspaceTabs(tab)}<button class="back-to-account" data-back-account>← User settings</button></aside><main id="workspace-settings-pane"><header class="shell-modal-head compact"><div><small>${escp(workspace.name)}</small><h2>Workspace settings</h2></div><button data-close-modal aria-label="Close">×</button></header><div class="settings-loading">Loading…</div></main></div>`);
    $p("[data-close-modal]", ensureModal())?.addEventListener("click", closeModal);
    $p("[data-back-account]", ensureModal())?.addEventListener("click", () => openAccountSettings("workspaces"));
    $$p("[data-workspace-tab]", ensureModal()).forEach(button => button.addEventListener("click", () => openWorkspaceSettings(workspaceId, button.dataset.workspaceTab)));
    await renderWorkspacePane(workspaceId, tab);
  }

  async function renderWorkspacePane(workspaceId, tab) {
    const pane = $p("#workspace-settings-pane");
    if (!pane) return;
    const workspace = shell.workspaces.find(item => item.id === workspaceId);
    const close = `<header class="shell-modal-head compact"><div><small>${escp(workspace?.name || "Workspace")}</small><h2>${tab === "members" ? "Members & roles" : tab === "connections" ? "Integrations" : tab === "danger" ? "Danger zone" : "Workspace overview"}</h2></div><button data-close-modal aria-label="Close">×</button></header>`;
    if (tab === "general") {
      pane.innerHTML = `${close}<form id="workspace-general-form" class="settings-section shell-form"><div class="workspace-identity-preview"><span class="workspace-logo-preview">${workspaceMark(workspace)}</span><div><h3>${escp(workspace.name)}</h3><p>${escp(workspace.role)} · Shared workspace</p></div></div><label>Workspace name<input id="workspace-settings-name" maxlength="200" value="${escp(workspace.name)}" required></label><label>Logo URL<input id="workspace-settings-logo" type="url" maxlength="1000" placeholder="https://…/logo.png" value="${escp(workspace.logo_url || "")}"><small>Use an HTTPS image URL. Leave empty to use initials.</small></label><label>Timezone<input id="workspace-settings-timezone" maxlength="100" value="${escp(workspace.timezone || "UTC")}" required></label><button class="shell-button primary">Save changes</button></form>`;
      $p("#workspace-general-form")?.addEventListener("submit", async event => {
        event.preventDefault();
        try {
          await api(`/personal-agent/workspaces/${workspaceId}`, {method:"PATCH", body:JSON.stringify({name:$p("#workspace-settings-name").value,logo_url:$p("#workspace-settings-logo").value || null,timezone:$p("#workspace-settings-timezone").value})});
          showToast("Workspace updated");
          await refreshShell();
          await openWorkspaceSettings(workspaceId, "general");
        } catch (error) { showToast(error.message, "error"); }
      });
    } else if (tab === "members") {
      const [membersResult, rolesResult] = await Promise.allSettled([api("/workspace/members"), api("/workspace/roles")]);
      const members = membersResult.status === "fulfilled" ? membersResult.value : [];
      const roles = rolesResult.status === "fulfilled" ? rolesResult.value : [];
      pane.innerHTML = `${close}<section class="settings-section"><div class="section-heading"><h3>People in this workspace</h3><p>Workspace roles control what Operly may do on a person's behalf inside this boundary.</p></div><div class="member-list">${members.map(member => `<div><span class="member-avatar">${escp(initials(member.display_name || member.email))}</span><span><b>${escp(member.display_name || member.email)}</b><small>${escp(member.email)}</small></span><span class="role-chip">${escp(member.role)}</span></div>`).join("") || `<p>${membersResult.status === "rejected" ? escp(membersResult.reason?.message || "Members unavailable") : "No members."}</p>`}</div>${roles.length ? `<form id="workspace-add-member" class="shell-form inline-member-form"><label>Email<input id="workspace-member-email" type="email" placeholder="person@example.com" required></label><label>Role<select id="workspace-member-role">${roles.map(role => `<option value="${escp(role.key)}">${escp(role.name)}</option>`).join("")}</select></label><button class="shell-button primary">Add member</button><small>At the moment the person must already have an Operly account.</small></form>` : ""}</section>`;
      $p("#workspace-add-member")?.addEventListener("submit", async event => {
        event.preventDefault();
        try {
          await api("/workspace/members", {method:"POST", body:JSON.stringify({email:$p("#workspace-member-email").value,role:$p("#workspace-member-role").value})});
          showToast("Member added");
          await openWorkspaceSettings(workspaceId, "members");
        } catch (error) { showToast(error.message, "error"); }
      });
    } else if (tab === "connections") {
      pane.innerHTML = `${close}<section class="settings-section"><div class="section-heading"><h3>Workspace-owned integrations</h3><p>These credentials belong to the workspace. They are separate from your personal connectors.</p></div><div class="settings-card"><div class="connector-icon">⌁</div><div class="connector-copy"><h4>Open workspace connections</h4><p>Manage Google, Discord, and other installed workspace plugins in the existing integration surface.</p></div><button class="shell-button primary" data-open-workspace-connections>Open connections</button></div></section>`;
      $p("[data-open-workspace-connections]")?.addEventListener("click", async () => {
        closeModal();
        if (typeof window.renderPage === "function") await window.renderPage("settings");
      });
    } else {
      pane.innerHTML = `${close}<section class="settings-section danger-zone"><div class="section-heading"><h3>Delete workspace</h3><p>Permanent deletion affects memberships, workspace data, plugin state, and audit-linked resources.</p></div><div class="danger-card"><div><b>Permanent workspace deletion is intentionally locked in this patch.</b><p>The current database still has legacy tenant foreign keys without a complete cascade contract. Operly will not offer a button that can leave orphaned business data. Account-first navigation, workspace creation/editing, logos, members, roles and governed Personal AI execution are enabled; destructive deletion needs the referential cleanup migration first.</p></div><button class="shell-button danger" disabled>Delete workspace</button></div></section>`;
    }
    $p("[data-close-modal]", pane)?.addEventListener("click", closeModal);
  }

  function showToast(message, kind = "success") {
    let host = $p("#operly-shell-toasts");
    if (!host) {
      host = document.createElement("div");
      host.id = "operly-shell-toasts";
      host.className = "operly-shell-toasts";
      document.body.appendChild(host);
    }
    const toast = document.createElement("div");
    toast.className = `shell-toast ${kind}`;
    toast.textContent = message || (kind === "error" ? "Something went wrong" : "Done");
    host.appendChild(toast);
    setTimeout(() => toast.remove(), 3600);
  }

  async function mount() {
    await refreshScopeData().catch(() => {});
    decoratePersonal();
    await loadWorkspaces().catch(error => add("assistant", error.message, "Workspace list unavailable"));
    const sendButton = $p("#personal-send"), input = $p("#personal-input"), logout = $p("#personal-logout");
    if (sendButton && !sendButton.dataset.bound) { sendButton.dataset.bound = "1"; sendButton.addEventListener("click", send); }
    if (input && !input.dataset.bound) { input.dataset.bound = "1"; input.addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }); }
    if (logout && !logout.dataset.bound) { logout.dataset.bound = "1"; logout.addEventListener("click", async () => { try { await api("/auth/logout", {method:"POST", body:"{}"}); location.assign("/login"); } catch (error) { showToast(error.message, "error"); } }); }
    const oldCreate = $p("#personal-create-workspace");
    if (oldCreate && !oldCreate.dataset.bound) { oldCreate.dataset.bound = "1"; oldCreate.addEventListener("click", openCreateWorkspace); }
    await loadConversations();
    await refreshShell();
  }

  async function syncInitialRoute() {
    if (shell.initialSyncDone || (!initialWantsPersonal && !initialWorkspace)) return;
    shell.initialSyncDone = true;
    try {
      await refreshScopeData();
      if (initialWantsPersonal) {
        await goPersonal();
        return;
      }
      const target = shell.workspaces.find(item => item.id === initialWorkspace || item.slug === initialWorkspace);
      if (target) await goWorkspace(target.id);
    } catch (error) {
      console.warn("Could not restore requested Operly scope", error);
    }
  }

  function installObserver() {
    if (shell.observer) return;
    const personal = $p("#personal"), dashboard = $p("#dashboard");
    if (!personal || !dashboard) return;
    shell.observer = new MutationObserver(() => {
      if (authenticatedScreen()) refreshShell().catch(() => {});
      else renderRail();
    });
    shell.observer.observe(personal, {attributes:true, attributeFilter:["class"]});
    shell.observer.observe(dashboard, {attributes:true, attributeFilter:["class"]});
  }

  window.operlyPersonal = {mount, loadWorkspaces, send, refreshShell, goPersonal, goWorkspace, openAccountSettings, openWorkspaceSettings};
  installObserver();
  setTimeout(() => syncInitialRoute(), 120);
  setTimeout(() => refreshShell(), 280);
  if (!$p("#personal")?.classList.contains("hidden")) mount().catch(() => {});
  window.addEventListener("keydown", event => { if (event.key === "Escape" && !$p("#operly-shell-modal")?.classList.contains("hidden")) closeModal(); });
})();
