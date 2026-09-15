from abc import abstractmethod
from typing import Protocol
from uuid import UUID

from backend.domain.entities.balance import Balance
from backend.internal import Option


class BalanceRepo(Protocol):
    @abstractmethod
    async def get_by_user_id(self, user_id: UUID) -> Option[Balance]: ...

    @abstractmethod
    async def upsert_many(self, balances: list[Balance]) -> set[UUID]:
        """Write the rows whose ``version`` is ahead of the stored one; return their user ids."""
        ...
