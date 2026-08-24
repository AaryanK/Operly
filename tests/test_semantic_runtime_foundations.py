from __future__ import annotations

from dataclasses import dataclass

from packages.business_brain.agent import AgentService
from packages.capabilities.search_index import CapabilitySearchIndex
from packages.context.broker import ContextBroker
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
