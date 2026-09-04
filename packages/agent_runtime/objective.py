from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence

from packages.agent_runtime.context import ContextAssembler, ContextBudget, ContextItem, ContextSlice
from packages.agent_runtime.runtime import AgentRuntimeDisabled, AgentRuntimeSettings
from packages.security.execution_context import ExecutionContext


OBJECTIVE_INTERPRETER_INSTRUCTIONS = (
    "Interpret the user's objective; do not choose a provider or capability. Tool use is optional. "
    "If the request can be satisfied from the current message, supplied relevant context, or ordinary "
    "model reasoning, classify it as a response path with no external state. Use external-state paths "
    "only when connected Personal/Workspace data or a state-changing action is genuinely required. "
    "Trusted scope/surface metadata describes where the request arrived; never output or invent "
    "workspace IDs, principals, roles, permissions, approvals, credentials, or provider routes. "
    "Return only the exact JSON fields requested by the schema."
)

_AUTHORITY_SHAPED_FIELDS = frozenset(
    {
        "workspace_id",
        "user_id",
        "principal_id",
        "membership_id",
        "role",
        "permissions",
        "approval_id",
        "credentials",
        "provider_id",
        "provider_url",
        "scope",
        "scope_kind",
        "surface",
    }
)


class ObjectiveInterpretationError(RuntimeError):
    def __init__(self, message: str, *, code: str = "objective_interpretation_failed") -> None:
        super().__init__(message)
        self.code = code


class ObjectiveKind(StrEnum):
    RESPOND = "respond"
    RETRIEVE = "retrieve"
    ACT = "act"
    COMPOSITE = "composite"
    WAIT = "wait"


class ObjectiveOperation(StrEnum):
    RESPOND = "respond"
    RETRIEVE = "retrieve"
    ANALYZE = "analyze"
    TRANSFORM = "transform"
    ACT = "act"
    WAIT = "wait"


