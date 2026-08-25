from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agents.persistence import checkpoint_agent_run
from packages.capabilities.computer_provider import AgentComputerProvider
from packages.database.artifact_models import AgentRunEventRecord, AgentRunRecord, ArtifactRecord
from packages.database.db import Base
from packages.database.models import Tenant
from packages.model_runtime.trace_context import runtime_trace_scope


class SessionRunner:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def execute(self, payload: dict) -> dict:
        self.payloads.append(dict(payload))
        return {
            "ok": True,
            "exitCode": 0,
            "timedOut": False,
            "stdout": "ok",
            "stderr": "",
            "outputs": [],
            "isolation": "railway_sandbox_vm_v1",
            "network": "isolated",
            "sandboxId": "sbx_run_1234",
            "sessionReused": bool(payload.get("sandboxId")),
            "sessionRecovered": False,
            "runScoped": True,
            "environment": {"python": "operly-rich-python-v1"},
        }


@pytest_asyncio.fixture
async def runtime_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[
                    Tenant.__table__,
                    ArtifactRecord.__table__,
                    AgentRunRecord.__table__,
                    AgentRunEventRecord.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Tenant(id="tenant-a", name="Tenant A", slug="tenant-a"))
        db.add(
            AgentRunRecord(
                id="run-1234",
                scope_kind="workspace",
                scope_id="tenant-a",
                tenant_id="tenant-a",
                owner_user_id=None,
                actor_id="user-1",
                objective="Analyze the supplied files",
                state="running",
            )
        )
        await db.commit()
        yield db, factory
    await engine.dispose()


def workspace_context(db):
    return SimpleNamespace(
        tenant_id="tenant-a",
        actor_id="user-1",
        scope_kind="workspace",
        scope_id="tenant-a",
        owner_user_id=None,
        execution_id="action-1",
        db=db,
        invocation={"metadata": {}},
    )


@pytest.mark.asyncio
async def test_computer_reuses_sandbox_for_same_agent_run(runtime_db):
    db, _ = runtime_db
    runner = SessionRunner()
    provider = AgentComputerProvider(runner=runner)
    context = workspace_context(db)

    with runtime_trace_scope({"runtime_run_id": "run-1234"}):
        first = await provider.execute(
            context,
            "computer.run_python",
            {"code": "print('first')"},
        )
        await db.commit()
        second = await provider.execute(
            context,
            "computer.run_python",
            {"code": "print('second')"},
        )

    assert first.success is True
    assert second.success is True
    assert runner.payloads[0]["keepAlive"] is True
    assert "sandboxId" not in runner.payloads[0]
    assert runner.payloads[1]["keepAlive"] is True
    assert runner.payloads[1]["sandboxId"] == "sbx_run_1234"
    assert second.evidence["session_reused"] is True
    row = await db.get(AgentRunRecord, "run-1234")
    assert row.computer_session_id == "sbx_run_1234"


@pytest.mark.asyncio
async def test_terminal_checkpoint_clears_and_destroys_computer_handle(runtime_db):
    db, factory = runtime_db
    row = await db.get(AgentRunRecord, "run-1234")
    row.computer_session_id = "sbx_run_1234"
    await db.commit()

    @asynccontextmanager
    async def local_session_scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    destroy = AsyncMock(return_value={"ok": True, "destroyed": True})
    metadata = {
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "conversation_id": "conv-1",
        "surface": "workspace_private",
        "channel": "web",
    }
    with (
        patch("packages.agents.persistence.session_scope", local_session_scope),
        patch("packages.agent_computer.runner_client.AgentComputerRunnerClient.destroy", destroy),
    ):
        await checkpoint_agent_run(
            runtime_run_id="run-1234",
            objective="Analyze the supplied files",
            metadata=metadata,
            state={"objective": "Analyze the supplied files"},
            event_type="run.finished",
            lifecycle_state="completed",
        )

    async with factory() as verify_db:
        persisted = await verify_db.get(AgentRunRecord, "run-1234")
        assert persisted.computer_session_id is None
        assert persisted.state == "completed"
    destroy.assert_awaited_once_with("sbx_run_1234")


def test_rich_environment_is_discoverable():
    definitions = {item.id: item for item in AgentComputerProvider.capabilities}
    assert "computer.environment" in definitions
    operations = definitions["computer.run_python"].semantic_operations
    assert "resize or compress images" in operations
    assert "analyze spreadsheets and csv" in operations
