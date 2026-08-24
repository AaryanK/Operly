import { Sandbox } from "railway";

if (process.env.OPERLY_RUNNER_STARTUP_SMOKE === "1") {
  const environmentId = String(process.env.RAILWAY_ENVIRONMENT_ID || "");
  if (!environmentId) throw new Error("RAILWAY_ENVIRONMENT_ID is required for startup Sandbox smoke");
  if (!process.env.RAILWAY_TOKEN && !process.env.RAILWAY_API_TOKEN) {
    throw new Error("Railway credential is required for startup Sandbox smoke");
  }

  let box = null;
  try {
    box = await Sandbox.create({
      environmentId,
      networkIsolation: "ISOLATED",
      idleTimeoutMinutes: 5,
    });
    const result = await box.exec("printf operly-sandbox-smoke", { timeoutSec: 30 });
    if (result.exitCode !== 0 || result.timedOut || String(result.stdout || "") !== "operly-sandbox-smoke") {
      throw new Error(
        `Sandbox smoke exec failed: exit=${result.exitCode} timedOut=${result.timedOut} output=${String(result.stdout || result.stderr || "").slice(0, 200)}`,
      );
    }
    console.log(`OPERLY_SANDBOX_SMOKE_OK sandbox=${box.id}`);
  } finally {
    if (box) {
      try {
        await box.destroy();
        console.log(`OPERLY_SANDBOX_SMOKE_DESTROYED sandbox=${box.id}`);
      } catch (error) {
        console.error("OPERLY_SANDBOX_SMOKE_DESTROY_FAILED", error?.message || error);
        throw error;
      }
    }
  }
}
