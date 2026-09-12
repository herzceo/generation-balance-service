from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from backend.domain.generation import BalanceTopUp, FreeRequestsExhaustedError
from tests.integration.billing.helpers import (
    basic_request,
    flush_once,
    read_pg_balance,
    read_redis_balance,
    run_generation,
    run_top_up,
    seed_pg_balance,
)

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from backend.app.shared.ports.billing import BalanceStore
    from tests.integration.billing.helpers import PgCounter


async def test_generation_cold_cache_loads_pg_balance(
    container: AsyncContainer, store: BalanceStore, pg_counter: PgCounter
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"), free_requests=3)
    pg_counter.reset()
    assert await read_redis_balance(store, user_id) is None

    await run_generation(container, basic_request(user_id))

    redis_balance = await read_redis_balance(store, user_id)
    assert redis_balance is not None
    assert redis_balance.paid_usd == Decimal("0.92")
    assert redis_balance.free_requests == 3
    assert redis_balance.version == 1
    pg_balance = await read_pg_balance(container, user_id)
    assert pg_balance is not None
    assert pg_balance.paid_usd == Decimal("1.00")
    assert pg_balance.version == 0
    assert pg_counter.statements[0].lstrip().upper().startswith("SELECT")
    assert pg_counter.count == 2  # the cold load plus read_pg_balance above


async def test_cold_cache_concurrent_requests_load_once(
    container: AsyncContainer, store: BalanceStore, pg_counter: PgCounter
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("10.00"))
    pg_counter.reset()
    requests = [basic_request(user_id) for _ in range(30)]

    await asyncio.gather(*(run_generation(container, r) for r in requests))

    assert pg_counter.count == 1
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("7.60")


async def test_user_without_pg_row_starts_at_zero(
    container: AsyncContainer, store: BalanceStore, pg_counter: PgCounter
) -> None:
    user_id = uuid4()

    with pytest.raises(FreeRequestsExhaustedError):
        await run_generation(container, basic_request(user_id))

    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal(0)
    assert balance.version == 0
    assert pg_counter.count == 1

    await run_top_up(
        container, BalanceTopUp(operation_id=uuid4(), user_id=user_id, paid_usd=Decimal("1.00"))
    )
    assert await flush_once(container) == 1
    assert pg_counter.count == 2

    row = await read_pg_balance(container, user_id)
    assert row is not None
    assert row.paid_usd == Decimal("1.00")
    assert row.version == 1
