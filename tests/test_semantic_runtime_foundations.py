from __future__ import annotations

import os
from dataclasses import dataclass
from unittest.mock import patch

from packages.business_brain.agent import AgentService
from packages.capabilities.search_index import CapabilitySearchIndex
from packages.context.broker import ContextBroker
from packages.model_runtime import ModelPool, model_for_role
from packages.model_runtime.contracts import ModelSelector
from packages.model_runtime.registry import ModelRegistry
from packages.model_runtime.routing_policy import role_routing_profile
from packages.retrieval.semantic import SemanticDocument, SemanticTextIndex
from packages.security.surfaces import SurfaceKind


class FakeEmbeddingBackend:
    """Tiny injected backend proving ranking uses embeddings, not token overlap."""

    name = "fake:test"
    degraded_reason = None

    _vectors = {
        "need-a-meeting": (1.0, 0.0, 0.0),
        "calendar-operation": (1.0, 0.0, 0.0),
        "unrelated-operation": (0.0, 1.0, 0.0),
        "customer-renewal": (0.0, 0.0, 1.0),
        "renewal-record": (0.0, 0.0, 1.0),
    }

    def embed_documents(self, texts):
        return [self._vectors.get(str(text), (0.0, 1.0, 0.0)) for text in texts]

    def embed_query(self, text):
        return self._vectors.get(str(text), (0.0, 1.0, 0.0))


def test_semantic_text_index_uses_injected_embedding_backend():
    index = SemanticTextIndex(backend=FakeEmbeddingBackend())
    matches = index.rank(
        [
            SemanticDocument("calendar", "calendar-operation"),
            SemanticDocument("other", "unrelated-operation"),
        ],
        "need-a-meeting",
        limit=2,
    )
    assert matches[0].key == "calendar"
    assert matches[0].score > matches[1].score
    assert index.backend_name == "fake:test"


@dataclass
class _Definition:
    id: str
    text: str
    name: str = ""
    display_name: str = ""
    category: str = ""
    tags: frozenset[str] = frozenset()
    semantic_operations: frozenset[str] = frozenset()

    def discovery_document(self):
        return self.text


def test_capability_search_semantic_backend_never_adds_candidates():
    index = CapabilitySearchIndex(
        semantic_index=SemanticTextIndex(backend=FakeEmbeddingBackend())
    )
    allowed = [
        _Definition(
            id="calendar.create_event",
            name="calendar_create_event",
            text="calendar-operation",
        )
    ]
    hits = index.search(allowed, "need-a-meeting", limit=10)
    assert [hit.capability_id for hit in hits] == ["calendar.create_event"]
    assert all(hit.capability_id in {definition.id for definition in allowed} for hit in hits)


def test_context_authority_predicate_fails_closed_without_read_authority():
    predicate = ContextBroker._allowed_predicate(
        tenant_id="workspace-1",
        user_id="user-1",
        conversation_id="conversation-1",
        authority=set(),
        surface=SurfaceKind.PERSONAL_PRIVATE,
    )
    assert predicate is None


def test_shared_workspace_context_predicate_does_not_include_private_human_scope():
    predicate = ContextBroker._allowed_predicate(
        tenant_id="workspace-1",
        user_id="user-1",
        conversation_id="conversation-1",
        authority={"context:tenant:read", "context:conversation:read", "context:human:read"},
        surface=SurfaceKind.WORKSPACE_SHARED,
    )
    sql = str(predicate).lower()
    assert "tenant" in sql
    assert "conversation" in sql
    # A shared surface can have context:human:read in its role permission set, but
    # the trusted surface policy must prevent a human-private branch from being built.
    assert "scope_type = :scope_type_3" not in sql


def test_workspace_agent_surface_is_explicit_and_never_account_private():
    shared = type("Request", (), {"metadata": {}, "channel": "web"})()
    direct = type("Request", (), {"metadata": {"is_direct": True}, "channel": "web"})()
    assert AgentService._surface_for(shared) is SurfaceKind.WORKSPACE_SHARED
    assert AgentService._surface_for(direct) is SurfaceKind.WORKSPACE_PRIVATE
    assert AgentService._surface_for(direct) is not SurfaceKind.PERSONAL_PRIVATE


def test_normal_business_worker_prefers_small_fast_tool_model_over_heavy_model():
    registry = ModelRegistry()
    small = registry.configure(
        id="small-worker",
        provider="test",
        model="small-worker",
        capabilities={"text", "reasoning", "tools"},
        tags={"orchestrator", "small", "fast", "verified", "reliable", "free"},
        priority=20,
    )
    registry.configure(
        id="heavy-reasoner",
        provider="test",
        model="heavy-reasoner",
        capabilities={"text", "reasoning", "tools"},
        tags={"orchestrator", "heavy", "verified", "reliable"},
        priority=1,
    )
    selected = registry.resolve(role_routing_profile("business_agent").selector())
    assert selected.id == small.id


def test_real_catalog_business_agent_starts_with_small_tool_model_when_available():
    provider_env = {
        "OPEN_ROUTER_API": "test-openrouter",
        "OLLAMA_API_KEY": "test-ollama",
        "groq_api_key": "test-groq",
        "gemini_api_key": "test-gemini",
        "nvidia_api_key": "test-nvidia",
        "OPERLY_MODEL_AUTO_PORTFOLIO": "1",
    }
    with patch.dict(os.environ, provider_env, clear=False):
        model = model_for_role("business_agent")

    first = model.models[0] if isinstance(model, ModelPool) else model
    assert "tools" in first.capabilities
    assert "small" in first.tags
    assert "heavy" not in first.tags
    assert first.provider_model_id != "stealth/ox-alpha"


def test_deep_reasoning_selector_prefers_heavy_and_excludes_small():
    registry = ModelRegistry()
    registry.configure(
        id="small-worker",
        provider="test",
        model="small-worker",
        capabilities={"text", "reasoning"},
        tags={"small", "fast", "verified", "reliable", "free"},
        priority=1,
    )
    heavy = registry.configure(
        id="heavy-reasoner",
        provider="test",
        model="heavy-reasoner",
        capabilities={"text", "reasoning"},
        tags={"heavy", "reasoning", "verified", "reliable"},
        priority=50,
    )
    selected = registry.resolve(
        ModelSelector(
            requires=frozenset({"reasoning"}),
            prefer_tags=frozenset({"heavy", "reasoning", "reliable"}),
            avoid_tags=frozenset({"small"}),
            prefer_free=False,
        )
    )
    assert selected.id == heavy.id
