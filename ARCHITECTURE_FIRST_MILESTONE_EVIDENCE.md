# Architecture-first milestone evidence

Date: 2026-08-05  
Branch: `main`  
State: uncommitted; not deployed

## Outcome

The local implementation now supports strict plan-first generation, approved-plan binding, fail-closed implementation coverage, quotation and inventory architecture packs, versioned behavioral editing, artifact traceability, a persistent mocked sandbox lifecycle, and public-runtime safety controls. All automated tests pass. Quotation and inventory business loops were exercised through the browser. Binary screenshots remain the sole evidence-export limitation.

## Exact acceptance prompts

1. `Build a travel agency system where customers request trips, agents prepare and revise quotations, managers approve them, and customers review itineraries.`
2. Revision: `Add WhatsApp follow-up`
3. `Build a grocery inventory system with suppliers, stock receiving, low-stock alerts, purchase orders, partial receipts, and manager approval for large orders.`
4. Behavioral quotation edit: `Make the customer quotation price area more prominent, hide margin from customers, and require manager approval before sending.`
5. Behavioral inventory edit: `Turn this into a compact priority board, show the preferred supplier, and require manager approval for purchase orders above 5,000.`

## Plan JSON excerpts

Quotation plan excerpt:

```json
{
  "architecture": "quotation",
  "generationMode": "architecture_pack",
  "design": {"family": "editorial"},
  "roles": ["owner", "manager", "agent", "customer"],
  "workflow": "quotation_draft -> internal_review -> approved_for_sending -> sent_to_customer -> revision_requested|accepted|rejected"
}
```

Inventory plan excerpt:

```json
{
  "architecture": "inventory",
  "generationMode": "architecture_pack",
  "design": {"family": "dashboard_led"},
  "roles": ["owner", "manager", "stock_employee", "purchasing_employee", "administrator"],
  "workflow": "draft -> approved -> ordered -> partially_received -> received -> closed"
}
```

Studio visibly reported 98% architecture-selection confidence for the inventory prompt and required explicit approval before implementation generation.

## Before and after

Before, Studio could generate a field-service-shaped managed application but could not represent quotation revision/approval/customer-decision behavior or inventory receiving and purchase-order state. After, the same plan-first surface selects a domain architecture independently of the visual family, binds generated projects to the approved plan version, blocks incomplete mappings, renders domain-specific public/staff surfaces, and persists the corresponding records and histories.

## Workflow histories observed in the browser

### Quotation

```text
public inquiry submitted (QI-8BC808)
-> quotation_draft, quotation version 1, total 1400.00
-> internal_review
-> approved_for_sending
-> sent_to_customer
-> signed customer quotation opened
-> revision_requested
```

The customer route showed `revision requested` and `Your response was recorded.` after the signed action. The customer representation did not expose internal margin fields.

### Inventory

```text
supplier North Market Supply created
product APL-001 / Honeycrisp Apples created, reorder point 5
location Main Store created
initial movement +10
adjustment movement -7 -> low-stock view showed 3 / 5
purchase order draft, total 20.00, version 1
approved -> ordered, version 3
partial receipt +4 -> partially_received, version 4, 6 remaining
final receipt +6 -> received, version 5
closed, version 6
```

The final visible movement history was `receiving 6`, `receiving 4`, `adjustment -7`, `initial 10`; low-stock attention cleared after receipt.

## API request and response excerpts

Representative requests exercised by the browser UI:

```http
POST /api/inventory/stock-movements
{"productId":"<scoped-id>","locationId":"<scoped-id>","quantity":-7,"movementType":"adjustment"}

POST /api/inventory/purchase-orders/<scoped-id>/receive
{"locationId":"<scoped-id>","receipts":{"<line-id>":4},"expectedVersion":3}
```

```json
{"id":"<scoped-id>","status":"partially_received","version":4}
```

```http
POST /api/public/quotation/customer/<signed-token>/decision
{"status":"revision_requested","expectedVersion":4}
```

```json
{"status":"revision_requested"}
```

Identifiers and signed tokens are intentionally redacted from this durable evidence file.

## Artifact graph excerpts

```json
[
  {"id":"public_inquiry.editorial-hero","route":"/generated/{slug}","entity":"quotation_inquiry"},
  {"id":"inquiry_queue.quotation-editor","route":"/generated/{slug}/manage","entity":"quotation"},
  {"id":"quotation.send-action","api":"/api/quotation/quotations/{id}/transition","workflow":"quotation_lifecycle","permission":"manager"},
  {"id":"customer_quotation.price-sidebar","route":"/quotation/customer/{token}","permission":"signed_customer"},
  {"id":"inventory_dashboard.low-stock-queue","entity":"stock_level","api":"/api/inventory/projects/{id}/low-stock"},
  {"id":"inventory.receive-items","entity":"purchase_order_line","workflow":"purchase_order_lifecycle","permission":"stock_employee"}
]
```

Every graph node carries stable artifact identity plus route, source/runtime component, related entity, API/workflow/permission links, viewport metadata, and dependency edges. Selection therefore maps a visible element back to affected implementation artifacts.

## Plan-to-implementation coverage

Studio showed `Approved plan fully mapped` for both generated architecture packs.

```text
quotation: entities 8, relationships 3, roles 4, workflows 1, pages 4, permissions 8
inventory: entities 8, relationships 3, roles 5, workflows 1, pages 4, permissions 7
```

Generation fails closed if any required plan ID is missing from the implementation graph. Coverage is also available from `GET /api/custom-software/projects/{id}/coverage`.

## Behavioral editing

