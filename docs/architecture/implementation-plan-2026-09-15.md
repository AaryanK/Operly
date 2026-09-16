# DragonZpyder + Operly: implementation plan

Date: 2026-09-15. Proposed execution plan, based on the inspected Operly `c01b5a70` and DragonZpyder `86a12ef5` baselines. Re-fetch both default branches before implementation and adapt to changes. Existing architecture charter: [baseline, evidence and decisions](dragonzpyder-operly-2026-09-15.md).

## Goal and first release

Ship DragonZpyder as a personal task-completion product using the same governed execution foundations that Operly uses for organizations. Keep authority, personal data and organizational data separate. Prefer Railway for hosting and reuse existing services where appropriate. The combined infrastructure target is below $100/month, subject to actual consumption and admission limits.

The first release completes: “Find a free afternoon next week, draft an invitation to Alex, send it after approval, and schedule the meeting if Alex accepts.” Build this as separately accepted increments. The second anchor workflow is Operly's “Investigate a failed release and prepare a diagnosis for the engineering team,” followed by separately authorized posting.

Done means correct external state, independent verification, bounded spend, restart-safe progress and truthful status. A plausible final response, registered tool or passing mock test is insufficient proof of live completion.

## Execution rules

- One active implementation slice at a time. Do not start dependent work to avoid finishing a failing slice.
- Each PR states its problem, changed behavior, acceptance evidence, migration implications and rollback.
- Reuse existing Kernel, scope resolution, capability search, providers, workflow engine and durable run infrastructure. Add adapters rather than a second implementation.
- Record fixture-only, live-model and authenticated test-account evidence separately.
- Do not merge production behavior solely on unit-test success. Use staging/test accounts and explicit rollout gates.
- No migrations, new subscriptions, credential rotations or production changes have been performed by this planning work.

## Work packages and dependencies

These are proposed PR-sized packages, not PRs already created. Estimates are focused engineering windows of approximately 25 minutes; integration/provider failures can require more. Waiting for OAuth setup or external review is outside the estimate. Overall expected effort is several weeks for one developer; dates are targets, never permission to omit acceptance gates.

| Package | Repository | Depends on | Windows | Reviewable output |
| --- | --- | --- | ---: | --- |
| P0: reconcile source and deployment baseline | Operly + DragonZpyder | None | 2–3 | Current SHAs, test baseline, Railway inventory, measured cost snapshot |
| P1: contain legacy credentials | DragonZpyder | P0 | 2–4 | Source cleanup, secret/config rules, redacted scan evidence, rotation tracking |
| P2: executable outcome evaluation foundation | Operly | P0 | 4–6 | Deterministic fixtures, effect ledger, oracle and report format |
| P3: calendar read and email draft | Operly | P2 | 4–8 | Real Personal entrypoint completes first two operations under fixtures |
| P4: persistent spending controls | Operly | P2; before live paid evaluation | 5–8 | Call reservation/settlement ledger, task/user/project caps |
| P5: minimal DragonZpyder personal client | DragonZpyder + narrow Operly adapter | P1, P3, P4 | 4–6 | Authenticated client, task display, attachment and error handling |
| P6: approved sending and reconciliation | Operly | P3, P4 | 4–7 | Bound approval, verified send, safe handling of uncertain outcomes |
| P7: durable submission and continuation | Operly | P6 | 6–10 | Persisted task facade and worker integration, status/cancel/resume |
| P8: reply recognition and verified booking | Operly + DragonZpyder display | P7 | 5–8 | Full invitation lifecycle, restart/event tests |
| P9: controlled memory lifecycle | Operly; client controls in DragonZpyder | P7 | 5–8 | Provenance-aware preferences/outcomes and inspect/edit/forget |
| P10: organization release diagnosis | Operly | P2, P4; P7 for long work | 5–8 | Scoped evidence retrieval, diagnosis, separately approved posting |
| P11: Railway alpha rollout | Deployment configuration | P5–P8 | 3–5 | Staged deployment, resource limits, restoration/recovery evidence |
| P12: shared package extraction | Both | Stable P8 and P10 | 8–12 | One versioned dependency, parity and rollback tests |
| P13: browser/local-node pilot | Both | P4, P7, isolation gate | 8–12 | One isolated browser task, then restricted local node |

