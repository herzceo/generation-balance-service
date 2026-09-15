from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from backend.app.billing.balance_loader import BalanceLoader
from backend.app.billing.config import BillingConfig
from backend.app.errors import BalanceContentionError, InvalidInputError
from backend.app.shared.ports.billing.balance_store import (
    MONEY_QUANTUM,
    AlreadyApplied,
    Applied,
    BalanceSnapshot,
    BalanceStore,
    Missing,
)
from backend.domain.generation import BalanceTopUp


def _validate(command: BalanceTopUp) -> None:
    for name in ("paid_usd", "bonus_usd"):
        amount: Decimal = getattr(command, name)
        if amount < 0:
            raise InvalidInputError(message=f"{name} must be non-negative")
        if amount != amount.quantize(MONEY_QUANTUM):
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
        for _ in range(self.config.RESERVE_MAX_ATTEMPTS):
            match await self.store.top_up(command):
                case Applied() | AlreadyApplied():
                    return
                case Missing():
                    await self.loader.ensure_loaded(command.user_id)
        raise BalanceContentionError(message="top-up retries exhausted")

    async def get_balance(self, user_id: UUID) -> BalanceSnapshot:
        return await self.loader.ensure_loaded(user_id)
