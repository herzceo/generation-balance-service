from __future__ import annotations

import pytest

from backend.app.billing import BillingConfig
from backend.infra.database.config import DatabaseConfig
from backend.infra.database.redis import RedisConfig
from backend.infra.database.redis.adapters import RedisBalanceStoreConfig
from backend.main.utils.load_from_env import _convert_to_dto


def test_database_config_reads_required_fields() -> None:
    config = _convert_to_dto(
        {
            "POSTGRES_HOST": "db",
            "POSTGRES_PORT": "6543",
            "POSTGRES_DB": "generations",
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASS": "secret",
            "POSTGRES_POOL_SIZE": "3",
        },
        DatabaseConfig,
    )
    assert config.POSTGRES_POOL_SIZE == 3
    assert config.get_postgres_url() == ("postgresql+psycopg://postgres:secret@db:6543/generations")


def test_redis_config_optional_union_field() -> None:
    without = _convert_to_dto({"REDIS_HOST": "cache"}, RedisConfig)
    assert without.REDIS_PASSWORD is None
    assert without.url == "redis://cache:6379/0"

    with_password = _convert_to_dto({"REDIS_PASSWORD": "hunter2"}, RedisConfig)
    assert with_password.url == "redis://:hunter2@localhost:6379/0"


def test_billing_and_store_configs_read_floats_and_ints() -> None:
    billing = _convert_to_dto(
        {"FLUSH_INTERVAL_SECONDS": "0.25", "FLUSH_BATCH_SIZE": "7"}, BillingConfig
    )
    assert billing.FLUSH_INTERVAL_SECONDS == 0.25
    assert billing.FLUSH_BATCH_SIZE == 7

    store = _convert_to_dto({"RETRY_ATTEMPTS": "5"}, RedisBalanceStoreConfig)
    assert store.RETRY_ATTEMPTS == 5
    assert store.GENERATION_RECORD_TTL_SECONDS == 86400


def test_missing_required_field_is_reported() -> None:
    with pytest.raises(ValueError, match="POSTGRES_HOST"):
        _convert_to_dto({}, DatabaseConfig)


def test_unconvertible_value_names_the_field() -> None:
    with pytest.raises(ValueError, match="FLUSH_BATCH_SIZE"):
        _convert_to_dto({"FLUSH_BATCH_SIZE": "many"}, BillingConfig)