P9, P12 and P13 are not required to demonstrate a first useful read/draft release. Minimal task outcome persistence is part of P7/P8; full long-term personal memory is separate.

## P0: freeze what is real

Fetch default branches, read any new AGENTS.md, inspect open PRs and retain the prior architecture work. Record current runtime routes, schema migration head, provider configuration names and dependency locks. Re-run affected regression tests, not every test by default.

Inspect Railway through authorized access: project/environment IDs, service roles, deployed source revision, replicas, start commands, database engine and connection path, volume/bucket configuration, health endpoints, existing worker processes, restart behavior, backups and month-to-date/projected usage. Record presence and purpose of secrets, never their values. Verify whether inference is enabled and which route is actually selected.

Acceptance: source and deployed revisions are distinguishable; actual fixed/variable costs are recorded or explicitly unknown; no duplicate scheduler is accidentally introduced. If Railway access is unavailable, proceed with local fixtures and leave deployment facts unknown—do not infer them from Dockerfile.

## P1: secure the legacy repository

Replace non-placeholder credential literals with validated configuration references or remove obsolete integration code from the runnable path. Add ignored credential/token/environment patterns and placeholder-only configuration examples. Run a redacted current-tree and full-history scan, classifying findings instead of exposing values.

Provider credentials require a separate revocation/rotation action through an authorized account. Check whether any deployment still uses them, create replacement configuration when needed, verify service health and revoke the old credential. Until confirmed, report rotation as outstanding. Removing text from Git does not revoke credentials.

Keep the legacy program explicitly non-default; do not simply fix its syntax and expose unrestricted desktop actions. Establish Python packaging and minimal CI only for the new client/package. Avoid copying the old OAuth pickle files into a new process. Full-history rewriting is a separately coordinated operation, not a routine cleanup step.

Acceptance: new startup cannot import or execute the legacy program; no non-placeholder credential literals remain in current runnable code; configuration fails clearly when missing; new package installs reproducibly; source containment and provider revocation have separate statuses.

## P2: make the evaluation specifications executable

Start from the existing 50 personal and 20 organizational acceptance specifications. Freeze five initial fixtures: free/busy retrieval, professor-email draft, invitation draft, wrong-account access and missing connector. Add an invitation-draft variant to the corpus with a stable ID rather than silently changing the existing send case.

Build a test harness around the mounted Personal entrypoint or a narrowly factored service that the entrypoint actually calls. Use disposable database state, a fixed clock, two personal accounts, two workspaces, test contacts and provider fakes. Fake Gmail/Calendar providers write to an independent effect ledger. The verifier inspects that ledger, not model output or the executor's success flag.

Report run ID, source SHA, environment, fixture version, route/model, scope, status, score, evidence, call counts, tokens/cost, latency, retries and policy decisions. Store sanitized output only. Missing evidence produces unknown/incomplete. Record correct refusals as safety passes separately from task completion.

Acceptance: the oracle rejects fabricated message IDs, wrong recipients, timezone errors, duplicate effects and claimed success with no state change. Scripted-model fixture tests and actual-model tests are labelled differently. No external mail or paid call occurs in the deterministic fixture suite.

## P3: implement the first useful operations

Use the existing objective interpreter and capability discovery. Inspect current personal Google contracts/providers and fix only the gaps revealed by the fixtures. Add no provider-specific logic to the planner.

Calendar: translate “next week” using a fixed reference time and the user's confirmed timezone; resolve the selected calendar; obtain free/busy; choose an interval with explicit duration and afternoon constraints. Missing calendar, timezone or duration is resolved from trusted context or requested from the user when necessary.

