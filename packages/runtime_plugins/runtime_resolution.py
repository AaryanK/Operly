"""Deterministic runtime selection through trusted RuntimePlugins.

The coding model may author arbitrary source, but it does not choose execution
authority. Runtime selection and source validation remain deterministic.
"""
from __future__ import annotations

import json

from packages.software_projects.coding.contract_guidance import source_contract_repair_packet
from packages.software_projects.source_bundle import SourceBundle
from packages.runtime_plugins import register_builtin_runtimes


class RuntimeResolutionError(ValueError):
    pass


def _registry():
    return register_builtin_runtimes()


def infer_runtime_profile(bundle: SourceBundle) -> str:
    matches = []
    for plugin in _registry().plugins():
        result = plugin.detect(bundle)
        if result.matched:
            matches.append((result.score, plugin.spec.id))
    if not matches:
        raise RuntimeResolutionError(
            "No installed isolated runtime plugin recognizes the generated source tree"
        )
    matches.sort(key=lambda item: (-item[0], item[1]))
    return matches[0][1]


def _runtime_error_message(error: Exception) -> str:
    message = str(error)
    packet = source_contract_repair_packet(message)
    if not packet:
        return message
    # Keep deterministic validator evidence and canonical repair guidance together.
    # The coding loop sees this immediately after a failed finish attempt, while the
    # terminal UI still receives a bounded version through its existing truncation.
    return (
        message
        + "\nOPERLY_CONTRACT_REPAIR_PACKET="
        + json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def validate_runtime_contract(bundle: SourceBundle) -> str:
    try:
        plugin = _registry().resolve(bundle)
    except (LookupError, ValueError) as error:
        raise RuntimeResolutionError(_runtime_error_message(error)) from error
    return plugin.spec.id


def validate_source_files(files) -> str:
    return validate_runtime_contract(SourceBundle(tuple(files), {}, "in-memory"))
