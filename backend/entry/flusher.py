from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from backend.app.billing import BalanceFlusher, BillingConfig
from backend.entry.ioc import create_container
from backend.infra.database.config import DatabaseConfig
from backend.infra.database.psql.engine import create_async_engine
from backend.infra.database.redis import RedisConfig, create_redis_client
from backend.infra.database.redis.adapters import RedisBalanceStoreConfig

logger = logging.getLogger(__name__)


def run_flusher(
    db_config: DatabaseConfig,
    redis_config: RedisConfig,
    billing_config: BillingConfig,
    store_config: RedisBalanceStoreConfig,
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    asyncio.run(_run(db_config, redis_config, billing_config, store_config))


async def _run(
    db_config: DatabaseConfig,
    redis_config: RedisConfig,
    billing_config: BillingConfig,
    store_config: RedisBalanceStoreConfig,
) -> None:
    engine = create_async_engine(db_config)
    redis = create_redis_client(redis_config)
    container = create_container(
        engine=engine, redis=redis, billing_config=billing_config, store_config=store_config
    )
    try:
        async with container() as request_container:
            flusher = await request_container.get(BalanceFlusher)
            task = asyncio.create_task(flusher.run())
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, task.cancel)
            logger.info(
                "balance flusher started (interval=%ss)", billing_config.FLUSH_INTERVAL_SECONDS
            )
            with contextlib.suppress(asyncio.CancelledError):
                await task
            logger.info("balance flusher stopped")
    finally:
        await container.close()
        await redis.aclose()
        await engine.dispose()
