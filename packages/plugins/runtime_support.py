"""Implemented Workspace plugin runtimes, separate from historical manifest syntax.

This describes available adapters, not the health/configuration of a deployment.
Never narrow PluginExecutionMode or mutate a persisted manifest to enforce support.
"""
from __future__ import annotations

from typing import Any

from packages.plugins.contracts import PluginContractError, PluginExecutionMode, PluginManifest


_MODE_PROFILES = {
    PluginExecutionMode.PLATFORM_NATIVE: (),
    PluginExecutionMode.REMOTE_HTTP: ("remote-http",),
    PluginExecutionMode.SANDBOX_JOB: ("sandbox-job",),
    PluginExecutionMode.WEB_SERVICE: ("node-web", "python-fastapi"),
    PluginExecutionMode.WORKER: ("worker",),
    PluginExecutionMode.STATIC_SITE: ("static-web", "react-vite"),
}
MODE_BY_RUNTIME_KIND = {
    "remote": PluginExecutionMode.REMOTE_HTTP,
    "job": PluginExecutionMode.SANDBOX_JOB,
    "web": PluginExecutionMode.WEB_SERVICE,
    "worker": PluginExecutionMode.WORKER,
    "static": PluginExecutionMode.STATIC_SITE,
}
_IMPLEMENTED = frozenset({PluginExecutionMode.REMOTE_HTTP, PluginExecutionMode.SANDBOX_JOB})


def runtime_mode_support(mode: PluginExecutionMode) -> dict[str, Any]:
    supported = mode in _IMPLEMENTED
    code = None
    reason = None
    if mode is PluginExecutionMode.PLATFORM_NATIVE:
        code = "platform_native_reserved"
        reason = "platform_native providers ship with trusted Operly code; Workspace plugins cannot provision them."
    elif not supported:
        code = "unsupported_runtime_mode"
        reason = f"Operly has no reconciliation or capability-execution adapter for {mode.value}. Historical manifests remain readable."
    requirements = {
        PluginExecutionMode.REMOTE_HTTP: ["A separately operated HTTPS endpoint and successful health reconciliation are required."],
        PluginExecutionMode.SANDBOX_JOB: ["Requires the Sandbox Runner and a validated immutable artifact; network access, credentials and capability bindings are not supported."],
    }
    return {
        "execution_mode": mode.value,
        "supported": supported,
        "reason_code": code,
        "reason": reason,
        "profiles": list(_MODE_PROFILES[mode]),
        "requirements": requirements.get(mode, []),
    }


def manifest_runtime_support(manifest: PluginManifest) -> dict[str, Any]:
    support = runtime_mode_support(manifest.execution_mode)
    if support["supported"] and (
        manifest.runtime is None
        or manifest.runtime.profile not in support["profiles"]
        or MODE_BY_RUNTIME_KIND.get(manifest.runtime.kind) is not manifest.execution_mode
    ):
        support.update(
            supported=False,
            reason_code="runtime_profile_mode_mismatch",
            reason=f"Runtime profile/kind does not match the {manifest.execution_mode.value} adapter. Supported profiles: {', '.join(support['profiles'])}.",
        )
    return support


class UnsupportedPluginRuntime(PluginContractError):
    def __init__(self, support: dict[str, Any]) -> None:
        self.support = support
        super().__init__(f"{support['reason_code']}: {support['reason']}")


def require_supported_runtime(manifest: PluginManifest) -> None:
    support = manifest_runtime_support(manifest)
    if not support["supported"]:
        raise UnsupportedPluginRuntime(support)
