from __future__ import annotations

import asyncio
from dataclasses import dataclass

from backend.app.billing.config import BillingConfig
from backend.app.billing.reaper import ReservationReaper
from backend.app.shared.db.database import Database
from backend.app.shared.ports.billing.balance_store import BalanceStore
from backend.domain.entities.balance import Balance


@dataclass
class BalanceFlusher:
    """Write-behind: copies dirty balances from the hot store to PostgreSQL in one upsert."""

    store: BalanceStore
    db: Database
    reaper: ReservationReaper
    config: BillingConfig

    async def flush_once(self) -> int:
        users = await self.store.dirty_users(self.config.FLUSH_BATCH_SIZE)
        if not users:
            return 0
        snapshots = await self.store.load_many(users)
        rows = [
            Balance(
                user_id=user_id,
                free_usd=snapshot.free_usd,
                bonus_usd=snapshot.bonus_usd,
                paid_usd=snapshot.paid_usd,
                free_requests=snapshot.free_requests,
                version=snapshot.version,
            )
            for user_id, snapshot in snapshots.items()
        ]
        if rows:
            async with self.db:
                await self.db.gateway.balance.upsert_many(rows)
                await self.db.commit()
        await self.store.clear_dirty(
            [
                (user_id, snapshots[user_id].version if user_id in snapshots else -1)
                for user_id in users
            ]
        )
        return len(rows)

    async def run(self) -> None:
        try:
            while True:
                flushed = await self.flush_once()
                await self.reaper.reap_once()
                if flushed < self.config.FLUSH_BATCH_SIZE:
                    await asyncio.sleep(self.config.FLUSH_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            await self.flush_once()
            raise
