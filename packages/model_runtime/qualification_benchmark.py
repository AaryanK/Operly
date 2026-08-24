"""Evidence-based qualification of concrete model routes.

A model route is the concrete provider + provider model ID.  Catalog/vendor labels
are hints; qualification evidence is what routing policy should trust.  The probes
are intentionally small, deterministic, and provider-neutral.
"""
from __future__ import annotations

import ast
import asyncio
import builtins
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from packages.model_runtime.contracts import InferenceBudget, InferenceRequest, ModelInferenceError


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


@dataclass(slots=True)
class QualificationCase:
    name: str
    passed: bool
    latency_ms: int
    detail: str = ""
    classification: str | None = None
    provider_model_id: str | None = None
    finish_reason: str | None = None
    usage: dict[str, int | None] | None = None

    @property
    def status(self) -> str:
        if self.passed:
            return "pass"
        if self.classification in _TRANSIENT_CLASSIFICATIONS:
            return "inconclusive"
        return "fail"

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status
        return value


@dataclass(slots=True)
class QualificationReport:
    resource_id: str
    provider: str
    model_id: str
    canonical_id: str
    advertised_capabilities: list[str]
    free: bool
    context_length: int | None
    cases: list[QualificationCase] = field(default_factory=list)

    @property
    def passed_cases(self) -> set[str]:
        return {case.name for case in self.cases if case.passed}

    @property
    def verified_capabilities(self) -> list[str]:
        passed = self.passed_cases
        caps: set[str] = set()
        if "availability" in passed:
            caps.add("text")
        if "structured_json" in passed:
            caps.add("structured_output")
        if "reasoning" in passed:
            caps.add("reasoning")
        # Studio needs a persistent tool protocol, so one function call alone is
        # deliberately insufficient to grant the tools capability.
        if {"tool_single", "tool_multi"}.issubset(passed):
            caps.add("tools")
        if "coding" in passed:
            caps.add("coding")
        if "repair" in passed:
            caps.add("repair")
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
            "free": self.free,
            "contextLength": self.context_length,
            "advertisedCapabilities": self.advertised_capabilities,
            "verifiedCapabilities": self.verified_capabilities,
            "score": self.score,
            "inconclusiveCases": [case.name for case in self.cases if case.status == "inconclusive"],
            "cases": [case.as_dict() for case in self.cases],
        }


def _usage(result) -> dict[str, int | None] | None:
    value = getattr(result, "usage", None)
    return asdict(value) if value is not None else None


def _final_text(message: dict[str, Any]) -> str:
    text = str(message.get("content") or "").strip()
    if "</think>" in text:
        tail = text.rsplit("</think>", 1)[-1].strip()
        if tail:
            return tail
    return text


def _tool_calls(message: dict[str, Any]) -> list[tuple[str, dict[str, Any], str | None]]:
    rows: list[tuple[str, dict[str, Any], str | None]] = []
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = str(fn.get("name") or "")
        raw = fn.get("arguments") or {}
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


RECORD_TOOL = _tool(
    "benchmark_record",
    "Record the requested benchmark result instead of answering in prose.",
    {"label": {"type": "string"}, "value": {"type": "integer"}},
    ("label", "value"),
)
READ_TOOL = _tool(
    "read_fixture",
    "Read a benchmark fixture before computing a result.",
    {"name": {"type": "string"}},
    ("name",),
)
CODE_TOOL = _tool(
    "submit_code",
    "Submit the complete Python function requested by the benchmark.",
    {"source": {"type": "string"}},
    ("source",),
)
PLAN_TOOL = _tool(
    "submit_plan",
    "Submit the requested implementation plan.",
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


async def _infer(
    model,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    max_output_tokens: int = 1024,
    timeout: float = 60.0,
):
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
                metadata={"component": "model_qualification_benchmark"},
            )
        )
        return result, int((time.monotonic() - started) * 1000), None
    except ModelInferenceError as error:
        return None, int((time.monotonic() - started) * 1000), error
    except Exception as error:
        normalized = ModelInferenceError(str(error), classification="benchmark_error", retryable=False)
        return None, int((time.monotonic() - started) * 1000), normalized


def _error_case(name: str, latency: int, error: ModelInferenceError) -> QualificationCase:
    return QualificationCase(
        name=name,
        passed=False,
        latency_ms=latency,
        detail=str(error)[:1200],
        classification=getattr(error, "classification", None),
        provider_model_id=getattr(error, "model_id", None),
    )


def _result_case(name: str, passed: bool, latency: int, result, detail: str) -> QualificationCase:
    return QualificationCase(
        name=name,
        passed=passed,
        latency_ms=latency,
        detail=detail[:1200],
        provider_model_id=result.provider_model_id,
        finish_reason=result.finish_reason,
        usage=_usage(result),
    )


