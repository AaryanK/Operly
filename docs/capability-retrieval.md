# Capability retrieval

Runtime 1.0 capability discovery is authorization-first and ranking-second.

`CapabilityRegistry.search(..., effective_only=True)` first limits candidates to the capabilities visible on the current surface and permitted by the trusted `ExecutionContext`. Search ranking cannot create authority, and Kernel independently re-resolves and re-authorizes a capability at execution.

The Runtime 1.0 Objective IR emits a compact query of the form:

`<objective> | resources <semantic resources> | operations <semantic operations>`

The registry uses deterministic lexical ranking plus a deliberately small semantic normalization layer for common resource vocabulary (for example email/mail/Gmail/mailbox) and operation vocabulary (for example retrieve/search/read). Resource matches are weighted more strongly than generic operation matches so unrelated read/list capabilities do not crowd out the correct resource family. Read-only capabilities receive a modest preference for explicit retrieval operations.

This is intentionally not an embedding or model-based authorization mechanism. Future semantic retrieval may use embeddings or a learned reranker to improve recall, but any such component must rank only the already-authorized candidate set and must not become a source of permissions, scope, provider credentials, or execution authority.
