"""Runtime view of empirical model-route qualification evidence.

The benchmark harness measures concrete provider + model routes.  This module turns
that evidence into conservative routing hints for the existing ModelRegistry rather
than creating a second scheduler.  Positive deep evidence may promote capabilities
(for example a route that proved persistent tool calling); inconclusive provider
failures never count against model quality, and advertised capabilities are never
removed from one failed probe.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Any, Iterable

from packages.model_runtime.catalog import model_resources, register_model_resource


_TRANSIENT_CLASSIFICATIONS = frozenset(
    {
        "rate_limited",
        "quota_or_credits",
        "provider_5xx",
        "provider_error",
        "response_timeout",
        "request_too_large",
    }
)

# This seed is evidence from the production-key deep qualification run performed on
# 2026-08-24.  Planning hit Groq capacity and is therefore intentionally not promoted.
# Future benchmark reports can augment/replace this evidence through
# OPERLY_MODEL_QUALIFICATION_JSON without code changes.
_BOOTSTRAP_REPORTS: tuple[dict[str, Any], ...] = (
    {
        "resourceId": "groq:qwen/qwen3.6-27b",
        "provider": "groq",
        "modelId": "qwen/qwen3.6-27b",
        "source": "operly-deep-2026-08-24",
        "cases": [
            {"name": "availability", "status": "pass"},
            {"name": "structured_json", "status": "pass"},
            {"name": "reasoning", "status": "pass"},
            {"name": "tool_single", "status": "pass"},
            {"name": "tool_multi", "status": "pass"},
            {"name": "coding", "status": "pass"},
            {"name": "repair", "status": "pass"},
            {"name": "planning", "status": "inconclusive", "classification": "rate_limited"},
        ],
    },
)


@dataclass(frozen=True, slots=True)
class RouteQualification:
    resource_id: str
    provider: str
    model_id: str
    source: str
    cases: tuple[tuple[str, str], ...]

    def status(self, case_name: str) -> str | None:
        wanted = str(case_name or "").strip().lower()
        for name, status in self.cases:
            if name == wanted:
                return status
        return None

    @property
    def passed_cases(self) -> frozenset[str]:
        return frozenset(name for name, status in self.cases if status == "pass")

    @property
    def verified_capabilities(self) -> frozenset[str]:
        passed = self.passed_cases
        capabilities: set[str] = set()
        if "availability" in passed:
            capabilities.add("text")
        if "structured_json" in passed:
            capabilities.add("structured_output")
        if "reasoning" in passed:
            capabilities.add("reasoning")
        if {"tool_single", "tool_multi"}.issubset(passed):
            capabilities.add("tools")
        if "coding" in passed:
            capabilities.add("coding")
        if "repair" in passed:
            capabilities.add("repair")
        if "planning" in passed:
            capabilities.add("planning")
        return frozenset(capabilities)

    @property
    def routing_tags(self) -> frozenset[str]:
        return frozenset(
            {"qualified"}
            | {f"qualified-{capability.replace('_', '-')}" for capability in self.verified_capabilities}
        )

    def task_score(self, task: str) -> int:
        """Return a small evidence score for observability/tests and future policy.

        Runtime ranking currently consumes the derived ``qualified-*`` tags through
        the existing ModelRegistry sort. Keeping this score here makes the evidence
        semantics explicit without introducing a parallel routing engine.
        """
        key = str(task or "").strip().lower()
        weights: dict[str, tuple[tuple[str, int], ...]] = {
            "ai.generate": (("availability", 2), ("structured_json", 1)),
            "ai.reason": (("reasoning", 4), ("structured_json", 1)),
            "ai.plan": (("planning", 6), ("reasoning", 2), ("structured_json", 1)),
            "ai.code.generate": (("coding", 6), ("tool_multi", 2), ("reasoning", 1)),
            "ai.code.repair": (("repair", 7), ("coding", 3), ("tool_multi", 2), ("reasoning", 1)),
            "ai.code.review": (("coding", 3), ("reasoning", 3), ("structured_json", 1)),
            "ai.extract.requirements": (("reasoning", 3), ("structured_json", 3)),
        }
        score = 0
        for case_name, weight in weights.get(key, ()):
            status = self.status(case_name)
            if status == "pass":
                score += weight
            elif status == "fail":
                score -= weight
        return score


def _case_status(case: dict[str, Any]) -> str:
    explicit = str(case.get("status") or "").strip().lower()
    if explicit in {"pass", "fail", "inconclusive"}:
        return explicit
    if bool(case.get("passed")):
        return "pass"
    classification = str(case.get("classification") or "").strip().lower()
    if classification in _TRANSIENT_CLASSIFICATIONS:
        return "inconclusive"
    return "fail"


def _report(value: dict[str, Any]) -> RouteQualification | None:
    resource_id = str(value.get("resourceId") or value.get("resource_id") or "").strip()
    provider = str(value.get("provider") or "").strip().lower()
    model_id = str(value.get("modelId") or value.get("model_id") or "").strip()
    if not resource_id and provider and model_id:
        resource_id = f"{provider}:{model_id}"
    if not resource_id:
        return None
    if not provider:
        provider = resource_id.partition(":")[0].strip().lower()
    if not model_id and ":" in resource_id:
        model_id = resource_id.split(":", 1)[1]
    raw_cases = value.get("cases") or []
    if not isinstance(raw_cases, list):
        return None
    cases: dict[str, str] = {}
    for item in raw_cases:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        if name:
            cases[name] = _case_status(item)
    return RouteQualification(
        resource_id=resource_id,
        provider=provider,
        model_id=model_id,
        source=str(value.get("source") or "runtime-config").strip() or "runtime-config",
        cases=tuple(sorted(cases.items())),
    )


def _configured_reports() -> list[dict[str, Any]]:
    raw = os.getenv("OPERLY_MODEL_QUALIFICATION_JSON", "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        # Qualification is an optimization/evidence layer, not an authority boundary.
        # A malformed optional override must not take the model runtime down.
        return []
    if isinstance(value, dict) and isinstance(value.get("reports"), list):
        return [item for item in value["reports"] if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        rows: list[dict[str, Any]] = []
        for resource_id, item in value.items():
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row.setdefault("resourceId", str(resource_id))
            rows.append(row)
        return rows
    return []


def qualification_profiles() -> dict[str, RouteQualification]:
    profiles: dict[str, RouteQualification] = {}
    for raw in (*_BOOTSTRAP_REPORTS, *_configured_reports()):
        parsed = _report(raw)
        if parsed is not None:
            profiles[parsed.resource_id] = parsed
    return profiles


def qualification_for(resource_id: str) -> RouteQualification | None:
    return qualification_profiles().get(str(resource_id or "").strip())


def apply_model_qualification_overrides() -> None:
    """Promote only capabilities that concrete routes have empirically demonstrated.

    Registered catalog resources have the highest normal catalog precedence, so this
    is a small overlay on the existing resource system. No new registry, worker, or
    routing service is introduced.
    """
    profiles = qualification_profiles()
    if not profiles:
        return
    resources = {f"{item.provider}:{item.id}": item for item in model_resources()}
    promotable = frozenset({"text", "reasoning", "coding", "tools"})
    for resource_id, profile in profiles.items():
        resource = resources.get(resource_id)
        if resource is None:
            continue
        capabilities = set(resource.capabilities)
        capabilities.update(profile.verified_capabilities & promotable)
        tags = set(resource.tags)
        tags.update(profile.routing_tags)
        register_model_resource(
            replace(
                resource,
                capabilities=frozenset(capabilities),
                tags=frozenset(tags),
            ),
            replace=True,
        )


def qualification_preference_tags(
    capability: str,
    existing_preference_tags: Iterable[str] = (),
) -> frozenset[str]:
    """Translate a provider-neutral model request into measured-evidence preferences."""
    clean_capability = str(capability or "").strip().lower()
    existing = {str(item).strip().lower() for item in existing_preference_tags if str(item).strip()}
    tags: set[str] = set()
    if clean_capability == "coding":
        tags.add("qualified-coding")
        if "reasoning" in existing:
            tags.add("qualified-repair")
    elif clean_capability == "reasoning":
        if "heavy" in existing or "long-context" in existing or "planning" in existing:
            tags.add("qualified-planning")
        else:
            tags.add("qualified-reasoning")
    return frozenset(tags)
