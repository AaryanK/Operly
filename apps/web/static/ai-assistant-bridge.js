document.addEventListener("click", async (event) => {
  const button = event.target.closest('[data-page="assistant"]');
  if (!button || !window.renderOperlyAssistant) return;

  event.stopImmediatePropagation();
  document.querySelector("#operly-chat-dock")?.classList.add("page-suppressed");

  document.querySelectorAll("#nav button").forEach((item) => {
    item.classList.toggle("active", item.dataset.page === "assistant");
  });

  document.querySelector("#page-title").textContent = "OPERLY AI";

  try {
    await window.renderOperlyAssistant();
  } catch (error) {
    document.querySelector("#content").innerHTML =
      `<div class="error">${String(error.message || error)}</div>`;
  }
}, true);

// The workspace shell is intentionally loaded from this tiny bridge so the
// legacy feature scripts can keep operating while the product migrates to the
// new Discord-style multi-workspace navigation model.
if (!document.querySelector('script[data-operly-workspace-shell]')) {
  const script = document.createElement("script");
  script.src = "/static/workspace-shell.js?v=20260821-shell-v1";
  script.defer = true;
  script.dataset.operlyWorkspaceShell = "1";
  document.head.append(script);
}

// Load the 2026 visual system after every legacy stylesheet so it owns the
// final cascade without changing any backend contracts or feature routers.
for (const [href, marker] of [
  ["/static/operly-modern.css?v=20260821-modern-v1", "core"],
  ["/static/operly-modern-extras.css?v=20260821-modern-v1", "extras"],
]) {
  if (!document.querySelector(`link[data-operly-modern-${marker}]`)) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.dataset[`operlyModern${marker[0].toUpperCase()}${marker.slice(1)}`] = "1";
    document.head.append(link);
  }
}

// The modern interaction layer is deliberately separate from business logic.
// It adds account/session controls, command navigation and visual repair while
// keeping all authorization and mutations behind the existing API contracts.
if (!document.querySelector('script[data-operly-modern]')) {
  const script = document.createElement("script");
  script.src = "/static/operly-modern.js?v=20260821-modern-v1";
  script.defer = true;
  script.dataset.operlyModern = "1";
  document.head.append(script);
}
