---
name: add-test
description: Write an integration test for a billing scenario (or a unit test for pure value-object logic). Tests call services through the Dishka container against real PostgreSQL + Redis.
argument-hint: <domain> <scenario>
---

# /add-test — Write Tests

## Usage

```
/add-test billing redelivery_after_top_up
/add-test billing flush_batches_many_users
/add-test unit balance_snapshot_refund
```

## Decision: unit vs integration

Unit test only for pure functions with no I/O: `backend/internal/` utilities and the arithmetic
on `BalanceSnapshot` / money serialisation (`tests/unit/billing/test_balance_snapshot.py`).

Integration test for everything else: services, adapter, repository, flusher, multi-process
behaviour. Real PostgreSQL + Redis via testcontainers, the real `FakeGenerationProvider`.

## File placement

| Target | File path |
|--------|-----------|
| Generation / top-up / flush scenario | `tests/integration/billing/test_{topic}.py` (extend the matching file) |
| Multi-process scenario | `tests/integration/billing/test_multiprocess.py` |
| Schema | `tests/integration/test_migrations.py` |
| Pure logic | `tests/unit/billing/test_balance_snapshot.py`, `tests/unit/internal/test_{module}.py` |

## Read before writing

1. `tests/integration/billing/conftest.py` and `helpers.py` -- fixtures and helpers
2. The service under test in `backend/app/billing/`
3. A neighbouring test in the same file for the pattern in use
4. `README.md` for the semantic being tested (redelivery, cached failure, cold load)

## Standard fixtures and helpers

```python
from tests.integration.billing.helpers import (
    basic_request, read_pg_balance, read_redis_balance, run_generation, run_top_up,
    seed_pg_balance, wait_until,
)
```

Fixtures: `container`, `provider` (`FakeGenerationProvider`), `store` (`BalanceStore`), `redis`,
`pg_counter`, `billing_config`. Every test uses a fresh `uuid4()` user.

## Skeleton: single scenario

```python
async def test_{action}_{condition}(container: AsyncContainer, provider: FakeGenerationProvider, pg_counter: PgCounter) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    pg_counter.reset()

    result = await run_generation(container, basic_request(user_id))

    assert result.billed_cost_usd == Decimal("0.08")
    balance = await read_redis_balance(store, user_id)
    assert balance.paid_usd == Decimal("0.92")
    assert provider.get_stats().calls[result.client_request_id] == 1
    assert pg_counter.count == 1
```

## Skeleton: parallel scenario

```python
requests = [basic_request(user_id) for _ in range(30)]
outcomes = await asyncio.gather(*(run_generation(container, r) for r in requests), return_exceptions=True)
ok = [o for o in outcomes if isinstance(o, GenerationResult)]
denied = [o for o in outcomes if isinstance(o, InsufficientBalanceError)]
assert len(ok) == 5 and len(denied) == 25
assert provider.get_stats().max_active_calls >= 2
```

Assert invariants (non-negative buckets, `initial − final == Σ billed`) in every parallel test.

## Skeleton: provider behaviour

```python
provider.set_scenario(request.client_request_id, ProviderScenario(delay_seconds=0.5))
task = asyncio.create_task(run_generation(container, request))
await wait_until(lambda: provider.get_stats().active_calls >= 1)
duplicate = await run_generation(container, request)
assert duplicate == await task
```

## Naming rules

```
test_{action}_{condition}
test_generation_basic_with_paid_balance_debits_paid
test_redelivery_after_failure_returns_cached_failure
test_top_up_same_operation_id_applied_once
```

Never name tests after internals (`test_reserve_script_conflict`).

## Coverage checklist per scenario

- [ ] Happy path with the exact expected amounts
- [ ] The failing branch (access error class, provider failure) if one exists
- [ ] Provider call count and balance invariants
- [ ] PostgreSQL statement count when the scenario touches the cold load or the flusher
