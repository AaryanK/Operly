from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import or_, update

from packages.agent_runtime.spend import (
    AgentSpendMeter,
    SpendBudgetExceeded,
    SpendCallBudgetExceeded,
    SpendConfigurationError,
    SpendReservation,
)
from packages.database.agent_spend_models import (
    AgentSpendBudgetRecord,
    AgentSpendReservationRecord,
)
from packages.kernel.contracts import CapabilitySpec


_MAX_TOOL_COST_MICROS = 100_000_000  # $100 per individual metered capability call.
_PAID_TOOL_TAG = "paid"


class PaidToolPriceUnknown(SpendConfigurationError):
    def __init__(self, capability_id: str) -> None:
        super().__init__(f"No trusted paid-tool price exists for {capability_id}")
        self.code = "paid_tool_price_unknown"


@dataclass(frozen=True, slots=True)
class PaidToolPriceSnapshot:
    """Server-owned fixed-cost prices embedded in the same versioned price snapshot.

    `OPERLY_AGENT_PRICE_SNAPSHOT_JSON` may contain both `models` and `tools`. Tool
    pricing is intentionally keyed by canonical capability ID, never model output.
    """

    version: str
    costs_micros: Mapping[str, int]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PaidToolPriceSnapshot":
        version = str(payload.get("version") or "").strip()
        if not version or len(version) > 80:
            raise ValueError("price snapshot version is required")
        raw_tools = payload.get("tools", {})
        if not isinstance(raw_tools, Mapping):
            raise ValueError("price snapshot tools must be an object")
        costs: dict[str, int] = {}
        for raw_capability_id, raw_price in raw_tools.items():
            capability_id = str(raw_capability_id or "").strip().lower()
            if not capability_id or len(capability_id) > 200:
                raise ValueError("paid-tool capability ID is invalid")
            if isinstance(raw_price, Mapping):
                value = raw_price.get("cost_micros")
            else:
                value = raw_price
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("paid-tool price must be an integer micro-dollar amount")
            if value < 0 or value > _MAX_TOOL_COST_MICROS:
                raise ValueError("paid-tool price is outside the accepted range")
            costs[capability_id] = value
        return cls(version=version, costs_micros=costs)

    @classmethod
    def from_environment(cls) -> "PaidToolPriceSnapshot":
        raw = os.getenv("OPERLY_AGENT_PRICE_SNAPSHOT_JSON", "").strip()
        if not raw:
            return cls(version="unconfigured", costs_micros={})
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SpendConfigurationError("OPERLY_AGENT_PRICE_SNAPSHOT_JSON is invalid JSON") from error
        try:
            return cls.from_mapping(payload)
        except ValueError as error:
            raise SpendConfigurationError(str(error)) from error

    def cost_for(self, capability_id: str) -> int:
        key = str(capability_id or "").strip().lower()
        try:
            return int(self.costs_micros[key])
        except KeyError as error:
            raise PaidToolPriceUnknown(key) from error


def is_paid_capability(spec: CapabilitySpec) -> bool:
    """Only server-owned capability metadata can opt a tool into paid metering."""

    return _PAID_TOOL_TAG in {str(tag).strip().lower() for tag in spec.tags}


