# Operly Managed Application Builder

The application builder extends authenticated Dashboard Studio. It does not generate source code or deploy a separate codebase. Applications run inside Operly at `/apps/{applicationId}/preview` and `/apps/{applicationId}/run`.

## Manifest, preview, versions, and rollback

Each workspace-scoped application has an immutable sequence of validated manifests containing theme tokens, installed modules, pages, regions, registered components, managed entities, permissions, workflows, integrations, and internal routes. Preview reads a proposed manifest without replacing the active version. Apply validates again and creates a version atomically. Rollback copies historical state into a new version.

## Controlled catalogs

The module catalog declares authentication, dashboard shell, CRUD entity, form, data table, permissions, theme, workflow, audit, and navigation. Authentication reuses Operly identity and membership, PBKDF2-HMAC-SHA256 password hashing, bounded login throttling, rotated signed sessions, HttpOnly/SameSite cookies, production Secure cookies, and login/logout audit events. It creates no duplicate credential store or default production user.

The component catalog defines allowed hierarchy and bounded properties. The renderer emits `data-operly-component-id` identifiers; raw DOM is never authoritative. Canvas selection and layers share those IDs. Scope resolves to application, page, section, component, multi-selection, or blank region. Ambiguous references without selection are rejected.

Add a module in `packages/application_builder/catalog.py` and its validated planner behavior in `service.py`. Add a component by defining hierarchy, schema validation, a safe renderer branch, and tests. Neither extension point may accept model-generated code, SQL, migrations, scripts, handlers, classes, or arbitrary styling.

## Theme, managed data, and workflows

Application tokens define palette, radius, spacing, typography, shadow, and density. Natural-language colors map to approved tokens. Component overrides remain explicit and can be removed to restore inheritance.

Managed fields are restricted to text, long text, integer, decimal, boolean, date, datetime, email, phone, status, relation, and user reference. Records use a generic workspace/application-scoped store; physical schema changes remain reviewed Alembic migrations.

Workflow events and actions are allowlisted: internal navigation, modal opening, managed form submission, approved workflow execution, managed record creation, approval request, and Operly chat. Arbitrary URLs, API calls, scripts, SQL, and shell execution are unsupported.

## Current limitations

Preview isolates the manifest but does not yet provide a disposable preview database or preview-only identity. The deterministic planner currently handles secure login, customer management, global dark-green/cream theme, selected orange overrides, and selected follow-up-task bindings. Unsupported intents are rejected. PostgreSQL revision `0003_application_builder_core` must pass the existing release-scoped backup gate before production deployment.