async def availability(model) -> QualificationCase:
    result, latency, error = await _infer(
        model,
        [
            {"role": "system", "content": "This is a deterministic model qualification probe."},
            {"role": "user", "content": "Reply with the exact token OPERLY_OK and nothing else."},
        ],
        max_output_tokens=256,
    )
    if error:
        return _error_case("availability", latency, error)
    text = _final_text(result.message)
    return _result_case("availability", text == "OPERLY_OK", latency, result, text)


async def structured_json(model) -> QualificationCase:
    result, latency, error = await _infer(
        model,
        [{"role": "user", "content": 'Return JSON only with exactly this object: {"alpha":7,"beta":"operly","ok":true}. No markdown and no explanation.'}],
        max_output_tokens=1024,
    )
    if error:
        return _error_case("structured_json", latency, error)
    text = _final_text(result.message).strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    return _result_case("structured_json", parsed == {"alpha": 7, "beta": "operly", "ok": True}, latency, result, text)


async def reasoning(model) -> QualificationCase:
    result, latency, error = await _infer(
        model,
        [{"role": "user", "content": "An employee works 08:47-12:05, 12:42-17:11, and 17:30-18:05. Return only the total worked minutes as an integer."}],
        max_output_tokens=1024,
    )
    if error:
        return _error_case("reasoning", latency, error)
    text = _final_text(result.message)
    passed = bool(re.fullmatch(r"\s*502\s*", text)) or bool(re.search(r"\b502\b", text))
    return _result_case("reasoning", passed, latency, result, text)


async def tool_single(model) -> QualificationCase:
    result, latency, error = await _infer(
        model,
        [{"role": "user", "content": "Do not answer in prose. Call benchmark_record with label operly and integer value 42."}],
        tools=[RECORD_TOOL],
        max_output_tokens=1024,
    )
    if error:
        return _error_case("tool_single", latency, error)
    calls = _tool_calls(result.message)
    passed = any(name == "benchmark_record" and args.get("label") == "operly" and args.get("value") == 42 for name, args, _ in calls)
    return _result_case("tool_single", passed, latency, result, json.dumps(calls, default=str))


async def tool_multi(model) -> QualificationCase:
    messages = [
        {"role": "user", "content": "First call read_fixture for secret-number. After receiving the tool result, call benchmark_record with label fixture and integer value equal to the fixture value plus 7. Do not answer in prose."}
    ]
    first, latency_a, error = await _infer(model, messages, tools=[READ_TOOL, RECORD_TOOL], max_output_tokens=1024)
    if error:
        return _error_case("tool_multi", latency_a, error)
    calls = _tool_calls(first.message)
    if not any(name == "read_fixture" for name, _, _ in calls):
        return _result_case("tool_multi", False, latency_a, first, "first turn did not call read_fixture")
    messages.append(dict(first.message))
    messages.append({"role": "tool", "tool_name": "read_fixture", "content": json.dumps({"name": "secret-number", "value": 35})})
    second, latency_b, error = await _infer(model, messages, tools=[READ_TOOL, RECORD_TOOL], max_output_tokens=1024)
    if error:
        return _error_case("tool_multi", latency_a + latency_b, error)
    calls = _tool_calls(second.message)
    passed = any(name == "benchmark_record" and args.get("label") == "fixture" and args.get("value") == 42 for name, args, _ in calls)
    return _result_case("tool_multi", passed, latency_a + latency_b, second, json.dumps(calls, default=str))


def _safe_python(source: str, function_name: str, cases: list[tuple[tuple[Any, ...], Any]]) -> tuple[bool, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return False, f"syntax error: {error}"
    forbidden = (
        ast.Import,
        ast.ImportFrom,
        ast.ClassDef,
        ast.AsyncFunctionDef,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        ast.Raise,
        ast.Global,
        ast.Nonlocal,
        ast.Lambda,
    )
    if any(isinstance(node, forbidden) for node in ast.walk(tree)):
        return False, "forbidden syntax"
    allowed_calls = {"sum", "min", "max", "len", "range", "enumerate", "zip", "abs", "int"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in allowed_calls):
            return False, "unexpected function call"
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name]
    if len(functions) != 1:
        return False, f"expected one {function_name} function"
    safe_builtins = {name: getattr(builtins, name) for name in allowed_calls}
    globals_scope = {"__builtins__": safe_builtins}
    locals_scope: dict[str, Any] = {}
    try:
        exec(compile(tree, "<operly-model-benchmark>", "exec"), globals_scope, locals_scope)
        fn = locals_scope[function_name]
        for args, expected in cases:
            actual = fn(*args)
            if actual != expected:
                return False, f"case {args!r}: expected {expected!r}, got {actual!r}"
    except Exception as error:
        return False, f"execution failed: {type(error).__name__}: {error}"
    return True, "all deterministic cases passed"


