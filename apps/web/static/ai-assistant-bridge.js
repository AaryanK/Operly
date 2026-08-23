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

/*
 * Authenticated frontend bootstrap.
 *
 * The previous bridge loaded several visual generations at runtime
 * (operly-modern, frontend-overhaul, viewport-fix, operly-cosmic) after
 * personal.css. Those global styles changed the same CSS variables and even
 * authenticated-screen visibility, which caused white-on-white text and let the
 * workspace UI remain visible on /channels/@me.
 *
 * Keep structural feature code, but load one account-shell visual contract last.
 */
function ensureStyle(href, marker, sharedMarker = null) {
  if (document.querySelector(`link[${marker}]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  link.setAttribute(marker, "1");
  if (sharedMarker) link.setAttribute(sharedMarker, "1");
  document.head.append(link);
}

function ensureScript(src, marker) {
  if (document.querySelector(`script[${marker}]`)) return;
  const script = document.createElement("script");
  script.src = src;
  script.defer = true;
  script.setAttribute(marker, "1");
  document.head.append(script);
}

// Preload the workspace structural styles with fresh revisions. The shared data
// markers intentionally satisfy workspace-shell.js/settings-scopes.js style
// guards so they do not inject older cached URLs afterward.
ensureStyle(
  "/static/workspace-shell.css?v=20260823-account-shell-v2",
  "data-operly-workspace-shell-style",
  "data-operly-workspace-shell",
);
ensureStyle(
  "/static/frontend-overhaul.css?v=20260823-account-shell-v2",
  "data-operly-frontend-overhaul",
);
ensureStyle(
  "/static/settings-scopes.css?v=20260823-account-shell-v2",
  "data-operly-settings-scopes-style",
  "data-operly-settings-scopes",
);

// One final authenticated color/visibility contract. It is intentionally loaded
// after structural styles, but unlike the removed legacy themes it is scoped to
// Personal and workspace authenticated surfaces.
ensureStyle(
  "/static/account-shell-clean.css?v=20260823-account-shell-v2",
  "data-operly-account-shell-clean",
);

// Canonical workspace navigation/content implementation. Personal/workspace
// selection itself is owned by personal.js and its global scope rail.
ensureScript(
  "/static/workspace-shell.js?v=20260823-account-shell-v2",
  "data-operly-workspace-shell",
);

// Workspace/personal connector settings remain functional, but their style file
// is already preloaded above with the current revision.
ensureScript(
  "/static/settings-scopes.js?v=20260823-account-shell-v2",
  "data-operly-settings-scopes",
);

// Semantic and chat behavior layers are feature-scoped rather than theme layers.
ensureScript(
  "/static/operations-semantic-fix.js?v=20260823-account-shell-v2",
  "data-operly-operations-semantic-fix",
);
ensureStyle(
  "/static/chat-enhancements.css?v=20260823-account-shell-v2",
  "data-operly-chat-enhancements-style",
);
ensureScript(
  "/static/chat-enhancements.js?v=20260823-account-shell-v2",
  "data-operly-chat-enhancements",
);

// The unified Studio script is loaded explicitly by index.html. Only its scoped
// stylesheet belongs here.
ensureStyle(
  "/static/unified-solution-studio.css?v=20260823-account-shell-v2",
  "data-operly-unified-solution-studio",
);

// Small mobile navigation behavior replacing the removed operly-cosmic.js shell
// interceptor. No fetch interception, route rewriting, or global theme mutation.
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