class PaidToolSpendController:
    """Reserve fixed paid-tool costs against the same persistent P4 budget buckets."""

    def __init__(
        self,
        *,
        meter: AgentSpendMeter,
        prices: PaidToolPriceSnapshot | None = None,
    ) -> None:
        self.meter = meter
        self.prices = prices or PaidToolPriceSnapshot.from_environment()

    async def reserve(self, *, capability_id: str) -> SpendReservation:
        cost_micros = self.prices.cost_for(capability_id)
        reservation_id = str(uuid4())
        reservation_key = f"{self.meter.run_id}:tool:{capability_id}:{reservation_id}"

        async with self.meter.session_factory() as db:
            async with db.begin():
                budgets = await self.meter._budget_rows(db)
                for row in budgets:
                    conditions = [
                        AgentSpendBudgetRecord.id == row.id,
                        AgentSpendBudgetRecord.spent_micros
                        + AgentSpendBudgetRecord.reserved_micros
                        + cost_micros
                        <= AgentSpendBudgetRecord.limit_micros,
                    ]
                    values: dict[str, Any] = {
                        "reserved_micros": AgentSpendBudgetRecord.reserved_micros + cost_micros,
                        "updated_at": datetime.utcnow(),
                    }
                    # The existing max_model_calls limit is deliberately model-only;
                    # step/mutation limits remain the independent tool-count guard.
                    result = await db.execute(
                        update(AgentSpendBudgetRecord)
                        .where(*conditions)
                        .values(**values)
                    )
                    if result.rowcount != 1:
                        raise SpendBudgetExceeded(
                            f"{row.budget_kind} monetary budget cannot reserve paid capability {capability_id}"
                        )

                db.add(
                    AgentSpendReservationRecord(
                        id=reservation_id,
                        reservation_key=reservation_key,
                        run_id=self.meter.run_id,
                        task_id=self.meter.scope.task_id,
                        scope_kind=self.meter.scope.scope_kind,
                        scope_id=self.meter.scope.scope_id,
                        project_id=self.meter.scope.project_id,
                        phase="paid_tool",
                        provider="tool",
                        model_id=str(capability_id)[:255],
                        price_snapshot_version=self.prices.version,
                        input_micros_per_million_tokens=0,
                        output_micros_per_million_tokens=0,
                        input_token_cap=0,
                        output_token_cap=0,
                        reserved_micros=cost_micros,
                        status="reserved",
                        budget_ids_json=json.dumps([row.id for row in budgets]),
                    )
                )
        return SpendReservation(
            reservation_id=reservation_id,
            reserved_micros=cost_micros,
            price_snapshot_version=self.prices.version,
        )

    async def settle(self, reservation_id: str) -> int:
        async with self.meter.session_factory() as db:
            async with db.begin():
                reservation = await db.get(AgentSpendReservationRecord, reservation_id)
                if reservation is None:
                    raise LookupError("paid-tool spend reservation not found")
                if reservation.status == "settled":
                    return int(reservation.actual_micros or 0)
                if reservation.status not in {"reserved", "uncertain"}:
                    raise SpendConfigurationError(
                        "paid-tool spend reservation cannot be settled from its current state"
                    )
                budget_ids = json.loads(reservation.budget_ids_json or "[]")
                for budget_id in budget_ids:
                    budget = await db.get(AgentSpendBudgetRecord, str(budget_id))
                    if budget is None:
                        raise SpendConfigurationError(
                            "paid-tool reservation references a missing budget"
                        )
                    if budget.reserved_micros < reservation.reserved_micros:
                        raise SpendConfigurationError(
                            "paid-tool reservation exceeds persisted reserved balance"
                        )
                    budget.reserved_micros -= reservation.reserved_micros
                    budget.spent_micros += reservation.reserved_micros
                    budget.updated_at = datetime.utcnow()
                reservation.actual_micros = reservation.reserved_micros
                reservation.status = "settled"
                reservation.uncertainty_reason = None
                reservation.settled_at = datetime.utcnow()
                return int(reservation.actual_micros)

    async def release(self, reservation_id: str, *, reason: str) -> None:
        """Release a reservation only when Kernel proves the tool was not dispatched."""

        async with self.meter.session_factory() as db:
            async with db.begin():
                reservation = await db.get(AgentSpendReservationRecord, reservation_id)
                if reservation is None:
                    raise LookupError("paid-tool spend reservation not found")
                if reservation.status == "released":
                    return
                if reservation.status not in {"reserved", "uncertain"}:
                    raise SpendConfigurationError(
                        "paid-tool spend reservation cannot be released from its current state"
                    )
                budget_ids = json.loads(reservation.budget_ids_json or "[]")
                for budget_id in budget_ids:
                    budget = await db.get(AgentSpendBudgetRecord, str(budget_id))
                    if budget is None:
                        raise SpendConfigurationError(
                            "paid-tool reservation references a missing budget"
                        )
                    if budget.reserved_micros < reservation.reserved_micros:
                        raise SpendConfigurationError(
                            "paid-tool reservation exceeds persisted reserved balance"
                        )
                    budget.reserved_micros -= reservation.reserved_micros
                    budget.updated_at = datetime.utcnow()
                reservation.actual_micros = 0
                reservation.status = "released"
                reservation.uncertainty_reason = str(reason or "not_dispatched")[:120]
                reservation.settled_at = datetime.utcnow()

    async def mark_uncertain(self, reservation_id: str, *, reason: str) -> None:
        await self.meter.mark_uncertain(reservation_id, reason=reason)
