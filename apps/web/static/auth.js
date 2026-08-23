const AUTH_ROUTES = {
  "/": "#landing",
  "/login": "#login",
  "/signup": "#signup",
  "/verify-email": "#verify-email",
  "/forgot-password": "#forgot-password",
  "/reset-password": "#reset-password",
  "/onboarding": "#onboarding",
  "/personal": "#personal"
};

const SIGNED_IN_ENTRY_ROUTES = new Set(["/", "/login", "/signup"]);
let accountHomeNavigationObserver = null;

function setFormMessage(id, message, kind = "error") {
  const element = $(id);
  if (!element) return;
  element.textContent = message || "";
  element.className = message ? kind : `${kind} hidden`;
}

function setFormBusy(form, busy, label) {
  const button = form.querySelector(".submit-button");
  form.querySelectorAll("button, input").forEach((control) => { control.disabled = busy; });
  if (!button) return;
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.textContent = busy ? label : button.dataset.label;
}

function commitAuthenticatedScreen() {
  $$(".screen").forEach((element) => element.classList.add("hidden"));
  $("#dashboard")?.classList.remove("hidden");
  document.title = "OPERLY";
}

function commitPersonalScreen() {
  $$(".screen").forEach((element) => element.classList.add("hidden"));
  $("#personal")?.classList.remove("hidden");
  document.title = "Personal AI · OPERLY";
}

function installPersonalHomeNavigation() {
  const install = () => {
    const rail = $("#operly-workspace-rail");
    if (rail && !rail.querySelector("[data-personal-home]")) {
      const button = document.createElement("button");
      button.className = "operly-rail-item operly-personal-home";
      button.dataset.personalHome = "1";
      button.title = "Personal AI";
      button.setAttribute("aria-label", "Open Personal AI");
      button.textContent = "✦";
      button.addEventListener("click", () => {
        enterAuthenticatedPersonal().catch(dashboardBootError);
      });
      const separator = rail.querySelector(".operly-rail-separator");
      rail.insertBefore(button, separator || rail.firstChild);
    }

    const nav = $(".operly-section-nav .operly-nav-scroll");
    if (nav && !nav.querySelector("[data-personal-home]")) {
      const button = document.createElement("button");
      button.className = "operly-nav-item operly-personal-home";
      button.dataset.personalHome = "1";
      button.innerHTML = '<span class="operly-nav-icon">✦</span>Personal AI <span class="operly-nav-pill">Private</span>';
      button.addEventListener("click", () => {
        enterAuthenticatedPersonal().catch(dashboardBootError);
      });
      nav.insertBefore(button, nav.firstChild);
    }
  };

  install();
  if (!accountHomeNavigationObserver && document.body) {
    accountHomeNavigationObserver = new MutationObserver(install);
    accountHomeNavigationObserver.observe(document.body, { childList: true, subtree: true });
  }
}

function dashboardBootError(error) {
  console.error("OPERLY dashboard boot failed after authentication", error);
  const content = $("#content");
  if (!content) return;
  content.innerHTML = `<div class="error" role="alert">Signed in successfully, but the workspace UI could not finish loading. Refresh this page to retry. ${String(error?.message || "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char])}</div>`;
}

async function enterAuthenticatedWorkspace() {
  const params = new URLSearchParams(location.search);
  state.pendingIdentityLink = params.get("identity_link") || state.pendingIdentityLink;

  state.me = await api("/me");
  $("#workspace-name").textContent = state.me.tenant.name;
  $("#workspace-avatar").textContent = state.me.tenant.name.slice(0, 1).toUpperCase();
  $("#workspace-role").textContent = state.me.role;
  $("#tenant-kicker").textContent = state.me.tenant.name;
  commitAuthenticatedScreen();
  installPersonalHomeNavigation();
  if (location.pathname !== "/app" && !state.pendingIdentityLink) history.replaceState({}, "", "/app");

  setTimeout(() => {
    Promise.resolve()
      .then(() => typeof loadWorkspaces === "function" ? loadWorkspaces() : null)
      .catch(dashboardBootError);

    setTimeout(() => {
      const content = $("#content");
      if (!content || content.childElementCount) return;
      const render = state.pendingIdentityLink
        ? (typeof renderPage === "function" ? () => renderPage("settings") : null)
        : (typeof window.operlySimpleHome === "function"
          ? () => window.operlySimpleHome()
          : (typeof renderPage === "function" ? () => renderPage("overview") : null));
      if (render) Promise.resolve(render()).catch(dashboardBootError);
    }, 0);
  }, 0);
}

