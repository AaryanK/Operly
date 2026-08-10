(() => {
  const nativeFetch = window.fetch.bind(window);

  function requestUrl(input) {
    if (typeof input === "string") return input;
    try { return input?.url || ""; } catch (_) { return ""; }
  }

  function requestMethod(input, init) {
    return String(init?.method || input?.method || "GET").toUpperCase();
  }

  function clarificationMessage(body) {
    const detail = body?.detail;
    return String((typeof detail === "string" ? detail : detail?.message) || body?.message || "");
  }

  // Legacy /custom-software/plans can still raise after durably storing a waiting
  // plan. Normalize that one known condition into the real persisted state.
  window.fetch = async function operlyClarificationAwareFetch(input, init = {}) {
    const response = await nativeFetch(input, init);
    const url = requestUrl(input);
    if (response.ok || requestMethod(input, init) !== "POST" || !url.includes("/api/custom-software/plans")) {
      return response;
    }

    let body = null;
    try { body = await response.clone().json(); } catch (_) {}
    if (!clarificationMessage(body).toLowerCase().includes("user input required before planning")) return response;

    const pending = await nativeFetch("/api/coding-harness/planning-clarification", {
      method: "GET",
      credentials: "same-origin",
      headers: {"Accept": "application/json"}
    });
    if (!pending.ok) return response;
    const clarification = await pending.json();
    return new Response(JSON.stringify(clarification), {
      status: 200,
      headers: {"Content-Type": "application/json"}
    });
  };

  function node(tag, text, cls) {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (cls) item.className = cls;
    return item;
  }

  function normalized(value) {
    return " " + String(value || "").toLowerCase().replace(/\s+/g, " ").trim() + " ";
  }

  function isPlacementQuestion(question) {
    const text = normalized(question);
    return /(standalone|existing website|internal tool|where .* live|integrated into|part of .* website)/.test(text);
  }

  function optionsForQuestion(question) {
    const text = normalized(question);
    let choices = [];

    if (isPlacementQuestion(question)) {
      choices = [
        ["Existing website", "Add this capability to my existing website."],
        ["Internal tool", "Create this as a private internal OPERLY tool."],
        ["Standalone app", "Create this as a standalone application."],
      ];
    } else if (/third-party.*api|external api|external service/.test(text)) {
      choices = [
        ["Only if technically necessary", "Use a third-party API only when the requested capability cannot be implemented reliably with OPERLY's existing capabilities and local code."],
        ["Avoid third parties", "Do not use third-party APIs for this capability."],
        ["Allowed when useful", "Third-party APIs are allowed when they materially improve the requested capability."],
      ];
    } else if (/interface|protocol|mcp|typed tool|api/.test(text) && /operly/.test(text)) {
      choices = [
        ["Typed tool / API", "Expose the capability to OPERLY through a typed tool or API boundary."],
        ["MCP-style capability", "Expose the capability through an MCP-style typed capability interface."],
        ["Integrated directly", "Keep the OPERLY interaction directly inside the generated application when that is the simplest reliable design."],
      ];
    } else if (/architectural decision|ask.*instead of|query rather than|guess/.test(text)) {
      choices = [
        ["Only consequential choices", "Ask me only when the choice materially changes placement, permissions, security, data ownership, cost, or user-visible behavior."],
        ["Only when inference is unsafe", "Ask only when OPERLY cannot safely infer the decision from my request, workspace, and platform defaults."],
        ["Ask more often", "Ask whenever multiple materially different implementations remain plausible."],
      ];
    }

    choices.push([
      "Let OPERLY decide",
      "Use OPERLY's best judgment from my request, existing workspace, security boundaries, and platform defaults; explain any consequential choice in the plan."
    ]);
    choices.push(["Other…", "__other__"]);
    return choices;
  }

  function clarificationTitle(questions) {
    if (questions.length === 1 && isPlacementQuestion(questions[0])) return "Where should this capability live?";
    if (questions.length > 1) return "A couple of decisions before I continue";
    return "One decision before I continue";
  }

  function removeClarification() {
    document.querySelector(".normal-clarification-overlay")?.remove();
  }

  function progressStage(role) {
    const stages = {
      requirements_analyst: "Understanding the request",
      planner: "Shaping the capability plan",
      validator: "Validating capability boundaries",
      requirement_partitioner: "Separating implementation responsibilities",
      contract_expander: "Completing implementation contracts",
      contract_patcher: "Repairing a missing contract detail",
      global_validator: "Checking the complete system",
    };
    return stages[role] || "Continuing the plan";
  }

  function tokenLabel(value) {
    const number = Number(value || 0);
    if (number < 1000) return String(number);
    return `${(number / 1000).toFixed(number >= 10000 ? 1 : 2)}k`;
  }

  function startProgress(card, planId) {
    const panel = node("div", undefined, "planning-live-progress");
    const pulse = node("span", undefined, "planning-pulse");
    const stage = node("strong", "Continuing from your decision…");
    const line = node("div", undefined, "planning-progress-stage");
    line.append(pulse, stage);
    const meta = node("span", "Waiting for the next planning step", "planning-progress-meta");
    panel.append(line, meta);

    card.querySelector(".planning-live-progress")?.remove();
    const actions = card.querySelector(".planning-clarification-actions");
    if (actions) card.insertBefore(panel, actions); else card.append(panel);

    let stopped = false;
    let timer = null;

    async function refresh() {
      if (stopped) return;
      try {
        const rows = await api(`/custom-software/plans/${encodeURIComponent(planId)}/provenance`);
        if (!Array.isArray(rows) || !rows.length) return;
        const latest = rows[rows.length - 1];
        const tokens = rows.reduce((sum, item) => sum + Number(item.inputTokens || 0) + Number(item.outputTokens || 0), 0);
        stage.textContent = progressStage(latest.role);
        meta.textContent = `${rows.length} model call${rows.length === 1 ? "" : "s"} · ~${tokenLabel(tokens)} estimated tokens`;
      } catch (_) {}
    }

    refresh();
    timer = setInterval(refresh, 1200);
    return () => {
      stopped = true;
      if (timer) clearInterval(timer);
    };
  }

  function renderClarification(clarification) {
    removeClarification();
    document.querySelector(".planning-overlay")?.remove();

    const overlay = node("div", undefined, "planning-overlay normal-clarification-overlay");
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "OPERLY needs a decision");

    const card = node("section", undefined, "planning-card planning-decision-card");
    const mark = node("div", "?", "planning-mark planning-mark-question");
    const kicker = node("span", "ONE DECISION BEFORE I CONTINUE", "planning-kicker");
    const questions = Array.isArray(clarification?.questions) ? clarification.questions.filter(Boolean) : [];
    const title = node("h2", clarificationTitle(questions));
    const copy = node(
      "p",
      "OPERLY is pausing only where your choice materially affects the result. Pick an option, let OPERLY decide, or describe something else.",
      "planning-copy"
    );

    const form = node("form", undefined, "planning-clarification-form");
    const list = node("div", undefined, "planning-decision-list");
    const answers = new Map();

    questions.forEach((question, index) => {
      const block = node("fieldset", undefined, "planning-decision-question");
      const legend = document.createElement("legend");
      legend.append(node("span", String(index + 1), "planning-question-number"), node("strong", question));
      block.append(legend);

      const optionGrid = node("div", undefined, "planning-option-grid");
      const otherWrap = node("div", undefined, "planning-other-wrap hidden");
      const otherInput = document.createElement("textarea");
      otherInput.rows = 3;
      otherInput.maxLength = 2000;
      otherInput.placeholder = "Describe what you want instead…";
      otherInput.setAttribute("aria-label", `Other answer for question ${index + 1}`);
      otherWrap.append(otherInput);

      optionsForQuestion(question).forEach(([label, value]) => {
        const button = node("button", label, "planning-option");
        button.type = "button";
        button.onclick = () => {
          optionGrid.querySelectorAll(".planning-option").forEach(item => item.classList.toggle("selected", item === button));
          otherWrap.classList.toggle("hidden", value !== "__other__");
          if (value === "__other__") {
            answers.set(index, {question, value: "__other__", otherInput});
            setTimeout(() => otherInput.focus(), 0);
          } else {
            answers.set(index, {question, value});
          }
        };
        optionGrid.append(button);
      });

      block.append(optionGrid, otherWrap);
      list.append(block);
    });

    const note = document.createElement("textarea");
    note.rows = 2;
    note.maxLength = 2000;
    note.placeholder = "Additional context (optional)…";
    note.className = "planning-additional-context";
    note.setAttribute("aria-label", "Additional clarification context");

    const actions = node("div", undefined, "planning-clarification-actions");
    const back = node("button", "Back to prompt", "button secondary");
    const submit = node("button", "Continue", "button primary");
    back.type = "button";
    submit.type = "submit";
    back.onclick = removeClarification;
    actions.append(back, submit);
    form.append(list, note, actions);

    form.onsubmit = async event => {
      event.preventDefault();
      const missing = questions.findIndex((_, index) => !answers.has(index));
      if (missing >= 0) {
        list.children[missing]?.scrollIntoView({behavior: "smooth", block: "center"});
        list.children[missing]?.classList.add("needs-answer");
        return;
      }

      const lines = [];
      for (let index = 0; index < questions.length; index += 1) {
        const selected = answers.get(index);
        let value = selected.value;
        if (value === "__other__") value = selected.otherInput.value.trim();
        if (!value) {
          selected.otherInput?.focus();
          return;
        }
        lines.push(`Question ${index + 1}: ${selected.question}`);
        lines.push(`Owner answer ${index + 1}: ${value}`);
      }
      if (note.value.trim()) lines.push(`Additional context: ${note.value.trim()}`);

      submit.disabled = true;
      back.disabled = true;
      submit.textContent = "Continuing…";
      form.querySelectorAll("button, textarea").forEach(item => { if (item !== submit) item.disabled = true; });
      const stopProgress = startProgress(card, clarification.planId);

      try {
        const next = await api(`/coding-harness/plans/${encodeURIComponent(clarification.planId)}/clarification`, {
          method: "POST",
          body: JSON.stringify({answer: lines.join("\n")})
        });
        customSoftwareState.plan = next;
        stopProgress();
        if (next?.status === "clarification_required") {
          renderClarification(next);
          return;
        }
        removeClarification();
        window.drawSynthesizedSoftwarePlan?.();
      } catch (error) {
        stopProgress();
        const message = node("p", error?.message || "Could not continue planning.", "builder-error planning-inline-error");
        card.querySelector(".planning-inline-error")?.remove();
        card.append(message);
        submit.disabled = false;
        back.disabled = false;
        submit.textContent = "Continue";
        form.querySelectorAll("button, textarea").forEach(item => { item.disabled = false; });
      }
    };

    card.append(mark, kicker, title, copy, form);
    overlay.append(card);
    document.body.append(overlay);
  }

  const previousDraw = window.drawSynthesizedSoftwarePlan;
  if (typeof previousDraw === "function") {
    window.drawSynthesizedSoftwarePlan = function clarificationAwareDraw(...args) {
      const current = typeof customSoftwareState !== "undefined" ? customSoftwareState.plan : null;
      if (current?.status === "clarification_required") {
        renderClarification(current);
        return;
      }
      return previousDraw.apply(this, args);
    };
  }

  window.operlyRenderPlanningClarification = renderClarification;
})();