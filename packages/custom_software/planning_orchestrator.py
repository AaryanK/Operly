"""Production live planning orchestrator composition."""
from packages.custom_software.dependency_orchestrator import DependencyResolvingPlanningOrchestrator
from packages.custom_software.global_repair import GlobalRepairPlanningOrchestrator
from packages.custom_software.scope_convergence import ScopeConvergingPlanningClient


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
    """

    def __init__(self, client, *args, **kwargs):
        super().__init__(ScopeConvergingPlanningClient(client), *args, **kwargs)
