# Workflow migration — verified clock handoff, not execution retirement

DragonZpyder's existing `operly_workflow_dispatch.py` is the **only**
Hatchet workflow pulse adapter; do not register the duplicate experimental
`workflow_compat_dispatch.py` task. The canonical task sends a fixed
POST /api/internal/dragonzpyder/workflow-pulse with the exact
X-DragonZpyder-Workflow-Dispatch header and expects
{state,claimed_runs,event_runs_queued}. These represent dispatched work,
**not business completion**.

Operly defaults to `OPERLY_WORKFLOW_DRIVER=local`, continuing its
two-second scheduler and event pollers. Opt-in `hatchet` mode disables
both local loops on startup; the protected route then runs one legacy
reconciliation tick via the existing transactional concurrency scheduler.
All workflow definitions, versions, event cursors, approvals, waits, run
history and Kernel permissions remain in Operly. No data is discarded.

## Readiness / rollback
- Deploy the two repositories with local mode unchanged.
- Back up production DB and count queued, running, waiting, waiting approval,
  and terminal runs. Reconcile owner permissions and scheduled next-fire times.
- Configure matching *independent* service proof
  `OPERLY_HATCHET_WORKFLOW_DISPATCH_SECRET` and
  `DRAGONZPYDER_OPERLY_WORKFLOW_DISPATCH_SECRET` via secret references.
- Use a synthetic environment to prove exact signed dispatch, duplicate
  pulses, event deduplication, scope checks, approval, cancellation, failure,
  network ambiguity and restart recovery. Verify business effects and history.
- Have one authorized operator register the named DragonZpyder Hatchet minute
  cron, once. Do not auto-register at deploy; confirm it is healthy.
- Drain all local poller replicas before setting
  `OPERLY_WORKFLOW_DRIVER=hatchet`; prevent dual-scheduler overlap.
- Minute wakes increase latency compared with today's two-second polling.
  Do not cut over if the measured event/wait delay is unacceptable.
- On rollback stop the cron, drain remote pulses, restore local driver and
  reconcile leases. Never auto-replay uncertain mutations.

## Final retirement
This adapter does not yet move workflow *execution* into DragonZpyder.
Next move versioned DAG state, trigger authority, idempotency and receipts
to one native LangGraph/Deep Agents execution contract using Hatchet as
scheduler. Operly stays a business MCP and permissions authority. Prove
shadow equivalence before retiring the legacy engine. No destructive
schema operation is authorized by this change.
