from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from packages.agent_runtime.context import ContextAssembler, ContextBudget, ContextItem
from packages.agent_runtime.contracts import AgentPlanStep, AgentStepStatus
from packages.agent_runtime.objective import ObjectiveInterpreter, RuntimeDispatchPath
from packages.agent_runtime.runtime import AgentRuntimeSettings, GovernedAgentRuntime
from packages.agent_runtime.telemetry import fingerprint, runtime_trace
from packages.kernel.contracts import CapabilityRisk, CapabilitySpec
from packages.kernel.runtime_availability import AvailabilityAwareKernelRuntime
from packages.kernel.schema_validation import SchemaValidationError, validate_schema
from packages.security.execution_context import ExecutionContext


class Runtime1Model(Protocol):
    async def interpret(self, request): ...
    async def respond(self, *, objective: str, user_message: str, context_items: Sequence[Mapping[str, str]] = (), observations: Sequence[Mapping[str, Any]] = ()) -> str: ...
    async def decide(self, *, objective: str, user_message: str, context_items: Sequence[Mapping[str, str]], observations: Sequence[Mapping[str, Any]], capabilities: Sequence[Mapping[str, Any]], remaining_steps: int, remaining_mutations: int): ...


@dataclass(frozen=True, slots=True)
class Runtime1Limits:
    max_cycles: int = 10
    max_capabilities: int = 12
    max_discoveries: int = 4
    max_mutations: int = 4
    max_failures: int = 3
    max_observation_bytes: int = 4 * 1024
    max_observations: int = 6
    context_budget: ContextBudget = ContextBudget(max_items=6, max_bytes=12 * 1024, max_item_bytes=4 * 1024)


@dataclass(frozen=True, slots=True)
class Runtime1Result:
    message: str
    run_id: str
    dispatch: str
    objective_kind: str
    cycles: int
    capability_calls: tuple[str, ...] = ()
    approval_id: str | None = None
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "run_id": self.run_id,
            "dispatch": self.dispatch,
            "objective_kind": self.objective_kind,
            "cycles": self.cycles,
            "capability_calls": list(self.capability_calls),
            "approval_id": self.approval_id,
            "error_code": self.error_code,
        }


