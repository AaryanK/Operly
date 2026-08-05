"""Model-independent contracts and deterministic services for OPERLY's coding harness."""

from .engine import build_harness_plan
from .evaluation import calculate_loss, compare_task, aggregate_report

__all__ = ["build_harness_plan", "calculate_loss", "compare_task", "aggregate_report"]
