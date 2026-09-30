import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
const page = read("src/workspace/WorkflowPage.tsx");
const access = read("src/workspace/AccessPage.tsx");
const allTools = read("src/workspace/CapabilitiesPage.tsx");
const rootApp = read("src/app/App.tsx");
const liveShell = read("src/workspace-lite/WorkspaceSafeApp.tsx");
const entry = read("src/main.tsx");
const liveStyles = read("src/ui/workspace.css");
const surfacePolish = read("src/ui/surface-polish.css");
const capabilityRuntime = read("src/runtime/capabilityRuntime.ts");

const requiredCapabilities = [
  "workflow.list",
  "workflow.get",
  "workflow.version.list",
  "workflow.version.get",
  "workflow.create",
  "workflow.update",
  "workflow.enable",
  "workflow.disable",
  "workflow.archive",
  "workflow.run.start",
  "workflow.run.list",
  "workflow.run.get",
  "workflow.run.cancel",
  "workflow.run.retry",
  "workflow.trace",
  "workflow.schedule.preview",
  "workflow.runtime.status",
];

const failures = [];
for (const id of requiredCapabilities) {
  if (!page.includes(`\"${id}\"`)) failures.push(`WorkflowPage must expose ${id}`);
}
for (const marker of [
  'api<ToolCatalog>("/workspace-tools")',
  '"/workspace-tools/approvals?limit=100"',
  'executeCapability(capability, argumentsValue)',
  'approveAndResumeCapability(pendingAction)',
  'decideCapabilityApproval(item.id, approved)',
  'actionTools',
  'Immutable attempt history',
  'Workflow trace',
  'Scheduler health',
  'Advanced condition JSON',
  'Preview next times',
]) {
  if (!page.includes(marker)) failures.push(`WorkflowPage missing frontend boundary: ${marker}`);
}
if (!liveShell.includes('import("../workspace/WorkflowPage")') || !liveShell.includes('case "workflows"')) failures.push("Live Workspace shell must retain WorkflowPage coverage");
if (!liveShell.includes('section="workflows"') || !liveShell.includes(">Workflows</WorkspaceControlLink>")) failures.push("Live Workspace shell must make Workflow discoverable");
if (!allTools.includes('api<CapabilityResponse>("/workspace-tools")') || !allTools.includes("no hidden API-only action")) failures.push("All tools must remain the universal capability fallback");
if (!capabilityRuntime.includes("/workspace-tools/approvals/") || !capabilityRuntime.includes("approval_id: options.approvalId")) failures.push("Shared capability runtime must own exact approval resume semantics");

if (rootApp.includes("ProductApp")) failures.push("Authenticated /channels routes must not hand off to the separate ProductApp bootstrap");
if (!rootApp.includes('pathname.startsWith("/channels/")') || !rootApp.includes("<WorkspaceSafeApp pathname={pathname}")) failures.push("All /channels routes must stay in WorkspaceSafeApp");
if (!liveShell.includes('api<Workspace[]>("/auth/workspaces")')) failures.push("Live advanced tools must bootstrap from the same deterministic workspace session endpoint");
if (liveShell.includes("/personal-agent/me") || liveShell.includes("/personal-agent/workspaces")) failures.push("Live advanced tools must not require the unmounted Personal Agent bootstrap");
for (const marker of [
  'import("../workspace/WorkflowPage")',
  'import("../workspace/ActivityPage")',
  'import("../workspace/AgentComputerPage")',
  'import("../workspace/ConnectionsPage")',
  'import("../workspace/CapabilitiesPage")',
  'import("../workspace/AccessPage")',
  'ADVANCED_WORKSPACE_SECTIONS',
  '<AdvancedWorkspacePage workspace={selected} section={advancedSection} />',
  'className="workspace-lite-advanced"',
]) {
  if (!liveShell.includes(marker)) failures.push(`Live workspace shell missing authenticated advanced-tool boundary: ${marker}`);
}
for (const [section, label] of [["workflows", "Workflows"], ["activity", "Activity"], ["agent-computer", "Computer"], ["connections", "Integrations"], ["capabilities", "All tools"], ["access", "AI & MCP"]]) {
  if (!liveShell.includes(`section=\"${section}\"`) || !liveShell.includes(`>${label}</WorkspaceControlLink>`)) failures.push(`Live workspace shell must visibly link to ${label}`);
}
const workspaceLinkStart = liveShell.indexOf("function WorkspaceControlLink(");
const workspaceLinkEnd = workspaceLinkStart >= 0 ? liveShell.indexOf("\n}\n\nexport function WorkspaceSafeApp", workspaceLinkStart) : -1;
const workspaceLink = workspaceLinkStart >= 0 && workspaceLinkEnd > workspaceLinkStart
  ? liveShell.slice(workspaceLinkStart, workspaceLinkEnd)
  : "";
if (
  !workspaceLink.includes("event.preventDefault()")
  || !workspaceLink.includes("navigate(path)")
  || workspaceLink.includes("go(path)")
  || workspaceLink.includes("window.location.assign")
) failures.push("Advanced workspace links must use in-app navigation instead of forcing a second document bootstrap");

for (const marker of [
  'api<McpCatalog>("/access/mcp-catalog")',
  'api<Client[]>("/access/external-clients")',
  'api<Grant[]>("/access/client-grants")',
  'api<Exposure[]>("/access/tool-exposure")',
  'defaultValue="workspace:*"',
  'All currently authorized Workspace capabilities',
  'Agent capability catalog',
  'This grant cannot add a Workspace permission',
]) {
  if (!access.includes(marker)) failures.push(`AI & MCP frontend missing live governance boundary: ${marker}`);
}
if (access.includes('value="public"')) failures.push("MCP frontend must not offer anonymous/public tool execution");

for (const stylesheet of ["foundation.css", "workspace.css", "mobile.css", "surface-polish.css"]) {
  if (!entry.includes(`./ui/${stylesheet}`)) failures.push(`Frontend entry must load ${stylesheet} for advanced workspace surfaces`);
}
if (entry.lastIndexOf('./ui/surface-polish.css') < entry.lastIndexOf('./ui/workspace.css')) failures.push("surface-polish.css must load after consolidated workspace styles");
for (const marker of [
  "@media (pointer: coarse)",
  ".workspace-lite-advanced .metric-grid",
  ".workspace-lite-advanced .agent-computer-layout",
  ".workspace-lite-topbar-actions > a.active",
  "overflow-x: auto",
]) {
  if (!liveStyles.includes(marker)) failures.push(`Live workspace responsive styles missing: ${marker}`);
}
for (const marker of [
  ".workspace-lite-shell { color-scheme: dark; }",
  ".workspace-lite-advanced .metric-card",
  ".workspace-lite-advanced .data-card",
  ".workspace-lite-advanced input:not([type=\"checkbox\"]):not([type=\"radio\"])",
  ".workspace-lite-advanced .computer-screen",
  ".workspace-lite-advanced .integration-tabs",
  ".workspace-lite-advanced details code",
]) {
  if (!surfacePolish.includes(marker)) failures.push(`Advanced workspace dark-surface contract missing: ${marker}`);
}

if (failures.length) {
  console.error("Workflow frontend contract failed:\n- " + failures.join("\n- "));
  process.exit(1);
}
console.log(`Workflow frontend contract OK: ${requiredCapabilities.length} Workflow capabilities plus MCP agent governance share one authenticated, responsive dark workspace surface.`);
