from argparse import ArgumentParser, Namespace
from typing import TYPE_CHECKING

from alembic.config import CommandLine as AlembicCLI
from alembic.config import Config as AlembicConfig

from backend.app.billing import BillingConfig
from backend.entry.flusher import run_flusher
from backend.infra.database.config import DatabaseConfig
from backend.infra.database.psql.alembic import ALEMBIC_CONFIG
from backend.infra.database.redis import RedisConfig
from backend.infra.database.redis.adapters import RedisBalanceStoreConfig

from .utils import load_from_env

if TYPE_CHECKING:
    from collections.abc import Callable


def create_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="backend", description="Generations and balances service")

    sub = parser.add_subparsers(dest="which", required=True)
    alembic_parent = AlembicCLI(prog="backend alembic").parser
    sub.add_parser(
        "alembic",
        parents=[alembic_parent],
        add_help=False,
        prog="backend alembic",
        formatter_class=alembic_parent.formatter_class,
    )
    sub.add_parser("flusher", prog="backend flusher")

    return parser


def cmd_run_alembic(options: Namespace) -> None:
    db = load_from_env(DatabaseConfig)

    if options.config is None:
        options.config = ALEMBIC_CONFIG

    alembic_cli = AlembicCLI()
    cfg = AlembicConfig(
        file_=options.config,
        toml_file="pyproject.toml",
        ini_section=options.name,
        cmd_opts=options,
        config_args={"sqlalchemy.url": db.get_postgres_url()},
    )
    alembic_cli.run_cmd(cfg, options)


def cmd_run_flusher(_options: Namespace) -> None:
    run_flusher(
        load_from_env(DatabaseConfig),
        load_from_env(RedisConfig),
        load_from_env(BillingConfig),
        load_from_env(RedisBalanceStoreConfig),
    )


def main() -> None:
    parser = create_parser()
    options = parser.parse_args()

    cmd_map: dict[str, Callable[[Namespace], None]] = {
        "alembic": cmd_run_alembic,
        "flusher": cmd_run_flusher,
    }
    cmd_map[options.which](options)
