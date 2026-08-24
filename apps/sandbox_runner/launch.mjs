import {
  installRunnerLeaseGuard,
  startRunnerLeaseMaintenance,
} from "./lease-guard.mjs";
import { installSandboxNetworkGuard } from "./network-guard.mjs";
import { runFullstackSmoke } from "./fullstack-smoke.mjs";

// Install process-global guards before server.mjs imports Railway/Postgres
// execution primitives. Rolling replicas respect durable job leases, and every
// new Sandbox starts with generated uid 10001 denied external egress.
installRunnerLeaseGuard();
installSandboxNetworkGuard();
await import("./server.mjs");
startRunnerLeaseMaintenance();

await runFullstackSmoke();
