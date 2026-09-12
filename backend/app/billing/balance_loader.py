from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from time import monotonic
from uuid import UUID

from backend.app.billing.config import BillingConfig
from backend.app.errors import BalanceContentionError
from backend.app.shared.db.database import Database
from backend.app.shared.ports.billing.balance_store import BalanceSnapshot, BalanceStore


@dataclass
class BalanceLoader:
    """Serves balances from the hot store, loading from PostgreSQL once on a cache miss."""

    store: BalanceStore
    db: Database
    config: BillingConfig

    async def ensure_loaded(self, user_id: UUID) -> BalanceSnapshot:
        deadline = monotonic() + self.config.LOAD_WAIT_TIMEOUT_SECONDS
        while True:
            snapshot = (await self.store.load(user_id)).value
            if snapshot is not None:
                return snapshot
            if await self.store.try_acquire_load_lock(user_id):
                try:
                    snapshot = (await self.store.load(user_id)).value
                    if snapshot is not None:
                        return snapshot
                    await self.store.seed_if_absent(user_id, await self._load_from_db(user_id))
                finally:
                    await self.store.release_load_lock(user_id)
                continue
            if monotonic() > deadline:
                raise BalanceContentionError(message="balance load timed out")
            await asyncio.sleep(0.01 + random.uniform(0, 0.005))

    async def _load_from_db(self, user_id: UUID) -> BalanceSnapshot:
        async with self.db:
            row = (await self.db.gateway.balance.get_by_user_id(user_id)).value
            await self.db.commit()
        if row is None:
            return BalanceSnapshot.zero()
        return BalanceSnapshot(
            free_usd=row.free_usd,
            bonus_usd=row.bonus_usd,
            paid_usd=row.paid_usd,
            free_requests=row.free_requests,
            version=row.version,
        )
