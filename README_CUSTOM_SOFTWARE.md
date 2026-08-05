# OPERLY custom software generation

Studio now exposes three execution levels in one project surface:

1. Managed manifest applications for bounded CRUD and workflow composition.
2. Rich Studio websites and source-backed field-service products.
3. Agentic application plans routed to a separately isolated code runner.

## Field-service generation

Owners can choose **Generate business software** in Studio and describe a field-service company. Supported brand classifiers currently include bicycle rescue, auto glass, pet transport, locksmith, mobile tire, HVAC, field IT, and commercial cleaning. The generated product includes public intake, customer status, an authenticated dispatcher queue, relational records, an enforced lifecycle, and an artifact graph.

Generated products are listed in Studio. The preview is served from an authenticated, same-origin, frameable route; public and dispatcher routes retain `frame-ancestors 'none'`.

## Visual editing

Selectable artifacts carry stable IDs. The current source-backed field-service graph includes `public.hero`, `public.request-form`, `dispatch.queue`, and `workflow.rescue-lifecycle`. Studio can propose and preview typography, hero-media mode, spacing, and request-layout changes. Apply checks the base version and creates a new project version without rewriting preserved backend artifacts.

## Business architecture planning

The architecture catalog recognizes field service, booking, commerce, membership, inventory, CRM, quotation, marketplace, and approval families. Non-field-service families produce typed architecture and framework plans and are routed to the agentic runner.

## Sandboxed generation boundary

Arbitrary code never runs inside the OPERLY web process. `OPERLY_SANDBOX_RUNNER_URL` and `OPERLY_SANDBOX_RUNNER_TOKEN` must identify a separate isolated runner implementing `POST /v1/generation-jobs`. If either is absent, generation fails closed with `sandbox_not_configured` and returns the proposed architecture, framework, output contract, tests, network policy, and resource limits.

The runner must provide ephemeral filesystems, deny-by-default network access, dependency allowlists and lockfiles, preview-scoped secrets, resource limits, test execution, immutable build digests, and isolated preview URLs. Production deployment remains explicitly disabled in the runner policy.
