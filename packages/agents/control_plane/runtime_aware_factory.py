"""Application-controlled runtime facts for the strict Operly Factory."""
from __future__ import annotations

from typing import Any, Iterable

from packages.database.db import session_scope
from packages.security.temporal_context import resolve_temporal_context

from .contracts import StageWorkerResult
from .safe_factory import SafeAgentFactoryControlPlane


class RuntimeAwareAgentFactoryControlPlane(SafeAgentFactoryControlPlane):
    """Inject canonical temporal facts before Factory context compilation.

    Relative time is operational state, not ambient history.  Workers receive the
    actor/workspace clocks as application-authored facts so intents such as "tomorrow"
    never need semantic retrieval over old workspace messages.
    """

    @staticmethod
    async def _with_runtime_facts(
        metadata: dict[str, Any],
        facts: dict[str, Any] | None,
    ) -> dict[str, Any]:
        output = dict(facts or {})
        if isinstance(output.get("temporal_context"), dict):
            return output
        tenant_id = str(metadata.get("tenant_id") or "").strip() or None
        user_id = str(metadata.get("user_id") or "").strip() or None
        if not tenant_id and not user_id:
            return output
        async with session_scope() as db:
            temporal = await resolve_temporal_context(
                db,
                user_id=user_id,
                tenant_id=tenant_id,
            )
        output["temporal_context"] = temporal.as_dict()
        return output

    async def run(
        self,
        *,
        objective: str,
        metadata: dict[str, Any],
        ingress_metadata: dict[str, Any] | None = None,
        initial_context_refs: set[str] | None = None,
        initial_artifact_refs: set[str] | None = None,
        stage_input_artifact_refs: dict[str, Iterable[str]] | None = None,
        facts: dict[str, Any] | None = None,
    ):
        return await super().run(
            objective=objective,
            metadata=metadata,
            ingress_metadata=ingress_metadata,
            initial_context_refs=initial_context_refs,
            initial_artifact_refs=initial_artifact_refs,
            stage_input_artifact_refs=stage_input_artifact_refs,
            facts=await self._with_runtime_facts(metadata, facts),
        )

    async def resume(
        self,
        *,
        runtime_run_id: str,
        metadata: dict[str, Any],
        stage_result: StageWorkerResult,
        stage_id: str | None = None,
        facts: dict[str, Any] | None = None,
    ):
        return await super().resume(
            runtime_run_id=runtime_run_id,
            metadata=metadata,
            stage_result=stage_result,
            stage_id=stage_id,
            facts=await self._with_runtime_facts(metadata, facts),
        )
