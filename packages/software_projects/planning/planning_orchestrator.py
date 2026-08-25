"""Live planning compatibility entrypoint.

The historical class name is retained so existing API/service imports keep working,
but the implementation is now the compiler-guided capability planner: model semantics
are extracted once, OPERLY compiles the baseline graph deterministically, and model
calls are reserved for whole-graph semantic review plus bounded unresolved repair.
"""
from packages.software_projects.planning.compiler_planning import CompilerGuidedPlanningOrchestrator
from packages.software_projects.planning.graph_coverage import semantic_claim_errors
from packages.software_projects.planning.graph_planning import (
    PlanningNeedsUserInput,
    _graph_errors,
    _unique,
    material_user_questions,
)


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

    @staticmethod
    def _deterministic_findings(graph, analysis):
        # Compiler-owned ``operly_*`` nodes are intentional cross-cutting obligations:
        # they may support several requirements without lexically restating each one.
        # The ordinary stale-coverage heuristic is still authoritative for model-owned
        # nodes, but must not reject these canonical compiler nodes as false coverage.
        semantic = [
            finding
            for finding in semantic_claim_errors(graph, analysis)
            if not str(finding).startswith("operly_")
        ]
        return _unique([*_graph_errors(graph, analysis), *semantic])
