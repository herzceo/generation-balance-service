from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from redis.asyncio import Redis, from_url
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing import BillingConfig
from backend.app.shared.ports.billing import BalanceStore
from backend.domain.generation import (
    FakeGenerationProvider,
    SharedProviderState,
    create_shared_provider_state,
)
from tests.integration.billing.helpers import PgCounter, attach_pg_counter
from tests.integration.billing.ioc import create_test_container, fast_billing_config

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from multiprocessing.managers import SyncManager

    from dishka import AsyncContainer


@pytest.fixture
def shared_state(manager: SyncManager) -> SharedProviderState:
    return create_shared_provider_state(manager)


@pytest.fixture
def provider(shared_state: SharedProviderState) -> FakeGenerationProvider:
    return FakeGenerationProvider(shared_state)


@pytest.fixture
async def engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    engine = _create_async_engine(postgres_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def pg_counter(engine: AsyncEngine) -> PgCounter:
    return attach_pg_counter(engine)


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = from_url(redis_url, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def billing_config() -> BillingConfig:
    return fast_billing_config()


@pytest.fixture
async def container(
    engine: AsyncEngine,
    redis: Redis,
    provider: FakeGenerationProvider,
    billing_config: BillingConfig,
    pg_counter: PgCounter,
) -> AsyncIterator[AsyncContainer]:
    c = create_test_container(
        engine=engine, redis=redis, provider=provider, billing_config=billing_config
    )
    try:
        yield c
    finally:
        await c.close()


@pytest.fixture
async def store(container: AsyncContainer) -> BalanceStore:
    balance_store: BalanceStore = await container.get(BalanceStore)
    return balance_store
