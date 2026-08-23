"""Context-local model-client decorators above the shared role runtime.

Product code can opt into request-scoped instrumentation without monkeypatching a
module-global factory or constructing provider clients. The underlying execution
authority remains ``model_chat_client_for_role`` / ``ModelPool``.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Callable

from packages.model_runtime.registry import model_chat_client_for_role as _base_client_for_role

ClientDecorator = Callable[[Any], Any]
_DECORATOR: ContextVar[ClientDecorator | None] = ContextVar(
    "operly_model_client_decorator",
    default=None,
)


def begin_client_decorator(decorator: ClientDecorator) -> Token:
    return _DECORATOR.set(decorator)


def end_client_decorator(token: Token) -> None:
    _DECORATOR.reset(token)


def model_chat_client_for_role(role: str, **kwargs):
    """Resolve the normal shared role client, then apply a context-local wrapper."""
    client = _base_client_for_role(role, **kwargs)
    decorator = _DECORATOR.get()
    return decorator(client) if decorator is not None else client
