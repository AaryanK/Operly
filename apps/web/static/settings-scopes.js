(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = (value = "") => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);

  function ensureStyles() {
    if ($('link[data-operly-settings-scopes]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/static/settings-scopes.css?v=20260821-scopes-v1";
    link.dataset.operlySettingsScopes = "1";
    document.head.append(link);
  }

  function setTitle(title) {
    const node = $("#page-title");
    if (node) node.textContent = title;
  }

  function markConnectionsActive(active = true) {
    $$(".operly-nav-item").forEach(button => {
      if (button.dataset.shellPage === "connections") button.classList.toggle("active", active);
      else if (active) button.classList.remove("active");
    });
  }

  async function currentWorkspace() {
    const workspaces = await api("/session/workspaces");
    return workspaces.find(item => item.current) || workspaces[0] || null;
  }

  function workspaceShell(title, subtitle, workspace, body) {
    setTitle(title);
    const target = $("#content");
    if (!target) return;
    target.innerHTML = `<div class="operly-settings-page">
      <section class="operly-settings-hero"><div><span class="operly-settings-kicker">Workspace settings</span><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div></section>
      <div class="operly-scope-banner"><div class="operly-scope-icon">${esc((workspace?.name || "W").slice(0,2).toUpperCase())}</div><div><strong>${esc(workspace?.name || "Current workspace")}</strong><small>${esc(workspace?.role || "member")} · Changes here belong only to this workspace.</small></div></div>
      ${body}
    </div>`;
  }

  function personalShell(title, subtitle, me, body) {
    setTitle(title);
    const target = $("#content");
    if (!target) return;
    const name = me?.user?.display_name || me?.display_name || "Operly user";
    const email = me?.user?.email || me?.email || "";
    target.innerHTML = `<div class="operly-settings-page">
      <section class="operly-settings-hero"><div><span class="operly-settings-kicker">My Operly</span><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div></section>
      <div class="operly-scope-banner"><div class="operly-personal-avatar">${esc(name.slice(0,2).toUpperCase())}</div><div><strong>${esc(name)}</strong><small>${esc(email || "Personal Operly identity")} · These settings follow you across workspaces.</small></div></div>
      ${body}
    </div>`;
  }

  async function renderWorkspaceConnections() {
    markConnectionsActive(true);
    const target = $("#content");
    if (target) target.innerHTML = `<div class="operly-settings-page">Loading workspace connections…</div>`;
    const [workspace, integrationsResult, connectorsResult, identitiesResult] = await Promise.all([
      currentWorkspace(),
      api("/integrations").catch(() => []),
      api("/connectors").catch(() => []),
      api("/identities").catch(() => []),
    ]);
    const integrations = Array.isArray(integrationsResult) ? integrationsResult : [];
    const connectors = Array.isArray(connectorsResult) ? connectorsResult : [];
    const identities = Array.isArray(identitiesResult) ? identitiesResult : [];
    const discordIdentity = identities.find(item => item.provider === "discord");
    const discord = integrations.find(item => item.provider === "discord") || {spaces:[],status:"disconnected"};
    const discordSpaces = Array.isArray(discord.spaces) ? discord.spaces : [];
    const google = connectors.find(item => item.provider === "google");

    const channelRows = discordSpaces.length
      ? discordSpaces.map(space => `<div class="operly-setting-row"><div><strong>${esc(space.name || "Discord server")}</strong><small>Discord server · bound to ${esc(workspace?.name || "this workspace")}</small></div><span class="operly-badge">connected</span></div>`).join("")
      : `<div class="operly-settings-empty">No Discord server is currently bound to this workspace.</div>`;

    const channelAction = discordIdentity
      ? `<div class="operly-bind-guide"><small>To connect or move a Discord server to this workspace</small><code>Inside that Discord server, run /bind and choose: ${esc(workspace?.name || "this workspace")}</code></div><p style="color:var(--op-muted);font-size:13px">You must have Discord <strong>Manage Server</strong> and permission to manage channels in both the old and new Operly workspaces. An explicit /bind now safely rebinds the server.</p>`
      : `<div class="operly-bind-guide"><small>First connect your personal Discord identity</small><code>My Operly → Personal connections → Discord</code></div><div class="operly-setting-actions"><button class="operly-setting-button primary" data-open-personal-connections>Open personal connections</button></div>`;

    const googleBody = google
      ? `<div class="operly-setting-row"><div><strong>${esc(google.account || google.display_name || "Google Workspace")}</strong><small>${esc(google.permission_tier || "basic")} access · ${esc(google.health_status || google.status || "connected")}</small></div><span class="operly-badge">${esc(google.status || "connected")}</span></div><div class="operly-setting-actions"><button class="operly-setting-button danger" data-disconnect-connector="${esc(google.id)}">Disconnect</button></div>`
      : `<div class="operly-settings-empty">No Google Workspace account is connected to this workspace.</div><div class="operly-setting-actions"><button class="operly-setting-button" data-google-tier="basic">Connect basic</button><button class="operly-setting-button primary" data-google-tier="assistant">Connect full assistant</button></div>`;

    workspaceShell(
      "Channels & integrations",
      "External groups and business connectors attached to the currently selected workspace. Personal account links live in My Operly instead.",
      workspace,
      `<section class="operly-settings-grid">
        <article class="operly-setting-card wide"><div class="operly-setting-head"><div><h3>Channels & groups</h3><p>Discord servers, Slack workspaces, WhatsApp groups and future communication spaces bind to one Operly workspace.</p></div><span class="operly-badge ${discordSpaces.length ? "" : "muted"}">${discordSpaces.length ? `${discordSpaces.length} bound` : "none bound"}</span></div><div class="operly-setting-list">${channelRows}</div>${channelAction}</article>
        <article class="operly-setting-card"><div class="operly-setting-head"><div><h3>Google Workspace</h3><p>Workspace-owned email and calendar authority. This is not your personal Operly identity link.</p></div><span class="operly-badge ${google ? "" : "muted"}">${google ? "connected" : "not connected"}</span></div>${googleBody}</article>
        <article class="operly-setting-card"><div class="operly-setting-head"><div><h3>Coming next</h3><p>Slack, WhatsApp and other external spaces use the same workspace-binding model.</p></div></div><div class="operly-setting-list">${integrations.filter(item => ["slack","whatsapp"].includes(item.provider)).map(item => `<div class="operly-setting-row"><div><strong>${esc(item.label)}</strong><small>${esc(item.status || "coming soon")}</small></div><span class="operly-badge muted">${esc(item.status || "coming soon")}</span></div>`).join("")}</div></article>
      </section>`
    );

    $$('[data-open-personal-connections]').forEach(button => button.addEventListener("click", renderPersonalSettings));
    $$('[data-google-tier]').forEach(button => button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const result = await api(`/connectors/google/connect?tier=${encodeURIComponent(button.dataset.googleTier)}`, {method:"POST"});
        if (result.authorization_url) location.assign(result.authorization_url);
      } catch (error) {
        button.disabled = false;
        alert(error.message || String(error));
      }
    }));
    $$('[data-disconnect-connector]').forEach(button => button.addEventListener("click", async () => {
      if (!confirm("Disconnect this workspace connector?")) return;
      await api(`/connectors/${button.dataset.disconnectConnector}`, {method:"DELETE"});
      await renderWorkspaceConnections();
    }));
  }

  async function renderPersonalSettings() {
    markConnectionsActive(false);
    const target = $("#content");
    if (target) target.innerHTML = `<div class="operly-settings-page">Loading personal settings…</div>`;
    const [me, identitiesResult, sessionsResult, workspacesResult] = await Promise.all([
      api("/me"),
      api("/identities").catch(() => []),
      api("/auth/sessions").catch(() => []),
      api("/session/workspaces").catch(() => []),
    ]);
    const identities = Array.isArray(identitiesResult) ? identitiesResult : [];
    const sessions = Array.isArray(sessionsResult) ? sessionsResult : [];
    const workspaces = Array.isArray(workspacesResult) ? workspacesResult : [];
    const discord = identities.find(item => item.provider === "discord");

    const identityBody = identities.length
      ? identities.map(identity => `<div class="operly-setting-row"><div><strong>${esc(identity.provider.charAt(0).toUpperCase() + identity.provider.slice(1))}</strong><small>${esc(identity.display_name || "Linked external identity")}</small></div><button class="operly-setting-button danger" data-unlink-identity="${esc(identity.id)}">Unlink</button></div>`).join("")
      : `<div class="operly-settings-empty">No external identities linked yet.</div>`;

    personalShell(
      "Personal settings",
      "Your identity, external accounts and sessions. These settings are independent of whichever workspace you currently have open.",
      me,
      `<section class="operly-settings-grid">
        <article class="operly-setting-card"><div class="operly-setting-head"><div><h3>Linked identities</h3><p>External human accounts that resolve back to your Operly user.</p></div><span class="operly-badge ${identities.length ? "" : "muted"}">${identities.length} linked</span></div><div class="operly-setting-list">${identityBody}</div>${discord ? "" : `<div class="operly-setting-actions"><button class="operly-setting-button primary" id="personal-link-discord">Link Discord</button></div><div id="personal-link-code"></div>`}</article>
        <article class="operly-setting-card"><div class="operly-setting-head"><div><h3>Your workspaces</h3><p>Memberships are separate from your personal channel identities.</p></div><span class="operly-badge">${workspaces.length}</span></div><div class="operly-setting-list">${workspaces.map(w => `<div class="operly-setting-row"><div><strong>${esc(w.name)}</strong><small>${esc(w.role)}</small></div>${w.current ? `<span class="operly-badge">current</span>` : ""}</div>`).join("")}</div></article>
        <article class="operly-setting-card wide"><div class="operly-setting-head"><div><h3>Sessions</h3><p>Devices currently authenticated to your Operly account.</p></div><span class="operly-badge">${sessions.length}</span></div><div class="operly-setting-list">${sessions.length ? sessions.map(s => `<div class="operly-setting-row"><div><strong>${esc(s.current ? "This device" : (s.device || "Device"))}</strong><small>${esc(s.device || "Unknown device")}${s.last_activity_at ? ` · ${esc(new Date(s.last_activity_at).toLocaleString())}` : ""}</small></div><span class="operly-badge ${s.current ? "" : "muted"}">${s.current ? "current" : "active"}</span></div>`).join("") : `<div class="operly-settings-empty">No active sessions found.</div>`}</div></article>
      </section>`
    );

    $$('[data-unlink-identity]').forEach(button => button.addEventListener("click", async () => {
      if (!confirm("Unlink this personal external identity? Workspace bindings are not removed.")) return;
      await api(`/identities/${button.dataset.unlinkIdentity}`, {method:"DELETE"});
      await renderPersonalSettings();
    }));
    $("#personal-link-discord")?.addEventListener("click", async () => {
      const button = $("#personal-link-discord"); button.disabled = true;
      try {
        const result = await api("/identities/discord/link-code", {method:"POST"});
        const box = $("#personal-link-code");
        box.innerHTML = `<div class="operly-code-box"><small>In Discord, run <span class="operly-inline-code">/link</span> and enter this one-time code:</small><br><strong>${esc(result.code)}</strong><br><small>Expires ${esc(new Date(result.expires_at).toLocaleTimeString())}</small></div>`;
      } catch (error) { button.disabled = false; alert(error.message || String(error)); }
    });
  }

  function relabelWorkspaceConnections() {
    const button = $('.operly-nav-item[data-shell-page="connections"]');
    if (!button || button.dataset.scopeRelabeled) return;
    button.dataset.scopeRelabeled = "1";
    const icon = $(".operly-nav-icon", button)?.outerHTML || "";
    button.innerHTML = `${icon}Channels & integrations`;
  }

  function enhanceAccountPopover() {
    const pop = $(".operly-modern-popover");
    if (!pop || $("[data-op-personal-connections]", pop)) return;
    const account = $("[data-op-account]", pop);
    const button = document.createElement("button");
    button.dataset.opPersonalConnections = "1";
    button.textContent = "Personal connections";
    button.addEventListener("click", event => { event.preventDefault(); pop.remove(); renderPersonalSettings(); });
    if (account) account.insertAdjacentElement("afterend", button); else pop.append(button);
  }

  function boot() {
    ensureStyles();
    document.addEventListener("click", event => {
      const connections = event.target.closest('.operly-nav-item[data-shell-page="connections"]');
      if (connections) {
        event.preventDefault();
        event.stopPropagation();
        renderWorkspaceConnections().catch(error => alert(error.message || String(error)));
      }
    }, true);
    const observer = new MutationObserver(() => {
      relabelWorkspaceConnections();
      enhanceAccountPopover();
    });
    observer.observe(document.documentElement, {childList:true, subtree:true});
    relabelWorkspaceConnections();
    enhanceAccountPopover();
    window.renderOperlyPersonalSettings = renderPersonalSettings;
    window.renderOperlyWorkspaceConnections = renderWorkspaceConnections;
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, {once:true}); else boot();
})();
