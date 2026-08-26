"""Validation execution for factory acceptance contracts.

Deterministic checks run locally over typed worker evidence when possible. More complex
file/runtime checks (for example inspecting a PDF) are delegated to a supplied bounded
sandbox callback. Semantic judgment is an explicit last resort and cannot be confused
with deterministic truth.
"""
from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from .contracts import StageSpec, StageWorkerResult, ValidatorKind, ValidatorSpec


PythonTestExecutor = Callable[
    [str, list[str], dict[str, Any]],
    Awaitable[dict[str, Any]] | dict[str, Any],
]
SemanticValidator = Callable[
    [ValidatorSpec, StageSpec, StageWorkerResult],
    Awaitable[dict[str, Any]] | dict[str, Any],
]


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _lookup(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in str(path or "").split("."):
        if not part:
            continue
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class ControlPlaneValidator:
    def __init__(
        self,
        *,
        python_test: PythonTestExecutor | None = None,
        semantic: SemanticValidator | None = None,
    ) -> None:
        self.python_test = python_test
        self.semantic = semantic

    @staticmethod
    def _worker_status(spec: ValidatorSpec, result: StageWorkerResult) -> dict[str, Any]:
        observed = str(result.status or "").lower()
        forbidden = {
            str(item).lower() for item in spec.expected.get("not_in", ["failed", "blocked"])
        }
        return {
            "passed": observed not in forbidden,
            "expected": spec.expected,
            "observed": observed,
            "retryable": True,
        }

    @staticmethod
    def _artifact_exists(spec: ValidatorSpec, result: StageWorkerResult) -> dict[str, Any]:
        expected_min = int(spec.expected.get("min", 1) or 1)
        observed = len(result.artifacts)
        return {
            "passed": observed >= expected_min,
            "expected": {"min": expected_min},
            "observed": observed,
            "evidence_refs": list(result.evidence_refs),
            "retryable": True,
        }

    @staticmethod
    def _artifact_count(spec: ValidatorSpec, result: StageWorkerResult) -> dict[str, Any]:
        expected = spec.expected.get("count")
        observed = len(result.artifacts)
        return {
            "passed": expected is not None and observed == int(expected),
            "expected": expected,
            "observed": observed,
            "evidence_refs": list(result.evidence_refs),
            "retryable": True,
        }

    @staticmethod
    def _evidence_present(spec: ValidatorSpec, result: StageWorkerResult) -> dict[str, Any]:
        path = str(spec.parameters.get("path") or spec.expected.get("path") or "").strip()
        if path:
            observed = _lookup(result.evidence, path)
            passed = observed is not None
        else:
            observed = result.evidence
            passed = bool(result.evidence or result.evidence_refs)
        return {
            "passed": passed,
            "expected": spec.expected or {"evidence": "present"},
            "observed": observed,
            "evidence_refs": list(result.evidence_refs),
            "retryable": True,
        }

    @staticmethod
    def _field_equals(spec: ValidatorSpec, result: StageWorkerResult) -> dict[str, Any]:
        path = str(spec.parameters.get("path") or spec.expected.get("path") or "").strip()
        expected = spec.expected.get("value")
        observed = _lookup(result.evidence, path)
        return {
            "passed": bool(path) and observed == expected,
            "expected": expected,
            "observed": observed,
            "evidence_refs": list(result.evidence_refs),
            "retryable": True,
        }

    @staticmethod
    def _field_gte(spec: ValidatorSpec, result: StageWorkerResult) -> dict[str, Any]:
        path = str(spec.parameters.get("path") or spec.expected.get("path") or "").strip()
        expected = spec.expected.get("min")
        observed = _lookup(result.evidence, path)
        passed = False
        try:
            passed = bool(path) and observed is not None and float(observed) >= float(expected)
        except (TypeError, ValueError):
            passed = False
        return {
            "passed": passed,
            "expected": expected,
            "observed": observed,
            "evidence_refs": list(result.evidence_refs),
            "retryable": True,
        }

    @staticmethod
    def _provider_verified(spec: ValidatorSpec, result: StageWorkerResult) -> dict[str, Any]:
        verification = result.evidence.get("verification")
        if isinstance(verification, dict):
            observed = bool(verification.get("success") or verification.get("verified"))
        else:
            observed = bool(result.evidence.get("verified"))
        return {
            "passed": observed,
            "expected": True,
            "observed": observed,
            "evidence_refs": list(result.evidence_refs),
            "retryable": True,
        }

    async def __call__(
        self,
        spec: ValidatorSpec,
        stage: StageSpec,
        result: StageWorkerResult,
    ) -> dict[str, Any]:
        if spec.validator == "worker_status":
            return self._worker_status(spec, result)
        if spec.validator == "artifact_exists":
            return self._artifact_exists(spec, result)
        if spec.validator == "artifact_count":
            return self._artifact_count(spec, result)
        if spec.validator == "evidence_present":
            return self._evidence_present(spec, result)
        if spec.validator == "field_equals":
            return self._field_equals(spec, result)
        if spec.validator == "field_gte":
            return self._field_gte(spec, result)
        if spec.validator == "provider_verified":
            return self._provider_verified(spec, result)

        if spec.validator == "python_test":
            if self.python_test is None:
                return {
                    "passed": False,
                    "expected": spec.expected,
                    "observed": "python_validator_unavailable",
                    "failure_class": "validator_unavailable",
                    "retryable": False,
                }
            intent = str(spec.parameters.get("test_intent") or spec.criterion).strip()[:3000]
            outcome = dict(
                await _resolve(
                    self.python_test(intent, list(result.artifacts), dict(result.evidence))
                )
                or {}
            )
            outcome.setdefault("expected", spec.expected)
            outcome.setdefault("evidence_refs", list(result.evidence_refs))
            return outcome

        if spec.kind is ValidatorKind.SEMANTIC or spec.validator == "semantic_evidence":
            if self.semantic is None:
                return {
                    "passed": False,
                    "expected": spec.expected,
                    "observed": "semantic_validator_unavailable",
                    "failure_class": "validator_unavailable",
                    "retryable": False,
                }
            outcome = dict(await _resolve(self.semantic(spec, stage, result)) or {})
            outcome.setdefault("expected", spec.expected)
            outcome.setdefault("evidence_refs", list(result.evidence_refs))
            return outcome

        return {
            "passed": False,
            "expected": spec.expected,
            "observed": f"unknown_validator:{spec.validator}",
            "failure_class": "validator_unavailable",
            "retryable": False,
        }
