import { access, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");
const text = (path) => readFile(resolve(repoRoot, path), "utf8");
const exists = async (path) => { try { await access(resolve(repoRoot, path)); return true; } catch { return false; } };
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const [page, router, main, bootstrap, nativeProvider] = await Promise.all([
  text("apps/web/src/workspace/CapabilitiesPage.tsx"),
  text("apps/api/workspace_tools_router.py"),
  text("apps/api/main.py"),
  text("packages/kernel/bootstrap.py"),
  text("packages/kernel/providers.py"),
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
assert(main.includes("app.include_router(workspace_tools_router)"), "FastAPI must mount the Workspace tools API");

for (const file of ["records.py", "controls.py", "business.py", "google.py", "availability.py", "system.py", "__init__.py"]) {
  assert(await exists(`packages/workspace_modules/tools/${file}`), `Workspace tool package is missing ${file}`);
}
for (const legacy of ["workspace_os_provider.py", "workspace_control_provider.py", "workspace_business_provider.py", "workspace_google_provider.py", "provider_availability.py"]) {
  assert(!(await exists(`packages/kernel/${legacy}`)), `Workspace-owned code leaked back into packages/kernel/${legacy}`);
}

assert(bootstrap.includes("from packages.workspace_modules.tools import register_workspace_providers, workspace_capabilities"), "Kernel composition must source Workspace tools from workspace_modules");
assert(!bootstrap.includes("workspace_control_provider"), "Generic Kernel bootstrap must not own Workspace provider modules");
assert(!nativeProvider.includes("_workspace_describe"), "Generic native provider must not implement Workspace domain operations");
assert(!nativeProvider.includes("_workspace_modules"), "Generic native provider must not implement Workspace module operations");

console.log("Workspace tools frontend-to-endpoint contracts passed.");
