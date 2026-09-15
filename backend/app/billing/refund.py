from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from backend.app.billing.balance_loader import BalanceLoader
from backend.app.billing.config import BillingConfig
from backend.app.errors import BalanceContentionError
from backend.app.shared.ports.billing.balance_store import Applied, BalanceStore, Missing, Stale
from backend.domain.generation import DebitPlan


@dataclass
class ReservationRefunder:
    """Returns a reserved plan to the balance; a no-op when the record is no longer running."""

    store: BalanceStore
    loader: BalanceLoader
    config: BillingConfig

    async def refund(
        self, *, user_id: UUID, client_request_id: UUID, plan: DebitPlan, error: str
    ) -> None:
        for _ in range(self.config.RESERVE_MAX_ATTEMPTS):
            outcome = await self.store.refund(
                user_id=user_id, client_request_id=client_request_id, plan=plan, error=error
            )
            match outcome:
                case Applied() | Stale():
                    return
                case Missing():
                    await self.loader.ensure_loaded(user_id)
        raise BalanceContentionError(message="refund retries exhausted")
