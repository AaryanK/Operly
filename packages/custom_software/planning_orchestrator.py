"""Production live planning orchestrator composition."""
from packages.custom_software.dependency_orchestrator import DependencyResolvingPlanningOrchestrator
from packages.custom_software.global_repair import GlobalRepairPlanningOrchestrator
from packages.custom_software.live_planning import PlanningBlocked, RequirementsAnalysis
from packages.custom_software.scope_convergence import ScopeConvergingPlanningClient


class PlanningNeedsUserInput(PlanningBlocked):
    """Planning cannot continue until the owner answers material questions."""

    def __init__(self, questions: list[str]):
        cleaned = [str(question).strip() for question in questions if str(question).strip()]
        self.questions = cleaned[:2]
        super().__init__("user input required before planning: " + " | ".join(self.questions))


def _material_user_questions(analysis: RequirementsAnalysis) -> list[str]:
    """Keep owner-resolvable ambiguity; drop questions OPERLY must answer itself."""
    questions: list[str] = []
    for question in analysis.questions_requiring_user_input:
        normalized = question.lower()
        # OPERLY is the current platform/assistant. Asking the owner to explain
        # OPERLY's technical nature is a platform-context failure, not user ambiguity.
        if "operly" in normalized and any(
            phrase in normalized
            for phrase in ("technical nature", "what is operly", "specific api", "third-party integration")
        ):
            continue
        questions.append(question)
    return questions


class RecursiveRepairPlanningOrchestrator(
    GlobalRepairPlanningOrchestrator,
    DependencyResolvingPlanningOrchestrator,
):
    """Combines local dependency repair with global-validation repair.

    MRO intentionally places DependencyResolvingPlanningOrchestrator beneath the
    global repair layer, so both the initial planning pass and every global-repair
    rerun use executable dependency resolution. Validator results also pass through
    a deterministic convergence guard so an already-resolved scope prune cannot
    cycle until the refinement budget is exhausted.

    A requirements-analysis clarification gate runs before any planner call. If the
    model already knows that user input materially changes the implementation, the
    expensive recursive planner is not allowed to guess and continue.
    """

    def __init__(self, client, *args, **kwargs):
        super().__init__(ScopeConvergingPlanningClient(client), *args, **kwargs)

    async def _call(self, role, context, schema, node_id=None):
        value = await super()._call(role, context, schema, node_id)
        if role == "requirements_analyst":
            analysis = RequirementsAnalysis.model_validate(value.model_dump(mode="json"))
            questions = _material_user_questions(analysis)
            if questions:
                raise PlanningNeedsUserInput(questions)
        return value
