from __future__ import annotations

import multiprocessing
from multiprocessing.managers import SyncManager
from pathlib import Path
from typing import TYPE_CHECKING

import alembic.command
import pytest
from alembic.config import Config as AlembicConfig
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

if TYPE_CHECKING:
    from collections.abc import Iterator

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_ALEMBIC_INI = _PROJECT_ROOT / "backend" / "infra" / "database" / "psql" / "alembic" / "alembic.ini"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("psycopg2", "psycopg")
        cfg = AlembicConfig(str(_ALEMBIC_INI))
        cfg.set_main_option("sqlalchemy.url", url)
        alembic.command.upgrade(cfg, "head")
        yield url


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as rd:
        host = rd.get_container_host_ip()
        port = rd.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def manager() -> Iterator[SyncManager]:
    mgr = multiprocessing.Manager()
    try:
        yield mgr
    finally:
        mgr.shutdown()
