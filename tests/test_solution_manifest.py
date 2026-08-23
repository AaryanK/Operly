from packages.solutions.manifest import derive_solution_manifest


def ids(manifest):
    return manifest.capability_ids


def test_static_marketing_presence_stays_presentation_only():
    manifest = derive_solution_manifest(
        "Acme Landing Page",
        "Create a public marketing landing page with an about section and SEO copy.",
    )
    assert manifest.compatibility_runtime == "studio"
    assert "ui.public_web" in ids(manifest)
    assert "server.http_api" not in ids(manifest)
    assert manifest.stateful is False


def test_haircut_booking_is_decomposed_into_stateful_primitives():
    manifest = derive_solution_manifest(
        "Haircut appointments",
        "Customers book haircut appointments from available time slots and staff manage bookings.",
    )
    required = {
        "ui.public_web",
        "ui.workspace_dashboard",
        "scheduler.time_slots",
        "workflow.state_machine",
        "data.relational",
        "server.http_api",
        "auth.sessions",
        "auth.roles",
    }
    assert required.issubset(ids(manifest))
    assert manifest.compatibility_runtime == "managed_app"


def test_qr_login_requires_token_exchange_and_sessions():
    manifest = derive_solution_manifest(
        "QR login",
        "Let users scan a QR code to login securely to their account.",
    )
    assert {"tokens.qr", "auth.sessions", "server.http_api"}.issubset(ids(manifest))
    assert manifest.compatibility_runtime == "managed_app"


def test_restaurant_pickup_notification_combines_workflow_jobs_and_notifications():
    manifest = derive_solution_manifest(
        "Restaurant pickup",
        "Customers place pickup orders, kitchen staff mark them ready, and notify the customer by SMS when ready.",
    )
    required = {
        "ui.public_web",
        "ui.workspace_dashboard",
        "workflow.state_machine",
        "data.relational",
        "server.http_api",
        "notifications.outbound",
        "jobs.background",
        "auth.sessions",
        "auth.roles",
    }
    assert required.issubset(ids(manifest))


def test_dependencies_are_explicit_and_closed():
    manifest = derive_solution_manifest(
        "Payments",
        "Build an app that accepts payments and stores transaction records.",
    )
    assert {"payments.transactions", "data.relational", "server.http_api"}.issubset(ids(manifest))
    edges = set(manifest.dependency_edges)
    assert ("payments.transactions", "data.relational") in edges
    assert ("payments.transactions", "server.http_api") in edges


def test_legacy_stateful_examples_do_not_regress_to_static_sites():
    notebook = derive_solution_manifest("Customer Notebook", "Build a lightweight customer notebook.")
    recorder = derive_solution_manifest(
        "Student Grades Recorder",
        "Record student grades and save them for later review.",
    )
    assert notebook.compatibility_runtime == "managed_app"
    assert recorder.compatibility_runtime == "managed_app"
