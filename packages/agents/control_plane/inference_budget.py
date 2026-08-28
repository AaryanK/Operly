"""Root-scoped inference accounting and admission control for Agent Factory workers.

The Factory may execute stages concurrently, but all model calls for one root objective
share one budget. Reservations are made before provider calls so parallel workers cannot
independently spend the same remaining tokens. Provider-reported usage is preferred;
when a provider does not report usage, a deterministic approximation is used.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

from packages.agents.compaction import approx_tokens
from packages.model_runtime import (
    InferenceBudget,
    InferenceRequest,
    InferenceResult,
    ModelUsage,
)


class FactoryInferenceBudgetExceeded(RuntimeError):
    """Describes why another model call cannot be admitted for this root objective."""

    def __init__(self, reason: str, snapshot: dict[str, int | bool]) -> None:
        super().__init__(reason)
        self.reason = str(reason)
        self.snapshot = dict(snapshot)


@dataclass(frozen=True, slots=True)
class _Reservation:
    tokens: int


class FactoryInferenceBudget:
    """Concurrency-safe token/model-call budget shared by one Factory execution."""

    def __init__(
        self,
        *,
        max_tokens: int = 120_000,
        max_model_calls: int = 48,
        initial_tokens: int = 0,
        initial_model_calls: int = 0,
    ) -> None:
        self.max_tokens = max(1_000, int(max_tokens))
        self.max_model_calls = max(1, int(max_model_calls))
        self._used_tokens = max(0, int(initial_tokens))
        self._reserved_tokens = 0
        self._model_calls = max(0, int(initial_model_calls))
        self._lock = asyncio.Lock()

    def snapshot(self) -> dict[str, int | bool]:
        committed = self._used_tokens
        reserved = self._reserved_tokens
        return {
            "used_tokens": committed,
            "reserved_tokens": reserved,
            "max_tokens": self.max_tokens,
            "remaining_tokens": max(0, self.max_tokens - committed - reserved),
            "model_calls": self._model_calls,
            "max_model_calls": self.max_model_calls,
            "exhausted": (
                committed + reserved >= self.max_tokens
                or self._model_calls >= self.max_model_calls
            ),
        }

    async def reserve(self, tokens: int) -> _Reservation:
        requested = max(1, int(tokens))
        async with self._lock:
            if self._model_calls >= self.max_model_calls:
                raise FactoryInferenceBudgetExceeded(
                    "root_model_call_budget_exhausted",
                    self.snapshot(),
                )
            if self._used_tokens + self._reserved_tokens + requested > self.max_tokens:
                raise FactoryInferenceBudgetExceeded(
                    "root_token_budget_exhausted",
                    self.snapshot(),
                )
            self._reserved_tokens += requested
            self._model_calls += 1
            return _Reservation(tokens=requested)

    async def reconcile(
        self,
        reservation: _Reservation,
        actual_tokens: int,
    ) -> dict[str, int | bool]:
        actual = max(0, int(actual_tokens))
        async with self._lock:
            self._reserved_tokens = max(0, self._reserved_tokens - reservation.tokens)
            self._used_tokens += actual
            return self.snapshot()

    async def charge_unknown(self, reservation: _Reservation) -> dict[str, int | bool]:
        """Fail-safe charge when a provider call fails without reporting usage."""
        return await self.reconcile(reservation, reservation.tokens)


class _UsageMixin:
    def __init__(
        self,
        model: Any,
        *,
        root_budget: FactoryInferenceBudget | None,
        max_output_tokens: int,
    ) -> None:
        self._model = model
        self._root_budget = root_budget
        self._max_output_tokens = max(128, int(max_output_tokens))
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.model_calls = 0
        self.budget_exhausted: FactoryInferenceBudgetExceeded | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)

    @property
    def usage(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "model_calls": self.model_calls,
        }

    @staticmethod
    def _actual_usage(result: Any, *, input_estimate: int) -> tuple[int, int, int]:
        usage = getattr(result, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
        output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
        total_tokens = getattr(usage, "total_tokens", None) if usage is not None else None
        if input_tokens is None:
            input_tokens = input_estimate
        if output_tokens is None:
            message = getattr(result, "message", {})
            output_tokens = approx_tokens(message)
        if total_tokens is None:
            total_tokens = max(0, int(input_tokens)) + max(0, int(output_tokens))
        return (
            max(0, int(input_tokens)),
            max(0, int(output_tokens)),
            max(0, int(total_tokens)),
        )

    async def _reserve(self, estimated_input: int, output_limit: int) -> _Reservation | None:
        if self._root_budget is None:
            return None
        # Character/4 is intentionally cheap and may underestimate some tokenizers.
        # Reserve 25% headroom plus the provider output ceiling so concurrent workers
        # cannot spend the same root budget while calls are in flight.
        guarded_input = max(estimated_input, (estimated_input * 5 + 3) // 4)
        return await self._root_budget.reserve(guarded_input + output_limit)

    async def _record(
        self,
        reservation: _Reservation | None,
        *,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
    ) -> dict[str, int | bool] | None:
        self.input_tokens += max(0, int(input_tokens))
        self.output_tokens += max(0, int(output_tokens))
        self.total_tokens += max(0, int(total_tokens))
        self.model_calls += 1
        if self._root_budget is not None and reservation is not None:
            return await self._root_budget.reconcile(reservation, total_tokens)
        return None

    async def _record_failure(self, reservation: _Reservation | None) -> None:
        self.model_calls += 1
        if self._root_budget is not None and reservation is not None:
            await self._root_budget.charge_unknown(reservation)

    def _mark_post_call_overflow(self, snapshot: dict[str, int | bool] | None) -> None:
        if not snapshot:
            return
        if int(snapshot.get("used_tokens") or 0) > int(snapshot.get("max_tokens") or 0):
            self.budget_exhausted = FactoryInferenceBudgetExceeded(
                "root_token_budget_exhausted",
                snapshot,
            )

    def _budget_stop_result(self, error: FactoryInferenceBudgetExceeded) -> InferenceResult:
        self.budget_exhausted = error
        return InferenceResult(
            message={
                "role": "assistant",
                "content": (
                    "The Factory inference budget is exhausted for this objective. "
                    "Stop this worker without issuing another capability call."
                ),
            },
            model_resource_id=str(getattr(self._model, "id", "operly:budget")),
            provider="operly",
            provider_model_id="budget-guard",
            latency_ms=0,
            usage=ModelUsage(input_tokens=0, output_tokens=0, total_tokens=0),
            finish_reason="budget_exhausted",
        )


class BudgetedInferenceModel(_UsageMixin):
    """Model.infer proxy that meters actual provider usage against a root budget."""

    async def infer(self, request: InferenceRequest):
        input_estimate = approx_tokens(request.messages) + approx_tokens(request.tools)
        existing = request.budget or InferenceBudget()
        existing_output = existing.max_output_tokens
        output_limit = (
            min(int(existing_output), self._max_output_tokens)
            if existing_output is not None
            else self._max_output_tokens
        )
        bounded_budget = replace(existing, max_output_tokens=output_limit)
        bounded_request = replace(request, budget=bounded_budget)
        try:
            reservation = await self._reserve(input_estimate, output_limit)
        except FactoryInferenceBudgetExceeded as error:
            return self._budget_stop_result(error)
        try:
            result = await self._model.infer(bounded_request)
        except Exception:
            await self._record_failure(reservation)
            raise
        input_tokens, output_tokens, total_tokens = self._actual_usage(
            result,
            input_estimate=input_estimate,
        )
        snapshot = await self._record(
            reservation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
        self._mark_post_call_overflow(snapshot)
        return result


class BudgetedChatModel(_UsageMixin):
    """Compatibility proxy for legacy chat-only model adapters."""

    async def chat(self, messages, tools):
        input_estimate = approx_tokens(messages) + approx_tokens(tools)
        try:
            reservation = await self._reserve(input_estimate, self._max_output_tokens)
        except FactoryInferenceBudgetExceeded as error:
            self.budget_exhausted = error
            return {
                "role": "assistant",
                "content": (
                    "The Factory inference budget is exhausted for this objective. "
                    "Stop this worker without issuing another capability call."
                ),
            }
        try:
            message = await self._model.chat(messages, tools)
        except Exception:
            await self._record_failure(reservation)
            raise
        output_tokens = approx_tokens(message)
        total_tokens = input_estimate + output_tokens
        snapshot = await self._record(
            reservation,
            input_tokens=input_estimate,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )
        self._mark_post_call_overflow(snapshot)
        return message


def budgeted_model(
    model: Any,
    *,
    root_budget: FactoryInferenceBudget | None,
    max_output_tokens: int = 2_000,
) -> Any:
    """Return a usage-metered proxy while preserving infer/chat compatibility."""
    if callable(getattr(model, "infer", None)):
        return BudgetedInferenceModel(
            model,
            root_budget=root_budget,
            max_output_tokens=max_output_tokens,
        )
    return BudgetedChatModel(
        model,
        root_budget=root_budget,
        max_output_tokens=max_output_tokens,
    )
