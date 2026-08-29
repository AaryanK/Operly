from types import SimpleNamespace

import pytest

from packages.agents.control_plane.contracts import (
    AcceptanceContract,
    ContextCapsule,
    StageGraph,
    StageSpec,
    StageWorkerResult,
)
from packages.agents.control_plane.safe_factory import (
    SafeAgentRuntimeWorker,
    SafeFactoryCapabilityIntentResolver,
    SafeFactoryStageRunner,
    SafeStageContextInjector,
)


def _tool(name):
    return {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object"}},
    }


class FakeRegistry:
    def __init__(self, rows):
        self.rows = list(rows)

    def search(self, scope_id, query, *, authority, limit):
        del scope_id, query, authority
        return self.rows[:limit]

    def availability(self, scope_id, capability_id, *, authority):
        del scope_id, capability_id, authority
        return SimpleNamespace(available=True)


@pytest.mark.asyncio
async def test_capability_resolver_keeps_calendar_reads_in_calendar_family():
    resolver = SafeFactoryCapabilityIntentResolver(
        registry=FakeRegistry(
            [
                {"id": "artifact.read_text"},
                {"id": "crm.get_contact"},
                {"id": "calendar.create_event"},
                {"id": "calendar.list_events"},
            ]
        ),
        scope_id="workspace-1",
        authority={"calendar:read", "calendar:write"},
    )

    selected = await resolver(("Read calendar events",))

    assert selected == ["calendar.list_events"]


@pytest.mark.asyncio
async def test_capability_resolver_does_not_substitute_gmail_write_or_context_search():
    resolver = SafeFactoryCapabilityIntentResolver(
        registry=FakeRegistry(
            [
                {"id": "context.search"},
                {"id": "gmail.create_draft"},
                {"id": "crm.search_contacts"},
                {"id": "gmail.search"},
                {"id": "gmail.read_message"},
            ]
        ),
        scope_id="workspace-1",
        authority={"messaging:read", "messaging:draft"},
    )

    selected = await resolver(("Search Gmail messages by sender and date",))

    assert "gmail.search" in selected
    assert "gmail.read_message" in selected
    assert "gmail.create_draft" not in selected
    assert "context.search" not in selected
    assert "crm.search_contacts" not in selected


@pytest.mark.asyncio
async def test_injector_does_not_search_history_for_runtime_time_inputs():
    searched = []

    async def search(intent, limit):
        searched.append((intent, limit))
        return [{"ref": "workspace_message:wrong", "score": 1.0}]

    async def resolve(intents):
        assert tuple(intents) == ("Read calendar events",)
        return ["calendar.list_events"]

    injector = SafeStageContextInjector(
        search=search,
        resolve_capabilities=resolve,
    )
    capsule = await injector.build(
        StageSpec(
            "stage-1",
            "Retrieve tomorrow's calendar events",
            context_intents=("Get tomorrow's date", "Filter events by time window"),
            capability_intents=("Read calendar events",),
        )
    )

    assert searched == []
    assert capsule.context_refs == ()
    assert capsule.capability_ids == ("calendar.list_events",)


@pytest.mark.asyncio
async def test_dependent_stage_receives_validated_result_without_history_rediscovery():
    searched = []
    validated = {
        "stage-1": StageWorkerResult(
            status="completed",
            strategy="calendar.list_events",
            summary="09:30 Product review with sam@example.com",
            evidence={"verified": True, "count": 1},
            evidence_refs=("action:calendar-read",),
        )
    }

    async def search(intent, limit):
        searched.append((intent, limit))
        return [{"ref": "workspace_message:old-meeting", "score": 1.0}]

    injector = SafeStageContextInjector(
        search=search,
        validated_results=validated,
    )
    capsule = await injector.build(
        StageSpec(
            "stage-2",
            "Analyze meeting titles and attendees",
            dependencies=("stage-1",),
            input_refs=("stage-1",),
            context_intents=(
                "Identify attendees for each meeting",
                "Determine preparation needs from title and attendees",
            ),
        )
    )

    facts = dict(capsule.facts)
    assert searched == []
    assert "dependency_results" in facts
    assert facts["dependency_results"]["stage-1"]["summary"].startswith("09:30 Product review")


