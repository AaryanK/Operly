from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.channels.envelope import ChannelEnvelope
from packages.channels.identity import IdentityService
from packages.channels.service import ChannelService
from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models
from packages.model_runtime.contracts import InferenceRequest, InferenceResult
from packages.model_runtime.task_routing import (
    TaskRouteDecision,
    TaskRoutedBusinessModel,
)


async def _linked_user_with_two_workspaces(sessions):
    async with sessions() as db:
        user = AppUser(email="personal-dm@example.com", display_name="Personal DM", active=True)
        first = Tenant(name="ANHITRA", slug="anhitra")
        second = Tenant(name="NaySchool", slug="nayschool")
        db.add_all([user, first, second])
        await db.flush()
        db.add_all(
            [
                TenantMember(tenant_id=first.id, user_id=user.id, role="owner"),
                TenantMember(tenant_id=second.id, user_id=user.id, role="member"),
            ]
        )
        await IdentityService.link_external_identity(
            db,
            user_id=user.id,
            provider="discord",
            external_user_id="discord-personal-1",
            display_name="Personal DM",
        )
        await db.commit()
        return user.id, first.id, second.id


@pytest_asyncio.fixture
async def dm_database(tmp_path):
    import_all_models()
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'personal-dm.db').as_posix()}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "what are all my workspaces?",
        "what all are in my workspaces?",
        "what all workspaces do I own?",
    ],
)
async def test_private_dm_does_not_choose_first_workspace_as_execution_scope(dm_database, prompt):
    user_id, _, _ = await _linked_user_with_two_workspaces(dm_database)
    async with dm_database() as db:
        resolved = await ChannelService.resolve(
            db,
            ChannelEnvelope(
                provider="discord",
                external_user_id="discord-personal-1",
                external_conversation_id=f"dm-{prompt[:8]}",
                actor_name="Personal DM",
                text=prompt,
                is_direct=True,
            ),
        )

    assert resolved.user_id == user_id
    assert resolved.tenant_id is None
    assert resolved.allow_tenant_context is False
    assert {item["name"] for item in resolved.options} == {"ANHITRA", "NaySchool"}


@pytest.mark.asyncio
async def test_linked_dm_with_memberships_runs_personal_agent_not_workspace_agent(dm_database):
    user_id, _, _ = await _linked_user_with_two_workspaces(dm_database)
    captured = {}

    class FakePersonalAgent:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return {
                "message": "You belong to ANHITRA and NaySchool.",
                "conversation_id": "discord:dm-personal",
            }

    @asynccontextmanager
    async def test_scope():
        async with dm_database() as db:
            yield db
            await db.commit()

    envelope = ChannelEnvelope(
        provider="discord",
        external_user_id="discord-personal-1",
        external_conversation_id="dm-personal",
        actor_name="Personal DM",
        text="what are all my workspaces?",
        is_direct=True,
    )
    with patch("packages.database.db.session_scope", test_scope), patch(
        "packages.channels.service.get_personal_agent_service",
        return_value=FakePersonalAgent(),
    ), patch("packages.channels.service.get_agent_service") as workspace_agent:
        response = await ChannelService.handle(envelope)

    workspace_agent.assert_not_called()
    assert response.user_id == user_id
    assert response.tenant_id is None
    assert response.status == "ok"
    assert captured["selected_workspace_id"] is None
    assert captured["conversation_id"] == "discord:dm-personal"


class CapturingModel:
    def __init__(self):
        self.request = None

    async def infer(self, request):
        self.request = request
        return InferenceResult(
            message={"role": "assistant", "content": "ok"},
            model_resource_id="test-model",
            provider="test",
            provider_model_id="test-model",
            latency_ms=1,
        )


