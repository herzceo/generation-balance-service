from dataclasses import dataclass


@dataclass(slots=True)
class Option[T]:
    value: T | None

    def some(self, exc: Exception | type[Exception]) -> T:
        if self.value is None:
            raise exc
        return self.value
