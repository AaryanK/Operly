from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.actions.service import ActionService, ActionStatus
from packages.agents.persistence import (
    checkpoint_agent_run,
    find_resumable_agent_run,
    load_agent_run,
)
from packages.agents.run_state import CompactRunState
from packages.artifacts.service import ArtifactScope, ArtifactService
from packages.business_brain.attachments.batch_processor import BatchRecord
from packages.capabilities.defaults import default_registry
from packages.capabilities.file_runtime_provider import FileRuntimeProvider
from packages.capabilities.gmail_artifact_provider import GmailArtifactProvider
from packages.capabilities.registry import CapabilityRegistry
from packages.database.artifact_models import ArtifactRecord, AgentRunEventRecord, AgentRunRecord
from packages.database.company_models import BusinessActionRecord, BusinessEventRecord
from packages.database.db import Base
from packages.database.models import AppUser, Approval, Task, Tenant


@pytest_asyncio.fixture
async def runtime_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[
                    Tenant.__table__,
                    AppUser.__table__,
                    Task.__table__,
                    Approval.__table__,
                    BusinessActionRecord.__table__,
                    BusinessEventRecord.__table__,
                    ArtifactRecord.__table__,
                    AgentRunRecord.__table__,
                    AgentRunEventRecord.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Tenant(id="tenant-a", name="Tenant A", slug="tenant-a"))
        db.add(Tenant(id="tenant-b", name="Tenant B", slug="tenant-b"))
        await db.commit()
        yield db, factory
    await engine.dispose()


def workspace_context(db, *, tenant_id: str = "tenant-a", run_id: str = "run-acceptance"):
    return SimpleNamespace(
        tenant_id=tenant_id,
        actor_id="user-1",
        scope_kind="workspace",
        scope_id=tenant_id,
        owner_user_id=None,
        db=db,
        execution_id="action-1",
        invocation={"metadata": {"runtime_run_id": run_id}},
    )


class DeterministicInvoiceBatchProcessor:
    """Avoid 400 paid model calls while exercising the real fan-out/fan-in runtime."""

    def __init__(self):
        self.chunk_sizes: list[int] = []

    async def extract(self, *, request, artifacts, columns, concurrency=4):
        del request, concurrency
        self.chunk_sizes.append(len(artifacts))
        rows = []
        for artifact_id, filename, _content_type, raw in artifacts:
            ordinal = int(filename.split("-")[1].split(".")[0])
            assert raw == f"invoice:{ordinal}".encode()
            values = {column.name: None for column in columns}
            values.update(
                {
                    "invoice_number": f"INV-{ordinal:04d}",
                    "vendor": f"Vendor {ordinal % 12}",
                    "invoice_date": "2026-08-24",
                    "subtotal": float(ordinal),
                    "tax": float(ordinal) * 0.1,
                    "total": float(ordinal) * 1.1,
                    "currency": "USD",
                }
            )
            rows.append(BatchRecord(artifact_id, filename, values))
        return rows


@pytest.mark.asyncio
async def test_artifacts_are_scope_isolated_and_integrity_checked(runtime_db):
    db, _ = runtime_db
    service = ArtifactService(db)
    scope_a = ArtifactScope("workspace", "tenant-a", tenant_id="tenant-a")
    scope_b = ArtifactScope("workspace", "tenant-b", tenant_id="tenant-b")
    artifact = await service.create_bytes(
        scope_a,
        filename="invoice.pdf",
        content_type="application/pdf",
        content=b"%PDF-test",
        source="test",
        created_by="user-1",
    )
    await db.commit()

    assert await service.read_bytes(scope_a, artifact.id) == b"%PDF-test"
    with pytest.raises(LookupError):
        await service.get(scope_b, artifact.id)

    artifact.content_bytes = b"tampered"
    await db.flush()
    with pytest.raises(RuntimeError, match="integrity"):
        await service.read_bytes(scope_a, artifact.id)


