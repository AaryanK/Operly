"""Arbitrary-prompt benchmark for the capability-placement sandbox.

These are evaluation fixtures, not product templates. The production resolver
never branches on these domains or prompt strings.
"""
from __future__ import annotations

from dataclasses import dataclass

from .target_resolution import WorkspaceResource, WorkspaceSnapshot


@dataclass(frozen=True)
class PlacementBenchmark:
    id: str
    prompt: str
    expected_disposition: str
    required_target_ids: tuple[str, ...] = ()
    expected_human_surface: str | None = None


RICH_WORKSPACE = WorkspaceSnapshot(
    resources=[
        WorkspaceResource(
            id="website-main",
            kind="website",
            name="Main business website",
            description="Existing public website with editable pages and a contact form.",
            interfaces=["public web", "source workspace"],
            capabilities=["pages", "contact form"],
        ),
        WorkspaceResource(
            id="operations-app",
            kind="internal application",
            name="Operations",
            description="Existing authenticated internal application for employees.",
            interfaces=["employee web", "source workspace"],
            capabilities=["authenticated pages", "internal records"],
        ),
        WorkspaceResource(
            id="crm-main",
            kind="data capability",
            name="Leads",
            description="Existing lead records available to OPERLY.",
            interfaces=["machine operations"],
            capabilities=["create lead", "read lead", "update lead"],
        ),
        WorkspaceResource(
            id="discord-main",
            kind="integration",
            name="Discord",
            description="Connected Discord workspace.",
            interfaces=["messages", "events"],
            capabilities=["receive message", "send message"],
        ),
        WorkspaceResource(
            id="slack-main",
            kind="integration",
            name="Slack",
            description="Connected Slack workspace.",
            interfaces=["messages"],
            capabilities=["send message"],
        ),
        WorkspaceResource(
            id="docs-main",
            kind="knowledge source",
            name="Business documents",
            description="Documents already available to OPERLY for retrieval.",
            interfaces=["search"],
            capabilities=["retrieve relevant documents"],
        ),
    ]
)


BENCHMARKS = [
    PlacementBenchmark(
        id="existing-site-calculator",
        prompt="Add a shipping cost calculator to my existing website. Customers should enter weight and destination and see the price.",
        expected_disposition="modify_existing",
        required_target_ids=("website-main",),
        expected_human_surface="required",
    ),
    PlacementBenchmark(
        id="ambiguous-inventory",
        prompt="I need inventory tracking for my products.",
        expected_disposition="clarify",
        expected_human_surface="unknown",
    ),
    PlacementBenchmark(
        id="explicit-standalone-invoices",
        prompt="Create a new standalone private invoice tracker for me where I can upload invoices and see who still owes money.",
        expected_disposition="create_new",
        expected_human_surface="required",
    ),
    PlacementBenchmark(
        id="contact-followup-automation",
        prompt="When someone fills out the contact form on my existing website, add them to my existing leads and remind me three days later if I have not replied.",
        expected_disposition="compose_existing",
        required_target_ids=("website-main", "crm-main"),
        expected_human_surface="not_required",
    ),
    PlacementBenchmark(
        id="discord-document-bot",
        prompt="Use my existing Discord connection and my existing business documents so the Discord bot can answer member questions from those documents.",
        expected_disposition="compose_existing",
        required_target_ids=("discord-main", "docs-main"),
        expected_human_surface="not_required",
    ),
    PlacementBenchmark(
        id="existing-site-repair-status",
        prompt="Add a public repair-status page to my existing website so customers can enter a tracking code and see their repair status.",
        expected_disposition="modify_existing",
        required_target_ids=("website-main",),
        expected_human_surface="required",
    ),
    PlacementBenchmark(
        id="new-experiment-tracker",
        prompt="Create a new internal experiment tracker where I can record parameters, attach results, compare runs, and later ask OPERLY which setup performed best.",
        expected_disposition="create_new",
        expected_human_surface="required",
    ),
    PlacementBenchmark(
        id="existing-site-quotes",
        prompt="Add a quote generator to my existing website. I should select services, adjust prices, export a PDF, and later ask OPERLY to find old quotes.",
        expected_disposition="modify_existing",
        required_target_ids=("website-main",),
        expected_human_surface="required",
    ),
    PlacementBenchmark(
        id="operations-lunch-vote",
        prompt="Add a lunch voting page to my existing operations app and post the winning option to my existing Slack connection every Friday.",
        expected_disposition="compose_existing",
        required_target_ids=("operations-app", "slack-main"),
        expected_human_surface="required",
    ),
    PlacementBenchmark(
        id="backend-only-document-api",
        prompt="Create a new backend-only API that accepts a PDF invoice and returns extracted invoice fields. Do not create any frontend or human UI.",
        expected_disposition="create_new",
        expected_human_surface="not_required",
    ),
]


def evaluate_benchmark(case: PlacementBenchmark, placement) -> list[str]:
    failures: list[str] = []
    if placement.disposition != case.expected_disposition:
        failures.append(f"expected disposition {case.expected_disposition}, got {placement.disposition}")
    missing = sorted(set(case.required_target_ids) - set(placement.target_resource_ids))
    if missing:
        failures.append("missing required targets: " + ", ".join(missing))
    if case.expected_human_surface and placement.human_surface != case.expected_human_surface:
        failures.append(f"expected human surface {case.expected_human_surface}, got {placement.human_surface}")
    return failures
