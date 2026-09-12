from functools import cached_property
from typing import final

from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.repos import RepoGateway

from .balance import ImplBalanceRepo


@final
class ImplRepoGateway(RepoGateway):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @cached_property
    def balance(self) -> ImplBalanceRepo:
        return ImplBalanceRepo(self._session)
