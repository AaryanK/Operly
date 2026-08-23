(() => {
  let activeWorkspaceId = null;

  function currentWorkspaceId() {
    const match = location.pathname.match(/^\/channels\/([^/]+)/);
    if (match && match[1] !== "@me") return match[1];
    return null;
  }

  function rememberWorkspaceFromClick(event) {
    const explicit = event.target.closest?.("[data-settings-workspace]");
    if (explicit?.dataset.settingsWorkspace) {
      activeWorkspaceId = explicit.dataset.settingsWorkspace;
      return;
    }
    if (event.target.closest?.("[data-workspace-settings]")) {
      activeWorkspaceId = currentWorkspaceId();
    }
  }

  function csrf(path) {
    if (typeof window.csrfToken === "function") return window.csrfToken(path);
    const cookies = Object.fromEntries(document.cookie.split(";").map((part) => {
      const [name, ...value] = part.trim().split("=");
      return [name, decodeURIComponent(value.join("="))];
    }).filter(([name]) => name));
    return cookies["__Host-operly_csrf"] || cookies.operly_csrf || "";
  }

  async function request(path, options = {}) {
    const headers = {...(options.headers || {})};
    const token = csrf(path);
    if (token) headers["X-CSRF-Token"] = token;
    const response = await fetch(`/api${path}`, {
      ...options,
      headers,
      credentials: "same-origin",
    });
    let body = null;
    try { body = await response.json(); } catch {}
    if (!response.ok) {
      const detail = body?.detail;
      throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status})`);
    }
    return body;
  }

  function setStatus(host, message, kind = "") {
    const status = host.querySelector("[data-icon-status]");
    if (!status) return;
    status.textContent = message || "";
    status.dataset.kind = kind;
  }

  function setPreview(host, url) {
    const image = host.querySelector("[data-icon-preview]");
    const fallback = host.querySelector("[data-icon-fallback]");
    if (!image || !fallback) return;
    if (url) {
      image.src = url;
      image.hidden = false;
      fallback.hidden = true;
    } else {
      image.removeAttribute("src");
      image.hidden = true;
      fallback.hidden = false;
    }
  }

  async function upload(host, workspaceId, file, hiddenInput) {
    const allowed = new Set(["image/jpeg", "image/png", "image/webp"]);
    if (!allowed.has(file.type)) throw new Error("Choose a JPEG, PNG, or WebP image");
    if (file.size > 2 * 1024 * 1024) throw new Error("Workspace icon must be 2 MB or smaller");
    setStatus(host, "Uploading…");
    const result = await request(`/personal-agent/workspaces/${encodeURIComponent(workspaceId)}/icon`, {
      method: "PUT",
      headers: {"Content-Type": file.type},
      body: file,
    });
    hiddenInput.value = result.logo_url || "";
    setPreview(host, result.logo_url);
    setStatus(host, "Icon saved", "success");
    await window.operlyPersonal?.refreshShell?.();
  }

  async function remove(host, workspaceId, hiddenInput) {
    setStatus(host, "Removing…");
    await request(`/personal-agent/workspaces/${encodeURIComponent(workspaceId)}/icon`, {method: "DELETE"});
    hiddenInput.value = "";
    setPreview(host, null);
    setStatus(host, "Using workspace initials", "success");
    await window.operlyPersonal?.refreshShell?.();
  }

  function enhanceWorkspaceIconForm() {
    const hiddenInput = document.querySelector("#workspace-settings-logo");
    const form = document.querySelector("#workspace-general-form");
    if (!hiddenInput || !form || hiddenInput.dataset.firstPartyIcon === "1") return;

    const workspaceId = activeWorkspaceId || currentWorkspaceId();
    if (!workspaceId) return;
    activeWorkspaceId = workspaceId;
    hiddenInput.dataset.firstPartyIcon = "1";
    const oldLabel = hiddenInput.closest("label");
    if (oldLabel) oldLabel.hidden = true;
    hiddenInput.type = "hidden";

    const host = document.createElement("section");
    host.className = "workspace-icon-control";
    host.innerHTML = `
      <div class="workspace-icon-control-copy">
        <b>Workspace icon</b>
        <small>Stored privately by Operly. JPEG, PNG or WebP · 2 MB max.</small>
      </div>
      <div class="workspace-icon-editor">
        <span class="workspace-icon-preview">
          <img data-icon-preview alt="Workspace icon" hidden>
          <span data-icon-fallback>◎</span>
        </span>
        <label class="shell-button secondary workspace-icon-file">
          Choose image
          <input type="file" accept="image/jpeg,image/png,image/webp" data-icon-file hidden>
        </label>
        <button type="button" class="shell-button danger-subtle" data-remove-icon>Remove</button>
      </div>
      <small class="workspace-icon-status" data-icon-status aria-live="polite"></small>`;
    (oldLabel || form.querySelector("label"))?.insertAdjacentElement("afterend", host);
    setPreview(host, hiddenInput.value || null);

    const fileInput = host.querySelector("[data-icon-file]");
    fileInput?.addEventListener("change", async () => {
      const file = fileInput.files?.[0];
      if (!file) return;
      try {
        await upload(host, workspaceId, file, hiddenInput);
      } catch (error) {
        setStatus(host, error.message || "Upload failed", "error");
      } finally {
        fileInput.value = "";
      }
    });

    host.querySelector("[data-remove-icon]")?.addEventListener("click", async () => {
      try {
        await remove(host, workspaceId, hiddenInput);
      } catch (error) {
        setStatus(host, error.message || "Could not remove icon", "error");
      }
    });
  }

  document.addEventListener("click", rememberWorkspaceFromClick, true);
  const observer = new MutationObserver(enhanceWorkspaceIconForm);
  observer.observe(document.documentElement, {childList: true, subtree: true});
  enhanceWorkspaceIconForm();
})();
