from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.billing import BillingConfig
from backend.entry.ioc import create_container

if TYPE_CHECKING:
    from dishka import AsyncContainer
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

    from backend.app.shared.ports.billing import GenerationProvider


def fast_billing_config(**overrides: Any) -> BillingConfig:
    values: dict[str, Any] = {
        "RUNNING_POLL_INTERVAL_SECONDS": 0.01,
        "FLUSH_INTERVAL_SECONDS": 0.05,
        **overrides,
    }
    return BillingConfig(**values)


def create_test_container(
    *,
    engine: AsyncEngine,
    redis: Redis,
    provider: GenerationProvider,
    billing_config: BillingConfig | None = None,
) -> AsyncContainer:
    return create_container(
        engine=engine,
        redis=redis,
        billing_config=billing_config or fast_billing_config(),
        provider=provider,
    )
