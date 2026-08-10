/* Ensure the focused Build experience replaces the legacy Studio entrypoint. */
(() => {
  if (typeof window.studioHome === "function") {
    window.operlyStudio = window.studioHome;
  }

  const dashboard = document.querySelector("#dashboard");
  const title = document.querySelector("#page-title");
  const navButton = document.querySelector('#nav [data-page="studio"]');
  if (navButton) navButton.textContent = "Build";

  const syncFocus = () => {
    let value = title?.textContent?.trim();
    if (value === "Studio" && title) {
      title.textContent = "Build";
      value = "Build";
    }
    dashboard?.classList.toggle("studio-focus", value === "Build");
    if (navButton && navButton.textContent.trim() !== "Build") navButton.textContent = "Build";
  };
  syncFocus();
  if (title) new MutationObserver(syncFocus).observe(title, { childList: true, characterData: true, subtree: true });
})();
