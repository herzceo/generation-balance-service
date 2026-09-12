from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from backend.domain.generation import FreeRequestsExhaustedError, InsufficientBalanceError
from tests.integration.billing.helpers import (
    assert_non_negative,
    basic_request,
    read_redis_balance,
    run_generation,
    seed_pg_balance,
    split_results,
    total_usd,
)

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from backend.app.shared.ports.billing import BalanceStore
    from backend.domain.generation import FakeGenerationProvider

PARALLEL = 30


async def test_parallel_premium_exactly_k_succeed(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    requests = [basic_request(user_id, model_name="premium") for _ in range(PARALLEL)]

    results = await asyncio.gather(
        *(run_generation(container, r) for r in requests), return_exceptions=True
    )

    ok, errors = split_results(results)
    assert len(ok) == 5
    assert len(errors) == 25
    assert all(isinstance(e, InsufficientBalanceError) for e in errors)
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal(0)
    assert_non_negative(balance)
    assert Decimal("1.00") - total_usd(balance) == sum(r.billed_cost_usd for r in ok)
    stats = provider.get_stats()
    assert len(stats.calls) == 5
    assert all(count == 1 for count in stats.calls.values())
    print(f"[parallel premium] max_active_calls={stats.max_active_calls}")
    assert stats.max_active_calls >= 2


async def test_parallel_basic_free_requests_cap(
    container: AsyncContainer, store: BalanceStore
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, free_usd=Decimal("0.25"), free_requests=3)
    requests = [basic_request(user_id) for _ in range(PARALLEL)]

    results = await asyncio.gather(
        *(run_generation(container, r) for r in requests), return_exceptions=True
    )

    ok, errors = split_results(results)
    assert len(ok) == 3
    assert len(errors) == 27
    assert all(isinstance(e, FreeRequestsExhaustedError) for e in errors)
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.free_usd == Decimal("0.10")
    assert balance.free_requests == 0
    assert_non_negative(balance)


async def test_parallel_different_dialogs_same_user_all_run_concurrently(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("10.00"))
    requests = [basic_request(user_id, dialog_id=uuid4()) for _ in range(PARALLEL)]

    results = await asyncio.gather(*(run_generation(container, r) for r in requests))

    assert sum(r.billed_cost_usd for r in results) == Decimal("2.40")
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("7.60")
    assert balance.version == PARALLEL
    assert_non_negative(balance)
    stats = provider.get_stats()
    assert len(stats.calls) == PARALLEL
    print(f"[parallel basic] max_active_calls={stats.max_active_calls}")
    assert stats.max_active_calls >= 2
