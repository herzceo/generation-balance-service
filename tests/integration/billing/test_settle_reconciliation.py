from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from redis.exceptions import ConnectionError as RedisConnectionError

from backend.app.shared.ports.billing import BalanceStore, GenerationStatus
from tests.integration.billing.helpers import (
    ScaledProvider,
    assert_non_negative,
    basic_request,
    get_balance,
    run_generation,
    seed_pg_balance,
    split_results,
    total_usd,
)
from tests.integration.billing.ioc import create_test_container

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import Any

    from dishka import AsyncContainer
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

    from backend.domain.generation import FakeGenerationProvider
    from backend.infra.database.redis.adapters import ImplRedisBalanceStore


async def _scaled(
    engine: AsyncEngine, redis: Redis, provider: FakeGenerationProvider, **kwargs: Any
) -> AsyncIterator[AsyncContainer]:
    container = create_test_container(
        engine=engine, redis=redis, provider=ScaledProvider(provider, **kwargs)
    )
    try:
        yield container
    finally:
        await container.close()


async def test_billed_below_authorized_refunds_surplus_lifo(
    engine: AsyncEngine, redis: Redis, provider: FakeGenerationProvider, store: BalanceStore
) -> None:
    user_id = uuid4()
    async for container in _scaled(engine, redis, provider, override=Decimal("0.03")):
        await seed_pg_balance(
            container, user_id, free_usd=Decimal("0.05"), paid_usd=Decimal("0.03")
        )
        request = basic_request(user_id)
        result = await run_generation(container, request)

        assert result.billed_cost_usd == Decimal("0.03")
        balance = await get_balance(container, user_id)
        assert balance.paid_usd == Decimal("0.03")
        assert balance.free_usd == Decimal("0.02")
        record = (await store.get_generation(request.client_request_id)).value
        assert record is not None
        assert record.status is GenerationStatus.DONE
        assert record.billed_cost_usd == Decimal("0.03")
        assert record.provider_billed_cost_usd == Decimal("0.03")
        assert record.refunded_surplus_usd == Decimal("0.05")


async def test_billed_above_authorized_is_capped(
    engine: AsyncEngine, redis: Redis, provider: FakeGenerationProvider, store: BalanceStore
) -> None:
    user_id = uuid4()
    async for container in _scaled(engine, redis, provider, override=Decimal("0.50")):
        await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
        request = basic_request(user_id, model_name="premium")
        result = await run_generation(container, request)

        assert result.billed_cost_usd == Decimal("0.20")
        assert (await get_balance(container, user_id)).paid_usd == Decimal("0.80")
        record = (await store.get_generation(request.client_request_id)).value
        assert record is not None
        assert record.billed_cost_usd == Decimal("0.20")
        assert record.provider_billed_cost_usd == Decimal("0.50")
        assert record.refunded_surplus_usd == Decimal(0)


async def test_redelivery_after_surplus_refund_returns_charged_amount(
    engine: AsyncEngine, redis: Redis, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    async for container in _scaled(engine, redis, provider, override=Decimal("0.03")):
        await seed_pg_balance(
            container, user_id, free_usd=Decimal("0.05"), paid_usd=Decimal("0.03")
        )
        request = basic_request(user_id)
        first = await run_generation(container, request)
        second = await run_generation(container, request)

        assert second == first
        assert second.billed_cost_usd == Decimal("0.03")
        assert provider.get_stats().calls == {request.client_request_id: 1}
        assert total_usd(await get_balance(container, user_id)) == Decimal("0.05")


async def test_surplus_settle_concurrent_with_generations_keeps_balance_exact(
    engine: AsyncEngine, redis: Redis, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    async for container in _scaled(engine, redis, provider, factor=Decimal("0.5")):
        await seed_pg_balance(container, user_id, paid_usd=Decimal("10.00"))
        requests = [basic_request(user_id) for _ in range(11)]
        results = await asyncio.gather(
            *(run_generation(container, r) for r in requests), return_exceptions=True
        )
        ok, errors = split_results(results)

        assert errors == []
        assert len(ok) == 11
        assert all(r.billed_cost_usd == Decimal("0.04") for r in ok)
        balance = await get_balance(container, user_id)
        assert_non_negative(balance)
        assert Decimal("10.00") - total_usd(balance) == sum(r.billed_cost_usd for r in ok)


async def test_settle_survives_transient_redis_error(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    impl: ImplRedisBalanceStore = store  # type: ignore[assignment]
    original = impl._settle
    calls = 0

    async def flaky(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            msg = "connection dropped"
            raise RedisConnectionError(msg)
        return await original(**kwargs)

    impl._settle = flaky  # type: ignore[assignment]
    request = basic_request(user_id)
    result = await run_generation(container, request)

    assert calls == 2
    assert result.billed_cost_usd == Decimal("0.08")
    record = (await store.get_generation(request.client_request_id)).value
    assert record is not None
    assert record.status is GenerationStatus.DONE
    assert (await get_balance(container, user_id)).paid_usd == Decimal("0.92")
