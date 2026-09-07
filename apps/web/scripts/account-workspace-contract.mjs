import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");

async function text(path) { return readFile(resolve(webRoot, path), "utf8"); }
async function repoText(path) { return readFile(resolve(repoRoot, path), "utf8"); }
function assert(condition, message) { if (!condition) throw new Error(message); }

const [safeShell, accountSettings, main, workspaceRouter] = await Promise.all([
  text("src/workspace-lite/WorkspaceSafeApp.tsx"),
  text("src/account/AccountSettings.tsx"),
  text("src/main.tsx"),
  repoText("apps/api/workspace_router.py"),
]);

assert(
  safeShell.includes('const AccountSettings = lazy(() => import("../account/AccountSettings")'),
  "The active authenticated shell must expose the canonical account settings UI",
);
assert(
  safeShell.includes('api<PersonalProfile>("/personal-agent/me")') && safeShell.includes('api<Workspace[]>("/personal-agent/workspaces")'),
  "The active shell must load account identity and account-scoped workspaces",
);
assert(
  safeShell.includes('className="workspace-lite-mark workspace-lite-add"') && safeShell.includes('aria-label="Create workspace"'),
  "The Discord-style scope rail must expose a visible create-workspace control",
);
assert(
  safeShell.includes('openAccountSettings("workspaces")') && safeShell.includes('>Create workspace</button>'),
  "The workspace landing experience must let a new user create their first workspace",
);
assert(
  safeShell.includes('openAccountSettings("account")') && safeShell.includes('>Account settings</button>'),
  "Account identity controls must open account settings directly",
);
assert(
  safeShell.includes('void logout()') && safeShell.includes('Signing out…'),
  "The account menu must retain direct sign-out access",
);
assert(
  safeShell.includes('<PersonalHome profile={profile} />'),
  "Personal Operly must receive the signed-in profile when rendered by the active shell",
);

assert(
  accountSettings.includes('api<WorkspaceSummary>("/workspaces", { method: "POST"'),
  "Canonical account settings must create workspaces through the account-scoped workspace endpoint",
);
assert(
  accountSettings.includes('api("/auth/logout", { method: "POST"'),
  "Canonical account settings must retain sign-out support",
);
assert(
  workspaceRouter.includes('@router.post("/workspaces", status_code=201)') && workspaceRouter.includes('role="owner"'),
  "Workspace creation backend must create an owner membership for the creator",
);
assert(
  main.includes('import "./ui/settings.css"') && main.includes('import "./ui/connection-avatars.css"'),
  "The active frontend bundle must load account settings presentation styles",
);

console.log("Account and workspace shell contracts passed.");
