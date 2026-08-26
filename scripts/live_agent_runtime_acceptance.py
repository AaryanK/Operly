"""Live model-backed acceptance checks for the canonical Operly agent runtime.

This script is intentionally NOT part of ordinary hermetic CI. It must run in an
environment with the same model-provider configuration used by Operly. It exercises
real model inference through AgentRuntime/ModelPool while keeping business side effects
synthetic and deterministic.

Usage:
    python scripts/live_agent_runtime_acceptance.py

Exit status is non-zero unless every live scenario passes.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from packages.agents.capability_rescue import attempt_capability_rescue, has_execution_evidence
from packages.agents.runtime import AgentRuntime
from packages.coding_harness.model_client import coding_model_client
from packages.coding_harness.objective_audit import audit_generated_source
from packages.coding_harness.opencode_agent import CapabilityCodingAgent
from packages.model_runtime import InferenceBudget, model_for_role


SYSTEM = """
You are OPERLY, the governed AI operating layer for a business.
The application controls identity, workspace, permissions, and available capabilities.
For operational requests, use supplied capabilities and continue until the requested
business operation has verified evidence. The initial tool list can be incomplete: use
capability.search then capability.describe when an operation you need is not exposed.
Do not claim success without a verified tool result. Do not invent capability IDs.
""".strip()

# Keep the acceptance run within the same small/tool-driven completion envelope used
# by the production agent. Explicit budgets also prevent a provider from rejecting a
# perfectly small tool turn because a legacy long-form max_tokens reservation was used.
AGENT_BUDGET = InferenceBudget(
    timeout_seconds=45.0,
    attempts_per_model=1,
    max_models=4,
    max_output_tokens=2_048,
)
CODING_BUDGET = InferenceBudget(
    timeout_seconds=60.0,
    attempts_per_model=1,
    max_models=4,
    max_output_tokens=4_096,
)


def _schema(name: str, description: str, properties: dict[str, Any] | None = None, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": dict(properties or {}),
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


SEARCH_SCHEMA = _schema(
    "capability.search",
    "Discover authorized capabilities relevant to an operation you need.",
    {"query": {"type": "string"}, "limit": {"type": "integer"}},
    ("query",),
)
DESCRIBE_SCHEMA = _schema(
    "capability.describe",
    "Inspect and expose exact schemas for discovered authorized capabilities.",
    {"ids": {"type": "array", "items": {"type": "string"}}},
    ("ids",),
)
RUNTIME_CONTEXT_SCHEMA = _schema(
    "runtime.context",
    "Read trusted runtime time/workspace metadata. This is metadata, not business execution.",
)


@dataclass
class SyntheticGovernedHarness:
    capabilities: dict[str, dict[str, Any]]
    root_ids: set[str] = field(default_factory=set)
    exposed: set[str] = field(default_factory=lambda: {"capability.search", "capability.describe", "runtime.context"})
    invocations: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __post_init__(self):
        self.exposed.update(self.root_ids)

    async def schemas(self):
        output = [SEARCH_SCHEMA, DESCRIBE_SCHEMA, RUNTIME_CONTEXT_SCHEMA]
        for capability_id in sorted(self.exposed):
            if capability_id in self.capabilities:
                output.append(self.capabilities[capability_id]["schema"])
        return output

    async def invoke(self, name: str, arguments: dict[str, Any], call_id: str | None):
        del call_id
        self.invocations.append((name, dict(arguments)))
        if name == "runtime.context":
            return {
                "ok": True,
                "status": "VERIFIED",
                "observation": {"workspace": "live-agent-acceptance", "time": "application-controlled"},
            }
        if name == "capability.search":
            rows = []
            for index, (capability_id, item) in enumerate(self.capabilities.items()):
                rows.append(
                    {
                        "id": capability_id,
                        "authorized": True,
                        "display_name": item.get("display_name", capability_id),
                        "description": item["description"],
                        "availability": {"available": True, "reason": "available"},
                        "semantic_score": 0.98 - index * 0.01,
                        "lexical_score": 2.0,
                    }
                )
            return {
                "ok": True,
                "status": "VERIFIED",
                "observation": {
                    "capabilities": rows,
                    "ranked_ids": [row["id"] for row in rows],
                    "sufficient_match": True,
                    "search_again_recommended": False,
                },
            }
        if name == "capability.describe":
            ids = [str(value) for value in arguments.get("ids") or []]
            rows = []
            for capability_id in ids:
                item = self.capabilities.get(capability_id)
                if item is None:
                    continue
                self.exposed.add(capability_id)
                rows.append(
                    {
                        "id": capability_id,
                        "authorized": True,
                        "schema": item["schema"],
                        "availability": {"available": True, "reason": "available"},
                    }
                )
            return {"ok": True, "status": "VERIFIED", "observation": {"capabilities": rows}}
        item = self.capabilities.get(name)
        if item is None or name not in self.exposed:
            return {
                "ok": False,
                "success": False,
                "status": "DENIED",
                "error": "capability_not_exposed",
                "retryable": True,
            }
        evidence = item.get("evidence")
        if callable(evidence):
            evidence = evidence(arguments)
        return {
            "ok": True,
            "status": "VERIFIED",
            "observation": dict(evidence or {"executed": True}),
            "lifecycle": {"completed": True, "verified": True},
        }


async def run_agent_case(
    *,
    name: str,
    objective: str,
    harness: SyntheticGovernedHarness,
    expected_operations: set[str],
) -> dict[str, Any]:
    print(f"[live-agent] START {name}", flush=True)
    model = model_for_role("business_agent")
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": objective},
    ]
    combined_trace = []
    result = await AgentRuntime(max_steps=8, inference_budget=AGENT_BUDGET).run(
        model=model,
        messages=messages,
        schemas=harness.schemas,
        invoke=harness.invoke,
        inference_metadata={
            "runtime_run_id": f"live-{name}",
            "tenant_id": "live-agent-workspace",
            "user_id": "live-agent-user",
            "surface": "workspace_private",
            "executor_role": "business_agent",
            "live_acceptance": True,
        },
    )
    combined_trace.extend(result.get("trace") or [])

    if not expected_operations.issubset({entry.capability_id for entry in combined_trace if has_execution_evidence([entry])}):
        rescue = await attempt_capability_rescue(
            objective=objective,
            messages=messages,
            invoke=harness.invoke,
        )
        combined_trace.extend(rescue.trace)
        if rescue.applied:
            followup = await AgentRuntime(max_steps=8, inference_budget=AGENT_BUDGET).run(
                model=model,
                messages=messages,
                schemas=harness.schemas,
                invoke=harness.invoke,
                inference_metadata={
                    "runtime_run_id": f"live-{name}-rescued",
                    "tenant_id": "live-agent-workspace",
                    "user_id": "live-agent-user",
                    "surface": "workspace_private",
                    "executor_role": "business_agent",
                    "live_acceptance": True,
                },
            )
            combined_trace.extend(followup.get("trace") or [])
            result = followup

    executed = {
        entry.capability_id
        for entry in combined_trace
        if has_execution_evidence([entry])
    }
    missing = sorted(expected_operations - executed)
    if missing:
        raise AssertionError(
            f"{name}: live agent did not execute required operations {missing}; "
            f"invocations={[item[0] for item in harness.invocations]}"
        )
    outcome = {
        "name": name,
        "passed": True,
        "executed": sorted(executed),
        "invocations": [item[0] for item in harness.invocations],
        "stop_reason": result.get("stop_reason"),
        "message": str(result.get("message") or "")[:500],
    }
    print(f"[live-agent] PASS {name}: {sorted(expected_operations)}", flush=True)
    return outcome


async def live_runtime_suite() -> list[dict[str, Any]]:
    results = []

    results.append(
        await run_agent_case(
            name="crm-create",
            objective="Create a CRM contact named Acme Corp. Do not merely explain how.",
            harness=SyntheticGovernedHarness(
                capabilities={
                    "crm.create_contact": {
                        "display_name": "Create CRM contact",
                        "description": "Create a new contact in the current workspace CRM.",
                        "schema": _schema(
                            "crm.create_contact",
                            "Create a new CRM contact.",
                            {"name": {"type": "string"}},
                            ("name",),
                        ),
                        "evidence": lambda arguments: {"contact_id": "contact-live-1", "name": arguments.get("name")},
                    }
                }
            ),
            expected_operations={"crm.create_contact"},
        )
    )

    results.append(
        await run_agent_case(
            name="multi-step-business",
            objective=(
                "Find the Acme Corp contact, then create a follow-up task for that contact. "
                "Execute both operations and do not stop after lookup."
            ),
            harness=SyntheticGovernedHarness(
                capabilities={
                    "crm.search_contacts": {
                        "description": "Search the current workspace CRM for contacts.",
                        "schema": _schema(
                            "crm.search_contacts",
                            "Search CRM contacts.",
                            {"query": {"type": "string"}},
                            ("query",),
                        ),
                        "evidence": {"contacts": [{"id": "contact-live-1", "name": "Acme Corp"}]},
                    },
                    "task.create": {
                        "description": "Create a governed follow-up task in the workspace.",
                        "schema": _schema(
                            "task.create",
                            "Create a workspace task.",
                            {
                                "title": {"type": "string"},
                                "contact_id": {"type": "string"},
                            },
                            ("title",),
                        ),
                        "evidence": {"task_id": "task-live-1", "created": True},
                    },
                }
            ),
            expected_operations={"crm.search_contacts", "task.create"},
        )
    )

    results.append(
        await run_agent_case(
            name="complex-business-workflow",
            objective=(
                "Find Acme Corp in the CRM, check stock for SKU-WIDGET, create a follow-up task because stock is low, "
                "and send a workspace message summarizing the result. Execute every step, not just a plan."
            ),
            harness=SyntheticGovernedHarness(
                capabilities={
                    "crm.search_contacts": {
                        "description": "Search current workspace CRM contacts.",
                        "schema": _schema(
                            "crm.search_contacts",
                            "Search CRM contacts.",
                            {"query": {"type": "string"}},
                            ("query",),
                        ),
                        "evidence": {"contacts": [{"id": "contact-live-1", "name": "Acme Corp"}]},
                    },
                    "inventory.get_stock": {
                        "description": "Read current stock for a workspace inventory SKU.",
                        "schema": _schema(
                            "inventory.get_stock",
                            "Get inventory stock.",
                            {"sku": {"type": "string"}},
                            ("sku",),
                        ),
                        "evidence": {"sku": "SKU-WIDGET", "quantity": 2},
                    },
                    "task.create": {
                        "description": "Create a governed follow-up task in the workspace.",
                        "schema": _schema(
                            "task.create",
                            "Create a workspace task.",
                            {"title": {"type": "string"}, "contact_id": {"type": "string"}},
                            ("title",),
                        ),
                        "evidence": {"task_id": "task-live-complex", "created": True},
                    },
                    "messaging.send": {
                        "description": "Send a message to the current authorized workspace destination.",
                        "schema": _schema(
                            "messaging.send",
                            "Send a workspace message.",
                            {"message": {"type": "string"}},
                            ("message",),
                        ),
                        "evidence": {"message_id": "message-live-1", "sent": True},
                    },
                }
            ),
            expected_operations={
                "crm.search_contacts",
                "inventory.get_stock",
                "task.create",
                "messaging.send",
            },
        )
    )

    results.append(
        await run_agent_case(
            name="software-root-routing",
            objective=(
                "Build a working hosted web application for employee clock-in and clock-out using camera QR scanning. "
                "Create the application rather than returning source snippets or a plan."
            ),
            harness=SyntheticGovernedHarness(
                root_ids={"software.build"},
                capabilities={
                    "software.build": {
                        "description": "Create and durably build a canonical SoftwareProject with isolated acceptance verification.",
                        "schema": _schema(
                            "software.build",
                            "Build a complete hosted software project.",
                            {
                                "objective": {"type": "string"},
                                "name": {"type": "string"},
                            },
                            ("objective",),
                        ),
                        "evidence": {
                            "project_id": "project-live-1",
                            "build_success": True,
                            "process_start_success": True,
                            "health_check_success": True,
                            "acceptance_check_success": True,
                            "preview_available": True,
                        },
                    }
                },
            ),
            expected_operations={"software.build"},
        )
    )

    return results


def qr_specification() -> str:
    return json.dumps(
        {
            "objective": (
                "Create a browser-hosted employee clock-in and clock-out application. "
                "Employees must use the browser camera to scan real QR codes. A decoded clock-in QR performs clock-in, "
                "a decoded clock-out QR performs clock-out, and attendance status/history must persist."
            ),
            "requirements": [
                {
                    "id": "R-001",
                    "requirement": "Use navigator.mediaDevices.getUserMedia with a visible browser video/capture surface.",
                    "mandatory": True,
                    "acceptance": ["Executable browser source opens the camera rather than simulating it."],
                },
                {
                    "id": "R-002",
                    "requirement": "Decode actual QR frames with BarcodeDetector configured for qr_code or a real QR decoder library.",
                    "mandatory": True,
                    "acceptance": ["Decoded scan data drives the clock workflow; buttons/comments are not substitutes."],
                },
                {
                    "id": "R-003",
                    "requirement": "Decoded clock-in and clock-out QR data drive distinct clock-in and clock-out operations.",
                    "mandatory": True,
                    "acceptance": ["Executable tests exercise both operations."],
                },
            ],
            "completionPolicy": {"objectiveAuditRequired": True},
        },
        ensure_ascii=False,
    )


async def _coding_progress(event: dict[str, Any]) -> None:
    phase = str(event.get("phase") or "")
    summary = str(event.get("summary") or "")
    step = event.get("step")
    tool = event.get("tool")
    print(
        f"[live-coding] step={step} phase={phase}"
        + (f" tool={tool}" if tool else "")
        + (f" :: {summary}" if summary else ""),
        flush=True,
    )


async def live_coding_case() -> dict[str, Any]:
    print("[live-coding] START qr-software-build", flush=True)
    client = coding_model_client(budget=CODING_BUDGET)
    agent = CapabilityCodingAgent(client=client, progress_callback=_coding_progress)
    specification = qr_specification()
    result = await agent.build(specification)
    audit = audit_generated_source(json.loads(specification), result.files)
    if not bool(audit.get("verified")):
        raise AssertionError(
            "qr-software-build: deterministic objective audit rejected live generated source: "
            + json.dumps(
                {
                    "message": audit.get("message"),
                    "behaviorGaps": audit.get("behaviorGaps"),
                    "unmetRequirements": audit.get("unmetRequirements"),
                    "runtimeContractGaps": audit.get("runtimeContractGaps"),
                },
                ensure_ascii=False,
            )
        )
    outcome = {
        "name": "qr-software-build",
        "passed": True,
        "model_provider": result.model_provider,
        "model_id": result.model_id,
        "files": [item.path for item in result.files],
        "changed_paths": result.changed_paths,
        "audit": {
            "verified": audit.get("verified"),
            "behaviorGaps": audit.get("behaviorGaps"),
            "runtimeContractGaps": audit.get("runtimeContractGaps"),
        },
    }
    print(
        f"[live-coding] PASS qr-software-build provider={result.model_provider} model={result.model_id}",
        flush=True,
    )
    return outcome


async def main() -> int:
    outcomes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    try:
        outcomes.extend(await live_runtime_suite())
    except Exception as error:  # noqa: BLE001 - acceptance runner must report a bounded failure
        failures.append({"suite": "agent-runtime", "error": f"{type(error).__name__}: {error}"})

    try:
        outcomes.append(await live_coding_case())
    except Exception as error:  # noqa: BLE001
        failures.append({"suite": "coding-agent", "error": f"{type(error).__name__}: {error}"})

    print(json.dumps({"outcomes": outcomes, "failures": failures}, ensure_ascii=False, indent=2, default=str), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
