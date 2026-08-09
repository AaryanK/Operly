"""Production live planning orchestrator composition."""
from packages.custom_software.dependency_orchestrator import DependencyResolvingPlanningOrchestrator
from packages.custom_software.global_repair import GlobalRepairPlanningOrchestrator


class RecursiveRepairPlanningOrchestrator(
    GlobalRepairPlanningOrchestrator,
    DependencyResolvingPlanningOrchestrator,
):
    """Combines local dependency repair with global-validation repair.

    MRO intentionally places DependencyResolvingPlanningOrchestrator beneath the
    global repair layer, so both the initial planning pass and every global-repair
    rerun use executable dependency resolution.
    """

    pass