async def coding(model) -> QualificationCase:
    prompt = """Implement the Python function below. Ignore intervals whose end is before start and sum the duration of all valid intervals. Call submit_code with the complete function source. Do not answer in prose.\n\ndef worked_minutes(intervals):\n    pass\n"""
    result, latency, error = await _infer(model, [{"role": "user", "content": prompt}], tools=[CODE_TOOL], max_output_tokens=3072)
    if error:
        return _error_case("coding", latency, error)
    calls = _tool_calls(result.message)
    source = next((args.get("source") for name, args, _ in calls if name == "submit_code"), None)
    if not isinstance(source, str):
        return _result_case("coding", False, latency, result, "submit_code was not called; final=" + _final_text(result.message))
    passed, detail = _safe_python(source, "worked_minutes", [(([(0, 10), (20, 35)],), 25), (([(10, 10), (60, 30), (100, 160)],), 60), (([],), 0)])
    return _result_case("coding", passed, latency, result, detail)


async def repair(model) -> QualificationCase:
    prompt = """Repair only this Python function so the listed tests pass. Call submit_code with the complete corrected function source. Do not answer in prose.\n\nCurrent source:\ndef clamp(n, low, high):\n    return min(low, max(n, high))\n\nTests:\nclamp(5, 0, 10) == 5\nclamp(-3, 0, 10) == 0\nclamp(30, 0, 10) == 10\n"""
    result, latency, error = await _infer(model, [{"role": "user", "content": prompt}], tools=[CODE_TOOL], max_output_tokens=3072)
    if error:
        return _error_case("repair", latency, error)
    calls = _tool_calls(result.message)
    source = next((args.get("source") for name, args, _ in calls if name == "submit_code"), None)
    if not isinstance(source, str):
        return _result_case("repair", False, latency, result, "submit_code was not called; final=" + _final_text(result.message))
    passed, detail = _safe_python(source, "clamp", [((5, 0, 10), 5), ((-3, 0, 10), 0), ((30, 0, 10), 10)])
    return _result_case("repair", passed, latency, result, detail)


async def planning(model) -> QualificationCase:
    prompt = """Plan a small employee QR attendance application. It must validate QR tokens, associate the action with an employee identity, persist clock-in/clock-out attendance state, and provide a private admin attendance view. Call submit_plan with 4-8 implementation nodes. Do not answer in prose."""
    result, latency, error = await _infer(model, [{"role": "user", "content": prompt}], tools=[PLAN_TOOL], max_output_tokens=3072)
    if error:
        return _error_case("planning", latency, error)
    calls = _tool_calls(result.message)
    nodes = next((args.get("nodes") for name, args, _ in calls if name == "submit_plan"), None)
    if not isinstance(nodes, list):
        return _result_case("planning", False, latency, result, "submit_plan was not called; final=" + _final_text(result.message))
    text = json.dumps(nodes, ensure_ascii=False).lower()
    domains = (
        any(term in text for term in ("qr", "token")),
        any(term in text for term in ("employee", "identity")),
        any(term in text for term in ("attendance", "clock", "state", "persist")),
        any(term in text for term in ("admin", "dashboard", "private", "view")),
    )
    passed = 4 <= len(nodes) <= 8 and all(domains)
    return _result_case("planning", passed, latency, result, f"nodes={len(nodes)} domains={domains}")


SUITES = {
    "probe": (availability,),
    "smoke": (availability, structured_json, reasoning, tool_single),
    "deep": (availability, structured_json, reasoning, tool_single, tool_multi, coding, repair, planning),
}


def _case_delay() -> float:
    try:
        return max(0.0, min(float(os.getenv("OPERLY_MODEL_BENCH_CASE_DELAY", "0.75")), 10.0))
    except ValueError:
        return 0.75


async def qualify_model(model, resource, *, suite: str = "deep") -> QualificationReport:
    if suite not in SUITES:
        raise ValueError(f"Unknown benchmark suite: {suite}")
    report = QualificationReport(
        resource_id=str(getattr(model, "id", f"{resource.provider}:{resource.id}")),
        provider=str(resource.provider),
        model_id=str(resource.id),
        canonical_id=str(resource.canonical_id or resource.id),
        advertised_capabilities=sorted(resource.capabilities),
        free=bool(resource.free),
        context_length=resource.context_length,
    )
    cases = SUITES[suite]
    for index, case in enumerate(cases):
        if case is not availability and report.cases and not report.cases[0].passed:
            break
        report.cases.append(await case(model))
        if index + 1 < len(cases) and _case_delay() > 0:
            await asyncio.sleep(_case_delay())
    return report


__all__ = ["QualificationCase", "QualificationReport", "qualify_model", "SUITES"]
