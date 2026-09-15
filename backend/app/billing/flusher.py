from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from backend.app.billing.config import BillingConfig
from backend.app.billing.reaper import ReservationReaper
from backend.app.shared.db.database import Database
from backend.app.shared.ports.billing.balance_store import BalanceStore
from backend.domain.entities.balance import Balance

logger = logging.getLogger(__name__)


def _state(row: Balance) -> tuple[object, ...]:
    return (row.free_usd, row.bonus_usd, row.paid_usd, row.free_requests, row.version)


@dataclass
class BalanceFlusher:
    """Write-behind: copies dirty balances from the hot store to PostgreSQL in one upsert."""

    store: BalanceStore
    db: Database
    reaper: ReservationReaper
    config: BillingConfig

    async def flush_once(self) -> int:
        """Flush one batch of dirty users; returns how many rows PostgreSQL accepted."""
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
        written: set[UUID] = set()
        stored: dict[UUID, Balance | None] = {}
        if rows:
            async with self.db:
                written = await self.db.gateway.balance.upsert_many(rows)
                for row in rows:
                    if row.user_id not in written:
                        stored[row.user_id] = (
                            await self.db.gateway.balance.get_by_user_id(row.user_id)
                        ).value
                await self.db.commit()
        for row in rows:
            if row.user_id not in written:
                await self._repair_regressed(row, stored[row.user_id])
        await self.store.clear_dirty(
            [
                (user_id, snapshots[user_id].version if user_id in snapshots else -1)
                for user_id in users
            ]
        )
        return len(written)

    async def _repair_regressed(self, row: Balance, stored: Balance | None) -> None:
        """Advance a hot store that lost writes past the PostgreSQL row.

        A rejected row is either another flusher's newer write (the CAS then fails harmlessly)
        or a hot store that restarted behind PostgreSQL; only the second one is repaired.
        """
        if stored is None or _state(stored) == _state(row):
            return
        advanced = await self.store.advance_version(
            row.user_id, expected=row.version, to=stored.version + 1
        )
        if advanced:
            logger.warning(
                "balance %s: hot store version %d is behind PostgreSQL version %d; advanced to %d",
                row.user_id,
                row.version,
                stored.version,
                stored.version + 1,
            )

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
