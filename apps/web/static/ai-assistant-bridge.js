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

// Workspace shell: the current production frontend lives in /static, so this
// bridge mounts the unified workspace navigation over the legacy feature pages.
if (!document.querySelector('script[data-operly-workspace-shell]')) {
  const script = document.createElement("script");
  script.src = "/static/workspace-shell.js?v=20260821-shell-v2";
  script.defer = true;
  script.dataset.operlyWorkspaceShell = "1";
  document.head.append(script);
}

// Base modern visual system.
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

// Repair layer for the command center, operations dashboard and all workspace
// surfaces. It is deliberately loaded after the modern base cascade.
if (!document.querySelector('link[data-operly-frontend-overhaul]')) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "/static/frontend-overhaul.css?v=20260821-command-center-v1";
  link.dataset.operlyFrontendOverhaul = "1";
  document.head.append(link);
}

// Final viewport and responsive corrections must win the entire legacy cascade.
if (!document.querySelector('link[data-operly-viewport-fix]')) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "/static/viewport-fix.css?v=20260821-viewport-v1";
  link.dataset.operlyViewportFix = "1";
  document.head.append(link);
}

if (!document.querySelector('script[data-operly-modern]')) {
  const script = document.createElement("script");
  script.src = "/static/operly-modern.js?v=20260821-modern-v1";
  script.defer = true;
  script.dataset.operlyModern = "1";
  document.head.append(script);
}

// Personal identity settings and workspace-owned channel/integration settings
// stay separate security scopes.
if (!document.querySelector('script[data-operly-settings-scopes]')) {
  const script = document.createElement("script");
  script.src = "/static/settings-scopes.js?v=20260821-scopes-v1";
  script.defer = true;
  script.dataset.operlySettingsScopes = "1";
  document.head.append(script);
}

// Persist the authenticated human's browser IANA timezone for every channel.
if (!document.querySelector('script[data-operly-time-sync]')) {
  const script = document.createElement("script");
  script.src = "/static/time-sync.js?v=20260821-time-v1";
  script.defer = true;
  script.dataset.operlyTimeSync = "1";
  document.head.append(script);
}

// Reconcile the Operations dashboard's status language with the actual alert
// and snapshot data after the shell renderer has populated the page.
if (!document.querySelector('script[data-operly-operations-semantic-fix]')) {
  const script = document.createElement("script");
  script.src = "/static/operations-semantic-fix.js?v=20260821-semantics-v1";
  script.defer = true;
  script.dataset.operlyOperationsSemanticFix = "1";
  document.head.append(script);
}

// Progressive chat enhancements: render model Markdown safely and replace raw
// SpeechRecognition alerts with permission-aware, inline voice status.
if (!document.querySelector('link[data-operly-chat-enhancements]')) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "/static/chat-enhancements.css?v=20260821-chat-v1";
  link.dataset.operlyChatEnhancements = "1";
  document.head.append(link);
}
if (!document.querySelector('script[data-operly-chat-enhancements]')) {
  const script = document.createElement("script");
  script.src = "/static/chat-enhancements.js?v=20260821-chat-v1";
  script.defer = true;
  script.dataset.operlyChatEnhancements = "1";
  document.head.append(script);
}

// Unified Solution Studio: one editor shell for websites, managed apps and
// generated software. The runtime-specific mutation systems stay underneath it.
if (!document.querySelector('link[data-operly-unified-solution-studio]')) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "/static/unified-solution-studio.css?v=20260821-studio-v1";
  link.dataset.operlyUnifiedSolutionStudio = "1";
  document.head.append(link);
}
if (!document.querySelector('script[data-operly-unified-solution-studio]')) {
  const script = document.createElement("script");
  script.src = "/static/unified-solution-studio.js?v=20260821-studio-v1";
  script.defer = true;
  script.dataset.operlyUnifiedSolutionStudio = "1";
  document.head.append(script);
}
