# DragonZpyder and Operly: evidence-based implementation charter

Date: 2026-09-15. Status: proposed architecture and acceptance specifications; no production change.

The first engineering work should secure DragonZpyder's legacy source, then prove an outcome-evaluation path through Operly's existing personal runtime. Do not begin by creating another planner, capability registry, workflow scheduler or approval engine. Build a thin DragonZpyder personal client against the existing governed backend while establishing a tested extraction boundary. Extract only after both products can exercise the same contracts.

Railway is the preferred host, per the user's instruction during this investigation. The approximately $100/month limit covers both products together, including existing Operly consumption. Keep the existing deployment topology until measurements justify a change.

## A. Baseline and current-state map

Both repositories were freshly cloned from their default branches. No AGENTS.md was found in either checkout. References below describe these exact revisions, not a deployment inspection:

| Repository | Default branch | Inspected commit | Observed state |
| --- | --- | --- | --- |
| [AaryanK/DragonZpyder](https://github.com/AaryanK/DragonZpyder/tree/86a12ef56bf46d214dd02ee4be7549d7056dd4c7) | master | `86a12ef56bf46d214dd02ee4be7549d7056dd4c7` | One 1,042-line tracked legacy program named `DragonZpyder`; no dependency manifest, package, test suite or CI. |
| [AaryanK/Operly](https://github.com/AaryanK/Operly/tree/c01b5a7019de166194e1ff7ebdb5c92058912496) | main | `c01b5a7019de166194e1ff7ebdb5c92058912496` | Python/FastAPI control plane, React/Vite UI, governed Kernel, scoped providers, agent loop and durable workflow infrastructure. |
| [AaryanK/DRAGONZPYDER-CORE](https://github.com/AaryanK/DRAGONZPYDER-CORE/tree/9b860692399dfb6db078e98c62c80a591fc6b298) | main | `9b860692399dfb6db078e98c62c80a591fc6b298` | Checked only to resolve repository ambiguity: README only; referenced `main.py` and `requirements.txt` absent. Claims of functionality are not implementation evidence. |

### DragonZpyder

Microphone/GUI input feeds a monolithic command dispatcher, which invokes SMTP, Gmail/Calendar OAuth, Twilio, Selenium, PyAutoGUI, desktop files, subprocesses, translation, weather and other libraries directly. Python parsing fails at line 346 with an unterminated string literal. Therefore the checked-in program cannot currently start as Python; individual feature functions were not executed. A local `testcode` import is also not supplied in the repository. Historical features are ideas to evaluate, not working integrations to preserve wholesale.

Credential-related assignments include placeholders and non-placeholder credential-like Twilio literals at lines 320–321 and 359–360. Values were not printed or tested. Other credential-bearing configuration needs a full-history scan. Local OAuth tokens use pickle files. Desktop automation lacks an independent permission boundary. See the companion `DragonZpyder/docs/security-baseline.md` for containment work.

### Operly

`Dockerfile` builds the React app, installs `requirements.lock`, drops to UID 10001 and starts `alembic upgrade head` followed by `uvicorn apps.api.main:app`. `railway.toml` configures healthcheck timeout and restart policy. This is consistent with the user's confirmed Railway hosting; current Railway variables, bills, replicas, storage and production health were not inspected.

Mounted Personal and Workspace chat routes are in `apps/api/agent_runtime_router.py`, included by `apps/api/main.py`. Personal requests resolve account authority and call `build_personal_runtime()`. Workspace requests resolve membership authority and build a request-local runtime. `_run` awaits `Runtime1Agent.run` inside the HTTP request, using bounded conversation history and uploaded artifact context.

`packages/agent_runtime/interactive.py` performs objective interpretation, scoped discovery, bounded model/tool cycles and governed execution. Current default limits include six capabilities, ten cycles, four mutations, four discoveries, six observations and a 12 KiB context bundle. `packages/kernel/capability_search.py` contains an index, lexical/structural ranking and a semantic candidate-provider interface. This is more nuanced than either “no semantic discovery” or “a fully learned semantic index.” Measure retrieval failures before adding embeddings.

`packages/kernel/{contracts,policy,runtime,approvals,idempotency}.py` and `packages/security/` implement the trusted capability and authority boundary. Google personal providers, workspace integrations, plugins, MCP and Agent Computer surfaces already exist. Availability depends on configured credentials and runtimes; a registered capability does not prove a working provider.

Separate durable-agent components in `packages/agent_runtime/{store,orchestrator}.py` include persisted runs, leases, approval resume and uncertain-execution states. `packages/workflow/{engine,scheduler,triggers}.py` provides persisted actions/waits, schedules, leases and event dispatch. API lifespan starts the workflow scheduler and event dispatcher. These components do not establish that every interactive chat turn is durably resumable: the mounted chat path still awaits the interactive loop.

`InferenceRoute.from_environment()` selects a configured route among Groq, OpenRouter, Gemini, NVIDIA and optional Ollama. This is provider abstraction, not per-phase cost/quality routing. `AgentBudget` limits steps and mutations; `Runtime1Limits` bounds loops/context. Neither is a monetary ledger or a global monthly spending cap. Inference telemetry records provider usage, but recording usage is not budget enforcement.

The active chat route loads conversation/artifact context. A legacy database `Memory` model exists, but its tenant/guild/channel fields are not the proposed provenance-aware personal memory architecture, and the inspected route does not show automatic durable-memory retrieval. Do not treat a memory table or `ContextKind.MEMORY` as proof of a working memory lifecycle.

### What was tested

After installing the locked dependencies into an isolated Python 3.12 environment, **62 existing tests passed** in 45.098 seconds across these modules:

- `test_agent_runtime_real_world_evaluation`, `test_kernel_capability_search`, `test_agent_runtime_semantic_scope_hardening`;
- `test_agent_runtime_foundation`, `test_agent_runtime_store`, `test_agent_runtime_orchestrator`;
- `test_agent_runtime_races`, `test_agent_runtime_uncertain`, `test_workflow_package`.

Command: `python -m unittest tests.test_agent_runtime_real_world_evaluation tests.test_kernel_capability_search tests.test_agent_runtime_semantic_scope_hardening tests.test_agent_runtime_foundation tests.test_agent_runtime_store tests.test_agent_runtime_orchestrator tests.test_agent_runtime_races tests.test_agent_runtime_uncertain tests.test_workflow_package -q`.

These are focused local regression results, not live model, authenticated connector, browser, full-CI or production acceptance results. The first attempt lacked SQLAlchemy; that environment issue was resolved by installing the lockfile. Datetime deprecation warnings remain. DragonZpyder was inspected statically only.

## B. Gaps and sequencing corrections

| Target | Existing evidence | Next bounded change |
| --- | --- | --- |
| Useful personal product | Legacy source cannot parse; Operly already has Personal runtime | Secure legacy source, establish thin Personal client, verify one real task through existing runtime. |
| Generic contracts | Kernel capabilities/context/results; agent objective/plan/budget types already exist | Write adapters and contract tests; avoid parallel definitions of the same authority. |
| Tool discovery | Scoped registry/index and semantic query grounding | Add outcome-linked retrieval traces; improve ranking only on measured misses. |
| Completion benchmark | Existing real-world evaluation explicitly does not execute capabilities | Retain routing tests; add independent effect verification and outcome scoring. |
| Routing and budgets | Environment-selected provider, usage telemetry, count limits | Shared reservation/settlement ledger around every model/tool call, then route selection. |
| Durable personal memory | Bounded context assembler; legacy memory table | Provenance-aware records and explicit retrieval/write/delete integration. |
| Persistent interactive task | Durable infrastructure exists; chat loop awaits request | Route long work to existing persisted runs/workflows with restart-safe checkpoints. |
| Approval and verification | Kernel policy/idempotency and structured results | Verify external state; test revoked authority, stale approvals and ambiguous provider outcomes. |
| Browser/computer | Agent Computer/sandbox adapter exists | Prove isolation and one observe–act–reobserve scenario; do not advertise arbitrary browser success. |
| Organizational knowledge | Workspace records and integrations | Scoped relational evidence links first; graph database only if SQL cannot serve measured queries. |
| Shared runtime | Useful logic coupled to Operly package initialization/providers | First remote reuse, then dependency-light contracts, then extract one tested seam at a time. |

The brief's immediate Gmail→reply→calendar milestone is a good destination, but too broad as the first acceptance slice. Start with calendar read + a verified email draft, then approved send, then durable reply handling, then calendar mutation. Each step has independent evidence and rollback.

## C. Final repository and package boundaries

Two products, two active product repositories. `DRAGONZPYDER-CORE` should not become a third implementation or brand; leave it untouched until the owner chooses to archive or redirect it.

| Owner | Proposed contents | Forbidden dependency |
| --- | --- | --- |
| DragonZpyder | Personal client/CLI, local execution node, personal UX, personal intent fixtures; eventually `src/dragonzpyder/core` with versioned generic contracts/runtime | Generic core must not import Operly database models, invoice semantics, Google SDKs or organization UI. |
| Operly | Workspace API/UI, RBAC, organization records/modules, existing DB/workflow/Kernel integration, provider adapters, organizational evaluations | Workspace authority must never be inferred from model text or personal account identity alone. |
| Generic package, ultimately owned in DragonZpyder repo | Objective/plan, capability metadata, budget and route contracts, context/memory interfaces, execution and verification ports, event contracts | No hard-coded application provider, hosting vendor, credential or product-specific SQL schema. |

During migration, the actual runtime remains in Operly. DragonZpyder uses a narrow authenticated Personal API; it does not receive workspace-capable tokens. The final shared package is pinned by version in Operly and consumed behind compatibility adapters. Database/workflow persistence may initially remain Operly adapters rather than move into core. Local-only DragonZpyder needs a later local persistence/policy adapter; it is not silently supported by remote reuse.

Map rather than duplicate: `Objective` → current objective contracts; `ExecutionContext` → trusted security context through a narrow protocol; `Capability/CapabilityResult` → Kernel spec/response; `AgentPlan` → existing plan; `TaskBudget` → additive extension of current budgets; `ApprovalRequest` → Kernel approval contract; `AgentEvent` → versioned task event; `MemoryRecord` → new provenance-aware interface; `ModelRoute` → evolution of `InferenceRoute` with secrets represented by broker handles.

## D. Migration and rollback

1. Preserve both current default-branch baselines. Contain legacy credentials without running the program. Do not force-push history or rotate a live integration blindly.
2. Add acceptance specifications and a fixture-based runner alongside existing routing evaluations. Give every result a commit, environment, model route and evidence classification.
3. Introduce a narrow runtime facade inside Operly. Keep existing HTTP request/response behavior and scope resolution. Test old and facade paths against the same contract fixtures before changing callers.
4. Add budget accounting at that shared facade and inference adapter, including interpretation, retries, final responses and verification. Preserve count limits as additional caps.
5. Build DragonZpyder's minimal authenticated personal client against the existing Personal path. One account, one selected fixture task, no new planner. Add server-side validation that a client cannot select workspace authority.
6. Connect durable task submission/status/resume through the existing store/workflow layer. Add additive schema migrations, versioned checkpoints and old-record compatibility. No destructive schema migration in the first phase.
7. Extract dependency-light types and interfaces into the future shared package only after both callers pass identical tests. Eager `packages.agent_runtime` and Kernel imports currently reach database/provider code; remove coupling before publishing a reusable package.
8. Move one generic implementation at a time behind adapters. Pin exact package versions. Run scope/approval/idempotency fixtures in both products and a canary on one internal account/workspace.
9. Roll back by feature flag and previous package version. Existing persisted records remain readable. Cancel/reconcile in-flight uncertain mutations before switching executors; never replay them merely because deployment rolled back.

No dual write of external mutations during shadow testing. Compare proposed decisions and read-only observations; only one implementation owns a real action. Production cutover requires completed CI and controlled live acceptance, not just this report.

## E–F. Intent benchmark deliverables and scoring

Personal: [50 readable acceptance cases](https://github.com/AaryanK/DragonZpyder/blob/codex/intent-completion-baseline/evals/real_world_intents/CASES.md), with machine-readable `cases.json` in the same folder. Organizational: [20 acceptance cases](../../evals/organizational_intents/CASES.md), also with JSON. These are fixture specifications, not implemented fixtures or measured completions. Every baseline is `not_run` with null score. Unknown is never converted to zero or counted as a pass.

| Score | Required evidence |
| --- | --- |
| 0 | An executed attempt cannot help; record why. |
| 1 | Relevant answer, without useful execution guidance or state access. |
| 2 | Useful concrete steps, but no observed connected-state retrieval. |
| 3 | Correct current/personal/organizational state retrieved with provenance. |
| 4 | Verified partial effect; remaining work explicitly identified. |
| 5 | Requested outcome complete according to independent fixture/provider oracle. |
| 6 | Completion independently verified plus appropriate durable outcome or required follow-up. |

Score 6 never authorizes unnecessary memory writes. An ephemeral request may retain only a permitted task audit record. An explanation-only request should use zero tools and can complete successfully without pretending to retrieve state; report intent-appropriate success separately from the action ladder. Approval-waiting is progress, not completed execution. A correct denial is a safety pass and separately an incomplete requested action.

Runner contract: isolated account and workspace fixtures; fixed clock/timezones; typed approval driver; fake provider ledgers; adapter for the real mounted entrypoint; independent verifier; restart/event injector; usage ledger; sanitized report. Run deterministic provider fixtures first, live-model fixtures second, and opt-in authenticated test accounts third. Never label scripted model decisions as measured model competence.

Each run records: case/run ID, baseline commit, route/model, scope, result status/score, evidence references, input/output/cached tokens, estimated and settled costs, call counts, elapsed active time, wait duration, discovered capabilities, retries, approval decisions, memory reads/writes, and verification result. Missing usage is unknown, not zero. Score cannot be supplied authoritatively by the agent itself.

Metrics: completion rate over attempted cases; coverage = attempted/all cases; safety violations; score distribution; verified successful intents per dollar = count of verified completions / all incurred evaluation cost including failures and retries. Also report allocated hosting cost and confidence from repeated trials. Use fixed held-out fixtures and perturbations so more prompts do not merely teach to a keyword list.

## G. Railway-first hosting, model routing and economics

Keep the current Railway API/UI deployment. Prefer one Railway PostgreSQL service for tasks, authority, audit, budgets, memory and relational knowledge links. Reuse an existing database if suitable rather than provisioning another by default. Its real engine/configuration is not known from this session. Keep the existing workflow scheduler initially; give it a dedicated Railway worker from the same image only when latency, restart behavior or measured memory use warrants it. Introduce an explicit process-role entrypoint before splitting: starting another copy of the current API starts its lifespan services too.

Use Railway storage/volumes for durable artifacts where appropriate and test backup restoration. Use Railway cron for bounded maintenance/housekeeping that exits; it is not the task queue or the sole mechanism for responding to approval/mail events. Check provider scheduling semantics before selecting poll cadence. Avoid Redis until measured queue behavior requires it; current durable tables and leases should be evaluated first. Railway-first does not mean the untrusted browser should share the API's credentials or private network.

Railway's public pricing currently lists Hobby at $5 minimum usage including $5 credit, Pro at $20 minimum usage, service memory at $0.00000386/GB-second, CPU at $0.00000772/vCPU-second and service egress at $0.05/GB. Thus 0.75 GB average API+DB memory over 30 days is about $7.50 before CPU/storage, and 0.05 average vCPU about $1.00. These are arithmetic assumptions, not observed resource use; plan minimums are not added twice to included usage. Use the plan appropriate to the account and production/team requirements. [Railway pricing](https://railway.com/pricing).

| Combined monthly allocation | Target USD | Enforcement/assumption |
| --- | ---: | --- |
| Railway API/UI, scheduler, PostgreSQL, storage/egress | 25 | Total includes current Operly; measure actual usage and plan minimum before alpha expansion. |
| Railway isolated browser/worker compute | 10 | On demand, short TTL, concurrency 1 initially; no idle permanent browser fleet. |
| Default inference | 25 | One shared ledger; per-account allowance and daily project cap. |
| Stronger-model escalation | 10 | Off until a vetted priced route and objective failure trigger exist. |
| Search, notifications and optional connector API costs | 5 | Meter paid calls; decline paid work when exhausted. |
| Observability | 0 | Existing structured logs and bounded retained evidence. |
| Reserve including taxes, backup/build variance | 20 | Unallocated; do not spend automatically. |
| **Total target** | **95** | Planning envelope, not a hosting quote or guarantee. |

The original range table can total $120 at its upper bounds; it is not a hard $100 budget. The fixed $95 allocation is internally consistent, but feasible only if measured usage fits. If current Operly hosting exceeds $25, shrink admission/inference allowances or revise the product scope before adding services. Developer wages, user-owned hardware and any existing developer subscriptions are outside this infrastructure envelope; they are not free resources to acquire.

Groq's currently documented `openai/gpt-oss-120b` price is $0.15/M input and $0.60/M output tokens (cached input $0.075/M). A hypothetical task consuming 12,000 uncached input and 2,000 output tokens across all calls costs $0.003. Three fully repeated attempts cost $0.009. This is token arithmetic, not measured task performance; browser/search costs are additional. [Groq model pricing](https://console.groq.com/docs/model/openai/gpt-oss-120b).

Start with Operly's existing provider adapter and benchmark its configured route. Route records should include model ID/version, structured-output/tool reliability, modalities, context limit, measured latency, price snapshot/date, privacy eligibility, availability and health. Route selection: deterministic parsing for exact validated tasks; otherwise cheapest eligible route meeting measured task requirements; escalate only after bounded repair or explicitly high complexity, within the same ledger. A provider outage may trigger another approved provider only when data-egress policy permits it. Local Ollama is optional on a user's existing capable machine, with latency and electricity tradeoffs; no rented GPU and no presumed 120B local capacity.

Before each call, atomically reserve a conservative worst-case token/tool charge and call slot against task, user and monthly budgets. Set output limits; settle from actual usage; count retries; retain reservation if usage is uncertain; reconcile later. Persist deadline and cumulative budget across restarts and child tasks. No model decision can raise a cap. Reject NaN/negative/unknown price inputs, concurrent overspend and cheaper-route privacy violations. At $75 aggregate spend reduce heavy tasks; at $90 stop new paid discretionary work and reserve reconciliation capacity. A cap inside the app cannot guarantee the hosting provider's bill; also configure provider-side spending/resource controls where available and monitor accrued fixed cost.

Free alpha proposal: 10 invited users, initially $1 inference allowance each/month within the shared $25 bucket, with local execution/BYOK as optional expansion. Do not promise unlimited frontier use or add “choose a model” to the normal user journey. A free tier must expose a clear allowance/exhaustion state.

Cloudflare is not required for this milestone. Keep any existing DNS arrangement, but avoid adding a second compute/data platform merely for the plan. [Railway PostgreSQL](https://docs.railway.com/databases/postgresql), [cron](https://docs.railway.com/cron-jobs), and [volumes](https://docs.railway.com/volumes/reference) are the deployment references to verify during implementation.

## H. Security and approval architecture

Authority is server-derived principal + active scope + grants + resource ownership + surface restrictions. Filter capabilities before discovery and authorize again immediately before execution. Re-resolve authority after approval, restart and event delivery. Never accept user/model-supplied scope as authorization. A personal account's workspace membership does not grant its personal agent a workspace execution context.

Use existing Kernel policies, approval records, idempotency and audit. Bind approval to actor, scope, capability version, canonical arguments/hash, recipient/resource, amount where applicable and expiry. Changing recipient, body, price or artifact invalidates approval. Read-only access still requires proper grants. Draft/file writes use scoped policy; consequential send/purchase/delete/publish/install/grant actions need explicit authorization appropriate to risk. Higher-risk operations can remain unsupported.

External content—including email replies, web pages, tool descriptions and repository text—is untrusted data, never new authority. Broker credentials outside model context with short-lived, narrow executor handles. Audit identifiers and hashes rather than raw secrets. Redact free-form provider errors as well as sensitive field names. Scope idempotency to the actor/resource/operation; reconcile ambiguous external outcomes using provider receipts before any retry. Do not claim universal exactly-once delivery for providers without transactional idempotency.

Security gates: wrong-account/workspace reads and writes denied; approval replay/argument substitution denied; role revocation blocks resume; malicious page/email cannot grant permissions; private-network/metadata access blocked; secrets absent from prompts/logs; uncertain sends never blindly repeated. Rotation and full-history cleanup remain separate operational work, not accomplished by this planning PR.

## I. Memory and organizational knowledge

Use four logical categories in the same durable store: task checkpoints, episodic outcomes, explicit semantic preferences/facts, and project/artifact state. Record ID, principal/scope, ACL, category, source URI/event/message ID, content hash, observed/created/updated timestamps, confidence, expiry, sensitivity, supersedes/tombstone and schema version. Keep task checkpoint state authoritative; model summaries are derived views.

Filter by authority before retrieval; then rank relevance/recency and apply the existing bounded ContextAssembler. Prefer explicit user-confirmed facts and current evidence. Conflict does not silently overwrite truth. Write durable semantic memory only on explicit instruction or a narrow user-controlled policy. Support inspect/edit/forget/export, expiry and deletion propagation to summaries/indexes. Document backup retention when forgetting cannot immediately erase immutable backups.

For Operly, begin with scoped SQL entities and evidence-bearing edges: person owns project, decision supersedes decision, deployment built from commit, task blocked by task, paper supports claim, invoice references supplier. Source ACLs constrain traversal and summaries; shared identities must not bridge organizations. Answer “why/owner/changed/blocked/affected” with evidence. Do not introduce a graph database or broad ingestion before these five queries work on current records.

## J. Persistent task and event architecture

Reuse existing leases, workflow events and state tables. Add versioned objective/checkpoint references and an explicit task facade rather than a second queue. Required conceptual states: queued, running, waiting_approval, waiting_event, completed, failed, cancelled, budget_exhausted, execution_uncertain. Current enums do not imply all are integrated in chat.

Persist objective, actor/scope, grants reference, plan version, consumed budget, deadline, step identity, verified observations and wait condition before yielding. Worker lease claims and heartbeats fence stale workers. On resume recheck current authority and remaining budget. Use a transactional outbox/event receipt and deduplication key for database changes; external actions require a provider idempotency key or reconciliation. Dead-letter exhausted events with visible reason and retry controls.

For the Alex scenario, wait identity includes mailbox/account, conversation/thread, counterpart, proposed time and deadline. Matching acceptance is data, not approval for new permissions. Unrelated/ambiguous replies do not schedule. Recheck free/busy before creating the event; if the slot changed, ask or renegotiate. Timezone/DST, cancellation, duplicate reply, expired approval, missing connector and restart after external success all need fixtures. Bound polling and watcher count; prefer provider events when authenticated and available.

## K. Browser/computer execution

First reuse and audit the existing Agent Computer/sandbox adapter. A generic Railway service running arbitrary code is not automatically a safe sandbox; do not assume Docker-in-Docker or a Docker socket is available or acceptable. Evaluate Railway's documented isolated sandbox/VM offering against actual account availability, authenticated API, network isolation, browser image support, TTL and cost before selecting an adapter. If unsuitable, keep this feature gated while an isolated alternative is evaluated; do not move untrusted code into the API process.

Each action observes DOM/accessibility or screenshot state, resolves a bounded target, executes, then re-observes and checks the postcondition. Persist a sanitized evidence reference and artifact hash. Scope cookies to user/site; forbid sharing sessions across tenants. Block control-plane/private-network and metadata access, constrain downloads/files/CPU/memory/runtime, and clean up sessions. Credential entry should use a trusted user/provider flow; page text cannot request secret disclosure.

Before irreversible form submission, show the precise target/fields/price to the applicable approval flow. A click is not a purchase receipt. The browser may stop for CAPTCHA/MFA/user presence. Local nodes later use outbound authenticated connections, revocable short-lived grants and explicit folder/application access; they must not expose an unauthenticated command port.

## L. Prioritized 25-minute engineering sessions

These are bounded work windows, not claims that every item takes exactly 25 minutes. Continue an unfinished slice in the next window and record its actual state.

| Order | Deliverable | Acceptance / stop condition |
| --- | --- | --- |
| 1 | Freeze repository evidence and selected test baseline | Exact commits and executable routes recorded; tested vs unknown explicit. Completed in this investigation. |
| 2 | Charter and 50 personal + 20 organizational cases | Cases have fixture, policy and independent outcome specifications. Completed as specifications, not runnable evaluations. |
| 3 | DragonZpyder containment PR | Remove credential literals from head; secret/config rules; full-history scan without printing secrets; owner revocation checklist. No legacy execution. |
| 4 | Implement first evaluation fixtures | Calendar read and draft-mail sinks; fixed clock and identities; verifier rejects fabricated effects. |
| 5 | Drive existing Personal route through fixtures | DZ-001/DZ-007 or selected draft variant reaches verified result; missing credentials reported correctly. |
| 6 | Shared cost reservation and settlement | Interpretation/retry/respond paths counted; simultaneous reservations cannot exceed cap. |
| 7 | Thin DragonZpyder client and scoped auth | Same Personal fixture succeeds; malicious workspace selector cannot expand access. |
| 8 | Approved send with reconciliation | Exact approval binding, provider receipt and read-back; timeout produces uncertain state rather than resend. |
| 9 | Durable wait/resume integration | Restart plus matching reply produces one continuation; unrelated/replayed event ignored. |
| 10 | Calendar mutation and outcome memory | Conflict recheck, approved event verified once, minimal provenance outcome recorded. |
| 11 | Organizational diagnosis slice | OP-001 evidence-backed CI diagnosis; OP-002 only after separate approved posting. |
| 12 | Acceptance report, migration review and next PRs | Failures recorded; only verified capabilities advertised; no extraction unless parity gate passes. |

If windows 4–5 fail, do not spend windows 7–10 on new scaffolding. Fix that read/draft slice first. Host changes occur only after local fixtures and measured Railway headroom.

## M–N. Milestones and explicit acceptance gates

| Horizon | Deliverable | Acceptance tests |
| --- | --- | --- |
| 1 week | Secured source, reproducible DragonZpyder client, existing Personal runtime facade, first outcome runner, budget ledger | Secret-like literals removed from default head after review and revocation tracked; 10 selected personal fixtures attempted; at least 8 reach their complete outcome with independent evidence; all scope/approval/budget negative cases pass; read/draft plus approved-send path verified; no production claim from mocks. |
| 1 month | Alex invitation lifecycle, useful provenance memory, organizational release diagnosis, Railway cost measurement | DZ-001/002/003 lifecycle passes 20 deterministic restart/duplicate/timezone variants without duplicate external effect; stale approval/role revocation blocked; 25 personal and 10 organization cases attempted, at least 80% complete their supported outcome; five repeated live-model test-account runs per anchor scenario with measured success/failure; memory inspect/edit/forget and scope isolation pass; 7-day measured spend projects below $95 under declared alpha volume. |
| 3 months | Versioned shared package behind both products, local-node/browser pilot, expanded organization evidence links | All 50 personal + 20 organization cases attempted with supported/blocked status; at least 80% of attempted cases verified complete under declared fixtures; all mandatory security tests pass; package parity tests preserve public behavior and rollback reads prior checkpoints; sustained 30-day alpha bill below $100 or admissions reduced; p95 supported immediate-task latency below 60 seconds excluding approval/wait; browser pilot reports real postconditions and unsupported sites honestly. |

Select and freeze the milestone case subsets before implementation. Always publish attempted/all coverage alongside completion so unsupported cases cannot disappear from the denominator. Large travel/purchase/browser goals may remain blocked; state this rather than lowering evidence standards. Real customer data and transactions require a controlled acceptance environment and the proper action authority.

## O. Explicitly defer

No Operly rewrite; no third public Core product; no second generic runtime/queue/policy engine; no dedicated GPUs or foundation-model training; no unlimited free frontier calls; no multi-cloud migration; no default all-tool prompt; no custom graph/vector service before retrieval evidence demands one; no autonomous recursive swarm; no UI overhaul before verified effects; no unrestricted desktop/browser control; no bulk connector rollout driven by popularity.

Subagents are a later product feature: only after the single-agent lifecycle is reliable, with bounded child scopes, budgets, depth and parent reconciliation. This investigation did not use delegated agents.

**First implementation PR:** contain DragonZpyder's exposed credential-like source and establish safe configuration. **First product-function PR:** run a calendar read and verified email draft through Operly's current Personal entrypoint under the new outcome oracle. That ordering addresses the immediate security exposure and proves useful shared behavior without creating another runtime.
