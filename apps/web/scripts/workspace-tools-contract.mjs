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
  liveShell,
  router,
  main,
  bootstrap,
  workspaceRuntime,
  nativeProvider,
  integrations,
  discordBot,
  discordLifecycle,
  discordRuntimeStatus,
  discordProvider,
  connections,
  connectionsPage,
  canvaAuthoring,
  integrationRuntime,
  integrationWorkbench,
  gmailPanel,
  calendarPanel,
  canvaPanel,
  discordPanel,
  connectionsManager,
  capabilityRuntime,
  workspaceOSPanel,
  workspaceControls,
] = await Promise.all([
  text("apps/web/src/workspace/CapabilitiesPage.tsx"),
  text("apps/web/src/workspace/ActivityPage.tsx"),
  text("apps/web/src/workspace-lite/WorkspaceSafeApp.tsx"),
  text("packages/workspace_modules/tools/router.py"),
  text("apps/api/main.py"),
  text("packages/kernel/bootstrap.py"),
  text("packages/workspace_modules/tools/runtime.py"),
  text("packages/kernel/providers.py"),
  text("packages/workspace_modules/integrations/__init__.py"),
  text("packages/workspace_modules/integrations/discord/bot.py"),
  text("packages/workspace_modules/integrations/discord/lifecycle.py"),
  text("packages/workspace_modules/integrations/discord/runtime_status.py"),
  text("packages/workspace_modules/integrations/discord/provider.py"),
  text("packages/workspace_modules/integrations/router.py"),
  text("apps/web/src/workspace/ConnectionsPage.tsx"),
  text("packages/workspace_modules/integrations/canva/authoring.py"),
  text("apps/web/src/workspace/integrations/runtime.tsx"),
  text("apps/web/src/workspace/integrations/IntegrationWorkbench.tsx"),
  text("apps/web/src/workspace/integrations/GmailPanel.tsx"),
  text("apps/web/src/workspace/integrations/CalendarPanel.tsx"),
  text("apps/web/src/workspace/integrations/CanvaPanel.tsx"),
  text("apps/web/src/workspace/integrations/DiscordPanel.tsx"),
  text("apps/web/src/workspace/integrations/ConnectionsManager.tsx"),
  text("apps/web/src/runtime/capabilityRuntime.ts"),
  text("apps/web/src/workspace-lite/WorkspaceOSPanel.tsx"),
  text("packages/workspace_modules/tools/controls.py"),
]);

assert(
  page.includes('api<CapabilityResponse>("/workspace-tools")'),
  "Workspace UI must discover tools through /workspace-tools",
);
assert(
  page.includes("executeCapability(selected, parsed)"),
  "Workspace UI must invoke capabilities through the shared frontend runtime",
);
assert(
  page.includes("approveAndResumeCapability(pendingApproval)") && page.includes("denyCapability(pendingApproval)"),
  "Workspace UI approval decisions must use the shared exact-payload resume path",
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
  activityPage.includes('"/workspace-tools/approvals?limit=50"') && activityPage.includes("decideCapabilityApproval(id, approved)"),
  "Activity must expose Workspace tool approval review through the shared Kernel approval client",
);
assert(
  activityPage.includes('"/workspace-tools/events?limit=80"'),
  "Activity must expose Workspace tool event history",
);
assert(
  activityPage.includes("/workspace-tools/runs/${encodeURIComponent(clean)}"),
  "Activity must expose the Workspace tool run inspector",
);
assert(
  liveShell.includes('section="capabilities"') && liveShell.includes(">All tools</WorkspaceControlLink>"),
  "Live Workspace shell must make universal tool access obvious",
);
assert(
  liveShell.includes('section="connections"') && liveShell.includes(">Integrations</WorkspaceControlLink>"),
  "Live Workspace shell must expose governed integrations",
);

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

assert(
  !bootstrap.includes("from packages.workspace_modules") && !bootstrap.includes("import packages.workspace_modules"),
  "Generic Kernel composition must not import Workspace modules",
);
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

assert(liveShell.includes('import("../workspace/ConnectionsPage")'), "Live Workspace shell must render the dedicated integration workbench");
assert(connectionsPage.includes("IntegrationWorkbench"), "Connections page must delegate to the modular integration workbench");
assert(integrationWorkbench.includes("IntegrationRuntimeProvider"), "Integration workbench must use the shared deterministic integration runtime");
assert(integrationWorkbench.includes("GmailPanel"), "Integration workbench must mount Gmail UI");
assert(integrationWorkbench.includes("CalendarPanel"), "Integration workbench must mount Calendar UI");
assert(integrationWorkbench.includes("CanvaPanel"), "Integration workbench must mount Canva UI");
assert(integrationWorkbench.includes("DiscordPanel"), "Integration workbench must mount Discord UI");

