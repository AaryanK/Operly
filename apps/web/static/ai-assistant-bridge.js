document.addEventListener("click", async (event) => {
  const button = event.target.closest('[data-page="assistant"]');
  if (!button || !window.renderOperlyAssistant) return;

  event.stopImmediatePropagation();
  document.querySelector("#operly-chat-dock")?.classList.add("page-suppressed");

  document.querySelectorAll("#nav button").forEach((item) => {
    item.classList.toggle("active", item.dataset.page === "assistant");
  });

  document.querySelector("#page-title").textContent = "Operly";

  try {
    await window.renderOperlyAssistant();
  } catch (error) {
    document.querySelector("#content").innerHTML =
      `<div class="error">${String(error.message || error)}</div>`;
  }
}, true);

/* Authenticated frontend bootstrap.
 *
 * Structural legacy CSS is kept only where current HTML still depends on it.
 * authenticated-ui.css is the sole runtime owner of product colors, surfaces,
 * component appearance, settings layout and responsive behavior.
 */
function ensureStyle(href, marker, extraMarkers = []) {
  if (document.querySelector(`link[${marker}]`)) return document.querySelector(`link[${marker}]`);
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  link.setAttribute(marker, "1");
  for (const extra of extraMarkers) link.setAttribute(extra, "1");
  document.head.append(link);
  return link;
}

function ensureScript(src, marker) {
  if (document.querySelector(`script[${marker}]`)) return;
  const script = document.createElement("script");
  script.src = src;
  script.defer = true;
  script.setAttribute(marker, "1");
  document.head.append(script);
}

// workspace-shell.css still provides structural selectors for the current static
// shell. It does not own the final visual system.
ensureStyle(
  "/static/workspace-shell.css?v=20260823-ui-system-v3",
  "data-operly-workspace-shell-style",
  ["data-operly-workspace-shell"],
);

// One final authenticated visual contract. Compatibility markers intentionally
// prevent workspace-shell.js and settings-scopes.js from re-injecting the old
// frontend-overhaul/settings-scopes cosmetic stylesheets.
ensureStyle(
  "/static/authenticated-ui.css?v=20260823-ui-system-v3",
  "data-operly-authenticated-ui",
  ["data-operly-frontend-overhaul", "data-operly-settings-scopes", "data-operly-account-shell-clean"],
);

ensureScript(
  "/static/workspace-shell.js?v=20260823-ui-system-v3",
  "data-operly-workspace-shell",
);
ensureScript(
  "/static/settings-scopes.js?v=20260823-ui-system-v3",
  "data-operly-settings-scopes",
);
ensureScript(
  "/static/operations-semantic-fix.js?v=20260823-ui-system-v3",
  "data-operly-operations-semantic-fix",
);
ensureStyle(
  "/static/chat-enhancements.css?v=20260823-ui-system-v3",
  "data-operly-chat-enhancements-style",
);
ensureScript(
  "/static/chat-enhancements.js?v=20260823-ui-system-v3",
  "data-operly-chat-enhancements",
);
ensureStyle(
  "/static/unified-solution-studio.css?v=20260823-ui-system-v3",
  "data-operly-unified-solution-studio",
);
ensureScript(
  "/static/authenticated-ui.js?v=20260823-ui-system-v3",
  "data-operly-authenticated-ui-script",
);

// Shared tablet/mobile workspace navigation. This is intentionally tiny and
// does not intercept fetch, auth, routing or model behavior.
document.addEventListener("click", (event) => {
  const dashboard = document.querySelector("#dashboard.workspace-shell-ready");
  if (!dashboard) return;
  if (event.target.closest("#mobile-nav-toggle")) {
    event.preventDefault();
    event.stopImmediatePropagation();
    const open = !dashboard.classList.contains("operly-mobile-nav-open");
    dashboard.classList.toggle("operly-mobile-nav-open", open);
    document.querySelector("#mobile-nav-toggle")?.setAttribute("aria-expanded", String(open));
    return;
  }
  if (event.target.closest(".mobile-nav-backdrop") || event.target.closest(".operly-nav-item")) {
    dashboard.classList.remove("operly-mobile-nav-open");
    document.querySelector("#mobile-nav-toggle")?.setAttribute("aria-expanded", "false");
  }
}, true);

window.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const dashboard = document.querySelector("#dashboard.workspace-shell-ready");
  dashboard?.classList.remove("operly-mobile-nav-open");
  document.querySelector("#mobile-nav-toggle")?.setAttribute("aria-expanded", "false");
});
