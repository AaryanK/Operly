import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");

async function text(path) { return readFile(resolve(webRoot, path), "utf8"); }
async function repoText(path) { return readFile(resolve(repoRoot, path), "utf8"); }
function assert(condition, message) { if (!condition) throw new Error(message); }

const [
  rootApp,
  liveShell,
  workspaceOS,
  personal,
  capabilityRuntime,
  publicApp,
  apiClient,
  adminPage,
  legalPage,
  main,
  foundationStyles,
  publicStyles,
  personalStyles,
  workspaceStyles,
  apiMain,
  dockerfile,
  emailBase,
  ...emailBodies
] = await Promise.all([
  text("src/app/App.tsx"),
  text("src/workspace-lite/WorkspaceSafeApp.tsx"),
  text("src/workspace-lite/WorkspaceOSPanel.tsx"),
  text("src/account/PersonalHome.tsx"),
  text("src/runtime/capabilityRuntime.ts"),
  text("src/public/PublicApp.tsx"),
  text("src/api.ts"),
  text("src/admin/AdminPage.tsx"),
  text("src/legal/LegalPage.tsx"),
  text("src/main.tsx"),
  text("src/ui/foundation.css"),
  text("src/ui/public-surfaces.css"),
  text("src/ui/personal.css"),
  text("src/ui/workspace.css"),
  repoText("apps/api/main.py"),
  repoText("Dockerfile"),
  repoText("packages/email/templates/base.html"),
  repoText("packages/email/templates/verify_email.html"),
  repoText("packages/email/templates/password_reset.html"),
  repoText("packages/email/templates/welcome.html"),
  repoText("packages/email/templates/password_changed.html"),
  repoText("packages/email/templates/security_alert.html"),
]);

// Canonical routing and authenticated shell.
assert(rootApp.includes('import { WorkspaceSafeApp } from "../workspace-lite/WorkspaceSafeApp";'), "React root must import the canonical authenticated shell");
assert(!rootApp.includes("ProductApp"), "React root must not reintroduce the retired parallel ProductApp shell");
for (const route of ["/account", "/personal", "/app", "/channels"]) {
  assert(rootApp.includes(`pathname === "${route}"`), `React root must route ${route} through the authenticated shell`);
}
assert(rootApp.includes('pathname.startsWith("/channels/")'), "React root must own scoped /channels routes");
assert(rootApp.includes("<WorkspaceSafeApp pathname={pathname}"), "Authenticated routes must converge on WorkspaceSafeApp");

// Live scope and authority boundary.
assert(liveShell.includes('api<PersonalProfile>("/auth/me")'), "Live shell must load authenticated account identity");
assert(liveShell.includes('api<Workspace[]>("/auth/workspaces")'), "Live shell must load current Workspace membership");
assert(liveShell.includes('api("/auth/switch-workspace"'), "Workspace switching must update server authority");
assert(liveShell.includes('api("/auth/personal-scope"'), "Personal scope switching must update server authority");
assert(liveShell.includes('<WorkspaceOSPanel workspaceId={selected.id} pathname={pathname} />'), "Normal Workspace pages must render through the live Workspace OS");
for (const page of ["WorkflowPage", "ActivityPage", "AgentComputerPage", "ConnectionsPage", "CapabilitiesPage", "AccessPage"]) {
  assert(liveShell.includes(page), `Live shell is missing advanced surface ${page}`);
}
assert(liveShell.includes("navigate(path)"), "Advanced Workspace navigation must stay inside the SPA");
assert(liveShell.includes("WorkspaceAssistantPanel"), "Live Workspace shell must retain the scoped assistant");

// Human mutations converge on the governed capability runtime.
assert(workspaceOS.includes("loadWorkspaceCapabilities()"), "Workspace OS must resolve live authorized capabilities");
assert(workspaceOS.includes("executeCapability(tool, args)"), "Workspace OS mutations must use the shared capability runtime");
for (const id of [
  "workspace.settings.update",
  "workspace.modules.set",
  "workspace.presets.apply",
  "workspace.members.add",
  "workspace.members.role.update",
  "workspace.members.remove",
  "workspace.roles.permissions.set",
  "workspace.invitations.create",
  "workspace.invitations.revoke",
  "workspace.inventory.adjust",
]) {
  assert(workspaceOS.includes(`tools.get("${id}")`), `Workspace OS is missing governed mutation ${id}`);
}
assert(capabilityRuntime.includes('api<CapabilityCatalog>("/workspace-tools")'), "Shared capability runtime must discover the authorized tool catalog");
assert(capabilityRuntime.includes("request_id: requestId"), "Shared capability runtime must preserve mutation request identity");
assert(capabilityRuntime.includes("approval_id: options.approvalId"), "Shared capability runtime must preserve approval identity on resume");

