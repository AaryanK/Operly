from packages.harness.registry import ToolRegistry
from packages.harness.tools.discord_tools import register_discord_tools
from packages.harness.tools.memory_tools import register_memory_tools
from packages.harness.tools.task_tools import register_task_tools


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_discord_tools(registry)
    register_memory_tools(registry)
    register_task_tools(registry)
    return registry
