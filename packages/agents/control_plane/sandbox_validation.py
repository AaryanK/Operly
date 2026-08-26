"""Compile deterministic test intent into bounded sandbox validation code."""
from __future__ import annotations

import inspect
import json
import re
from typing import Any, Awaitable, Callable

from packages.model_runtime import InferenceBudget, InferenceRequest
from packages.model_runtime.registry import model_for_role


CapabilityInvoke = Callable[[str, dict[str, Any], str | None], Awaitable[dict[str, Any]] | dict[str, Any]]
ExposeCapability = Callable[[str], Awaitable[bool] | bool]

_PREFIX = "OPERLY_VALIDATOR_RESULT="


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _extract_code(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:python)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _parse_stdout(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        if not line.startswith(_PREFIX):
            continue
        try:
            value = json.loads(line[len(_PREFIX) :])
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


class SandboxPythonValidator:
    """Use the isolated Agent Computer for objective checks over result artifacts.

    The code-generation model sees only the test intent and tiny evidence summary. It
    never receives workspace credentials or raw artifact bytes. The generated code
    executes in the same network-isolated, credential-free Agent Computer used by
    governed computation capabilities.
    """

    def __init__(
        self,
        *,
        invoke: CapabilityInvoke,
        expose: ExposeCapability | None = None,
    ) -> None:
        self.invoke = invoke
        self.expose = expose

    async def _compile(self, intent: str, evidence: dict[str, Any]) -> str:
        model = model_for_role("requirements_analyst")
        system = (
            "You write a deterministic Python acceptance test for OPERLY. Output Python code only, no markdown or reasoning. "
            "Input artifacts are mounted read-only under /workspace/input with their original filenames. No network is available. "
            "Inspect only those files. Use installed libraries when helpful (PyMuPDF/fitz, pypdf, Pillow, openpyxl, python-docx, python-pptx, pandas, etc.). "
            "The script MUST always finish by printing exactly one line beginning OPERLY_VALIDATOR_RESULT= followed by JSON with keys: "
            "passed (boolean), observed (JSON-safe value), expected (JSON-safe value), and optional failure_class. "
            "Catch inspection errors and return passed=false rather than hiding them. Do not modify inputs or rely on model judgment."
        )
        payload = {
            "test_intent": intent[:4000],
            "worker_evidence": {
                key: value
                for key, value in evidence.items()
                if key in {
                    "status",
                    "count",
                    "row_count",
                    "processed_count",
                    "page_count",
                    "delivery_status",
                    "verified",
                }
            },
            "result_prefix": _PREFIX,
        }
        result = await model.infer(
            InferenceRequest(
                messages=(
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
                ),
                budget=InferenceBudget(
                    timeout_seconds=12.0,
                    attempts_per_model=1,
                    max_models=2,
                    max_output_tokens=2200,
                ),
                metadata={"runtime_component": "factory_python_validator_compiler"},
            )
        )
        code = _extract_code(str(result.message.get("content") or ""))
        if not code or _PREFIX not in code:
            raise ValueError("Validator compiler did not emit the required result contract")
        return code[:80_000]

    async def __call__(
        self,
        intent: str,
        artifact_ids: list[str],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        if not artifact_ids:
            return {
                "passed": False,
                "observed": "no_result_artifact",
                "failure_class": "missing_artifact",
                "retryable": True,
            }
        if self.expose is not None:
            allowed = bool(await _resolve(self.expose("computer.run_python")))
            if not allowed:
                return {
                    "passed": False,
                    "observed": "computer.run_python unavailable under current authority",
                    "failure_class": "validator_unavailable",
                    "retryable": False,
                }
        try:
            code = await self._compile(intent, evidence)
        except (LookupError, RuntimeError, TypeError, ValueError) as error:
            return {
                "passed": False,
                "observed": f"validator_compile_failed:{type(error).__name__}",
                "failure_class": "validator_compile_failed",
                "retryable": True,
            }
        result = dict(
            await _resolve(
                self.invoke(
                    "computer.run_python",
                    {
                        "code": code,
                        "artifact_ids": artifact_ids[:20],
                        "output_paths": [],
                        "timeout_seconds": 120,
                    },
                    None,
                )
            )
            or {}
        )
        if str(result.get("status") or "").upper() != "VERIFIED":
            return {
                "passed": False,
                "observed": result.get("error") or result.get("status") or "validator_execution_failed",
                "failure_class": "validator_execution_failed",
                "retryable": bool(result.get("retryable", True)),
            }
        observation = result.get("observation") if isinstance(result.get("observation"), dict) else {}
        parsed = _parse_stdout(str(observation.get("stdout") or ""))
        if not parsed or not isinstance(parsed.get("passed"), bool):
            return {
                "passed": False,
                "observed": "validator_result_missing_or_malformed",
                "failure_class": "validator_malformed_result",
                "retryable": True,
            }
        return {
            "passed": bool(parsed["passed"]),
            "expected": parsed.get("expected"),
            "observed": parsed.get("observed"),
            "failure_class": str(parsed.get("failure_class") or "validation_failed")[:120],
            "retryable": True,
            "evidence_refs": [
                str(item)
                for item in (
                    result.get("action_id"),
                    *(artifact_ids[:20]),
                )
                if str(item or "").strip()
            ],
        }
