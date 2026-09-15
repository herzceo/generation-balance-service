from abc import abstractmethod
from collections.abc import Mapping
from typing import Any, Protocol, Self


class FromBuiltinsSupported(Protocol):
    @classmethod
    @abstractmethod
    def from_builtins(cls, data: Mapping[str, Any]) -> Self: ...
