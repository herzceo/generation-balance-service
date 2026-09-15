"""A money script that landed but whose reply was lost must not be applied twice."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from backend.app.shared.ports.billing import BalanceStore, GenerationStatus
from backend.domain.generation import BalanceTopUp
from tests.integration.billing.helpers import (
    ScaledProvider,
    basic_request,
    drop_reply_once,
    get_balance,
    read_redis_balance,
    run_generation,
    run_top_up,
    seed_pg_balance,
)
from tests.integration.billing.ioc import create_test_container

if TYPE_CHECKING:
    from dishka import AsyncContainer
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

    from backend.domain.generation import FakeGenerationProvider


async def test_reserve_retry_after_landed_reply_debits_once(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    flaky = drop_reply_once(store, "_reserve", after_landing=True)

    request = basic_request(user_id)
    result = await run_generation(container, request)

    assert flaky.calls == 2
    assert result.billed_cost_usd == Decimal("0.08")
    assert provider.get_stats().calls == {request.client_request_id: 1}
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("0.92")
    assert balance.version == 1


async def test_settle_retry_after_landed_reply_is_not_reported_as_failure(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    flaky = drop_reply_once(store, "_settle", after_landing=True)

    request = basic_request(user_id)
    result = await run_generation(container, request)

    assert flaky.calls == 2
    assert result.billed_cost_usd == Decimal("0.08")
    record = (await store.get_generation(request.client_request_id)).value
    assert record is not None
    assert record.status is GenerationStatus.DONE
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("0.92")
    assert balance.version == 1


async def test_settle_retry_after_landed_reply_refunds_the_surplus_once(
    engine: AsyncEngine, redis: Redis, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    scaled = create_test_container(
        engine=engine, redis=redis, provider=ScaledProvider(provider, override=Decimal("0.03"))
    )
    try:
        await seed_pg_balance(scaled, user_id, paid_usd=Decimal("1.00"))
        scaled_store: BalanceStore = await scaled.get(BalanceStore)
        flaky = drop_reply_once(scaled_store, "_settle", after_landing=True)

        result = await run_generation(scaled, basic_request(user_id))

        assert flaky.calls == 2
        assert result.billed_cost_usd == Decimal("0.03")
        balance = await get_balance(scaled, user_id)
        assert balance.paid_usd == Decimal("0.97")
        assert balance.version == 2
    finally:
        await scaled.close()


async def test_settle_retry_before_landing_still_settles(
    container: AsyncContainer, store: BalanceStore
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    flaky = drop_reply_once(store, "_settle", after_landing=False)

    result = await run_generation(container, basic_request(user_id))

    assert flaky.calls == 2
    assert result.billed_cost_usd == Decimal("0.08")
    assert (await get_balance(container, user_id)).paid_usd == Decimal("0.92")


async def test_top_up_retry_after_landed_reply_credits_once(
    container: AsyncContainer, store: BalanceStore
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    await get_balance(container, user_id)  # warm the hot store so the first call credits
    flaky = drop_reply_once(store, "_top_up", after_landing=True)

    await run_top_up(
        container,
        BalanceTopUp(operation_id=uuid4(), user_id=user_id, paid_usd=Decimal("5.00")),
    )

    assert flaky.calls == 2
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("6.00")
    assert balance.version == 1
