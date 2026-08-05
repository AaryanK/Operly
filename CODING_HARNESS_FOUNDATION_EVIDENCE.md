# OPERLY Coding Harness foundation

This increment introduces a model-independent lowering path beside the existing architecture-pack path. It does not execute generated code and makes no Codex-parity claim.

## Required output map

1. `RequirementGraph`: `packages/coding_harness/contracts.py`
2. `CapabilityGraph`: `packages/coding_harness/contracts.py`
3. `ToolRegistry`: strict permission, access, risk, approval, rollback, and audit declarations
4–5. Candidate generation and stack recommendation: `packages/coding_harness/engine.py`
6. Versioned implementation plan: `ImplementationPlan`
7. Isolated runner contract: `RunnerJob`; in-process and production execution are literal `False`
8. Iterative state machine: `packages/coding_harness/state_machine.py`
9–10. `TestRepairRecord` and `BrowserObservation`
11. Source-aware `ArtifactGraph` v2
12. `VisualEditImpact`
13–14. `BenchmarkTask` and `BaselineImport`
15. Deterministic weighted loss: `packages/coding_harness/evaluation.py`
16–17. Per-task and aggregate comparison reports
18. Explicit `development`/`held_out` split retained in every report
19. Contract-level security and isolation tests
20. Independent small-business, scientific-workspace, and repository-repair fixtures

## Verification

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_coding_harness.py -q
```

The three fixtures prove intent lowering and contract behavior, not full generated-product outcomes. Real benchmark evidence requires independent executions, external acceptance/security tests, immutable source revisions, and evidence references imported through `BaselineImport`.
