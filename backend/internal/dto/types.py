from abc import abstractmethod
from collections.abc import Mapping
from typing import Any, Protocol, Self


class ToBuiltinsSupported(Protocol):
    @abstractmethod
    def to_builtins(self) -> Mapping[str, Any]: ...


class FromBuiltinsSupported(Protocol):
    @classmethod
    @abstractmethod
    def from_builtins(cls, data: Mapping[str, Any]) -> Self: ...


class FromObjectSupported(Protocol):
    @classmethod
    @abstractmethod
    def from_object(cls, obj: object) -> Self: ...


class DTO(ToBuiltinsSupported, FromBuiltinsSupported, FromObjectSupported, Protocol): ...