class Runtime1Agent:
    """Interactive Runtime 1.0 loop shared by web, Personal AI and Discord.

    Model intelligence chooses meaning and strategy. Kernel remains the only executor.
    Every inference phase receives a freshly selected, byte-bounded context slice.
    """

    _AUTHORITY_FIELDS = {
        "workspace_id", "user_id", "principal_id", "membership_id", "permissions", "role",
        "approval_id", "provider_id", "provider_url", "credentials", "request_id", "step_id",
    }

    def __init__(self, *, model: Runtime1Model, settings: AgentRuntimeSettings | None = None, limits: Runtime1Limits | None = None, context_assembler: ContextAssembler | None = None) -> None:
        self.model = model
        self.settings = settings or AgentRuntimeSettings.from_environment()
        self.limits = limits or Runtime1Limits()
        self.context_assembler = context_assembler or ContextAssembler()
        self.interpreter = ObjectiveInterpreter(model=model, settings=self.settings, context_assembler=self.context_assembler)

    def _cards(self, specs: Sequence[CapabilitySpec]) -> list[dict[str, Any]]:
        return [
            {
                "id": spec.id,
                "name": str(spec.display_name or spec.id)[:240],
                "description": " ".join(str(spec.description or "").split())[:1000],
                "input_schema": dict(spec.input_schema),
                "risk": spec.risk.value,
                "approval_required": bool(spec.approval_required),
            }
            for spec in specs[: self.limits.max_capabilities]
        ]

    def _decode_decision(self, raw: Any) -> Mapping[str, Any]:
        if isinstance(raw, bytes):
            if len(raw) > 24 * 1024:
                raise ValueError("decision output too large")
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            if len(raw.encode("utf-8")) > 24 * 1024:
                raise ValueError("decision output too large")
            text = raw.strip()
            if text.startswith("```") and text.endswith("```"):
                lines = text.splitlines()
                if len(lines) >= 3 and lines[0].strip().lower() in {"```", "```json"} and lines[-1].strip() == "```":
                    text = "\n".join(lines[1:-1]).strip()
            raw = json.loads(text)
        if not isinstance(raw, Mapping):
            raise ValueError("decision must be a JSON object")
        if set(raw) & self._AUTHORITY_FIELDS:
            raise ValueError("decision contains authority-shaped fields")

        move = str(raw.get("move") or "").strip().lower()
        if move == "call":
            capability_id = str(raw.get("capability_id") or "").strip().lower()
            arguments = raw.get("arguments")
            if not capability_id or len(capability_id) > 300 or not isinstance(arguments, dict):
                raise ValueError("call decision fields are invalid")
            return {"move": "call", "capability_id": capability_id, "arguments": dict(arguments)}
        if move == "discover":
            query = " ".join(str(raw.get("query") or "").split())
            if not query or len(query) > 500:
                raise ValueError("discover query is invalid")
            return {"move": "discover", "query": query}
        if move == "finish":
            message = str(raw.get("message") or "").strip()
            if not message or len(message) > 20_000:
                raise ValueError("finish message is invalid")
            return {"move": "finish", "message": message}
        raise ValueError("unsupported next move")

    def _bounded_observation(self, *, capability_id: str, result: Mapping[str, Any] | None, error_code: str | None = None, error: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"capability_id": capability_id, "ok": error_code is None}
        if result is not None:
            payload["result"] = dict(result)
        if error_code:
            payload["error_code"] = error_code
            payload["error"] = str(error or "")[:500]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > self.limits.max_observation_bytes:
            payload = {
                "capability_id": capability_id,
                "ok": error_code is None,
                "result_summary": {
                    "bytes": len(encoded.encode("utf-8")),
                    "sha256_16": fingerprint(encoded),
                    "top_level_keys": sorted(result.keys())[:30] if isinstance(result, Mapping) else [],
                },
            }
            if error_code:
                payload["error_code"] = error_code
        return payload

    async def _discover(self, db: AsyncSession, *, kernel: AvailabilityAwareKernelRuntime, context: ExecutionContext, query: str) -> tuple[CapabilitySpec, ...]:
        specs = await kernel.available_capabilities(db, context=context, query=query, limit=self.limits.max_capabilities)
        runtime_trace(
            "capabilities.discovered",
            scope_kind=context.scope_kind.value,
            surface=context.surface.value,
            query_chars=len(query),
            query_sha256_16=fingerprint(query),
            count=len(specs),
            capability_ids=[spec.id for spec in specs],
        )
        return specs

    async def run(self, db: AsyncSession, *, context: ExecutionContext, message: str, kernel: AvailabilityAwareKernelRuntime, context_items: Sequence[ContextItem] = (), run_id: str) -> Runtime1Result:
        if not self.settings.enabled:
            from packages.agent_runtime.runtime import AgentRuntimeDisabled
            raise AgentRuntimeDisabled("Agent runtime is disabled")

        runtime_trace(
            "request.received", run_id=run_id, scope_kind=context.scope_kind.value,
            surface=context.surface.value, channel=context.channel, message_chars=len(message),
            message_sha256_16=fingerprint(message), offered_context_items=len(context_items),
        )
        objective = await self.interpreter.interpret(message=message, context=context, context_items=context_items)
        dispatch = objective.dispatch_path()
        runtime_trace(
            "objective.interpreted", run_id=run_id, kind=objective.kind.value,
            operations=[op.value for op in objective.operations], resources=list(objective.resource_hints),
            complexity=objective.complexity.value, dispatch=dispatch.value,
            external_state=objective.requires_external_state, mutation=objective.requires_mutation,
            future_wait=objective.requires_future_wait,
        )

        selected = self.context_assembler.select(objective.objective, context_items, budget=self.limits.context_budget)
        runtime_trace(
            "context.selected", run_id=run_id, phase="reason", selected_count=len(selected.items),
            selected_bytes=selected.total_bytes, omitted_count=selected.omitted_count,
            kinds=[item.kind.value for item in selected.items],
        )

        if dispatch is RuntimeDispatchPath.RESPOND:
            answer = await self.model.respond(objective=objective.objective, user_message=message, context_items=selected.as_prompt_items())
            runtime_trace("request.completed", run_id=run_id, dispatch=dispatch.value, cycles=0, capability_calls=0, answer_chars=len(answer))
            return Runtime1Result(message=answer, run_id=run_id, dispatch=dispatch.value, objective_kind=objective.kind.value, cycles=0)

        capabilities = await self._discover(db, kernel=kernel, context=context, query=objective.capability_query())
        if not capabilities:
            answer = await self.model.respond(
                objective=objective.objective, user_message=message, context_items=selected.as_prompt_items(),
                observations=[{"ok": False, "error_code": "no_authorized_capabilities", "message": "No currently authorized and available capability matched this objective."}],
            )
            runtime_trace("request.completed", run_id=run_id, dispatch=dispatch.value, cycles=0, capability_calls=0, error_code="no_authorized_capabilities")
            return Runtime1Result(message=answer, run_id=run_id, dispatch=dispatch.value, objective_kind=objective.kind.value, cycles=0, error_code="no_authorized_capabilities")

        governed = GovernedAgentRuntime(kernel=kernel, settings=self.settings)
        observations: list[dict[str, Any]] = []
        calls: list[str] = []
        signatures: Counter[str] = Counter()
        mutation_count = 0
        discovery_count = 1
        failure_count = 0
        max_cycles = 1 if dispatch is RuntimeDispatchPath.DIRECT_CAPABILITY else self.limits.max_cycles

        for cycle in range(1, max_cycles + 1):
            raw_decision = await self.model.decide(
                objective=objective.objective, user_message=message,
                context_items=selected.as_prompt_items(), observations=observations[-self.limits.max_observations:],
                capabilities=self._cards(capabilities), remaining_steps=max_cycles - cycle + 1,
                remaining_mutations=max(0, self.limits.max_mutations - mutation_count),
            )
            try:
                decision = self._decode_decision(raw_decision)
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
                if isinstance(raw_decision, Mapping):
                    diagnostic = json.dumps(dict(raw_decision), ensure_ascii=False, sort_keys=True, default=str)
                    top_level_keys = sorted(str(key) for key in raw_decision.keys())[:30]
                elif isinstance(raw_decision, bytes):
                    diagnostic = raw_decision.decode("utf-8", errors="replace")
                    top_level_keys = []
                else:
                    diagnostic = str(raw_decision)
                    top_level_keys = []
                runtime_trace(
                    "decision.rejected",
                    run_id=run_id,
                    cycle=cycle,
                    error_type=type(error).__name__,
                    raw_type=type(raw_decision).__name__,
                    top_level_keys=top_level_keys,
                    output_bytes=len(diagnostic.encode("utf-8")),
                    output_sha256_16=fingerprint(diagnostic),
                )
                return Runtime1Result(message="I could not safely interpret the model's next action.", run_id=run_id, dispatch=dispatch.value, objective_kind=objective.kind.value, cycles=cycle, capability_calls=tuple(calls), error_code="invalid_agent_decision")

            move = str(decision["move"])
            runtime_trace("decision.accepted", run_id=run_id, cycle=cycle, move=move, candidate_count=len(capabilities))
            if move == "finish":
                answer = str(decision["message"]).strip()
                runtime_trace("request.completed", run_id=run_id, dispatch=dispatch.value, cycles=cycle, capability_calls=len(calls), answer_chars=len(answer))
                return Runtime1Result(message=answer, run_id=run_id, dispatch=dispatch.value, objective_kind=objective.kind.value, cycles=cycle, capability_calls=tuple(calls))

            if move == "discover":
                if dispatch is RuntimeDispatchPath.DIRECT_CAPABILITY or discovery_count >= self.limits.max_discoveries:
                    return Runtime1Result(message="I reached the capability-discovery limit before completing this request.", run_id=run_id, dispatch=dispatch.value, objective_kind=objective.kind.value, cycles=cycle, capability_calls=tuple(calls), error_code="capability_discovery_budget_exhausted")
                query = " ".join(str(decision["query"]).split())
                capabilities = await self._discover(db, kernel=kernel, context=context, query=query)
                discovery_count += 1
                if not capabilities:
                    observations.append({"ok": False, "error_code": "no_capability_match", "query_sha256_16": fingerprint(query)})
                continue

            capability_id = str(decision["capability_id"]).strip().lower()
            allowed = {spec.id: spec for spec in capabilities}
            spec = allowed.get(capability_id)
            if spec is None:
                runtime_trace("capability.rejected", run_id=run_id, cycle=cycle, capability_id=capability_id, reason="outside_candidate_set")
                return Runtime1Result(message="The model selected a capability outside the authorized candidate set.", run_id=run_id, dispatch=dispatch.value, objective_kind=objective.kind.value, cycles=cycle, capability_calls=tuple(calls), error_code="capability_not_authorized_for_decision")
            arguments = dict(decision["arguments"])
            try:
                validate_schema(arguments, spec.input_schema)
            except SchemaValidationError:
                observations.append({"capability_id": capability_id, "ok": False, "error_code": "invalid_arguments"})
                failure_count += 1
                runtime_trace("capability.arguments_rejected", run_id=run_id, cycle=cycle, capability_id=capability_id, argument_keys=sorted(arguments))
                if dispatch is RuntimeDispatchPath.DIRECT_CAPABILITY or failure_count >= self.limits.max_failures:
                    break
                continue

            signature = hashlib.sha256((capability_id + "\0" + json.dumps(arguments, sort_keys=True, separators=(",", ":"))).encode("utf-8")).hexdigest()
            signatures[signature] += 1
            if signatures[signature] > 2:
                runtime_trace("loop.detected", run_id=run_id, cycle=cycle, capability_id=capability_id)
                return Runtime1Result(message="I stopped because the agent began repeating the same action.", run_id=run_id, dispatch=dispatch.value, objective_kind=objective.kind.value, cycles=cycle, capability_calls=tuple(calls), error_code="agent_loop_detected")

            if spec.risk is not CapabilityRisk.READ_ONLY:
                if mutation_count >= self.limits.max_mutations:
                    return Runtime1Result(message="I reached the mutation budget before completing this request.", run_id=run_id, dispatch=dispatch.value, objective_kind=objective.kind.value, cycles=cycle, capability_calls=tuple(calls), error_code="mutation_budget_exhausted")
                mutation_count += 1

            runtime_trace(
                "capability.started", run_id=run_id, cycle=cycle, capability_id=capability_id,
                risk=spec.risk.value, approval_required=spec.approval_required,
                argument_keys=sorted(arguments), arguments_sha256_16=fingerprint(json.dumps(arguments, sort_keys=True, default=str)),
            )
            result = await governed.execute_step(
                db, context=context, run_id=run_id, goal=objective.objective,
                step=AgentPlanStep(step_id=f"interactive-{cycle:03d}", capability_id=capability_id, arguments=arguments),
            )
            calls.append(capability_id)
            runtime_trace(
                "capability.completed", run_id=run_id, cycle=cycle, capability_id=capability_id,
                status=result.status.value, kernel_run_id=result.kernel_run_id, error_code=result.error_code,
                result_keys=sorted((result.result or {}).keys()),
            )

            if result.status is AgentStepStatus.WAITING_APPROVAL:
                return Runtime1Result(message="This action requires approval before I can continue.", run_id=run_id, dispatch=dispatch.value, objective_kind=objective.kind.value, cycles=cycle, capability_calls=tuple(calls), approval_id=result.approval_id, error_code="approval_required")
            if result.status is AgentStepStatus.EXECUTION_UNCERTAIN:
                return Runtime1Result(message="I stopped because the outcome of a mutating action is uncertain and must be reconciled before retrying.", run_id=run_id, dispatch=dispatch.value, objective_kind=objective.kind.value, cycles=cycle, capability_calls=tuple(calls), error_code="execution_outcome_uncertain")

            observations.append(self._bounded_observation(capability_id=capability_id, result=result.result, error_code=result.error_code if result.status is AgentStepStatus.FAILED else None, error=result.error))
            if result.status is AgentStepStatus.FAILED:
                failure_count += 1
                if failure_count >= self.limits.max_failures:
                    break

            if dispatch is RuntimeDispatchPath.DIRECT_CAPABILITY:
                answer = await self.model.respond(objective=objective.objective, user_message=message, context_items=selected.as_prompt_items(), observations=observations[-self.limits.max_observations:])
                runtime_trace("request.completed", run_id=run_id, dispatch=dispatch.value, cycles=cycle, capability_calls=len(calls), answer_chars=len(answer))
                return Runtime1Result(message=answer, run_id=run_id, dispatch=dispatch.value, objective_kind=objective.kind.value, cycles=cycle, capability_calls=tuple(calls))

        answer = await self.model.respond(objective=objective.objective, user_message=message, context_items=selected.as_prompt_items(), observations=observations[-self.limits.max_observations:])
        runtime_trace("request.completed", run_id=run_id, dispatch=dispatch.value, cycles=max_cycles, capability_calls=len(calls), error_code="agent_budget_exhausted", answer_chars=len(answer))
        return Runtime1Result(message=answer, run_id=run_id, dispatch=dispatch.value, objective_kind=objective.kind.value, cycles=max_cycles, capability_calls=tuple(calls), error_code="agent_budget_exhausted")