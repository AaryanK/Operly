"""Run-level success verification over structured capability evidence.

Capability providers verify individual actions. This module verifies the *root user
objective* after a multi-step run so a model cannot turn a collection of individually
successful calls into a false "fully completed" claim.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from packages.model_runtime import InferenceBudget, InferenceRequest
from packages.model_runtime.registry import model_for_role


@dataclass(frozen=True, slots=True)
class RunGoalVerification:
    satisfied: bool
    missing: tuple[str, ...] = ()
    verified: tuple[str, ...] = ()
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "missing": list(self.missing),
            "verified": list(self.verified),
            "reason": self.reason,
        }


def _parse_json_object(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


def _small_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:400]
    if isinstance(value, list):
        return [_small_value(item) for item in value[:30]]
    if isinstance(value, dict):
        allowed = {}
        for key, item in value.items():
            if key in {
                "status",
                "ok",
                "success",
                "changed",
                "error",
                "reason",
                "artifact_id",
                "artifact_ids",
                "artifacts",
                "artifact_kind",
                "output_format",
                "columns",
                "row_count",
                "processed_count",
                "attachment_count",
                "expected_attachment_count",
                "attachment_filenames",
                "expected_attachment_filenames",
                "attachments_persisted_by_provider",
                "draft_persisted_by_provider",
                "draft_id",
                "message_id",
                "delivery_status",
                "recipients",
                "subject",
                "conversion",
                "input_artifact_id",
                "verification",
                "observation",
            }:
                allowed[str(key)] = _small_value(item)
        return allowed
    return str(value)[:400]


def compact_trace_evidence(trace: Iterable[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for entry in list(trace)[-50:]:
        capability_id = str(getattr(entry, "capability_id", "") or "")
        arguments = getattr(entry, "arguments", {})
        observation = getattr(entry, "observation", {})
        if not isinstance(arguments, dict):
            arguments = {}
        if not isinstance(observation, dict):
            observation = {}
        argument_evidence = {
            key: _small_value(value)
            for key, value in arguments.items()
            if key in {
                "output_format",
                "output_formats",
                "columns",
                "artifact_id",
                "artifact_ids",
                "to",
                "subject",
                "filename",
                "title",
            }
        }
        output.append(
            {
                "capability": capability_id,
                "arguments": argument_evidence,
                "result": _small_value(observation),
            }
        )
    return output


def _strings(value: Any, *, limit: int = 12) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip()[:500] for item in value[:limit] if str(item).strip())


class ObjectiveEvidenceVerifier:
    """Semantically compare plan success criteria with capability-verified evidence."""

    async def verify(
        self,
        *,
        objective: str,
        success_criteria: Iterable[str],
        trace: Iterable[Any],
        metadata: dict[str, Any] | None = None,
    ) -> RunGoalVerification:
        criteria = tuple(str(item).strip()[:700] for item in success_criteria if str(item).strip())
        if not criteria:
            return RunGoalVerification(True, reason="no_run_level_success_criteria")
        evidence = compact_trace_evidence(trace)
        if not evidence:
            return RunGoalVerification(
                False,
                missing=criteria,
                reason="no_capability_evidence",
            )

        system = (
            "You are OPERLY's run evidence verifier. Return JSON only and never provide chain-of-thought. "
            "Determine whether every observable success criterion for the root user objective is proven by the supplied structured capability evidence. "
            "Be strict: model prose is not evidence. A file extension or artifact existence alone does not prove requested spreadsheet columns/content. "
            "A Gmail draft alone does not prove an attachment; attachment delivery requires provider verification such as attachments_persisted_by_provider=true and matching counts/filenames. "
            "A missing or ambiguous fact is UNSATISFIED, not an invitation to guess. Read-only absence (for example zero meetings) is valid only when evidence shows the corresponding read capability actually ran successfully."
        )
        payload = {
            "objective": objective[:12000],
            "success_criteria": list(criteria),
            "capability_evidence": evidence,
            "output_contract": {
                "satisfied": True,
                "verified": ["criterion supported by exact evidence"],
                "missing": ["criterion not proven"],
                "reason": "short explanation",
            },
        }
        try:
            model = model_for_role("requirements_analyst")
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
                        max_output_tokens=1400,
                    ),
                    metadata={
                        **dict(metadata or {}),
                        "runtime_component": "objective_evidence_verifier",
                    },
                )
            )
            parsed = _parse_json_object(str(result.message.get("content") or ""))
        except Exception as error:
            return RunGoalVerification(
                False,
                missing=("Run-level success evidence could not be verified.",),
                reason=f"verifier_unavailable:{type(error).__name__}",
            )

        missing = _strings(parsed.get("missing"))
        verified = _strings(parsed.get("verified"))
        satisfied = bool(parsed.get("satisfied")) and not missing
        if not parsed:
            return RunGoalVerification(
                False,
                missing=("Run-level success evidence could not be verified.",),
                reason="verifier_malformed_response",
            )
        if not satisfied and not missing:
            missing = ("One or more requested success criteria are not proven by capability evidence.",)
        return RunGoalVerification(
            satisfied=satisfied,
            missing=missing,
            verified=verified,
            reason=str(parsed.get("reason") or "")[:800] or None,
        )


def partial_completion_message(verification: RunGoalVerification) -> str:
    missing = [item for item in verification.missing if item]
    if not missing:
        missing = ["The full requested outcome could not be verified."]
    body = "\n".join(f"- {item}" for item in missing[:8])
    return (
        "Partially completed. Operly could not verify every requested deliverable or side effect.\n\n"
        "Still missing or unverified:\n"
        f"{body}"
    )
