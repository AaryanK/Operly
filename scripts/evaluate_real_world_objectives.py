from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Direct `python scripts/...` execution puts only scripts/ on sys.path. Add the repo root
# explicitly so the benchmark behaves the same in CI, a checkout, and an operator shell.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.agent_runtime.real_world_evaluation import (
    CURATED_CASES,
    _workspace_os_cases,
    corpus_inventory,
    run_real_world_eval,
    write_report_files,
)
from packages.agent_runtime.real_world_evaluation_extra import EXTRA_CURATED_CASES


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run raw human-style prompts through Operly's configured ObjectiveInterpreter "
            "and real scoped capability registries without executing any capability."
        )
    )
    parser.add_argument("--include-workspace-os", action="store_true", help="also model-test every generated Workspace OS CRUD case")
    parser.add_argument("--interval", type=float, default=0.75, help="delay between model cases in seconds")
    parser.add_argument("--max-attempts", type=int, default=3, help="bounded model attempts per case")
    parser.add_argument("--retry-delay", type=float, default=3.0, help="base backoff for model-provider failures")
    parser.add_argument("--limit", type=int, default=0, help="optional leading-case limit for a smoke run")
    parser.add_argument("--output-dir", default="artifacts/real-world-objective-eval")
    parser.add_argument("--inventory-only", action="store_true")
    return parser.parse_args()


async def _main() -> int:
    args = _args()
    inventory = corpus_inventory()
    inventory["extra_curated"] = len(EXTRA_CURATED_CASES)
    inventory["live_default_total"] = len(CURATED_CASES) + len(EXTRA_CURATED_CASES)
    print("REAL_WORLD_EVAL_INVENTORY " + json.dumps(inventory, ensure_ascii=False, sort_keys=True), flush=True)
    if args.inventory_only:
        return 0

    cases = CURATED_CASES + EXTRA_CURATED_CASES
    if args.include_workspace_os:
        cases += _workspace_os_cases()
    if args.limit > 0:
        cases = cases[: args.limit]

    report = await run_real_world_eval(
        cases=cases,
        interval_seconds=max(0.0, args.interval),
        max_attempts=max(1, args.max_attempts),
        retry_delay_seconds=max(0.5, args.retry_delay),
    )
    json_path, markdown_path = write_report_files(report, directory=Path(args.output_dir))
    print(f"REAL_WORLD_EVAL_REPORT_JSON {json_path}", flush=True)
    print(f"REAL_WORLD_EVAL_REPORT_MARKDOWN {markdown_path}", flush=True)
    print(markdown_path.read_text(encoding="utf-8"), flush=True)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
