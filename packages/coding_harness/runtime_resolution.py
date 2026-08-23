"""Deterministic runtime selection through trusted RuntimePlugins.

The coding model authors source; it never chooses or invents execution commands.
Runtime plugins detect source shape, validate their own contract, and later build
typed runner submissions from trusted policy.
"""
from __future__ import annotations

from packages.custom_software.source_bundles import SourceBundle
from packages.runtime_plugins import (
    FULLSTACK_EXECUTION_ENABLED,
    FULLSTACK_MANIFEST,
    FULLSTACK_RUNTIME_ID,
    register_builtin_runtimes,
    validate_fullstack_source,
)


class RuntimeResolutionError(ValueError):
    pass


def _registry():
    return register_builtin_runtimes()


def _guard_contract_only_runtime(bundle: SourceBundle) -> None:
    """Recognize future runtime contracts without pretending they can execute.

    A generated tree containing ``operly.solution.json`` gets its real contract
    validated first.  Until an isolated runner plugin implements that profile we
    return a precise unavailable-executor error instead of falling through to a
    stdlib/static profile or claiming preview/deploy support.
    """

    if not any(item.path == FULLSTACK_MANIFEST for item in bundle.files):
        return
    validation = validate_fullstack_source(bundle)
    if not validation.valid:
        raise RuntimeResolutionError(
            f"Invalid {FULLSTACK_RUNTIME_ID} project contract: " + "; ".join(validation.errors)
        )
    if not FULLSTACK_EXECUTION_ENABLED:
        raise RuntimeResolutionError(
            f"{FULLSTACK_RUNTIME_ID} project contract is valid, but no trusted isolated "
            "executor is enabled for it yet; preview/deploy remain unavailable"
        )


def infer_runtime_profile(bundle: SourceBundle) -> str:
    _guard_contract_only_runtime(bundle)
    matches = []
    for plugin in _registry().plugins():
        match = plugin.detect(bundle)
        if match.matched:
            matches.append((match.score, plugin.spec.id))
    if not matches:
        raise RuntimeResolutionError(
            "No installed isolated runtime plugin matches the generated source tree; "
            "the coding harness must create a supported entrypoint and executable test shape first"
        )
    matches.sort(key=lambda item: (-item[0], item[1]))
    return matches[0][1]


def validate_runtime_contract(bundle: SourceBundle) -> str:
    """Resolve one installed runtime and enforce its deterministic source contract."""
    _guard_contract_only_runtime(bundle)
    try:
        plugin = _registry().resolve(bundle)
    except (LookupError, ValueError) as error:
        raise RuntimeResolutionError(str(error)) from error
    return plugin.spec.id


def validate_source_files(files) -> str:
    """Validate an in-memory coding workspace before the agent may finish."""
    return validate_runtime_contract(SourceBundle(tuple(files), {}, "in-memory"))
