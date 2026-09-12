from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from backend.app.billing.balance_loader import BalanceLoader
from backend.app.billing.config import BillingConfig
from backend.app.errors import BalanceContentionError, InvalidInputError
from backend.app.shared.ports.billing.balance_store import (
    AlreadyApplied,
    Applied,
    BalanceSnapshot,
    BalanceStore,
    Conflict,
    Missing,
)
from backend.domain.generation import BalanceTopUp

_MONEY_QUANTUM = Decimal("0.000001")


def _validate(command: BalanceTopUp) -> None:
    for name in ("paid_usd", "bonus_usd"):
        amount: Decimal = getattr(command, name)
        if amount < 0:
            raise InvalidInputError(message=f"{name} must be non-negative")
        if amount != amount.quantize(_MONEY_QUANTUM):
            raise InvalidInputError(message=f"{name} must have at most 6 decimal places")
    if command.free_requests < 0:
        raise InvalidInputError(message="free_requests must be non-negative")


@dataclass
class BalanceService:
    store: BalanceStore
    loader: BalanceLoader
    config: BillingConfig

    async def apply_top_up(self, command: BalanceTopUp) -> None:
        _validate(command)
        snapshot = await self.loader.ensure_loaded(command.user_id)
        for _ in range(self.config.RESERVE_MAX_ATTEMPTS):
            outcome = await self.store.top_up(
                operation_id=command.operation_id,
                user_id=command.user_id,
                expected_version=snapshot.version,
                new=snapshot.apply_top_up(command),
            )
            match outcome:
                case Applied() | AlreadyApplied():
                    return
                case Conflict(current=current):
                    snapshot = current
                case Missing():
                    snapshot = await self.loader.ensure_loaded(command.user_id)
        raise BalanceContentionError(message="top-up retries exhausted")

    async def get_balance(self, user_id: UUID) -> BalanceSnapshot:
        return await self.loader.ensure_loaded(user_id)