def _account_tool():
    return {
        "type": "function",
        "function": {
            "name": "account.list_workspaces",
            "description": "List account workspaces",
            "parameters": {"type": "object", "properties": {}},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "hello",
        "what is 2 + 2?",
        "explain this error to me",
        "what workspaces do I own?",
    ],
)
async def test_common_assistant_requests_bypass_specialist_router(prompt):
    target = CapturingModel()
    router = AsyncMock()
    with patch(
        "packages.model_runtime.task_routing.route_business_task",
        router,
    ), patch(
        "packages.model_runtime.task_routing.model_for_requirements",
        return_value=target,
    ) as resolver:
        model = TaskRoutedBusinessModel()
        await model.infer(
            InferenceRequest(
                messages=({"role": "user", "content": prompt},),
                tools=(_account_tool(),),
            )
        )

    router.assert_not_awaited()
    requirements = resolver.call_args.args[0]
    assert resolver.call_args.kwargs["fallback_role"] == "business_agent"
    assert {"text", "tools"}.issubset(requirements.requires)
    assert model.last_decision is not None
    assert model.last_decision.role == "business_agent"
    assert target.request.tools == (_account_tool(),)
    metadata = target.request.metadata["task_route"]
    assert metadata["compatibilityRole"] == "business_agent"
    assert metadata["toolSchemasForwarded"] is True
    assert set(metadata["modelRequirements"]["requires"]) >= {"text", "tools"}


@pytest.mark.asyncio
async def test_planning_with_tools_keeps_planning_shape_and_requires_tool_capability():
    target = CapturingModel()
    decision = TaskRouteDecision(
        task_type="planning",
        role="planner",
        tool_policy="read_then_propose",
        confidence=0.9,
        reason="test planner route",
    )
    router = AsyncMock(return_value=decision)
    with patch(
        "packages.model_runtime.task_routing.route_business_task",
        router,
    ), patch(
        "packages.model_runtime.task_routing.model_for_requirements",
        return_value=target,
    ) as resolver:
        model = TaskRoutedBusinessModel()
        await model.infer(
            InferenceRequest(
                messages=({"role": "user", "content": "plan a growth strategy"},),
                tools=(_account_tool(),),
            )
        )

    router.assert_awaited_once()
    requirements = resolver.call_args.args[0]
    assert {"text", "reasoning", "tools"}.issubset(requirements.requires)
    assert resolver.call_args.kwargs["fallback_role"] == "business_agent"
    assert target.request.tools == (_account_tool(),)
    metadata = target.request.metadata["task_route"]
    assert metadata["role"] == "planner"
    assert metadata["compatibilityRole"] == "planner"
    assert metadata["toolSchemasForwarded"] is True
    assert "executionRole" not in metadata


@pytest.mark.asyncio
async def test_planning_without_tools_requires_reasoning_not_tool_support():
    target = CapturingModel()
    decision = TaskRouteDecision(
        task_type="planning",
        role="planner",
        tool_policy="read_then_propose",
        confidence=0.9,
        reason="test planner route",
    )
    with patch(
        "packages.model_runtime.task_routing.route_business_task",
        AsyncMock(return_value=decision),
    ), patch(
        "packages.model_runtime.task_routing.model_for_requirements",
        return_value=target,
    ) as resolver:
        model = TaskRoutedBusinessModel()
        await model.infer(
            InferenceRequest(messages=({"role": "user", "content": "plan a growth strategy"},))
        )

    requirements = resolver.call_args.args[0]
    assert resolver.call_args.kwargs["fallback_role"] == "planner"
    assert {"text", "reasoning"}.issubset(requirements.requires)
    assert "tools" not in requirements.requires
    assert target.request.tools == ()
    assert target.request.metadata["task_route"]["toolSchemasForwarded"] is False


def test_router_context_does_not_equate_available_tools_with_need():
    source = __import__("pathlib").Path("packages/model_runtime/task_routing.py").read_text()
    assert '"hasAvailableCapabilities": bool(payload.get("tool_count"))' in source
    assert '"needsCapabilities": bool(payload.get("tool_count"))' not in source
