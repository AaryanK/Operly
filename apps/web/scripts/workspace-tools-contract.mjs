import { access, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");
const text = (path) => readFile(resolve(repoRoot, path), "utf8");
const exists = async (path) => { try { await access(resolve(repoRoot, path)); return true; } catch { return false; } };
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const [page, router, main, bootstrap, workspaceRuntime, nativeProvider, integrations, discordBot, discordLifecycle, connections, connectionsPage, workspaceShell] = await Promise.all([
  text("apps/web/src/workspace/CapabilitiesPage.tsx"),
  text("packages/workspace_modules/tools/router.py"),
  text("apps/api/main.py"),
  text("packages/kernel/bootstrap.py"),
  text("packages/workspace_modules/tools/runtime.py"),
  text("packages/kernel/providers.py"),
  text("packages/workspace_modules/integrations/__init__.py"),
  text("packages/workspace_modules/integrations/discord/bot.py"),
  text("packages/workspace_modules/integrations/discord/lifecycle.py"),
  text("packages/workspace_modules/integrations/router.py"),
  text("apps/web/src/workspace/ConnectionsPage.tsx"),
  text("apps/web/src/workspace/WorkspaceShell.tsx"),
]);

assert(page.includes('api<CapabilityResponse>("/workspace-tools")'), "Workspace UI must discover tools through /workspace-tools");
assert(page.includes("api<RunResult>(selected.endpoint"), "Workspace UI must invoke each backend-advertised tool endpoint directly");
assert(page.includes("/workspace-tools/approvals/"), "Workspace UI approval resume must stay on workspace-tools API");
assert(page.includes("{item.method} /api{item.endpoint}"), "Workspace UI must expose the real callable HTTP endpoint");
assert(!page.includes('"/kernel/execute"'), "Workspace UI must not use the generic Kernel execute endpoint");
assert(!page.includes('"/kernel/capabilities"'), "Workspace UI must not discover tools from the generic Kernel route");

assert(router.includes('prefix="/api/workspace-tools"'), "Workspace tools need their own authenticated API boundary");
assert(router.includes('@router.post("/{capability_id}/execute")'), "Every capability ID must resolve to an executable endpoint");
assert(router.includes('"endpoint": workspace_tool_endpoint(spec.id)'), "Tool discovery must advertise the exact execute endpoint");
assert(router.includes("await _available_tool(db, context, capability_id)"), "Endpoint execution must preflight current authority/availability");

for (const file of ["records.py", "controls.py", "business.py", "availability.py", "system.py", "runtime.py", "router.py", "__init__.py"]) {
  assert(await exists(`packages/workspace_modules/tools/${file}`), `Workspace tool package is missing ${file}`);
}
assert(!(await exists("packages/workspace_modules/tools/google.py")), "Google must live in the integrations package, not generic Workspace tools");

for (const provider of ["google", "canva", "discord"]) {
  assert(await exists(`packages/workspace_modules/integrations/${provider}/__init__.py`), `${provider} integration package is missing`);
  assert(await exists(`packages/workspace_modules/integrations/${provider}/provider.py`), `${provider} deterministic provider is missing`);
  assert(await exists(`packages/workspace_modules/integrations/${provider}/permissions.py`), `${provider} permission resolver is missing`);
}

for (const legacy of ["workspace_os_provider.py", "workspace_control_provider.py", "workspace_business_provider.py", "workspace_google_provider.py", "provider_availability.py"]) {
  assert(!(await exists(`packages/kernel/${legacy}`)), `Workspace-owned code leaked back into packages/kernel/${legacy}`);
}
assert(!(await exists("apps/api/workspace_tools_router.py")), "Workspace tool router must live in workspace_modules, not apps/api");

assert(!bootstrap.includes("packages.workspace_modules"), "Generic Kernel composition must not import Workspace modules");
assert(workspaceRuntime.includes("workspace_capabilities()"), "Workspace package must own capability composition");
assert(workspaceRuntime.includes("register_workspace_providers(runtime.providers)"), "Workspace package must own provider composition");
assert(!nativeProvider.includes("_workspace_describe"), "Generic native provider must not implement Workspace domain operations");
assert(!nativeProvider.includes("_workspace_modules"), "Generic native provider must not implement Workspace module operations");

assert(integrations.includes("workspace_google_capabilities"), "Google capabilities must be composed by Workspace integrations");
assert(integrations.includes("workspace_canva_capabilities"), "Canva capabilities must be composed by Workspace integrations");
assert(integrations.includes("workspace_discord_capabilities"), "Discord capabilities must be composed by Workspace integrations");
assert(connections.includes('prefix="/api/connectors"'), "Workspace integration package must own connector management endpoints");
assert(main.includes("workspace_integrations_router"), "FastAPI must mount Workspace-owned connection management");
assert(main.includes("await discord_bot_lifecycle.start()"), "Application lifespan must start the deterministic Discord bot");
assert(main.includes("await discord_bot_lifecycle.stop()"), "Application lifespan must stop the deterministic Discord bot");

assert(workspaceShell.includes('import("./ConnectionsPage")'), "Workspace shell must render the dedicated integration Connections page");
assert(connectionsPage.includes('api<Connection[]>("/connectors")'), "Connections UI must load Workspace-owned connector state");
assert(connectionsPage.includes('"/connectors/google/connect?tier=assistant"'), "Connections UI must initiate Google Workspace OAuth");
assert(connectionsPage.includes('"/connectors/canva/connect"'), "Connections UI must initiate Canva OAuth");
assert(connectionsPage.includes('api<DiscordStatus>("/connectors/discord/status")'), "Connections UI must inspect the deterministic Discord bot");
assert(connectionsPage.includes("Add Discord bot"), "Connections UI must expose Discord installation");
assert(connectionsPage.includes("Discord AI"), "Connections UI must show that Discord AI is disabled");

for (const forbidden of ["AgentRuntime", "ChannelService.handle", "model_runtime", "secure_runtime", "packages.agents"]) {
  assert(!discordBot.includes(forbidden), `Deterministic Discord bot leaked AI runtime dependency: ${forbidden}`);
  assert(!discordLifecycle.includes(forbidden), `Discord lifecycle leaked AI runtime dependency: ${forbidden}`);
}
assert(discordBot.includes("AI chat is not enabled yet"), "Discord bot must make the deterministic-only behavior explicit");

console.log("Workspace tools and deterministic integration contracts passed.");
