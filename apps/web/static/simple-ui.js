(() => {
  let booted = false;

  function esc(value = "") {
    return String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[ch]);
  }

  function formatWhen(value) {
    if (!value) return "";
    try {
      return new Intl.DateTimeFormat(undefined, {month:"short", day:"numeric", hour:"numeric", minute:"2-digit"}).format(new Date(value));
    } catch { return ""; }
  }

  function setTitle(title) {
    const node = document.querySelector("#page-title");
    if (node) node.textContent = title;
  }

  function setActive(kind) {
    document.querySelectorAll("#nav button").forEach(button => {
      const value = button.dataset.simplePage || button.dataset.page;
      button.classList.toggle("active", value === kind);
    });
  }

  function leaveStudioFocus() {
    document.querySelector("#dashboard")?.classList.remove("studio-focus");
  }

  async function openAssistant(text = "") {
    leaveStudioFocus();
    setActive("assistant");
    setTitle("OPERLY AI");
    if (typeof window.renderOperlyAssistant !== "function") return;
    await window.renderOperlyAssistant();
    const input = document.querySelector("#ai-input");
    if (input && text.trim()) {
      input.value = text.trim();
      input.focus();
      document.querySelector("#ai-form")?.requestSubmit();
    } else {
      input?.focus();
    }
  }

  async function openBuild(text = "") {
    setActive("studio");
    setTitle("Solutions");
    document.querySelector("#operly-chat-dock")?.classList.add("page-suppressed");
    document.querySelector("#dashboard")?.classList.add("studio-focus");
    if (typeof window.operlyStudio === "function") {
      await window.operlyStudio();
      const input = document.querySelector("#studio-software-prompt");
      if (input && text.trim()) {
        input.value = text.trim();
        input.focus();
        input.closest("form")?.requestSubmit();
      } else {
        input?.focus();
      }
    }
  }

  function attentionSummary(tasks, approvals) {
    const openTasks = tasks.filter(item => item.status !== "completed");
    const pending = approvals.filter(item => item.status === "pending");
    return {openTasks, pending};
  }

  function approvalDetails(item) {
    const details = item.details || {}, args = details.arguments || {};
    if (item.action === "messaging.send") {
      return `<p><strong>${esc(args.subject || "Follow-up email")}</strong></p><p>${esc(args.message || "")}</p><small>${esc(details.rationale || "Review the email before sending")}</small>`;
    }
    return `<p>${esc(Object.values(details).filter(value => typeof value !== "object").join(" · ") || "No details")}</p>`;
  }

  async function renderHome() {
    leaveStudioFocus();
    document.querySelector("#operly-chat-dock")?.classList.remove("page-suppressed");
    setActive("home");
    setTitle("Home");
    const content = document.querySelector("#content");
    if (!content) return;
    content.innerHTML = `<div class="simple-loading">Loading your workspace…</div>`;

    const [tasksResult, approvalsResult, projectsResult, sitesResult, appsResult, leadsResult] = await Promise.allSettled([
      api("/tasks"), api("/approvals"), api("/custom-software/projects"),
      api("/studio/projects"), api("/application-builder/applications"), api("/business/leads")
    ]);
    const tasks = tasksResult.status === "fulfilled" && Array.isArray(tasksResult.value) ? tasksResult.value : [];
    const approvals = approvalsResult.status === "fulfilled" && Array.isArray(approvalsResult.value) ? approvalsResult.value : [];
    const generated = projectsResult.status === "fulfilled" && Array.isArray(projectsResult.value) ? projectsResult.value : [];
    const sites = sitesResult.status === "fulfilled" && Array.isArray(sitesResult.value) ? sitesResult.value : [];
    const managed = appsResult.status === "fulfilled" && Array.isArray(appsResult.value) ? appsResult.value : [];
    const leads = leadsResult.status === "fulfilled" && Array.isArray(leadsResult.value) ? leadsResult.value.filter(item => item.contact_email && !["won","lost"].includes(item.stage)) : [];
    const solutions = [
      ...generated.map(item => ({...item, solutionKind:"generated", solutionType:(item.vertical || "custom solution").replaceAll("_", " ")})),
      ...sites.map(item => ({...item, solutionKind:"website", solutionType:"website"})),
      ...managed.map(item => ({...item, solutionKind:"managed", solutionType:"managed application"}))
    ];
    const {openTasks, pending} = attentionSummary(tasks, approvals);
    const workspace = document.querySelector("#workspace-name")?.textContent || "your workspace";

    content.innerHTML = `
      <section class="simple-home-hero">
        <span class="simple-eyebrow">${esc(workspace)}</span>
        <h2>What should OPERLY compose for your business?</h2>
        <p>Ask about the company, take an action, or launch a tailored Solution—from a website to an internal operating system.</p>
        <form id="simple-command-form" class="simple-command-form">
          <textarea id="simple-command" rows="4" placeholder="Describe what you need…" aria-label="What should OPERLY do?"></textarea>
          <div class="simple-command-actions">
            <button type="button" id="simple-ask" class="button secondary">Ask OPERLY</button>
            <button type="button" id="simple-build" class="button primary">Launch a Solution</button>
          </div>
        </form>
      </section>

      <section class="simple-block simple-follow-up">
        <div class="simple-section-head"><div><span class="simple-eyebrow">GMAIL</span><h3>Follow up with a lead</h3></div><span>Approval required before sending</span></div>
        ${leads.length ? `<form id="simple-follow-up-form" class="simple-follow-up-form">
          <label>Lead<select id="follow-up-lead" required>${leads.map(lead => `<option value="${esc(lead.id)}">${esc(lead.title)} — ${esc(lead.contact_name || lead.contact_email)}</option>`).join("")}</select></label>
          <label>Subject<input id="follow-up-subject" maxlength="998" value="Following up" required></label>
          <label class="full">Message<textarea id="follow-up-message" rows="5" maxlength="20000" placeholder="Write the exact email that should be sent…" required></textarea></label>
          <div class="full simple-follow-up-actions"><span id="follow-up-status">Nothing sends until you approve it in Activity.</span><button class="button primary" type="submit">Prepare for approval</button></div>
        </form>` : `<form id="simple-new-follow-up-lead" class="simple-follow-up-form">
          <label>Name<input id="new-follow-up-name" maxlength="200" placeholder="Customer name" required></label>
          <label>Email<input id="new-follow-up-email" type="email" maxlength="320" placeholder="customer@example.com" required></label>
          <label class="full">Opportunity<input id="new-follow-up-title" maxlength="300" placeholder="What are you following up about?" required></label>
          <div class="full simple-follow-up-actions"><span>Add the recipient first; then compose the approval-gated email.</span><button class="button primary" type="submit">Add follow-up lead</button></div>
        </form>`}
      </section>

      <section class="simple-block">
        <div class="simple-section-head"><div><span class="simple-eyebrow">NOW</span><h3>Needs attention</h3></div><button class="simple-link" data-simple-open="activity">View activity</button></div>
        <div class="simple-attention-grid">
          <button class="simple-attention-card" data-simple-open="activity">
            <span>Approvals</span><strong>${pending.length}</strong><p>${pending.length ? "Waiting for your decision" : "Nothing waiting"}</p>
          </button>
          <button class="simple-attention-card" data-simple-open="activity">
            <span>Open tasks</span><strong>${openTasks.length}</strong><p>${openTasks.length ? "Still in progress" : "You’re clear"}</p>
          </button>
        </div>
      </section>

      <section class="simple-block">
        <div class="simple-section-head"><div><span class="simple-eyebrow">RECENT</span><h3>Your Solutions</h3></div><button class="simple-link" id="simple-new-build">Launch Solution</button></div>
        <div class="simple-project-grid">
          ${solutions.length ? solutions.slice(0,6).map(project => `
            <button class="simple-project-card" data-solution-id="${esc(project.id)}" data-solution-kind="${esc(project.solutionKind)}">
              <span>${esc(project.solutionType)}</span>
              <h4>${esc(project.name || "Untitled Solution")}</h4>
              <p>${project.version ? `Version ${esc(project.version)}` : "Open project"}</p>
            </button>`).join("") : `
            <div class="simple-empty-projects">
              <h4>No Solutions yet</h4><p>Describe what your business needs and OPERLY will shape the first tailored Solution.</p>
            </div>`}
        </div>
      </section>`;

    const command = document.querySelector("#simple-command");
    document.querySelector("#simple-ask")?.addEventListener("click", () => openAssistant(command?.value || ""));
    document.querySelector("#simple-build")?.addEventListener("click", () => openBuild(command?.value || ""));
    document.querySelector("#simple-command-form")?.addEventListener("submit", event => { event.preventDefault(); openAssistant(command?.value || ""); });
    document.querySelector("#simple-new-build")?.addEventListener("click", () => openBuild());
    document.querySelector("#simple-follow-up-form")?.addEventListener("submit", async event => {
      event.preventDefault(); const button=event.submitter; if(button)button.disabled=true;
      const status=document.querySelector("#follow-up-status");
      try {
        const result=await api(`/business/leads/${document.querySelector("#follow-up-lead").value}/follow-up`,{method:"POST",body:JSON.stringify({subject:document.querySelector("#follow-up-subject").value,message:document.querySelector("#follow-up-message").value})});
        if(status)status.textContent=`Prepared for ${result.recipient}. Opening approval…`; await renderActivity();
      } catch(error) { if(status)status.textContent=error.message; if(button)button.disabled=false; }
    });
    document.querySelector("#simple-new-follow-up-lead")?.addEventListener("submit", async event => {
      event.preventDefault(); const button=event.submitter; if(button)button.disabled=true;
      try {
        const contact=await api("/business/contacts",{method:"POST",body:JSON.stringify({name:document.querySelector("#new-follow-up-name").value,email:document.querySelector("#new-follow-up-email").value})});
        await api("/business/leads",{method:"POST",body:JSON.stringify({title:document.querySelector("#new-follow-up-title").value,contact_id:contact.id,stage:"new",value:0})}); await renderHome();
      } catch(error) { alert(error.message); if(button)button.disabled=false; }
    });
    document.querySelectorAll("[data-solution-id]").forEach(button => button.addEventListener("click", () => {
      const open = button.dataset.solutionKind === "website" ? window.openStudioProject
        : button.dataset.solutionKind === "managed" ? window.openManagedApplication
        : window.openCustomSoftware;
      if (typeof open === "function") open(button.dataset.solutionId);
    }));
    document.querySelectorAll("[data-simple-open='activity']").forEach(button => button.addEventListener("click", renderActivity));
    refreshActivityBadge(tasks, approvals);
    command?.focus();
  }

  async function renderActivity() {
    leaveStudioFocus();
    document.querySelector("#operly-chat-dock")?.classList.remove("page-suppressed");
    setActive("activity");
    setTitle("Activity");
    const content = document.querySelector("#content");
    if (!content) return;
    content.innerHTML = `<div class="simple-loading">Loading activity…</div>`;

    const [messagesResult, tasksResult, approvalsResult] = await Promise.allSettled([
      api("/messages"), api("/tasks"), api("/approvals")
    ]);
    const messages = messagesResult.status === "fulfilled" && Array.isArray(messagesResult.value) ? messagesResult.value : [];
    const tasks = tasksResult.status === "fulfilled" && Array.isArray(tasksResult.value) ? tasksResult.value : [];
    const approvals = approvalsResult.status === "fulfilled" && Array.isArray(approvalsResult.value) ? approvalsResult.value : [];
    const {openTasks, pending} = attentionSummary(tasks, approvals);

    content.innerHTML = `
      <section class="simple-page-intro"><span class="simple-eyebrow">WORKSPACE</span><h2>Activity</h2><p>Decisions, work, and recent conversations in one place.</p></section>
      <div class="simple-activity-layout">
        <section class="simple-activity-section">
          <div class="simple-section-head"><div><h3>Approvals</h3><span>${pending.length} pending</span></div></div>
          <div class="simple-list">
            ${approvals.length ? approvals.slice(0,8).map(item => `
              <article class="simple-list-row">
                <div><span class="simple-status ${esc(item.status)}">${esc(item.status)}</span><h4>${esc(item.action)}</h4>${approvalDetails(item)}</div>
                ${item.status === "pending" ? `<div class="simple-row-actions"><button class="button secondary" data-simple-approval="rejected" data-id="${esc(item.id)}">Reject</button><button class="button primary" data-simple-approval="approved" data-id="${esc(item.id)}">Approve</button></div>` : ""}
              </article>`).join("") : `<div class="simple-empty">No approvals yet.</div>`}
          </div>
        </section>

        <section class="simple-activity-section">
          <div class="simple-section-head"><div><h3>Tasks</h3><span>${openTasks.length} open</span></div></div>
          <div class="simple-list">
            ${tasks.length ? tasks.slice(0,10).map(item => `
              <article class="simple-list-row simple-task-row ${item.status === "completed" ? "completed" : ""}">
                <div><h4>${esc(item.title)}</h4><p>${item.due_at ? `Due ${formatWhen(item.due_at)}` : esc(item.status)}</p></div>
                ${item.status !== "completed" ? `<button class="simple-check" data-simple-complete="${esc(item.id)}" aria-label="Complete task">✓</button>` : `<span class="simple-done">Done</span>`}
              </article>`).join("") : `<div class="simple-empty">No tasks yet.</div>`}
          </div>
        </section>

        <section class="simple-activity-section simple-messages-section">
          <div class="simple-section-head"><div><h3>Recent messages</h3><span>${messages.length} total</span></div></div>
          <div class="simple-list">
            ${messages.length ? messages.slice(0,12).map(item => `
              <article class="simple-message-row"><div class="simple-avatar">${esc((item.author_name || "?").slice(0,1).toUpperCase())}</div><div><h4>${esc(item.author_name || "Unknown")}</h4><p>${esc(item.content || "")}</p></div><time>${formatWhen(item.created_at)}</time></article>`).join("") : `<div class="simple-empty">No messages yet.</div>`}
          </div>
        </section>
      </div>`;

    document.querySelectorAll("[data-simple-approval]").forEach(button => button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await api(`/approvals/${button.dataset.id}`, {method:"PATCH", body:JSON.stringify({status:button.dataset.simpleApproval})});
        await renderActivity();
      } catch (error) { button.disabled = false; alert(error.message); }
    }));
    document.querySelectorAll("[data-simple-complete]").forEach(button => button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await api(`/tasks/${button.dataset.simpleComplete}/complete`, {method:"PATCH"});
        await renderActivity();
      } catch (error) { button.disabled = false; alert(error.message); }
    }));
    refreshActivityBadge(tasks, approvals);
  }

  function refreshActivityBadge(tasks, approvals) {
    const badge = document.querySelector("#simple-activity-badge");
    if (!badge) return;
    const count = tasks.filter(item => item.status !== "completed").length + approvals.filter(item => item.status === "pending").length;
    badge.textContent = count ? String(count) : "";
    badge.classList.toggle("hidden", !count);
  }

  function bootSimpleUi() {
    if (booted) return;
    const dashboard = document.querySelector("#dashboard");
    if (!dashboard || dashboard.classList.contains("hidden")) return;
    booted = true;
    renderHome().catch(error => {
      const content = document.querySelector("#content");
      if (content) content.innerHTML = `<div class="simple-empty">${esc(error.message || "Could not load Home")}</div>`;
    });
  }

  document.addEventListener("click", event => {
    const simple = event.target.closest("[data-simple-page]");
    if (simple) {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (simple.dataset.simplePage === "home") renderHome();
      if (simple.dataset.simplePage === "activity") renderActivity();
      return;
    }
    if (event.target.closest("#simple-topbar-ask")) {
      event.preventDefault();
      openAssistant();
    }
  }, true);

  const dashboard = document.querySelector("#dashboard");
  if (dashboard) {
    new MutationObserver(bootSimpleUi).observe(dashboard, {attributes:true, attributeFilter:["class"]});
  }
  document.addEventListener("DOMContentLoaded", bootSimpleUi);
  setTimeout(bootSimpleUi, 0);

  window.operlySimpleHome = renderHome;
  window.operlySimpleActivity = renderActivity;
  window.operlyOpenAssistant = openAssistant;
  window.operlyOpenBuild = openBuild;
})();
