import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
const page = read("src/workspace/WorkflowPage.tsx");
const access = read("src/workspace/AccessPage.tsx");
const routes = read("src/app/routes.ts");
const shell = read("src/workspace/WorkspaceShell.tsx");
const home = read("src/workspace/WorkspaceHome.tsx");
const allTools = read("src/workspace/CapabilitiesPage.tsx");
const rootApp = read("src/app/App.tsx");
const productApp = read("src/app/ProductApp.tsx");
const entry = read("src/main.tsx");
const productShell = read("src/ui/product-shell.css");

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

// The canonical authenticated shell now owns the complete channel journey. Do not
// regress to a second bootstrap just for advanced tools or Runtime 1.0 surfaces.
if (!rootApp.includes('import { ProductApp } from "./ProductApp"')) failures.push("Authenticated /channels routes must use ProductApp");
if (!rootApp.includes('pathname.startsWith("/channels/")') || !rootApp.includes("<ProductApp />")) failures.push("All /channels operating surfaces must stay in ProductApp");
if (!productApp.includes('import("../workspace/WorkspaceShell")') || !productApp.includes('<WorkspaceShell workspace={workspace} section={route.section}')) failures.push("ProductApp must own WorkspaceShell directly");
if (!productApp.includes('import("../account/PersonalHome")') || !productApp.includes('<PersonalHome profile={profile} />')) failures.push("ProductApp must own Personal Operly directly");

for (const marker of [
  'import("./WorkflowPage")',
  'import("./ActivityPage")',
  'import("./AgentComputerPage")',
  'import("./ConnectionsPage")',
  'import("./CapabilitiesPage")',
  'import("./AccessPage")',
  'import("./WorkspaceOperly")',
  'workspaceSections.filter',
  'workspace-nav-search',
  'nav-group-heading',
]) {
  if (!shell.includes(marker)) failures.push(`Canonical workspace shell missing authenticated boundary: ${marker}`);
}
for (const [section, label] of [["operly", "Operly"], ["workflows", "Workflows"], ["activity", "Activity"], ["agent-computer", "Computer"], ["connections", "Integrations"], ["capabilities", "All tools"], ["access", "AI & developer access"]]) {
  if (!routes.includes(`{ id: \"${section}\", label: \"${label}\"`)) failures.push(`Canonical workspace navigation must visibly expose ${label}`);
}
if (!shell.includes("navigate(workspacePath(workspace.id, nextSection))")) failures.push("Workspace navigation must use one in-app route transition");

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

for (const stylesheet of ["tokens.css", "app.css", "theme.css", "mobile.css", "product-shell.css", "surface-polish.css"]) {
  if (!entry.includes(`./ui/${stylesheet}`)) failures.push(`Frontend entry must load ${stylesheet} for canonical workspace surfaces`);
}
for (const marker of [
  ".authenticated-content",
  ".workspace-content-frame",
  ".workspace-nav-search",
  ".mobile-content-open .workspace-content-frame",
  "grid-template-columns: 72px minmax(0, 1fr)",
  "100dvh",
  ":focus-visible",
]) {
  if (!productShell.includes(marker)) failures.push(`Canonical authenticated shell missing responsive contract: ${marker}`);
}

if (failures.length) {
  console.error("Workflow frontend contract failed:\n- " + failures.join("\n- "));
  process.exit(1);
}
console.log(`Workflow frontend contract OK: ${requiredCapabilities.length} Workflow capabilities plus MCP agent governance share one canonical authenticated ProductApp surface.`);
