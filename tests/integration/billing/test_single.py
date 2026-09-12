from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from backend.app.errors import InvalidInputError
from backend.domain.generation import (
    ConditionalFreeAccessDeniedError,
    FreeRequestsExhaustedError,
    InsufficientBalanceError,
)
from tests.integration.billing.helpers import (
    basic_request,
    read_redis_balance,
    run_generation,
    seed_pg_balance,
)

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from backend.app.shared.ports.billing import BalanceStore
    from backend.domain.generation import FakeGenerationProvider


async def test_generation_basic_with_paid_balance_debits_paid(
    container: AsyncContainer, store: BalanceStore
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"), free_requests=2)

    result = await run_generation(container, basic_request(user_id))

    assert result.content == "generated:basic"
    assert result.billed_cost_usd == Decimal("0.08")
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("0.92")
    assert balance.free_requests == 2
    assert balance.version == 1


async def test_generation_basic_without_paid_uses_free_and_consumes_request(
    container: AsyncContainer, store: BalanceStore
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, free_usd=Decimal("0.10"), free_requests=2)

    result = await run_generation(container, basic_request(user_id))

    assert result.billed_cost_usd == Decimal("0.05")
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.free_usd == Decimal("0.05")
    assert balance.free_requests == 1


async def test_generation_basic_mixed_buckets_takes_free_first(
    container: AsyncContainer, store: BalanceStore
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, free_usd=Decimal("0.10"), paid_usd=Decimal("0.03"))

    await run_generation(container, basic_request(user_id))
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.free_usd == Decimal("0.02")
    assert balance.paid_usd == Decimal("0.03")

    with pytest.raises(InsufficientBalanceError):
        await run_generation(container, basic_request(user_id))


POLICY_TABLE = [
    ("premium", False, {"paid_usd": Decimal("0.20")}, {"paid_usd": Decimal(0)}),
    ("premium", False, {"paid_usd": Decimal("0.19")}, InsufficientBalanceError),
    (
        "conditional",
        False,
        {"paid_usd": Decimal("0.12"), "free_requests": 0},
        {"paid_usd": Decimal(0), "free_requests": 0},
    ),
    (
        "conditional",
        True,
        {"paid_usd": Decimal("0.10"), "free_usd": Decimal("0.05"), "free_requests": 1},
        {"paid_usd": Decimal("0.03"), "free_usd": Decimal("0.05"), "free_requests": 0},
    ),
    (
        "conditional",
        True,
        {"free_usd": Decimal("0.10"), "free_requests": 1},
        {"free_usd": Decimal("0.03"), "free_requests": 0},
    ),
    ("conditional", False, {"paid_usd": Decimal("0.10")}, ConditionalFreeAccessDeniedError),
    (
        "conditional",
        True,
        {"paid_usd": Decimal("0.05"), "free_requests": 0},
        FreeRequestsExhaustedError,
    ),
    ("basic", False, {"free_usd": Decimal("0.10"), "free_requests": 0}, FreeRequestsExhaustedError),
    ("basic", False, {"free_usd": Decimal("0.01"), "free_requests": 3}, InsufficientBalanceError),
]


@pytest.mark.parametrize(("model_name", "eligible", "seed", "expected"), POLICY_TABLE)
async def test_generation_policy_table(
    container: AsyncContainer,
    store: BalanceStore,
    model_name: str,
    eligible: bool,  # noqa: FBT001
    seed: dict[str, Decimal | int],
    expected: dict[str, Decimal | int] | type[Exception],
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, **seed)  # type: ignore[arg-type]
    request = basic_request(user_id, model_name=model_name, conditional_free_eligible=eligible)

    if isinstance(expected, type):
        with pytest.raises(expected):
            await run_generation(container, request)
        return

    result = await run_generation(container, request)
    assert result.content == f"generated:{model_name}"
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    for name, value in expected.items():
        assert getattr(balance, name) == value, name


async def test_generation_unknown_model_rejected(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))

    with pytest.raises(InvalidInputError):
        await run_generation(container, basic_request(user_id, model_name="ultra"))

    assert provider.get_stats().calls == {}
    assert await read_redis_balance(store, user_id) is None


async def test_generation_denied_does_not_touch_provider_or_balance(
    container: AsyncContainer, store: BalanceStore, provider: FakeGenerationProvider
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("0.10"))

    with pytest.raises(InsufficientBalanceError):
        await run_generation(container, basic_request(user_id, model_name="premium"))

    assert provider.get_stats().calls == {}
    balance = await read_redis_balance(store, user_id)
    assert balance is not None
    assert balance.paid_usd == Decimal("0.10")
    assert balance.version == 0
