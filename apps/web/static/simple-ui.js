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

    const [tasksResult, approvalsResult, projectsResult, sitesResult, appsResult, leadsResult, profileResult, questionsResult] = await Promise.allSettled([
      api("/tasks"), api("/approvals"), api("/custom-software/projects"),
      api("/studio/projects"), api("/application-builder/applications"), api("/business/leads"), api("/company/profile"), api("/company/questions")
    ]);
    const tasks = tasksResult.status === "fulfilled" && Array.isArray(tasksResult.value) ? tasksResult.value : [];
    const approvals = approvalsResult.status === "fulfilled" && Array.isArray(approvalsResult.value) ? approvalsResult.value : [];
    const generated = projectsResult.status === "fulfilled" && Array.isArray(projectsResult.value) ? projectsResult.value : [];
    const sites = sitesResult.status === "fulfilled" && Array.isArray(sitesResult.value) ? sitesResult.value : [];
    const managed = appsResult.status === "fulfilled" && Array.isArray(appsResult.value) ? appsResult.value : [];
    const leads = leadsResult.status === "fulfilled" && Array.isArray(leadsResult.value) ? leadsResult.value.filter(item => item.contact_email && !["won","lost"].includes(item.stage)) : [];
    const companyProfile = profileResult.status === "fulfilled" ? (profileResult.value.profile || {}) : {};
    const companyFields = profileResult.status === "fulfilled" ? (profileResult.value.fields || {}) : {};
    const companyQuestions = questionsResult.status === "fulfilled" && Array.isArray(questionsResult.value) ? questionsResult.value.filter(item => !item.answered) : [];
    const solutions = [
      ...generated.map(item => ({...item, solutionKind:"generated", solutionType:(item.vertical || "custom solution").replaceAll("_", " ")})),
      ...sites.map(item => ({...item, solutionKind:"website", solutionType:"website"})),
      ...managed.map(item => ({...item, solutionKind:"managed", solutionType:"managed application"}))
    ];
    const {openTasks, pending} = attentionSummary(tasks, approvals);
    const workspace = document.querySelector("#workspace-name")?.textContent || "your workspace";

    content.innerHTML = `
      <section class="company-learning ${Object.keys(companyProfile).length ? "has-profile" : "is-new"}">
        ${Object.keys(companyProfile).length ? `<div class="simple-section-head"><div><span class="simple-eyebrow">YOUR BUSINESS</span><h3>${esc(companyProfile.display_name || companyProfile.business_name || companyProfile.legal_name || "Company profile")}</h3></div><span>${companyQuestions.length ? `I still need ${companyQuestions.length} ${companyQuestions.length === 1 ? "thing" : "things"} from you.` : "You’re all caught up."}</span></div><div class="company-found">${Object.entries(companyProfile).slice(0,6).map(([key,value]) => `<article><span>✓ ${esc(key.replaceAll("_"," "))}</span><strong>${esc(typeof value === "object" ? JSON.stringify(value) : value)}</strong>${companyFields[key]?.source_type ? `<small>${companyFields[key].owner_confirmed ? "Confirmed by you" : `Found on ${esc(companyFields[key].source_type.replaceAll("_"," "))}`}</small>` : ""}</article>`).join("")}</div>${companyQuestions.length ? `<div class="company-questions"><h4>A few useful questions</h4>${companyQuestions.map(item => `<form class="company-answer" data-question="${esc(item.id)}"><label>${esc(item.question)}<small>${esc(item.why_it_matters)}</small><input name="answer" required maxlength="1000" placeholder="Your answer"></label><button class="button primary" type="submit">Save answer</button></form>`).join("")}</div>` : ""}` : `<span class="simple-eyebrow">LET’S GET TO KNOW YOUR BUSINESS</span><h2>Tell OPERLY your business name, website, or what you’re trying to start.</h2><p>We’ll learn what we can first, then ask only for the important details we couldn’t find.</p><form id="company-discover-form" class="company-discover-form"><input id="company-discover-input" required minlength="2" maxlength="2000" placeholder="Business name, website, location, or a short description"><button class="button primary" type="submit">Learn about my business</button></form><div id="company-discover-status" class="company-discover-status"></div>`}
      </section>
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

      <section class="simple-block simple-follow-up ${leads.length ? "" : "hidden"}">
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
    document.querySelector("#company-discover-form")?.addEventListener("submit", async event => {event.preventDefault();const button=event.submitter,status=document.querySelector("#company-discover-status");button.disabled=true;status.innerHTML="<span class='company-spinner'></span> Learning about your business…";try{const result=await api("/company/discover",{method:"POST",body:JSON.stringify({business:document.querySelector("#company-discover-input").value,max_pages:5})});if(result.status==="failed")throw new Error(result.error || "Research could not be completed");status.textContent=`Found ${result.found.length} useful details. Preparing your questions…`;await renderHome()}catch(error){status.textContent=error.message;button.disabled=false}});
    document.querySelectorAll(".company-answer").forEach(form => form.addEventListener("submit",async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await api("/company/answers",{method:"POST",body:JSON.stringify({question_id:form.dataset.question,answer:new FormData(form).get("answer")})});await renderHome()}catch(error){alert(error.message);button.disabled=false}}));
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

    const [messagesResult, tasksResult, approvalsResult, solutionsResult] = await Promise.allSettled([
      api("/messages"), api("/tasks"), api("/approvals"), api("/solutions")
    ]);
    const messages = messagesResult.status === "fulfilled" && Array.isArray(messagesResult.value) ? messagesResult.value : [];
    const tasks = tasksResult.status === "fulfilled" && Array.isArray(tasksResult.value) ? tasksResult.value : [];
    const approvals = approvalsResult.status === "fulfilled" && Array.isArray(approvalsResult.value) ? approvalsResult.value : [];
    const solutions = solutionsResult.status === "fulfilled" && Array.isArray(solutionsResult.value) ? solutionsResult.value : [];
    const presence = solutions.find(item => item.solution_type === "digital_presence");
    const improvements = presence ? await api(`/solutions/${presence.id}/improvements`).catch(() => []) : [];
    const {openTasks, pending} = attentionSummary(tasks, approvals);

    content.innerHTML = `
      <section class="simple-page-intro"><span class="simple-eyebrow">WORKSPACE</span><h2>Activity</h2><p>Decisions, work, and recent conversations in one place.</p></section>
      <div class="simple-activity-layout">
        ${improvements.length ? `<section class="simple-activity-section"><div class="simple-section-head"><div><h3>Website care</h3><span>${improvements.filter(x=>x.status==="proposed").length} to review</span></div></div><div class="simple-list">${improvements.slice(0,8).map(item=>`<article class="simple-list-row"><div><span class="simple-status ${esc(item.status)}">${esc(item.status.replaceAll("_"," "))}</span><h4>${item.status==="verified"?"Updated successfully":"OPERLY found an inconsistency"}</h4><p><b>Your business profile says:</b> ${esc((item.supporting_evidence.owner_confirmed_profile||[]).map(x=>x.value).join(", "))} ✓</p><p><b>Your website currently:</b> Does not mention it</p><p><b>${item.status==="verified"?"Published":"Proposed update"}:</b> ${esc(item.proposed_change.summary||"")}</p>${item.status==="verified"?`<small>Published version ${esc(item.after_version||"")} · Site health verified</small>`:""}</div>${item.status==="proposed"?`<button class="button primary" data-review-improvement="${esc(item.id)}" data-solution="${esc(item.solution_id)}">Review change</button>`:""}</article>`).join("")}</div></section>`:""}
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
    document.querySelectorAll("[data-review-improvement]").forEach(button => button.addEventListener("click", async () => {button.disabled=true;try{await api(`/solutions/${button.dataset.solution}/improvements/${button.dataset.reviewImprovement}/review`,{method:"POST",body:"{}"});await renderActivity()}catch(error){button.disabled=false;alert(error.message)}}));
    document.querySelectorAll("[data-simple-complete]").forEach(button => button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await api(`/tasks/${button.dataset.simpleComplete}/complete`, {method:"PATCH"});
        await renderActivity();
      } catch (error) { button.disabled = false; alert(error.message); }
    }));
    refreshActivityBadge(tasks, approvals);
  }

  async function renderPresence() {
    leaveStudioFocus();document.querySelector("#operly-chat-dock")?.classList.remove("page-suppressed");setActive("presence");setTitle("Presence");
    const content=document.querySelector("#content");if(!content)return;content.innerHTML=`<div class="simple-loading">Checking your business presence…</div>`;
    const [solutionsResult,profileResult,connectorsResult]=await Promise.allSettled([api("/solutions"),api("/company/profile"),api("/connectors")]);
    const solutions=solutionsResult.status==="fulfilled"&&Array.isArray(solutionsResult.value)?solutionsResult.value:[];const profile=profileResult.status==="fulfilled"?(profileResult.value.profile||{}):{};const presence=solutions.find(x=>x.solution_type==="digital_presence");const connectors=connectorsResult.status==="fulfilled"&&Array.isArray(connectorsResult.value)?connectorsResult.value:[];
    content.innerHTML=`<section class="presence-hero"><span class="simple-eyebrow">YOUR BUSINESS ONLINE</span><h2>${esc(profile.display_name||profile.business_name||profile.legal_name||"Your digital presence")}</h2>${presence?`<div class="presence-status"><div><span>Website</span><strong>${esc(presence.status.replaceAll("_"," "))}</strong><small>${presence.production.state==="live"?"Your website is live.":presence.status==="failed"?"Publishing failed. No unverified version was made public.":presence.status==="publishing"?"Publishing and checking your website…":"Your verified preview is private until you publish."}</small></div><div class="presence-actions">${presence.preview.url?`<a class="button secondary" href="${esc(presence.preview.url)}" target="_blank">Preview</a>`:""}${presence.production.state==="live"&&presence.production.url?`<a class="button primary" href="${esc(presence.production.url)}" target="_blank">View live</a><button class="button secondary" id="presence-rollback">Rollback</button>`:`<button class="button primary" id="presence-publish">Publish</button>`}<button class="button secondary" id="presence-change">Change something</button></div></div><div id="presence-publish-message" class="company-discover-status"></div>`:`<p>Your business information is ready. Create a real preview using everything OPERLY already knows.</p><button class="button primary" id="create-presence" ${Object.keys(profile).length?"":"disabled"}>Get my business online</button>${Object.keys(profile).length?"":"<small>Finish company understanding first.</small>"}`}</section><div class="presence-grid"><section><span class="simple-eyebrow">BUSINESS INFORMATION</span><h3>${Object.keys(profile).length?"Up to date ✓":"Still learning"}</h3><p>${esc(profile.description||"Add your business details from Home so your presence starts accurate.")}</p></section><section><span class="simple-eyebrow">CONNECTED CHANNELS</span><h3>${connectors.filter(x=>x.status==="connected").length?"Connected":"No channels connected yet"}</h3><p>${connectors.filter(x=>x.status==="connected").map(x=>esc(x.display_name||x.label||x.connector_type)).join(" · ")||"Connect Google or email when it helps your business."}</p></section></div>${solutions.filter(x=>x.id!==presence?.id).length?`<section class="presence-existing"><h3>Other business tools</h3>${solutions.filter(x=>x.id!==presence?.id).map(x=>`<article><div><strong>${esc(x.name)}</strong><small>${esc(x.status.replaceAll("_"," "))}</small></div>${x.preview.url?`<a class="simple-link" href="${esc(x.preview.url)}" target="_blank">Open</a>`:""}</article>`).join("")}</section>`:""}`;
    document.querySelector("#create-presence")?.addEventListener("click",async event=>{event.currentTarget.disabled=true;event.currentTarget.textContent="Building your preview…";try{await api("/solutions",{method:"POST",body:JSON.stringify({solution_type:"digital_presence"})});await renderPresence()}catch(error){alert(error.message);event.currentTarget.disabled=false}});document.querySelector("#presence-publish")?.addEventListener("click",async event=>{const message=document.querySelector("#presence-publish-message");event.currentTarget.disabled=true;event.currentTarget.textContent="Publishing…";message.textContent="Publishing and checking your website…";try{const result=await api(`/solutions/${presence.id}/approve`,{method:"POST"});if(result.job.status!=="succeeded")throw new Error(result.job.failure_classification==="provider_unconfigured"?"Publishing is not configured yet.":"Publishing failed during health verification. Your previous live version is still serving.");await renderPresence()}catch(error){message.textContent=error.message;event.currentTarget.disabled=false;event.currentTarget.textContent="Publish"}});document.querySelector("#presence-rollback")?.addEventListener("click",async event=>{const message=document.querySelector("#presence-publish-message");event.currentTarget.disabled=true;event.currentTarget.textContent="Rolling back…";try{const result=await api(`/solutions/${presence.id}/rollback`,{method:"POST",body:"{}"});if(result.job.status!=="succeeded")throw new Error("Rollback could not be verified. Your current site is still serving.");await renderPresence()}catch(error){message.textContent=error.message;event.currentTarget.disabled=false;event.currentTarget.textContent="Rollback"}});document.querySelector("#presence-change")?.addEventListener("click",()=>openAssistant(`Change my digital presence${presence?` (${presence.id})`:""}: `));
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
      if (simple.dataset.simplePage === "presence") renderPresence();
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
  window.operlySimplePresence = renderPresence;
  window.operlyOpenAssistant = openAssistant;
  window.operlyOpenBuild = openBuild;
})();
