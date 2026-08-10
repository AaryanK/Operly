/* Focused Studio shell layered over the existing builders. */
(() => {
  const STAGES = [
    "Understanding the request",
    "Extracting requirements",
    "Shaping the software plan",
    "Resolving dependencies",
    "Validating implementation contracts",
    "Checking the complete system"
  ];

  let planningTimer = null;
  let elapsedTimer = null;

  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  function normalizeBuildShell() {
    const nav = document.querySelector('#nav [data-page="studio"]');
    if (nav) nav.textContent = "Build";
    const title = document.querySelector("#page-title");
    if (title) title.textContent = "Build";
  }

  function dismissPlanningOverlay() {
    if (planningTimer) clearInterval(planningTimer);
    if (elapsedTimer) clearInterval(elapsedTimer);
    planningTimer = null;
    elapsedTimer = null;
    document.querySelector(".planning-overlay")?.remove();
  }

  function showPlanningOverlay() {
    dismissPlanningOverlay();
    const overlay = el("div", undefined, "planning-overlay");
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "OPERLY planning progress");

    const card = el("section", undefined, "planning-card");
    const mark = el("div", "O", "planning-mark");
    const kicker = el("span", "OPERLY SOFTWARE STUDIO", "planning-kicker");
    const title = el("h2", "Shaping your software plan");
    const copy = el("p", "OPERLY is working through the request before anything is built.", "planning-copy");
    const stage = el("div", undefined, "planning-stage");
    const pulse = el("span", undefined, "planning-pulse");
    const stageText = el("strong", STAGES[0]);
    stage.append(pulse, stageText);
    const meta = el("div", undefined, "planning-meta");
    const elapsed = el("span", "0s elapsed");
    const note = el("span", "Complex plans can take a few minutes.");
    meta.append(elapsed, note);
    card.append(mark, kicker, title, copy, stage, meta);
    overlay.append(card);
    document.body.append(overlay);

    let stageIndex = 0;
    let seconds = 0;
    planningTimer = setInterval(() => {
      stageIndex = Math.min(stageIndex + 1, STAGES.length - 1);
      stageText.textContent = STAGES[stageIndex];
    }, 6000);
    elapsedTimer = setInterval(() => {
      seconds += 1;
      elapsed.textContent = `${seconds}s elapsed`;
    }, 1000);
    return { overlay, card };
  }

  function showPlanningError(error, retry) {
    const overlay = document.querySelector(".planning-overlay");
    if (!overlay) return;
    if (planningTimer) clearInterval(planningTimer);
    if (elapsedTimer) clearInterval(elapsedTimer);
    planningTimer = null;
    elapsedTimer = null;

    const card = overlay.querySelector(".planning-card");
    card.replaceChildren();
    const mark = el("div", "!", "planning-mark planning-mark-error");
    const kicker = el("span", "PLANNING STOPPED", "planning-kicker planning-kicker-error");
    const title = el("h2", "OPERLY could not finish this plan");
    const copy = el("p", error?.message || "The planning request failed.", "planning-error-copy");
    const actions = el("div", undefined, "planning-error-actions");
    const close = el("button", "Back to prompt", "button secondary");
    const again = el("button", "Try again", "button primary");
    close.type = again.type = "button";
    close.onclick = dismissPlanningOverlay;
    again.onclick = () => {
      dismissPlanningOverlay();
      retry?.();
    };
    actions.append(close, again);
    card.append(mark, kicker, title, copy, actions);
  }

  function projectCard(project, type, onOpen) {
    const card = el("button", undefined, "studio-project-card");
    card.type = "button";
    const top = el("div", undefined, "studio-project-card-top");
    top.append(el("span", type, "studio-project-type"), el("span", "→", "studio-project-arrow"));
    const title = el("h3", project.name || "Untitled project");
    const meta = el(
      "p",
      project.version ? `Version ${project.version}` : (project.description || project.status || "Open project")
    );
    card.append(top, title, meta);
    card.onclick = onOpen;
    return card;
  }

  async function focusedStudioHome() {
    normalizeBuildShell();
    const content = document.querySelector("#content");
    content.replaceChildren(el("div", "Loading Build…", "studio-loading"));

    let siteRows = [], managedApps = [], customProjects = [];
    try {
      [siteRows, managedApps, customProjects] = await Promise.all([
        api("/studio/projects"),
        api("/application-builder/applications"),
        api("/custom-software/projects")
      ]);
    } catch (error) {
      content.replaceChildren();
      const box = el("section", undefined, "studio-load-error");
      box.append(el("h2", "Build could not load"), el("p", error.message || "Request failed."));
      const retry = el("button", "Retry", "button primary");
      retry.onclick = focusedStudioHome;
      box.append(retry);
      content.append(box);
      return;
    }

    const sites = Array.isArray(siteRows) ? siteRows : [];
    const apps = Array.isArray(managedApps) ? managedApps : [];
    const custom = Array.isArray(customProjects) ? customProjects : [];
    content.replaceChildren();

    const launch = el("section", undefined, "studio-launch");
    const copy = el("div", undefined, "studio-launch-copy");
    copy.append(
      el("span", "BUILD", "studio-eyebrow"),
      el("h2", "What should OPERLY build?"),
      el("p", "Describe the software you want. OPERLY will plan it, let you approve the specification, author the code with the coding harness, and run it in the isolated runner.")
    );

    const flow = el("div", undefined, "studio-build-flow");
    ["1 · Plan", "2 · Approve", "3 · Code", "4 · Run"].forEach(step => flow.append(el("span", step, "pill")));
    copy.append(flow);

    const form = el("form", undefined, "studio-prompt-form");
    const input = document.createElement("textarea");
    input.id = "studio-software-prompt";
    input.required = true;
    input.rows = 6;
    input.placeholder = "Example: Build a calculator with HTML, CSS and JavaScript, served by a small Python backend. Include tests and handle division by zero.";
    input.setAttribute("aria-label", "Describe the software to build");
    const promptFooter = el("div", undefined, "studio-prompt-footer");
    promptFooter.append(el("span", "Nothing executes until you approve the plan.", "studio-prompt-hint"));
    const submit = el("button", "Start build", "button primary studio-generate");
    submit.type = "submit";
    promptFooter.append(submit);
    form.append(input, promptFooter);
    launch.append(copy, form);

    form.onsubmit = async event => {
      event.preventDefault();
      const prompt = input.value.trim();
      if (!prompt) return;
      submit.disabled = true;
      showPlanningOverlay();
      try {
        customSoftwareState.plan = await api("/custom-software/plans", {
          method: "POST",
          body: JSON.stringify({ prompt })
        });
        dismissPlanningOverlay();
        drawSynthesizedSoftwarePlan();
      } catch (error) {
        showPlanningError(error, () => form.requestSubmit());
      } finally {
        submit.disabled = false;
      }
    };

    const secondary = el("div", undefined, "studio-secondary-actions");
    secondary.append(el("span", "Other builders", "studio-secondary-label"));
    const blank = el("button", "Blank managed app", "studio-text-action");
    blank.type = "button";
    blank.onclick = () => showManagedApplicationCreate(launch);
    const website = el("button", "Website", "studio-text-action");
    website.type = "button";
    website.onclick = createStudioProject;
    secondary.append(blank, website);
    launch.append(secondary);
    content.append(launch);

    const recent = el("section", undefined, "studio-section-block");
    const recentHead = el("div", undefined, "studio-section-heading");
    const recentCopy = el("div");
    recentCopy.append(el("span", "RECENT", "studio-eyebrow"), el("h2", "Software projects"));
    recentHead.append(recentCopy, el("span", `${custom.length} total`, "studio-count"));
    recent.append(recentHead);

    const grid = el("div", undefined, "studio-project-grid");
    if (custom.length) {
      custom.slice(0, 8).forEach(project => {
        const type = (project.vertical || "software").replaceAll("_", " ");
        grid.append(projectCard(project, type, () => openCustomSoftware(project.id)));
      });
    } else {
      const empty = el("div", undefined, "studio-empty-state");
      empty.append(el("h3", "No software projects yet"), el("p", "Your first generated plan will appear here."));
      grid.append(empty);
    }
    recent.append(grid);
    content.append(recent);

    const legacy = document.createElement("details");
    legacy.className = "studio-legacy";
    const summary = document.createElement("summary");
    const summaryText = el("div");
    summaryText.append(el("strong", "Legacy builders"), el("span", "Managed applications and websites"));
    summary.append(summaryText, el("span", `${apps.length + sites.length} items`, "studio-count"));
    legacy.append(summary);

    const legacyBody = el("div", undefined, "studio-legacy-body");
    const appSection = el("section");
    appSection.append(el("h3", "Managed applications"));
    const appGrid = el("div", undefined, "studio-project-grid studio-project-grid-compact");
    apps.forEach(project => appGrid.append(projectCard(project, "managed runtime", () => openManagedApplication(project.id))));
    if (!apps.length) appGrid.append(el("p", "No managed applications.", "studio-muted"));
    appSection.append(appGrid);

    const siteSection = el("section");
    siteSection.append(el("h3", "Websites"));
    const siteGrid = el("div", undefined, "studio-project-grid studio-project-grid-compact");
    sites.forEach(project => siteGrid.append(projectCard(project, project.status || "website", () => openStudioProject(project.id))));
    if (!sites.length) siteGrid.append(el("p", "No websites.", "studio-muted"));
    siteSection.append(siteGrid);
    legacyBody.append(appSection, siteSection);
    legacy.append(legacyBody);
    content.append(legacy);
  }

  const previousRenderPage = window.renderPage;
  if (typeof previousRenderPage === "function") {
    window.renderPage = async function(page) {
      document.querySelector("#dashboard")?.classList.toggle("studio-focus", page === "studio");
      if (page === "studio") normalizeBuildShell();
      return previousRenderPage(page);
    };
  }

  normalizeBuildShell();
  window.studioHome = focusedStudioHome;
  window.showCustomSoftwareCreate = () => {
    document.querySelector("#studio-software-prompt")?.focus();
  };
})();
