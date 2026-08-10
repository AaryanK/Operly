import asyncio
import json

import pytest

from packages.capability_sandbox.benchmarks import BENCHMARKS, RICH_WORKSPACE
from packages.capability_sandbox.target_resolution import (
    CapabilityPlacement,
    PlacementResolutionError,
    WorkspaceSnapshot,
    resolve_capability_placement,
    validate_placement,
)


class FakeModel:
    def __init__(self, payload):
        self.payload = payload
        self.messages = None

    async def chat(self, messages, tools=None):
        self.messages = messages
        return {"role": "assistant", "content": json.dumps(self.payload)}


def placement(**overrides):
    data = {
        "requested_capability": "track inventory",
        "likely_consumers": ["workspace owner"],
        "disposition": "clarify",
        "target_resource_ids": [],
        "human_surface": "unknown",
        "persistent_state_needs": ["inventory state survives restarts"],
        "external_dependencies": [],
        "machine_operations": ["look up current quantity", "find inventory history"],
        "events": [],
        "new_artifacts": [],
        "alternatives": [
            {
                "id": "existing-site",
                "action": "modify_existing",
                "target_resource_ids": ["website-main"],
                "human_surface": "required",
                "description": "Add inventory to the existing website",
                "architecture_impact": "Changes existing website source and adds durable inventory state",
                "materially_different": True,
            },
            {
                "id": "internal-tool",
                "action": "modify_existing",
                "target_resource_ids": ["operations-app"],
                "human_surface": "required",
                "description": "Add inventory to the existing employee application",
                "architecture_impact": "Changes internal application source and keeps the surface private",
                "materially_different": True,
            },
            {
                "id": "new-service",
                "action": "create_new",
                "target_resource_ids": [],
                "human_surface": "optional",
                "description": "Create an independent inventory capability",
                "architecture_impact": "Creates a separate runtime rather than changing either existing application",
                "materially_different": True,
            },
        ],
        "explicit_placement_evidence": [],
        "clarification_questions": ["Where do you want to use inventory tracking: your website, your internal operations app, or as a separate tool?"],
        "reasoning_summary": "The capability is clear but its delivery target is not.",
        "confidence": 0.92,
    }
    data.update(overrides)
    return CapabilityPlacement.model_validate(data)


def test_ambiguous_capability_must_not_silently_become_standalone_app():
    candidate = placement(
        disposition="create_new",
        human_surface="required",
        clarification_questions=[],
        alternatives=placement().alternatives,
    )
    with pytest.raises(PlacementResolutionError, match="materially different delivery targets"):
        validate_placement("I need inventory tracking for my products.", RICH_WORKSPACE, candidate)


def test_ambiguous_capability_can_ask_one_high_value_question():
    candidate = placement()
    resolved = validate_placement("I need inventory tracking for my products.", RICH_WORKSPACE, candidate)
    assert resolved.disposition == "clarify"
    assert len(resolved.clarification_questions) == 1


def test_explicit_existing_website_evidence_allows_modification():
    candidate = placement(
        requested_capability="shipping calculator",
        disposition="modify_existing",
        target_resource_ids=["website-main"],
        human_surface="required",
        alternatives=[
            {
                "id": "site",
                "action": "modify_existing",
                "target_resource_ids": ["website-main"],
                "human_surface": "required",
                "description": "Put the calculator on the current site",
                "architecture_impact": "Modify current website source",
                "materially_different": True,
            },
            {
                "id": "new",
                "action": "create_new",
                "target_resource_ids": [],
                "human_surface": "required",
                "description": "Create a separate calculator application",
                "architecture_impact": "New independent runtime",
                "materially_different": True,
            },
        ],
        explicit_placement_evidence=["my existing website"],
        clarification_questions=[],
    )
    resolved = validate_placement(
        "Add a shipping calculator to my existing website.",
        RICH_WORKSPACE,
        candidate,
    )
    assert resolved.target_resource_ids == ["website-main"]


def test_model_cannot_invent_existing_target():
    candidate = placement(
        disposition="modify_existing",
        target_resource_ids=["website-that-does-not-exist"],
        alternatives=[],
        explicit_placement_evidence=["my website"],
        clarification_questions=[],
    )
    with pytest.raises(PlacementResolutionError, match="unknown workspace resources"):
        validate_placement("Add this to my website.", RICH_WORKSPACE, candidate)


def test_explicit_placement_evidence_must_come_from_user_text():
    candidate = placement(
        disposition="create_new",
        target_resource_ids=[],
        alternatives=[],
        explicit_placement_evidence=["standalone application"],
        clarification_questions=[],
    )
    with pytest.raises(PlacementResolutionError, match="copied from the user request"):
        validate_placement("I need inventory tracking.", WorkspaceSnapshot(), candidate)


def test_resolver_uses_model_semantics_but_deterministic_validation():
    payload = placement().model_dump(mode="json")
    client = FakeModel(payload)
    result = asyncio.run(
        resolve_capability_placement(
            "I need inventory tracking for my products.",
            RICH_WORKSPACE,
            client=client,
        )
    )
    assert result.disposition == "clarify"
    assert "workspace" in client.messages[-1]["content"]


def test_benchmark_covers_arbitrary_create_modify_compose_and_clarify_cases():
    assert len(BENCHMARKS) == 10
    assert len({case.id for case in BENCHMARKS}) == 10
    assert {case.expected_disposition for case in BENCHMARKS} == {
        "create_new",
        "modify_existing",
        "compose_existing",
        "clarify",
    }
    assert any(case.expected_human_surface == "not_required" for case in BENCHMARKS)
    assert any(case.expected_human_surface == "required" for case in BENCHMARKS)