async function enterAuthenticatedPersonal() {
  // Crossing from any workspace back to Personal AI must rotate the authenticated
  // session into account scope. A workspace is a child scope, never the account.
  await api("/auth/personal-scope", { method: "POST", body: "{}" });
  await api("/auth/workspaces");
  state.me = null;
  commitPersonalScreen();
  if (location.pathname !== "/personal") history.replaceState({}, "", "/personal");
  await window.operlyPersonal?.mount?.();
}

async function enterAuthenticatedScope(preferredScope = null) {
  // Account-first is the default. Workspace entry is only an explicit navigation
  // choice (for example direct /app or a workspace switch), never inferred from the
  // existence/order of memberships.
  if (preferredScope === "workspace") return enterAuthenticatedWorkspace();
  return enterAuthenticatedPersonal();
}

function extractLinkToken() {
  const token = new URLSearchParams(location.hash.slice(1)).get("token");
  if (!token) return;
  state.linkToken = token;
  history.replaceState(history.state || {}, "", `${location.pathname}${location.search}`);
}

function showRoute(path = location.pathname) {
  const target = AUTH_ROUTES[path] || (path === "/app" ? "#dashboard" : "#landing");
  state.workflow = history.state?.workflow || state.workflow || {};
  show(target);
  if (target === "#verify-email" && state.workflow.email) {
    setFormMessage("#verify-status", `We sent a code to ${state.workflow.email}.`, "success");
  }
  if (target === "#reset-password" && state.workflow.email) $("#reset-email").value = state.workflow.email;
  if (target === "#reset-password") $("#reset-code-fields").classList.toggle("hidden", Boolean(state.linkToken));
  document.title = target === "#landing" || target === "#dashboard"
    ? "OPERLY"
    : target === "#personal" ? "Personal AI · OPERLY" : `${target.slice(1).replaceAll("-", " ")} · OPERLY`;
}

function navigate(path, workflow = {}) {
  state.workflow = workflow;
  state.linkToken = null;
  history.pushState({ workflow }, "", path);
  showRoute(path);
}

function openVerificationRecovery(email, message) {
  navigate("/verify-email", { email });
  setFormMessage("#verify-status", "");
  setFormMessage("#verify-error", message);
}

async function refreshAuthBootstrap() {
  state.authBootstrap = await api("/auth/bootstrap");
  await renderGoogleButtons();
}

let googleScriptPromise = null;
function loadGoogleScript() {
  if (window.google?.accounts?.id) return Promise.resolve();
  if (googleScriptPromise) return googleScriptPromise;
  googleScriptPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.onload = resolve;
    script.onerror = () => reject(new Error("Google sign-in is temporarily unavailable"));
    document.head.appendChild(script);
  });
  return googleScriptPromise;
}

async function renderGoogleButtons() {
  const containers = ["#google-login-button", "#google-signup-button"].map((selector) => $(selector));
  if (!state.authBootstrap?.google_client_id) {
    containers.forEach((element) => {
      element?.classList.add("hidden");
      element?.nextElementSibling?.classList.add("hidden");
    });
    return;
  }
  containers.forEach((element) => {
    element?.classList.remove("hidden");
    element?.nextElementSibling?.classList.remove("hidden");
  });
  try {
    await loadGoogleScript();
    google.accounts.id.initialize({
      client_id: state.authBootstrap.google_client_id,
      nonce: state.authBootstrap.google_nonce,
      callback: handleGoogleCredential,
      auto_select: false,
      cancel_on_tap_outside: true
    });
    ["#google-login-button", "#google-signup-button"].forEach((selector) => {
      const element = $(selector);
      if (!element) return;
      element.replaceChildren();
      google.accounts.id.renderButton(element, {
        theme: "outline",
        size: "large",
        text: "continue_with",
        width: Math.min(380, element.clientWidth || 380)
      });
    });
  } catch {
    console.warn("Google sign-in button could not be loaded");
  }
}

