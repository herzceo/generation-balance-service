from typing import Any, ClassVar


class ApplicationError(Exception):
    """Base type for errors raised by application/use-case code."""


class DetailedError(ApplicationError):
    _default_message: ClassVar[str] = ""
    _default_code: ClassVar[str] = ""

    def __init__(
        self,
        *,
        message: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message if message is not None else self._default_message
        self.code = code if code is not None else self._default_code
        self.details = details if details is not None else {}
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


class NotFoundError(DetailedError):
    _default_message = "Not found"
    _default_code = "not_found"


class InvalidInputError(DetailedError):
    _default_message = "Invalid input"
    _default_code = "invalid_input"


class ConflictError(DetailedError):
    _default_message = "Conflict"
    _default_code = "conflict"


class GenerationFailedError(DetailedError):
    _default_message = "Generation failed"
    _default_code = "generation_failed"


class GenerationInProgressError(DetailedError):
    _default_message = "Generation is still in progress"
    _default_code = "generation_in_progress"


class BalanceContentionError(ConflictError):
    _default_message = "Balance contention"
    _default_code = "balance_contention"
