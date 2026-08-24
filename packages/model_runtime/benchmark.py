"""Empirical qualification probes for provider/model routes.

The model catalog is descriptive metadata; this module measures what a concrete
provider route can actually do through Operly's provider-neutral model boundary.
No benchmark result silently grants runtime capability.  Results are evidence for
operator review and routing-policy updates.
"""
from __future__ import annotations

import ast
import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from packages.model_runtime.contracts import InferenceBudget, InferenceRequest, ModelInferenceError


@dataclass(slots=True)
class BenchmarkCaseResult:
    name: str
    passed: bool
    latency_ms: int
    detail: str = ""
    classification: str | None = None
    provider_model_id: str | None = None
    usage: dict[str, int | None] | None = None


@dataclass(slots=True)
class ModelBenchmarkReport:
    resource_id: str
    provider: str
    model_id: str
    canonical_id: str
    advertised_capabilities: list[str]
    cases: list[BenchmarkCaseResult] = field(default_factory=list)

    @property
    def verified_capabilities(self) -> list[str]:
        passed = {case.name for case in self.cases if case.passed}
        caps: set[str] = set()
        if "availability" in passed:
            caps.add("text")
        if "reasoning" in passed:
            caps.add("reasoning")
        if {"tool_single", "tool_multi"}.issubset(passed):
            caps.add("tools")
        if "coding" in passed:
            caps.add("coding")
        if "repair" in passed:
            caps.add("repair")
        if "structured_json" in passed:
            caps.add("structured_output")
        if "planning" in passed:
            caps.add("planning")
        return sorted(caps)

    @property
    def score(self) -> int:
        weights = {
            "availability": 5,
            "structured_json": 5,
            "reasoning": 10,
            "tool_single": 15,
            "tool_multi": 15,
            "coding": 25,
            "repair": 20,
            "planning": 5,
        }
        return sum(weights.get(case.name, 0) for case in self.cases if case.passed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "resourceId": self.resource_id,
            "provider": self.provider,
            "modelId": self.model_id,
            "canonicalId": self.canonical_id,
            "advertisedCapabilities": self.advertised_capabilities,
            "verifiedCapabilities": self.verified_capabilities,
            "score": self.score,
            "cases": [asdict(case) for case in self.cases],
        }


def _usage_dict(result) -> dict[str, int | None] | None:
    usage = getattr(result, "usage", None)
    return asdict(usage) if usage is not None else None


def _tool_name_args(message: dict[str, Any]) -> list[tuple[str, dict[str, Any], str | None]]:
    rows: list[tuple[str, dict[str, Any], str | None]] = []
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        raw = function.get("arguments") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        rows.append((name, raw if isinstance(raw, dict) else {}, call.get("id")))
    return rows


def _tool(name: str, description: str, properties: dict[str, Any], required: Iterable[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


_RECORD_TOOL = _tool(
    "benchmark_record",
    "Record the requested benchmark value. Use this instead of answering in prose.",
    {"label": {"type": "string"}, "value": {"type": "integer"}},
    ("label", "value"),
)
_READ_TOOL = _tool(
    "read_fixture",
    "Read the benchmark fixture before computing the requested answer.",
    {"name": {"type": "string"}},
    ("name",),
)
_CODE_TOOL = _tool(
    "submit_code",
    "Submit the complete Python function source requested by the benchmark.",
    {"source": {"type": "string"}},
    ("source",),
)
_PLAN_TOOL = _tool(
    "submit_plan",
    "Submit a compact implementation plan.",
    {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "dependsOn": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "title", "dependsOn"],
                "additionalProperties": False,
            },
        }
    },
    ("nodes",),
)


