import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");

async function text(path) {
  return readFile(resolve(webRoot, path), "utf8");
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const [publicShell, auth, runtime, main, convergence, personal, workspace, emailBase] = await Promise.all([
  text("static/index.html"),
  text("static/auth.js"),
  text("static/auth-runtime.js"),
  text("src/main.tsx"),
  text("src/ui/convergence.css"),
  text("src/account/PersonalHome.tsx"),
  text("src/workspace/WorkspaceShell.tsx"),
  readFile(resolve(repoRoot, "packages/email/templates/base.html"), "utf8"),
]);

const forbiddenLegacyPayload = [
  "/static/app.js",
  "/static/personal.js",
  "/static/operations-phase.js",
  "/static/ai-assistant.js",
  "/static/general-business.js",
  "/static/studio.js",
  "/static/dashboard-customize.js",
  "/static/coding-harness-ui.js",
  "/static/graph-planning-ui.js",
  'id="dashboard"',
  'id="personal"',
];

for (const token of forbiddenLegacyPayload) {
  assert(!publicShell.includes(token), `Public/auth shell regressed to legacy signed-in payload: ${token}`);
}

assert(publicShell.includes("/static/auth-runtime.js"), "Public shell must load the lightweight auth runtime");
assert(publicShell.includes("/static/auth.js"), "Public shell must load the auth flow");
assert(auth.includes('return "/channels/@me"'), "Personal auth handoff must target the canonical React route");
assert(auth.includes("/channels/${encodeURIComponent(workspaceId)}"), "Workspace auth handoff must target the canonical React route");
assert(!auth.includes("commitAuthenticatedScreen"), "Legacy authenticated screen rendering must not return to auth.js");
assert(runtime.includes("async function api"), "Auth runtime must own the public API helper");

assert(main.includes('import "./ui/convergence.css"'), "React must load the final convergence layer");
assert(convergence.includes("@media (max-width: 680px)"), "Phone breakpoint contract is missing");
assert(convergence.includes("@media (max-width: 430px)"), "Small-phone breakpoint contract is missing");
assert(convergence.includes("100dvh"), "Mobile shell must use dynamic viewport units");
assert(convergence.includes("env(safe-area-inset-bottom)"), "Mobile shell must respect safe-area insets");
assert(convergence.includes(".workspace-mobile-nav"), "Mobile workspace navigation contract is missing");
assert(convergence.includes(".workspace-more-sheet"), "Mobile More sheet contract is missing");
assert(convergence.includes(".mobile-history-open .personal-history"), "Personal history drawer contract is missing");
assert(convergence.includes("--ui-accent: #185d43"), "React brand accent must match the public Operly green");
assert(convergence.includes("--ui-accent-cyan: #b9ee72"), "React secondary accent must match the public Operly lime");

assert(workspace.includes('const mobilePrimarySections: WorkspaceSection[] = ["home", "operly", "activity", "solutions"]'), "Workspace phone navigation must stay intentionally small");
assert(workspace.includes("workspace-more-sheet"), "Workspace secondary destinations must remain reachable on phones");
assert(personal.includes("mobile-history-button"), "Personal conversation history must stay reachable on phones");
assert(personal.includes("history-mobile-close"), "Personal history drawer must have an explicit close control");

assert(emailBase.includes("#12392b") || emailBase.includes("#176c4a"), "Transactional email brand should remain in the Operly green family");

console.log("Frontend convergence contracts passed.");
