from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import uuid4


class WorkflowValidationError(ValueError):
    pass


class WorkflowExecutionError(RuntimeError):
    pass


_MAX_DEPTH = 8
_MAX_NODES = 80
_MAX_FOREACH_ITEMS = 50
_ALLOWED_TYPES = {"invoke", "model", "if", "foreach", "set", "emit", "stop"}


def _walk_nodes(nodes: list[dict], *, depth: int = 0) -> list[dict]:
    if depth > _MAX_DEPTH:
        raise WorkflowValidationError("workflow_nesting_too_deep")
    output: list[dict] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise WorkflowValidationError("workflow_node_must_be_object")
        node_type = str(node.get("type") or "").strip()
        if node_type not in _ALLOWED_TYPES:
            raise WorkflowValidationError(f"unsupported_workflow_node:{node_type or 'missing'}")
        output.append(node)
        if node_type == "if":
            output.extend(_walk_nodes(list(node.get("then") or []), depth=depth + 1))
            output.extend(_walk_nodes(list(node.get("else") or []), depth=depth + 1))
        elif node_type == "foreach":
            output.extend(_walk_nodes(list(node.get("steps") or []), depth=depth + 1))
    return output


def validate_workflow(value: dict | None) -> dict | None:
    """Validate the bounded declarative workflow language used by durable Tasks."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise WorkflowValidationError("workflow_must_be_object")
    steps = value.get("steps")
    if not isinstance(steps, list) or not steps:
        raise WorkflowValidationError("workflow_steps_required")
    flattened = _walk_nodes(steps)
    if len(flattened) > _MAX_NODES:
        raise WorkflowValidationError("workflow_too_large")
    seen_ids: set[str] = set()
    for node in flattened:
        node_type = str(node.get("type") or "")
        node_id = str(node.get("id") or "").strip()
        if node_type in {"invoke", "model", "set", "emit"} and not node_id:
            raise WorkflowValidationError(f"workflow_node_id_required:{node_type}")
        if node_id:
            if node_id in seen_ids:
                raise WorkflowValidationError(f"duplicate_workflow_node_id:{node_id}")
            seen_ids.add(node_id)
        if node_type == "invoke":
            capability = str(node.get("capability") or "").strip()
            if not capability or len(capability) > 200:
                raise WorkflowValidationError("workflow_capability_required")
            if not isinstance(node.get("args") or {}, dict):
                raise WorkflowValidationError("workflow_invoke_args_must_be_object")
        elif node_type == "model":
            objective = str(node.get("objective") or "").strip()
            if not objective or len(objective) > 8000:
                raise WorkflowValidationError("workflow_model_objective_required")
            ai_capability = str(node.get("ai_capability") or "").strip()
            if ai_capability and (
                not ai_capability.startswith("ai.") or len(ai_capability) > 200
            ):
                raise WorkflowValidationError("workflow_ai_capability_invalid")
        elif node_type == "if":
            if not isinstance(node.get("condition"), dict):
                raise WorkflowValidationError("workflow_if_condition_required")
        elif node_type == "foreach":
            if node.get("items") is None or not isinstance(node.get("steps"), list):
                raise WorkflowValidationError("workflow_foreach_items_and_steps_required")
            max_items = int(node.get("max_items") or _MAX_FOREACH_ITEMS)
            if max_items < 1 or max_items > _MAX_FOREACH_ITEMS:
                raise WorkflowValidationError("workflow_foreach_limit_out_of_range")
        elif node_type == "set":
            target = str(node.get("target") or "").strip()
            if not (target.startswith("state.") or target.startswith("vars.")):
                raise WorkflowValidationError("workflow_set_target_must_be_state_or_vars")
    normalized = dict(value)
    normalized["version"] = int(value.get("version") or 1)
    normalized["steps"] = steps
    return normalized


def _lookup(root: Any, path: str) -> Any:
    current = root
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _resolve_ref(value: str, env: dict[str, Any]) -> Any:
    path = value[1:]
    if not path:
        return None
    first, _, rest = path.partition(".")
    return _lookup(env.get(first), rest)


def resolve_value(value: Any, env: dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value.startswith("$"):
            return _resolve_ref(value, env)
        output = value
        for key in list(env):
            marker = "{{" + key + "}}"
            if marker in output:
                replacement = env.get(key)
                output = output.replace(marker, str(replacement if replacement is not None else ""))
        return output
    if isinstance(value, list):
        return [resolve_value(item, env) for item in value]
    if isinstance(value, dict):
        return {key: resolve_value(item, env) for key, item in value.items()}
    return value


def _condition(value: dict, env: dict[str, Any]) -> bool:
    left = resolve_value(value.get("left"), env)
    op = str(value.get("op") or "truthy").lower()
    right = resolve_value(value.get("right"), env)
    if op == "truthy":
        return bool(left)
    if op == "falsy":
        return not bool(left)
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "contains":
        try:
            return right in left
        except TypeError:
            return False
    try:
        if op == "gt":
            return left > right
        if op == "gte":
            return left >= right
        if op == "lt":
            return left < right
        if op == "lte":
            return left <= right
    except TypeError:
        return False
    raise WorkflowExecutionError(f"unsupported_condition_operator:{op}")


def _assign(target: str, value: Any, env: dict[str, Any]) -> None:
    root_name, _, rest = target.partition(".")
    if root_name not in {"state", "vars"} or not rest:
        raise WorkflowExecutionError("invalid_set_target")
    root = env.setdefault(root_name, {})
    parts = rest.split(".")
    current = root
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _stable_call_id(run_key: str, node_path: str, capability: str) -> str:
    """Produce a compact deterministic call id for ActionService idempotency."""
    digest = hashlib.sha256(
        f"{run_key}|{node_path}|{capability}".encode("utf-8")
    ).hexdigest()[:48]
    return f"wf:{digest}"


def _json_from_model(value: str) -> Any:
    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


_LEGACY_MODEL_CAPABILITY_TO_AI = {
    "text": "ai.generate",
    "generation": "ai.generate",
    "summarization": "ai.generate",
    "reasoning": "ai.reason",
    "planning": "ai.plan",
    "coding": "ai.code.generate",
}


def _workflow_model_target(node: dict[str, Any]) -> tuple[str, str | None]:
    """Resolve an existing model node onto the semantic AI capability bus.

    Explicit ``ai_capability`` is the new contract.  Common historical model
    capabilities map onto ``ai.*`` without changing workflow definitions. Unknown
    historical capabilities keep using ``model.invoke`` so older workflows retain
    access to specialist capabilities that do not yet have a semantic alias.
    """
    explicit = str(node.get("ai_capability") or "").strip()
    if explicit:
        return explicit, None
    legacy = str(node.get("capability") or "reasoning").strip() or "reasoning"
    if legacy.startswith("ai."):
        return legacy, None
    semantic = _LEGACY_MODEL_CAPABILITY_TO_AI.get(legacy.lower())
    if semantic:
        return semantic, None
    return "model.invoke", legacy


@dataclass(slots=True)
class WorkflowResult:
    output: Any
    state: dict[str, Any]
    values: dict[str, Any]
    stopped: bool = False


AgentInvoker = Callable[[str, dict[str, Any]], Awaitable[Any]]


class WorkflowExecutor:
    """Deterministic interpreter whose side effects still cross PluginAgentHarness.

    The module deliberately does not import the capability harness at import time.
    ``capabilities.defaults`` owns the Task provider and the harness owns the default
    registry, so eager imports would form a cycle. The existing harness is resolved
    lazily only when a workflow capability is actually executed.
    """

    def __init__(self, harness: Any | None = None) -> None:
        self.harness = harness

    def _harness(self):
        if self.harness is None:
            from packages.capabilities.agent_harness import PluginAgentHarness

            self.harness = PluginAgentHarness()
        return self.harness

    async def _invoke_workspace(
        self,
        capability: str,
        args: dict[str, Any],
        context: Any,
        *,
        call_id: str,
    ) -> dict[str, Any]:
        harness = self._harness()
        authority = await harness.authority_for(context)
        if not authority:
            return {"ok": False, "error": "workflow_execution_not_authorized"}
        registry = await harness.registry_for(context)
        try:
            definition = registry.definition(capability)
        except LookupError:
            return {"ok": False, "error": "workflow_capability_not_registered"}
        availability = registry.availability(
            context.tenant_id,
            definition.id,
            authority=authority,
        )
        if not availability.available or not harness.capability_authorized(
            definition.id, authority, context
        ):
            return {
                "ok": False,
                "error": "workflow_capability_unavailable",
                "availability": availability.as_dict(),
            }
        view = await harness.session_view_for(
            context,
            authority=authority,
            registry=registry,
        )
        view.expose([definition.id])
        return await harness.invoke(
            definition.id,
            args,
            context,
            call_id=call_id,
        )

    async def execute(
        self,
        workflow: dict,
        *,
        context: Any,
        trigger: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        agent_invoke: AgentInvoker | None = None,
    ) -> WorkflowResult:
        spec = validate_workflow(workflow)
        if spec is None:
            raise WorkflowExecutionError("workflow_required")
        metadata = getattr(context, "metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        run_key = str(metadata.get("workflow_run_key") or uuid4().hex)
        env: dict[str, Any] = {
            "trigger": dict(trigger or {}),
            "state": dict(state or {}),
            "vars": {},
        }
        output: Any = None
        stopped = False

        async def run_steps(
            nodes: list[dict],
            *,
            local_env: dict[str, Any],
            path_prefix: str,
        ) -> None:
            nonlocal output, stopped
            for position, node in enumerate(nodes):
                if stopped:
                    return
                node_type = str(node.get("type"))
                node_id = str(node.get("id") or "")
                label = node_id or f"{node_type}-{position}"
                node_path = f"{path_prefix}.{label}" if path_prefix else label
                if node_type == "invoke":
                    capability = str(node.get("capability"))
                    args = resolve_value(node.get("args") or {}, local_env)
                    result = await self._invoke_workspace(
                        capability,
                        args,
                        context,
                        call_id=_stable_call_id(run_key, node_path, capability),
                    )
                    if not result.get("ok"):
                        if bool(node.get("continue_on_error")):
                            local_env[node_id] = result
                            continue
                        raise WorkflowExecutionError(
                            f"capability_failed:{capability}:{result.get('error') or 'unknown'}"
                        )
                    local_env[node_id] = result.get("observation", result)
                elif node_type == "model":
                    target_capability, legacy_model_capability = _workflow_model_target(node)
                    model_args = {
                        "objective": str(resolve_value(node.get("objective"), local_env) or "")[:8000],
                        "context": json.dumps(
                            resolve_value(node.get("context") or {}, local_env),
                            ensure_ascii=False,
                            default=str,
                        )[:12000],
                        "prefer_tags": list(node.get("prefer_tags") or [])[:8],
                        "avoid_tags": list(node.get("avoid_tags") or [])[:8],
                        "prefer_free": bool(node.get("prefer_free", True)),
                    }
                    latency_class = str(node.get("latency_class") or "").strip()
                    if latency_class:
                        model_args["latency_class"] = latency_class
                    if legacy_model_capability is not None:
                        model_args["capability"] = legacy_model_capability
                    result = await self._invoke_workspace(
                        target_capability,
                        model_args,
                        context,
                        call_id=_stable_call_id(run_key, node_path, target_capability),
                    )
                    if not result.get("ok"):
                        raise WorkflowExecutionError(
                            f"model_invoke_failed:{result.get('error') or 'unknown'}"
                        )
                    observation = result.get("observation", result)
                    content = observation.get("content") if isinstance(observation, dict) else observation
                    if bool(node.get("parse_json")) and isinstance(content, str):
                        try:
                            content = _json_from_model(content)
                        except json.JSONDecodeError as error:
                            raise WorkflowExecutionError("model_output_not_json") from error
                    local_env[node_id] = content
                elif node_type == "if":
                    take_then = _condition(node.get("condition") or {}, local_env)
                    branch = node.get("then") if take_then else node.get("else")
                    await run_steps(
                        list(branch or []),
                        local_env=local_env,
                        path_prefix=f"{node_path}.{'then' if take_then else 'else'}",
                    )
                elif node_type == "foreach":
                    items = resolve_value(node.get("items"), local_env)
                    if not isinstance(items, (list, tuple)):
                        items = []
                    alias = str(node.get("as") or "item").strip() or "item"
                    limit = min(int(node.get("max_items") or _MAX_FOREACH_ITEMS), _MAX_FOREACH_ITEMS)
                    for index, item in enumerate(list(items)[:limit]):
                        child = dict(local_env)
                        child[alias] = item
                        child["index"] = index
                        await run_steps(
                            list(node.get("steps") or []),
                            local_env=child,
                            path_prefix=f"{node_path}.item-{index}",
                        )
                        local_env["state"] = child.get("state", local_env.get("state", {}))
                        local_env["vars"] = child.get("vars", local_env.get("vars", {}))
                elif node_type == "set":
                    value = resolve_value(node.get("value"), local_env)
                    _assign(str(node.get("target")), value, local_env)
                    local_env[node_id] = value
                elif node_type == "emit":
                    output = resolve_value(node.get("value"), local_env)
                    local_env[node_id] = output
                elif node_type == "stop":
                    stopped = True
                    return

        await run_steps(spec["steps"], local_env=env, path_prefix="")
        values = {
            key: value
            for key, value in env.items()
            if key not in {"trigger", "state", "vars"}
        }
        return WorkflowResult(
            output=output,
            state=dict(env.get("state") or {}),
            values=values,
            stopped=stopped,
        )