from __future__ import annotations

from collections.abc import AsyncIterator

from dishka import AsyncContainer, Provider, Scope, make_async_container
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from backend.app.billing import (
    BalanceFlusher,
    BalanceLoader,
    BalanceService,
    BillingConfig,
    GenerationService,
)
from backend.app.shared.db.database import Database
from backend.app.shared.ports.billing import BalanceStore, GenerationProvider
from backend.domain.generation import DebitPolicyService
from backend.infra.database.psql import ImplDatabase
from backend.infra.database.psql.engine import create_async_session_maker
from backend.infra.database.redis.adapters import ImplRedisBalanceStore, RedisBalanceStoreConfig


async def _create_impl_database(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[ImplDatabase]:
    db = ImplDatabase(session_maker)
    try:
        yield db
    finally:
        await db.close()


def create_infra_provider(
    *,
    engine: AsyncEngine,
    redis: Redis,
    billing_config: BillingConfig,
    store_config: RedisBalanceStoreConfig,
) -> Provider:
    provider = Provider(scope=Scope.APP)
    provider.provide(lambda: engine, provides=AsyncEngine)
    provider.provide(create_async_session_maker, provides=async_sessionmaker[AsyncSession])
    provider.provide(lambda: redis, provides=Redis)
    provider.provide(lambda: billing_config, provides=BillingConfig)
    provider.provide(lambda: store_config, provides=RedisBalanceStoreConfig)
    provider.provide(ImplRedisBalanceStore, provides=BalanceStore)
    provider.provide(DebitPolicyService, provides=DebitPolicyService)
    provider.provide(_create_impl_database, provides=Database, scope=Scope.REQUEST)
    return provider


def create_billing_provider() -> Provider:
    provider = Provider(scope=Scope.REQUEST)
    provider.provide(BalanceLoader, provides=BalanceLoader)
    provider.provide(GenerationService, provides=GenerationService)
    provider.provide(BalanceService, provides=BalanceService)
    provider.provide(BalanceFlusher, provides=BalanceFlusher)
    return provider


def create_provider_provider(generation_provider: GenerationProvider) -> Provider:
    provider = Provider(scope=Scope.APP)
    provider.provide(lambda: generation_provider, provides=GenerationProvider)
    return provider


def create_container(
    *,
    engine: AsyncEngine,
    redis: Redis,
    billing_config: BillingConfig,
    store_config: RedisBalanceStoreConfig | None = None,
    provider: GenerationProvider | None = None,
) -> AsyncContainer:
    providers = [
        create_infra_provider(
            engine=engine,
            redis=redis,
            billing_config=billing_config,
            store_config=store_config or RedisBalanceStoreConfig(),
        ),
        create_billing_provider(),
    ]
    if provider is not None:
        providers.append(create_provider_provider(provider))
    return make_async_container(*providers)