@pytest.mark.asyncio
async def test_dependent_stage_can_explicitly_request_historical_context():
    searched = []

    async def search(intent, limit):
        searched.append((intent, limit))
        return [{"ref": "context:preference", "score": 0.9}]

    injector = SafeStageContextInjector(search=search)
    capsule = await injector.build(
        StageSpec(
            "stage-2",
            "Prepare meeting briefing",
            dependencies=("stage-1",),
            context_intents=("Previous workspace conversation about briefing preferences",),
        )
    )

    assert searched == [("Previous workspace conversation about briefing preferences", 5)]
    assert capsule.context_refs == ("context:preference",)


@pytest.mark.asyncio
async def test_worker_hard_denies_discovery_tools_even_if_capsule_contains_them():
    worker = SafeAgentRuntimeWorker(
        schemas=lambda: [
            _tool("context.search"),
            _tool("capability.search"),
            _tool("capability.describe"),
            _tool("context.get"),
            _tool("gmail.search"),
        ],
        invoke=lambda *_args: {},
        model_resolver=lambda _role: object(),
    )
    tools = await worker._stage_schemas(
        StageSpec("mail", "Search mail"),
        ContextCapsule(
            stage_id="mail",
            objective="Search mail",
            context_refs=("ctx-1",),
            capability_ids=(
                "context.search",
                "capability.search",
                "capability.describe",
                "gmail.search",
            ),
        ),
    )

    assert {item["function"]["name"] for item in tools} == {"context.get", "gmail.search"}


@pytest.mark.asyncio
async def test_worker_blocks_before_model_call_when_required_capability_is_missing():
    worker = SafeAgentRuntimeWorker(
        schemas=lambda: [],
        invoke=lambda *_args: {},
        model_resolver=lambda _role: (_ for _ in ()).throw(AssertionError("model must not run")),
    )
    result = await worker(
        StageSpec(
            "calendar",
            "Read calendar",
            capability_intents=("Read calendar events",),
        ),
        ContextCapsule(
            stage_id="calendar",
            objective="Read calendar",
            facts=(("missing_capability_intents", ["Read calendar events"]),),
        ),
        1,
        None,
    )

    assert result.status == "denied"
    assert result.evidence["terminal"] is True
    assert result.evidence["failure_class"] == "capability_missing"
    assert result.token_usage == 0


@pytest.mark.asyncio
async def test_runner_promotes_validated_result_into_next_stage_capsule():
    seen_facts = {}
    validated_results = {}
    injector = SafeStageContextInjector(validated_results=validated_results)

    async def worker(stage, capsule, attempt, defect):
        del attempt, defect
        if stage.id == "stage-1":
            return StageWorkerResult(
                status="completed",
                summary="Calendar event: 10:00 Design review",
                evidence={"verified": True},
            )
        seen_facts.update(dict(capsule.facts))
        return StageWorkerResult(status="completed", summary="Prepared briefing")

    async def validator(spec, stage, result):
        del spec, stage, result
        return {"passed": True}

    result = await SafeFactoryStageRunner(
        context_injector=injector,
        worker=worker,
        validator=validator,
        validated_results=validated_results,
    ).run(
        graph=StageGraph(
            (
                StageSpec("stage-1", "Read calendar"),
                StageSpec("stage-2", "Prepare", dependencies=("stage-1",)),
            )
        ),
        acceptance=AcceptanceContract(()),
    )

    assert result.completed is True
    assert seen_facts["dependency_results"]["stage-1"]["summary"] == "Calendar event: 10:00 Design review"