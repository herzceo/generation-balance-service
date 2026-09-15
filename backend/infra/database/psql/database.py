from __future__ import annotations

from typing import TYPE_CHECKING, Self, final

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.shared.db.database import Database
from backend.infra.database.psql.repos.gateway import ImplRepoGateway

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSessionTransaction


@final
class ImplDatabase(Database):
    __slots__ = ("_session", "_txn")

    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session: AsyncSession = session_maker()
        self._txn: AsyncSessionTransaction | None = None

    async def close(self) -> None:
        await self._session.close()

    @property
    def gateway(self) -> ImplRepoGateway:
        if self._txn is None:
            msg = "Database.gateway accessed outside of an `async with db:` block"
            raise RuntimeError(msg)
        return ImplRepoGateway(self._session)

    async def commit(self) -> None:
        if self._txn is None:
            msg = "Database.commit() called outside of an `async with db:` block"
            raise RuntimeError(msg)
        await self._txn.commit()

    async def __aenter__(self) -> Self:
        if self._txn is not None:
            msg = "Database does not support nested `async with db:` blocks"
            raise RuntimeError(msg)
        self._txn = await self._session.begin()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        txn, self._txn = self._txn, None
        if txn is not None and txn.is_active:
            self._session.expunge_all()
            await txn.rollback()
