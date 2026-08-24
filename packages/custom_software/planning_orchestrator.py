"""Live planning compatibility entrypoint.

The historical class name is retained so existing API/service imports keep working,
but the implementation is now the compiler-guided capability planner: model semantics
are extracted once, OPERLY compiles the baseline graph deterministically, and model
calls are reserved for whole-graph semantic review plus bounded unresolved repair.
"""
from packages.custom_software.compiler_planning import CompilerGuidedPlanningOrchestrator
from packages.custom_software.graph_planning import PlanningNeedsUserInput, material_user_questions


def _is_operly_internal_question(question: str) -> bool:
    """Compatibility facade for the owner-vs-platform decision policy."""
    text = str(question or "").strip()
    return bool(text) and not material_user_questions([text])


# Compatibility helper retained for tests/callers that imported the old private name.
def _material_user_questions(analysis):
    return material_user_questions(list(getattr(analysis, "questions_requiring_user_input", []) or []))


class RecursiveRepairPlanningOrchestrator(CompilerGuidedPlanningOrchestrator):
    """Compatibility name for compiler-guided Studio planning.

    Normal path: requirements -> deterministic capability compilation -> whole-graph
    semantic review. OPERLY inserts known platform obligations without another graph
    generation call. Review gaps are repaired deterministically first; a planner model
    is used only for any semantic delta that remains unresolved.
    """

    pass