Draft: resolve Alex to a verified contact; if ambiguous, ask. Build the invitation from the selected interval and save a provider draft through the governed runtime. Retrieve the saved draft and compare recipient, subject and body to the proposed content. Do not send while testing the draft-only slice.

Acceptance: correct time window, no overlap, correct account/calendar, unique resolved recipient and retrievable draft. A read failure yields a truthful blocker. Prompt injection in retrieved content cannot change permissions. No send capability is invoked by the draft-only request.

## P4: spending controls before paid alpha

Extend the existing inference route and budget interfaces rather than creating an independent router. Add integer micro-dollar accounting or Decimal currency with a versioned price snapshot. Scope every spend to task, account/workspace and project/month. Charge interpretation, planning, retries, response generation, verification and paid tools.

Atomically reserve conservative maximum cost before dispatch; enforce output caps; settle usage after response. Preserve uncertain reservations and reconcile them. Persist cumulative spend across resumes. Provider errors and retries must consume the correct call budget. Unknown prices fail closed for paid routes. A model cannot raise limits or select an unapproved endpoint.

Initial task policies: approximately $0.01 for small retrieval/draft tasks and $0.05 for approved composite tasks, with bounded model/tool counts and active runtime. These are configurable ceilings; empirical task traces determine revisions. Do not promise success at those ceilings.

Acceptance: simultaneous requests cannot over-reserve; restarting does not reset spend; malformed/negative/NaN prices rejected; every inference phase counted; exhaustions return usable progress and a clear stop reason; missing usage never becomes zero cost. Retain existing step/mutation limits as independent constraints.

After usage evidence exists, add deterministic cheapest-eligible route selection and bounded escalation. Availability, privacy, model capability and measured reliability constrain price optimization. Start with the configured provider instead of adding five providers at once.

## P5: minimal personal experience

Create the smallest DragonZpyder client that supports sign-in, task submission, result/progress display, clarification and approval interactions. Start with a developer CLI if faster; choose a thin web surface when it improves real test-user usability. The public product remains DragonZpyder.

Use existing authentication mechanisms or a narrow server-supported flow; never distribute a shared backend administrator key. Authenticate the account server-side and issue only Personal authority. Document token revocation and client logout. Avoid a new cross-product session-sharing scheme without a security review.

The interface shows the task, proposed action, actual result, blocker and remaining wait. Model names and infrastructure choices remain internal. Do not label mock/stub results as successful actions.

Acceptance: a real client can complete the fixture-backed read/draft flow; another account cannot fetch the task or artifacts; supplied workspace IDs cannot expand Personal authority; expired sessions fail cleanly. A request ID prevents browser retries from creating unintended duplicate task submissions.

## P6: approved send and uncertain outcomes

Reuse Kernel approval and idempotency contracts. Bind approval to account, recipient, canonical content, capability, scope and expiry. Re-resolve authorization before sending. Edits invalidate approval. Show an exact draft for review and retain the final approved hash.

Persist send intent and logical step identity before the external action. Verify provider response and, where possible, read the sent message back. For timeout/disconnect after a possible send, enter uncertain status and search/reconcile using provider-supported identity; do not blindly retry. Exactly-once effects depend on provider facilities, not a promise made by the agent.

Acceptance: rejected approval sends nothing; changed recipient/body invalidates approval; replay does not send twice; expired/revoked authority blocks execution; uncertain delivery is visible and recoverable. Use synthetic recipients and a test mailbox for live acceptance.

## P7–P8: turn the interaction into a persistent task

Factor task submission/status/cancel/resume around existing store/orchestrator/workflow components. Proposed task API operations are conceptual until reconciled with existing routes; do not invent a second job service merely to match a naming convention.

Persist objective, source conversation, actor/scope, plan/checkpoint version, grants reference, budget/deadline, current step, verified observations and event-wait predicate. Commit task submission before responding. The worker owns execution through leases and fencing. On resume, reconstruct trusted authority and remaining budget from current state.

