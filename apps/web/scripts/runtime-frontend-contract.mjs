import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");

async function text(path) { return readFile(resolve(webRoot, path), "utf8"); }
function assert(condition, message) { if (!condition) throw new Error(message); }

const [rootApp, productApp, useScope, scopeRail, accountSettings, personalHome, workspaceShell, workspaceHome, routes] = await Promise.all([
  text("src/app/App.tsx"),
  text("src/app/ProductApp.tsx"),
  text("src/app/useScope.ts"),
  text("src/account/ScopeRail.tsx"),
  text("src/account/AccountSettings.tsx"),
  text("src/account/PersonalHome.tsx"),
  text("src/workspace/WorkspaceShell.tsx"),
  text("src/workspace/WorkspaceHome.tsx"),
  text("src/app/routes.ts"),
]);

// One authenticated shell must own the complete Personal/Workspace journey.
assert(rootApp.includes('import { ProductApp } from "./ProductApp"'), "Authenticated Runtime 1.0 routes must import ProductApp");
assert(rootApp.includes("return <ProductApp />"), "Personal/workspace Runtime 1.0 routes must render ProductApp");
assert(rootApp.includes('pathname === "/personal"'), "Legacy /personal must converge into ProductApp");
assert(rootApp.includes('pathname === "/channels/@me"'), "Canonical Personal route must use ProductApp");
assert(rootApp.includes('pathname.startsWith("/channels/")'), "Workspace channel routes must use ProductApp");

// Bootstrap only from mounted authenticated routes. These checks exist because the
// previous ProductApp referenced unmounted /personal-agent/workspaces and never made
// it through a real browser journey.
assert(useScope.includes('api<WorkspaceSummary[]>("/auth/workspaces")'), "Canonical shell must bootstrap membership from /auth/workspaces");
assert(!useScope.includes("/personal-agent/workspaces"), "Canonical shell must not depend on the unmounted /personal-agent/workspaces route");
assert(!useScope.includes("/api/me"), "Canonical shell must not depend on the retired /api/me bootstrap");

// Scope transitions are mutating requests and must satisfy the JSON request-safety contract.
assert(useScope.includes('api("/auth/personal-scope", { method: "POST", body: "{}" })'), "Personal scope switch must send an explicit JSON body");
assert(useScope.includes('body: JSON.stringify({ tenant_id: workspaceId })'), "Workspace switch must send an explicit JSON body");

// Sign out and workspace creation must be real reachable actions from the shared shell.
assert(scopeRail.includes("onSignOut"), "Canonical scope rail must expose sign out");
assert(productApp.includes('api("/auth/logout", { method: "POST", body: "{}" })'), "Product shell sign out must call the authenticated logout endpoint with JSON");
assert(productApp.includes('window.location.assign("/login")'), "Product shell sign out must hard-navigate to login after clearing session state");
assert(accountSettings.includes('>("/auth/workspaces", {'), "Create workspace must use the mounted authenticated workspace endpoint");
assert(!accountSettings.includes('>("/workspaces", {'), "Account settings must not call the retired /workspaces endpoint");
assert(!accountSettings.includes('/personal-agent/me'), "Account settings must not show a profile-save action backed by an unmounted endpoint");

// Personal human control must use Kernel v3 approvals, not the retired legacy approvals router.
assert(personalHome.includes('"/kernel/personal/approvals?limit=12"'), "Personal Operly must read canonical Kernel approvals");
assert(personalHome.includes('/kernel/personal/approvals/${encodeURIComponent(id)}/decision'), "Personal Operly must decide canonical Kernel approvals");
assert(!personalHome.includes('"/approvals/personal"'), "Personal Operly must not call the unmounted legacy approval endpoint");

// Workspace Operly must remain a first-class workspace surface, not an optional side route.
assert(routes.includes('{ id: "operly", label: "Operly", group: "workspace" }'), "Operly must be a primary workspace navigation section");
assert(workspaceShell.includes('case "operly": return <DeferredPage><WorkspaceOperly workspace={workspace} /></DeferredPage>;'), "Workspace shell must render WorkspaceOperly directly");
assert(workspaceHome.includes('title: "Ask Operly"'), "Workspace home must surface an Ask Operly destination");
assert(workspaceHome.includes('workspacePath(workspace.id, "operly")'), "Workspace home must route directly into Workspace Operly");

console.log("Runtime 1.0 frontend journey contracts passed.");
