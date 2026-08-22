/* Ensure the focused Build experience replaces the legacy Studio entrypoint. */
(() => {
  if (typeof window.studioHome === "function") {
    window.operlyStudio = window.studioHome;
  }

  /*
   * Durable Studio runs are created asynchronously. A successful 202 should carry
   * the run JSON, but a proxy/browser edge can occasionally leave the response body
   * empty. Keep that transport glitch from becoming `null.state` inside the Studio
   * polling UI: recover the authoritative persisted run from /runs/latest.
   */
  const baseApi = typeof window.api === "function" ? window.api.bind(window) : null;
  if (baseApi) {
    const runPath = /^\/studio\/projects\/([^/]+)\/source\/runs(?:\/([^/?]+))?$/;
    window.api = async function studioRunSafeApi(path, options = {}) {
      const result = await baseApi(path, options);
      if (result != null) return result;

      const match = String(path || "").match(runPath);
      if (!match) return result;

      const method = String(options.method || "GET").toUpperCase();
      if (method !== "GET" && method !== "POST") return result;

      const projectId = match[1];
      const suffix = match[2] || "";
      if (suffix === "latest") {
        throw new Error("Studio source agent returned an empty run status.");
      }

      const latest = await baseApi(`/studio/projects/${projectId}/source/runs/latest`);
      if (latest && typeof latest === "object" && latest.id && latest.state) return latest;
      throw new Error("Studio source agent did not return a usable run record.");
    };
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
