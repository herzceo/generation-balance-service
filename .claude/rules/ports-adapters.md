---
paths:
  - "backend/app/shared/ports/**/*.py"
  - "backend/infra/database/redis/**/*.py"
---

# Port & Adapter Rules

Ports are abstract interfaces. Adapters are concrete implementations. They enforce dependency
inversion between `app/` and `infra/`.

## Port (Protocol)

Ports live in `backend/app/shared/ports/{category}/`. The only category today is `billing/`:
`balance_store.py` (`BalanceStore` + value types) and `generation_provider.py`
(`GenerationProvider`).

```python
class GenerationProvider(Protocol):
    @abstractmethod
    async def generate(
        self, request: GenerationRequest, *, authorized_cost_usd: Decimal
    ) -> GenerationResult: ...
```

- Always `Protocol`, never `ABC`; methods `@abstractmethod`
- Named after the capability, not the implementation: `BalanceStore`, not `RedisBalanceStore`
- Value types that cross the port (`BalanceSnapshot`, `GenerationRecord`, outcome dataclasses)
  live next to the Protocol so `infra/` can import them from `app.shared`
- `BalanceSnapshot` is a **non-frozen** `StructDTO(kw_only=True)`: the given `BalanceView`
  Protocol declares settable attributes and mypy rejects a frozen struct
- Pure arithmetic (`apply_plan`, `refund_plan`, `apply_top_up`, `money_to_str`, `str_to_money`)
  lives on/next to the value types and is unit-tested

## Adapter (Implementation)

Adapters live in `backend/infra/database/redis/adapters/` (Redis) or
`backend/infra/database/psql/` (PostgreSQL).

```python
@final
class ImplRedisBalanceStore(BalanceStore):
    def __init__(self, redis: Redis, ...) -> None:
        self._redis = redis
        self._reserve = redis.register_script(RESERVE)
```

- Always `@final`; naming `Impl{Detail}{PortName}` -- `ImplRedisBalanceStore`
- Receives infrastructure dependencies (the `redis.asyncio.Redis` client, TTL values) via constructor
- Parsing Redis replies into value types stays in private helpers of the adapter

## Lua conventions (infra/database/redis/scripts.py)

- Every key goes through `KEYS`, every value/TTL/timestamp through `ARGV`; scripts are
  deterministic (no `TIME`, no randomness)
- Scripts compare **only** the integer `version` field (string compare of what `HGET` returns) and
  bump it with `HINCRBY`; they never add or compare money
- Money travels as strings produced by `format(d, "f")` in Python; all arithmetic is `Decimal`
- Return arrays start with a tag (`OK`, `CONFLICT`, `MISSING`, `DUP`, `STALE`); use `''`
  sentinels, never `nil` inside a table (Redis truncates the array)
- A mutation and its bookkeeping (`SADD bal:dirty`, record HSET, marker SET) happen in the same
  script; never split them across round trips
- Register scripts once with `redis.register_script(...)` (EVALSHA with automatic EVAL fallback)
- Money-path commands (`settle`, `refund`, `forget_inflight`) go through `_retrying`: up to
  `RETRY_ATTEMPTS` (3) on `redis.exceptions.ConnectionError`/`TimeoutError` with 50 ms · attempt
  back-off; safe because every script is idempotent (`status == running` guard, version CAS)

Key layout:

| Key | Type | Content |
|---|---|---|
| `bal:{user_id}` | HASH | `free_usd bonus_usd paid_usd free_requests version`; no TTL |
| `bal:dirty` | SET | user ids with unflushed changes |
| `bal:load:{user_id}` | STRING | cold-load lock (`SET NX PX`) |
| `gen:{client_request_id}` | HASH | generation record; one TTL for running/done/failed |
| `gen:inflight` | ZSET | running reservations scored by `started_at`; `RESERVE` adds, `SETTLE`/`REFUND` remove; the reaper scans it |
| `topup:{operation_id}` | STRING | idempotency marker (value = user_id) |

## DI Wiring

Ports and adapters are wired in `backend/entry/ioc.py`:

```python
provider.provide(ImplRedisBalanceStore, provides=BalanceStore)
provider.provide(lambda: fake_provider, provides=GenerationProvider)
```

Services depend on the port type, never the adapter:

```python
@dataclass
class GenerationService:
    store: BalanceStore          # Port, not ImplRedisBalanceStore
    provider: GenerationProvider
```
