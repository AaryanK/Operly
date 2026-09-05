#!/usr/bin/env python3
"""Empirically qualify Kernel-v3 Agent Runtime inference routes.

This script deliberately exercises only the narrow inference boundary. It cannot
execute a Kernel capability, receive an ExecutionContext, or mutate Operly state.

Examples:
  python scripts/benchmark_models.py --suite smoke
  python scripts/benchmark_models.py --provider groq --suite planner
  python scripts/benchmark_models.py --provider groq --provider openrouter --suite smoke

One INFERENCE_QUALIFICATION line is emitted per case and a final
INFERENCE_QUALIFICATION_SUMMARY line is emitted at the end. Credentials and raw
provider error bodies are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from packages.agent_runtime.inference import (
    AgentInferenceError,
    AgentInferenceRuntime,
    InferenceBudget,
    InferencePortfolio,
    InferenceRequest,
    InferenceRoute,
)


@dataclass(frozen=True, slots=True)
class QualificationCase:
    name: str
    system: str
    payload: Any
    structured: bool
    required_keys: tuple[str, ...] = ()


SUITES: dict[str, tuple[QualificationCase, ...]] = {
    "probe": (
        QualificationCase(
            name="text",
            system="Reply with exactly the word ready.",
            payload="Readiness probe.",
            structured=False,
        ),
    ),
    "smoke": (
        QualificationCase(
            name="text",
            system="Reply briefly and identify the answer as Operly output.",
            payload="Say that the route is available.",
            structured=False,
        ),
        QualificationCase(
            name="structured_json",
            system='Return exactly one JSON object with keys "ok" and "kind".',
            payload={"task": "Return ok=true and kind=structured"},
            structured=True,
            required_keys=("ok", "kind"),
        ),
    ),
    "planner": (
        QualificationCase(
            name="structured_json",
            system='Return exactly one JSON object with keys "ok" and "kind".',
            payload={"task": "Return ok=true and kind=structured"},
            structured=True,
            required_keys=("ok", "kind"),
        ),
        QualificationCase(
            name="planner_shape",
            system=(
                'Return exactly one JSON object with one top-level key "steps". '
                'The value must be an array containing one object with exactly '
                '"capability_id" and "arguments".'
            ),
            payload={
                "objective": "Create a task named qualification",
                "candidate_capabilities": [
                    {
                        "capability_id": "tasks.create",
                        "input_schema": {"type": "object", "required": ["title"]},
                    }
                ],
            },
            structured=True,
            required_keys=("steps",),
        ),
    ),
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provider",
        action="append",
        default=[],
        help="Configured fixed-destination provider to qualify; repeat for multiple routes",
    )
    parser.add_argument("--suite", choices=sorted(SUITES), default="smoke")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=900,
        help="Per-case output ceiling; still capped by the runtime hard budget",
    )
    return parser.parse_args()


def _routes(args: argparse.Namespace) -> tuple[InferenceRoute, ...]:
    requested = []
    for raw in args.provider:
        provider = str(raw or "").strip().lower()
        if provider and provider not in requested:
            requested.append(provider)
    if requested:
        return tuple(
            InferenceRoute.for_provider(provider, primary=index == 0)
            for index, provider in enumerate(requested)
        )
    return InferencePortfolio.from_environment().routes


def _validate_case(case: QualificationCase, content: str) -> tuple[bool, str | None]:
    if not content.strip():
        return False, "empty_output"
    if not case.structured:
        return True, None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return False, "malformed_json"
    if not isinstance(payload, dict):
        return False, "json_not_object"
    missing = [key for key in case.required_keys if key not in payload]
    if missing:
        return False, "missing_required_keys"
    if case.name == "planner_shape":
        steps = payload.get("steps")
        if not isinstance(steps, list) or len(steps) != 1:
            return False, "invalid_steps"
        step = steps[0]
        if not isinstance(step, dict) or set(step) != {"capability_id", "arguments"}:
            return False, "invalid_step_shape"
        if step.get("capability_id") != "tasks.create" or not isinstance(step.get("arguments"), dict):
            return False, "invalid_capability_selection"
    return True, None


async def _qualify_route(
    route: InferenceRoute,
    *,
    suite: str,
    max_output_tokens: int,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for case in SUITES[suite]:
        runtime = AgentInferenceRuntime(
            portfolio=InferencePortfolio(routes=(route,)),
            budget=InferenceBudget(
                total_timeout_seconds=min(max(route.timeout_seconds * route.max_attempts, 5.0), 180.0),
                max_output_tokens=max(128, min(max_output_tokens, 4096)),
                max_total_attempts=route.max_attempts,
                max_provider_routes=1,
            ),
        )
        started = time.monotonic()
        try:
            result = await runtime.complete(
                InferenceRequest(
                    system=case.system,
                    user_payload=case.payload,
                    structured=case.structured,
                    max_output_tokens=max_output_tokens,
                )
            )
            passed, validation_error = _validate_case(case, result.content)
            report = {
                "provider": route.provider,
                "modelId": route.model_id,
                "case": case.name,
                "passed": passed,
                "errorCode": validation_error,
                "attempts": result.attempts,
                "latencyMs": round((time.monotonic() - started) * 1000),
                "outputBytes": len(result.content.encode("utf-8")),
            }
        except AgentInferenceError as error:
            report = {
                "provider": route.provider,
                "modelId": route.model_id,
                "case": case.name,
                "passed": False,
                "errorCode": error.code,
                "retryable": error.retryable,
                "latencyMs": round((time.monotonic() - started) * 1000),
            }
        print(
            "INFERENCE_QUALIFICATION "
            + json.dumps(report, ensure_ascii=False, sort_keys=True),
            flush=True,
        )
        reports.append(report)
    return reports


async def main() -> int:
    args = _args()
    try:
        routes = _routes(args)
    except AgentInferenceError as error:
        print(
            "INFERENCE_QUALIFICATION_SUMMARY "
            + json.dumps({"passed": False, "errorCode": error.code}, sort_keys=True),
            flush=True,
        )
        return 2

    all_reports: list[dict[str, Any]] = []
    for route in routes:
        all_reports.extend(
            await _qualify_route(
                route,
                suite=args.suite,
                max_output_tokens=max(128, min(args.max_output_tokens, 4096)),
            )
        )

    passed_cases = sum(bool(report.get("passed")) for report in all_reports)
    summary = {
        "suite": args.suite,
        "routes": len(routes),
        "cases": len(all_reports),
        "passedCases": passed_cases,
        "passed": bool(all_reports) and passed_cases == len(all_reports),
        "providers": [route.provider for route in routes],
        "models": [route.model_id for route in routes],
    }
    print(
        "INFERENCE_QUALIFICATION_SUMMARY "
        + json.dumps(summary, ensure_ascii=False, sort_keys=True),
        flush=True,
    )
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