async function handleGoogleCredential(result) {
  const errorTarget = location.pathname === "/signup" ? "#signup-error" : "#login-error";
  setFormMessage(errorTarget, "");
  try {
    const response = await api("/auth/google", {
      method: "POST",
      body: JSON.stringify({ credential: result.credential })
    });
    state.me = null;
    if (response.new_account) {
      state.workflow = { scope: "personal" };
      history.replaceState({workflow:state.workflow}, "", "/onboarding");
      showRoute("/onboarding");
    } else {
      await enterAuthenticatedPersonal();
    }
  } catch (error) {
    const code = error.details?.code;
    const recoveryEmail = state.workflow.email || (
      location.pathname === "/signup"
        ? $("#signup-email")?.value.trim()
        : $("#login-email")?.value.trim()
    );
    if (code === "ACCOUNT_LINK_REQUIRES_VERIFICATION" && recoveryEmail) {
      openVerificationRecovery(
        recoveryEmail,
        "This email already has an unverified OPERLY account. Request a new code before linking Google."
      );
    } else {
      setFormMessage(errorTarget, error.message);
    }
    await refreshAuthBootstrap().catch(() => {});
  }
}

$$("[data-route]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.route)));
$$("[data-toggle-password]").forEach((button) => button.addEventListener("click", () => {
  const input = document.getElementById(button.dataset.togglePassword);
  const reveal = input.type === "password";
  input.type = reveal ? "text" : "password";
  button.textContent = reveal ? "Hide" : "Show";
  button.setAttribute("aria-label", reveal ? "Hide password" : "Show password");
}));

window.addEventListener("popstate", async () => {
  state.linkToken = null;
  extractLinkToken();
  if (location.pathname === "/personal") {
    try { await enterAuthenticatedPersonal(); return; } catch { state.me = null; }
  }
  if (location.pathname === "/app") {
    try { await enterAuthenticatedWorkspace(); return; } catch { state.me = null; }
  }
  if (SIGNED_IN_ENTRY_ROUTES.has(location.pathname)) {
    try {
      await enterAuthenticatedPersonal();
      return;
    } catch {
      state.me = null;
    }
  }
  showRoute();
});

$("#logout").addEventListener("click", async () => {
  try {
    await api("/auth/logout", { method: "POST", body: "{}" });
    state.me = null;
    navigate("/login");
    await refreshAuthBootstrap();
  } catch (error) {
    alert(`We couldn't sign you out: ${error.message}`);
  }
});

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  setFormMessage("#login-error", "");
  setFormBusy(form, true, "Signing in…");
  try {
    await api("/auth/login", {
      method: "POST",
      body: JSON.stringify({
        email: $("#login-email").value,
        password: $("#login-password").value
      })
    });
    await enterAuthenticatedPersonal();
  } catch (error) {
    if (error.details?.code === "EMAIL_NOT_VERIFIED") {
      openVerificationRecovery(
        $("#login-email").value.trim(),
        "Your password is correct, but this account still needs email verification. Request a new code to continue."
      );
    } else {
      setFormMessage("#login-error", error.message);
    }
  } finally {
    setFormBusy(form, false);
  }
});

$("#signup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  setFormMessage("#signup-error", "");
  if ($("#signup-password").value !== $("#signup-confirm").value) {
    setFormMessage("#signup-error", "Passwords do not match");
    return;
  }
  setFormBusy(form, true, "Creating account…");
  try {
    const email = $("#signup-email").value.trim();
    const result = await api("/auth/signup", {
      method: "POST",
      body: JSON.stringify({
        display_name: $("#signup-name").value,
        email,
        password: $("#signup-password").value
      })
    });
    navigate("/verify-email", { challenge_id: result.challenge_id, email: result.email });
  } catch (error) {
    const code = error.details?.code;
    if (["EMAIL_DELIVERY_FAILED", "ACCOUNT_PENDING_VERIFICATION"].includes(code)) {
      openVerificationRecovery($("#signup-email").value.trim(), error.message);
    } else {
      setFormMessage("#signup-error", error.message);
    }
  } finally {
    setFormBusy(form, false);
  }
});

