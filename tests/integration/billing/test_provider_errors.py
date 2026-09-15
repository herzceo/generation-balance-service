from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from backend.app.errors import GenerationFailedError
from backend.app.shared.ports.billing import GenerationStatus
from backend.domain.generation import ProviderScenario
from tests.integration.billing.helpers import (
    assert_non_negative,
    basic_request,
    read_redis_balance,
    run_generation,
    seed_pg_balance,
    split_results,
)
from tests.integration.billing.ioc import create_test_container, fast_billing_config

if TYPE_CHECKING:
    from dishka import AsyncContainer
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

    from backend.app.shared.ports.billing import BalanceStore
    from backend.domain.generation import FakeGenerationProvider


async def test_provider_failure_refunds_reservation(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(
        container, user_id, free_usd=Decimal("0.10"), paid_usd=Decimal("0.05"), free_requests=1
    )
    request = basic_request(user_id)
    provider.set_scenario(request.client_request_id, ProviderScenario(fail=True))

    with pytest.raises(GenerationFailedError):
        await run_generation(container, request)

    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert (balance.free_usd, balance.paid_usd, balance.free_requests) == (
        Decimal("0.10"),
        Decimal("0.05"),
        1,
    )
    assert balance.version == 2
    record = (await store.get_generation(request.client_request_id)).value
    assert record is not None
    assert record.status is GenerationStatus.FAILED
    assert provider.get_stats().calls[request.client_request_id] == 1


async def test_provider_failures_mixed_with_successes_keep_balance_exact(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("2.00"))
    requests = [basic_request(user_id, model_name="premium") for _ in range(10)]
    for request in requests[:4]:
        provider.set_scenario(request.client_request_id, ProviderScenario(fail=True))

    results = await asyncio.gather(
        *(run_generation(container, r) for r in requests), return_exceptions=True
    )

    ok, errors = split_results(results)
    assert len(ok) == 6
    assert len(errors) == 4
    assert all(isinstance(e, GenerationFailedError) for e in errors)
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("0.80")
    assert_non_negative(balance)
    stats = provider.get_stats()
    assert len(stats.calls) == 10
    assert all(count == 1 for count in stats.calls.values())
    print(f"[mixed failures] max_active_calls={stats.max_active_calls}")
    assert stats.max_active_calls >= 2


async def test_provider_failure_does_not_block_other_dialogs(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    failing = basic_request(user_id)
    healthy = basic_request(user_id)
    provider.set_scenario(failing.client_request_id, ProviderScenario(delay_seconds=0.3, fail=True))
    provider.set_scenario(healthy.client_request_id, ProviderScenario(delay_seconds=0.3))

    results = await asyncio.gather(
        run_generation(container, failing),
        run_generation(container, healthy),
        return_exceptions=True,
    )

    assert isinstance(results[0], GenerationFailedError)
    assert not isinstance(results[1], BaseException)
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("0.92")
    assert provider.get_stats().max_active_calls >= 2


async def test_provider_slower_than_timeout_is_abandoned_and_refunded(
    engine: AsyncEngine, redis: Redis, provider: FakeGenerationProvider, store: BalanceStore
) -> None:
    user_id = uuid4()
    impatient = create_test_container(
        engine=engine,
        redis=redis,
        provider=provider,
        billing_config=fast_billing_config(PROVIDER_TIMEOUT_SECONDS=0.1),
    )
    try:
        await seed_pg_balance(impatient, user_id, paid_usd=Decimal("1.00"))
        request = basic_request(user_id)
        provider.set_scenario(request.client_request_id, ProviderScenario(delay_seconds=1.0))

        with pytest.raises(GenerationFailedError):
            await run_generation(impatient, request)
    finally:
        await impatient.close()

    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("1.00")
    assert balance.version == 2
    record = (await store.get_generation(request.client_request_id)).value
    assert record is not None
    assert record.status is GenerationStatus.FAILED
    assert record.error == "TimeoutError"
    assert await redis.zcard("gen:inflight") == 0
    assert provider.get_stats().calls[request.client_request_id] == 1
