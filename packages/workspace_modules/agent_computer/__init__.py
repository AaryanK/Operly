"""Workspace-owned Agent Computer runtime and human interface.

The package owns both the native isolated-compute capability contracts and the
Workspace HTTP interface. Business authority remains in normal Workspace tools.
"""

from packages.workspace_modules.agent_computer.native_tools import (
    PROVIDER_ID,
    AgentComputerProvider,
    computer_native_capabilities,
)
from packages.workspace_modules.agent_computer.router import router

__all__ = ["PROVIDER_ID", "AgentComputerProvider", "computer_native_capabilities", "router"]
