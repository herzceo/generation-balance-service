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
- `BalanceSnapshot` is a read-only view: no arithmetic on it. Additive mutations take the amounts
  (`DebitPlan`, `BalanceTopUp`) and the store adds them; only `reserve` takes a snapshot version
- Serialisation helpers (`money_to_micros` / `micros_to_money` for balances and record amounts,
  `money_to_str` / `str_to_money` for the raw provider bill, `quantize_money`) live next to the
  value types and are unit-tested; `money_to_micros` raises on anything finer than the quantum
- Money that enters from outside (a provider bill) is `quantize_money`d to `MONEY_QUANTUM`
  (six decimals, `ROUND_DOWN`) before it touches a balance: the hot store must never hold a value
  `NUMERIC(18, 6)` cannot represent, or the flushed row silently stops matching Redis

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
- Balances are integer micro-dollars: scripts add with `HINCRBY`, never with Lua arithmetic
  (Lua numbers are doubles). `RESERVE` negates via string concat with a `'0'` guard, because
  `HINCRBY '-0'` is rejected by Redis
- Only `RESERVE` compares `version` (string compare of what `HGET` returns) because its plan was
  computed on a snapshot; `SETTLE`, `REFUND`, `TOP_UP` are additive and never conflict. Every
  mutation bumps `version` with `HINCRBY` so a concurrent `RESERVE` re-reads and `CLEAR_DIRTY`
  notices the change; `ADVANCE_VERSION` is the flusher's CAS to jump past a PostgreSQL row that
  is ahead of a hot store which lost writes
- Python keeps `Decimal`; the only decimal string in Redis is the raw `provider_billed_cost_usd`
- Return arrays start with a tag (`OK`, `CONFLICT`, `MISSING`, `DUP`, `DONE`, `STALE`); use `''`
  sentinels, never `nil` inside a table (Redis truncates the array)
- A tag exists per *outcome*, not per branch: `SETTLE` answers `DONE` for a record that is already
  settled and `STALE` only for one the reaper failed, because the caller treats them differently
- A mutation and its bookkeeping (`SADD bal:dirty`, record HSET, marker SET) happen in the same
  script; never split them across round trips
- Register scripts once with `redis.register_script(...)` (EVALSHA with automatic EVAL fallback)
- Every money-path command (`reserve`, `settle`, `refund`, `top_up`, `advance_version`, `forget_inflight`) goes
  through `_retrying`: up to `RETRY_ATTEMPTS` (3) on `redis.exceptions.ConnectionError`/
  `TimeoutError` with 50 ms · attempt back-off. Safe only because each script recognises its own
  landed-but-unacknowledged call: `RESERVE` compares the per-call `reservation_token` on the
  record, `TOP_UP` the `topup:{operation_id}` marker, `SETTLE` the `done` status, `REFUND` the
  `status == running` guard, `ADVANCE_VERSION` its own CAS on the version it expects
- A lock is released by its owner: `bal:load:{user_id}` holds a random token and
  `RELEASE_LOAD_LOCK` deletes it only on a match, so a holder that overran the TTL cannot free the
  lock its successor now holds

Key layout:

| Key | Type | Content |
|---|---|---|
| `bal:{user_id}` | HASH | `free_usd bonus_usd paid_usd` (integer micro-dollars) `free_requests version`; no TTL |
| `bal:dirty` | SET | user ids with unflushed changes |
| `bal:load:{user_id}` | STRING | cold-load lock (`SET NX PX`); value is the owner token |
| `gen:{client_request_id}` | HASH | generation record (incl. `reservation_token`); one TTL for running/done/failed |
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
