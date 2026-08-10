/* Ensure the focused Studio replaces the legacy Studio entrypoint. */
(() => {
  if (typeof window.studioHome === "function") {
    window.operlyStudio = window.studioHome;
  }

  const dashboard = document.querySelector("#dashboard");
  const title = document.querySelector("#page-title");
  const syncFocus = () => {
    const value = title?.textContent?.trim();
    dashboard?.classList.toggle("studio-focus", value === "Studio" || value === "Build");
  };
  syncFocus();
  if (title) new MutationObserver(syncFocus).observe(title, { childList: true, characterData: true, subtree: true });
})();
