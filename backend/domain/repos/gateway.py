from abc import abstractmethod
from typing import Protocol

from .balance import BalanceRepo


class RepoGateway(Protocol):
    @property
    @abstractmethod
    def balance(self) -> BalanceRepo: ...
