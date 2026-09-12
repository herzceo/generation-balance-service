from __future__ import annotations

import asyncio
from decimal import Decimal
from time import time
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from backend.app.errors import GenerationFailedError, GenerationInProgressError, InvalidInputError
from backend.app.shared.ports.billing import BalanceStore
from backend.domain.generation import DebitPlan, ProviderScenario
from tests.integration.billing.helpers import (
    basic_request,
    get_balance,
    read_redis_balance,
    run_generation,
    seed_pg_balance,
    wait_until,
)
from tests.integration.billing.ioc import create_test_container, fast_billing_config

if TYPE_CHECKING:
    from dishka import AsyncContainer
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

    from backend.domain.generation import FakeGenerationProvider


async def test_redelivery_in_flight_returns_same_result_once(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    request = basic_request(user_id)
    provider.set_scenario(request.client_request_id, ProviderScenario(delay_seconds=0.5))

    first = asyncio.create_task(run_generation(container, request))
    await wait_until(lambda: provider.get_stats().active_calls >= 1)
    second = await run_generation(container, request)
    assert await first == second

    assert provider.get_stats().calls[request.client_request_id] == 1
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("0.92")
    assert balance.version == 1


async def test_redelivery_after_completion_uses_cached_result(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    request = basic_request(user_id)

    first = await run_generation(container, request)
    second = await run_generation(container, request)

    assert first == second
    assert provider.get_stats().calls[request.client_request_id] == 1
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("0.92")
    assert balance.version == 1


async def test_redelivery_after_failure_returns_cached_failure(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    request = basic_request(user_id)
    provider.set_scenario(request.client_request_id, ProviderScenario(fail=True))

    with pytest.raises(GenerationFailedError):
        await run_generation(container, request)
    with pytest.raises(GenerationFailedError):
        await run_generation(container, request)

    assert provider.get_stats().calls[request.client_request_id] == 1
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("1.00")
    assert balance.version == 2


async def test_redelivery_with_different_payload_is_rejected(
    container: AsyncContainer, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    request = basic_request(user_id)
    await run_generation(container, request)

    changed = basic_request(
        user_id, model_name="premium", client_request_id=request.client_request_id
    )
    with pytest.raises(InvalidInputError):
        await run_generation(container, changed)

    assert provider.get_stats().calls == {request.client_request_id: 1}


async def test_redelivery_waiter_times_out_when_holder_never_settles(
    engine: AsyncEngine, redis: Redis, provider: FakeGenerationProvider, container: AsyncContainer
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    request = basic_request(user_id)
    plan = DebitPlan(paid_usd=Decimal("0.08"))
    snapshot = await get_balance(container, user_id)
    impatient = create_test_container(
        engine=engine,
        redis=redis,
        provider=provider,
        billing_config=fast_billing_config(RUNNING_WAIT_TIMEOUT_SECONDS=0.2),
    )
    try:
        store = await impatient.get(BalanceStore)
        await store.reserve(
            request=request,
            expected_version=snapshot.version,
            new=snapshot.apply_plan(plan),
            plan=plan,
            authorized_cost_usd=Decimal("0.08"),
            started_at=time(),
        )
        with pytest.raises(GenerationInProgressError):
            await run_generation(impatient, request)
    finally:
        await impatient.close()

    assert provider.get_stats().calls == {}
