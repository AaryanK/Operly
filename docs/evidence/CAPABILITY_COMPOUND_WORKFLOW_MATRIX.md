# Capability Compound Workflow Matrix

**Observed:** 2026-09-04 UTC (2026-09-03 America/Chicago)  
**Repository:** `AaryanK/Operly`  
**Stress PR:** #297  
**Prerequisite merges:** #294 (`12f44783883dcd18174159b4ad61e1f712a55382`) and #295 (`7f06b1ed5313a810fc6f4ec0d4c594eeea0b466d`)

## Purpose

This matrix tests the capability and Workflow substrate that will sit underneath Operly's future assistant/agent runtime. It is intentionally broader than isolated unit tests: capabilities are placed into multi-step, event-triggered Workflow DAGs so trigger dispatch, scope resolution, schema validation, Kernel execution, audit persistence and Workflow lineage are exercised together.

The architectural boundary under test is:

**Semantic event -> Workflow durability -> Kernel authority -> capability provider**

The intended higher-level invariant remains:

**Agent = intelligence. Workflow = durability. Kernel = authority. Semantic Events = triggers.**

## Scope of the catalog

The test derives its catalog at runtime from Operly's current built-in Personal and Workspace runtime registries. It does not maintain a second hand-written list.

Observed catalog size:

| Measure | Observed |
| --- | ---: |
| Workspace capability registrations | 303 |
| Personal capability registrations | 36 |
| Unique capability IDs across both scopes | 306 |
| Workspace capabilities eligible for compound Workflow placement | 283 |
| Personal capabilities eligible for compound Workflow placement | 16 |
| Workspace `workflow.*` management capabilities | 20 |
| Personal `workflow.*` management capabilities | 20 |
| Workspace approval-required capabilities | 75 |
| Personal approval-required capabilities | 11 |

The catalog covers current built-in surfaces including Google Gmail/Calendar, Canva, Discord, Studio, Agent Computer, Workspace controls, Workflow management, and the broad Workspace OS CRUD capability families.

### Important scope limit

This result does **not** claim coverage of arbitrary tenant-installed plugin capabilities. Workspace plugin capabilities are composed dynamically for each tenant; the synthetic Workspace used in this run had no separately installed plugin package. A plugin-installation matrix should be treated as a distinct follow-up if dynamic plugin-contract coverage is required.

## Compound Workflow design

Non-Workflow capabilities are grouped into batches of up to 24 action steps. The steps are not independent one-step workflows: the generated specs use backward-only dependency graphs with branching/joining dependencies and conditional nodes.

Each capability is exercised under four semantic trigger families:

1. exact event match;
2. namespace wildcard match;
3. global wildcard match;
4. namespace wildcard plus a deterministic event-payload condition.

Observed compound execution:

| Measure | Observed |
| --- | ---: |
| Workspace compound Workflows | 48 |
| Personal compound Workflows | 4 |
| Total Workflow runs | 52 |
| Compound capability executions | 1,196 |
| Trigger families per eligible capability | 4 |

All 52 broad compound runs completed successfully in the final matrix run.

## Provider safety model

The stress suite intentionally does **not** send live email, create calendar events, post Discord messages, alter Canva designs, deploy Studio sites, start Railway sandboxes, or mutate real business data.

For the broad compound lane, only the external provider boundary is replaced with a deterministic schema-contract provider. The real Workflow and Kernel substrate remains in place, including:

- Workflow definitions and immutable versions;
- semantic event triggers and durable Workflow runs;
- Personal and Workspace authority resolution;
- Kernel capability lookup and policy evaluation;
- capability input and output schema validation;
- Kernel audit/run persistence;
- Workflow step and attempt lineage;
- event dispatch and scope isolation.

This validates Operly's orchestration/governance contracts, not the availability or correctness of third-party network APIs.

## Workflow-management recursion guard

The catalog includes 20 `workflow.*` management capabilities in each authority scope. Operly intentionally forbids Workflow recursion/self-modification from inside Workflow specs.

