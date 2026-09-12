from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine
from sqlalchemy.ext.asyncio.session import AsyncSession

from backend.infra.database.config import DatabaseConfig


def create_async_engine(config: DatabaseConfig) -> AsyncEngine:
    return _create_async_engine(
        config.get_postgres_url(),
        pool_size=config.POSTGRES_POOL_SIZE,
        max_overflow=config.POSTGRES_MAX_OVERFLOW,
    )


def create_async_session_maker(async_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
