from dataclasses import dataclass


@dataclass(slots=True)
class Option[T]:
    value: T | None

    def some(self, exc: Exception | type[Exception]) -> T:
        if self.value is None:
            raise exc
        return self.value

    def none(self, exc: Exception | type[Exception]) -> None:
        if self.value is not None:
            raise exc

    def some_or[R](self, default: R) -> T | R:
        if self.value is None:
            return default
        return self.value