For Alex, match acceptance to the account, mailbox/thread, sender, invitation and proposed slot. Unrelated messages and ambiguous responses do not trigger booking. Authenticate inbound events, deduplicate them and bound polling where webhooks are unavailable. Wait deadlines and cancellation stop future processing. Recheck calendar availability immediately before creating the meeting.

Create the event under a valid approval or explicit standing policy. Read it back and verify times, timezone, attendees and uniqueness. Persist an appropriate task outcome and show the actual event link.

Acceptance matrix:

| Injected condition | Expected behavior |
| --- | --- |
| Restart before send | Resume from persisted intent; no effect lost or duplicated |
| Crash after possible send | Reconcile uncertain send before proceeding |
| Duplicate/replayed reply | One logical continuation |
| Unrelated or spoofed reply | No calendar mutation |
| Slot becomes occupied | Clarify/renegotiate; no conflicting booking |
| Approval expires or permission revoked | Pause/deny before effect |
| Cancel during wait | Pending delivery cannot resume the cancelled task |
| Worker lease expires | Stale worker cannot commit a competing continuation |
| Budget exhausted after restart | Return progress and stop new paid calls |
| Calendar provider unavailable | Preserve state and bounded retry policy; never claim created event |

## P9: useful memory, with user control

Keep operational checkpoints separate from semantic preferences. Add provenance-aware records only after checking existing schemas and consumers. Required fields: owner/scope, category, source, timestamps, confidence, sensitivity, expiry, supersession and deletion state.

Start with explicit preferences such as meeting times, plus references to verified project outcomes. Authority filtering precedes relevance ranking; the existing context assembler bounds prompt contribution. Add inspect/edit/forget/export and invalidate derived summaries/index entries on deletion. Disclose backup retention limits where applicable.

Acceptance: a confirmed preference improves a later task after restart; contradictory newer evidence is handled explicitly; another person/workspace cannot retrieve it; forgetting removes it from future retrieval; no automatic permanent memory write for every chat message.

## P10: prove the organizational specialization

Use a test GitHub/CI project and a test team channel. If the required connector is absent, implement the narrow read adapter justified by this scenario. Retrieve failed job/log, implicated change, related discussion and project ownership within the current workspace.

Produce a diagnosis with links and a distinction between observed cause and hypothesis. Save a draft before posting. Resolve the correct audience and obtain the required approval/standing-policy authorization for posting or issue creation. Verify the resulting message/issue.

Acceptance: wrong-workspace material excluded; failed build linked to correct revision; missing logs yield uncertainty; duplicate existing issue detected; unauthorized posting blocked; posted content matches approval. Run the same budget/effect-verification contracts used for the personal workflow.

## P11: Railway rollout plan

### Desired topology

Keep the existing API/UI service, existing database where suitable, and existing scheduler initially. Introduce a worker using the same image only after an explicit process-role switch separates API startup, scheduler ownership and worker execution. Use the current durable database/leases before considering another queue.

Prefer Railway PostgreSQL for new shared durable state if the existing store is unsuitable, but any database conversion is separately planned and tested. Use private database connectivity and scoped secrets. User artifacts need durable storage and tested restore. Repository configuration does not prove that a volume or backup is actually enabled.

Railway cron is appropriate for bounded housekeeping; event/approval continuation must not rely only on a coarse cron tick. Untrusted browser/code workers must have separate credentials and proven isolation, even if hosted on Railway.

### Deployment sequence

1. Capture current deployed revision and baseline health, take/verify backup, and measure usage.
2. Provision or reuse a staging environment with synthetic accounts and minimal resources.
3. Apply additive schema migration and verify old records remain readable.
4. Deploy with new task execution disabled; verify health, auth and existing workspace behavior.
5. Enable for one internal Personal account, then one internal workspace.
6. Run full invitation and release-diagnosis acceptance with test providers/accounts.
7. Observe task failures, duplicate effects, scope violations, usage and worker recovery.
8. Expand to a maximum of ten invited users only while reliability and projected cost remain within bounds.

