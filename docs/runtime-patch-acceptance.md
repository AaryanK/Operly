# Acceptance criteria

- Shared workspace surfaces cannot discover, materialize, delegate, or invoke personal-only capabilities/context.
- Missing/unknown surfaces fail closed.
- Session capability views are keyed by trusted surface.
- Initial model tool surface stays to the discovery/runtime kernel; authorized low-risk connectors are not bulk exposed.
- Capability search ranks only candidates already eligible for the current execution/surface authority and returns metadata, not schemas.
- Exact schemas are exposed only after describe/observation.
- Context retrieval supports reference-first discovery and re-authorizes materialization/delegation.
- Final capability execution still crosses the canonical ActionBackedCapabilityFirewall.
- Regression tests cover workspace-shared, personal-private, and unknown surface behavior.
