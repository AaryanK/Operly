"""Safe normalization for structured planner output before retry exhaustion.

The model may occasionally mark a scope claim as a derived essential requirement
without supplying the evidence OPERLY requires. We never invent that evidence.
Instead, unsupported essential claims are demoted to non-blocking implementation
choices and remain subject to the normal deterministic scope/validator checks.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from packages.custom_software.live_planning import FailureClass, StructuredModelResult


ESSENTIAL = "derived_essential_requirement"
IMPLEMENTATION = "implementation_choice"


def _clean_claim(claim: Any) -> Any:
    if not isinstance(claim, dict):
        return claim
    cleaned = dict(claim)
    authority = str(cleaned.get("authority") or "")
    linked = cleaned.get("linked_requirement_ids")
    linked_ok = isinstance(linked, list) and any(str(item).strip() for item in linked)
    justification = str(cleaned.get("justification") or "").strip()

    if authority == ESSENTIAL and (not linked_ok or not justification):
        cleaned["authority"] = IMPLEMENTATION
        cleaned["linked_requirement_ids"] = []
        cleaned["blocks_readiness"] = False
        cleaned["justification"] = (
            "Model supplied no complete evidence for an essential derivation; "
            "OPERLY treats this as a non-blocking implementation choice."
        )
    elif authority in {IMPLEMENTATION, "optional_enhancement"}:
        cleaned["blocks_readiness"] = False
        if not justification:
            cleaned["justification"] = "Non-blocking model-introduced scope subject to validator review."
    elif not justification:
        # Explicit claims may remain explicit, but the normalizer records why the
        # field is populated rather than fabricating product justification.
        cleaned["justification"] = "Planner marked this claim explicit; verify against the linked requirement ledger."
    return cleaned


def _clean_node(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    cleaned = dict(node)
    claims = cleaned.get("scope_claims")
    if isinstance(claims, list):
        cleaned["scope_claims"] = [_clean_claim(item) for item in claims]
    children = cleaned.get("children")
    if isinstance(children, list):
        cleaned["children"] = [_clean_node(item) for item in children]
    return cleaned


def normalize_planner_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    cleaned = dict(payload)
    nodes = cleaned.get("nodes")
    if isinstance(nodes, list):
        cleaned["nodes"] = [_clean_node(item) for item in nodes]
    return cleaned


class NormalizingPlanningClient:
    """Planning-client wrapper that repairs only safe, deterministic shape defects."""

    def __init__(self, inner):
        self.inner = inner
        self.provider = inner.provider
        self.model_id = inner.model_id

    async def generate_structured(self, *, role, context, output_schema, request_id, timeout_seconds, attempt=1):
        result: StructuredModelResult = await self.inner.generate_structured(
            role=role,
            context=context,
            output_schema=output_schema,
            request_id=request_id,
            timeout_seconds=timeout_seconds,
            attempt=attempt,
        )
        if role != "planner" or result.structured_output is not None or not result.raw_response:
            return result
        if result.failure_classification not in {FailureClass.SCHEMA_MISMATCH, FailureClass.MALFORMED_OUTPUT}:
            return result

        try:
            raw = result.raw_response.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
            parsed = json.loads(raw)
            normalized = normalize_planner_payload(parsed)
            validated = output_schema.model_validate(normalized)
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError):
            return result

        return result.model_copy(
            update={
                "structured_output": validated.model_dump(mode="json"),
                "validation_errors": [],
                "failure_classification": None,
            }
        )
