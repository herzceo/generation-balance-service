---
paths:
  - "tests/**/*.py"
---

# Testing Rules

## Test type decision

**Unit tests** (`tests/unit/`): pure logic with no I/O — `Option[T]` (`tests/unit/internal/`) and the
value-object arithmetic of `BalanceSnapshot` / money serialisation (`tests/unit/billing/`). If a
test needs Redis, PostgreSQL or the provider, it is an integration test.

**Integration tests** (`tests/integration/`): everything else. Real PostgreSQL + real Redis via
testcontainers, the real `FakeGenerationProvider` from the given module. Nothing is mocked. This
is the default test type.

Never write unit tests for services, the Redis adapter, the repository or the flusher.

## Directory structure

```
tests/
├── unit/
│   ├── internal/test_option.py
│   └── billing/test_balance_snapshot.py
└── integration/
    ├── conftest.py            # session: postgres_url (alembic upgrade head), redis_url, manager
    ├── test_migrations.py     # compare_metadata(...) == []
    └── billing/
        ├── conftest.py        # function: shared_state, provider, engine, redis, container, store, pg_counter
        ├── ioc.py             # builds the Dishka container from engine + redis + provider
        ├── helpers.py         # run_generation, run_top_up, seed_pg_balance, read_*_balance, wait_until
        ├── test_single.py
        ├── test_parallel.py
        ├── test_redelivery.py
        ├── test_provider_errors.py
        ├── test_top_up.py
        ├── test_cold_cache.py
        ├── test_pg_budget.py
        ├── test_convergence.py
        └── test_multiprocess.py
```

## Fixture scopes

| Fixture | Scope | Reason |
|---------|-------|--------|
| `postgres_url` | session | Container starts once; migrations run once |
| `redis_url` | session | Container starts once |
| `manager` | session | `multiprocessing.Manager()` started once, `shutdown()` at teardown |
| `shared_state` | function | `create_shared_provider_state(manager)` — fresh provider counters per test |
| `provider` | function | `FakeGenerationProvider(shared_state)` |
| `engine` | function | NullPool engine, disposed at teardown |
| `redis` | function | `redis.asyncio` client; `FLUSHDB` before the test, `aclose()` after |
| `billing_config` | function | fast defaults (`RUNNING_POLL_INTERVAL_SECONDS=0.01`, `FLUSH_INTERVAL_SECONDS=0.05`) |
| `container` | function | Fresh Dishka container per test — complete state isolation |
| `store` | function | `BalanceStore` from the container (direct Redis inspection) |
| `pg_counter` | function | `before_cursor_execute` listener on `engine.sync_engine`; `.count`, `.statements`, `.reset()` |

Never promote `container`, `engine` or `redis` to session scope — shared state between tests
causes flaky order-dependent failures. PostgreSQL is never truncated: use a fresh `uuid4()` user
per test.

## Calling services

Open one REQUEST scope per operation, exactly like production callers:

```python
async def run_generation(container: AsyncContainer, request: GenerationRequest) -> GenerationResult:
    async with container() as c:
        service = await c.get(GenerationService)
        return await service.execute_generation(request)
```

`asyncio.gather(*[run_generation(container, r) for r in requests], return_exceptions=True)` is the
parallel idiom; classify results by `isinstance`.

## Arranging state

- PostgreSQL rows: `seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"), ...)` writes
  through the repo, then call `pg_counter.reset()` so the seed does not count against the budget.
- Redis balance: run a top-up, or seed PostgreSQL and let the cold load populate Redis.
- `free_usd` cannot be topped up through `BalanceTopUp`; seed it via the PostgreSQL row.
- Provider behaviour: `provider.set_scenario(client_request_id, ProviderScenario(delay_seconds=0.5, fail=True))`.

## What to assert

- **Balances**: compare `Decimal` values field by field (PostgreSQL returns scale-6 values;
  `Decimal("0.05") == Decimal("0.050000")`), plus `version`. Every parallel test asserts all
  buckets `>= 0` and `initial − final == Σ billed_cost_usd`.
- **Provider**: `provider.get_stats()` — `calls[id] == 1` per executed id, `max_active_calls >= 2`
  for parallelism (print the value; never assert an exact concurrency).
- **PostgreSQL budget**: exact `pg_counter.count` numbers (1 SELECT per cold load, 1 upsert per
  non-empty flush, 0 for an empty flush). Assert the count before and after `flush_once()`.
- **Exceptions**: `pytest.raises(InsufficientBalanceError)` etc.; in `gather` results check the
  class, never the message text.
- **Convergence**: after `flush_once()` the PostgreSQL row equals the Redis snapshot and
  `bal:dirty` is empty; a second flush returns 0 and issues 0 statements.

## Timing

No bare `asyncio.sleep(x)` waits. Use `wait_until(predicate, timeout=5.0, interval=0.02)` from
`helpers.py` for: `provider.get_stats().active_calls >= 1` (before sending an in-flight duplicate),
PostgreSQL catching up with the background flusher, waiter timeouts. Drive the background flusher as
`asyncio.create_task(flusher.run())` with a short `FLUSH_INTERVAL_SECONDS`, then `cancel()` and
await it under `contextlib.suppress(asyncio.CancelledError)`.

## Multi-process tests

`tests/integration/billing/test_multiprocess.py` uses `multiprocessing.get_context("spawn")` on every
platform. The worker function is **module-level** (spawn must import it by name), takes only
picklable primitives plus the `SharedProviderState` proxies (`redis_url`, `postgres_url`,
`user_id: str`, requests as tuples of `str`/`bool`), builds its own engine/redis/container, runs
`asyncio.run(...)` and returns builtins only (result tuples, error class names, its own PG statement
count). The parent asserts on the shared provider stats, on Redis, on `sum(child pg counts)`, then
flushes and compares PostgreSQL with Redis.

## Naming conventions

Tests express behaviour, not implementation internals:

```
test_{action}_{condition}
```

Examples: `test_generation_basic_with_paid_balance_debits_paid`,
`test_parallel_premium_exactly_k_succeed`, `test_redelivery_in_flight_returns_same_result_once`,
`test_provider_failure_refunds_reservation`, `test_top_up_same_operation_id_applied_once`,
`test_cold_cache_concurrent_requests_load_once`, `test_thirty_generations_cost_two_pg_statements`.

Don't name tests after internals (`test_reserve_script_returns_conflict` is wrong).

## What NOT to test

- Framework behaviour: SQLAlchemy, redis-py, Dishka
- mypy-caught issues: wrong types, missing fields
- Alembic migration correctness beyond the drift test (migrations run in the session fixture)
- Private helpers of the adapter or SQL/Lua text
- Error messages verbatim: check exception classes and `error.code`

## Coverage expectations

Required scenarios (each already has a file): single requests per policy branch, parallel
requests with funds for exactly K, in-flight and post-completion redelivery, provider failures
(refund, cached failure), top-up idempotency and concurrency with generations, cold cache (balance
in PostgreSQL only), PostgreSQL statement budget, Redis/PostgreSQL convergence, multi-process
execution and cross-process redelivery, schema drift.

Aim for meaningful invariants, not line coverage percentage.