// Personal experience remains first-class and separate from Workspace authority.
assert(personal.includes("personal-conversation-search"), "Personal Operly must provide conversation search");
assert(personal.includes("mobile-personal-list"), "Personal Operly must have a mobile conversation-list state");
assert(personal.includes("mobile-personal-thread"), "Personal Operly must have a full-screen mobile thread state");
assert(!personal.includes('"/approvals/personal"'), "Personal Operly must not revive the retired legacy approval UI");

// Public/auth/admin/legal routes remain React-owned.
assert(rootApp.includes('pathname === "/admin"'), "React root must own /admin");
assert(rootApp.includes('pathname === "/privacy"'), "React root must own /privacy");
assert(rootApp.includes('pathname === "/terms"'), "React root must own /terms");
for (const route of ["/login", "/signup", "/verify-email", "/forgot-password", "/reset-password", "/onboarding"]) {
  assert(publicApp.includes(`pathname === "${route}"`), `React public app is missing ${route}`);
}
for (const contract of ["/auth/login", "/auth/signup", "/auth/google", "/auth/verify-email", "/auth/resend-verification", "/auth/forgot-password", "/auth/reset-password", "/workspace-invitations/accept"]) {
  assert(publicApp.includes(contract), `React auth migration is missing ${contract}`);
}
for (const preauthPath of ["/auth/signup", "/auth/login", "/session/login", "/auth/verify-email", "/auth/resend-verification", "/auth/forgot-password", "/auth/reset-password", "/auth/google"]) {
  assert(apiClient.includes(`"${preauthPath}"`), `React API client must recognize ${preauthPath} as a pre-auth CSRF path`);
}
assert(adminPage.includes('type Tab = "overview" | "ai-usage" | "users" | "workspaces"'), "React admin must keep its canonical tabs");
assert(legalPage.includes("Privacy Policy") && legalPage.includes("Terms of Service"), "Legal surfaces must remain present");
assert(legalPage.includes("Google API Services User Data Policy"), "Google Limited Use disclosure must remain present");

// Backend/frontend delivery remains React-only.
assert(apiMain.includes("KNOWN_REACT_ROUTES"), "FastAPI must declare canonical React frontend routes");
assert(apiMain.includes("return react_shell(status_code=404)"), "Unknown frontend routes must render the React shell");
assert(!apiMain.includes("WEB_STATIC"), "FastAPI must not depend on the removed static frontend");
assert(!apiMain.includes('app.mount("/static"'), "Legacy static application mount must stay retired");
assert(!apiMain.includes("approvals_router"), "Canonical API must not mount the retired pre-Kernel approvals router");
assert(!dockerfile.includes("apps/web/static"), "Production image must not depend on apps/web/static");
assert(dockerfile.includes("apps/web/public/operly-logo.png"), "Production logo source must come from Vite public assets");

// Current stylesheet entry must describe the live app, not deleted shell generations.
for (const stylesheet of [
  "foundation.css",
  "account-base.css",
  "workspace.css",
  "experience.css",
  "personal.css",
  "public-surfaces.css",
  "account-overrides.css",
]) {
  assert(main.includes(`import "./ui/${stylesheet}"`), `Frontend entry must load ${stylesheet}`);
}
assert(workspaceStyles.includes("@media (pointer: coarse)") || workspaceStyles.includes("@media (max-width:"), "Workspace shell must retain responsive behavior");
assert(personalStyles.includes("color-scheme: dark"), "Personal Operly must retain the authenticated dark surface");
assert(publicStyles.includes(".react-auth-card"), "React auth styling is missing");
assert(publicStyles.includes("@media (prefers-reduced-motion: reduce)"), "Public/admin motion must respect reduced-motion preference");

for (const legacyPurple of ["#8173ff", "#7568e8", "#b9b0ff", "rgba(126, 104, 255", "rgba(129,115,255", "#7d6cff", "rgba(125,108,255", "rgba(111,92,255"]) {
  assert(!foundationStyles.toLowerCase().includes(legacyPurple.toLowerCase()), `Foundation contains legacy brand accent: ${legacyPurple}`);
}

// Transactional email design remains aligned with the product.
for (const token of ["#f3f5f1", "#13231c", "#dfe6df", "#102f24"]) {
  assert(emailBase.toLowerCase().includes(token), `Transactional email shell is missing canonical Operly token: ${token}`);
}
for (const emailBody of emailBodies) {
  assert(emailBody.toLowerCase().includes("#185d43") || !emailBody.includes("$action_url"), "Transactional email CTA/link must use canonical Operly green #185d43");
  assert(!emailBody.toLowerCase().includes("#176c4a"), "Legacy email green #176c4a must not return");
}

console.log("Canonical React frontend contracts passed.");
