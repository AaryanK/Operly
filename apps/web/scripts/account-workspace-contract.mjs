import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");

async function text(path) { return readFile(resolve(webRoot, path), "utf8"); }
async function repoText(path) { return readFile(resolve(repoRoot, path), "utf8"); }
function assert(condition, message) { if (!condition) throw new Error(message); }

const [safeShell, accountSettings, personalHome, main, discordCss, accountCompatRouter, runtimeEntry] = await Promise.all([
  text("src/workspace-lite/WorkspaceSafeApp.tsx"),
  text("src/account/AccountSettings.tsx"),
  text("src/account/PersonalHome.tsx"),
  text("src/main.tsx"),
  text("src/ui/discord-account-shell.css"),
  repoText("apps/api/account_compat_router.py"),
  repoText("apps/api/runtime_entry.py"),
]);

assert(
  safeShell.includes('import { AccountSettings } from "../account/AccountSettings"') && !safeShell.includes('lazy(() => import("../account/AccountSettings")'),
  "Account settings must ship with the current authenticated shell instead of loading as a legacy lazy UI island",
);
assert(
  safeShell.includes('api<PersonalProfile>("/personal-agent/me")') && safeShell.includes('api<Workspace[]>("/personal-agent/workspaces")'),
  "The active shell must load account identity and account-scoped workspaces",
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
  accountSettings.includes('api<Connector[]>("/personal-connectors")') && !accountSettings.includes('api<ExternalIdentity[]>("/identities")') && !accountSettings.includes('/api/identities'),
  "Current account settings must use registered Personal Operly connector APIs and never call the retired identities surface",
);
assert(
  accountSettings.includes('await api("/personal-agent/me", { method: "PATCH"') && accountSettings.includes('api<WorkspaceSummary>("/workspaces"'),
  "Current account settings must use the registered profile and workspace compatibility endpoints",
);
assert(
  accountSettings.includes('api("/auth/change-password"') && accountSettings.includes('api("/auth/logout"'),
  "Current account settings must retain canonical authentication actions",
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
  accountCompatRouter.includes('@router.get("/api/personal-agent/me")') &&
  accountCompatRouter.includes('@router.get("/api/personal-agent/workspaces")') &&
  accountCompatRouter.includes('@router.post("/api/workspaces", status_code=201)') &&
  accountCompatRouter.includes('workspace["current"] = True'),
  "The production account compatibility router must expose the shell profile/workspace contract",
);
assert(
  runtimeEntry.includes('app.include_router(account_compat_router)') &&
  runtimeEntry.includes('app.router.routes.remove(frontend_catch_all)') &&
  runtimeEntry.includes('app.router.routes.append(frontend_catch_all)'),
  "The production entrypoint must register account compatibility routes ahead of the React catch-all",
);
assert(
  main.includes('import "./ui/discord-account-shell.css"'),
  "The active frontend bundle must load the current Discord-style account shell layer",
);
assert(
  discordCss.includes('.discord-settings-overlay') &&
  discordCss.includes('.discord-user-panel') &&
  discordCss.includes('.personal-new-draft'),
  "The current account shell must own settings, user-panel, and new-conversation presentation",
);

console.log("Current account, workspace, and Personal Operly shell contracts passed.");
