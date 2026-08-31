import { access, readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");
const text = (path) => readFile(resolve(repoRoot, path), "utf8");
const exists = async (path) => { try { await access(resolve(repoRoot, path)); return true; } catch { return false; } };
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const [
  page,
  routes,
  shell,
  computerRouter,
  nativeTools,
  sandboxClient,
  referenceRunner,
  studioProvider,
  tools,
  main,
  schema,
] = await Promise.all([
  text("apps/web/src/workspace/AgentComputerPage.tsx"),
  text("apps/web/src/app/routes.ts"),
  text("apps/web/src/workspace/WorkspaceShell.tsx"),
  text("packages/workspace_modules/agent_computer/router.py"),
  text("packages/workspace_modules/agent_computer/native_tools.py"),
  text("packages/workspace_modules/agent_computer/sandbox.py"),
  text("apps/computer_runner/main.py"),
  text("packages/workspace_modules/studio/provider.py"),
  text("packages/workspace_modules/tools/__init__.py"),
  text("apps/api/main.py"),
  text("packages/database/schema.py"),
]);

for (const file of [
  "packages/workspace_modules/agent_computer/__init__.py",
  "packages/workspace_modules/agent_computer/router.py",
  "packages/workspace_modules/agent_computer/native_tools.py",
  "packages/workspace_modules/agent_computer/sandbox.py",
  "packages/workspace_modules/studio/__init__.py",
  "packages/workspace_modules/studio/provider.py",
  "packages/workspace_modules/studio/router.py",
  "packages/database/agent_computer_models.py",
  "alembic/versions/0050_workspace_agent_computer.py",
  "apps/computer_runner/main.py",
  "apps/computer_runner/Dockerfile",
  "apps/computer_runner/requirements.txt",
  "apps/computer_runner/README.md",
  "apps/web/src/ui/agent-computer.css",
]) assert(await exists(file), `Agent Computer implementation is missing ${file}`);

assert(routes.includes('| "agent-computer"'), "Workspace route type must expose Agent Computer");
assert(routes.includes('{ id: "agent-computer", label: "Agent Computer"'), "Workspace navigation must expose Agent Computer");
assert(shell.includes('import("./AgentComputerPage")'), "Workspace shell must lazy-load Agent Computer");
assert(shell.includes('case "agent-computer"'), "Workspace shell must render Agent Computer");

assert(page.includes('api<ComputerStatus>("/agent-computer/status")'), "Agent Computer UI must resolve current runtime authority");
assert(page.includes('"/agent-computer/sessions"'), "Agent Computer UI must create durable sessions");
assert(page.includes('general: "General agent computer"'), "General Computer must be the first-class mode, not only Studio presets");
assert(page.includes('"computer.python.exec"'), "Frontend must expose Python execution");
assert(page.includes('"computer.terminal.exec"'), "Frontend must expose terminal execution");
assert(page.includes("Native tool console"), "Frontend must expose a human-visible native tool console");
assert(page.includes("/tools/${encodeURIComponent(toolId)}/execute"), "Frontend native tools must invoke the real session-scoped endpoint");
assert(page.includes("computer_session_id is injected server-side"), "Frontend must not choose another Computer session through arguments");
assert(page.includes("/workspace-tools/approvals/"), "Business approvals must use the canonical Workspace approval boundary");
assert(page.includes("/resume"), "Approved Workspace presets must resume the same session");

assert(computerRouter.includes('context.can("computer:execute")'), "Computer API must require computer:execute");
assert(computerRouter.includes("build_workspace_runtime"), "Computer must execute through the shared Workspace runtime");
assert(computerRouter.includes('Literal["general", "inspect", "deploy", "rollback", "domain"]'), "Computer sessions must support general-purpose runs");
assert(computerRouter.includes('capability_id="computer.runtime.start"'), "General Computer must start through a native runtime capability");
assert(computerRouter.includes('capability_id.startswith("computer.")'), "Session tool endpoint must be restricted to native computer.* capabilities");
assert(computerRouter.includes('arguments["computer_session_id"] = row.id'), "Computer session identity must be injected from trusted server scope");
assert(computerRouter.includes("request_id=request_id"), "Computer must persist and reuse exact request IDs");
assert(computerRouter.includes("approval_id=approval_id"), "Computer resume must use the exact approval ID");

for (const capability of [
  "computer.runtime.start",
  "computer.runtime.status",
  "computer.runtime.stop",
  "computer.terminal.exec",
  "computer.python.exec",
  "computer.files.list",
  "computer.files.read",
  "computer.files.write",
  "computer.files.search",
  "computer.process.list",
  "computer.process.kill",
  "computer.git.status",
  "computer.git.diff",
  "computer.git.exec",
  "computer.web.fetch",
  "computer.web.download",
  "computer.browser.open",
  "computer.browser.navigate",
  "computer.browser.snapshot",
  "computer.browser.click",
  "computer.browser.type",
  "computer.browser.press",
  "computer.browser.evaluate",
  "computer.browser.screenshot",
  "computer.browser.close",
]) assert(nativeTools.includes(`"${capability}"`), `Agent Computer native tool surface is missing ${capability}`);
assert(nativeTools.includes('permissions=("computer:execute",)'), "Every native Computer capability must require computer:execute");
assert(nativeTools.includes("ComputerRunnerClient"), "Computer provider must cross the runner boundary");
assert(sandboxClient.includes("OPERLY_AGENT_COMPUTER_RUNNER_URL"), "Computer runtime must use an explicit runner endpoint");
assert(!sandboxClient.includes("create_subprocess"), "Operly API-side Computer client must never execute a local process");
assert(!nativeTools.includes("create_subprocess"), "Operly Workspace provider must never execute a local process");
assert(!computerRouter.includes("create_subprocess"), "Agent Computer router must never execute a local process");

assert(referenceRunner.includes('ENVIRONMENT in {"production", "prod"}'), "Unsafe reference runner must refuse production");
assert(referenceRunner.includes("intentionally refuses production"), "Reference runner must clearly fail closed in production");
assert(referenceRunner.includes('["python3", "-c", code]'), "Reference runner must provide actual Python execution");
assert(referenceRunner.includes('["/bin/bash", "-lc", command]'), "Reference runner must provide actual terminal execution");
assert(referenceRunner.includes("async_playwright"), "Reference runner must provide Playwright browser support");
assert(referenceRunner.includes("Private/link-local network targets are blocked"), "Reference runner web tools must block private network targets");

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
  assert(!computerRouter.includes(forbidden), `Agent Computer control plane leaked execution/AI dependency: ${forbidden}`);
  assert(!studioProvider.includes(forbidden), `Studio provider leaked arbitrary execution dependency: ${forbidden}`);
}

assert(tools.includes("computer_native_capabilities"), "Workspace tool composition must include native Computer capabilities");
assert(tools.includes("AgentComputerProvider"), "Workspace provider composition must include the Computer provider");
assert(tools.includes("workspace_studio_capabilities"), "Workspace tool composition must include Studio capabilities");
assert(tools.includes("WorkspaceStudioProvider"), "Workspace provider composition must include Studio provider");
assert(main.includes("agent_computer_router"), "FastAPI must mount the Workspace Agent Computer API");
assert(main.includes("studio_public_router"), "FastAPI must mount verified Studio hosting routes");
assert(schema.includes('ALEMBIC_HEAD = "0050_workspace_agent_computer"'), "Schema head must include Agent Computer migration");
assert(schema.includes("agent_computer_models"), "Agent Computer models must be registered with Base metadata");

console.log("General Agent Computer, native runtime, and Studio deployment contracts passed.");
