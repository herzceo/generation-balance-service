from __future__ import annotations

import logging
from dataclasses import dataclass
from time import time

from backend.app.billing.config import BillingConfig
from backend.app.billing.refund import ReservationRefunder
from backend.app.shared.ports.billing.balance_store import BalanceStore, GenerationStatus

logger = logging.getLogger(__name__)

REAPED_ERROR = "reaped: reservation exceeded REAP_AFTER_SECONDS"


@dataclass
class ReservationReaper:
    """Refunds reservations whose holder never settled (process died mid-generation)."""

    store: BalanceStore
    refunder: ReservationRefunder
    config: BillingConfig

    async def reap_once(self, now: float | None = None) -> int:
        cutoff = (time() if now is None else now) - self.config.REAP_AFTER_SECONDS
        stale = await self.store.stale_inflight(
            older_than=cutoff, limit=self.config.REAP_BATCH_SIZE
        )
        reaped = 0
        for client_request_id in stale:
            record = (await self.store.get_generation(client_request_id)).value
            if record is None or record.status is not GenerationStatus.RUNNING:
                await self.store.forget_inflight(client_request_id)
                continue
            await self.refunder.refund(
                user_id=record.user_id,
                client_request_id=client_request_id,
                plan=record.plan,
                error=REAPED_ERROR,
            )
            logger.warning("reaped stale reservation %s", client_request_id)
            reaped += 1
        return reaped
