import sys
from datetime import datetime
from types import ModuleType

import pytest

from packages.company.events.service import (
    append_event,
    query_events,
    query_personal_events,
)
from packages.database.company_models import BusinessEventRecord


class _WriteDB:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    async def flush(self):
        for index, row in enumerate(self.rows, start=1):
            if row.id is None:
                row.id = f"event-{index}"
            if row.occurred_at is None:
                row.occurred_at = datetime.utcnow()


class _ScalarRows:
    def all(self):
        return []


class _QueryDB:
    def __init__(self):
        self.statement = None

    async def scalars(self, statement):
        self.statement = statement
        return _ScalarRows()


def _install_task_waker(monkeypatch, calls):
    module = ModuleType("packages.tasks.events")

    async def wake_workspace_tasks(db, event):
        calls.append((db, event))

    module.wake_workspace_tasks = wake_workspace_tasks
    monkeypatch.setitem(sys.modules, "packages.tasks.events", module)


def test_business_event_record_has_exactly_one_scope_owner_contract():
    table = BusinessEventRecord.__table__
    assert table.c.tenant_id.nullable is True
    assert table.c.owner_user_id.nullable is True
    assert table.c.scope_kind.nullable is False
    assert "ck_business_events_scope_owner" in {constraint.name for constraint in table.constraints}
    assert "ix_business_events_owner_type_time" in {index.name for index in table.indexes}


@pytest.mark.asyncio
async def test_personal_event_is_owner_scoped_and_does_not_wake_workspace(monkeypatch):
    wake_calls = []
    _install_task_waker(monkeypatch, wake_calls)
    db = _WriteDB()

    event = await append_event(
        db,
        tenant_id=None,
        owner_user_id="user-1",
        event_type="action.proposed",
        payload={"action_id": "a-1"},
    )

    assert event.scope_kind == "personal"
    assert event.tenant_id is None
    assert event.owner_user_id == "user-1"
    assert wake_calls == []


@pytest.mark.asyncio
async def test_workspace_event_still_wakes_workspace_tasks(monkeypatch):
    wake_calls = []
    _install_task_waker(monkeypatch, wake_calls)
    db = _WriteDB()

    event = await append_event(
        db,
        tenant_id="workspace-1",
        event_type="action.proposed",
    )

    assert event.scope_kind == "workspace"
    assert event.tenant_id == "workspace-1"
    assert event.owner_user_id is None
    assert len(wake_calls) == 1
    assert wake_calls[0][1] is event


@pytest.mark.asyncio
async def test_event_queries_are_scope_isolated():
    personal_db = _QueryDB()
    await query_personal_events(personal_db, "user-1")
    personal_sql = str(
        personal_db.statement.compile(compile_kwargs={"literal_binds": True})
    ).lower()
    assert "scope_kind = 'personal'" in personal_sql
    assert "owner_user_id = 'user-1'" in personal_sql

    workspace_db = _QueryDB()
    await query_events(workspace_db, "workspace-1")
    workspace_sql = str(
        workspace_db.statement.compile(compile_kwargs={"literal_binds": True})
    ).lower()
    assert "scope_kind = 'workspace'" in workspace_sql
    assert "tenant_id = 'workspace-1'" in workspace_sql


def test_event_scope_rejects_ambiguous_ownership():
    db = _WriteDB()

    async def both():
        await append_event(
            db,
            tenant_id="workspace-1",
            owner_user_id="user-1",
            event_type="invalid",
        )

    async def neither():
        await append_event(db, tenant_id=None, event_type="invalid")

    with pytest.raises(ValueError, match="exactly one"):
        import asyncio

        asyncio.run(both())
    with pytest.raises(ValueError, match="exactly one"):
        import asyncio

        asyncio.run(neither())
