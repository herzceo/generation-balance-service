import re
import typing

_pattern: typing.Final[re.Pattern[str]] = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)


def pascal_case_to_snake_case(string: str, /) -> str:
    return _pattern.sub("_", string).lower().strip("_")
