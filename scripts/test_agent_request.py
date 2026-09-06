from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.agent_runtime.context import ContextItem, ContextKind
from packages.agent_runtime.inference import OpenAICompatibleAgentModel
from packages.agent_runtime.objective import ObjectiveInterpreter
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContext,
    PERSONAL_EXECUTION_PERMISSIONS,
    ScopeKind,
)
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send one raw prompt through Operly's configured objective interpreter and "
            "show the semantic route plus bounded capability candidates. No capability is executed."
        )
    )
    parser.add_argument("prompt", help="raw request to test")
    parser.add_argument("--scope", choices=("personal", "workspace"), default="personal")
    parser.add_argument(
        "--context",
        action="append",
        default=[],
        help="optional relevant conversation line; repeat to add more than one",
    )
    parser.add_argument("--limit", type=int, default=12, help="maximum capability candidates to display")
    return parser.parse_args()


def _execution_context(scope: str) -> ExecutionContext:
    if scope == "workspace":
        return ExecutionContext(
            workspace_id="manual-test-workspace",
            user_id="manual-test-user",
            membership_id="manual-test-membership",
            role="owner",
            permissions=frozenset(),
            channel="operator_manual_test",
            surface=SurfaceKind.WORKSPACE_PRIVATE,
            conversation_id="manual-test-workspace-conversation",
            scope_kind=ScopeKind.WORKSPACE,
            focus_workspace_id="manual-test-workspace",
            principal_id="user:manual-test-user",
            workspace_mode="full",
        )
    return ExecutionContext(
        workspace_id=None,
        user_id="manual-test-user",
        membership_id=None,
        role="personal_owner",
        permissions=PERSONAL_EXECUTION_PERMISSIONS,
        channel="operator_manual_test",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id="manual-test-personal-conversation",
        scope_kind=ScopeKind.PERSONAL,
        principal_id="user:manual-test-user",
        workspace_mode="personal",
    )


def _context_items(lines: list[str]) -> tuple[ContextItem, ...]:
    return tuple(
        ContextItem(
            key=f"manual:{index}",
            kind=ContextKind.CONVERSATION,
            text=line,
            relevance=1.0,
            priority=50,
        )
        for index, line in enumerate(lines, 1)
        if str(line).strip()
    )


def _registry(scope: str):
    if scope == "workspace":
        return build_workspace_runtime().registry
    return build_personal_runtime().registry


async def _main() -> int:
    args = _args()
    context = _execution_context(args.scope)
    model = OpenAICompatibleAgentModel()
    interpreter = ObjectiveInterpreter(
        model=model,
        settings=AgentRuntimeSettings(enabled=True),
    )
    objective = await interpreter.interpret(
        message=args.prompt,
        context=context,
        context_items=_context_items(args.context),
    )

    candidates: list[dict[str, object]] = []
    if objective.requires_external_state:
        specs = _registry(args.scope).search(
            objective.capability_query(),
            context=context,
            effective_only=True,
            limit=max(1, min(args.limit, 25)),
        )
        candidates = [
            {
                "id": spec.id,
                "name": spec.display_name,
                "risk": spec.risk.value,
                "approval_required": bool(spec.approval_required),
            }
            for spec in specs
        ]

    output = {
        "provider": model.route.provider,
        "model": model.route.model_id,
        "scope": args.scope,
        "prompt": args.prompt,
        "objective": objective.objective,
        "kind": objective.kind.value,
        "operations": [operation.value for operation in objective.operations],
        "resource_hints": list(objective.resource_hints),
        "requires_external_state": objective.requires_external_state,
        "requires_mutation": objective.requires_mutation,
        "requires_future_wait": objective.requires_future_wait,
        "complexity": objective.complexity.value,
        "dispatch": objective.dispatch_path().value,
        "capability_query": objective.capability_query(),
        "capability_candidates": candidates,
        "capabilities_executed": False,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
