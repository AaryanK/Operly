# AI Application Builder milestone evidence

Test date: 2026-08-05

## Before and after

| Use case | Before | After | Evidence |
| --- | --- | --- | --- |
| Vague customer-notebook request | `where` was treated as the selection token `here`; the request could be rejected as ambiguous or sent to Ollama. | The exact request is classified as `customer_notebook` and produces six deterministic operations without Ollama. | Browser acceptance created a medium-risk proposal for CRUD entity, form, data table, navigation, permissions, and customer notebook. `test_whole_word_and_context_ambiguity` and `test_customer_notebook_phrases_are_canonical_and_deterministic` pass. |
| Generated customer form | Controls were disabled and had no trusted submit behavior. | Manifest fields render as enabled controls; submission sends the active version, form ID, declared data, and an idempotency key to the managed-record API. | Browser acceptance submitted `Test Customer`, `555-0100`, and `test@example.com` and displayed `Saved successfully.` |
| Managed data persistence | No generic runtime write path persisted generated-app records. | Validated JSON records are scoped by workspace, application, entity, and application version. | Acceptance record `a5e14524-c5df-4fdb-ad03-37ab4947e6f4` was stored for the customer entity; only safe identifiers appeared in audit metadata. |
| Generated customer table | Tables were static scaffolds and could not show newly entered data. | The trusted same-origin runtime loads a bounded record list and renders only manifest-declared columns with `textContent`. | Browser acceptance displayed one row: `Test Customer`, `555-0100`, `test@example.com`; the row remained after a full refresh. |
| Manifest routes | Routes existed structurally, but the renderer defaulted to the first page. | Direct managed-route opening, route navigation, active route state, and controlled not-found rendering work in preview and runtime. | Browser preview navigated from Overview to Add customer; active runtime opened `/run/customers/new` and navigated to `/run/customers`. |
| Model schema failure | The UI generally surfaced only a generic invalid-plan message. | Initial and repair stages return bounded, sanitized path/category/message details; failures are audited separately and cannot create a version. | `test_model_cannot_inject_code`, `test_invalid_first_response_is_repaired_on_second_attempt`, and `test_atomic_validation_failure_creates_no_version` pass. |
| Tenant and application boundaries | Existing manifest/application isolation existed, but generated records had no complete business-data loop. | Record reads and writes resolve the authenticated tenant and active application, enforce permissions, and reject cross-workspace/application access and stale versions. | `test_record_rejects_unknown_missing_invalid_stale_cross_app_and_workspace`, `test_workspace_isolation`, and record-loop tests pass. |
| Runtime security policy | Inline runtime behavior is blocked by the production CSP. | Trusted behavior is served from `/static/managed-runtime.js`; no model-defined handlers, raw HTML, script, or SQL are executed. | The first browser run exposed the CSP mismatch; after moving the runtime to the same-origin static script, the identical form flow succeeded. |

## Acceptance flow performed

The acceptance test used an isolated temporary SQLite database and did not read, recreate, migrate, or delete `operly.db`.

1. Created blank application `Customer Notebook Acceptance`.
2. Submitted: `make me a little place where I can remember who bought what and how to reach them`.
3. Confirmed no ambiguity response and a deterministic six-operation proposal.
4. Previewed working Overview, Customers, Add customer, Purchases, and Add purchase navigation.
5. Applied atomically; active application advanced from version 1 to version 2.
6. Opened the active Add customer route and submitted the prescribed customer values.
7. Opened Customers and confirmed the new row.
8. Refreshed and confirmed the row remained.
9. Inspected safe audit history: `customer_notebook_proposed`, deterministic `change_set_proposed`, `change_set_applied`, and `managed_record_created` were present without customer values.

Cross-workspace/application denial, stale-version rejection, rollback definition behavior, idempotency, invalid-output non-mutation, and unsafe field rejection were exercised by automated service tests rather than claimed as browser-manual checks.

## Use cases now supported

- Lightweight customer/contact notebook with persistent customer records.
- Purchase log using a safe customer-name text association until managed relationships are formally supported.
- Mobile-capable generated create forms with field-level validation messages.
- Generated data tables with loading, empty, failure, and populated states.
- Multi-page managed applications with direct safe route opening.
- Visually selectable page, route, component, entity, and field identities for future agentic editing.
- Deterministic known-intent generation while retaining guarded Ollama generation and repair for unfamiliar requests.
