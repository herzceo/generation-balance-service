from ast import literal_eval
from collections.abc import Callable, Mapping
from os import environ
from typing import Any

from dotenv import load_dotenv
from msgspec import inspect

from backend.internal.dto import types as dto_types


def load_from_env[C: dto_types.FromBuiltinsSupported](config_cls: type[C]) -> C:
    load_dotenv(override=True)
    return _convert_to_dto(environ, config_cls)


def _convert_to_dto[C: dto_types.FromBuiltinsSupported](
    data: Mapping[str, str], dto_cls: type[C]
) -> C:
    dto_data: dict[str, Any] = {}
    for field in inspect.type_info(dto_cls).fields:  # type: ignore[attr-defined]
        value = data.get(field.name)
        if field.required and (value is None):
            raise ValueError(f"Missing required by {dto_cls.__name__!r} config field: {field.name}")
        if value is None:
            continue

        try:
            dto_data[field.name] = _convert_value(value, field.type)
        except (ValueError, SyntaxError, TypeError) as e:
            raise ValueError(
                f"Failed to convert {value!r} to {field.type} for {dto_cls.__name__!r} "
                f"config field: {field.name}"
            ) from e
    return dto_cls.from_builtins(dto_data)


def _convert_value(value: str, msg_type: inspect.Type) -> Any:
    _map: dict[type[inspect.Type], Callable[[Any, Any], Any]] = {
        inspect.StrType: _convert_to_str,
        inspect.IntType: _convert_to_int,
        inspect.FloatType: _convert_to_float,
        inspect.BoolType: _convert_to_bool,
        inspect.ListType: _convert_to_list,
        inspect.DictType: _convert_to_dict,
        inspect.EnumType: _convert_to_enum,
        inspect.LiteralType: _convert_to_literal,
        inspect.UnionType: _convert_to_union,
    }
    if handler := _map.get(type(msg_type)):
        return handler(value, msg_type)
    raise TypeError(f"Unsupported by config classes type: {msg_type}")


def _convert_to_union(value: Any, type: inspect.UnionType) -> Any:
    for member in type.types:
        if isinstance(member, inspect.NoneType):
            continue
        return _convert_value(value, member)
    raise TypeError(f"Union with no convertible member: {type}")


def _convert_to_str(value: Any, _: inspect.Type) -> str:
    return str(value)


def _convert_to_int(value: Any, _: inspect.Type) -> int:
    return int(value)


def _convert_to_float(value: Any, _: inspect.Type) -> float:
    return float(value)


def _convert_to_bool(value: Any, _: inspect.Type) -> bool:
    if value.lower() in ("true", "1"):
        return True
    if value.lower() in ("false", "0"):
        return False
    raise ValueError(f"Invalid value for bool: {value!r}")


def _convert_to_list(value: Any, _: inspect.Type) -> list[Any]:
    return list(literal_eval(value))


def _convert_to_dict(value: Any, _: inspect.Type) -> dict[Any, Any]:
    return dict(literal_eval(value))


def _convert_to_enum(value: Any, type: inspect.EnumType) -> Any:
    return type.cls(value)


def _convert_to_literal(value: Any, type: inspect.LiteralType) -> Any:
    if value not in type.values:
        raise ValueError(f"Invalid value for {type.values} by {type}")
    return value