async def _infer(model, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None, timeout: float = 45.0, max_output_tokens: int = 1024):
    started = time.monotonic()
    try:
        result = await model.infer(
            InferenceRequest(
                messages=tuple(messages),
                tools=tuple(tools or ()),
                budget=InferenceBudget(
                    timeout_seconds=timeout,
                    attempts_per_model=1,
                    max_models=1,
                    max_output_tokens=max_output_tokens,
                ),
                metadata={"component": "model_benchmark"},
            )
        )
        return result, int((time.monotonic() - started) * 1000), None
    except ModelInferenceError as error:
        return None, int((time.monotonic() - started) * 1000), error
    except Exception as error:  # provider adapters should normalize; keep benchmark alive if one does not.
        normalized = ModelInferenceError(str(error), classification="benchmark_error", retryable=False)
        return None, int((time.monotonic() - started) * 1000), normalized


def _failure(name: str, latency_ms: int, error: ModelInferenceError) -> BenchmarkCaseResult:
    return BenchmarkCaseResult(
        name=name,
        passed=False,
        latency_ms=latency_ms,
        detail=str(error)[:800],
        classification=getattr(error, "classification", None),
        provider_model_id=getattr(error, "model_id", None),
    )


async def _availability(model) -> BenchmarkCaseResult:
    result, latency, error = await _infer(
        model,
        [
            {"role": "system", "content": "You are undergoing a deterministic capability probe."},
            {"role": "user", "content": "Reply with the exact token OPERLY_OK and nothing else."},
        ],
        max_output_tokens=64,
    )
    if error:
        return _failure("availability", latency, error)
    content = str(result.message.get("content") or "").strip()
    return BenchmarkCaseResult(
        "availability",
        "OPERLY_OK" in content,
        latency,
        detail=content[:300],
        provider_model_id=result.provider_model_id,
        usage=_usage_dict(result),
    )


async def _structured_json(model) -> BenchmarkCaseResult:
    result, latency, error = await _infer(
        model,
        [{"role": "user", "content": 'Return JSON only, exactly this semantic object: {"alpha":7,"beta":"operly","ok":true}. No markdown.'}],
        max_output_tokens=128,
    )
    if error:
        return _failure("structured_json", latency, error)
    content = str(result.message.get("content") or "").strip().removeprefix("```json").removesuffix("```").strip()
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        value = None
    passed = value == {"alpha": 7, "beta": "operly", "ok": True}
    return BenchmarkCaseResult("structured_json", passed, latency, content[:400], provider_model_id=result.provider_model_id, usage=_usage_dict(result))


async def _reasoning(model) -> BenchmarkCaseResult:
    result, latency, error = await _infer(
        model,
        [{"role": "user", "content": "An employee works 08:47-12:05, 12:42-17:11, and 17:30-18:05. Return only the total worked minutes as an integer."}],
        max_output_tokens=128,
    )
    if error:
        return _failure("reasoning", latency, error)
    content = str(result.message.get("content") or "").strip()
    match = re.search(r"\b502\b", content)
    return BenchmarkCaseResult("reasoning", bool(match), latency, content[:400], provider_model_id=result.provider_model_id, usage=_usage_dict(result))


async def _tool_single(model) -> BenchmarkCaseResult:
    result, latency, error = await _infer(
        model,
        [{"role": "user", "content": "Do not answer in prose. Call benchmark_record with label operly and value 42."}],
        tools=[_RECORD_TOOL],
        max_output_tokens=256,
    )
    if error:
        return _failure("tool_single", latency, error)
    calls = _tool_name_args(result.message)
    passed = any(name == "benchmark_record" and args.get("label") == "operly" and args.get("value") == 42 for name, args, _ in calls)
    return BenchmarkCaseResult("tool_single", passed, latency, json.dumps(calls, default=str)[:600], provider_model_id=result.provider_model_id, usage=_usage_dict(result))


