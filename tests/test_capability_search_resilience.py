from packages.capabilities.contracts import CapabilityDefinition
from packages.capabilities.search_index import CapabilitySearchIndex


class BrokenSemanticIndex:
    backend_name = "broken-test-backend"
    degraded_reason = None

    def rank(self, documents, query, *, limit=8, min_score=None):
        del documents, query, limit, min_score
        raise RuntimeError("semantic backend unavailable")


def _capability(
    capability_id: str,
    description: str,
    *,
    operations: tuple[str, ...] = (),
) -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        name=capability_id.replace(".", "_"),
        description=description,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        semantic_operations=frozenset(operations),
    )


def test_semantic_failure_degrades_to_lexical_candidates_instead_of_raising():
    index = CapabilitySearchIndex(semantic_index=BrokenSemanticIndex())
    definitions = [
        _capability(
            "gmail.send_email",
            "Send an email message through the linked Gmail account.",
            operations=("send email", "compose email"),
        ),
        _capability(
            "crm.contact.get",
            "Read a CRM contact record.",
            operations=("read contact",),
        ),
    ]

    hits = index.search(definitions, "email", limit=8)

    assert hits
    assert hits[0].capability_id == "gmail.send_email"
    assert all(hit.strategy == "lexical_degraded" for hit in hits)
    assert index.degraded_reason == "semantic_search_failed:RuntimeError"


def test_semantic_failure_with_no_lexical_match_returns_empty_search_result():
    index = CapabilitySearchIndex(semantic_index=BrokenSemanticIndex())
    definitions = [
        _capability(
            "crm.contact.get",
            "Read a CRM contact record.",
            operations=("read contact",),
        )
    ]

    hits = index.search(definitions, "astronomy", limit=8)

    assert hits == []
    assert index.degraded_reason == "semantic_search_failed:RuntimeError"