@pytest.mark.asyncio
async def test_agent_run_checkpoint_is_durable_scope_bound_and_resumable(runtime_db):
    db, factory = runtime_db
    del db

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
        "channel": "web",
        "surface": "workspace",
        "conversation_id": "conversation-1",
    }
    state = {
        "objective": "Process invoices",
        "plan": {
            "objective": "Process invoices",
            "planning_required": True,
            "revision": 1,
            "tasks": [
                {
                    "id": "extract",
                    "objective": "Extract invoices",
                    "status": "completed",
                },
                {
                    "id": "report",
                    "objective": "Create reports",
                    "dependencies": ["extract"],
                    "status": "pending",
                },
            ],
        },
        "artifact_refs": ["artifact-1", "artifact-2"],
        "pending_approval_ids": [],
        "revision": 3,
    }
    with patch("packages.agents.persistence.session_scope", local_session_scope):
        await checkpoint_agent_run(
            runtime_run_id="run-durable-1",
            objective="Process invoices",
            metadata=metadata,
            state=state,
            event_type="capability.observed",
            lifecycle_state="running",
        )
        loaded = await load_agent_run("run-durable-1", metadata=metadata)
        assert loaded is not None
        assert loaded["checkpoint"]["artifact_refs"] == ["artifact-1", "artifact-2"]
        implicit = await find_resumable_agent_run(
            objective="Process invoices",
            metadata=metadata,
        )
        assert implicit is not None
        assert implicit["run_id"] == "run-durable-1"
        assert await find_resumable_agent_run(objective="Different objective", metadata=metadata) is None
        with pytest.raises(PermissionError):
            await load_agent_run(
                "run-durable-1",
                metadata={**metadata, "tenant_id": "tenant-b"},
            )

    restored = CompactRunState.from_dict(state)
    assert restored.artifact_refs == {"artifact-1", "artifact-2"}
    assert restored.plan is not None
    assert restored.plan.task("extract").status.value == "completed"
    assert [task.id for task in restored.plan.ready_tasks()] == ["report"]

    async with factory() as inspect_db:
        row = await inspect_db.get(AgentRunRecord, "run-durable-1")
        events = list(
            (
                await inspect_db.scalars(
                    select(AgentRunEventRecord).where(AgentRunEventRecord.run_id == "run-durable-1")
                )
            ).all()
        )
        assert row is not None
        assert row.scope_id == "tenant-a"
        assert json.loads(row.artifact_refs_json) == ["artifact-1", "artifact-2"]
        assert [event.sequence for event in events] == [1]


