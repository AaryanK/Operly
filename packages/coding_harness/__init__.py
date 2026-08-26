"""Software tooling exposed through Operly's canonical AgentRuntime.

Studio is a surface, not an agent runtime.  The historical ``opencode_agent`` module
still owns the bounded virtual-workspace/tool definitions during this migration, but
its private model loop is no longer reachable through the package/service exports.
"""

from .engine import build_harness_plan
from .evaluation import calculate_loss, compare_task, aggregate_report
from . import opencode_agent as _software_tools
from .runtime_agent import AgentRuntimeCodingAgent, CapabilityCodingAgent, OpenCodeStyleCodingAgent

# Compatibility cutover for older service modules that import the historical symbols
# directly from ``opencode_agent``.  Importing any coding_harness submodule first loads
# this package, so those callers receive the canonical AgentRuntime adapter without a
# large risky source-service rewrite.  The old loop remains dead code until the tool
# definitions are extracted into their own module and the legacy file can be deleted.
_software_tools.CapabilityCodingAgent = CapabilityCodingAgent
_software_tools.OpenCodeStyleCodingAgent = OpenCodeStyleCodingAgent

__all__ = [
    "build_harness_plan",
    "calculate_loss",
    "compare_task",
    "aggregate_report",
    "AgentRuntimeCodingAgent",
    "CapabilityCodingAgent",
    "OpenCodeStyleCodingAgent",
]