async def _tool_multi(model) -> BenchmarkCaseResult:
    messages = [
        {
            "role": "user",
            "content": "Use read_fixture first to read fixture secret-number. Then call benchmark_record with label fixture and value equal to the secret number plus 7. Do not answer in prose.",
        }
    ]
    first, first_latency, error = await _infer(model, messages, tools=[_READ_TOOL, _RECORD_TOOL], max_output_tokens=256)
    if error:
        return _failure("tool_multi", first_latency, error)
    first_calls = _tool_name_args(first.message)
    read_call = next((row for row in first_calls if row[0] == "read_fixture"), None)
    if read_call is None:
        return BenchmarkCaseResult("tool_multi", False, first_latency, "first turn did not call read_fixture", provider_model_id=first.provider_model_id, usage=_usage_dict(first))
    messages.append(dict(first.message))
    messages.append({"role": "tool", "tool_name": "read_fixture", "content": json.dumps({"name": "secret-number", "value": 35})})
    second, second_latency, error = await _infer(model, messages, tools=[_READ_TOOL, _RECORD_TOOL], max_output_tokens=256)
    if error:
        return _failure("tool_multi", first_latency + second_latency, error)
    second_calls = _tool_name_args(second.message)
    passed = any(name == "benchmark_record" and args.get("label") == "fixture" and args.get("value") == 42 for name, args, _ in second_calls)
    return BenchmarkCaseResult("tool_multi", passed, first_latency + second_latency, json.dumps(second_calls, default=str)[:600], provider_model_id=second.provider_model_id, usage=_usage_dict(second))


