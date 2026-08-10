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


def _is_operly_internal_question(question: str) -> bool:
    """Return True when the planner is asking the owner to design OPERLY itself.

    Owners should resolve product intent and consequential placement/security choices.
    OPERLY owns its internal protocol, implementation-mechanism and architecture policy.
    """
    normalized = " ".join(str(question).lower().split())
    if not normalized:
        return False

    platform_phrases = (
        "technical nature",
        "what is operly",
        "specific api",
        "specific technical interface",
        "technical interface or protocol",
        "which protocol",
        "what protocol",
        "mcp-style",
        "third-party integration",
        "what constitutes an 'important architectural decision'",
        'what constitutes an "important architectural decision"',
        "what constitutes a 'necessary' third-party api",
        'what constitutes a "necessary" third-party api',
        "what constitutes an important architectural decision",
        "what constitutes a necessary third-party api",
    )
    if any(phrase in normalized for phrase in platform_phrases):
        return True

    # Questions that ask the owner to define OPERLY's own machine interface are
    # implementation-policy questions even when phrased differently.
    if "operly" in normalized and any(
        term in normalized
        for term in ("interface", "protocol", "api", "mcp", "internal architecture", "technical mechanism")
    ):
        return True

    # Whether a library/API is technically necessary is determined from the
    # requested capability and workspace, not delegated back to the owner.
    if "third-party" in normalized and any(
        term in normalized for term in ("necessary", "required", "constitutes", "criteria")
    ):
        return True

    return False


def _material_user_questions(analysis: RequirementsAnalysis) -> list[str]:
    """Keep owner-resolvable ambiguity; drop questions OPERLY must answer itself."""
    questions: list[str] = []
    for question in analysis.questions_requiring_user_input:
        if _is_operly_internal_question(question):
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
            # Internal/platform questions have been answered by policy: OPERLY
            # decides them. Do not carry them forward as unresolved ambiguity.
            if analysis.questions_requiring_user_input:
                return analysis.model_copy(update={"questions_requiring_user_input": []})
        return value
