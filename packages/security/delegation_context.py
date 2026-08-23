from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DelegationUseContext:
    user_id: str | None
    tenant_id: str
    capability_id: str
    action_id: str | None


_CURRENT: ContextVar[DelegationUseContext | None] = ContextVar("operly_delegation_use", default=None)


def current_delegation_use() -> DelegationUseContext | None:
    return _CURRENT.get()


@contextmanager
def delegation_use_context(value: DelegationUseContext):
    token = _CURRENT.set(value)
    try:
        yield
    finally:
        _CURRENT.reset(token)
