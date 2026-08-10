"""Live planning compatibility entrypoint.

The historical class name is retained so existing API/service imports keep working,
but the implementation is now the compact dynamic capability-graph planner.
"""
from packages.custom_software.graph_planning import (
    GraphPlanningOrchestrator,
    PlanningNeedsUserInput,
    material_user_questions,
)


# Compatibility helper retained for tests/callers that imported the old private name.
def _material_user_questions(analysis):
    return material_user_questions(list(getattr(analysis, "questions_requiring_user_input", []) or []))


class RecursiveRepairPlanningOrchestrator(GraphPlanningOrchestrator):
    """Compatibility name for the dynamic capability-graph planner.

    Normal path: requirements -> capability graph -> whole-graph review.
    A failed review receives one graph-level repair and one re-review. The old
    per-node recursive validator/partitioner/expander scheduler is intentionally
    no longer the default live planning engine.
    """

    pass
