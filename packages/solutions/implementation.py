"""Resolve a runtime implementation from a runtime-neutral SolutionManifest.

The Solution capability graph is product truth. Declarative Studio/managed-app
runtimes are optimization targets only when they can implement the complete
contract. Anything outside that finite declarative envelope falls through to the
isolated generated full-stack runtime instead of being approximated by a mock UI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from packages.solutions.manifest import SolutionManifest


# Capabilities that the current managed ApplicationManifest runtime can represent
# and execute without arbitrary source generation. Keep this intentionally
# conservative: routing to generated source is safer than claiming support that
# the declarative runtime does not actually provide.
MANAGED_RUNTIME_CAPABILITIES = frozenset(
    {
        "ui.public_web",
        "ui.workspace_dashboard",
        "ui.primary_app",
        "server.http_api",
        "data.relational",
        "auth.sessions",
        "auth.roles",
        "workflow.state_machine",
    }
)


# Browser/device capabilities are intentionally semantic, not framework-specific.
# They are implemented by generated browser code inside the isolated full-stack
# runtime and may later gain native Operly primitives without changing Solution
# product semantics.
_BROWSER_CAPABILITY_PATTERNS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "device.camera",
        (
            re.compile(r"\bcameras?\b", re.I),
            re.compile(r"\bwebcams?\b", re.I),
            re.compile(r"\btake (?:a )?(?:photo|picture)s?\b", re.I),
            re.compile(r"\bcapture (?:a )?(?:photo|image|video)s?\b", re.I),
        ),
    ),
    (
        "device.microphone",
        (
            re.compile(r"\bmicrophones?\b", re.I),
            re.compile(r"\brecord (?:audio|voice)\b", re.I),
            re.compile(r"\bvoice input\b", re.I),
        ),
    ),
    (
        "device.geolocation",
        (
            re.compile(r"\bgeolocation\b", re.I),
            re.compile(r"\bgps\b", re.I),
            re.compile(r"\bcurrent location\b", re.I),
            re.compile(r"\blive location\b", re.I),
        ),
    ),
    (
        "browser.maps",
        (
            re.compile(r"\bmap(?:s|ping)?\b", re.I),
            re.compile(r"\broute planning\b", re.I),
            re.compile(r"\bdirections\b", re.I),
        ),
    ),
    (
        "browser.webrtc",
        (
            re.compile(r"\bwebrtc\b", re.I),
            re.compile(r"\bvideo calls?\b", re.I),
            re.compile(r"\bvideo chats?\b", re.I),
            re.compile(r"\bpeer[- ]to[- ]peer\b", re.I),
        ),
    ),
    (
        "device.bluetooth",
        (re.compile(r"\bbluetooth\b", re.I),),
    ),
    (
        "device.usb",
        (re.compile(r"\bwebusb\b", re.I), re.compile(r"\busb devices?\b", re.I)),
    ),
)


# Phrases whose semantics imply durable state/workflow even when the older token
# floor does not split them into the exact deterministic keywords.
_DURABLE_WORKFLOW_PATTERNS = (
    re.compile(r"\bclock(?:ing)?[ -]?in\b", re.I),
    re.compile(r"\bclock(?:ing)?[ -]?out\b", re.I),
    re.compile(r"\bpunch(?:ing)?[ -]?in\b", re.I),
    re.compile(r"\bpunch(?:ing)?[ -]?out\b", re.I),
    re.compile(r"\bstart (?:a )?shift\b", re.I),
    re.compile(r"\bend (?:a )?shift\b", re.I),
    re.compile(r"\battendance\b", re.I),
    re.compile(r"\btimesheet\b", re.I),
)


@dataclass(frozen=True, slots=True)
class ImplementationResolution:
    runtime_type: str
    solution_type: str
    implementation_mode: str
    reason: str
    confidence: str
    required_capabilities: tuple[str, ...]
    generated_capabilities: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "runtimeType": self.runtime_type,
            "solutionType": self.solution_type,
            "implementationMode": self.implementation_mode,
            "reason": self.reason,
            "confidence": self.confidence,
            "requiredCapabilities": list(self.required_capabilities),
            "generatedCapabilities": list(self.generated_capabilities),
        }


def _semantic_extensions(name: str, objective: str) -> set[str]:
    text = f"{name}\n{objective}"
    extra: set[str] = set()
    for capability, patterns in _BROWSER_CAPABILITY_PATTERNS:
        if any(pattern.search(text) for pattern in patterns):
            extra.add(capability)
    if any(pattern.search(text) for pattern in _DURABLE_WORKFLOW_PATTERNS):
        extra.update({"server.http_api", "data.relational", "workflow.state_machine"})
    return extra


def resolve_solution_implementation(
    manifest: SolutionManifest,
    *,
    name: str | None = None,
    objective: str | None = None,
) -> ImplementationResolution:
    """Choose the narrowest runtime that truthfully implements the full contract."""

    required = set(manifest.capability_ids)
    required.update(_semantic_extensions(name or manifest.name, objective or manifest.objective))

    # Presentation-only work stays on Studio. Device/browser extensions make the
    # request executable software even if the older manifest floor was static.
    if not manifest.stateful and required.issubset(MANAGED_RUNTIME_CAPABILITIES) and not (
        required - set(manifest.capability_ids)
    ):
        return ImplementationResolution(
            runtime_type="studio",
            solution_type="digital_presence",
            implementation_mode="studio_source",
            reason="The complete capability graph is presentation-only and needs no trusted application runtime.",
            confidence="high",
            required_capabilities=tuple(sorted(required)),
            generated_capabilities=(),
        )

    generated = required - MANAGED_RUNTIME_CAPABILITIES
    if generated:
        return ImplementationResolution(
            runtime_type="generated_project",
            solution_type="custom_solution",
            implementation_mode="generated_fullstack",
            reason=(
                "The request requires capabilities outside the finite managed-app declarative runtime; "
                "generate executable source and verify it in the isolated full-stack runner."
            ),
            confidence="high",
            required_capabilities=tuple(sorted(required)),
            generated_capabilities=tuple(sorted(generated)),
        )

    return ImplementationResolution(
        runtime_type="managed_app",
        solution_type="business_app",
        implementation_mode="managed_declarative",
        reason="The complete stateful capability graph is covered by the managed declarative runtime.",
        confidence="high" if manifest.stateful else "medium",
        required_capabilities=tuple(sorted(required)),
        generated_capabilities=(),
    )


__all__ = [
    "ImplementationResolution",
    "MANAGED_RUNTIME_CAPABILITIES",
    "resolve_solution_implementation",
]
