# OPERLY goal and delivery roadmap

## North star

OPERLY is an AI-native business operating system. Its intelligence understands a
company and continuously composes tailored Solutions—software, websites, digital
presence, agents, workflows, connectors, and actions—around that company.

A business owner should be able to describe an outcome, approve the resulting
plan, launch a working Solution, inspect it visually, select any visible part, and
change the underlying product through language as naturally as editing an image.
Connectors should carry events and actions into and out of the operating system
without receiving unrestricted authority over Solution frontends.

## Current position

The repository contains much of the necessary machinery, but it is not yet one
continuous production-ready product.

| Capability | Current state | Remaining gap |
| --- | --- | --- |
| Business workspace | Working tenant-scoped authentication, tasks, approvals, memory, messages, and business records | Stronger company model, onboarding, and continuously maintained company context |
| Solution library | Generated projects, websites, and managed apps now appear together in the UI | One durable Solution identity, API, lifecycle, permissions model, and cross-runtime version history |
| Arbitrary planning | Dynamic capability graph, clarification gate, semantic review, approval, and deterministic acceptance evidence exist | Broader production evaluation, predictable cost/latency, stronger recovery, and measurable plan quality |
| Source generation | Persistent coding-agent tool loop and immutable source bundles exist | More complete tool/runner feedback loop, production reliability, dependency governance, and long-running job resilience |
| Execution | Development subprocess runner and external-runner contract exist | Operated production sandbox, durable queues, resource isolation, observability, preview expiry, and capacity controls |
| Visual inspection | Artifact graphs, stable selections, previews, and visual-change APIs exist for parts of the system | Universal rendered-element-to-source mapping, screenshot/DOM observation, arbitrary source edits, rebuild, undo, and cross-page selection |
| Image-like editing | Selected artifacts can receive bounded visual changes in supported generated products | A consistent canvas for every Solution runtime, direct manipulation, conversational edits, history, comparison, and reliable rollback |
| Connectors | Discord ingestion and business-agent paths exist; other providers are represented | Normalized event bus, provider adapters, credentials UI, subscriptions, retries, idempotency, workflow bindings, and delivery observability |
| Backend agents/workflows | Tools, scheduled jobs, approvals, and workflow primitives exist | Durable orchestration across connectors and Solutions, explicit authority policies, retries, compensation, and owner-readable execution history |
| Controlled frontend updates | The authority boundary is now explicit in API metadata and UI copy | Typed update contracts that let workflows publish records, status, notifications, and content without arbitrary frontend mutation |
| Deployment | Verified previews are the intended endpoint | Reviewed promotion, hosting, domains, secrets, migrations, monitoring, rollback, and safe production deployment of generated Solutions |

## Honest distance from the goal

OPERLY is beyond the prototype-concept stage: the planning graph, coding loop,
source snapshots, approval boundary, multiple runtimes, and visual-editing seeds
are implemented. The current system demonstrates the architecture and several
vertical slices.

It is not yet a dependable general-purpose operating system for arbitrary
businesses. The largest remaining work is integration and operational maturity,
not merely adding more model prompts. A reasonable engineering assessment is:

- **Product concept and core architecture:** approximately 65% established.
- **Coherent end-to-end product experience:** approximately 40% complete.
- **Production-ready arbitrary Solution platform:** approximately 25–30% complete.
- **Full north-star vision, including universal image-like editing and mature
  connectors:** approximately 20–25% complete.

These percentages describe capability maturity, not elapsed calendar time. They
should be replaced with measured release criteria as the platform gains telemetry
and production evaluations.

## Recommended delivery order

### 1. Unified Solution Registry

Introduce a stable Solution resource above the existing website, managed-app, and
generated-project runtimes. It should own identity, type, lifecycle, visibility,
current version, preview/deployment state, capabilities, and runtime reference.
Existing data should be projected into the registry without an immediate risky
table merger.

### 2. Production runner and job control

Operate the isolated runner with durable jobs, streaming events, strict resource
limits, dependency policy, preview lifecycle, cancellation, repair evidence, and
observability. No arbitrary Solution platform can be dependable without this
boundary.

### 3. Universal inspect-and-edit loop

Standardize every Solution preview on stable rendered artifact IDs and observation
contracts. Support selection, DOM/style/geometry inspection, mapping to source,
language edits, rebuild, visual comparison, undo, and immutable history.

### 4. Connector event bus

Normalize provider events into tenant-scoped business events. Add idempotent
subscriptions, workflow triggers, scheduled actions, approval gates, retries,
delivery logs, and typed Solution-update actions. Discord should become the first
adapter on this shared substrate rather than a special architecture.

### 5. Company intelligence layer

Build a durable, reviewable company model from onboarding, business records,
documents, conversations, decisions, and Solution activity. Planning and agents
should consume bounded projections of this model and record why they acted.

### 6. Promotion and operations

Add domains, environment configuration, secret references, database migrations,
deployment approval, health monitoring, rollback, cost controls, and supportable
failure handling for generated Solutions.

## Near-term release criterion

The next meaningful milestone is complete when an owner can:

1. create one Solution from an arbitrary business outcome;
2. approve a capability graph;
3. generate and verify it in the production sandbox;
4. open a stable preview from the unified Solution library;
5. select a rendered element and request a language edit;
6. compare and apply the rebuilt version or undo it;
7. connect Discord to trigger an approved backend workflow;
8. publish a typed status or record update into that Solution; and
9. inspect the complete tenant-scoped audit trail.

Until that journey works reliably in production, OPERLY should be described as an
advanced architectural preview rather than a finished arbitrary business operating
system.
