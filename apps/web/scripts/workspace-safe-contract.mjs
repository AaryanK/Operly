import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");

async function text(path) { return readFile(resolve(webRoot, path), "utf8"); }
async function repoText(path) { return readFile(resolve(repoRoot, path), "utf8"); }
function assert(condition, message) { if (!condition) throw new Error(message); }

const [safeShell, apiClient, csrfMiddleware] = await Promise.all([
  text("src/workspace-lite/WorkspaceSafeApp.tsx"),
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
assert(safeShell.includes("prominent>Ask Operly</WorkspaceControlLink>"), "Workspace header must expose a prominent AI entry point");
assert(safeShell.includes('case "operly": return <WorkspaceOperly workspace={workspace} />;'), "Workspace Operly route must render the workspace chat surface");

console.log("Workspace safe-shell interaction contracts passed.");
