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
if (!shell.includes('import("./WorkflowPage")') || !shell.includes('case "workflows"')) failures.push("WorkspaceShell must render WorkflowPage");
if (!home.includes('section: "workflows"') || !home.includes('title: "Automate work"')) failures.push("Workspace Home must make Workflow discoverable");
if (!allTools.includes('api<CapabilityResponse>("/workspace-tools")') || !allTools.includes("no hidden API-only action")) failures.push("All tools must remain the universal capability fallback");

if (failures.length) {
  console.error("Workflow frontend contract failed:\n- " + failures.join("\n- "));
  process.exit(1);
}
console.log(`Workflow frontend contract OK: ${requiredCapabilities.length} Workflow capabilities have explicit UI coverage.`);
