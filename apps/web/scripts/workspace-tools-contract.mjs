import { access, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");
const text = (path) => readFile(resolve(repoRoot, path), "utf8");
const exists = async (path) => {
  try {
    await access(resolve(repoRoot, path));
    return true;
  } catch {
    return false;
  }
};
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const [
  page,
  activityPage,
  home,
  routes,
  router,
  main,
  bootstrap,
  workspaceRuntime,
  nativeProvider,
  integrations,
  discordBot,
  discordLifecycle,
  connections,
  connectionsPage,
  workspaceShell,
  canvaAuthoring,
  integrationRuntime,
  integrationWorkbench,
  gmailPanel,
  calendarPanel,
  canvaPanel,
  discordPanel,
  connectionsManager,
] = await Promise.all([
  text("apps/web/src/workspace/CapabilitiesPage.tsx"),
  text("apps/web/src/workspace/ActivityPage.tsx"),
  text("apps/web/src/workspace/WorkspaceHome.tsx"),
  text("apps/web/src/app/routes.ts"),
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
  text("packages/workspace_modules/integrations/canva/authoring.py"),
  text("apps/web/src/workspace/integrations/runtime.tsx"),
  text("apps/web/src/workspace/integrations/IntegrationWorkbench.tsx"),
  text("apps/web/src/workspace/integrations/GmailPanel.tsx"),
  text("apps/web/src/workspace/integrations/CalendarPanel.tsx"),
  text("apps/web/src/workspace/integrations/CanvaPanel.tsx"),
  text("apps/web/src/workspace/integrations/DiscordPanel.tsx"),
  text("apps/web/src/workspace/integrations/ConnectionsManager.tsx"),
]);

assert(
  page.includes('api<CapabilityResponse>("/workspace-tools")'),
  "Workspace UI must discover tools through /workspace-tools",
);
assert(
  page.includes("api<RunResult>(selected.endpoint"),
  "Workspace UI must invoke each backend-advertised tool endpoint directly",
);
assert(
  page.includes("/workspace-tools/approvals/"),
  "Workspace UI approval resume must stay on workspace-tools API",
);
assert(
  page.includes("api<Capability>(selected.contract_endpoint)"),
  "Workspace UI must let a human re-check the exact advertised tool contract",
);
assert(
  page.includes("Guided form") && page.includes("buildArguments(selected, fieldValues)"),
  "Every discovered tool must have a schema-driven guided form, not only raw JSON",
);
assert(
  page.includes("Every currently authorized tool advertised by the Workspace API appears here automatically"),
  "All Tools must explicitly be the universal frontend coverage surface",
);
assert(
  page.includes("Don’t do it") && page.includes("decideApproval(false)") && page.includes("decideApproval(true)"),
  "Friendly tool UI must support both approving and denying a gated action",
);
assert(
  page.includes("{selected.method} /api{selected.endpoint}"),
  "Workspace UI must expose the real callable HTTP endpoint in technical details",
);
assert(!page.includes('"/kernel/execute"'), "Workspace UI must not use the generic Kernel execute endpoint");
assert(!page.includes('"/kernel/capabilities"'), "Workspace UI must not discover tools from the generic Kernel route");

assert(
  activityPage.includes('"/workspace-tools/approvals?limit=50"') && activityPage.includes("/workspace-tools/approvals/${encodeURIComponent(id)}/decision"),
  "Activity must expose Workspace tool approval review and decisions",
);
assert(
  activityPage.includes('"/workspace-tools/events?limit=80"'),
  "Activity must expose Workspace tool event history",
);
assert(
  activityPage.includes("/workspace-tools/runs/${encodeURIComponent(clean)}"),
  "Activity must expose the Workspace tool run inspector",
);
assert(home.includes('section: "capabilities", title: "Use any tool"'), "Workspace Home must make universal tool access obvious");
assert(routes.includes('{ id: "capabilities", label: "All tools"'), "Workspace navigation must use the plain-language All tools label");
assert(workspaceShell.includes('extend: "Tools & connections"'), "Workspace navigation group must be understandable without platform jargon");

assert(router.includes('prefix="/api/workspace-tools"'), "Workspace tools need their own authenticated API boundary");
assert(router.includes('@router.post("/{capability_id}/execute")'), "Every capability ID must resolve to an executable endpoint");
assert(router.includes('"endpoint": workspace_tool_endpoint(spec.id)'), "Tool discovery must advertise the exact execute endpoint");
assert(router.includes("await _available_tool(db, context, capability_id)"), "Endpoint execution must preflight current authority/availability");
assert(router.includes('@router.get("/approvals")'), "Workspace tool approvals must remain an inspectable API surface");
assert(router.includes('@router.get("/events")'), "Workspace tool events must remain an inspectable API surface");
assert(router.includes('@router.get("/runs/{run_id}")'), "Workspace tool runs must remain an inspectable API surface");

for (const file of [
  "records.py",
  "controls.py",
  "business.py",
  "availability.py",
  "system.py",
  "runtime.py",
  "router.py",
  "__init__.py",
]) {
  assert(
    await exists(`packages/workspace_modules/tools/${file}`),
    `Workspace tool package is missing ${file}`,
  );
}
assert(
  !(await exists("packages/workspace_modules/tools/google.py")),
  "Google must live in the integrations package, not generic Workspace tools",
);

for (const provider of ["google", "canva", "discord"]) {
  assert(
    await exists(`packages/workspace_modules/integrations/${provider}/__init__.py`),
    `${provider} integration package is missing`,
  );
  assert(
    await exists(`packages/workspace_modules/integrations/${provider}/provider.py`),
    `${provider} deterministic provider is missing`,
  );
  assert(
    await exists(`packages/workspace_modules/integrations/${provider}/permissions.py`),
    `${provider} permission resolver is missing`,
  );
}
assert(
  await exists("packages/workspace_modules/integrations/canva/authoring.py"),
  "Canva authoring capability package is missing",
);

for (const legacy of [
  "workspace_os_provider.py",
  "workspace_control_provider.py",
  "workspace_business_provider.py",
  "workspace_google_provider.py",
  "provider_availability.py",
]) {
  assert(
    !(await exists(`packages/kernel/${legacy}`)),
    `Workspace-owned code leaked back into packages/kernel/${legacy}`,
  );
}
assert(
  !(await exists("apps/api/workspace_tools_router.py")),
  "Workspace tool router must live in workspace_modules, not apps/api",
);

assert(!bootstrap.includes("packages.workspace_modules"), "Generic Kernel composition must not import Workspace modules");
assert(workspaceRuntime.includes("workspace_capabilities()"), "Workspace package must own capability composition");
assert(workspaceRuntime.includes("register_workspace_providers(runtime.providers)"), "Workspace package must own provider composition");
assert(!nativeProvider.includes("_workspace_describe"), "Generic native provider must not implement Workspace domain operations");
assert(!nativeProvider.includes("_workspace_modules"), "Generic native provider must not implement Workspace module operations");

assert(integrations.includes("workspace_google_capabilities"), "Google capabilities must be composed by Workspace integrations");
assert(integrations.includes("workspace_canva_capabilities"), "Canva capabilities must be composed by Workspace integrations");
assert(integrations.includes("workspace_canva_authoring_capabilities"), "Canva authoring capabilities must be composed by Workspace integrations");
assert(integrations.includes("workspace_discord_capabilities"), "Discord capabilities must be composed by Workspace integrations");
assert(connections.includes('prefix="/api/connectors"'), "Workspace integration package must own connector management endpoints");
assert(main.includes("workspace_integrations_router"), "FastAPI must mount Workspace-owned connection management");
assert(main.includes("await discord_bot_lifecycle.start()"), "Application lifespan must start the deterministic Discord bot");
assert(main.includes("await discord_bot_lifecycle.stop()"), "Application lifespan must stop the deterministic Discord bot");

assert(workspaceShell.includes('import("./ConnectionsPage")'), "Workspace shell must render the dedicated integration workbench");
assert(connectionsPage.includes("IntegrationWorkbench"), "Connections page must delegate to the modular integration workbench");
assert(integrationWorkbench.includes("IntegrationRuntimeProvider"), "Integration workbench must use the shared deterministic integration runtime");
assert(integrationWorkbench.includes("GmailPanel"), "Integration workbench must mount Gmail UI");
assert(integrationWorkbench.includes("CalendarPanel"), "Integration workbench must mount Calendar UI");
assert(integrationWorkbench.includes("CanvaPanel"), "Integration workbench must mount Canva UI");
assert(integrationWorkbench.includes("DiscordPanel"), "Integration workbench must mount Discord UI");

assert(
  integrationRuntime.includes('api<ToolIndex>("/workspace-tools")'),
  "Integration runtime must discover currently executable Workspace tools",
);
assert(
  integrationRuntime.includes("tool.endpoint"),
  "Integration runtime must execute backend-advertised endpoints rather than bypassing Workspace tools",
);
assert(
  integrationRuntime.includes("/workspace-tools/approvals/"),
  "Integration runtime must resume approval-gated tools through the Workspace approval boundary",
);
assert(
  integrationRuntime.includes('"/connectors/google/connect?tier=assistant"'),
  "Integration runtime must initiate Google Workspace OAuth",
);
assert(
  integrationRuntime.includes('"/connectors/canva/connect"'),
  "Integration runtime must initiate Canva OAuth",
);
assert(
  integrationRuntime.includes('api<DiscordStatus>("/connectors/discord/status")'),
  "Integration runtime must inspect the deterministic Discord bot",
);

for (const capability of [
  "google.gmail.search",
  "google.gmail.read_message",
  "google.gmail.create_draft",
  "google.gmail.send_email",
  "google.gmail.modify_labels",
]) {
  assert(gmailPanel.includes(`"${capability}"`), `Gmail panel is missing ${capability}`);
}
for (const capability of [
  "google.calendar.list_calendars",
  "google.calendar.list_events",
  "google.calendar.freebusy",
  "google.calendar.create_event",
  "google.calendar.update_event",
  "google.calendar.delete_event",
]) {
  assert(calendarPanel.includes(`"${capability}"`), `Calendar panel is missing ${capability}`);
}
for (const capability of [
  "canva.designs.list",
  "canva.design.get",
  "canva.design.create",
  "canva.design.export_formats",
  "canva.design.export.create",
  "canva.design.export.get",
  "canva.folder.items.list",
  "canva.design.dataset",
  "canva.brand_templates.list",
  "canva.brand_template.get",
  "canva.brand_template.dataset",
  "canva.autofill.create",
  "canva.autofill.get",
]) {
  assert(canvaPanel.includes(`"${capability}"`), `Canva panel is missing ${capability}`);
}
for (const capability of [
  "discord.installations.list",
  "discord.channels.list",
  "discord.messages.list",
  "discord.message.send",
  "discord.reaction.add",
  "discord.thread.create",
]) {
  assert(discordPanel.includes(`"${capability}"`), `Discord panel is missing ${capability}`);
}
assert(discordPanel.includes("AI off"), "Integration workbench must make Discord's deterministic-only state explicit");
assert(connectionsManager.includes("Reconnect / expand scopes"), "Connection manager must support scope expansion");

for (const capability of [
  "canva.design.dataset",
  "canva.brand_templates.list",
  "canva.brand_template.dataset",
  "canva.autofill.create",
  "canva.autofill.get",
]) {
  assert(
    canvaAuthoring.includes(`"${capability}"`),
    `Canva authoring provider is missing ${capability}`,
  );
}
assert(
  !canvaAuthoring.includes('"canva.design.pages.list"'),
  "Preview Canva design-pages API must not be exposed in the production capability surface",
);
assert(canvaAuthoring.includes("approval=True"), "Canva in-place/new-design autofill must remain approval gated");

for (const forbidden of [
  "AgentRuntime",
  "ChannelService.handle",
  "model_runtime",
  "secure_runtime",
  "packages.agents",
]) {
  assert(!discordBot.includes(forbidden), `Deterministic Discord bot leaked AI runtime dependency: ${forbidden}`);
  assert(!discordLifecycle.includes(forbidden), `Discord lifecycle leaked AI runtime dependency: ${forbidden}`);
}
assert(discordBot.includes("AI chat is not enabled yet"), "Discord bot must make the deterministic-only behavior explicit");

console.log("Workspace tools, universal human control surface, and deterministic integration workbench contracts passed.");
