#!/usr/bin/env python3
"""Empirically qualify concrete model routes available to Operly.

The safe workflow is staged:
  probe  -> one tiny availability request per route
  smoke  -> availability + JSON + reasoning + one tool call
  deep   -> smoke + multi-turn tools + coding + repair + planning

Examples:
  python scripts/benchmark_models.py --suite deep --provider groq
  python scripts/benchmark_models.py --suite deep --provider groq --model qwen/qwen3.6-27b
  python scripts/benchmark_models.py --suite probe --refresh-discovery --free-only

One MODEL_BENCH JSON line is printed per route, followed by MODEL_BENCH_SUMMARY.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import defaultdict, deque

from packages.model_runtime.catalog import model_resources, provider_is_configured
from packages.model_runtime.discovery import refresh_model_discovery
from packages.model_runtime.qualification_benchmark import SUITES, qualify_model
from packages.model_runtime.registry import ModelRegistry


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", action="append", default=[], help="Provider(s) to benchmark")
    parser.add_argument("--model", action="append", default=[], help="Exact provider model id(s) to benchmark")
    parser.add_argument("--suite", choices=sorted(SUITES), default="smoke")
    parser.add_argument("--refresh-discovery", action="store_true", help="Refresh dynamic catalogs such as OpenRouter")
    parser.add_argument("--free-only", action="store_true", help="Skip routes whose catalog metadata is not free")
    parser.add_argument("--max-per-provider", type=int, default=0, help="Optional cap after priority sort; 0 means no cap")
    parser.add_argument("--delay", type=float, default=float(os.getenv("OPERLY_MODEL_BENCH_DELAY", "0.75")))
    return parser.parse_args()


def _round_robin_provider_order(rows):
    """Avoid burning one provider's TPM quota before touching the next provider."""
    buckets: dict[str, deque] = defaultdict(deque)
    for resource in rows:
        buckets[resource.provider].append(resource)
    ordered = []
    names = sorted(buckets)
    while any(buckets.values()):
        for name in names:
            if buckets[name]:
                ordered.append(buckets[name].popleft())
    return ordered


def _selected_resources(args: argparse.Namespace):
    providers = {item.strip().lower() for item in args.provider if item.strip()}
    models = {item.strip() for item in args.model if item.strip()}
    rows = [
        resource
        for resource in model_resources()
        if provider_is_configured(resource.provider)
        and (not providers or resource.provider in providers)
        and (not models or resource.id in models)
        and (not args.free_only or resource.free)
    ]
    dedup = {(resource.provider, resource.id): resource for resource in rows}
    rows = sorted(
        dedup.values(),
        key=lambda item: (
            item.provider,
            item.priority,
            item.verified_latency_ms or 10**9,
            item.id,
        ),
    )
    if args.max_per_provider > 0:
        counts: dict[str, int] = defaultdict(int)
        limited = []
        for resource in rows:
            if counts[resource.provider] >= args.max_per_provider:
                continue
            counts[resource.provider] += 1
            limited.append(resource)
        rows = limited
    return _round_robin_provider_order(rows)


async def main() -> int:
    args = _args()
    if args.refresh_discovery:
        counts = await refresh_model_discovery(force=True)
        print("MODEL_BENCH_DISCOVERY " + json.dumps(counts, sort_keys=True), flush=True)

    resources = _selected_resources(args)
    print(
        "MODEL_BENCH_START "
        + json.dumps(
            {
                "routes": len(resources),
                "suite": args.suite,
                "providers": sorted({resource.provider for resource in resources}),
                "freeOnly": bool(args.free_only),
                "filters": {
                    "provider": args.provider,
                    "model": args.model,
                    "maxPerProvider": args.max_per_provider,
                },
                "scheduling": "provider_round_robin",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if not resources:
        print("MODEL_BENCH_SUMMARY " + json.dumps({"routes": 0, "error": "no configured model routes matched"}), flush=True)
        return 2

    reports = []
    registry = ModelRegistry()
    for index, resource in enumerate(resources, 1):
        model = registry.register_resource(resource, replace=True)
        report = await qualify_model(model, resource, suite=args.suite)
        reports.append(report)
        payload = report.as_dict()
        payload["index"] = index
        payload["routes"] = len(resources)
        print("MODEL_BENCH " + json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
        if args.delay > 0 and index < len(resources):
            await asyncio.sleep(args.delay)

    ranked = sorted(
        reports,
        key=lambda report: (
            -report.score,
            sum(case.latency_ms for case in report.cases),
            report.provider,
            report.model_id,
        ),
    )
    summary = {
        "routes": len(reports),
        "suite": args.suite,
        "passingAvailability": sum("text" in report.verified_capabilities for report in reports),
        "verifiedTools": sum("tools" in report.verified_capabilities for report in reports),
        "verifiedCoding": sum("coding" in report.verified_capabilities for report in reports),
        "verifiedRepair": sum("repair" in report.verified_capabilities for report in reports),
        "top": [
            {
                "provider": report.provider,
                "modelId": report.model_id,
                "free": report.free,
                "score": report.score,
                "verifiedCapabilities": report.verified_capabilities,
                "latencyMs": sum(case.latency_ms for case in report.cases),
            }
            for report in ranked[:30]
        ],
    }
    print("MODEL_BENCH_SUMMARY " + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
