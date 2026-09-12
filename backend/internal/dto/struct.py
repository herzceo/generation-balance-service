from collections.abc import Mapping
from typing import Any, Self

from msgspec import Struct, convert, to_builtins


class ImplToBuiltinsSupported(Struct):
    def to_builtins(self) -> Mapping[str, Any]:
        return to_builtins(self, str_keys=True)  # type: ignore[no-any-return]


class ImplFromBuiltinsSupported(Struct):
    @classmethod
    def from_builtins(cls, data: Mapping[str, Any]) -> Self:
        return convert(data, type=cls, from_attributes=True)


class ImplFromObjectSupported(Struct):
    @classmethod
    def from_object(cls, obj: object) -> Self:
        return convert(obj, type=cls, from_attributes=True)


class StructDTO(
    ImplToBuiltinsSupported,
    ImplFromBuiltinsSupported,
    ImplFromObjectSupported,
): ...