Rollback: disable new submissions; preserve/cancel/reconcile active tasks; redeploy previous compatible application version; retain additive schema and evidence. Never erase task state to fix a stuck job. Do not retry an uncertain external action during rollback.

### Budget and admission

The existing charter allocates $25 to combined Railway API/database/storage, $10 isolated worker compute, $25 default inference, $10 escalation, $5 search/notifications and $20 reserve: $95 total. These are engineering allocations, not current bills or updated vendor quotes. Reconcile against actual plan minimums, usage, taxes, storage and paid tool costs before provisioning.

Initially allocate up to $1/month inference per invited account with a shared project cap. At $75 aggregate accrued/projected spend, reduce heavy admissions; at $90, stop new discretionary paid work and retain reconciliation capacity. Alert before those levels. Application caps cannot prevent all fixed hosting bills, so configure provider-side controls and remove idle services. Do not consume reserve automatically.

Acceptance: one worker restart and one deployment interruption recover correctly; no duplicate scheduler behavior; task state/artifacts survive; backup restores in staging; measured usage forecast fits the combined limit; no claim that a $95 allocation guarantees a $95 bill.

## P12–P13: shared runtime and browser expansion

After the two anchor workflows are stable, extract dependency-light contracts, then one generic implementation at a time. Remove eager imports of Operly database/providers from generic code first. Keep authority and persistence behind explicit ports. Pin the shared package version in both products and retain compatibility adapters until parity/rollback tests pass.

Eventually `dragonzpyder.core` may own generic objective/plan/context/budget/execution interfaces, while Operly owns organizational policy, records, integrations and workflow adapters. Avoid a third public Core product. Remote reuse remains acceptable until extraction has a concrete benefit.

Browser pilot: one controlled booking form with observe–act–reobserve verification, exact final-submit approval and independent receipt check. Confirm Railway's available isolation/egress controls before choosing its executor. Do not assume a normal service container is sufficient for arbitrary untrusted code. If this gate fails, keep browser support disabled while evaluating an isolated adapter.

Local-node pilot follows browser reliability: outbound authenticated session, narrow file/application grants, short-lived credentials, revocation and no open unauthenticated command port. Add no recursive subagents until single-task recovery and budgets work.

## Scheduling and stop conditions

| Horizon | Target | Evidence required |
| --- | --- | --- |
| First working day | P0/P1 and start P2 | Current baseline; containment change; first executable fixture/oracle |
| Week 1 | P2/P3/P4 and minimal P5 if gates pass | Verified read/draft through actual entrypoint; cumulative budget tests; reproducible client |
| Weeks 2–3 | P6/P7 | Approved send, uncertain-send reconciliation, durable continuation and cancellation |
| Week 4 | P8, controlled Railway alpha; start P10 | Full invite lifecycle with restart/duplicate/timezone variants; actual spend evidence |
| Month 2 | P9/P10 and expand measured intent coverage | Useful controlled memory; organizational diagnosis/posting; task failure report |
| Month 3 | P12 and a bounded P13 pilot if justified | Shared-package parity, supported browser evidence and sustainable alpha costs |

Schedule may shift with OAuth authorization, provider access, schema work or unresolved reliability failures. Reduce scope before weakening permission, verification or budget gates.

Stop expansion if any scope leak, uncontrolled spending, repeated duplicate effect, unrecoverable task loss or secret exposure appears. Fix the defect and rerun the affected acceptance matrix before inviting more users. A missing connector should remain an explicit blocker rather than trigger unrestricted browser fallback.

## Next concrete work session

Start P0 and P1: refresh both repositories, establish current Railway facts if access is available, and prepare the credential-containment change. Then implement the five P2 fixtures and drive calendar read plus saved email draft through the existing Personal path. The session ends with a tested change or a precise recorded blocker—not another set of unconnected runtime modules.