The first matrix attempt surfaced this immediately. The test was corrected to preserve—not weaken—the safety invariant.

Observed result:

- 40 scope-specific attempts to embed Workflow-management capabilities recursively were rejected by the Workflow validator as expected.
- Workflow-management capabilities were then exercised directly through the Kernel contract instead of being embedded inside another Workflow.

This is an intentional safety property.

## Real Kernel approval lane

Approval-required capabilities need their original policy contract tested even though the broad compound lane cannot currently resume a Workflow approval safely (see the blocker below).

The suite therefore runs a second lane using the **original** capability contracts and the real Kernel approval lifecycle:

1. Kernel request;
2. `approval_required` decision;
3. durable approval creation;
4. approval decision;
5. approved retry;
6. approval consumption;
7. completed Kernel execution.

Observed results:

| Measure | Observed |
| --- | ---: |
| Direct Kernel capability executions | 114 |
| Real approvals consumed | 86 |
| Total capability executions across both lanes | 1,310 |
| Total Kernel runs | 1,396 |

The Kernel-run count is larger than the capability-execution count because an approval-required operation records the blocked pre-approval Kernel run and the later approved execution.

## Defect found: native Workflow approval resume

The stress matrix found one real orchestration defect, tracked as **#298 — Workflow approval resume loses ORM identity after Kernel rollback**.

Observed failure path:

`WorkflowEngine._action_step -> OperlyKernelRuntime.execute -> approval_required -> Kernel rollback -> Workflow exception handler`

The Kernel rollback correctly expires transactional ORM state. The Workflow exception handler then tries to obtain IDs from the expired `WorkflowStepRun`, `WorkflowStepAttempt`, and `WorkflowRun` ORM objects while reloading them. Under async SQLAlchemy this causes:

`sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here`

The likely fix is to capture the scalar step-run, attempt, and Workflow-run IDs before invoking Kernel, then use those durable scalar identifiers after any rollback.

### How the final matrix handles this blocker

The final green broad compound lane neutralizes **only** `approval_required` on non-Workflow capabilities in the test-only registry so the known approval-resume defect cannot abort unrelated trigger/DAG coverage.

This does not erase approval testing: every original approval-required contract is separately exercised through the real Kernel approval lane described above.

Therefore the green matrix means:

- broad semantic trigger dispatch is green;
- compound non-Workflow DAG execution is green;
- Personal/Workspace authority isolation is exercised;
- input/output schema contracts are exercised;
- Kernel audit/run persistence is exercised;
- original Kernel approval contracts are exercised and consumed;
- Workflow self-recursion remains blocked;
- **native approval-containing Workflow resume is not yet green** and remains blocked by #298.

## Final observed result

The final CI matrix completed successfully in approximately 54 seconds with:

- **306** unique capability IDs represented across current built-in runtime catalogs;
- **52** compound Workflow runs;
- **1,196** compound capability executions;
- **114** direct Kernel capability executions;
- **1,310** total capability executions;
- **1,396** Kernel runs;
- **86** consumed approvals;
- **40** verified Workflow-management recursion rejections.

No live external side effects were produced.

## What this result supports

It is reasonable, based on this test, to say that the merged semantic-event/Workflow/Kernel substrate can drive a large, heterogeneous built-in capability catalog through compound Workflows while preserving scope and schema boundaries.

It is **not** reasonable yet to claim that every production integration is end-to-end validated, or that approval-containing Workflows are production-ready. Third-party provider behavior remains outside this matrix, tenant-installed plugin capability composition requires its own test, and #298 must be fixed before native Workflow approval resume is considered ready.

## Required follow-up

1. Fix #298 by preserving scalar Workflow lineage IDs across Kernel rollback.
2. Remove the test-only compound approval neutralization.
3. Re-run this same matrix with native approval-required Workflow steps and require it to pass.
4. Add a synthetic installed-plugin Workspace to extend the catalog test across dynamic plugin composition.
5. Keep this CI matrix as a regression gate as the agent runtime is introduced above the substrate.
