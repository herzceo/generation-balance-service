from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from backend.app.errors import InvalidInputError
from backend.domain.generation import BalanceTopUp, InsufficientBalanceError
from tests.integration.billing.helpers import (
    assert_non_negative,
    basic_request,
    read_redis_balance,
    run_generation,
    run_top_up,
    seed_pg_balance,
    split_results,
)

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from backend.app.shared.ports.billing import BalanceStore
    from tests.integration.billing.helpers import PgCounter


async def test_top_up_increments_all_buckets(
    container: AsyncContainer, store: BalanceStore
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("0.10"), free_requests=1)

    await run_top_up(
        container,
        BalanceTopUp(
            operation_id=uuid4(),
            user_id=user_id,
            paid_usd=Decimal("1.00"),
            bonus_usd=Decimal("0.50"),
            free_requests=3,
        ),
    )

    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("1.10")
    assert balance.bonus_usd == Decimal("0.50")
    assert balance.free_requests == 4
    assert balance.version == 1


async def test_top_up_same_operation_id_applied_once(
    container: AsyncContainer, store: BalanceStore
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("0.10"))
    command = BalanceTopUp(operation_id=uuid4(), user_id=user_id, paid_usd=Decimal("1.00"))

    await run_top_up(container, command)
    await run_top_up(container, command)
    await asyncio.gather(*(run_top_up(container, command) for _ in range(5)))

    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("1.10")
    assert balance.version == 1


@pytest.mark.parametrize(
    "command",
    [
        BalanceTopUp(operation_id=uuid4(), user_id=uuid4(), paid_usd=Decimal("-0.01")),
        BalanceTopUp(operation_id=uuid4(), user_id=uuid4(), bonus_usd=Decimal(-1)),
        BalanceTopUp(operation_id=uuid4(), user_id=uuid4(), free_requests=-1),
        BalanceTopUp(operation_id=uuid4(), user_id=uuid4(), paid_usd=Decimal("0.0000001")),
    ],
)
async def test_top_up_invalid_amount_rejected(
    container: AsyncContainer, store: BalanceStore, command: BalanceTopUp
) -> None:
    with pytest.raises(InvalidInputError):
        await run_top_up(container, command)
    assert await read_redis_balance(store, command.user_id) is None


async def test_top_up_concurrent_with_generations_keeps_balance_exact(
    container: AsyncContainer, store: BalanceStore
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("0.20"))
    requests = [basic_request(user_id, model_name="premium") for _ in range(30)]
    top_up = BalanceTopUp(operation_id=uuid4(), user_id=user_id, paid_usd=Decimal("1.00"))

    results = await asyncio.gather(
        run_top_up(container, top_up),
        *(run_generation(container, r) for r in requests),
        return_exceptions=True,
    )

    ok, errors = split_results(results[1:])
    assert all(isinstance(e, InsufficientBalanceError) for e in errors)
    assert 1 <= len(ok) <= 6
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("1.20") - Decimal("0.20") * len(ok)
    assert_non_negative(balance)


async def test_top_up_on_cold_cache_loads_from_pg_first(
    container: AsyncContainer, store: BalanceStore, pg_counter: PgCounter
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("0.50"))
    pg_counter.reset()

    await run_top_up(
        container, BalanceTopUp(operation_id=uuid4(), user_id=user_id, paid_usd=Decimal("0.25"))
    )

    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("0.75")
    assert pg_counter.count == 1
