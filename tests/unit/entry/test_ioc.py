"""The flusher process builds its container without a generation provider."""

from __future__ import annotations

from decimal import Decimal

import pytest
from dishka import AsyncContainer
from dishka.exceptions import NoFactoryError
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.billing import (
    BalanceFlusher,
    BalanceService,
    BillingConfig,
    GenerationService,
    ReservationReaper,
)
from backend.app.shared.ports.billing import GenerationProvider
from backend.domain.generation import GenerationRequest, GenerationResult
from backend.entry.ioc import create_container
from backend.infra.database.redis import RedisConfig, create_redis_client


class _StubProvider(GenerationProvider):
    async def generate(
        self, request: GenerationRequest, *, authorized_cost_usd: Decimal
    ) -> GenerationResult:
        return GenerationResult(
            client_request_id=request.client_request_id,
            content="",
            billed_cost_usd=authorized_cost_usd,
        )


def _container(provider: GenerationProvider | None) -> AsyncContainer:
    return create_container(
        engine=create_async_engine("postgresql+psycopg://user:pass@127.0.0.1:1/db"),
        redis=create_redis_client(RedisConfig()),
        billing_config=BillingConfig(),
        provider=provider,
    )


async def test_flusher_container_resolves_without_a_generation_provider() -> None:
    container = _container(None)
    async with container() as scope:
        assert isinstance(await scope.get(BalanceFlusher), BalanceFlusher)
        assert isinstance(await scope.get(ReservationReaper), ReservationReaper)
        assert isinstance(await scope.get(BalanceService), BalanceService)
        with pytest.raises(NoFactoryError):
            await scope.get(GenerationService)
    await container.close()


async def test_container_with_a_provider_resolves_the_generation_service() -> None:
    container = _container(_StubProvider())
    async with container() as scope:
        assert isinstance(await scope.get(GenerationService), GenerationService)
    await container.close()
