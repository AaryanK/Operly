(() => {
  const originalDraw = window.drawSynthesizedSoftwarePlan;
  if (typeof originalDraw !== "function") return;

  async function applyHarnessEdit({instruction, mode = "source", context = {}}) {
    const result = typeof customSoftwareState !== "undefined" ? customSoftwareState.plan : null;
    if (!result?.id || result.status !== "approved") throw new Error("Approve the software plan before editing source");
    const source = await api(`/coding-harness/plans/${result.id}/source/edits`, {
      method: "POST",
      body: JSON.stringify({planId: result.id, approvedVersion: result.approvedVersion, instruction, mode, context})
    });
    renderSource(source);
    return source;
  }

  // Future canvas/DOM selection code can call this same bridge with
  // mode="visual" and selection metadata. Backend/source editors use the same
  // immutable source workspace instead of a second builder implementation.
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
      studioNode("p", `${source.harness || "coding harness"} · ${source.modelProvider || "ollama"} · ${source.files?.length || 0} files · ${source.totalBytes || 0} bytes${runtime}${operation}`),
      studioNode("p", source.summary || "Source tree authored. Execution has not run in the OPERLY control plane.")
    );
    if (source.changedPaths?.length) section.append(studioNode("p", `Changed: ${source.changedPaths.join(", ")}`));

    const grid = studioNode("div", undefined, "plan-grid");
    (source.files || []).forEach(file => grid.append(studioNode("article", `${file.path} · ${file.bytes} bytes`, "card plan-card")));
    section.append(grid);
    (source.verificationIntent || []).forEach(item => section.append(studioNode("p", `Verify: ${item}`)));

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

    if (build.state === "preview_ready") section.append(studioNode("p", "Preview verified in an isolated runner. Production deployment has not been performed."));
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
      const frame = document.createElement("iframe"); frame.title = "Isolated generated application preview"; frame.src = build.preview.url; frame.className = "construction-preview"; section.append(frame, studioNode("p", `Preview expires ${build.preview.expiresAt}`));
    }
    content.append(section);
  }

  window.renderRunnerBuild = renderHarnessRunnerBuild;

  async function loadExistingSource(result) {
    try { renderSource(await api(`/coding-harness/plans/${result.id}/source`)); } catch (_) {}
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
      try { renderSource(await api(`/coding-harness/plans/${result.id}/source`, {method: "POST", body: JSON.stringify({planId: result.id, approvedVersion: result.approvedVersion})})); code.textContent = "Regenerate source"; }
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
