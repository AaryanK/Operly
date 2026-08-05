from __future__ import annotations

from statistics import median

from .contracts import AggregateReport, BaselineImport, BenchmarkTask, ComparisonReport, OutcomeMetrics

WEIGHTS = {"requirement": 1.5, "functional": 1.5, "build": 1.0, "test": 1.2, "security": 2.0,
           "architecture": .7, "visual": .6, "editability": .7, "traceability": .8, "regression": 1.0,
           "humanIntervention": .6, "efficiency": .4, "operability": .7}


def calculate_loss(metrics: OutcomeMetrics) -> float:
    """Deterministic [0,1] loss. Inputs are normalized success scores; higher is better."""
    if metrics.criticalSecurityFailure:
        return 1.0
    values = metrics.model_dump(exclude={"criticalSecurityFailure"})
    total = sum(WEIGHTS.values())
    return round(sum(WEIGHTS[k] * (1 - values[k]) for k in WEIGHTS) / total, 6)


def compare_task(task: BenchmarkTask, operly: OutcomeMetrics, baseline: BaselineImport, evidence_refs: list[str]) -> ComparisonReport:
    if task.id != baseline.taskId:
        raise ValueError("Baseline belongs to a different benchmark task")
    left, right = calculate_loss(operly), calculate_loss(baseline.metrics)
    epsilon = 1e-6
    winner = "tie" if abs(left-right) <= epsilon else "operly" if left < right else "baseline"
    dimensions = {k: {"operly": getattr(operly, k), "baseline": getattr(baseline.metrics, k)} for k in WEIGHTS}
    return ComparisonReport(taskId=task.id, split=task.split, operlyLoss=left, baselineLoss=right,
                            delta=round(left-right, 6), winner=winner, dimensions=dimensions, evidenceRefs=evidence_refs)


def aggregate_report(reports: list[ComparisonReport]) -> AggregateReport:
    def summary(split):
        rows = [x for x in reports if x.split == split]
        if not rows:
            return {"count": 0.0, "operlyBest": 0.0, "operlyMedian": 0.0, "operlyFailureRate": 0.0}
        losses = [x.operlyLoss for x in rows]
        return {"count": float(len(rows)), "operlyBest": min(losses), "operlyMedian": median(losses),
                "operlyFailureRate": sum(x >= .5 for x in losses)/len(losses)}
    held = [x for x in reports if x.split == "held_out"]
    parity = bool(held) and all(x.operlyLoss <= x.baselineLoss for x in held)
    return AggregateReport(development=summary("development"), heldOut=summary("held_out"),
                           taskReports=reports, parityClaimAllowed=parity)
