from collections.abc import Mapping
from typing import Any, Self

from msgspec import Struct, convert


class ImplFromBuiltinsSupported(Struct):
    @classmethod
    def from_builtins(cls, data: Mapping[str, Any]) -> Self:
        return convert(data, type=cls, from_attributes=True)


class StructDTO(ImplFromBuiltinsSupported): ...
