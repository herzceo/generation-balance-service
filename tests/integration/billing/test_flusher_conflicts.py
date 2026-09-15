"""Two flushers on one user, and a hot store that restarted behind PostgreSQL."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest

from backend.app.shared.ports.billing import BalanceSnapshot, BalanceStore
from backend.domain.generation import BalanceTopUp
from tests.integration.billing.helpers import (
    basic_request,
    dirty_count,
    flush_once,
    read_pg_balance,
    read_redis_balance,
    run_generation,
    run_top_up,
    seed_pg_balance,
)
from tests.integration.billing.ioc import create_test_container

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from uuid import UUID

    from dishka import AsyncContainer
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

    from backend.domain.generation import FakeGenerationProvider
    from tests.integration.billing.helpers import PgCounter


@pytest.fixture
async def other(
    engine: AsyncEngine, redis: Redis, provider: FakeGenerationProvider
) -> AsyncIterator[AsyncContainer]:
    """A second process's container: same Redis and PostgreSQL, its own store and flusher."""
    container = create_test_container(engine=engine, redis=redis, provider=provider)
    try:
        yield container
    finally:
        await container.close()


async def test_two_flushers_flush_the_same_user_at_once(
    container: AsyncContainer,
    other: AsyncContainer,
    store: BalanceStore,
    redis: Redis,
    pg_counter: PgCounter,
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    await run_generation(container, basic_request(user_id))
    pg_counter.reset()

    accepted = await asyncio.gather(flush_once(container), flush_once(other))

    assert sorted(accepted) == [0, 1]
    snapshot = await read_redis_balance(store, user_id)
    assert snapshot is not None
    assert snapshot.version == 1
    row = await read_pg_balance(container, user_id)
    assert row is not None
    assert (row.paid_usd, row.version) == (Decimal("0.92"), 1)
    assert await dirty_count(redis) == 0


async def test_slow_flusher_does_not_overwrite_a_newer_snapshot(
    container: AsyncContainer, other: AsyncContainer, store: BalanceStore, redis: Redis
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"))
    await run_generation(container, basic_request(user_id))
    slow_store = cast("Any", await other.get(BalanceStore))
    original = slow_store.load_many

    async def load_then_lose_the_race(user_ids: Sequence[UUID]) -> dict[UUID, BalanceSnapshot]:
        snapshots: dict[UUID, BalanceSnapshot] = await original(user_ids)
        # A newer change lands and the other flusher ships it before we reach PostgreSQL.
        await run_top_up(
            container,
            BalanceTopUp(operation_id=uuid4(), user_id=user_id, paid_usd=Decimal("0.50")),
        )
        assert await flush_once(container) == 1
        return snapshots

    slow_store.load_many = load_then_lose_the_race

    assert await flush_once(other) == 0

    row = await read_pg_balance(container, user_id)
    assert row is not None
    assert (row.paid_usd, row.version) == (Decimal("1.42"), 2)
    snapshot = await read_redis_balance(store, user_id)
    assert snapshot is not None
    assert snapshot.version == 2
    assert await dirty_count(redis) == 0


@pytest.mark.parametrize("pg_version", [6, 7])
async def test_flusher_advances_a_hot_store_that_lost_writes(
    container: AsyncContainer,
    store: BalanceStore,
    redis: Redis,
    caplog: pytest.LogCaptureFixture,
    pg_version: int,
) -> None:
    user_id = uuid4()
    await seed_pg_balance(container, user_id, paid_usd=Decimal("1.00"), version=pg_version)
    # Redis came back from an AOF written before the changes PostgreSQL already holds.
    assert await store.seed_if_absent(
        user_id,
        BalanceSnapshot(
            free_usd=Decimal(0),
            bonus_usd=Decimal(0),
            paid_usd=Decimal("0.50"),
            free_requests=0,
            version=5,
        ),
    )
    await run_top_up(
        container, BalanceTopUp(operation_id=uuid4(), user_id=user_id, paid_usd=Decimal("0.10"))
    )

    with caplog.at_level(logging.WARNING, logger="backend.app.billing.flusher"):
        assert await flush_once(container) == 0

    assert f"behind PostgreSQL version {pg_version}" in caplog.text
    row = await read_pg_balance(container, user_id)
    assert row is not None
    assert (row.paid_usd, row.version) == (Decimal("1.00"), pg_version)
    snapshot = await read_redis_balance(store, user_id)
    assert snapshot is not None
    assert (snapshot.paid_usd, snapshot.version) == (Decimal("0.60"), pg_version + 1)
    assert await dirty_count(redis) == 1

    assert await flush_once(container) == 1
    row = await read_pg_balance(container, user_id)
    assert row is not None
    assert (row.paid_usd, row.version) == (Decimal("0.60"), pg_version + 1)
    assert await dirty_count(redis) == 0
