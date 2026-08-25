from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.artifact_router import _save_uploads
from packages.agents.persistence import (
    checkpoint_agent_run,
    find_resumable_agent_run,
)
from packages.artifacts.service import ArtifactScope, ArtifactService
from packages.capabilities.computer_provider import AgentComputerProvider
from packages.capabilities.defaults import default_registry
from packages.database.artifact_models import (
    AgentRunEventRecord,
    AgentRunRecord,
    ArtifactRecord,
)
from packages.database.db import Base
from packages.database.models import Tenant


class MemoryBlobStore:
    kind = "s3"

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def put(self, key: str, content: bytes, *, content_type: str) -> None:
        assert content_type
        self.objects[key] = bytes(content)

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


class FakeComputerRunner:
    def __init__(self):
        self.payload = None

    async def execute(self, payload: dict) -> dict:
        self.payload = payload
        source = base64.b64decode(payload["inputs"][0]["contentBase64"])
        assert source == b"2,3\n"
        return {
            "ok": True,
            "exitCode": 0,
            "timedOut": False,
            "stdout": "computed",
            "stderr": "",
            "outputs": [
                {
                    "path": "answer.txt",
                    "sizeBytes": 2,
                    "contentBase64": base64.b64encode(b"5\n").decode(),
                }
            ],
            "isolation": "railway_sandbox_vm_v1",
            "network": "isolated",
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
        execution_id="action-computer-1",
        db=db,
        invocation={"metadata": {"runtime_run_id": "computer-run-1"}},
    )


@pytest.mark.asyncio
async def test_s3_style_blob_store_keeps_bytes_out_of_database_and_verifies_integrity(runtime_db):
    db, _ = runtime_db
    blob = MemoryBlobStore()
    service = ArtifactService(db, blob_store=blob)
    scope = ArtifactScope("workspace", "tenant-a", tenant_id="tenant-a")

    row = await service.create_bytes(
        scope,
        filename="large-report.pdf",
        content_type="application/pdf",
        content=b"%PDF-object-store",
        source="test",
    )
    await db.commit()

    assert row.storage_kind == "s3"
    assert row.storage_key in blob.objects
    assert row.content_bytes is None
    assert await service.read_bytes(scope, row.id) == b"%PDF-object-store"

    blob.objects[row.storage_key] = b"tampered"
    with pytest.raises(RuntimeError, match="integrity"):
        await service.read_bytes(scope, row.id)


@pytest.mark.asyncio
async def test_large_n_ingress_helper_persists_durable_artifacts(runtime_db):
    db, _ = runtime_db
    scope = ArtifactScope("workspace", "tenant-a", tenant_id="tenant-a")
    uploads = [
        UploadFile(filename=f"invoice-{index}.txt", file=BytesIO(f"invoice {index}".encode()))
        for index in range(1, 21)
    ]
    rows = await _save_uploads(
        files=uploads,
        service=ArtifactService(db),
        scope=scope,
        actor_id="user-1",
        source="workspace_upload",
    )
    await db.commit()

    assert len(rows) == 20
    assert len({row["artifact_id"] for row in rows}) == 20
    assert all(row["source"] == "workspace_upload" for row in rows)

    too_many = [UploadFile(filename=f"f-{index}.txt", file=BytesIO(b"x")) for index in range(51)]
    with pytest.raises(HTTPException) as error:
        await _save_uploads(
            files=too_many,
            service=ArtifactService(db),
            scope=scope,
            actor_id="user-1",
            source="workspace_upload",
        )
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_agent_computer_round_trips_scoped_artifacts(runtime_db):
    db, _ = runtime_db
    context = workspace_context(db)
    scope = ArtifactScope("workspace", "tenant-a", tenant_id="tenant-a")
    service = ArtifactService(db)
    source = await service.create_bytes(
        scope,
        filename="numbers.csv",
        content_type="text/csv",
        content=b"2,3\n",
        source="test",
    )
    await db.commit()

    runner = FakeComputerRunner()
    provider = AgentComputerProvider(runner=runner)
    result = await provider.execute(
        context,
        "computer.run_python",
        {
            "code": "from pathlib import Path\na,b=map(int,Path('/workspace/input/numbers.csv').read_text().split(','))\nPath('/workspace/output/answer.txt').write_text(str(a+b)+'\\n')",
            "artifact_ids": [source.id],
            "output_paths": ["answer.txt"],
            "timeout_seconds": 30,
        },
    )
    assert result.success is True
    assert result.evidence["isolation"] == "railway_sandbox_vm_v1"
    assert result.evidence["network"] == "isolated"
    assert runner.payload["mode"] == "python"
    assert runner.payload["inputs"][0]["artifactId"] == source.id

    output_id = result.evidence["artifact_ids"][0]
    assert await service.read_bytes(scope, output_id) == b"5\n"
    verified = await provider.verify(context, "computer.run_python", {}, result)
    assert verified.success is True
    assert verified.evidence["verified"] is True


@pytest.mark.asyncio
async def test_exact_unfinished_agent_run_is_discovered_for_crash_recovery(runtime_db):
    _db, factory = runtime_db

    @asynccontextmanager
    async def local_session_scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    metadata = {
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "conversation_id": "conversation-recovery",
        "channel": "web",
        "surface": "workspace_private",
    }
    state = {
        "objective": "Prepare the invoice report",
        "artifact_refs": ["artifact-before-crash"],
        "pending_approval_ids": [],
        "revision": 4,
    }
    with patch("packages.agents.persistence.session_scope", local_session_scope):
        await checkpoint_agent_run(
            runtime_run_id="recovery-run-1",
            objective="Prepare the invoice report",
            metadata=metadata,
            state=state,
            event_type="capability.observed",
            lifecycle_state="running",
        )
        found = await find_resumable_agent_run(
            objective="Prepare the invoice report",
            metadata=metadata,
        )
        assert found is not None
        assert found["run_id"] == "recovery-run-1"
        assert found["checkpoint"]["artifact_refs"] == ["artifact-before-crash"]

        assert (
            await find_resumable_agent_run(
                objective="A different request",
                metadata=metadata,
            )
            is None
        )
        assert (
            await find_resumable_agent_run(
                objective="Prepare the invoice report",
                metadata={**metadata, "conversation_id": "different-conversation"},
            )
            is None
        )


def test_canonical_registry_exposes_agent_computer():
    registry = default_registry()
    assert registry.provider_name("computer.run_python") == "operly_agent_computer"
    assert registry.provider_name("computer.run_command") == "operly_agent_computer"
    python = registry.definition("computer.run_python")
    assert python.execution_mode.value == "isolated_runner"
    assert python.permissions == ("computer:execute", "files:process")
