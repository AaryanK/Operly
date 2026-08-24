import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");

async function text(path) {
  return readFile(resolve(webRoot, path), "utf8");
}

async function repoText(path) {
  return readFile(resolve(repoRoot, path), "utf8");
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const [publicShell, auth, runtime, main, convergence, theme, brand, personal, workspace, emailBase, ...emailBodies] = await Promise.all([
  text("static/index.html"),
  text("static/auth.js"),
  text("static/auth-runtime.js"),
  text("src/main.tsx"),
  text("src/ui/convergence.css"),
  text("src/ui/theme.css"),
  text("src/ui/brand.css"),
  text("src/account/PersonalHome.tsx"),
  text("src/workspace/WorkspaceShell.tsx"),
  repoText("packages/email/templates/base.html"),
  repoText("packages/email/templates/verify_email.html"),
  repoText("packages/email/templates/password_reset.html"),
  repoText("packages/email/templates/welcome.html"),
  repoText("packages/email/templates/password_changed.html"),
  repoText("packages/email/templates/security_alert.html"),
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

for (const legacyPurple of ["#8173ff", "#7568e8", "#b9b0ff", "rgba(126, 104, 255", "rgba(129,115,255"]) {
  assert(!theme.toLowerCase().includes(legacyPurple.toLowerCase()), `Dark theme contains legacy purple brand accent: ${legacyPurple}`);
}
for (const legacyPurple of ["#7d6cff", "rgba(125,108,255", "rgba(111,92,255"]) {
  assert(!brand.toLowerCase().includes(legacyPurple.toLowerCase()), `Brand boot contains legacy purple accent: ${legacyPurple}`);
}

assert(workspace.includes('const mobilePrimarySections: WorkspaceSection[] = ["home", "operly", "activity", "solutions"]'), "Workspace phone navigation must stay intentionally small");
assert(workspace.includes("workspace-more-sheet"), "Workspace secondary destinations must remain reachable on phones");
assert(personal.includes("mobile-history-button"), "Personal conversation history must stay reachable on phones");
assert(personal.includes("history-mobile-close"), "Personal history drawer must have an explicit close control");

for (const token of ["#f3f5f1", "#13231c", "#dfe6df", "#102f24"]) {
  assert(emailBase.toLowerCase().includes(token), `Transactional email shell is missing canonical Operly token: ${token}`);
}
for (const emailBody of emailBodies) {
  assert(emailBody.toLowerCase().includes("#185d43") || !emailBody.includes("$action_url"), "Transactional email CTA/link must use canonical Operly green #185d43");
  assert(!emailBody.toLowerCase().includes("#176c4a"), "Legacy email green #176c4a must not return");
}

console.log("Frontend convergence contracts passed.");
