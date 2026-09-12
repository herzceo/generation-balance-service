from __future__ import annotations

from typing import cast

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.domain.entities import Base


def _diff(sync_conn: Connection) -> list[object]:
    return cast(
        "list[object]", compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
    )


async def test_schema_matches_entities(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(_diff)
    finally:
        await engine.dispose()
    assert diff == []
