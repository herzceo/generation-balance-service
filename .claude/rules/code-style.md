---
paths:
  - "backend/**/*.py"
  - "tests/**/*.py"
---

# Code Style Rules

## Formatting

- ruff with `select = ["ALL"]`, line-length 100
- Enforced automatically by `just check` PostToolUse hook
- `backend/domain/generation.py` is excluded from ruff and mypy-overridden; never touch it

## Runtime

- All Python commands run through `uv run` -- never bare `python3` or `python`
- Shell scripts, justfile recipes, hooks: `uv run python`, `uv run ruff`, `uv run mypy`

## Naming Conventions

| Thing | Convention | Example |
|-------|-----------|---------|
| Entity | PascalCase | `Balance` |
| Repository Protocol | `{Entity}Repo` | `BalanceRepo` |
| Repository Impl | `Impl{Entity}Repo` | `ImplBalanceRepo` |
| Port Protocol | Capability name | `BalanceStore`, `GenerationProvider` |
| Adapter | `Impl{Detail}{Port}` | `ImplRedisBalanceStore` |
| Service | `{Subject}Service` / `{Subject}{Role}` | `GenerationService`, `BalanceService`, `BalanceLoader`, `BalanceFlusher` |
| Config | `{Domain}Config` | `BillingConfig` |
| Error | `{Subject}{Condition}Error` | `GenerationFailedError`, `BalanceContentionError` |
| Value type | PascalCase noun | `BalanceSnapshot`, `GenerationRecord` |
| Outcome | PascalCase past participle / noun | `Reserved`, `Conflict`, `Missing` |
| Lua script constant | UPPER_SNAKE | `RESERVE`, `CLEAR_DIRTY` |
| File | snake_case | `balance_store.py`, `balance_loader.py` |

## File Organization

- One primary class per file
- File named after the primary class (PascalCase -> snake_case)
- `__init__.py` with explicit `__all__` tuple and trailing comma for re-exports

```python
from .balance import BalanceService
from .generation import GenerationService

__all__ = (
    "BalanceService",
    "GenerationService",
)
```

## Comments

- No useless comments that restate the code
- No section dividers like `# ---- Models ----` or `# -- Config --`
- Only comment where logic is genuinely non-obvious (a CAS guard, a deliberate ordering)

## Imports

- All imports top-level or inside `if TYPE_CHECKING:`
- Never import inside function bodies
- `from __future__ import annotations` when using TYPE_CHECKING in the same file

## @final

Every concrete class implementing a Protocol gets `@final`:

```python
from typing import final

@final
class ImplBalanceRepo(BalanceRepo):
    ...
```

## Money

- `Decimal` everywhere; never `float`
- Serialise with `format(d, "f")`; parse with `Decimal(s)`
- Compare `Decimal` values, never their string forms

## Other

- `@dataclass` on services (not `__init__`)
- `__slots__ = ("_session",)` on repo implementations
- Prefer `|` union syntax over `Union[]`: `str | None` not `Optional[str]`