Behavioral proposals are versioned, scoped to selected artifact IDs, reject stale base versions, record dependency impact, apply atomically, and can be rolled back. Tests confirm the quotation edit adds the manager approval guard and customer-margin permission behavior; the inventory edit adds the 5,000 approval threshold and preferred-supplier relationship. The generated project version advances from 1 to 2 on apply.

## Mocked sandbox lifecycle

Persistent sandbox jobs use validated transitions:

```text
planned -> queued -> submitted -> generating -> installing -> building -> testing -> previewing -> completed
```

Terminal alternatives are `failed`, `cancelled`, and `expired`. Results must match the expected schema, events are audited, and failure/log text redacts bearer tokens and secrets. This is a mocked lifecycle only; it does not execute arbitrary code or deploy production changes.

## Security evidence

Automated checks cover:

- strict schema rejection of duplicate identifiers and script-bearing plan content;
- owner-only plan approval and authenticated management routes;
- tenant/project scoping for quotation and inventory records;
- signed, expiring customer quotation links and tamper rejection;
- optimistic version conflicts and invalid state-transition rejection;
- over-receipt prevention and transactional stock updates;
- SSRF/network target rejection and sandbox output validation;
- public payload-size and per-process rate limits;
- secret redaction in sandbox failures and event logs.

Focused command:

```text
.venv\Scripts\python.exe -m pytest -q tests/test_architecture_first.py tests/test_pack_workflows.py tests/test_migrations.py
28 passed, 82 warnings, 12 subtests passed in 50.45s
```

## Migration evidence

The migration chain is linear through `0009_sandbox_job_lifecycle`:

```text
0006 architecture_first_plans
0007 architecture_pack_runtimes
0008 plan_bound_projects
0009 sandbox_job_lifecycle (head)
```

Migration tests are included in the focused green run above and verify upgrade behavior from baseline to head without relying on production data.

## Full automated suite

```text
.venv\Scripts\python.exe -m pytest -q
144 passed, 5 skipped, 341 warnings, 41 subtests passed in 237.64s
```

The skips are pre-existing conditional tests; there were no failures.

## Browser acceptance

Runtime: disposable local SQLite database and local server on `127.0.0.1`; no production credentials or data.

Routes exercised:

- `/` — owner sign-in and Studio navigation
- Studio plan creation, approval, and implementation generation surface
- `/generated/build-a-travel-agency-system-where-customers-request-trips-agents-prepare-and-r`
- `/generated/build-a-travel-agency-system-where-customers-request-trips-agents-prepare-and-r/manage`
- `/quotation/customer/<signed-token>`
- `/generated/build-a-grocery-inventory-system-with-suppliers-stock-receiving-low-stock-aler/manage`

Browser surface: default in-app desktop viewport; responsive composition was additionally exercised through Studio's `desktop`, `tablet`, and `mobile` preview selectors. Exact intended capture viewports are specified below because the browser interface did not expose its ambient numeric viewport dimensions.

DOM/accessibility excerpts:

```text
heading "A considered itinerary, built around you."
textbox "Your trip"
button "Send travel brief"
heading "revision requested"
paragraph "Your response was recorded."
```

```text
heading "Low-stock priorities"
article "Honeycrisp Apples3 / 5"
article "partially_received · 20.00 · v4"
option "6 remaining"
article "closed · 20.00 · v6"
```

## Screenshot status and deferred capture manifest

Screenshot capture verified in browser session but binary export unavailable in current tool environment.

No PNG files or PNG paths are claimed as present. The environment was checked for repository Playwright hooks, Playwright CLI, Chromium/Chrome/Edge command-line capture, Python Selenium, Python Playwright, Puppeteer, and npm. None was available, and the browser tool did not persist a requested binary path.

| Intended filename | Route | Role | Viewport | Required visible state | Exact reproduction steps |
|---|---|---|---:|---|---|
| `01-quotation-public-desktop.png` | travel generated route | public | 1440×900 | Editorial hero and inquiry form | Open route; scroll to keep hero and start of form visible. |
| `02-quotation-inquiry-confirmation-mobile.png` | travel generated route | public | 390×844 | `Inquiry … received` confirmation | Fill name/email/trip; submit; capture success status. |
| `03-quotation-staff-sent.png` | travel `/manage` | owner/agent | 1440×900 | Version 1, total, `sent to customer`, signed-link action | Sign in; open quotation after manager approval and send. |
| `04-quotation-customer-revision.png` | signed customer route | signed customer | 390×844 | `revision requested` confirmation | Open signed link; choose Request revision; capture confirmation. |
| `05-inventory-low-stock.png` | inventory `/manage` | owner | 1440×900 | Honeycrisp Apples `3 / 5` plus movement history | Create master data; record +10 initial and -7 adjustment. |
| `06-inventory-partial-receipt.png` | inventory `/manage` | owner/stock employee | 1440×900 | `partially received`, version 4, `6 remaining` | Approve/order ten units; receive four. |
| `07-inventory-closed.png` | inventory `/manage` | owner | 1440×900 | Closed version 6 and four movement entries | Receive remaining six; close order. |
| `08-studio-plan-coverage.png` | Studio | owner | 1440×900 | Inventory plan, artifact graph, `Approved plan fully mapped` | Generate exact inventory prompt; approve; generate implementation. |
| `09-studio-visual-edit-mobile.png` | Studio | owner | 390×844 | Selected artifact, behavioral proposal impact and mobile target | Select `inventory_dashboard.low-stock-queue`; use exact behavioral edit prompt; preview proposal. |

Exact unresolved blocker: **Browser screenshots were visually verified but could not be exported as binary PNG files with the available tool interface.**

## Repository and deployment state

- Files are intentionally uncommitted.
- No commit was created.
- No branch was pushed or merged.
- No preview or production deployment was created.
- The browser server and database were disposable local acceptance resources only.

