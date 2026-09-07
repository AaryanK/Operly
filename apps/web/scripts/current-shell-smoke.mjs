import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const read = (path) => readFile(resolve(webRoot, path), "utf8");
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const [shell, settings, personal, css] = await Promise.all([
  read("src/workspace-lite/WorkspaceSafeApp.tsx"),
  read("src/account/AccountSettings.tsx"),
  read("src/account/PersonalHome.tsx"),
  read("src/ui/discord-account-shell.css"),
]);

assert(!settings.includes("/identities"), "legacy identities API must not be referenced by current settings");
assert(shell.includes('import { AccountSettings } from "../account/AccountSettings"'), "settings must be part of the current shell bundle");
assert(!shell.includes("workspace-lite-account"), "detached account avatar must not be on the server rail");
assert(shell.includes('onOpenSettings={() => openAccountSettings("account")}'), "Personal profile must open user settings");
assert(personal.includes("draftConversation"), "Personal Operly must expose a new-conversation draft state");
assert(personal.includes("discord-user-panel"), "Personal Operly must expose the Discord-style user panel");
assert(css.includes(".discord-settings-overlay") && css.includes(".discord-user-panel"), "current account shell styles must be present");

console.log("Current shell smoke contract passed.");
