(() => {
  const originalDraw = window.drawSynthesizedSoftwarePlan;
  if (typeof originalDraw !== "function") return;

  async function applyHarnessEdit({instruction, mode = "source", context = {}, clarificationDepth = 0}) {
    const result = typeof customSoftwareState !== "undefined" ? customSoftwareState.plan : null;
    if (!result?.id || result.status !== "approved") throw new Error("Approve the software plan before editing source");
    try {
      const source = await api(`/coding-harness/plans/${result.id}/source/edits`, {
        method: "POST",
        body: JSON.stringify({planId: result.id, approvedVersion: result.approvedVersion, instruction, mode, context})
      });
      renderSource(source);
      return source;
    } catch (error) {
      const detail = error?.details || {};
      if (detail.code === "coding_agent_clarification_required" && clarificationDepth < 2) {
        const options = Array.isArray(detail.options) && detail.options.length ? `\n\nOptions:\n${detail.options.map(item => `• ${item}`).join("\n")}` : "";
        const answer = window.prompt(`${detail.question || detail.message || "OPERLY needs one clarification."}${options}`);
        if (answer?.trim()) {
          return applyHarnessEdit({
            instruction: `${instruction}\n\nOWNER CLARIFICATION:\n${answer.trim()}`,
            mode,
            context: {...context, priorClarificationQuestion: detail.question || detail.message || ""},
            clarificationDepth: clarificationDepth + 1,
          });
        }
      }
      throw error;
    }
  }

  window.operlyCodingHarnessEdit = applyHarnessEdit;

  function renderSource(source) {
    const content = document.querySelector("#content");
    if (!content || !source) return;
    content.querySelector(".coding-harness-source")?.remove();
    const section = studioNode("section", undefined, "plan-section coding-harness-source");
    const runtime = source.runtimeProfile ? ` · runtime ${source.runtimeProfile}` : " · runtime pending";
    const operation = source.sourceOperation ? ` · ${source.sourceOperation.replaceAll("_", " ")}` : "";
    section.append(
      studioNode("h3", `Coding harness · source v${source.sourceVersion}`),
      studioNode("p", `${source.harness || "coding harness"} · ${source.modelProvider || "ollama"}/${source.modelId || "unknown"} · ${source.files?.length || 0} files · ${source.totalBytes || 0} bytes${runtime}${operation}`),
      studioNode("p", source.summary || "Source tree authored. Execution has not run in the OPERLY control plane.")
    );
    if (source.changedPaths?.length) section.append(studioNode("p", `Changed: ${source.changedPaths.join(", ")}`));

    const grid = studioNode("div", undefined, "plan-grid");
    (source.files || []).forEach(file => grid.append(studioNode("article", `${file.path} · ${file.bytes} bytes`, "card plan-card")));
    section.append(grid);
    (source.verificationIntent || []).forEach(item => section.append(studioNode("p", `Verify: ${item}`)));

    if (source.runtimeProfile === "static-web-js") {
      const frame = document.createElement("iframe");
      frame.title = "Generated source preview";
      frame.src = `/api/coding-harness/sources/${source.id}/preview/index.html`;
      frame.className = "construction-preview coding-source-preview";
      section.append(studioNode("h4", "Source preview"), studioNode("p", "Rendered directly from the immutable source bundle. Build & verify still runs tests and health gates before release."), frame);
    } else {
      section.append(studioNode("p", "This source requires Build & verify before a live preview can be started.", "coding-source-preview-note"));
    }

    const editor = studioNode("div", undefined, "harness-edit-box");
    const textarea = document.createElement("textarea");
    textarea.placeholder = "Change the page, frontend behavior, backend, API, or code…";
    textarea.setAttribute("aria-label", "Coding harness edit instruction");
    const mode = document.createElement("select");
    [["visual", "Visual/page edit"], ["frontend", "Frontend edit"], ["backend", "Backend edit"], ["source", "General code edit"]].forEach(([value, label]) => {
      const option = document.createElement("option"); option.value = value; option.textContent = label; mode.append(option);
    });
    const apply = studioNode("button", "Apply with harness", "button secondary");
    apply.onclick = async () => {
      if (!textarea.value.trim()) return;
      apply.disabled = true; const prior = apply.textContent; apply.textContent = "Editing…";
      try { await applyHarnessEdit({instruction: textarea.value.trim(), mode: mode.value, context: {sourceVersion: source.sourceVersion}}); }
      catch (error) { alert(error.message); }
      finally { apply.disabled = false; apply.textContent = prior; }
    };
    editor.append(textarea, mode, apply);
    section.append(editor);

    const actions = content.querySelector(".plan-actions");
    if (actions) content.insertBefore(section, actions); else content.append(section);
  }

  function readableEvidence(value) {
    if (value === null || value === undefined || value === "") return "";
    if (typeof value === "string") return value;
    try { return JSON.stringify(value, null, 2); } catch { return String(value); }
  }

  function safeText(value, limit = 300) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  }

  function simpleSelector(element) {
    if (!element || element.nodeType !== 1) return "";
    if (element.id) return `#${String(element.id).replace(/[^A-Za-z0-9_-]/g, "_")}`;
    const parts = [];
    let current = element;
    while (current && current.nodeType === 1 && parts.length < 6) {
      let part = current.tagName.toLowerCase();
      const classes = [...current.classList].filter(Boolean).slice(0, 2).map(value => value.replace(/[^A-Za-z0-9_-]/g, "_")).filter(Boolean);
      if (classes.length) part += "." + classes.join(".");
      if (current.parentElement) {
        const same = [...current.parentElement.children].filter(child => child.tagName === current.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(current) + 1})`;
      }
      parts.unshift(part);
      if (current.tagName.toLowerCase() === "body") break;
      current = current.parentElement;
    }
    return parts.join(" > ");
  }

  function selectionContext(element, frame, build) {
    const attrs = {};
    ["role", "aria-label", "name", "type", "title", "placeholder", "href"].forEach(name => {
      const value = element.getAttribute?.(name);
      if (value) attrs[name] = safeText(value, 200);
    });
    const doc = element.ownerDocument;
    const view = doc?.defaultView;
    const computed = view?.getComputedStyle?.(element);
    const rect = element.getBoundingClientRect?.();
    const style = {};
    [
      "display", "position", "width", "height", "color", "backgroundColor", "fontFamily", "fontSize",
      "fontWeight", "lineHeight", "textAlign", "padding", "margin", "gap", "border", "borderRadius",
      "boxShadow", "overflow", "opacity", "flexDirection", "justifyContent", "alignItems", "gridTemplateColumns"
    ].forEach(name => {
      const value = computed?.[name];
      if (value && value !== "none" && value !== "normal") style[name] = safeText(value, 300);
    });
    const parent = element.parentElement;
    return {
      sourceVersion: build.source?.sourceVersion || build.sourceVersion || null,
      previewPath: frame.getAttribute("src") || "",
      page: {
        title: safeText(doc?.title, 300),
        bodyClasses: [...(doc?.body?.classList || [])].slice(0, 12).map(value => safeText(value, 80)),
      },
      viewport: {
        width: view?.innerWidth || frame.clientWidth || null,
        height: view?.innerHeight || frame.clientHeight || null,
        devicePixelRatio: view?.devicePixelRatio || 1,
      },
      selection: {
        selector: simpleSelector(element),
        tag: element.tagName?.toLowerCase() || "",
        id: safeText(element.id, 120),
        classes: [...(element.classList || [])].slice(0, 8).map(value => safeText(value, 80)),
        text: safeText(element.textContent, 500),
        outerHTML: safeText(element.outerHTML, 2200),
        attributes: attrs,
        rect: rect ? {x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height)} : {},
        computedStyle: style,
        parent: parent ? {selector: simpleSelector(parent), tag: parent.tagName?.toLowerCase() || "", text: safeText(parent.textContent, 400)} : null,
      }
    };
  }

  function addVisualEditor(section, frame, build) {
    const controls = studioNode("div", undefined, "visual-harness-editor");
    const header = studioNode("div", undefined, "visual-harness-toolbar");
    const selectButton = studioNode("button", "Select on page", "button secondary");
    const selectedLabel = studioNode("span", "No element selected", "visual-selection-label");
    header.append(selectButton, selectedLabel);

    const instruction = document.createElement("textarea");
    instruction.placeholder = "Describe what should change about the selected element…";
    instruction.setAttribute("aria-label", "Visual edit instruction");
    instruction.disabled = true;
    const apply = studioNode("button", "Apply visual edit", "button primary");
    apply.disabled = true;
    const footer = studioNode("div", undefined, "visual-harness-actions");
    footer.append(instruction, apply);
    controls.append(header, footer);
    section.insertBefore(controls, frame);

    let selecting = false;
    let selected = null;
    let restoreOutline = null;

    function clearHighlight() {
      if (selected && restoreOutline) {
        selected.style.outline = restoreOutline.outline;
        selected.style.outlineOffset = restoreOutline.outlineOffset;
      }
      selected = null;
      restoreOutline = null;
    }

    function setSelection(element) {
      clearHighlight();
      selected = element;
      restoreOutline = {outline: element.style.outline, outlineOffset: element.style.outlineOffset};
      element.style.outline = "2px solid currentColor";
      element.style.outlineOffset = "2px";
      const context = selectionContext(element, frame, build);
      selectedLabel.textContent = context.selection.selector || context.selection.tag || "Selected element";
      instruction.disabled = false;
      apply.disabled = !instruction.value.trim();
    }

    function attachSelectionListener() {
      try {
        const doc = frame.contentDocument;
        if (!doc) return;
        doc.addEventListener("click", event => {
          if (!selecting) return;
          const element = event.target?.closest?.("*");
          if (!element) return;
          event.preventDefault();
          event.stopPropagation();
          event.stopImmediatePropagation();
          setSelection(element);
          selecting = false;
          selectButton.textContent = "Select another";
        }, true);
      } catch (_) {
        selectedLabel.textContent = "Visual selection unavailable for this preview";
        selectButton.disabled = true;
      }
    }

    frame.addEventListener("load", attachSelectionListener);
    selectButton.onclick = () => {
      selecting = true;
      selectButton.textContent = "Click an element in preview…";
      selectedLabel.textContent = selected ? selectedLabel.textContent : "Waiting for selection";
    };
    instruction.addEventListener("input", () => { apply.disabled = !selected || !instruction.value.trim(); });
    apply.onclick = async () => {
      if (!selected || !instruction.value.trim()) return;
      const context = selectionContext(selected, frame, build);
      apply.disabled = true; const prior = apply.textContent; apply.textContent = "Editing…";
      try {
        const source = await applyHarnessEdit({instruction: instruction.value.trim(), mode: "visual", context});
        selectedLabel.textContent = `Source v${source.sourceVersion} updated · rebuild preview to verify`;
        instruction.value = "";
        instruction.disabled = true;
        clearHighlight();
      } catch (error) {
        alert(error.message);
      } finally {
        apply.textContent = prior;
        apply.disabled = true;
      }
    };
  }

  async function renderHarnessRunnerBuild(content, build) {
    content.querySelector(".runner-evidence")?.remove();
    const section = studioNode("section", undefined, "plan-section construction-evidence runner-evidence");
    let events = [];
    if (build.id) { try { events = await api(`/custom-software/builds/${build.id}/events`); } catch (_) { events = []; } }
    const failure = build.result?.failureEvidence || {};
    const classification = build.failureClassification || failure.classification || "";
    const failed = !["preview_ready", "running", "completed"].includes(build.state);

    section.append(
      studioNode("h3", `Runner state: ${build.state}`),
      studioNode("p", `${build.runnerImplementation} · ${build.isolationProfile} · source v${build.sourceVersion || build.source?.sourceVersion || "?"} · attempt ${build.attempt}`),
      studioNode("p", `Runtime: ${build.source?.runtimeProfile || build.result?.runtime || "detected source profile"} · Network: ${build.networkPolicy?.mode || "unknown"} · CPU: ${build.resourcePolicy?.cpu || "?"} · memory: ${build.resourcePolicy?.memoryMb || "?"} MB · timeout: ${build.resourcePolicy?.durationSeconds || "?"}s`)
    );

    if (build.repairCount) {
      section.append(studioNode("h4", `Automatic repair loop · ${build.repairCount} repair${build.repairCount === 1 ? "" : "s"}`));
      (build.repairAttempts || []).forEach(item => section.append(studioNode("p", `Repair ${item.repairNumber}: ${item.classification} · source v${item.fromSourceVersion} → v${item.toSourceVersion} · ${item.changedPaths?.join(", ") || "source changed"}`)));
    }

    if (build.state === "preview_ready") section.append(studioNode("p", "Preview verified in an isolated runner. Select an element below to edit the page through the same coding harness."));
    else if (build.result?.code === "runner_unavailable") section.append(studioNode("p", "runner_unavailable — generated code was not executed in the control plane.", "builder-error"));
    else if (failed) {
      const lastFailureEvent = [...events].reverse().find(item => item.eventType === "failure" || /failed|blocked|exceeded|timed_out/.test(item.state || ""));
      section.append(studioNode("h4", "Failure evidence"));
      if (classification) section.append(studioNode("p", `Classification: ${classification}`, "builder-error"));
      if (lastFailureEvent) section.append(studioNode("p", `Failed phase: ${lastFailureEvent.state} · ${lastFailureEvent.message}`, "builder-error"));
      const evidenceText = readableEvidence(failure);
      if (evidenceText && evidenceText !== "{}") {
        const pre = studioNode("pre", evidenceText, "builder-error runner-failure-evidence"); pre.style.whiteSpace = "pre-wrap"; pre.style.overflowWrap = "anywhere"; section.append(pre);
      } else if (lastFailureEvent?.details && Object.keys(lastFailureEvent.details).length) {
        const pre = studioNode("pre", readableEvidence(lastFailureEvent.details), "builder-error runner-failure-evidence"); pre.style.whiteSpace = "pre-wrap"; pre.style.overflowWrap = "anywhere"; section.append(pre);
      }
    }

    section.append(planList("Phase history", events, item => `${item.sequence} · ${item.state} · ${item.message}${item.details && Object.keys(item.details).length ? ` · ${readableEvidence(item.details)}` : ""}`));
    if (build.result?.staticAnalysisReport && Object.keys(build.result.staticAnalysisReport).length) section.append(studioNode("h4", "Static analysis"), studioNode("pre", readableEvidence(build.result.staticAnalysisReport)));
    if (build.result?.testReport && Object.keys(build.result.testReport).length) section.append(studioNode("h4", "Test report"), studioNode("pre", readableEvidence(build.result.testReport)));
    if (build.preview) {
      const frame = document.createElement("iframe");
      frame.title = "Isolated generated application preview";
      frame.src = build.preview.url;
      frame.className = "construction-preview";
      section.append(frame, studioNode("p", `Preview expires ${build.preview.expiresAt}`));
      addVisualEditor(section, frame, build);
    }
    content.append(section);
  }

  window.renderRunnerBuild = renderHarnessRunnerBuild;

  async function loadExistingSource(result) {
    try { renderSource(await api(`/coding-harness/plans/${result.id}/source`)); } catch (_) {}
  }

  function renderCodingProgress(content, state, elapsedSeconds, detail = "", activity = []) {
    let panel = content.querySelector(".coding-harness-progress");
    if (!panel) {
      panel = studioNode("section", undefined, "plan-section coding-harness-progress");
      panel.setAttribute("role", "status"); panel.setAttribute("aria-live", "polite");
      const actions = content.querySelector(".plan-actions");
      if (actions) content.insertBefore(panel, actions); else content.append(panel);
    }
    panel.replaceChildren(
      studioNode("h3", state === "queued" ? "Coding queued" : state === "generating" ? "Coding your Solution" : state === "failed" ? "Coding stopped" : "Source ready"),
      studioNode("p", detail || (state === "generating" ? "The coding agent is creating and validating files in a persistent source workspace." : "Preparing the coding workspace.")),
      studioNode("p", `${elapsedSeconds}s elapsed · You can leave this screen and return; status is stored by OPERLY.`, "coding-progress-meta")
    );
    const recent = (activity || []).slice(-5).reverse();
    if (recent.length) {
      const list = studioNode("ol", undefined, "coding-activity-list");
      recent.forEach((item, index) => {
        const row = studioNode("li", item.summary || "Continuing the coding session.");
        if (index === 0) row.classList.add("current");
        list.append(row);
      });
      panel.append(studioNode("h4", "Current activity"), list);
    }
  }

  async function generateSourceInBackground(result, content, code) {
    const startedAt = Date.now();
    let job = await api(`/coding-harness/plans/${result.id}/source-jobs`, {method: "POST", body: JSON.stringify({planId: result.id, approvedVersion: result.approvedVersion})});
    while (["queued", "generating"].includes(job.state)) {
      const elapsed = Math.floor((Date.now() - startedAt) / 1000);
      const current = job.result?.current;
      renderCodingProgress(content, job.state, elapsed, current?.summary || "", job.result?.activity || []); code.textContent = `Coding · ${elapsed}s`;
      await new Promise(resolve => setTimeout(resolve, 2000));
      job = await api(`/coding-harness/source-jobs/${job.id}`);
    }
    const elapsed = Math.floor((Date.now() - startedAt) / 1000);
    if (job.state !== "completed" || !job.result?.source) {
      renderCodingProgress(content, "failed", elapsed, job.failure || "Source generation failed without usable output.");
      throw new Error(job.failure || "Source generation failed");
    }
    renderCodingProgress(content, "completed", elapsed, "The immutable source workspace is ready and previewable.");
    renderSource(job.result.source);
  }

  function cleanPlanPresentation(result) {
    const content = document.querySelector("#content"); if (!content) return;
    content.querySelectorAll(".plan-section").forEach(section => { const grid = section.querySelector(":scope > .plan-grid"); if (grid && grid.children.length === 0) section.remove(); });
    const actions = content.querySelector(".plan-actions"); if (!actions) return;
    const approve = [...actions.querySelectorAll("button")].find(button => ["Approve this plan", "Approve & continue"].includes(button.textContent.trim())); if (!approve) return;
    const ready = result?.plan?.planningMetrics?.globalValidationPassed === true && result?.plan?.globalValidation?.passed !== false;
    approve.disabled = !ready; approve.textContent = ready ? "Approve & continue" : "Approval blocked"; approve.title = ready ? "Approve this validated plan and continue to coding" : "Global validation must pass before approval";
  }

  function enhanceHarness() {
    const result = typeof customSoftwareState !== "undefined" ? customSoftwareState.plan : null;
    if (!result) return; cleanPlanPresentation(result); if (result.status !== "approved") return;
    if ((result.plan || {}).implementationMode === "architecture_pack") return;
    const content = document.querySelector("#content"), actions = content?.querySelector(".plan-actions"); if (!actions) return;
    [...actions.querySelectorAll("button")].forEach(button => { if (["Create contract artifacts", "Submit isolated build", "Send approved plan to sandbox"].includes(button.textContent.trim())) button.remove(); });
    if (actions.querySelector("[data-coding-harness-code]")) return;

    const code = studioNode("button", "Code with harness", "button primary"); code.dataset.codingHarnessCode = "1";
    code.onclick = async () => {
      code.disabled = true; const previous = code.textContent; code.textContent = "Coding…";
      try { await generateSourceInBackground(result, content, code); code.textContent = "Regenerate source"; }
      catch (error) { code.textContent = previous; alert(error.message); }
      finally { code.disabled = false; }
    };

    const build = studioNode("button", "Build & verify", "button secondary"); build.dataset.codingHarnessBuild = "1";
    build.onclick = async () => {
      build.disabled = true; const previous = build.textContent; build.textContent = "Building & repairing…";
      try {
        const row = await api("/coding-harness/builds", {method: "POST", body: JSON.stringify({planId: result.id, approvedVersion: result.approvedVersion, idempotencyKey: `coding-harness-${result.id}-${result.approvedVersion}-${Date.now()}`})});
        if (row.source) renderSource(row.source); await renderHarnessRunnerBuild(content, row);
      } catch (error) { alert(error.message); }
      finally { build.disabled = false; build.textContent = previous; }
    };
    actions.append(code, build); loadExistingSource(result);
  }

  window.drawSynthesizedSoftwarePlan = function (...args) { const value = originalDraw.apply(this, args); enhanceHarness(); return value; };
})();
