from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from tests.integration.billing.helpers import (
    basic_request,
    flush_once,
    run_generation,
    seed_pg_balance,
)

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from tests.integration.billing.helpers import PgCounter

GENERATIONS = 30


async def _run_batch(container: AsyncContainer, user_id: object) -> None:
    assert isinstance(user_id, type(uuid4()))
    requests = [basic_request(user_id) for _ in range(GENERATIONS)]
    await asyncio.gather(*(run_generation(container, r) for r in requests))


async def test_thirty_generations_cost_two_pg_statements(
    container: AsyncContainer, pg_counter: PgCounter
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("10.00"))
    pg_counter.reset()

    await _run_batch(container, user_id)
    before_flush = pg_counter.count
    assert await flush_once(container) == 1
    after_flush = pg_counter.count

    print(
        f"[pg budget] {GENERATIONS} generations, cold Redis: "
        f"{before_flush} statement(s) before flush, {after_flush} after flush"
    )
    assert before_flush == 1
    assert after_flush == 2
    assert pg_counter.statements[0].lstrip().upper().startswith("SELECT")
    assert pg_counter.statements[1].lstrip().upper().startswith("INSERT")


async def test_warm_cache_batch_costs_one_flush_statement(
    container: AsyncContainer, pg_counter: PgCounter
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("10.00"))
    await _run_batch(container, user_id)
    await flush_once(container)
    pg_counter.reset()

    await _run_batch(container, user_id)
    assert pg_counter.count == 0
    assert await flush_once(container) == 1

    print(f"[pg budget] {GENERATIONS} generations, warm Redis: {pg_counter.count} statement(s)")
    assert pg_counter.count == 1
    assert pg_counter.statements[0].lstrip().upper().startswith("INSERT")


async def test_flush_without_dirty_users_hits_pg_zero_times(
    container: AsyncContainer, pg_counter: PgCounter
) -> None:
    assert await flush_once(container) == 0
    assert pg_counter.count == 0
