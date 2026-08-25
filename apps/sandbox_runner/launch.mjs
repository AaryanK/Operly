import {
  installRunnerLeaseGuard,
  startRunnerLeaseMaintenance,
} from "./lease-guard.mjs";
import { installSandboxNetworkGuard } from "./network-guard.mjs";
import { installAgentComputerEndpoint } from "./computer-endpoint.mjs";
import { runFullstackSmoke } from "./fullstack-smoke.mjs";

// Install process-global guards before server.mjs imports Railway/Postgres
// execution primitives. Rolling replicas respect durable job leases, every new
// Sandbox starts with generated uid 10001 denied external egress, and the agent
// computer endpoint reuses the same authenticated Railway Sandbox substrate.
installRunnerLeaseGuard();
installSandboxNetworkGuard();
installAgentComputerEndpoint();
await import("./server.mjs");
startRunnerLeaseMaintenance();

await runFullstackSmoke();
