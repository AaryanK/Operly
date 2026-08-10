/* Ensure the focused Build experience replaces the legacy Studio entrypoint. */
(() => {
  if (typeof window.studioHome === "function") {
    window.operlyStudio = window.studioHome;
  }

  const dashboard = document.querySelector("#dashboard");
  const title = document.querySelector("#page-title");
  const navButton = document.querySelector('#nav [data-page="studio"]');
  if (navButton) navButton.textContent = "Solutions";

  const syncFocus = () => {
    let value = title?.textContent?.trim();
    if ((value === "Studio" || value === "Build") && title) {
      title.textContent = "Solutions";
      value = "Solutions";
    }
    dashboard?.classList.toggle("studio-focus", value === "Solutions");
    if (navButton && navButton.textContent.trim() !== "Solutions") navButton.textContent = "Solutions";
  };
  syncFocus();
  if (title) new MutationObserver(syncFocus).observe(title, { childList: true, characterData: true, subtree: true });
})();
