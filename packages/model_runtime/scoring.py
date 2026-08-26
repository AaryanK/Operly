"""Adaptive model-route scoring for Operly's shared model index.

Every provider/model pair is an independent route. Scores combine static model-card
traits with live success, latency, failure, and exploration evidence so routing can
self-correct instead of relying on a fixed primary/fallback order.
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Iterable, Protocol


class ScorableModel(Protocol):
    id: str
    provider: str
    provider_model_id: str
    priority: int
    verified_latency_ms: int | None
    tags: frozenset[str]
    traits: object


@dataclass(slots=True)
class RouteScoreState:
    attempts: int = 0
    successes: int = 0
    success_ewma: float = 0.78
    latency_ewma_ms: float | None = None
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_called_at: float = 0.0
    last_success_at: float = 0.0
    task_success_ewma: dict[str, float] = field(default_factory=dict)
    last_classification: str | None = None


@dataclass(frozen=True, slots=True)
class RankedModelRoute:
    model: ScorableModel
    score: float
    available: bool


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _route_key(model: ScorableModel) -> str:
    provider = str(getattr(model, "provider", "") or "").strip().lower()
    model_id = str(
        getattr(model, "provider_model_id", "") or getattr(model, "id", "") or ""
    ).strip()
    return f"{provider}:{model_id}"


def _task_name(task_type: str | None) -> str:
    value = str(task_type or "general").strip().lower()
    if value.startswith("role:"):
        value = value.split(":", 1)[1]
    return value or "general"


class ModelScorer:
    """Thread-safe online scorer with deterministic UCB-style exploration."""

    def __init__(self) -> None:
        self._states: dict[str, RouteScoreState] = {}
        self._total_attempts = 0
        self._lock = RLock()

    def state_for(self, model: ScorableModel) -> RouteScoreState:
        key = _route_key(model)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = RouteScoreState()
                self._states[key] = state
            return state

    def _score(self, model: ScorableModel, state: RouteScoreState, *, task_type: str, now: float) -> float:
        # Static cards are only a prior. Runtime evidence becomes the dominant signal.
        priority = max(0, min(int(getattr(model, "priority", 100)), 200))
        prior = 18.0 * (1.0 - priority / 200.0)

        quality_class = str(getattr(getattr(model, "traits", None), "quality_class", "") or "").lower()
        quality = {"high": 12.0, "balanced": 8.0, "medium": 6.0, "low": 2.0}.get(quality_class, 5.0)

        reliability = 36.0 * max(0.0, min(state.success_ewma, 1.0))
        task_reliability = 10.0 * max(
            0.0,
            min(state.task_success_ewma.get(task_type, state.success_ewma), 1.0),
        )

        observed_latency = state.latency_ewma_ms
        if observed_latency is None:
            observed_latency = getattr(model, "verified_latency_ms", None)
        if observed_latency is None:
            latency = 5.0
        else:
            # 15 points near zero latency, smoothly tending toward zero for slow routes.
            latency = 15.0 / (1.0 + max(float(observed_latency), 0.0) / 1500.0)

        tags = set(getattr(model, "tags", frozenset()) or ())
        cost = 4.0 if "free" in tags else 1.5

        # UCB-style exploration means untried/recovered models keep receiving probes
        # without introducing random, non-reproducible routing behavior.
        exploration_strength = _bounded_float(
            "OPERLY_MODEL_EXPLORATION_STRENGTH", 8.0, 0.0, 30.0
        )
        exploration = exploration_strength * math.sqrt(
            math.log(self._total_attempts + 2.0) / (state.attempts + 1.0)
        )
        exploration = min(exploration, 15.0)

        failure_penalty = min(30.0, state.consecutive_failures * 8.0)
        if state.cooldown_until > now:
            return -10_000.0

        # A model that has not been called recently gets a small recovery/probe bonus.
        recovery_window = _bounded_float(
            "OPERLY_MODEL_RECOVERY_WINDOW_SECONDS", 300.0, 30.0, 3600.0
        )
        idle_seconds = max(0.0, now - state.last_called_at) if state.last_called_at else recovery_window
        recovery = min(4.0, 4.0 * idle_seconds / recovery_window)

        return prior + quality + reliability + task_reliability + latency + cost + exploration + recovery - failure_penalty

    def rank(
        self,
        models: Iterable[ScorableModel],
        *,
        task_type: str | None = None,
        include_cooling: bool = False,
    ) -> list[RankedModelRoute]:
        task = _task_name(task_type)
        now = time.monotonic()
        with self._lock:
            ranked: list[RankedModelRoute] = []
            for model in models:
                state = self.state_for(model)
                available = state.cooldown_until <= now
                if not include_cooling and not available:
                    continue
                ranked.append(
                    RankedModelRoute(
                        model=model,
                        score=self._score(model, state, task_type=task, now=now),
                        available=available,
                    )
                )
            ranked.sort(key=lambda row: (-row.score, _route_key(row.model)))
            return ranked

    def record_success(
        self,
        model: ScorableModel,
        *,
        latency_ms: int | None = None,
        task_type: str | None = None,
    ) -> None:
        task = _task_name(task_type)
        now = time.monotonic()
        with self._lock:
            state = self.state_for(model)
            self._total_attempts += 1
            state.attempts += 1
            state.successes += 1
            state.success_ewma = 0.82 * state.success_ewma + 0.18
            task_value = state.task_success_ewma.get(task, 0.78)
            state.task_success_ewma[task] = 0.82 * task_value + 0.18
            if latency_ms is not None:
                latency = max(0.0, float(latency_ms))
                state.latency_ewma_ms = (
                    latency
                    if state.latency_ewma_ms is None
                    else 0.75 * state.latency_ewma_ms + 0.25 * latency
                )
            state.consecutive_failures = 0
            state.cooldown_until = 0.0
            state.last_called_at = now
            state.last_success_at = now
            state.last_classification = None

    def record_failure(
        self,
        model: ScorableModel,
        *,
        classification: str | None = None,
        latency_ms: int | None = None,
        task_type: str | None = None,
    ) -> None:
        task = _task_name(task_type)
        kind = str(classification or "model_error").strip().lower()
        now = time.monotonic()
        with self._lock:
            state = self.state_for(model)
            self._total_attempts += 1
            state.attempts += 1
            state.success_ewma *= 0.72 if kind == "rate_limited" else 0.80
            task_value = state.task_success_ewma.get(task, 0.78)
            state.task_success_ewma[task] = task_value * (0.72 if kind == "rate_limited" else 0.80)
            if latency_ms is not None:
                latency = max(0.0, float(latency_ms))
                state.latency_ewma_ms = (
                    latency
                    if state.latency_ewma_ms is None
                    else 0.8 * state.latency_ewma_ms + 0.2 * latency
                )
            state.consecutive_failures += 1
            state.last_called_at = now
            state.last_classification = kind

            base_cooldown = _bounded_float(
                "OPERLY_MODEL_POOL_COOLDOWN_SECONDS", 45.0, 5.0, 600.0
            )
            multiplier = {
                "rate_limited": 2.0,
                "quota_or_credits": 4.0,
                "auth": 4.0,
                "provider_5xx": 1.5,
                "response_timeout": 1.0,
                "model_unavailable": 2.0,
            }.get(kind, 0.5)
            state.cooldown_until = max(
                state.cooldown_until,
                now + base_cooldown * multiplier,
            )

    def snapshot(self) -> dict[str, dict[str, object]]:
        now = time.monotonic()
        with self._lock:
            return {
                key: {
                    "attempts": state.attempts,
                    "successes": state.successes,
                    "success_ewma": round(state.success_ewma, 6),
                    "latency_ewma_ms": state.latency_ewma_ms,
                    "consecutive_failures": state.consecutive_failures,
                    "cooldown_seconds": max(0.0, state.cooldown_until - now),
                    "last_classification": state.last_classification,
                    "task_success_ewma": dict(state.task_success_ewma),
                }
                for key, state in sorted(self._states.items())
            }


_DEFAULT_SCORER = ModelScorer()


def default_model_scorer() -> ModelScorer:
    return _DEFAULT_SCORER
