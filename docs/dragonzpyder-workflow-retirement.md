# Workflow ownership migration: compatibility handoff (phase 1)

Target: DragonZpyder owns user workflow definitions, durable execution and
Hatchet scheduling; Operly remains the scoped business-capability authority.
Do not delete workflow tables, versions, run/step attempts, approvals, event
cursors, or scheduled work. Do not enable a new dispatcher before providing
a verified replacement.

This change only transfers the wake clock, not actual workflow execution.
Default `OPERLY_WORKFLOW_DISPATCH_DRIVER=local` preserves existing behaviour.
In explicitly configured `dragonzpyder` mode, neither local polling loop
starts; a server-only authenticated wake calls the existing event cursor and
leased run dispatcher once. Existing workflow APIs and Kernel decisions are
unchanged. Each response is dispatch evidence, not completion evidence.
The old executor and its PostgreSQL leases remain required.

## Activation gates (not performed here)
1. Count and back up all definitions/versions/schedules/triggers/runs,
   including pending approvals and uncertain external effects.
2. Test the paired DragonZpyder Hatchet task on an isolated database with
   synthetic Personal and Workspace workflows, event triggers, waits,
   approvals, retries and duplicate wakes. Compare against legacy results.
3. Install a single explicitly authorized named Hatchet minute cron.
   Confirm it is registered and healthy. Remote calls in local mode fail
   closed. The changed polling frequency can delay immediate/event/wait
   workflows by a minute; accept or address this before cutover.
4. Configure the dedicated shared secret via environment references or a
   secret manager, never model context. Set remote mode only after deployment
   readiness, and drain older replicas before calling the new endpoint.
5. Verify live scheduler tick receipts AND existing workflow completion and
   trace counts; do not equate HTTP success with completed workflows.
6. Roll back by stopping the remote cron, draining remote ticks and restoring
   local mode. Retain all DB rows and leases; use normal orphan recovery and
   audit uncertain actions. Do not force-replay mutations.

## Final retirement is a separate milestone
Port versioned plan/trigger semantics and user-scoped durable authority to
DragonZpyder's existing execution graph/harness. Use its native registry and
MCP broker, with live Operly Kernel checks for business effects. Import
definitions in shadow mode, reconcile schedules, pending approvals, history
and action idempotency, then cut over ownership and remove this temporary
compatibility route and legacy run executor. No database destruction is
part of this migration.
