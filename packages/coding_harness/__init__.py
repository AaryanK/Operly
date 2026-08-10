"""Model-independent contracts and agentic services for OPERLY's coding harness."""

from .engine import build_harness_plan
from .evaluation import calculate_loss, compare_task, aggregate_report
from .opencode_agent import OpenCodeStyleCodingAgent

__all__ = [
    "build_harness_plan",
    "calculate_loss",
    "compare_task",
    "aggregate_report",
    "OpenCodeStyleCodingAgent",
]
