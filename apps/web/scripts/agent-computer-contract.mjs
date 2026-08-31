import { access, readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");
const text = (path) => readFile(resolve(repoRoot, path), "utf8");
const exists = async (path) => { try { await access(resolve(repoRoot, path)); return true; } catch { return false; } };
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const [page, routes, shell, computerRouter, studioProvider, tools, main, schema] = await Promise.all([
  text("apps/web/src/workspace/AgentComputerPage.tsx"),
  text("apps/web/src/app/routes.ts"),
  text("apps/web/src/workspace/WorkspaceShell.tsx"),
  text("packages/workspace_modules/agent_computer/router.py"),
  text("packages/workspace_modules/studio/provider.py"),
  text("packages/workspace_modules/tools/__init__.py"),
  text("apps/api/main.py"),
  text("packages/database/schema.py"),
]);

for (const file of [
  "packages/workspace_modules/agent_computer/__init__.py",
  "packages/workspace_modules/agent_computer/router.py",
  "packages/workspace_modules/studio/__init__.py",
  "packages/workspace_modules/studio/provider.py",
  "packages/workspace_modules/studio/router.py",
  "packages/database/agent_computer_models.py",
  "alembic/versions/0050_workspace_agent_computer.py",
  "apps/web/src/ui/agent-computer.css",
]) assert(await exists(file), `Agent Computer implementation is missing ${file}`);

assert(routes.includes('| "agent-computer"'), "Workspace route type must expose Agent Computer");
assert(routes.includes('{ id: "agent-computer", label: "Agent Computer"'), "Workspace navigation must expose Agent Computer");
assert(shell.includes('import("./AgentComputerPage")'), "Workspace shell must lazy-load Agent Computer");
assert(shell.includes('case "agent-computer"'), "Workspace shell must render Agent Computer");

assert(page.includes('api<ComputerStatus>("/agent-computer/status")'), "Agent Computer UI must resolve current authority");
assert(page.includes('api<{ projects: Project[] }>("/agent-computer/catalog")'), "Agent Computer UI must load its Studio catalog through the governed interface");
assert(page.includes('"/agent-computer/sessions"'), "Agent Computer UI must create durable sessions");
assert(page.includes("/workspace-tools/approvals/"), "Human approval must use the canonical Workspace approval boundary");
assert(page.includes("/resume"), "Approved Computer tasks must resume the same session");
assert(page.includes("AI planner off"), "The current Computer must truthfully show that AI planning is disabled");
assert(page.includes("never receives a shell"), "The UI must state the no-shell boundary");

assert(computerRouter.includes('context.can("computer:execute")'), "Computer API must require computer:execute");
assert(computerRouter.includes("build_workspace_runtime"), "Computer must execute through the shared Workspace runtime");
assert(computerRouter.includes('"deploy": "studio.solution.deploy"'), "Computer deploy action must map to the Studio deployment capability");
assert(computerRouter.includes('"rollback": "studio.solution.rollback"'), "Computer rollback action must map to the Studio rollback capability");
assert(computerRouter.includes('"domain": "studio.solution.domain.request"'), "Computer domain action must map to the Studio domain capability");
assert(computerRouter.includes("request_id=request_id"), "Computer must persist and reuse the exact idempotent request ID");
assert(computerRouter.includes("approval_id=approval_id"), "Computer resume must use the exact approval ID");

for (const capability of [
  "studio.projects.list",
  "studio.project.inspect",
  "studio.solution.status",
  "studio.solution.deploy",
  "studio.solution.rollback",
  "studio.solution.domain.request",
]) assert(studioProvider.includes(`"${capability}"`), `Studio provider is missing ${capability}`);
assert(studioProvider.includes("approval=True"), "Studio mutation capabilities must remain approval gated");
assert(studioProvider.includes('permission="solution:write"'), "Studio deployment mutations must require solution:write");
assert(studioProvider.includes('"dist/index.html"'), "Studio deployer must recognize prebuilt dist output");
assert(studioProvider.includes("has no committed dist/build output"), "Unbuilt application source must fail closed");

for (const forbidden of ["business_brain", "model_runtime", "AgentRuntime", "subprocess", "playwright", "selenium"]) {
  assert(!computerRouter.includes(forbidden), `Agent Computer leaked privileged/AI execution dependency: ${forbidden}`);
  assert(!studioProvider.includes(forbidden), `Studio provider leaked arbitrary execution dependency: ${forbidden}`);
}

assert(tools.includes("workspace_studio_capabilities"), "Workspace tool composition must include Studio capabilities");
assert(tools.includes("WorkspaceStudioProvider"), "Workspace provider composition must include Studio provider");
assert(main.includes("agent_computer_router"), "FastAPI must mount the Workspace Agent Computer API");
assert(main.includes("studio_public_router"), "FastAPI must mount verified Studio hosting routes");
assert(schema.includes('ALEMBIC_HEAD = "0050_workspace_agent_computer"'), "Schema head must include Agent Computer migration");
assert(schema.includes("agent_computer_models"), "Agent Computer models must be registered with Base metadata");

console.log("Agent Computer and Studio deployment contracts passed.");
