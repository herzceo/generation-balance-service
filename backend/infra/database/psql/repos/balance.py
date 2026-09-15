from typing import final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.entities.balance import Balance
from backend.domain.repos.balance import BalanceRepo
from backend.internal import Option


@final
class ImplBalanceRepo(BalanceRepo):
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_user_id(self, user_id: UUID) -> Option[Balance]:
        result = await self._session.execute(select(Balance).where(Balance.user_id == user_id))
        return Option(result.scalar_one_or_none())

    async def upsert_many(self, balances: list[Balance]) -> set[UUID]:
        if not balances:
            return set()
        # Sorted by primary key so concurrent flushers never deadlock on row order.
        rows = [b.to_builtins() for b in sorted(balances, key=lambda b: b.user_id)]
        stmt = insert(Balance).values(rows)
        upsert = stmt.on_conflict_do_update(
            index_elements=[Balance.user_id],
            set_={
                "free_usd": stmt.excluded.free_usd,
                "bonus_usd": stmt.excluded.bonus_usd,
                "paid_usd": stmt.excluded.paid_usd,
                "free_requests": stmt.excluded.free_requests,
                "version": stmt.excluded.version,
                "updated_at": func.now(),
            },
            where=stmt.excluded.version > Balance.version,
        ).returning(Balance.user_id)
        result = await self._session.execute(upsert)
        return set(result.scalars().all())
