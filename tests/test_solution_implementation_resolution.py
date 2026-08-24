from packages.solutions.composer import classify_solution_intent
from packages.solutions.service import RuntimeType, SolutionType


def test_camera_qr_clock_routes_to_generated_fullstack():
    decision = classify_solution_intent(
        "Employee Clock in and Clock out system",
        "Employees should be able to clock in using their cameras, by scanning a QR code and clocking out by using another QR code.",
    )

    assert decision.runtime_type == RuntimeType.GENERATED_PROJECT
    assert decision.solution_type == SolutionType.CUSTOM_SOLUTION
    assert decision.implementation_mode == "generated_fullstack"
    assert {
        "device.camera",
        "tokens.qr",
        "data.relational",
        "workflow.state_machine",
        "server.http_api",
    }.issubset(set(decision.required_capabilities))
    assert {"device.camera", "tokens.qr"}.issubset(set(decision.generated_capabilities))


def test_simple_customer_notebook_keeps_managed_fast_path():
    decision = classify_solution_intent(
        "Customer Notebook",
        "Build a lightweight customer notebook.",
    )

    assert decision.runtime_type == RuntimeType.MANAGED_APP
    assert decision.solution_type == SolutionType.BUSINESS_APP
    assert decision.implementation_mode == "managed_declarative"
    assert decision.generated_capabilities == ()


def test_presentation_only_site_keeps_studio_fast_path():
    decision = classify_solution_intent(
        "Acme Landing Page",
        "Create a public marketing landing page with an about section and SEO copy.",
    )

    assert decision.runtime_type == RuntimeType.STUDIO
    assert decision.solution_type == SolutionType.DIGITAL_PRESENCE
    assert decision.implementation_mode == "studio_source"


def test_capabilities_outside_declarative_catalog_route_to_generated_source():
    cases = [
        ("Bookings", "Customers book appointments from available time slots."),
        ("Payments", "Accept customer payments and store transaction records."),
        ("Live board", "Show realtime order status updates to staff."),
        ("Uploads", "Let employees upload photos and store them with each record."),
        ("Calendar sync", "Sync appointments with an external calendar integration."),
    ]

    for name, objective in cases:
        decision = classify_solution_intent(name, objective)
        assert decision.runtime_type == RuntimeType.GENERATED_PROJECT, (name, decision.as_dict())
        assert decision.implementation_mode == "generated_fullstack"
        assert decision.generated_capabilities
