from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, Self

if TYPE_CHECKING:
    from backend.domain.repos.gateway import RepoGateway


class Database(Protocol):
    __slots__ = ()

    @property
    @abstractmethod
    def gateway(self) -> RepoGateway: ...

    @abstractmethod
    async def commit(self) -> None: ...

    @abstractmethod
    async def rollback(self) -> None: ...

    @abstractmethod
    async def flush(self) -> None: ...

    @abstractmethod
    async def __aenter__(self) -> Self: ...

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None: ...
