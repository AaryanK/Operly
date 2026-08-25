"""Controller guardrails for semantic scope-pruning convergence.

The model may recommend pruning, but repeated no-op pruning is a controller concern.
This wrapper preserves the model as semantic authority while preventing a validated
atomic contract from cycling forever on the same already-resolved scope finding.
"""
from __future__ import annotations

from typing import Any

from packages.software_projects.planning.live_planning import StructuredModelResult, ValidatorOutput


_SCOPE_DISPOSITIONS = {"prune", "replace_with_minimal_contract"}
_BLOCKING_FIELDS = (
    "missing_information",
    "ambiguous_behavior",
    "missing_inputs",
    "missing_outputs",
    "missing_invariants",
    "missing_dependencies",
    "missing_failure_handling",
    "missing_security_rules",
    "missing_persistence_behavior",
    "missing_tests",
    "requirement_conflicts",
)
_CONCRETE_MECHANISMS = {
    "json", "xml", "csv", "yaml", "database", "sql", "http", "grpc",
    "websocket", "file upload", "pagination", "character encoding",
    "encoding matrix", "rest api", "graphql", "sqlite", "postgresql",
}
_ERROR_WORDS = {"error", "invalid", "division", "zero", "message"}


def _normalized_scope(items: list[str]) -> set[str]:
    return {" ".join(str(item).lower().split()) for item in items if str(item).strip()}


def _linked_requirement_text(context) -> str:
    linked = (context.untrusted_requirements or {}).get("linked") or []
    rows: list[str] = []
    for item in linked:
        if isinstance(item, dict):
            rows.extend(
                str(item.get(key) or "")
                for key in ("source_excerpt", "normalized_requirement", "exactText", "normalizedMeaning")
            )
    return " ".join(rows).lower()


def _required_error_scope(term: str, requirement_text: str) -> bool:
    """Protect minimal error behavior when the user explicitly required it."""
    if not any(word in requirement_text for word in _ERROR_WORDS):
        return False
    if not any(word in term for word in _ERROR_WORDS):
        return False
    return not any(mechanism in term for mechanism in _CONCRETE_MECHANISMS)


def _has_blockers(verdict: ValidatorOutput) -> bool:
    return any(bool(getattr(verdict, field)) for field in _BLOCKING_FIELDS)


def _deterministic_scope_terms(findings: list[str]) -> list[str]:
    """Extract concrete mechanisms from deterministic scope findings.

    The live planner's canonicalizer removes literal scope terms. Validator prose can
    be broader than the deterministic finding (for example "persistent storage"
    while the deterministic guard reports "unjustified scope expansion: database").
    Supplying both makes the first minimal replacement actually remove the offending
    mechanism instead of burning repeated refinement turns.
    """
    terms: list[str] = []
    for finding in findings:
        text = " ".join(str(finding).lower().split())
        if ":" in text and "scope" in text:
            tail = text.split(":", 1)[1].strip()
            if tail:
                terms.append(tail)
        for mechanism in _CONCRETE_MECHANISMS:
            if mechanism in text:
                terms.append(mechanism)
    return list(dict.fromkeys(term for term in terms if term))


class ScopeConvergingPlanningClient:
    """Post-process contradictory/no-op validator prune decisions.

    First, deterministic scope findings are merged into the model's prune target so
    canonicalization removes the exact offending mechanism on the next replacement.
    Once scope is deterministically clean, repeated prune decisions are converted to
    approval when the model itself reports readiness and no other blockers remain.
    """

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.provider = getattr(delegate, "provider", "unknown")
        self.model_id = getattr(delegate, "model_id", "unknown")

    async def generate_structured(self, **kwargs) -> StructuredModelResult:
        result = await self.delegate.generate_structured(**kwargs)
        if kwargs.get("role") != "validator" or not result.structured_output:
            return result

        try:
            verdict = ValidatorOutput.model_validate(result.structured_output)
        except Exception:
            return result
        if verdict.disposition not in _SCOPE_DISPOSITIONS:
            return result

        context = kwargs.get("context")
        if context is None:
            return result

        deterministic = [str(item) for item in (context.constraints or {}).get("deterministic_scope_findings", [])]
        deterministic_terms = _deterministic_scope_terms(deterministic)

        # Make the model's prune target mechanically complete. This does not add
        # semantic scope; it only names mechanisms the deterministic validator has
        # already rejected.
        merged_scope = list(dict.fromkeys([*verdict.irrelevant_scope_expansion, *deterministic_terms]))
        history = list(result.retry_history)
        if merged_scope != verdict.irrelevant_scope_expansion:
            history.append({
                "controller": "scope_convergence",
                "reason": "merge_deterministic_scope_targets",
                "deterministic_scope_findings": deterministic,
                "added_scope_terms": [term for term in deterministic_terms if term not in verdict.irrelevant_scope_expansion],
            })
            verdict = verdict.model_copy(update={
                "disposition": "replace_with_minimal_contract",
                "ready_for_implementation": False,
                "irrelevant_scope_expansion": merged_scope,
                "minimal_contract_guidance": list(dict.fromkeys([
                    *verdict.minimal_contract_guidance,
                    "Remove every deterministic scope target and use only typed OPERLY platform defaults for implementation mechanics.",
                ])),
            })

        current_scope = _normalized_scope(verdict.irrelevant_scope_expansion)
        requirement_text = _linked_requirement_text(context)
        actionable_scope = {
            term for term in current_scope
            if not _required_error_scope(term, requirement_text)
        }

        previous_scope: set[str] = set()
        prior_simplification = False
        for item in context.previous_findings or []:
            if not isinstance(item, dict):
                continue
            if item.get("disposition") in _SCOPE_DISPOSITIONS:
                prior_simplification = True
                previous_scope |= _normalized_scope(item.get("irrelevant_scope_expansion") or [])

        repeated = bool(actionable_scope) and bool(previous_scope) and actionable_scope <= previous_scope
        no_actionable_scope = not actionable_scope and not deterministic
        converged = (prior_simplification and no_actionable_scope) or (repeated and not deterministic)

        if converged and verdict.ready_for_implementation and not _has_blockers(verdict):
            original = verdict.disposition
            verdict = verdict.model_copy(update={
                "disposition": "approve",
                "irrelevant_scope_expansion": [],
            })
            history.append({
                "controller": "scope_convergence",
                "reason": "repeated_or_resolved_scope_prune",
                "original_disposition": original,
                "new_disposition": "approve",
                "deterministic_scope_findings": deterministic,
                "protected_requirement_scope": sorted(current_scope - actionable_scope),
            })

        return result.model_copy(update={
            "structured_output": verdict.model_dump(mode="json"),
            "retry_history": history,
        })
