import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");

async function text(path) { return readFile(resolve(webRoot, path), "utf8"); }
async function repoText(path) { return readFile(resolve(repoRoot, path), "utf8"); }
function assert(condition, message) { if (!condition) throw new Error(message); }

const [safeShell, assistantPanel, assistantStyles, main, apiClient, csrfMiddleware] = await Promise.all([
  text("src/workspace-lite/WorkspaceSafeApp.tsx"),
  text("src/workspace/WorkspaceAssistantPanel.tsx"),
  text("src/ui/workspace-assistant-shell.css"),
  text("src/main.tsx"),
  text("src/api.ts"),
  repoText("apps/api/csrf.py"),
]);

assert(
  csrfMiddleware.includes('"code": "JSON_REQUIRED"') && csrfMiddleware.includes('"message": "Send this request as JSON."'),
  "CSRF middleware JSON requirement changed; update the frontend request contract deliberately",
);
assert(
  apiClient.includes('const mutates = !["GET", "HEAD", "OPTIONS"].includes(method);'),
  "Shared API client must identify mutation methods",
);
assert(
  apiClient.includes('if (mutates && !(options.body instanceof FormData) && !headers.has("Content-Type"))'),
  "Bodyless JSON mutations must still receive the application/json content type",
);
assert(
  apiClient.includes('headers.set("Content-Type", "application/json")'),
  "Shared API client must enforce the JSON mutation contract",
);

for (const bodylessAuthAction of ["/auth/personal-scope", "/auth/logout"]) {
  assert(
    safeShell.includes(`api("${bodylessAuthAction}", { method: "POST" })`),
    `Workspace shell must keep ${bodylessAuthAction} on the shared mutation path`,
  );
}

assert(safeShell.includes("const [logoutBusy, setLogoutBusy]"), "Sign out must have independent request state");
assert(safeShell.includes("disabled={logoutBusy}"), "Sign out must not be disabled by workspace switching");
assert(!safeShell.includes("if (busy) return;\n    setBusy(true);\n    setError(\"\");\n    try {\n      await api(\"/auth/logout\""), "Sign out must not reuse workspace busy state");

assert(
  safeShell.includes('const operlyPath = operlyWorkspace ? workspaceControlPath(operlyWorkspace.id, "operly") : "/personal";'),
  "Operly brand navigation must resolve to the active AI scope",
);
assert(safeShell.includes('label="Operly AI"'), "Scope rail must expose Operly AI explicitly");
assert(safeShell.includes('aria-label="Switch to Personal Operly">ME</button>'), "Personal scope switching must be a separate explicit control");
assert(safeShell.includes('case "operly": return <WorkspaceOperly workspace={workspace} />;'), "Workspace Operly full-page route must remain available");

assert(safeShell.includes("const [assistantOpen, setAssistantOpen]"), "Workspace shell must own assistant drawer state");
assert(safeShell.includes("<WorkspaceAssistantPanel workspace={selected}"), "Workspace shell must render Operly inside the active workspace");
assert(safeShell.includes('workspace-lite-stage ${showAssistant ? "assistant-open" : ""}'), "Workspace content and assistant must share one stage");
assert(safeShell.includes('assistantOpen ? "Hide Operly" : "Ask Operly"'), "Workspace header must expose the assistant as a toggle");
assert(safeShell.includes('className="workspace-lite-menu"'), "Secondary workspace tools must be grouped instead of flooding the top bar");
assert(safeShell.includes('className="workspace-lite-menu workspace-lite-account-menu"'), "Account actions must be grouped in their own menu");
assert(safeShell.includes('function closeParentMenu(target: HTMLElement)'), "Workspace shell must close dropdown state after SPA actions");
assert(safeShell.includes('closeParentMenu(event.currentTarget); navigate(path);'), "Workspace tool navigation must close its parent menu before changing route");

for (const chatContract of ["/agent/conversations", "/agent/chat", "/agent/chat-with-attachments"]) {
  assert(assistantPanel.includes(chatContract), `Integrated workspace assistant is missing ${chatContract}`);
}
assert(assistantPanel.includes("workspace.name"), "Integrated assistant must visibly retain workspace identity");
assert(assistantPanel.includes("Open Operly full page"), "Integrated assistant must retain a full-page escape hatch");
assert(main.includes('import "./ui/workspace-assistant-shell.css"'), "Integrated assistant styles must load in the application shell");
assert(assistantStyles.includes(".workspace-lite-stage.assistant-open"), "Desktop assistant split-pane contract is missing");
assert(assistantStyles.includes("position: fixed"), "Narrow assistant layout must become an overlay rather than crush workspace content");
assert(assistantStyles.includes("env(safe-area-inset-bottom)"), "Mobile assistant must respect device safe areas");
assert(assistantStyles.includes("flex-wrap: nowrap"), "Touch workspace header must stay one row so assistant overlay offset remains correct");
assert(assistantStyles.includes("order: initial") && assistantStyles.includes("width: auto"), "Touch header actions must not inherit the old full-width second row");
assert(assistantStyles.includes("z-index: 130"), "Workspace header menus must stay above the assistant overlay");

console.log("Workspace safe-shell interaction contracts passed.");
