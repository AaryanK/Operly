/* Ensure the focused Studio replaces the legacy Studio entrypoint. */
(() => {
  if (typeof window.studioHome === "function") {
    window.operlyStudio = window.studioHome;
  }

  const dashboard = document.querySelector("#dashboard");
  const title = document.querySelector("#page-title");
  const syncFocus = () => dashboard?.classList.toggle("studio-focus", title?.textContent?.trim() === "Studio");
  syncFocus();
  if (title) new MutationObserver(syncFocus).observe(title, { childList: true, characterData: true, subtree: true });
})();