$("#verify-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  setFormMessage("#verify-error", "");
  setFormBusy(form, true, "Verifying…");
  try {
    const payload = state.linkToken
      ? { token: state.linkToken }
      : {
          challenge_id: state.workflow.challenge_id,
          email: state.workflow.email,
          code: $("#verify-code").value
        };
    await api("/auth/verify-email", { method: "POST", body: JSON.stringify(payload) });
    state.linkToken = null;
    state.workflow = {...state.workflow, scope:"personal"};
    history.replaceState({workflow:state.workflow}, "", "/onboarding");
    showRoute("/onboarding");
  } catch (error) {
    setFormMessage("#verify-error", error.message);
  } finally {
    setFormBusy(form, false);
  }
});

$("#resend-verification").addEventListener("click", async () => {
  const email = state.workflow.email || $("#signup-email").value;
  if (!email) {
    setFormMessage("#verify-error", "Return to account creation and enter your email again");
    return;
  }
  const button = $("#resend-verification");
  button.disabled = true;
  setFormMessage("#verify-error", "");
  try {
    const result = await api("/auth/resend-verification", {
      method: "POST",
      body: JSON.stringify({ email })
    });
    state.workflow = {
      ...state.workflow,
      email,
      challenge_id: result.challenge_id || state.workflow.challenge_id
    };
    history.replaceState({ workflow: state.workflow }, "", "/verify-email");
    setFormMessage("#verify-status", result.message, "success");
  } catch (error) {
    setFormMessage("#verify-error", error.message);
  } finally {
    button.disabled = false;
  }
});

$("#forgot-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const email = $("#forgot-email").value;
  setFormMessage("#forgot-error", "");
  setFormMessage("#forgot-status", "");
  setFormBusy(form, true, "Sending…");
  try {
    const result = await api("/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email })
    });
    state.workflow = { email };
    history.replaceState({ workflow: state.workflow }, "", "/forgot-password");
    $("#reset-email").value = email;
    setFormMessage("#forgot-status", result.message, "success");
  } catch (error) {
    setFormMessage("#forgot-error", error.message);
  } finally {
    setFormBusy(form, false);
  }
});

$("#reset-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  setFormMessage("#reset-error", "");
  if ($("#reset-password-input").value !== $("#reset-confirm").value) {
    setFormMessage("#reset-error", "Passwords do not match");
    return;
  }
  setFormBusy(form, true, "Resetting…");
  try {
    const proof = state.linkToken
      ? { token: state.linkToken }
      : { email: $("#reset-email").value, code: $("#reset-code").value };
    await api("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ ...proof, password: $("#reset-password-input").value })
    });
    state.linkToken = null;
    await enterAuthenticatedPersonal();
  } catch (error) {
    setFormMessage("#reset-error", error.message);
  } finally {
    setFormBusy(form, false);
  }
});

$("#open-workspace").addEventListener("click", async () => {
  try { await enterAuthenticatedPersonal(); }
  catch (error) { setFormMessage("#login-error", error.message); navigate("/login"); }
});

async function initializeAuth() {
  extractLinkToken();

  if (location.pathname === "/personal") {
    try {
      await enterAuthenticatedPersonal();
      return;
    } catch {
      state.me = null;
      history.replaceState({}, "", "/login");
      await refreshAuthBootstrap().catch(() => {});
      showRoute("/login");
      return;
    }
  }

  if (location.pathname === "/app") {
    try {
      await enterAuthenticatedWorkspace();
      return;
    } catch {
      state.me = null;
      history.replaceState({}, "", "/personal");
      try {
        await enterAuthenticatedPersonal();
        return;
      } catch {
        history.replaceState({}, "", "/login");
      }
    }
  }

  if (SIGNED_IN_ENTRY_ROUTES.has(location.pathname)) {
    try {
      await enterAuthenticatedPersonal();
      return;
    } catch {
      state.me = null;
    }
  }

  if (AUTH_ROUTES[location.pathname]) {
    await refreshAuthBootstrap().catch(() => {});
    showRoute();
    if (location.pathname === "/verify-email" && state.linkToken) {
      $("#verify-form").requestSubmit();
    }
    return;
  }

  try {
    await enterAuthenticatedPersonal();
  } catch {
    state.me = null;
    history.replaceState({}, "", "/");
    await refreshAuthBootstrap().catch(() => {});
    showRoute("/");
  }
}

initializeAuth();
