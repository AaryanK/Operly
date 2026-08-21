(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = (value = "") => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);

  function enhancePublicLanding() {
    const landing = $("#landing");
    if (!landing || $(".operly-public-capabilities")) return;
    const hero = $(".hero", landing);
    const features = $(".features", landing);
    if (!hero || !features) return;
    const strip = document.createElement("section");
    strip.className = "operly-public-capabilities";
    strip.innerHTML = `<div class="op-public-kicker">One operating layer</div><div class="op-public-marquee" aria-label="Operly capabilities"><span>Workspaces</span><i></i><span>CRM</span><i></i><span>Websites</span><i></i><span>Automations</span><i></i><span>Marketing</span><i></i><span>Knowledge</span><i></i><span>Connectors</span><i></i><span>AI tools</span></div>`;
    hero.insertAdjacentElement("afterend", strip);
    const story = document.createElement("section");
    story.className = "operly-public-story";
    story.innerHTML = `<div class="op-story-copy"><span class="op-public-kicker">Your business, composed</span><h2>Not another dashboard. A workspace that can actually operate.</h2><p>Bring your people, customer channels, digital presence, software and business tools into one governed workspace. Operly understands the context and can act through the capabilities you allow.</p><div class="op-story-points"><span><b>01</b> One identity across many workspaces</span><span><b>02</b> Plugins determine what each workspace can do</span><span><b>03</b> Models reason; Operly controls state, tools and permissions</span></div></div><div class="op-story-visual" aria-label="Operly workspace concept"><div class="op-mini-rail"><b>✦</b><span>A</span><span>N</span><span>O</span><em>+</em></div><div class="op-mini-nav"><strong>ANHITRA</strong><small>Workspace</small><p>Home</p><p class="active">✦ Operly</p><label>BUSINESS</label><p>CRM</p><p>Operations</p><label>PRESENCE</label><p>Website</p><p>Marketing</p></div><div class="op-mini-main"><div class="op-mini-top"><span>ANHITRA / Home</span><button>Ask Operly</button></div><div class="op-mini-command"><small>OPERLY</small><h3>What should we work on?</h3><div>Ask, search or take an action… <b>✦</b></div></div><div class="op-mini-cards"><article><small>Leads</small><strong>12</strong></article><article><small>Tasks</small><strong>4</strong></article><article><small>Approvals</small><strong>2</strong></article></div></div></div>`;
    features.insertAdjacentElement("beforebegin", story);
  }

  function removeUndefinedText(root = document) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const doomed = [];
    while (walker.nextNode()) if (walker.currentNode.nodeValue?.trim() === "undefined") doomed.push(walker.currentNode);
    doomed.forEach(node => node.remove());
  }

  function repairShellHero(root = document) {
    $$(".operly-shell-page", root).forEach(page => {
      const hero = $(".operly-shell-hero", page); if (!hero) return;
      const directButton = [...page.children].find(node => node.classList?.contains("operly-shell-button"));
      if (directButton && !hero.contains(directButton)) hero.append(directButton);
    });
  }

  function placePopover(popover, anchor) {
    const rect = anchor.getBoundingClientRect(); const width = 330;
    const left = Math.min(window.innerWidth - width - 12, Math.max(12, rect.right - width));
    let top = rect.top - 8; if (top + 320 > window.innerHeight) top = Math.max(12, window.innerHeight - 332);
    popover.style.left = `${left}px`; popover.style.top = `${top}px`;
  }
  function closePopovers() { $$(".operly-modern-popover").forEach(node => node.remove()); }

  async function renderAccountWorkspaces() {
    const target = $("#content"); if (!target) return;
    let me = {}, workspaces = [];
    try { [me, workspaces] = await Promise.all([api("/me"), api("/session/workspaces")]); } catch {}
    const name = me?.user?.display_name || me?.display_name || "Operly user";
    const email = me?.user?.email || me?.email || "";
    $("#page-title").textContent = "My Operly";
    target.innerHTML = `<div class="operly-shell-page"><section class="operly-shell-hero"><div><span class="operly-shell-eyebrow">Direct identity</span><h2>My Operly</h2><p>Your identity, workspaces and personal account controls.</p></div></section><section class="operly-shell-card"><div class="op-account-identity"><div class="operly-user-avatar">${esc((name||"U").slice(0,2).toUpperCase())}</div><div><strong>${esc(name)}</strong><small>${esc(email || "Operly account")}</small></div></div></section><section class="operly-shell-card"><h3>Your workspaces</h3><div class="operly-shell-list">${workspaces.length ? workspaces.map(w => `<div class="operly-shell-row"><div><strong>${esc(w.name)}</strong><small>${esc(w.role)}</small></div>${w.current ? `<span class="operly-status">current</span>` : `<button class="operly-shell-button" data-modern-workspace="${esc(w.id)}">Open</button>`}</div>`).join("") : `<small>No workspaces found.</small>`}</div></section></div>`;
    $$("[data-modern-workspace]", target).forEach(button => button.addEventListener("click", async () => { await api("/session/switch-workspace", {method:"POST", body:JSON.stringify({tenant_id:button.dataset.modernWorkspace})}); location.reload(); }));
  }

  async function accountPopover(anchor) {
    closePopovers(); let me = {}, sessions = [];
    try { [me, sessions] = await Promise.all([api("/me"), api("/auth/sessions")]); } catch {}
    const name = me?.user?.display_name || me?.display_name || $("#operly-user-name")?.textContent || "Operly user";
    const email = me?.user?.email || me?.email || "";
    const pop = document.createElement("div"); pop.className = "operly-modern-popover";
    pop.innerHTML = `<div class="op-user"><strong>${esc(name)}</strong><small>${esc(email || "Your Operly identity")}</small></div><button data-op-account>Account & workspaces</button><button data-op-security>Security & sessions${sessions.length ? ` · ${sessions.length}` : ""}</button><div class="sep"></div><button data-op-logout>Sign out</button><button class="danger" data-op-logout-all>Sign out everywhere</button>`;
    document.body.append(pop); placePopover(pop, anchor);
    $("[data-op-account]", pop)?.addEventListener("click", () => { closePopovers(); renderAccountWorkspaces(); });
    $("[data-op-security]", pop)?.addEventListener("click", () => { closePopovers(); renderSecuritySessions(sessions, name, email); });
    $("[data-op-logout]", pop)?.addEventListener("click", async () => { try { await api("/auth/logout", {method:"POST"}); } finally { location.assign("/login"); } });
    $("[data-op-logout-all]", pop)?.addEventListener("click", async () => { try { await api("/auth/logout-all", {method:"POST"}); } finally { location.assign("/login"); } });
  }

  function renderSecuritySessions(sessions, name, email) {
    const target = $("#content"); if (!target) return; $("#page-title").textContent = "Security";
    target.innerHTML = `<div class="operly-shell-page"><section class="operly-shell-hero"><div><span class="operly-shell-eyebrow">My Operly</span><h2>Security & sessions</h2><p>Review where your Operly account is signed in.</p></div></section><section class="operly-shell-card"><div class="op-account-identity"><div class="operly-user-avatar">${esc((name||"U").slice(0,2).toUpperCase())}</div><div><strong>${esc(name)}</strong><small>${esc(email || "Operly account")}</small></div></div></section><section class="operly-shell-card"><h3>Active sessions</h3><div class="operly-shell-list">${sessions.length ? sessions.map(s => `<div class="operly-shell-row"><div><strong>${esc(s.current ? "This device" : s.device || "Device")}</strong><small>${esc(s.device || "Unknown device")} · active ${esc(new Date(s.last_activity_at).toLocaleString())}</small></div><span class="operly-status">${s.current ? "current" : "active"}</span></div>`).join("") : `<small>No active sessions found.</small>`}</div></section></div>`;
  }

  async function workspacePopover(anchor) {
    closePopovers(); let workspaces = []; try { workspaces = await api("/session/workspaces"); } catch {}
    const pop = document.createElement("div"); pop.className = "operly-modern-popover";
    pop.innerHTML = `<div class="op-user"><strong>Switch workspace</strong><small>Independent business and organization spaces</small></div>${workspaces.map(w => `<button data-op-workspace="${esc(w.id)}">${w.current ? "✓ " : ""}${esc(w.name)} <span style="float:right;color:#7f8d85">${esc(w.role)}</span></button>`).join("")}<div class="sep"></div><button data-op-new-workspace>＋ Create workspace</button>`;
    document.body.append(pop); placePopover(pop, anchor);
    $$("[data-op-workspace]", pop).forEach(button => button.addEventListener("click", async () => { const selected = workspaces.find(w => w.id === button.dataset.opWorkspace); if (!selected || selected.current) return closePopovers(); await api("/session/switch-workspace", {method:"POST", body:JSON.stringify({tenant_id:selected.id})}); location.reload(); }));
    $("[data-op-new-workspace]", pop)?.addEventListener("click", () => { closePopovers(); $("#operly-create-workspace")?.click(); });
  }

  function shellButton(page) { return $(`.operly-nav-item[data-shell-page='${page}']`); }
  const commands = [["home","Home","Workspace overview","H"],["operly","Ask Operly","Open the intelligence layer","✦"],["crm","CRM","Contacts, leads, quotes and orders","C"],["operations","Operations","Alerts, scans and operational state","O"],["activity","Activity","Tasks and approvals","A"],["presence","Presence","Website and digital presence","P"],["solutions","Solutions","Build apps, sites and software","S"],["connections","Connections","External services and channels","↗"],["plugins","Plugins","Installed workspace capabilities","＋"],["workspace","Members & Roles","Workspace membership and permissions","M"],["access","AI & MCP Access","Client grants and MCP exposure","AI"]];

  function openCommandPalette() {
    if ($(".operly-command-palette")) return;
    const wrap = document.createElement("div"); wrap.className = "operly-command-palette"; wrap.innerHTML = `<div class="operly-command-box"><input autofocus placeholder="Search Operly, jump to a workspace tool, or ask…" aria-label="Command palette"><div class="operly-command-results"></div></div>`;
    document.body.append(wrap); const input = $("input", wrap); const results = $(".operly-command-results", wrap);
    const render = () => { const q = input.value.trim().toLowerCase(); const visible = commands.filter(([,name,desc]) => !q || `${name} ${desc}`.toLowerCase().includes(q)); results.innerHTML = visible.map(([page,name,desc,icon]) => `<button data-command-page="${page}"><span class="icon">${esc(icon)}</span><span><strong>${esc(name)}</strong><small>${esc(desc)}</small></span><span class="hint">Open</span></button>`).join("") + (q ? `<button data-command-ask><span class="icon">✦</span><span><strong>Ask Operly</strong><small>${esc(input.value)}</small></span><span class="hint">Enter</span></button>` : ""); $$("[data-command-page]", results).forEach(button => button.addEventListener("click", () => { const page=button.dataset.commandPage; wrap.remove(); shellButton(page)?.click(); })); $("[data-command-ask]", results)?.addEventListener("click", () => { const text=input.value; wrap.remove(); shellButton("operly")?.click(); setTimeout(()=>{const area=$("#ai-input")||$("#dock-input"); if(area){area.value=text;area.focus();}},150); }); };
    input.addEventListener("input", render); input.addEventListener("keydown", e => { if(e.key==="Escape") wrap.remove(); if(e.key==="Enter" && input.value.trim()){ e.preventDefault(); $("[data-command-ask]", results)?.click(); }}); wrap.addEventListener("mousedown", e => { if(e.target===wrap) wrap.remove(); }); render(); input.focus();
  }

  function enhanceTopbar() {
    const topbar = $("#dashboard.workspace-shell-ready .topbar"); if (!topbar || $(".operly-command-trigger", topbar)) return;
    const trigger = document.createElement("button"); trigger.className = "operly-command-trigger"; trigger.innerHTML = `<span>⌕</span><span>Search or jump to…</span><kbd>⌘ K</kbd>`; trigger.addEventListener("click", openCommandPalette);
    const actions = $(".simple-topbar-actions", topbar); topbar.insertBefore(trigger, actions || null);
  }

  function enhanceShellEvents() {
    document.addEventListener("click", event => {
      const account = event.target.closest(".operly-user-menu,.operly-rail-account"); if (account) { event.preventDefault(); event.stopImmediatePropagation(); accountPopover(account); return; }
      const workspaceHead = event.target.closest(".operly-workspace-head"); if (workspaceHead) { event.preventDefault(); event.stopImmediatePropagation(); workspacePopover(workspaceHead); return; }
      if (!event.target.closest(".operly-modern-popover")) closePopovers();
    }, true);
    document.addEventListener("keydown", event => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openCommandPalette(); } if (event.key === "Escape") closePopovers(); });
  }

  function observeApp() {
    let scheduled = false;
    const repair = () => { scheduled = false; removeUndefinedText(document); repairShellHero(document); enhanceTopbar(); };
    const observer = new MutationObserver(() => { if (!scheduled) { scheduled = true; requestAnimationFrame(repair); } });
    observer.observe(document.documentElement,{subtree:true,childList:true}); repair();
  }

  function boot() { enhancePublicLanding(); enhanceShellEvents(); observeApp(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot,{once:true}); else boot();
})();
