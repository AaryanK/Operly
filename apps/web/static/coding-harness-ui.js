(() => {
  const originalDraw = window.drawSynthesizedSoftwarePlan;
  if (typeof originalDraw !== "function") return;

  function renderSource(source) {
    const content = document.querySelector("#content");
    if (!content || !source) return;
    content.querySelector(".coding-harness-source")?.remove();
    const section = studioNode("section", undefined, "plan-section coding-harness-source");
    section.append(
      studioNode("h3", `Coding harness · source v${source.sourceVersion}`),
      studioNode("p", `${source.harness || "coding harness"} · ${source.files?.length || 0} files · ${source.totalBytes || 0} bytes`),
      studioNode("p", source.summary || "Source tree authored. Execution has not run in the OPERLY control plane.")
    );
    const grid = studioNode("div", undefined, "plan-grid");
    (source.files || []).forEach(file => {
      grid.append(studioNode("article", `${file.path} · ${file.bytes} bytes`, "card plan-card"));
    });
    section.append(grid);
    (source.verificationIntent || []).forEach(item => section.append(studioNode("p", `Verify: ${item}`)));
    const actions = content.querySelector(".plan-actions");
    if (actions) content.insertBefore(section, actions);
    else content.append(section);
  }

  async function loadExistingSource(result) {
    try {
      const source = await api(`/coding-harness/plans/${result.id}/source`);
      renderSource(source);
    } catch (_) {
      // A source bundle is optional until the owner explicitly starts coding.
    }
  }

  function enhanceHarness() {
    const result = typeof customSoftwareState !== "undefined" ? customSoftwareState.plan : null;
    if (!result || result.status !== "approved") return;
    const plan = result.plan || {};
    if (plan.implementationMode === "architecture_pack") return;
    const content = document.querySelector("#content");
    const actions = content?.querySelector(".plan-actions");
    if (!actions) return;

    [...actions.querySelectorAll("button")].forEach(button => {
      if (["Create contract artifacts", "Submit isolated build", "Send approved plan to sandbox"].includes(button.textContent.trim())) button.remove();
    });
    if (actions.querySelector("[data-coding-harness-code]")) return;

    const code = studioNode("button", "Code with harness", "button primary");
    code.dataset.codingHarnessCode = "1";
    code.onclick = async () => {
      code.disabled = true;
      const previous = code.textContent;
      code.textContent = "Coding…";
      try {
        const source = await api(`/coding-harness/plans/${result.id}/source`, {
          method: "POST",
          body: JSON.stringify({planId: result.id, approvedVersion: result.approvedVersion})
        });
        renderSource(source);
        code.textContent = "Regenerate source";
      } catch (error) {
        code.textContent = previous;
        alert(error.message);
      } finally {
        code.disabled = false;
      }
    };

    const build = studioNode("button", "Build in isolated runner", "button secondary");
    build.dataset.codingHarnessBuild = "1";
    build.onclick = async () => {
      build.disabled = true;
      const previous = build.textContent;
      build.textContent = "Building…";
      try {
        const row = await api("/coding-harness/builds", {
          method: "POST",
          body: JSON.stringify({
            planId: result.id,
            approvedVersion: result.approvedVersion,
            idempotencyKey: `coding-harness-${result.id}-${result.approvedVersion}-${Date.now()}`
          })
        });
        if (row.source) renderSource(row.source);
        if (typeof renderRunnerBuild === "function") await renderRunnerBuild(content, row);
      } catch (error) {
        alert(error.message);
      } finally {
        build.disabled = false;
        build.textContent = previous;
      }
    };

    actions.append(code, build);
    loadExistingSource(result);
  }

  window.drawSynthesizedSoftwarePlan = function (...args) {
    const value = originalDraw.apply(this, args);
    enhanceHarness();
    return value;
  };
})();
