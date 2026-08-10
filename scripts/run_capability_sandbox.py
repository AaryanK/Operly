"""Run the 10-case capability placement benchmark against the configured model.

This script is intentionally read-only with respect to OPERLY application data. It
only sends synthetic benchmark prompts and a synthetic workspace snapshot to the
placement resolver, then scores the returned architecture mechanics.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

# Allow `python scripts/run_capability_sandbox.py` to resolve the repository
# packages without requiring callers to configure PYTHONPATH first.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
load_dotenv(REPOSITORY_ROOT / ".env")

from packages.capability_sandbox.benchmarks import BENCHMARKS, RICH_WORKSPACE, evaluate_benchmark
from packages.capability_sandbox.target_resolution import resolve_capability_placement


async def main() -> int:
    failures = 0
    for index, case in enumerate(BENCHMARKS, 1):
        try:
            result = await resolve_capability_placement(case.prompt, RICH_WORKSPACE)
            issues = evaluate_benchmark(case, result)
        except Exception as error:
            result = None
            issues = [f"resolver error: {error}"]

        status = "PASS" if not issues else "FAIL"
        print(f"[{index:02d}/10] {status} {case.id}")
        if result is not None:
            print(
                json.dumps(
                    {
                        "disposition": result.disposition,
                        "targets": result.target_resource_ids,
                        "humanSurface": result.human_surface,
                        "questions": result.clarification_questions,
                        "machineOperations": result.machine_operations,
                        "confidence": result.confidence,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        for issue in issues:
            print("  -", issue)
        if issues:
            failures += 1

    print(f"\nResult: {len(BENCHMARKS) - failures}/{len(BENCHMARKS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