class ObjectiveComplexity(StrEnum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    COMPOUND = "compound"
    OPEN_ENDED = "open_ended"


class RuntimeDispatchPath(StrEnum):
    RESPOND = "respond"
    DIRECT_CAPABILITY = "direct_capability"
    AGENT_LOOP = "agent_loop"
    WAIT = "wait"


@dataclass(frozen=True, slots=True)
class ObjectiveInterpreterLimits:
    max_message_chars: int = 12_000
    max_objective_chars: int = 1_600
    max_resource_hints: int = 8
    max_resource_hint_chars: int = 80
    max_request_bytes: int = 24 * 1024
    max_output_bytes: int = 12 * 1024
    context_budget: ContextBudget = field(
        default_factory=lambda: ContextBudget(
            max_items=5,
            max_bytes=8 * 1024,
            max_item_bytes=3 * 1024,
        )
    )

    def __post_init__(self) -> None:
        if not 256 <= self.max_message_chars <= 64_000:
            raise ValueError("max_message_chars must be between 256 and 64000")
        if not 64 <= self.max_objective_chars <= 4_000:
            raise ValueError("max_objective_chars must be between 64 and 4000")
        if not 0 <= self.max_resource_hints <= 32:
            raise ValueError("max_resource_hints must be between 0 and 32")
        if not 8 <= self.max_resource_hint_chars <= 160:
            raise ValueError("max_resource_hint_chars must be between 8 and 160")
        if self.max_request_bytes <= 0 or self.max_output_bytes <= 0:
            raise ValueError("request/output byte limits must be positive")


@dataclass(frozen=True, slots=True)
class ObjectiveInterpreterRequest:
    message: str
    scope_kind: str
    surface: str
    relevant_context: ContextSlice
    instructions: str = OBJECTIVE_INTERPRETER_INSTRUCTIONS

    def as_dict(self) -> dict[str, Any]:
        return {
            "instructions": self.instructions,
            "request": self.message,
            "trusted_runtime_context": {
                "scope_kind": self.scope_kind,
                "surface": self.surface,
            },
            "relevant_context": self.relevant_context.as_prompt_items(),
            "output_schema": {
                "objective": "concise string",
                "kind": [kind.value for kind in ObjectiveKind],
                "operations": [operation.value for operation in ObjectiveOperation],
                "resource_hints": ["short semantic resource labels; empty when none are needed"],
                "requires_external_state": "boolean",
                "requires_mutation": "boolean",
                "requires_future_wait": "boolean",
                "complexity": [complexity.value for complexity in ObjectiveComplexity],
            },
        }


class ObjectiveInterpreterModel(Protocol):
    async def interpret(
        self,
        request: ObjectiveInterpreterRequest,
    ) -> Mapping[str, Any] | str | bytes:
        """Return structured semantic interpretation only; this interface cannot execute."""
        ...


@dataclass(frozen=True, slots=True)
class ObjectiveIR:
    objective: str
    kind: ObjectiveKind
    operations: tuple[ObjectiveOperation, ...]
    resource_hints: tuple[str, ...]
    requires_external_state: bool
    requires_mutation: bool
    requires_future_wait: bool
    complexity: ObjectiveComplexity

    @property
    def needs_capability_discovery(self) -> bool:
        return self.requires_external_state

    def capability_query(self) -> str:
        """Return the smallest semantic query needed for capability retrieval.

        The raw user message, conversation history, memory payloads and observations
        deliberately do not flow into capability discovery.
        """
        if not self.requires_external_state:
            return ""
        operations = [
            operation.value
            for operation in self.operations
            if operation
            in {
                ObjectiveOperation.RETRIEVE,
                ObjectiveOperation.ACT,
                ObjectiveOperation.WAIT,
            }
        ]
        parts = [self.objective]
        if self.resource_hints:
            parts.append("resources " + " ".join(self.resource_hints))
        if operations:
            parts.append("operations " + " ".join(operations))
        return " | ".join(parts)

    def dispatch_path(self) -> RuntimeDispatchPath:
        if self.requires_future_wait or self.kind is ObjectiveKind.WAIT:
            return RuntimeDispatchPath.WAIT
        if not self.requires_external_state:
            return RuntimeDispatchPath.RESPOND
        if (
            self.kind is ObjectiveKind.COMPOSITE
            or self.complexity in {ObjectiveComplexity.COMPOUND, ObjectiveComplexity.OPEN_ENDED}
            or len(
                {
                    operation
                    for operation in self.operations
                    if operation
                    in {
                        ObjectiveOperation.RETRIEVE,
                        ObjectiveOperation.ACT,
                        ObjectiveOperation.WAIT,
                    }
                }
            )
            > 1
        ):
            return RuntimeDispatchPath.AGENT_LOOP
        return RuntimeDispatchPath.DIRECT_CAPABILITY


class ObjectiveInterpreter:
    """Front-door semantic classifier for Runtime 1.0.

    The model decides meaning, not authority. Only trusted scope/surface labels are
    provided, and the output cannot contain authority-shaped fields. Context is selected
    through a strict relevance/byte budget before model inference.
    """

    _EXPECTED_FIELDS = frozenset(
        {
            "objective",
            "kind",
            "operations",
            "resource_hints",
            "requires_external_state",
            "requires_mutation",
            "requires_future_wait",
            "complexity",
        }
    )

    def __init__(
        self,
        *,
        model: ObjectiveInterpreterModel,
        settings: AgentRuntimeSettings | None = None,
        limits: ObjectiveInterpreterLimits | None = None,
        context_assembler: ContextAssembler | None = None,
    ) -> None:
        self.model = model
        self.settings = settings or AgentRuntimeSettings.from_environment()
        self.limits = limits or ObjectiveInterpreterLimits()
        self.context_assembler = context_assembler or ContextAssembler()

    async def interpret(
        self,
        *,
        message: str,
        context: ExecutionContext,
        context_items: Sequence[ContextItem] = (),
    ) -> ObjectiveIR:
        if not self.settings.enabled:
            raise AgentRuntimeDisabled("Agent runtime is disabled")

        clean_message = " ".join(str(message or "").replace("\x00", " ").split())
        if not clean_message:
            raise ObjectiveInterpretationError(
                "message is required",
                code="invalid_objective_request",
            )
        if len(clean_message) > self.limits.max_message_chars:
            raise ObjectiveInterpretationError(
                "message exceeds objective interpreter limit",
                code="objective_input_too_large",
            )

        selected = self.context_assembler.select(
            clean_message,
            context_items,
            budget=self.limits.context_budget,
        )
        request = ObjectiveInterpreterRequest(
            message=clean_message,
            scope_kind=context.scope_kind.value,
            surface=context.surface.value,
            relevant_context=selected,
        )
        try:
            encoded_request = json.dumps(
                request.as_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ObjectiveInterpretationError(
                "objective interpreter request is not JSON serializable",
                code="invalid_objective_request",
            ) from error
        if len(encoded_request) > self.limits.max_request_bytes:
            raise ObjectiveInterpretationError(
                "objective interpreter request exceeds byte budget",
                code="objective_input_too_large",
            )

        try:
            raw = await self.model.interpret(request)
        except Exception as error:
            raise ObjectiveInterpretationError(
                "objective interpreter model failed",
                code="objective_model_failed",
            ) from error

        return self._validate(self._decode(raw))

    def _decode(self, raw: Mapping[str, Any] | str | bytes) -> Mapping[str, Any]:
        if isinstance(raw, bytes):
            if len(raw) > self.limits.max_output_bytes:
                raise ObjectiveInterpretationError(
                    "objective interpreter output is too large",
                    code="objective_output_too_large",
                )
            try:
                return self._decode_text(raw.decode("utf-8"))
            except UnicodeDecodeError as error:
                raise ObjectiveInterpretationError(
                    "objective interpreter output is not UTF-8",
                    code="invalid_objective_output",
                ) from error

        if isinstance(raw, str):
            if len(raw.encode("utf-8")) > self.limits.max_output_bytes:
                raise ObjectiveInterpretationError(
                    "objective interpreter output is too large",
                    code="objective_output_too_large",
                )
            return self._decode_text(raw)

        if not isinstance(raw, Mapping):
            raise ObjectiveInterpretationError(
                "objective interpreter output must be a JSON object",
                code="invalid_objective_output",
            )
        try:
            encoded = json.dumps(
                raw,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ObjectiveInterpretationError(
                "objective interpreter output is not JSON serializable",
                code="invalid_objective_output",
            ) from error
        if len(encoded) > self.limits.max_output_bytes:
            raise ObjectiveInterpretationError(
                "objective interpreter output is too large",
                code="objective_output_too_large",
            )
        return json.loads(encoded)

    def _decode_text(self, text: str) -> Mapping[str, Any]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise ObjectiveInterpretationError(
                "objective interpreter returned malformed JSON",
                code="invalid_objective_output",
            ) from error
        if not isinstance(payload, dict):
            raise ObjectiveInterpretationError(
                "objective interpreter output must be a JSON object",
                code="invalid_objective_output",
            )
        return payload

    def _validate(self, payload: Mapping[str, Any]) -> ObjectiveIR:
        unexpected = set(payload) - self._EXPECTED_FIELDS
        if unexpected & _AUTHORITY_SHAPED_FIELDS:
            raise ObjectiveInterpretationError(
                "objective interpreter attempted to supply authority-shaped fields",
                code="objective_authority_violation",
            )
        if set(payload) != self._EXPECTED_FIELDS:
            raise ObjectiveInterpretationError(
                "objective interpreter output fields do not match the contract",
                code="invalid_objective_output",
            )

        objective = " ".join(
            str(payload.get("objective") or "").replace("\x00", " ").split()
        )
        if not objective or len(objective) > self.limits.max_objective_chars:
            raise ObjectiveInterpretationError(
                "objective is empty or exceeds the interpreter limit",
                code="invalid_objective_output",
            )

        try:
            kind = ObjectiveKind(str(payload["kind"]).strip().lower())
            complexity = ObjectiveComplexity(
                str(payload["complexity"]).strip().lower()
            )
        except ValueError as error:
            raise ObjectiveInterpretationError(
                "objective kind or complexity is unsupported",
                code="invalid_objective_output",
            ) from error

        raw_operations = payload.get("operations")
        if not isinstance(raw_operations, list) or not raw_operations:
            raise ObjectiveInterpretationError(
                "objective operations must be a non-empty array",
                code="invalid_objective_output",
            )
        operations: list[ObjectiveOperation] = []
        for value in raw_operations:
            try:
                operation = ObjectiveOperation(str(value).strip().lower())
            except ValueError as error:
                raise ObjectiveInterpretationError(
                    "objective contains an unsupported operation",
                    code="invalid_objective_output",
                ) from error
            if operation not in operations:
                operations.append(operation)

        raw_hints = payload.get("resource_hints")
        if not isinstance(raw_hints, list):
            raise ObjectiveInterpretationError(
                "resource_hints must be an array",
                code="invalid_objective_output",
            )
        if len(raw_hints) > self.limits.max_resource_hints:
            raise ObjectiveInterpretationError(
                "too many resource hints",
                code="invalid_objective_output",
            )
        hints: list[str] = []
        for raw_hint in raw_hints:
            hint = re.sub(r"\s+", " ", str(raw_hint or "").strip().lower())
            if not hint or len(hint) > self.limits.max_resource_hint_chars:
                raise ObjectiveInterpretationError(
                    "resource hint is empty or too long",
                    code="invalid_objective_output",
                )
            if hint not in hints:
                hints.append(hint)

        for field_name in (
            "requires_external_state",
            "requires_mutation",
            "requires_future_wait",
        ):
            if not isinstance(payload.get(field_name), bool):
                raise ObjectiveInterpretationError(
                    f"{field_name} must be boolean",
                    code="invalid_objective_output",
                )

        requires_external_state = payload["requires_external_state"]
        requires_mutation = payload["requires_mutation"]
        requires_future_wait = payload["requires_future_wait"]

        if (requires_mutation or requires_future_wait) and not requires_external_state:
            raise ObjectiveInterpretationError(
                "mutation/future wait cannot exist without external state",
                code="inconsistent_objective_output",
            )
        if kind is ObjectiveKind.RESPOND and requires_external_state:
            raise ObjectiveInterpretationError(
                "respond objectives cannot require external state",
                code="inconsistent_objective_output",
            )
        if kind is ObjectiveKind.RETRIEVE and (
            not requires_external_state
            or requires_mutation
            or ObjectiveOperation.RETRIEVE not in operations
        ):
            raise ObjectiveInterpretationError(
                "retrieve objective flags are inconsistent",
                code="inconsistent_objective_output",
            )
        if kind is ObjectiveKind.ACT and (
            not requires_external_state
            or not requires_mutation
            or ObjectiveOperation.ACT not in operations
        ):
            raise ObjectiveInterpretationError(
                "act objective flags are inconsistent",
                code="inconsistent_objective_output",
            )
        if kind is ObjectiveKind.WAIT and (
            not requires_external_state
            or not requires_future_wait
            or ObjectiveOperation.WAIT not in operations
        ):
            raise ObjectiveInterpretationError(
                "wait objective flags are inconsistent",
                code="inconsistent_objective_output",
            )

        return ObjectiveIR(
            objective=objective,
            kind=kind,
            operations=tuple(operations),
            resource_hints=tuple(hints),
            requires_external_state=requires_external_state,
            requires_mutation=requires_mutation,
            requires_future_wait=requires_future_wait,
            complexity=complexity,
        )
