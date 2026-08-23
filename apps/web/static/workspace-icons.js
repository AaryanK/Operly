(() => {
  let activeWorkspaceId = null;

  function injectStyles() {
    if (document.querySelector("#operly-workspace-icon-styles")) return;
    const style = document.createElement("style");
    style.id = "operly-workspace-icon-styles";
    style.textContent = `
      .workspace-icon-control{display:grid;gap:10px;padding:14px;border:1px solid var(--op-line,#dde1e8);border-radius:13px;background:#fafbfc}
      .workspace-icon-control-copy{display:grid;gap:2px}.workspace-icon-control-copy b{font-size:11px;color:var(--op-text,#17191f)}
      .workspace-icon-control-copy small,.workspace-icon-status{font-size:9px;color:var(--op-text-3,#707887);line-height:1.45}
      .workspace-icon-editor{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
      .workspace-icon-preview{position:relative;width:58px;height:58px;border-radius:17px;overflow:hidden;display:grid;place-items:center;background:#333b48;color:#fff;font-size:19px;flex:0 0 auto}
      .workspace-icon-preview img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
      .workspace-icon-file{position:relative;overflow:hidden}.workspace-icon-status[data-kind="success"]{color:#287356}.workspace-icon-status[data-kind="error"]{color:#a6323c}
      @media(max-width:700px){.workspace-icon-editor{align-items:flex-start}.workspace-icon-preview{width:52px;height:52px;border-radius:15px}}
    `;
    document.head.appendChild(style);
  }

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
      const message = typeof detail === "string" ? detail : detail?.message || detail?.code;
      throw new Error(message || `Request failed (${response.status})`);
    }
    return body;
  }

  async function requestJson(path, payload) {
    return request(path, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
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
        <small>Stored by Operly, not loaded from a third-party URL. JPEG, PNG or WebP · 2 MB max.</small>
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

  function closeShellModal() {
    const modal = document.querySelector("#operly-shell-modal");
    modal?.classList.add("hidden");
    document.body.classList.remove("shell-modal-open");
  }

  async function createWorkspaceWithTimezone(event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || form.id !== "shell-create-workspace") return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const name = document.querySelector("#shell-workspace-name")?.value?.trim();
    const timezone = document.querySelector("#shell-workspace-timezone")?.value?.trim() || "UTC";
    if (!name) return;
    const submit = form.querySelector('button[type="submit"],button:not([type])');
    if (submit) submit.disabled = true;
    try {
      const result = await requestJson("/auth/workspaces", {name, timezone});
      closeShellModal();
      await window.operlyPersonal?.goWorkspace?.(result.workspace.id);
    } catch (error) {
      if (typeof window.alert === "function") window.alert(error.message || "Workspace could not be created");
      if (submit) submit.disabled = false;
    }
  }

  injectStyles();
  document.addEventListener("click", rememberWorkspaceFromClick, true);
  document.addEventListener("submit", createWorkspaceWithTimezone, true);
  const observer = new MutationObserver(enhanceWorkspaceIconForm);
  observer.observe(document.documentElement, {childList: true, subtree: true});
  enhanceWorkspaceIconForm();
})();