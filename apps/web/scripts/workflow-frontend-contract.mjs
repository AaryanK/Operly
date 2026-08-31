import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
const page = read("src/workspace/WorkflowPage.tsx");
const routes = read("src/app/routes.ts");
const shell = read("src/workspace/WorkspaceShell.tsx");
const home = read("src/workspace/WorkspaceHome.tsx");
const allTools = read("src/workspace/CapabilitiesPage.tsx");
const rootApp = read("src/app/App.tsx");
const liveShell = read("src/workspace-lite/WorkspaceSafeApp.tsx");
const entry = read("src/main.tsx");
const liveStyles = read("src/ui/workspace-lite.css");
const surfacePolish = read("src/ui/surface-polish.css");

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
  '/workspace-tools/approvals/${encodeURIComponent',
  'actionTools',
  'Immutable attempt history',
  'Workflow trace',
  'Scheduler health',
  'Advanced condition JSON',
  'Preview next times',
]) {
  if (!page.includes(marker)) failures.push(`WorkflowPage missing frontend boundary: ${marker}`);
}
if (!routes.includes('| "workflows"') || !routes.includes('{ id: "workflows", label: "Workflows"')) failures.push("Workspace route must expose Workflows");
if (!shell.includes('import("./WorkflowPage")') || !shell.includes('case "workflows"')) failures.push("WorkspaceShell must retain WorkflowPage coverage");
if (!home.includes('section: "workflows"') || !home.includes('title: "Automate work"')) failures.push("Workspace Home must make Workflow discoverable");
if (!allTools.includes('api<CapabilityResponse>("/workspace-tools")') || !allTools.includes("no hidden API-only action")) failures.push("All tools must remain the universal capability fallback");

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
  'ADVANCED_WORKSPACE_SECTIONS',
  '<AdvancedWorkspacePage workspace={selected} section={advancedSection} />',
  'className="workspace-lite-advanced"',
]) {
  if (!liveShell.includes(marker)) failures.push(`Live workspace shell missing authenticated advanced-tool boundary: ${marker}`);
}
for (const [section, label] of [["workflows", "Workflows"], ["activity", "Activity"], ["agent-computer", "Computer"], ["connections", "Integrations"], ["capabilities", "All tools"]]) {
  if (!liveShell.includes(`section=\"${section}\"`) || !liveShell.includes(`>${label}</WorkspaceControlLink>`)) failures.push(`Live workspace shell must visibly link to ${label}`);
}
if (!liveShell.includes("event.preventDefault(); navigate(path);")) failures.push("Advanced workspace links must use in-app navigation instead of forcing a second document bootstrap");

for (const stylesheet of ["tokens.css", "app.css", "theme.css", "mobile.css", "surface-polish.css"]) {
  if (!entry.includes(`./ui/${stylesheet}`)) failures.push(`Frontend entry must load ${stylesheet} for advanced workspace surfaces`);
}
if (entry.lastIndexOf('./ui/surface-polish.css') < entry.lastIndexOf('./ui/agent-computer.css')) failures.push("surface-polish.css must load after component-specific advanced workspace styles");
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
console.log(`Workflow frontend contract OK: ${requiredCapabilities.length} Workflow capabilities and supported advanced tools share one authenticated, responsive dark workspace surface.`);
