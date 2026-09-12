from __future__ import annotations

import asyncio
import contextlib
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from backend.app.billing import BalanceFlusher
from backend.domain.generation import BalanceTopUp, ProviderScenario
from tests.integration.billing.helpers import (
    basic_request,
    dirty_count,
    flush_once,
    read_pg_balance,
    read_redis_balance,
    run_generation,
    run_top_up,
    seed_pg_balance,
    wait_until,
)

if TYPE_CHECKING:
    from dishka import AsyncContainer
    from redis.asyncio import Redis

    from backend.app.shared.ports.billing import BalanceSnapshot, BalanceStore
    from backend.domain.entities.balance import Balance
    from backend.domain.generation import FakeGenerationProvider
    from tests.integration.billing.helpers import PgCounter


def _same(row: Balance | None, snapshot: BalanceSnapshot | None) -> bool:
    if row is None or snapshot is None:
        return False
    return (
        row.free_usd == snapshot.free_usd
        and row.bonus_usd == snapshot.bonus_usd
        and row.paid_usd == snapshot.paid_usd
        and row.free_requests == snapshot.free_requests
        and row.version == snapshot.version
    )


async def _workload(
    container: AsyncContainer, provider: FakeGenerationProvider, user_id: object
) -> None:
    assert isinstance(user_id, type(uuid4()))
    requests = [basic_request(user_id) for _ in range(5)]
    provider.set_scenario(requests[0].client_request_id, ProviderScenario(fail=True))
    await asyncio.gather(
        *(run_generation(container, r) for r in requests),
        run_top_up(
            container, BalanceTopUp(operation_id=uuid4(), user_id=user_id, paid_usd=Decimal("0.50"))
        ),
        run_top_up(container, BalanceTopUp(operation_id=uuid4(), user_id=user_id, free_requests=2)),
        return_exceptions=True,
    )


async def test_flush_once_makes_pg_match_redis(
    container: AsyncContainer,
    store: BalanceStore,
    provider: FakeGenerationProvider,
    redis: Redis,
    pg_counter: PgCounter,
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    await _workload(container, provider, user_id)

    assert await flush_once(container) == 1

    snapshot = await read_redis_balance(store, user_id)
    assert snapshot is not None
    assert snapshot.paid_usd == Decimal("1.18")
    assert snapshot.free_requests == 2
    assert _same(await read_pg_balance(container, user_id), snapshot)
    assert await dirty_count(redis) == 0
    pg_counter.reset()
    assert await flush_once(container) == 0
    assert pg_counter.count == 0


async def test_background_flusher_converges(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider, redis: Redis
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))

    async with container() as scope:
        flusher = await scope.get(BalanceFlusher)
        task = asyncio.create_task(flusher.run())
        try:
            await _workload(container, provider, user_id)

            async def _converged() -> bool:
                return _same(
                    await read_pg_balance(container, user_id),
                    await read_redis_balance(store, user_id),
                )

            await wait_until(_converged)
            await run_top_up(
                container,
                BalanceTopUp(operation_id=uuid4(), user_id=user_id, paid_usd=Decimal("0.01")),
            )
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    snapshot = await read_redis_balance(store, user_id)
    assert snapshot is not None
    assert snapshot.paid_usd == Decimal("1.19")
    assert _same(await read_pg_balance(container, user_id), snapshot)
    assert await dirty_count(redis) == 0


async def test_flush_is_idempotent_and_version_guarded(
    container: AsyncContainer, store: BalanceStore, pg_counter: PgCounter
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    await run_generation(container, basic_request(user_id))

    assert await flush_once(container) == 1
    first = await read_pg_balance(container, user_id)
    assert first is not None
    pg_counter.reset()
    assert await flush_once(container) == 0
    assert pg_counter.count == 0

    await seed_pg_balance(container, user_id, paid_usd=Decimal("5.00"), version=0)
    second = await read_pg_balance(container, user_id)
    assert second is not None
    assert second.paid_usd == first.paid_usd == Decimal("0.92")
    assert second.version == first.version == 1