@pytest.mark.asyncio
async def test_400_invoice_north_star_runs_through_firewall_and_drafts_email(runtime_db):
    db, _ = runtime_db
    scope = ArtifactScope("workspace", "tenant-a", tenant_id="tenant-a")
    artifacts = ArtifactService(db)

    input_ids = []
    for ordinal in range(1, 401):
        artifact = await artifacts.create_bytes(
            scope,
            filename=f"invoice-{ordinal}.txt",
            content_type="text/plain",
            content=f"invoice:{ordinal}".encode(),
            source="test_invoice",
            created_by="user-1",
            run_id="run-acceptance",
        )
        input_ids.append(artifact.id)
    await db.commit()

    extractor = DeterministicInvoiceBatchProcessor()
    file_provider = FileRuntimeProvider(batch_processor=extractor)
    gmail_provider = GmailArtifactProvider()
    registry = CapabilityRegistry()
    registry.register(file_provider)
    registry.register(gmail_provider)
    actions = ActionService(
        db,
        registry,
        authority={"files:process", "gmail:draft"},
        actor_id="user-1",
    )
    batch_arguments = {
        "request": "Extract invoice details, calculate totals, create an Excel workbook and PDF summary.",
        "artifact_ids": input_ids,
        "columns": [
            {"name": "invoice_number", "type": "string"},
            {"name": "vendor", "type": "string"},
            {"name": "invoice_date", "type": "date"},
            {"name": "subtotal", "type": "number"},
            {"name": "tax", "type": "number"},
            {"name": "total", "type": "number"},
            {"name": "currency", "type": "string"},
        ],
        "sum_columns": ["subtotal", "tax", "total"],
        "output_formats": ["xlsx", "pdf"],
        "concurrency": 4,
        "title": "400 Invoice Summary",
    }

    with patch("packages.actions.service.emit_runtime_trace_event", AsyncMock()):
        batch_action = await actions.propose(
            tenant_id="tenant-a",
            objective="Process 400 invoices and create Excel/PDF reports",
            capability="files.batch_process",
            arguments=batch_arguments,
            rationale="User explicitly requested the reports",
            expected_outcome="Durable XLSX and PDF artifacts",
            risk_level="low",
            idempotency_key="north-star-batch-400",
            runtime_context={"metadata": {"runtime_run_id": "run-acceptance"}},
        )
    assert batch_action.status == ActionStatus.VERIFIED
    payload = json.loads(batch_action.result_json)
    result = payload["evidence"]
    assert result["processed_count"] == 400
    assert result["success_count"] == 400
    assert result["failure_count"] == 0
    assert result["sums"]["subtotal"] == pytest.approx(80200.0)
    assert result["sums"]["tax"] == pytest.approx(8020.0)
    assert result["sums"]["total"] == pytest.approx(88220.0)
    assert extractor.chunk_sizes == [20] * 20
    assert len(result["records_preview"]) == 20
    assert file_provider.capabilities[1].execution_timeout_seconds == 1800

    generated = result["artifacts"]
    assert len(generated) == 2
    xlsx = next(item for item in generated if item["content_type"].startswith("application/vnd.openxmlformats"))
    pdf = next(item for item in generated if item["content_type"] == "application/pdf")
    assert (await artifacts.read_bytes(scope, xlsx["artifact_id"])).startswith(b"PK")
    assert (await artifacts.read_bytes(scope, pdf["artifact_id"])).startswith(b"%PDF")

    captured: dict = {}

    async def fake_request(method, url, token, payload=None, **kwargs):
        del kwargs
        captured.update(method=method, url=url, token=token, payload=payload)
        return {"id": "draft-400", "message": {"id": "message-400"}}

    with (
        patch("packages.actions.service.emit_runtime_trace_event", AsyncMock()),
        patch(
            "packages.capabilities.gmail_artifact_provider.google_connector_for_context",
            AsyncMock(return_value=SimpleNamespace(provider_account_id="acct-1")),
        ),
        patch(
            "packages.capabilities.gmail_artifact_provider.google_access_token_for_context",
            AsyncMock(return_value="token-1"),
        ),
        patch(
            "packages.capabilities.gmail_artifact_provider.request_json",
            side_effect=fake_request,
        ),
    ):
        draft_action = await actions.propose(
            tenant_id="tenant-a",
            objective="Draft the invoice summary email",
            capability="gmail.create_draft_with_artifacts",
            arguments={
                "to": ["finance@example.com"],
                "subject": "400 invoice summary",
                "text_body": "Attached is the generated invoice summary.",
                "artifact_ids": [pdf["artifact_id"]],
            },
            rationale="User asked for a draft with the generated PDF attached",
            expected_outcome="Verified Gmail draft with scoped PDF artifact",
            risk_level="low",
            idempotency_key="north-star-draft-400",
            runtime_context={"metadata": {"runtime_run_id": "run-acceptance"}},
        )

    assert draft_action.status == ActionStatus.VERIFIED
    draft = json.loads(draft_action.result_json)["evidence"]
    assert draft["draft_id"] == "draft-400"
    assert draft["attachment_artifact_ids"] == [pdf["artifact_id"]]
    assert draft["delivery_status"] == "draft"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/gmail/v1/users/me/drafts")

    raw = captured["payload"]["message"]["raw"]
    mime_bytes = base64.urlsafe_b64decode(raw.encode())
    message = BytesParser(policy=policy.default).parsebytes(mime_bytes)
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "operly-batch-summary.pdf"
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_payload(decode=True).startswith(b"%PDF")


@pytest.mark.asyncio
async def test_gmail_artifact_draft_cannot_cross_workspace_scope(runtime_db):
    db, _ = runtime_db
    service = ArtifactService(db)
    source_scope = ArtifactScope("workspace", "tenant-a", tenant_id="tenant-a")
    artifact = await service.create_bytes(
        source_scope,
        filename="private.pdf",
        content_type="application/pdf",
        content=b"%PDF-private",
    )
    await db.commit()

    provider = GmailArtifactProvider()
    other_context = workspace_context(db, tenant_id="tenant-b")
    result = await provider.execute(
        other_context,
        "gmail.create_draft_with_artifacts",
        {
            "to": ["finance@example.com"],
            "subject": "Should not work",
            "artifact_ids": [artifact.id],
        },
    )
    assert result.success is False
    assert result.evidence["reason"] == "gmail_artifact_draft_failed"


def test_shared_registry_exposes_artifacts_batch_and_gmail_attachment_capabilities():
    registry = default_registry()
    assert registry.provider_name("artifact.list") == "operly_artifacts"
    assert registry.provider_name("files.batch_process") == "operly_file_runtime"
    assert registry.provider_name("gmail.create_draft_with_artifacts") == "gmail_artifacts"
    batch = registry.definition("files.batch_process")
    assert "process many files" in batch.semantic_operations
    assert batch.execution_timeout_seconds == 1800
    gmail = registry.definition("gmail.create_draft_with_artifacts")
    assert gmail.permissions == ("gmail:draft", "files:process")
