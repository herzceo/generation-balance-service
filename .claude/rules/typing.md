---
paths:
  - "backend/**/*.py"
---

# Typing Rules

Strict mypy is enforced with `disallow_untyped_defs`, `disallow_any_unimported`, `disallow_any_generics`.
The only exception is `backend.domain.generation` (the given module), covered by a
`[[tool.mypy.overrides]] ignore_errors = true` block because it must stay byte-identical.

## Python 3.12+ Generics

Use the new syntax, not `Generic[T]`:

```python
# correct
class Option[T]:
    value: T | None

type ReserveOutcome = Reserved | Conflict | Missing | Duplicate

# wrong
from typing import Generic, TypeVar
T = TypeVar("T")
class Option(Generic[T]):
    ...
```

## TYPE_CHECKING Imports

Use `TYPE_CHECKING` blocks for type-only imports that would cause circular dependencies or are not needed at runtime:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.domain.repos.gateway import RepoGateway
```

`from __future__ import annotations` is required when using TYPE_CHECKING imports in the same file for forward references.

## All Imports Top-Level

Never import inside function bodies. All imports go at the top of the file or inside `if TYPE_CHECKING:`.

```python
# wrong
def create_engine():
    from sqlalchemy import create_engine
    ...

# correct
from sqlalchemy import create_engine
```

## Type Annotations

- All function parameters and returns typed
- No `Any` except where genuinely unavoidable (Redis replies before parsing)
- `ClassVar[str]` for class-level defaults (`DetailedError._default_code`)
- `Mapped[T]` for all entity columns
- `Protocol` for all interfaces/ports; structural satisfaction is enough (`FakeGenerationProvider`
  satisfies `GenerationProvider` without inheriting it)
- msgspec `StructDTO` fields are instance attributes for mypy; a Protocol with attribute members
  (`BalanceView`) requires the struct to be non-frozen

## @overload

Use `@overload` to express return-type variations based on input types. Keeps call sites fully typed without casts.

```python
from typing import overload

@overload
async def get_by_user_id(self, user_id: UUID, /, *, strict: Literal[True]) -> Balance: ...
@overload
async def get_by_user_id(self, user_id: UUID, /, *, strict: Literal[False] = ...) -> Option[Balance]: ...

async def get_by_user_id(self, user_id: UUID, /, *, strict: bool = False) -> Balance | Option[Balance]:
    result = Option(await self._fetch(user_id))
    if strict:
        return result.some(NotFoundError())
    return result
```

## @final Decorator

Use `@final` (from `typing`) on every concrete class that implements a Protocol:

```python
from typing import final

@final
class ImplRedisBalanceStore(BalanceStore):
    ...
```