def _safe_function(source: str, function_name: str, cases: list[tuple[tuple[Any, ...], Any]]) -> tuple[bool, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return False, f"syntax error: {error}"
    forbidden = (ast.Import, ast.ImportFrom, ast.ClassDef, ast.AsyncFunctionDef, ast.With, ast.AsyncWith, ast.Try, ast.Raise, ast.Global, ast.Nonlocal, ast.Lambda)
    if any(isinstance(node, forbidden) for node in ast.walk(tree)):
        return False, "forbidden syntax in benchmark submission"
    allowed_calls = {"sum", "min", "max", "len", "range", "enumerate", "zip", "abs", "int"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in allowed_calls:
                return False, "unsafe or unexpected call in benchmark submission"
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name]
    if len(functions) != 1:
        return False, f"expected one {function_name} function"
    scope: dict[str, Any] = {}
    safe_builtins = {name: __builtins__[name] for name in allowed_calls if isinstance(__builtins__, dict) and name in __builtins__}
    if not safe_builtins:
        import builtins
        safe_builtins = {name: getattr(builtins, name) for name in allowed_calls}
    try:
        exec(compile(tree, "<model-benchmark>", "exec"), {"__builtins__": safe_builtins}, scope)
        fn = scope[function_name]
        for args, expected in cases:
            actual = fn(*args)
            if actual != expected:
                return False, f"case {args!r}: expected {expected!r}, got {actual!r}"
    except Exception as error:
        return False, f"execution failed: {type(error).__name__}: {error}"
    return True, "all deterministic cases passed"


async def _coding(model) -> BenchmarkCaseResult:
    prompt = """Implement this Python function. Ignore intervals whose end is before start. Return the total minutes across valid intervals. Use submit_code with the complete function source and no prose.\n\ndef worked_minutes(intervals):\n    pass\n"""
    result, latency, error = await _infer(model, [{"role": "user", "content": prompt}], tools=[_CODE_TOOL], max_output_tokens=768)
    if error:
        return _failure("coding", latency, error)
    calls = _tool_name_args(result.message)
    submission = next((args.get("source") for name, args, _ in calls if name == "submit_code"), None)
    if not isinstance(submission, str):
        return BenchmarkCaseResult("coding", False, latency, "submit_code was not called", provider_model_id=result.provider_model_id, usage=_usage_dict(result))
    passed, detail = _safe_function(
        submission,
        "worked_minutes",
        [
            (([(0, 10), (20, 35)],), 25),
            (([(10, 10), (60, 30), (100, 160)],), 60),
            (([],), 0),
        ],
    )
    return BenchmarkCaseResult("coding", passed, latency, detail, provider_model_id=result.provider_model_id, usage=_usage_dict(result))


async def _repair(model) -> BenchmarkCaseResult:
    prompt = """A test suite found a bug. Repair only the function and call submit_code with the complete corrected function source. No prose.\n\nCurrent source:\ndef clamp(n, low, high):\n    return min(low, max(n, high))\n\nFailing expectations:\nclamp(5, 0, 10) == 5\nclamp(-3, 0, 10) == 0\nclamp(30, 0, 10) == 10\n"""
    result, latency, error = await _infer(model, [{"role": "user", "content": prompt}], tools=[_CODE_TOOL], max_output_tokens=512)
    if error:
        return _failure("repair", latency, error)
    calls = _tool_name_args(result.message)
    submission = next((args.get("source") for name, args, _ in calls if name == "submit_code"), None)
    if not isinstance(submission, str):
        return BenchmarkCaseResult("repair", False, latency, "submit_code was not called", provider_model_id=result.provider_model_id, usage=_usage_dict(result))
    passed, detail = _safe_function(submission, "clamp", [((5, 0, 10), 5), ((-3, 0, 10), 0), ((30, 0, 10), 10)])
    return BenchmarkCaseResult("repair", passed, latency, detail, provider_model_id=result.provider_model_id, usage=_usage_dict(result))


async def _planning(model) -> BenchmarkCaseResult:
    prompt = """Plan a small employee QR attendance application. It must validate a QR token, associate an employee identity, persist clock-in/clock-out attendance state, and provide a private admin attendance view. Call submit_plan with 4-8 implementation nodes. Do not answer in prose."""
    result, latency, error = await _infer(model, [{"role": "user", "content": prompt}], tools=[_PLAN_TOOL], max_output_tokens=1024)
    if error:
        return _failure("planning", latency, error)
    calls = _tool_name_args(result.message)
    nodes = next((args.get("nodes") for name, args, _ in calls if name == "submit_plan"), None)
    if not isinstance(nodes, list):
        return BenchmarkCaseResult("planning", False, latency, "submit_plan was not called", provider_model_id=result.provider_model_id, usage=_usage_dict(result))
    text = " ".join(str((node or {}).get("title") or "").lower() for node in nodes if isinstance(node, dict))
    domains = (
        any(term in text for term in ("qr", "token")),
        any(term in text for term in ("employee", "identity")),
        any(term in text for term in ("attendance", "clock", "state", "persist")),
        any(term in text for term in ("admin", "dashboard", "view")),
    )
    passed = 4 <= len(nodes) <= 8 and all(domains)
    return BenchmarkCaseResult("planning", passed, latency, f"nodes={len(nodes)} domains={domains}", provider_model_id=result.provider_model_id, usage=_usage_dict(result))


async def benchmark_model(model, resource, *, deep: bool = True) -> ModelBenchmarkReport:
    """Benchmark one concrete route; never fail the entire run because one route fails."""
    report = ModelBenchmarkReport(
        resource_id=str(getattr(model, "id", f"{resource.provider}:{resource.id}")),
        provider=str(resource.provider),
        model_id=str(resource.id),
        canonical_id=str(resource.canonical_id or resource.id),
        advertised_capabilities=sorted(resource.capabilities),
    )
    for case in (_availability, _structured_json, _reasoning, _tool_single):
        report.cases.append(await case(model))
    if not deep or not next((case.passed for case in report.cases if case.name == "availability"), False):
        return report
    # Deep probes are intentionally conditional: do not spend several more requests
    # on a route that cannot complete the cheapest availability probe.
    for case in (_tool_multi, _coding, _repair, _planning):
        report.cases.append(await case(model))
    return report


__all__ = [
    "BenchmarkCaseResult",
    "ModelBenchmarkReport",
    "benchmark_model",
]
