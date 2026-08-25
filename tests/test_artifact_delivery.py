from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.artifacts.delivery import (
    artifact_ids_from_run,
    artifacts_by_assistant_message,
    project_agent_result,
)
from packages.artifacts.service import ArtifactScope, ArtifactService
from packages.database.agent_models import AgentConversation, AgentMessage
from packages.database.artifact_models import AgentRunRecord, ArtifactRecord
from packages.database.db import Base
from packages.database.models import Tenant


@pytest_asyncio.fixture
async def delivery_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[
                    Tenant.__table__,
                    ArtifactRecord.__table__,
                    AgentRunRecord.__table__,
                    AgentConversation.__table__,
                    AgentMessage.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Tenant(id="tenant-a", name="Tenant A", slug="tenant-a"))
        await db.commit()
        yield db
    await engine.dispose()


def test_artifact_ids_from_run_reads_compact_run_state():
    assert artifact_ids_from_run(
        {"run_state": {"artifact_refs": ["a", "a", "b"]}}
    ) == ["a", "b"]


@pytest.mark.asyncio
async def test_project_agent_result_resolves_scope_and_replaces_generic_done(delivery_db):
    db = delivery_db
    scope = ArtifactScope("workspace", "tenant-a", tenant_id="tenant-a")
    artifact = await ArtifactService(db).create_bytes(
        scope,
        filename="customer-report.pdf",
        content_type="application/pdf",
        content=b"%PDF-test",
        source="files.process",
        run_id="run-1",
    )
    db.add(
        AgentRunRecord(
            id="run-1",
            scope_kind="workspace",
            scope_id="tenant-a",
            tenant_id="tenant-a",
            objective="Create the customer report",
            state="completed",
            artifact_refs_json=json.dumps([artifact.id]),
        )
    )
    await db.commit()

    result = await project_agent_result(
        db,
        scope,
        {"message": "Done.", "runtime_run_id": "run-1"},
    )

    assert result["artifact_ids"] == [artifact.id]
    assert result["artifacts"][0]["filename"] == "customer-report.pdf"
    assert result["delivery"]["artifact_count"] == 1
    assert result["message"] == "Created `customer-report.pdf`."

    foreign_scope = ArtifactScope("workspace", "tenant-b", tenant_id="tenant-b")
    # Scope validation happens before artifact metadata leaves the store/run boundary.
    foreign = await project_agent_result(
        db,
        foreign_scope,
        {"message": "Done.", "runtime_run_id": "run-1"},
    )
    assert foreign["artifacts"] == []


@pytest.mark.asyncio
async def test_conversation_history_projects_run_artifacts_to_assistant_turn(delivery_db):
    db = delivery_db
    scope = ArtifactScope("workspace", "tenant-a", tenant_id="tenant-a")
    conversation = AgentConversation(
        id="conversation-1",
        tenant_id="tenant-a",
        principal_id="web-user:user-1",
        channel="web",
        title="Report",
    )
    db.add(conversation)
    await db.flush()

    started = datetime.utcnow()
    user = AgentMessage(
        id="user-message",
        tenant_id="tenant-a",
        conversation_id=conversation.id,
        role="user",
        content="Create a PDF",
        created_at=started - timedelta(seconds=1),
    )
    assistant = AgentMessage(
        id="assistant-message",
        tenant_id="tenant-a",
        conversation_id=conversation.id,
        role="assistant",
        content="Created it.",
        created_at=started + timedelta(seconds=1),
    )
    db.add_all([user, assistant])
    artifact = await ArtifactService(db).create_bytes(
        scope,
        filename="report.pdf",
        content_type="application/pdf",
        content=b"%PDF-history",
        source="files.process",
        run_id="run-history",
    )
    db.add(
        AgentRunRecord(
            id="run-history",
            scope_kind="workspace",
            scope_id="tenant-a",
            tenant_id="tenant-a",
            conversation_id=conversation.id,
            objective="Create a PDF",
            state="completed",
            artifact_refs_json=json.dumps([artifact.id]),
            started_at=started,
        )
    )
    await db.commit()

    mapping = await artifacts_by_assistant_message(
        db,
        scope,
        conversation_id=conversation.id,
        messages=[user, assistant],
    )
    assert mapping[assistant.id][0]["artifact_id"] == artifact.id
