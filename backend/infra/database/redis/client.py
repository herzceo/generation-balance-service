from redis.asyncio import Redis, from_url

from backend.infra.database.redis.config import RedisConfig


def create_redis_client(config: RedisConfig) -> Redis:
    return from_url(config.url, decode_responses=True)
