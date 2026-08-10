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
    return String(
      (typeof detail === "string" ? detail : detail?.message) ||
      body?.message ||
      ""
    );
  }

  // Legacy /custom-software/plans still raises PlanningNeedsUserInput after it
  // has durably stored the waiting plan. Treat that known state as a normal
  // response until the legacy route itself is retired.
  window.fetch = async function operlyClarificationAwareFetch(input, init = {}) {
    const response = await nativeFetch(input, init);
    const url = requestUrl(input);
    if (
      response.ok ||
      requestMethod(input, init) !== "POST" ||
      !url.includes("/api/custom-software/plans")
    ) return response;

    let body = null;
    try { body = await response.clone().json(); } catch (_) {}
    if (!clarificationMessage(body).toLowerCase().includes("user input required before planning")) {
      return response;
    }

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

  function quickChoices(questions) {
    const text = (questions || []).join(" ").toLowerCase();
    if (!/(standalone|existing website|internal tool|where.*live|integrated into)/.test(text)) return [];
    return [
      ["Existing website", "Add this capability to my existing website."],
      ["Internal tool", "Create this as a private internal OPERLY tool."],
      ["Standalone app", "Create this as a standalone application."],
      ["OPERLY decides", "Choose the best placement from my existing workspace and explain the choice."]
    ];
  }

  function removeClarification() {
    document.querySelector(".normal-clarification-overlay")?.remove();
  }

  function renderClarification(clarification) {
    removeClarification();
    document.querySelector(".planning-overlay")?.remove();

    const overlay = node("div", undefined, "planning-overlay normal-clarification-overlay");
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "OPERLY needs one decision");

    const card = node("section", undefined, "planning-card");
    const mark = node("div", "?", "planning-mark planning-mark-question");
    const kicker = node("span", "ONE DECISION BEFORE I CONTINUE", "planning-kicker");
    const title = node("h2", "Where should this capability live?");
    const copy = node(
      "p",
      "This is not an error. OPERLY stopped because choosing for you would materially change what gets built. Your answer continues the same plan.",
      "planning-copy"
    );

    const questions = Array.isArray(clarification?.questions)
      ? clarification.questions.filter(Boolean)
      : [];
    const list = node("div", undefined, "planning-question-list");
    questions.forEach((question, index) => {
      const row = node("div", undefined, "planning-question");
      row.append(node("span", String(index + 1), "planning-question-number"), node("strong", question));
      list.append(row);
    });

    const form = node("form", undefined, "planning-clarification-form");
    const textarea = document.createElement("textarea");
    textarea.required = true;
    textarea.maxLength = 4000;
    textarea.rows = Math.max(4, questions.length + 2);
    textarea.placeholder = questions.length > 1
      ? "Answer the decisions here…"
      : "Tell OPERLY what you want…";
    textarea.setAttribute("aria-label", "Answer OPERLY's clarification");

    const choices = quickChoices(questions);
    if (choices.length) {
      const choiceBox = node("div", undefined, "planning-quick-choices");
      choices.forEach(([label, value]) => {
        const button = node("button", label, "planning-choice");
        button.type = "button";
        button.onclick = () => {
          textarea.value = value;
          choiceBox.querySelectorAll(".planning-choice").forEach(item => item.classList.toggle("selected", item === button));
          textarea.focus();
        };
        choiceBox.append(button);
      });
      form.append(choiceBox);
    }

    const actions = node("div", undefined, "planning-clarification-actions");
    const back = node("button", "Back to prompt", "button secondary");
    const submit = node("button", "Continue", "button primary");
    back.type = "button";
    submit.type = "submit";
    back.onclick = removeClarification;
    actions.append(back, submit);
    form.append(textarea, actions);

    form.onsubmit = async event => {
      event.preventDefault();
      const answer = textarea.value.trim();
      if (!answer) return;
      submit.disabled = true;
      submit.textContent = "Continuing…";
      try {
        const next = await api(`/coding-harness/plans/${encodeURIComponent(clarification.planId)}/clarification`, {
          method: "POST",
          body: JSON.stringify({answer})
        });
        customSoftwareState.plan = next;
        if (next?.status === "clarification_required") {
          renderClarification(next);
          return;
        }
        removeClarification();
        window.drawSynthesizedSoftwarePlan?.();
      } catch (error) {
        const message = node("p", error?.message || "Could not continue planning.", "builder-error");
        card.querySelector(".builder-error")?.remove();
        card.append(message);
      } finally {
        submit.disabled = false;
        submit.textContent = "Continue";
      }
    };

    card.append(mark, kicker, title, copy, list, form);
    overlay.append(card);
    document.body.append(overlay);
    setTimeout(() => textarea.focus(), 0);
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