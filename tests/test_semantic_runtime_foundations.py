from __future__ import annotations

import os
from dataclasses import dataclass
from unittest.mock import patch

from packages.agents.capability_rescue import has_execution_evidence
from packages.agents.runtime import AgentTraceEntry
from packages.business_brain.agent import AgentService
from packages.capabilities.search_index import CapabilitySearchIndex
from packages.context.broker import ContextBroker
from packages.model_runtime import (
    InferenceRequest,
    TaskRouteDecision,
    model_for_requirements,
    requirements_for_task,
)
from packages.model_runtime.contracts import ModelSelector
from packages.model_runtime.registry import ModelRegistry
from packages.model_runtime.requirements import AdaptiveRequirementsModel, _eligible_models
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


class FailIfEmbeddedBackend:
    name = "fail-if-called"
    degraded_reason = None

    def embed_documents(self, texts):
        raise AssertionError(f"lexical fast path unexpectedly embedded {len(texts)} documents")

    def embed_query(self, text):
        raise AssertionError(f"lexical fast path unexpectedly embedded query: {text}")


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
    assert hits[0].strategy == "hybrid_semantic"


def test_capability_search_exact_match_skips_embedding_backend_entirely():
    index = CapabilitySearchIndex(
        semantic_index=SemanticTextIndex(backend=FailIfEmbeddedBackend())
    )
    allowed = [
        _Definition(
            id="task.create",
            name="task_create",
            display_name="Create durable task",
            text="create durable workflow task",
            category="tasks",
            tags=frozenset({"workflow", "tasks"}),
        ),
        _Definition(
            id="crm.create_lead",
            name="crm_create_lead",
            display_name="Create CRM lead",
            text="create a CRM lead",
            category="crm",
        ),
    ]

    hits = index.search(allowed, "task.create", limit=10)

    assert hits
    assert hits[0].capability_id == "task.create"
    assert hits[0].strategy == "lexical_fast_path"
    assert hits[0].semantic_score == 0.0


def test_event_discovery_does_not_count_as_root_execution_evidence():
    trace = [
        AgentTraceEntry("event.search", {"query": "crm contact"}, {"ok": True}),
        AgentTraceEntry("event.describe", {"event_id": "crm.contact.created"}, {"ok": True}),
    ]
    assert has_execution_evidence(trace) is False


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
    injected_personal = type(
        "Request",
        (),
        {"metadata": {"_surface_kind": "personal_private"}, "channel": "web"},
    )()
    direct_discord = type(
        "Request",
        (),
        {"metadata": {"_surface_kind": "discord_dm", "is_direct": True}, "channel": "discord"},
    )()
    assert AgentService._surface_for(shared) is SurfaceKind.WORKSPACE_SHARED
    assert AgentService._surface_for(direct) is SurfaceKind.WORKSPACE_PRIVATE
    assert AgentService._surface_for(injected_personal) is SurfaceKind.WORKSPACE_SHARED
    assert AgentService._surface_for(direct_discord) is SurfaceKind.WORKSPACE_PRIVATE
    assert not AgentService._surface_for(direct_discord).allows_personal_global


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


def test_real_catalog_requirements_route_stays_small_and_non_heavy_through_failover():
    """Inspect the eligible pool behind the live requirements facade."""
    provider_env = {
        "OPEN_ROUTER_API": "test-openrouter",
        "OLLAMA_API_KEY": "test-ollama",
        "groq_api_key": "test-groq",
        "gemini_api_key": "test-gemini",
        "nvidia_api_key": "test-nvidia",
        "OPERLY_MODEL_AUTO_PORTFOLIO": "1",
    }
    tool_schema = {
        "type": "function",
        "function": {
            "name": "capability.search",
            "description": "Discover an authorized capability",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    request = InferenceRequest(
        messages=({"role": "user", "content": "help me with this routine task"},),
        tools=(tool_schema,),
    )
    decision = TaskRouteDecision(
        task_type="business_reasoning",
        role="business_agent",
        tool_policy="progressive_capability_access",
        confidence=1.0,
        reason="test routine primary-worker route",
    )
    requirements = requirements_for_task(decision, request)
    assert "heavy" in requirements.avoid_tags

    with patch.dict(os.environ, provider_env, clear=False):
        selected = model_for_requirements(requirements, fallback_role="business_agent")
        pool = _eligible_models(requirements)

    assert isinstance(selected, AdaptiveRequirementsModel)
    assert selected.requirements == requirements
    assert pool
    assert "tools" in pool[0].capabilities
    assert "small" in pool[0].tags
    # The architecture contract is tag/capability driven. Concrete route IDs can be
    # reclassified or promoted by operator catalog metadata without changing it.
    assert all("heavy" not in model.tags for model in pool)


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
