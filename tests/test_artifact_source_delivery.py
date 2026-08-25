from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from packages.artifacts.service import ArtifactScope, ArtifactService
from packages.capabilities.artifact_provider import ArtifactProvider
from packages.capabilities.search_index import CapabilitySearchIndex
from packages.database.artifact_models import ArtifactRecord
from packages.database.db import Base
from packages.database.models import Tenant


class _ZeroSemanticIndex:
    backend_name = "test-zero-semantic"
    degraded_reason = "forced lexical-only regression test"

    def rank(self, documents, query, *, limit):
        del documents, query, limit
        return []


@pytest_asyncio.fixture
async def runtime_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[Tenant.__table__, ArtifactRecord.__table__],
            )
        )
    async with engine.connect() as connection:
        transaction = await connection.begin()
        db = AsyncSession(bind=connection, expire_on_commit=False)
        db.add(Tenant(id="tenant-a", name="Tenant A", slug="tenant-a"))
        await db.flush()
        yield db
        await db.close()
        await transaction.rollback()
    await engine.dispose()


def _context(db):
    return SimpleNamespace(
        tenant_id="tenant-a",
        actor_id="user-1",
        scope_kind="workspace",
        scope_id="tenant-a",
        owner_user_id=None,
        execution_id="call-1",
        db=db,
        invocation={"metadata": {"runtime_run_id": "run-1"}},
    )


def test_exact_py_request_discovers_inert_text_artifact_capability_without_embeddings():
    provider = ArtifactProvider()
    definition = next(item for item in provider.capabilities if item.id == "artifact.create_text")
    index = CapabilitySearchIndex(semantic_index=_ZeroSemanticIndex())

    hits = index.search(
        [definition],
        "give write this into a .py file in here",
        limit=4,
    )

    assert hits
    assert hits[0].capability_id == "artifact.create_text"
    # capability_rescue accepts lexical relevance >= 0.75. Keep this exact user
    # regression comfortably above that boundary even when embeddings are degraded.
    assert hits[0].lexical_score >= 0.75


@pytest.mark.asyncio
async def test_create_text_delivers_real_py_filename_as_inert_source(runtime_db):
    provider = ArtifactProvider()
    ctx = _context(runtime_db)
    source = "print('hello from Operly')\n"

    result = await provider.execute(
        ctx,
        "artifact.create_text",
        {
            "filename": "qr_clock.py",
            "content": source,
            # A model must not be able to make source render as active HTML simply
            # by lying about its content type.
            "content_type": "text/html",
        },
    )
    verified = await provider.verify(ctx, "artifact.create_text", {}, result)

    assert verified.success is True
    assert verified.evidence["filename"] == "qr_clock.py"
    assert verified.evidence["inert"] is True
    assert verified.evidence["executed"] is False
    assert verified.evidence["content_type"] == "text/x-python; charset=utf-8"

    artifact_id = verified.evidence["artifact_id"]
    raw = await ArtifactService(runtime_db).read_bytes(
        ArtifactScope("workspace", "tenant-a", tenant_id="tenant-a"),
        artifact_id,
    )
    assert raw.decode("utf-8") == source


@pytest.mark.asyncio
async def test_active_html_content_type_is_downgraded_for_source_artifact(runtime_db):
    provider = ArtifactProvider()
    ctx = _context(runtime_db)
    result = await provider.execute(
        ctx,
        "artifact.create_text",
        {
            "filename": "landing_page.html",
            "content": "<script>alert('not executed')</script>",
            "content_type": "text/html",
        },
    )
    verified = await provider.verify(ctx, "artifact.create_text", {}, result)

    assert verified.success is True
    assert verified.evidence["filename"] == "landing_page.html"
    assert verified.evidence["content_type"] == "text/plain; charset=utf-8"
    assert verified.evidence["inert"] is True
    assert verified.evidence["executed"] is False