assert(
  integrationRuntime.includes("loadWorkspaceCapabilities()"),
  "Integration runtime must discover Workspace tools through the shared capability runtime",
);
assert(
  integrationRuntime.includes("executeCapability(tool, args)"),
  "Integration runtime must execute through the shared frontend capability runtime",
);
assert(
  integrationRuntime.includes("approveAndResumeCapability(approval)") && integrationRuntime.includes("denyCapability(approval)"),
  "Integration runtime must use the shared approval and resume path",
);
assert(
  capabilityRuntime.includes('api<CapabilityCatalog>("/workspace-tools")') && capabilityRuntime.includes("api<CapabilityRun>(tool.endpoint"),
  "Shared capability runtime must discover tools and execute backend-advertised endpoints",
);
assert(
  capabilityRuntime.includes("/workspace-tools/approvals/") && capabilityRuntime.includes("request_id: requestId") && capabilityRuntime.includes("approval_id: options.approvalId"),
  "Shared capability runtime must preserve request identity across approval resume",
);
assert(
  workspaceOSPanel.includes("recordCapabilityId(moduleKey, def.entity, operation)") && workspaceOSPanel.includes("executeCapability(tool, args)") && workspaceOSPanel.includes("loadWorkspaceCapabilities()"),
  "Generic Workspace record mutations must execute through the shared capability runtime",
);
assert(
  !workspaceOSPanel.includes('method: record?.id ? "PATCH" : "POST"') && !workspaceOSPanel.includes('method: "DELETE" }); await load(data.offset)'),
  "Generic Workspace record create/update/delete must not bypass the Kernel through direct REST mutations",
);
for (const capability of [
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
  assert(
    workspaceOSPanel.includes(`tools.get("${capability}")`),
    `Live Workspace UI must route ${capability} through the capability runtime`,
  );
}
for (const forbidden of [
  'api("/workspace-os/settings", { method: "PATCH"',
  'api(`/workspace-os/modules/${module.key}`, { method: "PUT"',
  'api(`/workspace-os/presets/${preset.key}/apply`, { method: "POST"',
  'api("/workspace-os/members", { method: "POST"',
  'api(`/workspace-os/members/${userId}`, { method: "PATCH"',
  'api(`/workspace-os/members/${member.user_id}`, { method: "DELETE"',
  'api("/workspace-os/invitations", { method: "POST"',
  'api(`/workspace-os/invitations/${invite.id}`, { method: "DELETE"',
  'api(`/workspace-os/roles/${role.key}`, { method: "PUT"',
  'api(`/workspace-os/inventory/${itemId}/adjust`, { method: "POST"',
]) {
  assert(!workspaceOSPanel.includes(forbidden), `Live Workspace UI mutation bypass returned: ${forbidden}`);
}
assert(
  workspaceControls.includes('"invite_url": {"type": "string"}') &&
  workspaceControls.includes('f"{base}/join#invite={quote(token, safe=\'\')}"'),
  "Governed invitation creation must preserve the canonical deployment invite URL",
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
assert(
  discordPanel.includes('"AI ready"') && discordPanel.includes('"AI unavailable"') && discordPanel.includes("discordStatus?.ai_enabled"),
  "Integration workbench must show the live Discord Agent Runtime readiness state",
);
assert(
  connections.includes("discord_ai_runtime_status()") && connections.includes('"ai_detail": ai_detail'),
  "Discord connector status API must report the same live Agent Runtime readiness used by the bot",
);
assert(
  discordProvider.includes("discord_ai_runtime_status()") && discordProvider.includes('"ai_enabled": ai_enabled'),
  "Discord status capability must report live Agent Runtime readiness",
);
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
  "ChannelService.handle",
  "model_runtime",
  "secure_runtime",
  "packages.agents",
]) {
  assert(!discordBot.includes(forbidden), `Discord bot leaked a parallel AI/runtime dependency: ${forbidden}`);
  assert(!discordLifecycle.includes(forbidden), `Discord lifecycle leaked a parallel AI/runtime dependency: ${forbidden}`);
}
assert(
  discordBot.includes("Runtime1Agent") &&
  discordBot.includes("resolve_execution_context") &&
  discordBot.includes("resolve_personal_execution_context") &&
  discordBot.includes("build_workspace_runtime") &&
  discordBot.includes("facade.request_runtime(db, context=context)") &&
  discordBot.includes("kernel=kernel"),
  "Discord AI ingress must resolve scoped authority and acquire the governed Kernel before Runtime1 execution",
);
assert(
  discordBot.includes("discord_ai_runtime_status()") &&
  discordRuntimeStatus.includes("AgentRuntimeSettings.from_environment().enabled") &&
  discordRuntimeStatus.includes("InferenceRoute.from_environment()"),
  "Discord bot and connector surfaces must share one deployment-aware Agent Runtime readiness check",
);

console.log("Workspace tools, universal human control surface, and deterministic integration workbench contracts passed.");
