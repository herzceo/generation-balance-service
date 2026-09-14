from __future__ import annotations

import asyncio
import contextlib
from decimal import Decimal
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest

from backend.app.billing import BalanceFlusher
from backend.app.errors import GenerationFailedError
from backend.app.shared.ports.billing import BalanceStore, GenerationStatus
from backend.domain.generation import ProviderScenario
from tests.integration.billing.helpers import (
    basic_request,
    get_balance,
    reap_once,
    run_generation,
    seed_pg_balance,
    wait_until,
)
from tests.integration.billing.ioc import create_test_container, fast_billing_config

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from dishka import AsyncContainer
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

    from backend.domain.generation import FakeGenerationProvider
    from tests.integration.billing.helpers import PgCounter


async def _inflight(redis: Redis) -> int:
    return cast("int", await redis.zcard("gen:inflight"))


@pytest.fixture
async def quick_reap(
    engine: AsyncEngine, redis: Redis, provider: FakeGenerationProvider
) -> AsyncIterator[AsyncContainer]:
    container = create_test_container(
        engine=engine,
        redis=redis,
        provider=provider,
        billing_config=fast_billing_config(REAP_AFTER_SECONDS=0.1),
    )
    try:
        yield container
    finally:
        await container.close()


async def test_stale_running_reservation_is_refunded(
    quick_reap: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider, redis: Redis
) -> None:
    user_id = uuid4()
    await seed_pg_balance(quick_reap, user_id, paid_usd=Decimal("1.00"))
    request = basic_request(user_id)
    provider.set_scenario(request.client_request_id, ProviderScenario(delay_seconds=1.0))
    task = asyncio.create_task(run_generation(quick_reap, request))
    await wait_until(lambda: provider.get_stats().active_calls >= 1)
    assert (await get_balance(quick_reap, user_id)).paid_usd == Decimal("0.92")

    reaped = 0

    async def _reap() -> bool:
        nonlocal reaped
        reaped += await reap_once(quick_reap)
        return reaped >= 1

    await wait_until(_reap)

    assert (await get_balance(quick_reap, user_id)).paid_usd == Decimal("1.00")
    record = (await store.get_generation(request.client_request_id)).value
    assert record is not None
    assert record.status is GenerationStatus.FAILED
    assert await _inflight(redis) == 0

    with pytest.raises(GenerationFailedError):
        await task
    assert (await get_balance(quick_reap, user_id)).paid_usd == Decimal("1.00")
    assert provider.get_stats().calls == {request.client_request_id: 1}


async def test_fresh_running_reservation_is_not_reaped(
    container: AsyncContainer, provider: FakeGenerationProvider, redis: Redis
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    request = basic_request(user_id)
    provider.set_scenario(request.client_request_id, ProviderScenario(delay_seconds=0.3))
    task = asyncio.create_task(run_generation(container, request))
    await wait_until(lambda: provider.get_stats().active_calls >= 1)

    assert await reap_once(container) == 0
    assert await _inflight(redis) == 1
    result = await task
    assert result.billed_cost_usd == Decimal("0.08")
    assert (await get_balance(container, user_id)).paid_usd == Decimal("0.92")


async def test_settled_and_failed_records_leave_inflight_index(
    container: AsyncContainer, provider: FakeGenerationProvider, redis: Redis
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    ok = basic_request(user_id)
    failing = basic_request(user_id)
    provider.set_scenario(failing.client_request_id, ProviderScenario(fail=True))

    await run_generation(container, ok)
    assert await _inflight(redis) == 0
    with pytest.raises(GenerationFailedError):
        await run_generation(container, failing)
    assert await _inflight(redis) == 0


async def test_reap_on_empty_index_costs_nothing(
    container: AsyncContainer, pg_counter: PgCounter
) -> None:
    pg_counter.reset()
    assert await reap_once(container) == 0
    assert pg_counter.count == 0


async def test_background_flusher_reaps(
    quick_reap: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(quick_reap, user_id, paid_usd=Decimal("1.00"))
    request = basic_request(user_id)
    provider.set_scenario(request.client_request_id, ProviderScenario(delay_seconds=1.0))

    async with quick_reap() as scope:
        flusher = await scope.get(BalanceFlusher)
        flusher_task = asyncio.create_task(flusher.run())
        generation = asyncio.create_task(run_generation(quick_reap, request))
        try:

            async def _reaped() -> bool:
                record = (await store.get_generation(request.client_request_id)).value
                return record is not None and record.status is GenerationStatus.FAILED

            await wait_until(_reaped)
        finally:
            flusher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flusher_task

    with pytest.raises(GenerationFailedError):
        await generation
    assert (await get_balance(quick_reap, user_id)).paid_usd == Decimal("1.00")
