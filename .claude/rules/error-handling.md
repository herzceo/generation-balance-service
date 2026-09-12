---
paths:
  - "backend/app/errors.py"
  - "backend/app/billing/**/*.py"
---

# Error Handling Rules

## Error Hierarchy (app/errors.py)

```python
ApplicationError                         # Base
  DetailedError(message, code, details)  # Structured
    NotFoundError                        # not_found
    InvalidInputError                    # invalid_input (unknown model, bad top-up amount, payload drift)
    ConflictError                        # conflict
      BalanceContentionError             # balance_contention (CAS or cold-load retries exhausted)
    GenerationFailedError                # generation_failed (provider raised; cached per client_request_id)
    GenerationInProgressError            # generation_in_progress (waiter deadline while a duplicate runs)
```

Access errors defined by the given module (`backend/domain/generation.py`) propagate untouched from
`DebitPolicyService.authorize`: `InsufficientBalanceError`, `FreeRequestsExhaustedError`,
`ConditionalFreeAccessDeniedError` (all `GenerationAccessError`). They are never cached and never
wrapped; a redelivery recomputes them against the current balance.

## Subclass defaults — never use `@dataclass` on `DetailedError`

`DetailedError` is **not** a dataclass. It uses `_default_message` / `_default_code` as `ClassVar[str]` and a manual `__init__` that falls back to those class-level defaults when the caller does not pass `message=` / `code=`.

Do not "simplify" this back to `@dataclass(eq=False)` with `code: str = ""` as a field. If you do, every subclass override (`code = "not_found"`) is silently shadowed by the dataclass-generated `__init__`, which writes the empty default to every instance. The exception will look correct at the class level (`NotFoundError.code == "not_found"`) but `instance.code` will be `""`.

When adding a new error type, override the `ClassVar`s only:

```python
class GenerationFailedError(DetailedError):
    _default_message = "Generation failed"
    _default_code = "generation_failed"
```

Never add `code: str = ...` as a class-level annotation in a subclass — that turns it back into a (shadowed) attribute.

Raise with custom message: `raise InvalidInputError(message=f"unknown model {name!r}")`

Raise with details: `raise InvalidInputError(message="Invalid top-up", details={"field": "paid_usd"})`

## Option.some() Pattern

All repository and store lookups return `Option[T]`. Unwrap with `.some(exception)` when absence is
an error, `.value` when absence is a normal branch (cold cache, missing record):

```python
# correct
row = (await self.db.gateway.balance.get_by_user_id(user_id)).value
if row is None:
    seed = BalanceSnapshot.zero()

# wrong -- manual None check where absence is an error
record = await self.store.get_generation(client_request_id)
if record.value is None:
    raise NotFoundError()
```

Other Option methods:
- `.none(exc)` -- raise if value IS present (for uniqueness checks)
- `.some_or(default)` -- return value or default without raising

## Money paths

- Wrap provider exceptions into `GenerationFailedError` **after** the refund has been applied;
  refund on `BaseException` so cancellation also refunds, then re-raise the original.
- `settle`/`refund` outcomes `Stale` mean the record is no longer `running`: log and continue, never
  retry the balance change.
- Retry exhaustion raises `BalanceContentionError`; never silently drop an operation.

## Key Rules

- Services raise domain/application errors directly; there is no HTTP mapping layer
- Never catch generic `Exception` except at the provider call boundary (where it becomes `GenerationFailedError`)
- Never raise `ValueError` or `TypeError` for business logic -- use the error hierarchy
- Always pass a descriptive `message` when raising errors
