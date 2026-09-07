import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");

async function text(path) { return readFile(resolve(webRoot, path), "utf8"); }
async function repoText(path) { return readFile(resolve(repoRoot, path), "utf8"); }
function assert(condition, message) { if (!condition) throw new Error(message); }

const [safeShell, accountSettings, personalHome, main, discordCss, operlyThemeCss, appTypes, accountCompatRouter, runtimeEntry, authSession] = await Promise.all([
  text("src/workspace-lite/WorkspaceSafeApp.tsx"),
  text("src/account/AccountSettings.tsx"),
  text("src/account/PersonalHome.tsx"),
  text("src/main.tsx"),
  text("src/ui/discord-account-shell.css"),
  text("src/ui/operly-settings-theme.css"),
  text("src/app/types.ts"),
  repoText("apps/api/account_compat_router.py"),
  repoText("apps/api/runtime_entry.py"),
  repoText("apps/api/session.py"),
]);

assert(
  safeShell.includes('import { AccountSettings } from "../account/AccountSettings"') && !safeShell.includes('lazy(() => import("../account/AccountSettings")'),
  "Account settings must ship with the current authenticated shell instead of loading as a legacy lazy UI island",
);
assert(
  safeShell.includes('api<PersonalProfile>("/auth/me")') && safeShell.includes('api<Workspace[]>("/auth/workspaces")'),
  "The active shell must bootstrap identity and workspaces from the authenticated account/session namespace",
);
assert(
  !safeShell.includes('"/personal-agent/me"') && !safeShell.includes('"/personal-agent/workspaces"'),
  "The active workspace shell must not depend on Personal Agent account bootstrap aliases",
);
assert(
  safeShell.includes('className="workspace-lite-mark workspace-lite-add"') && safeShell.includes('aria-label="Create workspace"'),
  "The Discord-style scope rail must expose workspace creation",
);
assert(
  !safeShell.includes('workspace-lite-account') && safeShell.includes('onOpenSettings={() => openAccountSettings("account")}'),
  "Profile settings must live in the Personal Operly user panel, not as a detached avatar on the server rail",
);
assert(
  safeShell.includes('void logout()') && safeShell.includes('Signing out…'),
  "The account menu must retain direct sign-out access",
);

assert(
  accountSettings.includes('api<Connector[]>("/personal-connectors")') && !accountSettings.includes('/identities'),
  "Current account settings must use registered Personal Operly connector APIs and never call the retired identities surface",
);
assert(
  accountSettings.includes('await api("/auth/me", { method: "PATCH"') && accountSettings.includes('api<WorkspaceCreateResult>("/auth/workspaces"'),
  "Current account settings must use authenticated profile and workspace endpoints",
);
assert(
  accountSettings.includes('api("/auth/change-password"') && accountSettings.includes('api("/auth/logout"'),
  "Current account settings must retain canonical authentication actions",
);
assert(
  accountSettings.includes('profile?.auth_identities?.find((identity) => identity.provider === "google")') &&
  accountSettings.includes('Google currently verifies your identity only') &&
  accountSettings.includes('Enable Google tools') &&
  accountSettings.includes('Disconnect tools'),
  "Google sign-in identity and separately-consented Google tool access must render as one clear account state",
);
assert(
  !accountSettings.includes('Legacy identity routes') && !accountSettings.includes('authorization boundaries'),
  "User settings must not expose internal implementation jargon",
);

assert(
  personalHome.includes('const [draftConversation, setDraftConversation] = useState(false)') &&
  personalHome.includes('className="active personal-new-draft"') &&
  personalHome.includes('composerInput.current?.focus()'),
  "New conversation must enter a visible draft state and focus the composer",
);
assert(
  personalHome.includes('className="history-account discord-user-panel"') &&
  personalHome.includes('className="discord-user-settings"') &&
  personalHome.includes('onClick={onOpenSettings}'),
  "The Personal Operly name/email row must be an interactive Discord-style user settings panel",
);

assert(
  accountCompatRouter.includes('@router.get("/api/auth/me")') &&
  accountCompatRouter.includes('@router.patch("/api/auth/me")') &&
  accountCompatRouter.includes('select(AuthIdentity)') &&
  accountCompatRouter.includes('"auth_identities"'),
  "The authenticated profile boundary must expose the account's linked sign-in methods without reviving legacy identity routes",
);
assert(
  appTypes.includes('export type AuthIdentitySummary') && appTypes.includes('auth_identities?: AuthIdentitySummary[]'),
  "Frontend account profile typing must include linked authentication methods",
);
assert(
  authSession.includes('@router.get("/api/auth/workspaces")') &&
  authSession.includes('@router.post("/api/auth/workspaces", status_code=201)') &&
  authSession.includes('role="owner"'),
  "Workspace listing and creation must remain canonical auth/session operations",
);
assert(
  runtimeEntry.includes('app.include_router(account_compat_router)') &&
  runtimeEntry.includes('app.router.routes.remove(frontend_catch_all)') &&
  runtimeEntry.includes('app.router.routes.append(frontend_catch_all)'),
  "The production entrypoint must register the account profile boundary ahead of the React catch-all",
);
assert(
  main.includes('import "./ui/discord-account-shell.css"') &&
  main.includes('import "./ui/operly-settings-theme.css"') &&
  main.lastIndexOf('./ui/operly-settings-theme.css') > main.lastIndexOf('./ui/discord-account-shell.css'),
  "Discord settings structure must be followed by Operly's visual theme layer",
);
assert(
  discordCss.includes('.discord-settings-overlay') &&
  discordCss.includes('.discord-user-panel') &&
  discordCss.includes('.personal-new-draft'),
  "The structural account shell must still own settings, user-panel, and new-conversation layout",
);
assert(
  operlyThemeCss.includes('var(--ui-accent') &&
  operlyThemeCss.includes('var(--ui-panel') &&
  operlyThemeCss.includes('var(--ui-nav') &&
  !operlyThemeCss.includes('#5865f2') &&
  !operlyThemeCss.includes('#313338'),
  "The final settings presentation must use Operly theme tokens instead of Discord colors",
);

console.log("Current account, workspace, Personal Operly, Google identity, and settings-theme contracts passed.");
