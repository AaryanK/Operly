import {
  installRunnerLeaseGuard,
  startRunnerLeaseMaintenance,
} from "./lease-guard.mjs";
import { runFullstackSmoke } from "./fullstack-smoke.mjs";

// Install the durable ownership/lease boundary before server.mjs constructs its
// Postgres pool or runs startup recovery. This keeps a rolling Railway deploy
// from treating another still-live replica's fresh build as an abandoned job.
installRunnerLeaseGuard();
await import("./server.mjs");
startRunnerLeaseMaintenance();

await runFullstackSmoke();
